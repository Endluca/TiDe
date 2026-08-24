"""Transactional v2 settlement of the four non-favorite lesson components."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_lesson_score_rules_v2 import (
    COMPONENT_SCORES,
    FrozenCompletionIdentityV2,
    LessonComponentConditionV2,
    LessonComponentSettlementPlanV2,
    PriorLessonComponentSettlementV2,
    SettlementAction,
    SettlementStatus,
    plan_lesson_component_settlement_v2,
)
from .dts_v2_course_source_wide_plan import CourseSourceWidePlanV2


_DIMENSIONS = {
    "FEEDBACK_PRAISE": "USER_FEEDBACK",
    "PERFECT_COMPLETED": "RELIABILITY",
    "PEAK_COMPLETED": "RELIABILITY",
    "CLASS_QUALITY_HARDWARE": "CLASS_QUALITY",
}


class DtsV2LessonComponentStoreError(RuntimeError):
    """The persisted settlement state cannot be changed safely."""


@dataclass(frozen=True)
class PersistedLessonComponentV2:
    prior: PriorLessonComponentSettlementV2
    award_projection_generation: int
    row_version: int
    materialization_origin: str
    materialized_by_run_id: str | None


@dataclass(frozen=True)
class LessonComponentTransitionV2:
    persisted: PersistedLessonComponentV2 | None
    plan: LessonComponentSettlementPlanV2
    evidence_status: str


def plan_course_component_transitions_v2(
    *,
    course_plan: CourseSourceWidePlanV2,
    persisted: Sequence[PersistedLessonComponentV2],
    score_rule_version: str,
) -> tuple[LessonComponentTransitionV2, ...]:
    """Reverse stale owners first, then settle the frozen completion owner."""

    if course_plan.completion_conflict_status == "PENDING":
        return ()
    by_identity = {
        (
            row.prior.identity.completion_participation_seq,
            row.prior.component_code,
        ): row
        for row in persisted
    }
    if len(by_identity) != len(persisted):
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_PERSISTED_DUPLICATE"
        )
    current_rows = [row for row in course_plan.participation_rows if row.valid_for_scoring]
    if len(current_rows) > 1:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_SCORE_OWNER_CONFLICT"
        )
    current = current_rows[0] if current_rows else None
    current_seq = None if current is None else current.participation_seq
    transitions: list[LessonComponentTransitionV2] = []

    for persisted_row in sorted(
        persisted,
        key=lambda item: (
            item.prior.identity.completion_participation_seq,
            item.prior.component_code,
        ),
    ):
        prior = persisted_row.prior
        if (
            prior.identity.completion_participation_seq == current_seq
            or prior.status != SettlementStatus.AWARDED
        ):
            continue
        condition = _stale_condition(
            prior.component_code,
            source_deleted=course_plan.source_deleted,
            current_completion_seq=current_seq,
        )
        transitions.append(
            LessonComponentTransitionV2(
                persisted=persisted_row,
                plan=plan_lesson_component_settlement_v2(
                    identity=prior.identity,
                    condition=condition,
                    score_rule_version=score_rule_version,
                    prior=prior,
                ),
                evidence_status=condition.evidence_status,
            )
        )

    if current is None:
        return tuple(transitions)
    identity = FrozenCompletionIdentityV2(
        source_region=course_plan.source_region,  # type: ignore[arg-type]
        source_appoint_id=course_plan.source_appoint_id,
        completion_participation_seq=current.participation_seq,
        teacher_id=current.teacher_id,
    )
    conditions = {item.component_code: item for item in current.score_conditions}
    if set(conditions) != set(COMPONENT_SCORES):
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_CONDITION_SET_INCOMPLETE"
        )
    for component_code in sorted(conditions):
        existing = by_identity.get((current.participation_seq, component_code))
        prior = None if existing is None else existing.prior
        transitions.append(
            LessonComponentTransitionV2(
                persisted=existing,
                plan=plan_lesson_component_settlement_v2(
                    identity=identity,
                    condition=conditions[component_code],
                    score_rule_version=score_rule_version,
                    prior=prior,
                ),
                evidence_status=conditions[component_code].evidence_status,
            )
        )
    return tuple(transitions)


class DtsV2LessonComponentStore:
    """Persist score entries and settlement pointers in the caller transaction."""

    def __init__(self, *, score_rule_version: str = "dts-lesson-score-v2") -> None:
        if (
            not isinstance(score_rule_version, str)
            or not score_rule_version
            or len(score_rule_version) > 64
            or any(character.isspace() for character in score_rule_version)
        ):
            raise DtsV2LessonComponentStoreError(
                "DTS_V2_LESSON_COMPONENT_RULE_VERSION_INVALID"
            )
        self.score_rule_version = score_rule_version

    def settle_course(
        self,
        connection: Connection,
        course_plan: CourseSourceWidePlanV2,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        if type(aggregate_revision) is not int or aggregate_revision < 1:
            raise DtsV2LessonComponentStoreError(
                "DTS_V2_LESSON_COMPONENT_AGGREGATE_REVISION_INVALID"
            )
        if type(projection_generation) is not int or projection_generation < 1:
            raise DtsV2LessonComponentStoreError(
                "DTS_V2_LESSON_COMPONENT_PROJECTION_GENERATION_INVALID"
            )
        if not isinstance(triggering_event_id, str) or not triggering_event_id:
            raise DtsV2LessonComponentStoreError(
                "DTS_V2_LESSON_COMPONENT_EVENT_ID_INVALID"
            )
        _verify_source_versions(connection, course_plan)
        persisted = _load_settlements(connection, course_plan)
        transitions = plan_course_component_transitions_v2(
            course_plan=course_plan,
            persisted=persisted,
            score_rule_version=self.score_rule_version,
        )
        counts = {
            "score_entries": 0,
            "component_awards": 0,
            "component_reversals": 0,
            "component_replacements": 0,
            "component_projection_touches": 0,
            "completion_conflict_holds": int(
                course_plan.completion_conflict_status == "PENDING"
            ),
        }
        now = _transaction_timestamp(connection)
        for transition in transitions:
            changed = self._persist_transition(
                connection,
                transition,
                aggregate_revision=aggregate_revision,
                projection_generation=projection_generation,
                triggering_event_id=triggering_event_id,
                now=now,
            )
            if changed == "NOOP":
                continue
            if changed == "TOUCH":
                counts["component_projection_touches"] += 1
                continue
            counts["score_entries"] += 2 if changed == "REPLACE" else 1
            counts[
                {
                    "AWARD": "component_awards",
                    "REVERSE": "component_reversals",
                    "REPLACE": "component_replacements",
                }[changed]
            ] += 1
        return counts

    def _persist_transition(
        self,
        connection: Connection,
        transition: LessonComponentTransitionV2,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
        now: datetime,
    ) -> str:
        persisted = transition.persisted
        plan = transition.plan
        if plan.action == SettlementAction.NOOP:
            if (
                persisted is None
                or projection_generation
                <= persisted.award_projection_generation
            ):
                return "NOOP"
            updated = connection.execute(
                text(
                    """
                    UPDATE public.lesson_score_component_settlements
                    SET award_projection_generation=:projection_generation,
                        row_version=row_version+1,updated_at=:now
                    WHERE source_region=:source_region
                      AND source_appoint_id=:source_appoint_id
                      AND completion_participation_seq=:completion_seq
                      AND component_code=:component_code
                      AND row_version=:row_version
                    RETURNING row_version
                    """
                ),
                {
                    **_identity_params(plan),
                    "projection_generation": projection_generation,
                    "row_version": persisted.row_version,
                    "now": now,
                },
            ).scalar_one_or_none()
            if updated is None:
                raise DtsV2LessonComponentStoreError(
                    "DTS_V2_LESSON_COMPONENT_CONCURRENT_UPDATE"
                )
            return "TOUCH"

        teacher = _teacher_account(connection, plan.identity.teacher_id)
        reversal_id: str | None = None
        award_id: str | None = None
        if plan.reversal_idempotency_key is not None:
            reversal_id = _write_score_entry(
                connection,
                plan=plan,
                idempotency_key=plan.reversal_idempotency_key,
                entry_kind="REVERSAL",
                teacher=teacher,
                aggregate_revision=aggregate_revision,
                projection_generation=projection_generation,
                triggering_event_id=triggering_event_id,
                occurred_at=now,
                recorded_at=now,
                evidence_status=transition.evidence_status,
            )
        if plan.award_idempotency_key is not None:
            award_id = _write_score_entry(
                connection,
                plan=plan,
                idempotency_key=plan.award_idempotency_key,
                entry_kind="AWARD",
                teacher=teacher,
                aggregate_revision=aggregate_revision,
                projection_generation=projection_generation,
                triggering_event_id=triggering_event_id,
                occurred_at=_completion_occurred_at(connection, plan.identity),
                recorded_at=now,
                evidence_status=transition.evidence_status,
            )

        if persisted is None:
            if plan.action != SettlementAction.AWARD or award_id is None:
                raise DtsV2LessonComponentStoreError(
                    "DTS_V2_LESSON_COMPONENT_INSERT_PLAN_INVALID"
                )
            connection.execute(
                text(
                    """
                    INSERT INTO public.lesson_score_component_settlements (
                        source_region,source_appoint_id,
                        completion_participation_seq,component_code,teacher_id,
                        status,award_generation,component_score,
                        score_rule_version,evidence_fingerprint,
                        current_award_score_entry_id,
                        last_reversal_score_entry_id,materialization_origin,
                        materialized_by_run_id,award_projection_generation,
                        row_version,awarded_at,reversed_at,created_at,updated_at
                    ) VALUES (
                        :source_region,:source_appoint_id,:completion_seq,
                        :component_code,:teacher_id,'AWARDED',:award_generation,
                        :component_score,:score_rule_version,
                        :evidence_fingerprint,:award_id,NULL,'V2_LIVE',NULL,
                        :projection_generation,1,:now,NULL,:now,:now
                    )
                    """
                ),
                {
                    **_identity_params(plan),
                    "award_generation": plan.award_generation,
                    "component_score": plan.score,
                    "score_rule_version": plan.score_rule_version,
                    "evidence_fingerprint": plan.evidence_fingerprint,
                    "award_id": award_id,
                    "projection_generation": projection_generation,
                    "now": now,
                },
            )
            return "AWARD"

        values = {
            **_identity_params(plan),
            "status": plan.resulting_status.value,
            "award_generation": plan.award_generation,
            "component_score": plan.score,
            "score_rule_version": plan.score_rule_version,
            "evidence_fingerprint": plan.evidence_fingerprint,
            "award_id": award_id,
            "reversal_id": reversal_id,
            "projection_generation": projection_generation,
            "row_version": persisted.row_version,
            "now": now,
        }
        updated = connection.execute(
            text(
                """
                UPDATE public.lesson_score_component_settlements
                SET status=:status,award_generation=:award_generation,
                    component_score=:component_score,
                    score_rule_version=:score_rule_version,
                    evidence_fingerprint=:evidence_fingerprint,
                    current_award_score_entry_id=:award_id,
                    last_reversal_score_entry_id=COALESCE(
                        :reversal_id,last_reversal_score_entry_id
                    ),
                    award_projection_generation=:projection_generation,
                    row_version=row_version+1,
                    awarded_at=CASE WHEN :status='AWARDED' THEN :now
                                    ELSE awarded_at END,
                    reversed_at=CASE WHEN :status='REVERSED' THEN :now
                                     ELSE NULL END,
                    updated_at=:now
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND completion_participation_seq=:completion_seq
                  AND component_code=:component_code
                  AND row_version=:row_version
                RETURNING row_version
                """
            ),
            values,
        ).scalar_one_or_none()
        if updated is None:
            raise DtsV2LessonComponentStoreError(
                "DTS_V2_LESSON_COMPONENT_CONCURRENT_UPDATE"
            )
        return plan.action.value


def _load_settlements(
    connection: Connection,
    course_plan: CourseSourceWidePlanV2,
) -> tuple[PersistedLessonComponentV2, ...]:
    rows = connection.execute(
        text(
            """
            SELECT source_region,source_appoint_id,
                   completion_participation_seq,component_code,teacher_id,
                   status,award_generation,component_score,
                   score_rule_version,evidence_fingerprint,
                   current_award_score_entry_id,last_reversal_score_entry_id,
                   materialization_origin,materialized_by_run_id,
                   award_projection_generation,row_version
            FROM public.lesson_score_component_settlements
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
            ORDER BY completion_participation_seq,component_code
            FOR UPDATE
            """
        ),
        {
            "source_region": course_plan.source_region,
            "source_appoint_id": course_plan.source_appoint_id,
        },
    ).mappings()
    result: list[PersistedLessonComponentV2] = []
    for row in rows:
        identity = FrozenCompletionIdentityV2(
            source_region=str(row["source_region"]),  # type: ignore[arg-type]
            source_appoint_id=str(row["source_appoint_id"]),
            completion_participation_seq=int(
                row["completion_participation_seq"]
            ),
            teacher_id=str(row["teacher_id"]),
        )
        result.append(
            PersistedLessonComponentV2(
                prior=PriorLessonComponentSettlementV2(
                    identity=identity,
                    component_code=str(row["component_code"]),  # type: ignore[arg-type]
                    status=SettlementStatus(str(row["status"])),
                    award_generation=int(row["award_generation"]),
                    current_score=Decimal(str(row["component_score"])),
                    score_rule_version=str(row["score_rule_version"]),
                    evidence_fingerprint=str(row["evidence_fingerprint"]),
                    current_award_score_entry_id=row.get(
                        "current_award_score_entry_id"
                    ),
                    last_reversal_score_entry_id=row.get(
                        "last_reversal_score_entry_id"
                    ),
                ),
                award_projection_generation=int(
                    row["award_projection_generation"]
                ),
                row_version=int(row["row_version"]),
                materialization_origin=str(row["materialization_origin"]),
                materialized_by_run_id=row.get("materialized_by_run_id"),
            )
        )
    return tuple(result)


def _verify_source_versions(
    connection: Connection,
    plan: CourseSourceWidePlanV2,
) -> None:
    course = connection.execute(
        text(
            """
            SELECT c.row_version,f.row_version AS fact_row_version
            FROM public.source_courses c
            LEFT JOIN public.source_course_fact_current f
              ON f.source_region=c.source_region
             AND f.source_appoint_id=c.source_appoint_id
            WHERE c.source_region=:source_region
              AND c.source_appoint_id=:source_appoint_id
            FOR SHARE OF c
            """
        ),
        {
            "source_region": plan.source_region,
            "source_appoint_id": plan.source_appoint_id,
        },
    ).mappings().one_or_none()
    if course is None or int(course["row_version"]) != plan.course_row_version:
        raise DtsV2LessonComponentStoreError("DTS_V2_COURSE_PLAN_STALE")
    fact_version = course.get("fact_row_version")
    if (
        None if fact_version is None else int(fact_version)
    ) != plan.course_fact_row_version:
        raise DtsV2LessonComponentStoreError("DTS_V2_COURSE_PLAN_STALE")

    rows = connection.execute(
        text(
            """
            SELECT p.participation_seq,p.row_version,
                   f.row_version AS fact_row_version
            FROM public.source_course_participations p
            LEFT JOIN public.source_participation_fact_current f
              ON f.source_region=p.source_region
             AND f.source_appoint_id=p.source_appoint_id
             AND f.participation_seq=p.participation_seq
            WHERE p.source_region=:source_region
              AND p.source_appoint_id=:source_appoint_id
            ORDER BY p.participation_seq
            FOR SHARE OF p
            """
        ),
        {
            "source_region": plan.source_region,
            "source_appoint_id": plan.source_appoint_id,
        },
    ).mappings()
    actual = {
        int(row["participation_seq"]): (
            int(row["row_version"]),
            None
            if row.get("fact_row_version") is None
            else int(row["fact_row_version"]),
        )
        for row in rows
    }
    expected = {
        row.participation_seq: (
            row.participation_row_version,
            row.participation_fact_row_version,
        )
        for row in plan.participation_rows
    }
    if actual != expected:
        raise DtsV2LessonComponentStoreError("DTS_V2_COURSE_PLAN_STALE")


def _teacher_account(connection: Connection, teacher_id: str) -> Mapping[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT teacher_id,camp_enrollment_id
            FROM public.teachers
            WHERE teacher_id=:teacher_id
            FOR SHARE
            """
        ),
        {"teacher_id": teacher_id},
    ).mappings().one_or_none()
    if row is None:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_TEACHER_DEPENDENCY_PENDING"
        )
    return row


def _completion_occurred_at(
    connection: Connection,
    identity: FrozenCompletionIdentityV2,
) -> datetime:
    value = connection.execute(
        text(
            """
            SELECT completion_end_time
            FROM public.source_courses
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
              AND completion_participation_seq=:completion_seq
              AND completion_teacher_id=:teacher_id
            FOR SHARE
            """
        ),
        _identity_values(identity),
    ).scalar_one_or_none()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_COMPLETION_TIME_REQUIRED"
        )
    return value


def _write_score_entry(
    connection: Connection,
    *,
    plan: LessonComponentSettlementPlanV2,
    idempotency_key: str,
    entry_kind: str,
    teacher: Mapping[str, Any],
    aggregate_revision: int,
    projection_generation: int,
    triggering_event_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    evidence_status: str,
) -> str:
    if entry_kind not in {"AWARD", "REVERSAL"}:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_ENTRY_KIND_INVALID"
        )
    if evidence_status not in {"CONFIRMED", "SOURCE_MISSING"}:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_EVIDENCE_STATUS_INVALID"
        )
    score_entry_id = "v2score-" + hashlib.sha256(
        idempotency_key.encode("utf-8")
    ).hexdigest()
    reversal = entry_kind == "REVERSAL"
    payload = {
        "settlement_contract": "lesson-score-component-v2",
        "source_region": plan.identity.source_region,
        "source_appoint_id": plan.identity.source_appoint_id,
        "completion_participation_seq": (
            plan.identity.completion_participation_seq
        ),
        "component_code": plan.component_code,
        "award_generation": plan.award_generation,
        "evidence_fingerprint": plan.evidence_fingerprint,
        "triggering_event_id": triggering_event_id,
        "aggregate_revision": aggregate_revision,
        "projection_generation": projection_generation,
    }
    values = {
        "score_entry_id": score_entry_id,
        "camp_enrollment_id": teacher["camp_enrollment_id"],
        "lesson_id": plan.identity.source_appoint_id,
        "source_region": plan.identity.source_region,
        "source_appoint_id": plan.identity.source_appoint_id,
        "participation_seq": plan.identity.completion_participation_seq,
        "teacher_id": plan.identity.teacher_id,
        "dimension": _DIMENSIONS[plan.component_code],
        "entry_type": (
            "LESSON_COMPONENT_REVERSAL" if reversal else "LESSON_COMPONENT_AWARD"
        ),
        "delta_score": -plan.score if reversal else plan.score,
        "reason_code": plan.component_code,
        "evidence_status": "CONFIRMED" if not reversal else evidence_status,
        "score_rule_version": plan.score_rule_version,
        "occurred_at": occurred_at,
        "recorded_at": recorded_at,
        "reversal_of": plan.reversal_of_score_entry_id if reversal else None,
        "projection_generation": projection_generation,
        "idempotency_key": idempotency_key,
        "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }
    inserted = connection.execute(
        text(
            """
            INSERT INTO public.score_entries (
                score_entry_id,camp_enrollment_id,lesson_id,source_region,
                source_appoint_id,participation_seq,teacher_id,dimension,
                entry_type,delta_score,reason_code,evidence_status,
                score_rule_version,occurred_at,recorded_at,
                reversal_of_score_entry_id,task_assignment_id,
                projection_origin,materialized_by_run_id,
                projection_generation,idempotency_key,payload
            ) VALUES (
                :score_entry_id,:camp_enrollment_id,:lesson_id,:source_region,
                :source_appoint_id,:participation_seq,:teacher_id,:dimension,
                :entry_type,:delta_score,:reason_code,:evidence_status,
                :score_rule_version,:occurred_at,:recorded_at,:reversal_of,
                NULL,'V2_LIVE',NULL,:projection_generation,:idempotency_key,
                CAST(:payload AS jsonb)
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING score_entry_id
            """
        ),
        values,
    ).scalar_one_or_none()
    if inserted is not None:
        return str(inserted)
    existing = connection.execute(
        text(
            """
            SELECT score_entry_id,camp_enrollment_id,lesson_id,teacher_id,
                   source_region,source_appoint_id,participation_seq,dimension,
                   entry_type,delta_score,reason_code,evidence_status,
                   score_rule_version,occurred_at,reversal_of_score_entry_id,
                   task_assignment_id,projection_origin,materialized_by_run_id,
                   projection_generation,payload
            FROM public.score_entries
            WHERE idempotency_key=:idempotency_key
            FOR SHARE
            """
        ),
        {"idempotency_key": idempotency_key},
    ).mappings().one_or_none()
    if existing is None or not _score_entry_matches(existing, values, payload):
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_IDEMPOTENCY_CONFLICT"
        )
    return str(existing["score_entry_id"])


def _score_entry_matches(
    existing: Mapping[str, Any],
    expected: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    try:
        return (
            existing.get("score_entry_id") == expected["score_entry_id"]
            and existing.get("camp_enrollment_id")
            == expected["camp_enrollment_id"]
            and existing.get("lesson_id") == expected["lesson_id"]
            and existing.get("teacher_id") == expected["teacher_id"]
            and existing.get("source_region") == expected["source_region"]
            and existing.get("source_appoint_id")
            == expected["source_appoint_id"]
            and int(existing.get("participation_seq"))
            == expected["participation_seq"]
            and existing.get("dimension") == expected["dimension"]
            and existing.get("entry_type") == expected["entry_type"]
            and Decimal(str(existing.get("delta_score")))
            == Decimal(str(expected["delta_score"]))
            and existing.get("reason_code") == expected["reason_code"]
            and existing.get("evidence_status")
            == expected["evidence_status"]
            and existing.get("score_rule_version")
            == expected["score_rule_version"]
            and existing.get("occurred_at") == expected["occurred_at"]
            and existing.get("reversal_of_score_entry_id")
            == expected["reversal_of"]
            and existing.get("task_assignment_id") is None
            and existing.get("projection_origin") == "V2_LIVE"
            and existing.get("materialized_by_run_id") is None
            and int(existing.get("projection_generation"))
            == expected["projection_generation"]
            and dict(existing.get("payload") or {}) == dict(payload)
        )
    except (InvalidOperation, TypeError, ValueError):
        return False


def _transaction_timestamp(connection: Connection) -> datetime:
    value = connection.execute(text("SELECT transaction_timestamp()")).scalar_one()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise DtsV2LessonComponentStoreError(
            "DTS_V2_LESSON_COMPONENT_DATABASE_TIME_INVALID"
        )
    return value


def _identity_values(identity: FrozenCompletionIdentityV2) -> dict[str, Any]:
    return {
        "source_region": identity.source_region,
        "source_appoint_id": identity.source_appoint_id,
        "completion_seq": identity.completion_participation_seq,
        "teacher_id": identity.teacher_id,
    }


def _identity_params(plan: LessonComponentSettlementPlanV2) -> dict[str, Any]:
    return {**_identity_values(plan.identity), "component_code": plan.component_code}


def _stale_condition(
    component_code: str,
    *,
    source_deleted: bool,
    current_completion_seq: int | None,
) -> LessonComponentConditionV2:
    evidence = {
        "reason": "COURSE_DELETED" if source_deleted else "COMPLETION_OWNER_CHANGED",
        "current_completion_seq": current_completion_seq,
    }
    return LessonComponentConditionV2(
        component_code=component_code,  # type: ignore[arg-type]
        should_award=False,
        evidence_status="CONFIRMED",
        evidence_fingerprint=hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
    )


__all__ = [
    "DtsV2LessonComponentStore",
    "DtsV2LessonComponentStoreError",
    "LessonComponentTransitionV2",
    "PersistedLessonComponentV2",
    "plan_course_component_transitions_v2",
]
