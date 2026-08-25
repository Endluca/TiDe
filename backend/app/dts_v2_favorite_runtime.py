"""PostgreSQL runtime for favorite observations and lifetime attribution.

TEACHER_STUDENT Outbox handling only creates or requeues observation work.
The observation worker claims due rows using database time, evaluates protected
relationship history, and asks one database command to finish the observation,
attribution, score entry, and course score result in the same transaction.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
import re
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_favorite_rules_v2 import evaluate_favorite_at
from .dts_v2_teacher_student_domain_projector import (
    _Pair,
    _history_covers,
    _read_favorite_intervals,
    _read_preferred_scopes,
)
from .dts_v2_teacher_student_outbox_processor import (
    TeacherStudentMaterializationPlanV2,
)
from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    guarded_projection_generation,
)


FAVORITE_RULE_VERSION = "favorite-score-v1"
_V2_PRIMARY_MODE = "V2_PRIMARY"
_PIPELINE_MODES = frozenset(
    {"V1_COMPAT_DUAL_CAPTURE", _V2_PRIMARY_MODE, "ROLLED_BACK"}
)
_REGIONS = frozenset({"dom", "ovs"})
_ID_TYPES = frozenset({"NUMERIC", "TEXT"})
_RESULT_STATUSES = frozenset(
    {
        "CONFIRMED_TRUE",
        "CONFIRMED_FALSE",
        "WAITING_HISTORY",
        "WAITING_EVIDENCE",
    }
)
_ATTRIBUTION_ACTIONS = frozenset(
    {"NONE", "HOLD", "RESTORE", "AWARD", "REVERSE", "RESELECT"}
)
_DOM_STUDENT_TOKEN = re.compile(r"^dom:v1:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$")


class DtsV2FavoriteRuntimeError(RuntimeError):
    """Favorite runtime state cannot be consumed without guessing."""


@dataclass(frozen=True)
class FavoriteObservationClaimV2:
    source_region: str
    source_appoint_id: str
    observation_revision: int
    teacher_id: str
    teacher_id_type: str
    student_token: str
    completion_participation_seq: int
    observed_at: datetime
    required_evidence_revision: int
    claimed_evidence_revision: int
    required_evidence_fingerprint: str
    lease_owner: str
    lease_token: str
    row_version: int
    runtime_projection_generation: int | None = None

    @classmethod
    def from_database(cls, value: Any) -> "FavoriteObservationClaimV2":
        row = _database_object(value)
        region = row.get("source_region")
        teacher_type = row.get("teacher_id_type")
        student = row.get("student_token")
        if region not in _REGIONS or teacher_type not in _ID_TYPES:
            _fail("CLAIM_IDENTITY_INVALID")
        if not isinstance(student, str) or not student:
            _fail("CLAIM_IDENTITY_INVALID")
        if region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(student) is None:
            _fail("CLAIM_DOM_TOKEN_INVALID")
        strings: dict[str, str] = {}
        for name in (
            "source_appoint_id",
            "teacher_id",
            "lease_owner",
            "lease_token",
        ):
            item = row.get(name)
            if not isinstance(item, str) or not item:
                _fail("CLAIM_IDENTITY_INVALID")
            strings[name] = item
        integers: dict[str, int] = {}
        for name in (
            "observation_revision",
            "completion_participation_seq",
            "required_evidence_revision",
            "claimed_evidence_revision",
            "row_version",
        ):
            item = row.get(name)
            if type(item) is not int or item < 1:
                _fail("CLAIM_REVISION_INVALID")
            integers[name] = item
        if (
            integers["claimed_evidence_revision"]
            > integers["required_evidence_revision"]
        ):
            _fail("CLAIM_REVISION_INVALID")
        fingerprint = row.get("required_evidence_fingerprint")
        if not isinstance(fingerprint, str) or _SHA256.fullmatch(
            fingerprint
        ) is None:
            _fail("CLAIM_FINGERPRINT_INVALID")
        observed_at = _aware_datetime(row.get("observed_at"))
        return cls(
            source_region=str(region),
            source_appoint_id=strings["source_appoint_id"],
            observation_revision=integers["observation_revision"],
            teacher_id=strings["teacher_id"],
            teacher_id_type=str(teacher_type),
            student_token=student,
            completion_participation_seq=integers[
                "completion_participation_seq"
            ],
            observed_at=observed_at,
            required_evidence_revision=integers[
                "required_evidence_revision"
            ],
            claimed_evidence_revision=integers[
                "claimed_evidence_revision"
            ],
            required_evidence_fingerprint=fingerprint,
            lease_owner=strings["lease_owner"],
            lease_token=strings["lease_token"],
            row_version=integers["row_version"],
        )


@dataclass(frozen=True)
class FavoriteObservationEvaluationV2:
    status: str
    relation_state: bool | None
    relation_evidence_status: str
    relation_error_code: str | None
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        if self.status not in _RESULT_STATUSES:
            _fail("EVALUATION_STATUS_INVALID")
        if not isinstance(self.evidence_fingerprint, str) or _SHA256.fullmatch(
            self.evidence_fingerprint
        ) is None:
            _fail("EVALUATION_FINGERPRINT_INVALID")


@dataclass(frozen=True)
class FavoriteAttributionOutcomeV2:
    action: str
    source_region: str
    teacher_id: str
    student_token: str
    previous_source_appoint_id: str | None
    current_source_appoint_id: str | None
    award_generation: int | None
    score_entry_ids: tuple[str, ...]

    @classmethod
    def from_database(
        cls,
        value: Any,
        *,
        source_region: str,
        teacher_id: str,
        student_token: str,
    ) -> "FavoriteAttributionOutcomeV2":
        row = _database_object(value)
        action = row.get("action")
        if action not in _ATTRIBUTION_ACTIONS:
            _fail("ATTRIBUTION_OUTCOME_INVALID")
        if (
            row.get("source_region") != source_region
            or row.get("teacher_id") != teacher_id
            or row.get("student_token") != student_token
        ):
            _fail("ATTRIBUTION_OUTCOME_IDENTITY_MISMATCH")
        previous = _optional_string(row.get("previous_source_appoint_id"))
        current = _optional_string(row.get("current_source_appoint_id"))
        generation = row.get("award_generation")
        if generation is not None and (
            type(generation) is not int or generation < 1
        ):
            _fail("ATTRIBUTION_OUTCOME_INVALID")
        raw_score_ids = row.get("score_entry_ids")
        if not isinstance(raw_score_ids, Sequence) or isinstance(
            raw_score_ids, (str, bytes, bytearray)
        ):
            _fail("ATTRIBUTION_OUTCOME_INVALID")
        score_ids = tuple(_required_string(item) for item in raw_score_ids)
        if len(score_ids) != len(set(score_ids)):
            _fail("ATTRIBUTION_OUTCOME_INVALID")
        if action in {"AWARD", "REVERSE", "RESELECT"} and not score_ids:
            _fail("ATTRIBUTION_OUTCOME_SCORE_EVIDENCE_REQUIRED")
        if action in {"NONE", "HOLD", "RESTORE"} and score_ids:
            _fail("ATTRIBUTION_OUTCOME_SCORE_EVIDENCE_UNEXPECTED")
        return cls(
            action=str(action),
            source_region=source_region,
            teacher_id=teacher_id,
            student_token=student_token,
            previous_source_appoint_id=previous,
            current_source_appoint_id=current,
            award_generation=generation,
            score_entry_ids=score_ids,
        )

    @property
    def affected_teacher_ids(self) -> tuple[str, ...]:
        return (
            (self.teacher_id,)
            if self.action in {"AWARD", "REVERSE", "RESELECT"}
            else ()
        )


@dataclass(frozen=True, eq=False)
class FavoriteMaterializationResultV2(Mapping[str, int]):
    counts: Mapping[str, int]
    affected_teacher_ids: tuple[str, ...]
    attribution_outcomes: tuple[FavoriteAttributionOutcomeV2, ...]

    def __getitem__(self, key: str) -> int:
        return self.counts[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.counts)

    def __len__(self) -> int:
        return len(self.counts)


class DtsV2FavoriteProjectionRebuilder(Protocol):
    """Rebuild course/account/qualification in the observation transaction."""

    def rebuild_after_favorite(
        self,
        connection: Connection,
        outcome: FavoriteAttributionOutcomeV2,
    ) -> Mapping[str, int]: ...


class PostgresDtsV2FavoriteMaterializer:
    """Materialize Outbox observation requirements through owner functions."""

    def __init__(self, *, require_guarded_generation: bool = False) -> None:
        self.require_guarded_generation = require_guarded_generation

    def apply_teacher_student_plan(
        self,
        connection: Connection,
        plan: TeacherStudentMaterializationPlanV2,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> FavoriteMaterializationResultV2:
        if not isinstance(plan, TeacherStudentMaterializationPlanV2):
            _fail("PLAN_REQUIRED")
        if type(aggregate_revision) is not int or aggregate_revision < 1:
            _fail("AGGREGATE_REVISION_INVALID")
        if not isinstance(triggering_event_id, str) or not triggering_event_id:
            _fail("EVENT_ID_INVALID")
        projection_generation = _read_primary_projection_generation(
            connection,
            require_guarded=self.require_guarded_generation,
        )
        _lock_pair(
            connection,
            source_region=plan.source_region,
            teacher_id=plan.teacher_id,
            student_token=plan.student_token,
        )
        counts = {
            "observation_rows_created": 0,
            "observation_rows_requeued": 0,
            "observation_rows_unchanged": 0,
            "observation_rows_skipped_missing_evidence": 0,
            "attributions_held": 0,
        }
        outcomes: list[FavoriteAttributionOutcomeV2] = []
        affected_teacher_ids: set[str] = set()
        for observation in plan.observations:
            if not observation.has_persistable_identity:
                counts["observation_rows_skipped_missing_evidence"] += 1
                continue
            assert observation.appoint_id_type is not None
            assert observation.observed_at is not None
            result = _database_object(
                connection.execute(
                    text(
                        """
                        SELECT public.materialize_favorite_observation_v2(
                            :source_region,:source_appoint_id,
                            :appoint_id_type,:teacher_id,:teacher_id_type,
                            :student_token,:completion_participation_seq,
                            :observed_at,:rule_version,
                            :projection_generation,:triggering_event_id
                        )
                        """
                    ),
                    {
                        "source_region": observation.source_region,
                        "source_appoint_id": observation.source_appoint_id,
                        "appoint_id_type": observation.appoint_id_type,
                        "teacher_id": observation.teacher_id,
                        "teacher_id_type": observation.teacher_id_type,
                        "student_token": observation.student_token,
                        "completion_participation_seq": (
                            observation.completion_participation_seq
                        ),
                        "observed_at": observation.observed_at,
                        "rule_version": FAVORITE_RULE_VERSION,
                        "projection_generation": projection_generation,
                        "triggering_event_id": triggering_event_id,
                    },
                ).scalar_one()
            )
            status = result.get("status")
            if status == "CREATED":
                counts["observation_rows_created"] += 1
            elif status in {"REQUEUED", "EVIDENCE_ADVANCED"}:
                counts["observation_rows_requeued"] += 1
            elif status == "UNCHANGED":
                counts["observation_rows_unchanged"] += 1
            else:
                _fail("MATERIALIZE_RESULT_INVALID")
            counts["attributions_held"] += int(
                result.get("attribution_held") is True
            )
            raw_outcome = result.get("attribution_outcome")
            if raw_outcome is not None:
                outcome = FavoriteAttributionOutcomeV2.from_database(
                    raw_outcome,
                    source_region=plan.source_region,
                    teacher_id=plan.teacher_id,
                    student_token=plan.student_token,
                )
                outcomes.append(outcome)
                affected_teacher_ids.update(outcome.affected_teacher_ids)
        # Blacklist is a region-local current relationship threshold.  It is
        # reconciled for every TEACHER_STUDENT aggregate event, independently
        # of whether this event also produced a favorite observation and
        # without requiring any completed-course evidence.
        blacklist_result = connection.execute(
            text(
                """
                SELECT public.reconcile_blacklist_threshold_v2(
                    :source_region,:teacher_id,:aggregate_revision,
                    :projection_generation,:triggering_event_id,
                    CAST(:threshold_evidence AS jsonb)
                )
                """
            ),
            {
                "source_region": plan.source_region,
                "teacher_id": plan.teacher_id,
                "aggregate_revision": aggregate_revision,
                "projection_generation": projection_generation,
                "triggering_event_id": triggering_event_id,
                "threshold_evidence": json.dumps(
                    plan.blacklist_threshold.evidence,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            },
        ).scalar_one()
        if not isinstance(blacklist_result, Mapping):
            _fail("BLACKLIST_RECONCILE_RESULT_INVALID")
        return FavoriteMaterializationResultV2(
            counts=counts,
            affected_teacher_ids=tuple(sorted(affected_teacher_ids)),
            attribution_outcomes=tuple(outcomes),
        )


@dataclass
class FavoriteObservationWorkerResultV2:
    claimed: int = 0
    confirmed_true: int = 0
    confirmed_false: int = 0
    waiting_history: int = 0
    waiting_evidence: int = 0
    retries: int = 0
    dead: int = 0
    stale_requeued: int = 0
    score_entries_created: int = 0
    attributions_awarded: int = 0
    attributions_reversed: int = 0
    attributions_reselected: int = 0
    expired_reaped: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.__dict__.items()))


class DtsV2FavoriteObservationWorker:
    """Durable claim/commit worker; claim and evaluation use separate txs."""

    def __init__(
        self,
        bind: Engine,
        *,
        projection_rebuilder: DtsV2FavoriteProjectionRebuilder | None = None,
        primary_guard: DtsV2PrimaryTransactionGuard | None = None,
    ) -> None:
        if bind.dialect.name != "postgresql":
            _fail("POSTGRESQL_REQUIRED")
        self.engine = bind
        self.projection_rebuilder = projection_rebuilder
        self.primary_guard = primary_guard

    def run_once(
        self,
        *,
        worker_id: str,
        max_observations: int = 25,
        reap_limit: int = 25,
    ) -> dict[str, int]:
        _validate_worker_arguments(
            worker_id=worker_id,
            max_observations=max_observations,
            reap_limit=reap_limit,
        )
        result = FavoriteObservationWorkerResultV2()
        initial_generation: int | None = None
        if self.primary_guard is None:
            with self.engine.begin() as connection:
                mode = _read_pipeline_mode(connection)
            if mode != _V2_PRIMARY_MODE:
                # Revision-local compatibility path. Production construction
                # always supplies the protected transaction guard.
                return result.as_dict()
        else:
            with self.engine.begin() as connection:
                initial_generation = self.primary_guard.acquire(
                    connection,
                    component="FAVORITE",
                )
            if initial_generation is None:
                return result.as_dict()
        with self.engine.begin() as connection:
            reap_generation = self._guard_generation(connection)
            if self.primary_guard is not None and (
                reap_generation is None
                or reap_generation != initial_generation
            ):
                return result.as_dict()
            reaped = _database_object(
                connection.execute(
                    text(
                        "SELECT public.reap_expired_favorite_observations_v2("
                        ":reap_limit)"
                    ),
                    {"reap_limit": reap_limit},
                ).scalar_one()
            )
            result.expired_reaped = _nonnegative_int(
                reaped.get("reaped"), "REAPER_RESULT_INVALID"
            )
            result.dead += _nonnegative_int(
                reaped.get("dead"), "REAPER_RESULT_INVALID"
            )

        claims = self._claim(
            worker_id=worker_id,
            limit=max_observations,
        )
        result.claimed = len(claims)
        for claim in claims:
            try:
                with self.engine.begin() as connection:
                    process_generation = self._guard_generation(connection)
                    if self.primary_guard is not None and (
                        process_generation is None
                        or process_generation
                        != claim.runtime_projection_generation
                    ):
                        # Leave the lease intact. A later PRIMARY reaper can
                        # safely retry it under one stable generation.
                        continue
                    evaluation = evaluate_favorite_observation_claim_v2(
                        connection,
                        claim,
                    )
                    completion = _complete_claim(
                        connection,
                        claim=claim,
                        evaluation=evaluation,
                    )
                    outcome = _completion_attribution_outcome(
                        completion,
                        claim=claim,
                    )
                    if outcome.affected_teacher_ids:
                        if self.projection_rebuilder is None:
                            _fail("PROJECTION_REBUILDER_REQUIRED")
                        _validate_rebuild_counts(
                            self.projection_rebuilder.rebuild_after_favorite(
                                connection,
                                outcome,
                            )
                        )
                _apply_completion_counts(result, completion)
            except Exception:
                with self.engine.begin() as connection:
                    failure_generation = self._guard_generation(connection)
                    if self.primary_guard is not None and (
                        failure_generation is None
                        or failure_generation
                        != claim.runtime_projection_generation
                    ):
                        continue
                    failure = _fail_claim(connection, claim=claim)
                failure_status = failure.get("status")
                if failure_status == "RETRY":
                    result.retries += 1
                elif failure_status == "DEAD":
                    result.dead += 1
                elif failure_status == "STALE_REQUEUED":
                    result.stale_requeued += 1
                else:
                    _fail("FAILURE_RESULT_INVALID")
        return result.as_dict()

    def _claim(
        self,
        *,
        worker_id: str,
        limit: int,
    ) -> tuple[FavoriteObservationClaimV2, ...]:
        with self.engine.begin() as connection:
            claim_generation = self._guard_generation(connection)
            if self.primary_guard is not None and claim_generation is None:
                return ()
            rows = connection.execute(
                text(
                    """
                    SELECT claim
                    FROM public.claim_favorite_observations_v2(
                        :worker_id,:claim_limit
                    ) AS claim
                    """
                ),
                {
                    "worker_id": worker_id,
                    "claim_limit": limit,
                },
            ).scalars()
            claims = tuple(
                FavoriteObservationClaimV2.from_database(value)
                for value in rows
            )
            if self.primary_guard is None:
                return claims
            return tuple(
                replace(
                    claim,
                    runtime_projection_generation=claim_generation,
                )
                for claim in claims
            )

    def _guard_generation(self, connection: Connection) -> int | None:
        if self.primary_guard is None:
            return None
        return self.primary_guard.acquire(
            connection,
            component="FAVORITE",
        )


def evaluate_favorite_observation_claim_v2(
    connection: Connection,
    claim: FavoriteObservationClaimV2,
) -> FavoriteObservationEvaluationV2:
    if not isinstance(claim, FavoriteObservationClaimV2):
        _fail("CLAIM_REQUIRED")
    _lock_pair(
        connection,
        source_region=claim.source_region,
        teacher_id=claim.teacher_id,
        student_token=claim.student_token,
    )
    row = connection.execute(
        text(
            """
            SELECT course.completion_participation_seq,
                   course.completion_teacher_id,
                   course.completion_teacher_id_type,
                   course.completion_student_token,
                   course.completion_end_time,
                   course.completion_conflict_status,
                   course.completion_voided_at,
                   course.evidence_status,
                   clock_timestamp() AS database_now,
                   public.favorite_observation_evidence_fingerprint_v1(
                       :source_region,:source_appoint_id,:rule_version
                   ) AS evidence_fingerprint
            FROM public.source_courses AS course
            WHERE course.source_region=:source_region
              AND course.source_appoint_id=:source_appoint_id
            FOR SHARE
            """
        ),
        {
            "source_region": claim.source_region,
            "source_appoint_id": claim.source_appoint_id,
            "rule_version": FAVORITE_RULE_VERSION,
        },
    ).mappings().one_or_none()
    if row is None:
        _fail("COURSE_MISSING")
    if row.get("completion_voided_at") is not None:
        _fail("COURSE_VOIDED_REQUIRES_CORRECTION")
    completion_end = _aware_datetime(row.get("completion_end_time"))
    database_now = _aware_datetime(row.get("database_now"))
    if database_now < claim.observed_at:
        _fail("OBSERVATION_NOT_DUE")
    if (
        row.get("completion_participation_seq")
        != claim.completion_participation_seq
        or row.get("completion_teacher_id") != claim.teacher_id
        or row.get("completion_teacher_id_type") != claim.teacher_id_type
        or row.get("completion_student_token") != claim.student_token
        or completion_end + timedelta(hours=24) != claim.observed_at
    ):
        _fail("COMPLETION_IDENTITY_MISMATCH")
    fingerprint = row.get("evidence_fingerprint")
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
        _fail("EVALUATION_FINGERPRINT_INVALID")

    if row.get("completion_conflict_status") == "PENDING" or row.get(
        "evidence_status"
    ) == "SOURCE_CONFLICT":
        return FavoriteObservationEvaluationV2(
            status="WAITING_EVIDENCE",
            relation_state=None,
            relation_evidence_status="SOURCE_MISSING",
            relation_error_code="SOURCE_CONFLICT:COURSE_COMPLETION_PENDING",
            evidence_fingerprint=fingerprint,
        )

    pair = _Pair(
        claim.source_region,
        claim.teacher_id_type,
        claim.teacher_id,
        claim.student_token,
    )
    scopes = _read_preferred_scopes(connection, pair=pair)
    history_scope = scopes[
        (f"{claim.source_region}_teacher_favorite", "HISTORY")
    ]
    intervals = _read_favorite_intervals(connection, pair=pair)
    evaluation = evaluate_favorite_at(
        intervals,
        observed_at=claim.observed_at,
        history_complete=_history_covers(
            history_scope,
            history_from=completion_end,
            history_through=claim.observed_at,
        ),
    )
    return FavoriteObservationEvaluationV2(
        status=evaluation.status.value,
        relation_state=evaluation.relation_state,
        relation_evidence_status=evaluation.relation_evidence_status,
        relation_error_code=evaluation.error_code,
        evidence_fingerprint=fingerprint,
    )


def _complete_claim(
    connection: Connection,
    *,
    claim: FavoriteObservationClaimV2,
    evaluation: FavoriteObservationEvaluationV2,
) -> Mapping[str, Any]:
    return _database_object(
        connection.execute(
            text(
                """
                SELECT public.complete_favorite_observation_v2(
                    :source_region,:source_appoint_id,
                    :observation_revision,:lease_owner,:lease_token,
                    :expected_row_version,:claimed_evidence_revision,
                    :evidence_fingerprint,:result_status,:relation_state,
                    :relation_evidence_status,:relation_error_code,
                    :rule_version
                )
                """
            ),
            {
                "source_region": claim.source_region,
                "source_appoint_id": claim.source_appoint_id,
                "observation_revision": claim.observation_revision,
                "lease_owner": claim.lease_owner,
                "lease_token": claim.lease_token,
                "expected_row_version": claim.row_version,
                "claimed_evidence_revision": (
                    claim.claimed_evidence_revision
                ),
                "evidence_fingerprint": evaluation.evidence_fingerprint,
                "result_status": evaluation.status,
                "relation_state": evaluation.relation_state,
                "relation_evidence_status": (
                    evaluation.relation_evidence_status
                ),
                "relation_error_code": evaluation.relation_error_code,
                "rule_version": FAVORITE_RULE_VERSION,
            },
        ).scalar_one()
    )


def _fail_claim(
    connection: Connection,
    *,
    claim: FavoriteObservationClaimV2,
) -> Mapping[str, Any]:
    return _database_object(
        connection.execute(
            text(
                """
                SELECT public.fail_favorite_observation_v2(
                    :source_region,:source_appoint_id,
                    :observation_revision,:lease_owner,:lease_token,
                    :expected_row_version,:claimed_evidence_revision,
                    'FAVORITE_OBSERVATION_TRANSIENT'
                )
                """
            ),
            {
                "source_region": claim.source_region,
                "source_appoint_id": claim.source_appoint_id,
                "observation_revision": claim.observation_revision,
                "lease_owner": claim.lease_owner,
                "lease_token": claim.lease_token,
                "expected_row_version": claim.row_version,
                "claimed_evidence_revision": (
                    claim.claimed_evidence_revision
                ),
            },
        ).scalar_one()
    )


def _apply_completion_counts(
    result: FavoriteObservationWorkerResultV2,
    completion: Mapping[str, Any],
) -> None:
    status = completion.get("status")
    if status == "STALE_REQUEUED":
        result.stale_requeued += 1
        return
    names = {
        "CONFIRMED_TRUE": "confirmed_true",
        "CONFIRMED_FALSE": "confirmed_false",
        "WAITING_HISTORY": "waiting_history",
        "WAITING_EVIDENCE": "waiting_evidence",
    }
    target = names.get(str(status))
    if target is None:
        _fail("COMPLETION_RESULT_INVALID")
    setattr(result, target, getattr(result, target) + 1)
    for field_name in (
        "score_entries_created",
        "attributions_awarded",
        "attributions_reversed",
        "attributions_reselected",
    ):
        setattr(
            result,
            field_name,
            getattr(result, field_name)
            + _nonnegative_int(
                completion.get(field_name), "COMPLETION_RESULT_INVALID"
            ),
        )


def _completion_attribution_outcome(
    completion: Mapping[str, Any],
    *,
    claim: FavoriteObservationClaimV2,
) -> FavoriteAttributionOutcomeV2:
    raw = completion.get("attribution_outcome")
    if raw is None:
        _fail("ATTRIBUTION_OUTCOME_REQUIRED")
    return FavoriteAttributionOutcomeV2.from_database(
        raw,
        source_region=claim.source_region,
        teacher_id=claim.teacher_id,
        student_token=claim.student_token,
    )


def _validate_rebuild_counts(value: Any) -> None:
    if not isinstance(value, Mapping):
        _fail("PROJECTION_REBUILD_RESULT_INVALID")
    for name, count in value.items():
        if (
            not isinstance(name, str)
            or not name
            or type(count) is not int
            or count < 0
        ):
            _fail("PROJECTION_REBUILD_RESULT_INVALID")


def _lock_pair(
    connection: Connection,
    *,
    source_region: str,
    teacher_id: str,
    student_token: str,
) -> None:
    connection.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            ":pair_identity,0))"
        ),
        {
            "pair_identity": (
                f"tit:favorite-pair:{source_region}:"
                f"{teacher_id}:{student_token}"
            )
        },
    )


def _validate_worker_arguments(
    *,
    worker_id: str,
    max_observations: int,
    reap_limit: int,
) -> None:
    if not isinstance(worker_id, str) or _WORKER_ID.fullmatch(worker_id) is None:
        _fail("WORKER_ID_INVALID")
    if type(max_observations) is not int or not 1 <= max_observations <= 1000:
        _fail("BATCH_SIZE_INVALID")
    if type(reap_limit) is not int or not 1 <= reap_limit <= 1000:
        _fail("REAP_LIMIT_INVALID")


def _read_pipeline_mode(connection: Connection) -> str:
    mode = connection.execute(
        text(
            "SELECT mode FROM public.dts_pipeline_control "
            "WHERE control_id='PRIMARY'"
        )
    ).scalar_one_or_none()
    if mode not in _PIPELINE_MODES:
        _fail("PIPELINE_CONTROL_INVALID")
    return str(mode)


def _read_primary_projection_generation(
    connection: Connection,
    *,
    require_guarded: bool = False,
) -> int:
    if require_guarded:
        return guarded_projection_generation(connection)
    row = connection.execute(
        text(
            "SELECT mode,projection_generation "
            "FROM public.dts_pipeline_control "
            "WHERE control_id='PRIMARY'"
        )
    ).mappings().one_or_none()
    if row is None or row.get("mode") != _V2_PRIMARY_MODE:
        _fail("PIPELINE_MODE_NOT_PRIMARY")
    generation = row.get("projection_generation")
    if type(generation) is not int or generation < 1:
        _fail("PROJECTION_GENERATION_INVALID")
    return generation


def _database_object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("DATABASE_RESULT_INVALID")
    return value


def _aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            _fail("DATETIME_INVALID")
    else:
        _fail("DATETIME_INVALID")
    if parsed.utcoffset() is None:
        _fail("DATETIME_INVALID")
    return parsed


def _nonnegative_int(value: Any, code: str) -> int:
    if type(value) is not int or value < 0:
        _fail(code)
    return value


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _fail("ATTRIBUTION_OUTCOME_INVALID")
    return value


def _optional_string(value: Any) -> str | None:
    return None if value is None else _required_string(value)


def _fail(code: str) -> None:
    raise DtsV2FavoriteRuntimeError(f"DTS_V2_FAVORITE_{code}")


__all__ = [
    "DtsV2FavoriteObservationWorker",
    "DtsV2FavoriteProjectionRebuilder",
    "DtsV2FavoriteRuntimeError",
    "FAVORITE_RULE_VERSION",
    "FavoriteAttributionOutcomeV2",
    "FavoriteMaterializationResultV2",
    "FavoriteObservationClaimV2",
    "FavoriteObservationEvaluationV2",
    "PostgresDtsV2FavoriteMaterializer",
    "evaluate_favorite_observation_claim_v2",
]
