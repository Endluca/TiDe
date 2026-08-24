"""Settle REAL mandatory-growth completions from the shared task outbox.

The teacher app owns writes to ``task_assignments``.  A database trigger
records status changes in the internal outbox, and this worker is
the only path that turns a current REAL mandatory completion into score ledger rows.
Nothing in this module publishes an external message.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, delete, func, literal_column, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config_models import (
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .config_service import validate_config_payload
from .database import engine as default_engine
from .db_models import (
    AuditEventRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherQualificationRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from .qualification_award_gate import (
    irreversible_qualification_grants_enabled,
    resolve_irreversible_qualification_grants,
)
from .dts_pipeline_mode import read_optional_dts_pipeline_mode
from .services import GrowthService
from .task_catalog import MANDATORY_TASK_CODES
from .score_projection_lock import acquire_score_projection_lock


EVENT_TYPE = "task.assignment_changed.shared"
LEGACY_EVENT_TYPE = "task.assignment_changed.shared.v1"
ACCOUNT_DIMENSION = "NEW_TEACHER_TASK"
ENTRY_TYPE = "FIXED_TASK_AWARD"
SYSTEM_SOURCE_MODE = "SYSTEM_TASK_STATUS"
SOURCE_WIDE_SNAPSHOT_LABEL = "SOURCE_WIDE_CURRENT"
MAXIMUM_FIXED_GROWTH_POINTS = 30.0
FIXED_GROWTH_CODES = MANDATORY_TASK_CODES
DIRECT_EXTERNAL_SCALE_POLICY_VERSIONS = frozenset(
    {"v1", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"}
)
LEGAL_TASK_STATUSES = frozenset(
    {
        "ASSIGNED",
        "VIEWED",
        "IN_PROGRESS",
        "SUBMITTED",
        "UNDER_REVIEW",
        "COMPLETED",
        "FAILED",
        "EXPIRED",
        "WAIVED",
        "CANCELLED",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _deterministic_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


class SettlementDataError(RuntimeError):
    """A persisted fact violates the score-settlement contract."""


class SettlementEventDataError(SettlementDataError):
    """A malformed persisted event that can be failed without its siblings."""

    def __init__(self, outbox_id: str, reason: str) -> None:
        super().__init__(reason)
        self.outbox_id = outbox_id


@dataclass(frozen=True)
class _Outcome:
    code: str
    score_entries_created: int = 0
    account_score: float | None = None


class SharedTaskScoreSettlementWorker:
    """Claim and settle shared task status events one transaction at a time."""

    def __init__(
        self,
        bind: Engine = default_engine,
        *,
        retry_delay: timedelta = timedelta(minutes=5),
        max_retry_delay: timedelta = timedelta(hours=1),
        max_attempts: int = 5,
        retry_jitter_ratio: float = 0.2,
    ) -> None:
        if retry_delay < timedelta(0):
            raise ValueError("retry_delay must not be negative")
        if max_retry_delay < retry_delay:
            raise ValueError("max_retry_delay must be at least retry_delay")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not 0 <= retry_jitter_ratio <= 1:
            raise ValueError("retry_jitter_ratio must be between 0 and 1")
        self.engine = bind
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self.max_attempts = max_attempts
        self.retry_jitter_ratio = retry_jitter_ratio
        self._sessions = sessionmaker(bind=bind, expire_on_commit=False, class_=Session)

    def run_once(self, *, max_events: int = 100) -> dict[str, Any]:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")

        result: dict[str, Any] = {
            "claimed": 0,
            "published": 0,
            "settled": 0,
            "skipped_non_completed": 0,
            "skipped_non_real": 0,
            "skipped_non_fixed": 0,
            "failed": 0,
            "dead_lettered": 0,
            "score_entries_created": 0,
            "projection_refreshes": 0,
        }
        attempted = 0
        excluded_outbox_ids: set[str] = set()
        while attempted < max_events:
            outbox_id: str | None = None
            failure_outbox_ids: list[str] = []
            try:
                group_outcomes: list[_Outcome] = []
                score_entries_created = 0
                projection_refreshed = False
                with self._sessions() as session, session.begin():
                    acquire_score_projection_lock(session)
                    events = self._claim_next_group(
                        session,
                        limit=max_events - attempted,
                        excluded_outbox_ids=excluded_outbox_ids,
                    )
                    if not events:
                        return result
                    outbox_id = events[0].outbox_id
                    failure_outbox_ids = [
                        event.outbox_id for event in events
                    ]
                    prepared = self._prepare_events(session, events)
                    eligible_assignments = [
                        assignment
                        for _, _, assignment in prepared
                        if assignment is not None
                    ]
                    eligible_outbox_ids = [
                        event.outbox_id
                        for event, _, assignment in prepared
                        if assignment is not None
                    ]
                    if eligible_outbox_ids:
                        # A projection/config/database failure affects the whole
                        # same-teacher group. Retry all those events together
                        # instead of re-reading N-1 siblings on every loop.
                        failure_outbox_ids = eligible_outbox_ids
                    settlement: _Outcome | None = None
                    if eligible_assignments:
                        outbox_id = next(
                            event.outbox_id
                            for event, _, assignment in prepared
                            if assignment is not None
                        )
                        settlement = self._settle_eligible_assignment(
                            session,
                            eligible_assignments[0],
                            triggering_outbox_id=outbox_id,
                        )
                        score_entries_created = (
                            settlement.score_entries_created
                        )
                        projection_refreshed = True
                    published_at = _utcnow()
                    for event, outcome, assignment in prepared:
                        if assignment is not None:
                            outcome = _Outcome(
                                "SETTLED",
                                account_score=(
                                    settlement.account_score
                                    if settlement is not None
                                    else None
                                ),
                            )
                        assert outcome is not None
                        group_outcomes.append(outcome)
                        event.attempt_count = int(event.attempt_count or 0) + 1
                        event.status = "PUBLISHED"
                        event.last_error = None
                        event.published_at = published_at

                attempted += len(group_outcomes)
                result["claimed"] += len(group_outcomes)
                result["published"] += len(group_outcomes)
                result["score_entries_created"] += score_entries_created
                result["projection_refreshes"] += int(
                    projection_refreshed
                )
                for outcome in group_outcomes:
                    if outcome.code == "SETTLED":
                        result["settled"] += 1
                    elif outcome.code == "SKIPPED_NON_COMPLETED":
                        result["skipped_non_completed"] += 1
                    elif outcome.code == "SKIPPED_NON_REAL":
                        result["skipped_non_real"] += 1
                    elif outcome.code == "SKIPPED_NON_FIXED":
                        result["skipped_non_fixed"] += 1
            except Exception as exc:  # the settlement transaction has rolled back
                if isinstance(exc, SettlementEventDataError):
                    failure_outbox_ids = [exc.outbox_id]
                elif not failure_outbox_ids and outbox_id is not None:
                    failure_outbox_ids = [outbox_id]
                failure_outbox_ids = list(dict.fromkeys(failure_outbox_ids))
                if not failure_outbox_ids:
                    raise
                failed_count = len(failure_outbox_ids)
                attempted += failed_count
                result["failed"] += failed_count
                result["claimed"] += len(failure_outbox_ids)
                excluded_outbox_ids.update(failure_outbox_ids)
                failure_states = self._record_failures(
                    failure_outbox_ids,
                    exc,
                )
                result["dead_lettered"] += sum(
                    state == "DEAD_LETTER"
                    for state in failure_states.values()
                )

        return result

    def _claim_next_group(
        self,
        session: Session,
        *,
        limit: int,
        excluded_outbox_ids: set[str],
    ) -> list[OutboxEventRecord]:
        conditions = [
            OutboxEventRecord.status == literal_column("'PENDING'"),
            OutboxEventRecord.event_type.in_((EVENT_TYPE, LEGACY_EVENT_TYPE)),
            OutboxEventRecord.aggregate_type == "TASK_ASSIGNMENT",
            OutboxEventRecord.available_at <= _utcnow(),
        ]
        if excluded_outbox_ids:
            conditions.append(
                OutboxEventRecord.outbox_id.notin_(excluded_outbox_ids)
            )
        first = session.scalar(
            select(OutboxEventRecord)
            .where(
                *conditions,
            )
            .order_by(
                OutboxEventRecord.available_at,
                OutboxEventRecord.created_at,
                OutboxEventRecord.outbox_id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if first is None:
            return []
        if limit == 1:
            return [first]
        teacher_id = session.scalar(
            select(TaskAssignmentRecord.teacher_id).where(
                TaskAssignmentRecord.assignment_id == first.aggregate_id
            )
        )
        if teacher_id is None:
            return [first]
        same_teacher_assignments = select(
            TaskAssignmentRecord.assignment_id
        ).where(TaskAssignmentRecord.teacher_id == teacher_id)
        additional = list(
            session.scalars(
                select(OutboxEventRecord)
                .where(
                    *conditions,
                    OutboxEventRecord.outbox_id != first.outbox_id,
                    OutboxEventRecord.aggregate_id.in_(
                        same_teacher_assignments
                    ),
                )
                .order_by(
                    OutboxEventRecord.available_at,
                    OutboxEventRecord.created_at,
                    OutboxEventRecord.outbox_id,
                )
                .with_for_update(skip_locked=True)
                .limit(limit - 1)
            ).all()
        )
        return [first, *additional]

    @staticmethod
    def _event_data_error(
        event: OutboxEventRecord,
        reason: str,
    ) -> SettlementEventDataError:
        return SettlementEventDataError(event.outbox_id, reason)

    def _prepare_events(
        self,
        session: Session,
        events: list[OutboxEventRecord],
    ) -> list[
        tuple[
            OutboxEventRecord,
            _Outcome | None,
            TaskAssignmentRecord | None,
        ]
    ]:
        staged: list[
            tuple[OutboxEventRecord, _Outcome | None, str | None]
        ] = []
        assignment_ids: set[str] = set()
        for event in events:
            payload = event.payload
            if not isinstance(payload, dict):
                raise self._event_data_error(
                    event,
                    "OUTBOX_PAYLOAD_MUST_BE_AN_OBJECT",
                )
            to_status = str(payload.get("to_status") or "")
            if to_status not in LEGAL_TASK_STATUSES:
                raise self._event_data_error(
                    event,
                    "OUTBOX_TO_STATUS_IS_INVALID",
                )
            if to_status != "COMPLETED":
                staged.append(
                    (event, _Outcome("SKIPPED_NON_COMPLETED"), None)
                )
                continue
            assignment_id = str(
                payload.get("assignment_id")
                or event.aggregate_id
                or ""
            )
            if not assignment_id or assignment_id != event.aggregate_id:
                raise self._event_data_error(
                    event,
                    "OUTBOX_ASSIGNMENT_ID_MISMATCH",
                )
            assignment_ids.add(assignment_id)
            staged.append((event, None, assignment_id))

        assignments_by_id = (
            {
                item.assignment_id: item
                for item in session.scalars(
                    select(TaskAssignmentRecord)
                    .where(
                        TaskAssignmentRecord.assignment_id.in_(
                            assignment_ids
                        )
                    )
                    .order_by(TaskAssignmentRecord.assignment_id)
                ).all()
            }
            if assignment_ids
            else {}
        )

        prepared: list[
            tuple[
                OutboxEventRecord,
                _Outcome | None,
                TaskAssignmentRecord | None,
            ]
        ] = []
        for event, outcome, assignment_id in staged:
            if assignment_id is None:
                prepared.append((event, outcome, None))
                continue
            assignment = assignments_by_id.get(assignment_id)
            if assignment is None:
                raise self._event_data_error(
                    event,
                    "TASK_ASSIGNMENT_NOT_FOUND",
                )
            payload = event.payload
            assert isinstance(payload, dict)
            payload_teacher_id = str(payload.get("teacher_id") or "")
            if (
                payload_teacher_id
                and payload_teacher_id != assignment.teacher_id
            ):
                raise self._event_data_error(
                    event,
                    "OUTBOX_TEACHER_ID_MISMATCH",
                )
            if (
                assignment.status != "COMPLETED"
                or assignment.completed_at is None
            ):
                raise self._event_data_error(
                    event,
                    "COMPLETED_EVENT_DOES_NOT_MATCH_ASSIGNMENT",
                )
            if (
                assignment.task_kind != "FIXED_GROWTH"
                or assignment.creator_system != "TRIGGER_CENTER"
                or assignment.task_code not in FIXED_GROWTH_CODES
            ):
                prepared.append(
                    (event, _Outcome("SKIPPED_NON_FIXED"), None)
                )
                continue
            if assignment.source_mode != "REAL":
                prepared.append(
                    (event, _Outcome("SKIPPED_NON_REAL"), None)
                )
                continue
            prepared.append((event, None, assignment))
        return prepared

    def _prepare_event(
        self,
        session: Session,
        event: OutboxEventRecord,
    ) -> tuple[_Outcome | None, TaskAssignmentRecord | None]:
        _, outcome, assignment = self._prepare_events(
            session,
            [event],
        )[0]
        return outcome, assignment

    def _process_locked_event(
        self,
        session: Session,
        event: OutboxEventRecord,
    ) -> _Outcome:
        outcome, assignment = self._prepare_event(session, event)
        if outcome is not None:
            return outcome
        assert assignment is not None
        return self._settle_eligible_assignment(
            session,
            assignment,
            triggering_outbox_id=event.outbox_id,
        )

    def _settle_eligible_assignment(
        self,
        session: Session,
        assignment: TaskAssignmentRecord,
        *,
        triggering_outbox_id: str,
    ) -> _Outcome:
        # This row lock serializes different G-task events for one teacher even
        # when multiple workers claim different outbox rows concurrently.
        teacher = session.scalar(
            select(TeacherRecord)
            .where(TeacherRecord.teacher_id == assignment.teacher_id)
            .with_for_update()
        )
        if teacher is None:
            raise SettlementDataError("ASSIGNMENT_TEACHER_NOT_FOUND")
        self._require_current_source_wide_teacher(session, teacher)
        pipeline_mode = self._dts_pipeline_mode(session)

        baseline = list(
            session.scalars(
                select(TaskAssignmentRecord)
                .where(
                    TaskAssignmentRecord.teacher_id == assignment.teacher_id,
                    TaskAssignmentRecord.task_code.in_(FIXED_GROWTH_CODES),
                    TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
                )
                .order_by(TaskAssignmentRecord.task_code)
            ).all()
        )
        baseline_by_code = {
            item.task_code: item
            for item in baseline
            if (
                item.task_kind == "FIXED_GROWTH"
                and item.creator_system == "TRIGGER_CENTER"
                and item.source_mode == "REAL"
            )
        }
        config_snapshot = self._score_config_snapshot(session)
        configured_maximum = float(
            config_snapshot["payload"]["scoring_items"]["new_teacher_tasks"][
                "maximum_points"
            ]
        )
        if not math.isclose(
            configured_maximum,
            MAXIMUM_FIXED_GROWTH_POINTS,
            abs_tol=1e-9,
        ):
            raise SettlementDataError("FIXED_GROWTH_CONFIG_MAXIMUM_MUST_BE_30")

        points_by_assignment: dict[str, float] = {}
        template_ids = {
            item.template_version_id
            for item in baseline_by_code.values()
            if item.template_version_id
        }
        templates_by_id = {
            item.row_id: item
            for item in session.scalars(
                select(TaskTemplateRecord)
                .where(TaskTemplateRecord.row_id.in_(template_ids))
                .with_for_update()
            ).all()
        }
        for item in baseline_by_code.values():
            points_by_assignment[item.assignment_id] = self._template_points(
                item,
                templates_by_id.get(item.template_version_id),
            )
        score_rule_version = (
            "fixed-task:"
            f"{config_snapshot['policy_version']}:"
            f"{config_snapshot['payload_sha256'][:12]}"
        )
        completed_assignment_ids = {
            item.assignment_id
            for item in baseline_by_code.values()
            if item.status == "COMPLETED"
        }
        existing_award_assignment_ids = set(
            session.scalars(
                select(ScoreEntryRecord.task_assignment_id).where(
                    ScoreEntryRecord.entry_type == ENTRY_TYPE,
                    ScoreEntryRecord.task_assignment_id.in_(
                        completed_assignment_ids
                    ),
                )
            ).all()
        )
        created = 0
        for item in baseline_by_code.values():
            if item.status != "COMPLETED":
                continue
            if item.assignment_id in existing_award_assignment_ids:
                continue
            score = points_by_assignment[item.assignment_id]
            session.add(
                ScoreEntryRecord(
                    score_entry_id=_deterministic_id(
                        "FIXED-TASK-AWARD",
                        item.assignment_id,
                    ),
                    camp_enrollment_id=teacher.camp_enrollment_id,
                    lesson_id=None,
                    source_region=None,
                    source_appoint_id=None,
                    participation_seq=None,
                    teacher_id=teacher.teacher_id,
                    dimension=ACCOUNT_DIMENSION,
                    entry_type=ENTRY_TYPE,
                    delta_score=score,
                    reason_code=f"FIXED_GROWTH_COMPLETED:{item.task_code}",
                    evidence_status="CONFIRMED",
                    score_rule_version=score_rule_version,
                    occurred_at=item.completed_at,
                    recorded_at=_utcnow(),
                    reversal_of_score_entry_id=None,
                    task_assignment_id=item.assignment_id,
                    projection_origin="FIXED_TASK_LIVE",
                    materialized_by_run_id=None,
                    projection_generation=None,
                    idempotency_key=f"fixed-task-award:{item.assignment_id}",
                    payload={
                        "source_mode": SYSTEM_SOURCE_MODE,
                        "settlement_contract": "shared-fixed-growth.v1",
                        "assignment_id": item.assignment_id,
                        "task_code": item.task_code,
                        "template_version_id": item.template_version_id,
                        "score_value": score,
                        "score_config": config_snapshot,
                    },
                )
            )
            created += 1
        session.flush()

        ledger_score = self._valid_ledger_score(
            session,
            teacher_id=teacher.teacher_id,
            expected_points=points_by_assignment,
        )
        account = session.scalar(
            select(ScoreAccountRecord)
            .where(
                ScoreAccountRecord.teacher_id == teacher.teacher_id,
                ScoreAccountRecord.dimension == ACCOUNT_DIMENSION,
            )
            .with_for_update()
        )
        source_wide_previous_task_score = (
            float(account.current_score) if account is not None else 0.0
        )
        if account is None:
            account = ScoreAccountRecord(
                teacher_id=teacher.teacher_id,
                dimension=ACCOUNT_DIMENSION,
                current_score=ledger_score,
                score_rule_version=score_rule_version,
                version=1,
                updated_at=_utcnow(),
                payload={},
            )
            session.add(account)
            previous_untrusted_score: float | None = None
            previous_source_mode: str | None = None
        else:
            previous_payload = account.payload if isinstance(account.payload, dict) else {}
            previous_source_mode = str(previous_payload.get("source_mode") or "") or None
            previous_untrusted_score = (
                float(account.current_score)
                if previous_source_mode != SYSTEM_SOURCE_MODE
                else None
            )

        old_payload = account.payload if isinstance(account.payload, dict) else {}
        cutover_payload = old_payload.get("cutover")
        if previous_untrusted_score is not None:
            cutover_payload = {
                "occurred_at": _utcnow().isoformat(),
                "previous_untrusted_score": previous_untrusted_score,
                "previous_source_mode": previous_source_mode or "UNSPECIFIED",
                "ledger_score_at_cutover": ledger_score,
                "trigger_assignment_id": assignment.assignment_id,
            }
            self._write_cutover_audit(
                session,
                teacher=teacher,
                assignment=assignment,
                previous_untrusted_score=previous_untrusted_score,
                previous_source_mode=previous_source_mode,
                ledger_score=ledger_score,
                config_snapshot=config_snapshot,
            )

        next_payload = {
            "source_mode": SYSTEM_SOURCE_MODE,
            "settlement_contract": "shared-fixed-growth.v1",
            "ledger_entry_type": ENTRY_TYPE,
            "maximum_points": MAXIMUM_FIXED_GROWTH_POINTS,
            "ledger_score": ledger_score,
            "last_settled_assignment_id": (
                assignment.assignment_id
                if account in session.new
                or not math.isclose(
                    float(account.current_score),
                    ledger_score,
                    abs_tol=1e-9,
                )
                else old_payload.get("last_settled_assignment_id")
            ),
            "score_config": config_snapshot,
        }
        if cutover_payload is not None:
            next_payload["cutover"] = cutover_payload

        account_changed = (
            not math.isclose(float(account.current_score), ledger_score, abs_tol=1e-9)
            or account.score_rule_version != score_rule_version
            or account.payload != next_payload
        )
        account.current_score = ledger_score
        account.score_rule_version = score_rule_version
        account.payload = next_payload
        projection_time = _utcnow()
        if account_changed:
            if account not in session.new:
                account.version = int(account.version or 0) + 1
            account.updated_at = projection_time
        session.flush()
        if pipeline_mode == "V2_PRIMARY":
            self._enqueue_v2_teacher_refresh(
                session,
                teacher_id=teacher.teacher_id,
                assignment_id=assignment.assignment_id,
                triggering_outbox_id=triggering_outbox_id,
            )
            return _Outcome(
                "SETTLED",
                score_entries_created=created,
                account_score=ledger_score,
            )

        qualification_grants_enabled = (
            irreversible_qualification_grants_enabled()
        )
        completed_count = sum(
            item.status == "COMPLETED" for item in baseline_by_code.values()
        )
        self._synchronize_current_score_projection(
            session,
            teacher=teacher,
            task_score=ledger_score,
            assignment_count=len(baseline_by_code),
            completed_count=completed_count,
            expected_count=len(FIXED_GROWTH_CODES),
            score_rule_version=score_rule_version,
            config_snapshot=config_snapshot,
            task_components=[
                {
                    "code": code,
                    "metric": "task_assignments.status",
                    "value": (
                        1
                        if baseline_by_code.get(code) is not None
                        and baseline_by_code[code].status == "COMPLETED"
                        else 0
                    ),
                    "points_per_unit": (
                        points_by_assignment[
                            baseline_by_code[code].assignment_id
                        ]
                        if baseline_by_code.get(code) is not None
                        else 0.0
                    ),
                    "score": (
                        points_by_assignment[
                            baseline_by_code[code].assignment_id
                        ]
                        if baseline_by_code.get(code) is not None
                        and baseline_by_code[code].status == "COMPLETED"
                        else 0.0
                    ),
                    "source_mode": (
                        SYSTEM_SOURCE_MODE
                        if baseline_by_code.get(code) is not None
                        else "TASK_BASELINE_INCOMPLETE"
                    ),
                    "assignment_id": (
                        baseline_by_code[code].assignment_id
                        if baseline_by_code.get(code) is not None
                        else None
                    ),
                    "status": (
                        baseline_by_code[code].status
                        if baseline_by_code.get(code) is not None
                        else None
                    ),
                    "template_version_id": (
                        baseline_by_code[code].template_version_id
                        if baseline_by_code.get(code) is not None
                        else None
                    ),
                }
                for code in FIXED_GROWTH_CODES
            ],
            occurred_at=projection_time,
            previous_task_score_override=source_wide_previous_task_score,
            qualification_grants_enabled=qualification_grants_enabled,
        )
        self._synchronize_source_wide_qualification(
            session,
            teacher=teacher,
            raw_total_score=float(teacher.total_score),
            assignment_count=len(baseline_by_code),
            completed_count=completed_count,
            expected_count=len(FIXED_GROWTH_CODES),
            config_snapshot=config_snapshot,
            occurred_at=projection_time,
            qualification_grants_enabled=qualification_grants_enabled,
        )
        return _Outcome(
            "SETTLED",
            score_entries_created=created,
            account_score=ledger_score,
        )

    @staticmethod
    def _dts_pipeline_mode(session: Session) -> str | None:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return None
        return read_optional_dts_pipeline_mode(session)

    @staticmethod
    def _enqueue_v2_teacher_refresh(
        session: Session,
        *,
        teacher_id: str,
        assignment_id: str,
        triggering_outbox_id: str,
    ) -> None:
        value = session.execute(
            text(
                "SELECT public.enqueue_shared_task_teacher_refresh_v2("
                ":teacher_id,:assignment_id,:outbox_id)"
            ),
            {
                "teacher_id": teacher_id,
                "assignment_id": assignment_id,
                "outbox_id": triggering_outbox_id,
            },
        ).scalar_one()
        if not isinstance(value, dict):
            raise SettlementDataError("DTS_V2_TASK_REFRESH_RESULT_INVALID")
        if value.get("status") not in {"ENQUEUED", "NOOP", "NOOP_OLDER"}:
            raise SettlementDataError("DTS_V2_TASK_REFRESH_RESULT_INVALID")
        if (
            type(value.get("dirty_work_revision")) is not int
            or value["dirty_work_revision"] < 1
        ):
            raise SettlementDataError("DTS_V2_TASK_REFRESH_RESULT_INVALID")

    @staticmethod
    def _require_current_source_wide_teacher(
        session: Session,
        teacher: TeacherRecord,
    ) -> None:
        if teacher.source_snapshot_label != SOURCE_WIDE_SNAPSHOT_LABEL:
            raise SettlementDataError("SOURCE_WIDE_TEACHER_EXPECTED")
        if session.get(TeacherSourceWideRecord, teacher.teacher_id) is None:
            raise SettlementDataError("SOURCE_WIDE_TEACHER_SOURCE_NOT_FOUND")

    @staticmethod
    def _public_score(
        raw_total_score: float,
        config_snapshot: dict[str, Any],
    ) -> float:
        policy = config_snapshot["payload"]
        thresholds = policy["thresholds"]
        if policy.get("policy_version") in DIRECT_EXTERNAL_SCALE_POLICY_VERSIONS:
            return round(
                min(raw_total_score, float(thresholds["gold_external_score"])),
                2,
            )
        if raw_total_score < float(thresholds["graduation_raw_score"]):
            return round(
                raw_total_score
                * float(thresholds["graduation_external_score"])
                / float(thresholds["graduation_raw_score"]),
                2,
            )
        if raw_total_score < float(thresholds["gold_raw_score"]):
            return round(
                float(thresholds["graduation_external_score"])
                + (
                    raw_total_score
                    - float(thresholds["graduation_raw_score"])
                )
                * (
                    float(thresholds["gold_external_score"])
                    - float(thresholds["graduation_external_score"])
                )
                / (
                    float(thresholds["gold_raw_score"])
                    - float(thresholds["graduation_raw_score"])
                ),
                2,
            )
        return round(float(thresholds["gold_external_score"]), 2)

    @staticmethod
    def _replace_task_dimension(
        dimensions: Any,
        *,
        task_score: float,
        score_rule_version: str,
    ) -> list[dict[str, Any]]:
        existing = dimensions if isinstance(dimensions, list) else []
        replacement = {
            "code": ACCOUNT_DIMENSION,
            "label": "成长任务（必修）",
            "score": round(task_score, 2),
            "minimum": 0,
            "weight": 0,
            "data_mode": SYSTEM_SOURCE_MODE,
            "source_mode": SYSTEM_SOURCE_MODE,
            "score_rule_version": score_rule_version,
        }
        result: list[dict[str, Any]] = []
        replaced = False
        for dimension in existing:
            if (
                isinstance(dimension, dict)
                and dimension.get("code") == ACCOUNT_DIMENSION
            ):
                if not replaced:
                    result.append(replacement)
                    replaced = True
                continue
            if isinstance(dimension, dict):
                result.append(deepcopy(dimension))
        if not replaced:
            result.append(replacement)
        return result

    @classmethod
    def _synchronize_current_score_projection(
        cls,
        session: Session,
        *,
        teacher: TeacherRecord,
        task_score: float,
        assignment_count: int,
        completed_count: int,
        expected_count: int,
        score_rule_version: str,
        config_snapshot: dict[str, Any],
        task_components: list[dict[str, Any]],
        occurred_at: datetime,
        previous_task_score_override: float | None,
        qualification_grants_enabled: bool,
    ) -> None:
        """Persist task status into the current source-wide projection only."""

        now = occurred_at

        original_teacher_payload = deepcopy(teacher.payload or {})
        original_total_score = float(teacher.total_score)
        original_graduation_state = teacher.graduation_state
        original_gold_qualified = bool(teacher.gold_qualified)
        teacher_payload = deepcopy(original_teacher_payload)
        payload_inputs = deepcopy(teacher_payload.get("metric_inputs") or {})
        previous_task_score = float(
            previous_task_score_override
            if previous_task_score_override is not None
            else payload_inputs.get("new_teacher_task_score", 0)
        )
        previous_raw_total = float(
            teacher_payload.get("raw_total_score", teacher.total_score)
        )
        raw_total_score = round(
            previous_raw_total - previous_task_score + task_score,
            2,
        )
        public_total_score = cls._public_score(
            raw_total_score,
            config_snapshot,
        )
        task_provenance = {
            "source_mode": SYSTEM_SOURCE_MODE,
            "source_field": "task_assignments.status",
            "source_fields": [
                "task_assignments.status",
                "task_assignments.template_version_id",
                "task_templates.payload.score_value",
            ],
            "batch_id": None,
            "note": (
                "Mandatory-growth points are the configured values of current "
                "COMPLETED assignments in the current mandatory catalog."
            ),
        }
        input_updates = {
            "new_teacher_task_score": round(task_score, 2),
            "mandatory_task_assignment_count": assignment_count,
            "mandatory_task_completed_count": completed_count,
            "mandatory_task_expected_count": expected_count,
        }

        payload_inputs.update(input_updates)
        payload_provenance = deepcopy(
            teacher_payload.get("metric_provenance") or {}
        )
        payload_provenance.update(
            {
                "new_teacher_task_score": deepcopy(task_provenance),
                "mandatory_task_assignment_count": deepcopy(task_provenance),
                "mandatory_task_completed_count": deepcopy(task_provenance),
            }
        )
        capacity_score = float(payload_inputs.get("capacity_score", 0))
        teacher_payload.update(
            {
                "metric_inputs": payload_inputs,
                "metric_provenance": payload_provenance,
                "dimensions": cls._replace_task_dimension(
                    teacher_payload.get("dimensions"),
                    task_score=task_score,
                    score_rule_version=score_rule_version,
                ),
                "new_teacher_task_score": round(task_score, 2),
                "base_score": round(capacity_score + task_score, 2),
                "raw_total_score": raw_total_score,
                "total_score": raw_total_score,
                "external_display_score": public_total_score,
            }
        )
        policy = ScoreGraduationConfig.model_validate(config_snapshot["payload"])
        current_accounts = list(
            session.scalars(
                select(ScoreAccountRecord).where(
                    ScoreAccountRecord.teacher_id == teacher.teacher_id
                )
            ).all()
        )
        score_overrides: dict[str, dict[str, Any]] = {}
        for current_account in current_accounts:
            current_payload = (
                current_account.payload
                if isinstance(current_account.payload, dict)
                else {}
            )
            score_overrides[current_account.dimension] = {
                "score": float(current_account.current_score),
                "source_mode": str(
                    current_payload.get("source_mode")
                    or current_payload.get("data_mode")
                    or "PERSISTED_ACCOUNT"
                ),
            }
        score_overrides[ACCOUNT_DIMENSION] = {
            "score": round(task_score, 2),
            "assignment_count": assignment_count,
            "completed_count": completed_count,
            "expected_count": expected_count,
            "source_mode": SYSTEM_SOURCE_MODE,
        }
        quality_rule = getattr(policy.scoring_items, "classroom_quality", None)
        if (
            quality_rule is not None
            and getattr(quality_rule, "metric", None)
            == "lesson_hardware_quality_passed"
        ):
            quality_account = next(
                (
                    item
                    for item in current_accounts
                    if item.dimension == "CLASS_QUALITY"
                ),
                None,
            )
            quality_score = float(
                quality_account.current_score
                if quality_account is not None
                else 0
            )
            score_overrides["CLASS_QUALITY"] = {
                "score": quality_score,
                "count": (
                    quality_score / float(quality_rule.points_per_unit)
                    if float(quality_rule.points_per_unit)
                    else 0.0
                ),
                "source_mode": (
                    str(
                        (
                            quality_account.payload
                            if quality_account is not None
                            and isinstance(quality_account.payload, dict)
                            else {}
                        ).get("source_mode")
                        or "DERIVED_REAL"
                    )
                ),
            }
        projector = GrowthService(
            None,  # type: ignore[arg-type]
            config_reader=lambda key: (
                deepcopy(config_snapshot["payload"])
                if ConfigKey(key) == ConfigKey.SCORE_GRADUATION
                else None
            ),
        )
        projected = projector._project_teacher_scoring(
            teacher_payload,
            (policy, "PUBLISHED"),
            score_overrides,
        )
        previous_graduation_earned = bool(
            original_teacher_payload.get("graduation_qualified")
            or original_graduation_state == "GRADUATED"
        )
        previous_gold_earned = bool(
            original_teacher_payload.get("gold_qualified")
            or original_gold_qualified
        )
        grant_decision = resolve_irreversible_qualification_grants(
            previous_graduation_earned=previous_graduation_earned,
            previous_gold_earned=previous_gold_earned,
            graduation_current=bool(projected["graduation_criteria_met"]),
            gold_current=bool(projected["gold_criteria_met"]),
            grants_enabled=qualification_grants_enabled,
        )
        projected.update(
            {
                "graduation_state": (
                    "GRADUATED"
                    if grant_decision.graduation_earned
                    else "IN_CAMP"
                ),
                "graduation_qualified": grant_decision.graduation_earned,
                "gold_qualified": grant_decision.gold_earned,
                "irreversible_qualification_grants_enabled": (
                    grant_decision.grants_enabled
                ),
            }
        )
        teacher_payload["irreversible_qualification_grants_enabled"] = (
            grant_decision.grants_enabled
        )
        projected_task_score = float(
            next(
                (
                    item.get("score", 0)
                    for item in projected.get("dimensions") or []
                    if item.get("code") == ACCOUNT_DIMENSION
                ),
                0,
            )
        )
        projected_non_task_score = (
            float(projected.get("raw_total_score") or 0)
            - projected_task_score
        )
        persisted_non_task_score = previous_raw_total - previous_task_score
        if math.isclose(
            projected_non_task_score,
            persisted_non_task_score,
            abs_tol=1e-6,
        ):
            # Only grant new irreversible qualifications when all other
            # persisted dimensions can be reconstructed from current facts.
            teacher_payload.update(
                {
                    "graduation_state": projected["graduation_state"],
                    "graduation_score_threshold_met": projected[
                        "graduation_score_threshold_met"
                    ],
                    "graduation_criteria_met": projected[
                        "graduation_criteria_met"
                    ],
                    "graduation_qualified": projected[
                        "graduation_qualified"
                    ],
                    "gold_score_threshold_met": projected[
                        "gold_score_threshold_met"
                    ],
                    "gold_criteria_met": projected["gold_criteria_met"],
                    "gold_qualified": projected["gold_qualified"],
                    "hard_gates": deepcopy(projected["hard_gates"]),
                }
            )
            teacher.graduation_state = str(projected["graduation_state"])
            teacher.gold_qualified = bool(projected["gold_qualified"])
        else:
            teacher_payload["graduation_qualified"] = bool(
                teacher_payload.get("graduation_qualified")
                or teacher.graduation_state == "GRADUATED"
            )
            teacher_payload["gold_qualified"] = bool(
                teacher_payload.get("gold_qualified")
                or teacher.gold_qualified
            )

        projection_state_hash = _canonical_hash(
            {
                "teacher_id": teacher.teacher_id,
                "score_rule_version": score_rule_version,
                "components": task_components,
            }
        )
        projection_id = _deterministic_id(
            "SPR",
            projection_state_hash,
        )
        task_component_by_code = {
            str(item["code"]): item for item in task_components
        }
        existing_components = {
            item.component_code: item
            for item in session.scalars(
                select(ScoreComponentAccountRecord)
                .where(
                    ScoreComponentAccountRecord.teacher_id
                    == teacher.teacher_id,
                    ScoreComponentAccountRecord.dimension
                    == ACCOUNT_DIMENSION,
                )
                .with_for_update()
            ).all()
        }
        obsolete_codes = set(existing_components) - set(
            task_component_by_code
        )
        if obsolete_codes:
            session.execute(
                delete(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id
                    == teacher.teacher_id,
                    ScoreComponentAccountRecord.component_code.in_(
                        obsolete_codes
                    ),
                )
            )
        for code, component in task_component_by_code.items():
            component_payload = {
                **deepcopy(component),
                "score_config_version_id": config_snapshot["version_id"],
                "projection_id": projection_id,
                "projection_trigger": {
                    "type": "TASK_STATUS_UPDATED",
                    "ref": projection_state_hash,
                },
                "attribution_contract": (
                    "task-status-ledger-is-authoritative"
                ),
            }
            component_record = existing_components.get(code)
            component_values = {
                "dimension": ACCOUNT_DIMENSION,
                "source_scope": "TASK",
                "source_metric": "task_assignments.status",
                "unit_count": float(component.get("value") or 0),
                "points_per_unit": float(
                    component.get("points_per_unit") or 0
                ),
                "current_score": float(component.get("score") or 0),
                "lesson_attributed_count": 0,
                "lesson_attributed_score": 0.0,
                "unattributed_score": float(
                    component.get("score") or 0
                ),
                "reconciliation_status": "NOT_APPLICABLE",
                "score_rule_version": score_rule_version,
                "payload": component_payload,
            }
            if component_record is None:
                session.add(
                    ScoreComponentAccountRecord(
                        teacher_id=teacher.teacher_id,
                        component_code=code,
                        projection_revision=1,
                        calculated_at=now,
                        **component_values,
                    )
                )
            else:
                changed = any(
                    getattr(component_record, field) != value
                    for field, value in component_values.items()
                )
                for field, value in component_values.items():
                    setattr(component_record, field, value)
                if changed:
                    component_record.projection_revision = (
                        int(component_record.projection_revision or 0) + 1
                    )
                    component_record.calculated_at = now
        previous_payload_for_compare = deepcopy(original_teacher_payload)
        previous_payload_updated_at = previous_payload_for_compare.pop(
            "updated_at",
            None,
        )
        next_payload_for_compare = deepcopy(teacher_payload)
        next_payload_for_compare.pop("updated_at", None)
        teacher_changed = bool(
            previous_payload_for_compare != next_payload_for_compare
            or not math.isclose(
                original_total_score,
                raw_total_score,
                abs_tol=1e-9,
            )
            or original_graduation_state != teacher.graduation_state
            or original_gold_qualified != bool(teacher.gold_qualified)
        )
        if teacher_changed:
            teacher_payload["updated_at"] = now.isoformat()
            teacher.updated_at = now
        elif previous_payload_updated_at is not None:
            teacher_payload["updated_at"] = previous_payload_updated_at
        else:
            teacher_payload.pop("updated_at", None)
        teacher.total_score = raw_total_score
        teacher.payload = teacher_payload
        session.flush()

    @staticmethod
    def _synchronize_source_wide_qualification(
        session: Session,
        *,
        teacher: TeacherRecord,
        raw_total_score: float,
        assignment_count: int,
        completed_count: int,
        expected_count: int,
        config_snapshot: dict[str, Any],
        occurred_at: datetime,
        qualification_grants_enabled: bool,
    ) -> None:
        """Refresh only task-dependent qualification state for a source teacher.

        Source-wide changes have their own field-routed worker.  A task status
        event must not rescan that teacher's lessons, so this path combines the
        newly settled task total with the last persisted non-task gate evidence.
        """

        if teacher.source_snapshot_label != SOURCE_WIDE_SNAPSHOT_LABEL:
            raise SettlementDataError("SOURCE_WIDE_TEACHER_EXPECTED")
        qualification = session.get(
            TeacherQualificationRecord,
            teacher.teacher_id,
        )
        if qualification is None:
            raise SettlementDataError(
                "SOURCE_WIDE_QUALIFICATION_NOT_FOUND"
            )
        gate_results = deepcopy(qualification.gate_results or {})
        required_gate_fields = {
            "l0_complaint_count",
            "l0_complaint_evidence_status",
            "late_count",
            "early_count",
            "absent_count",
            "attendance_evidence_status",
        }
        if not required_gate_fields.issubset(gate_results):
            raise SettlementDataError(
                "SOURCE_WIDE_QUALIFICATION_GATE_STATE_INCOMPLETE"
            )

        def persisted_count(field: str) -> int:
            value = gate_results[field]
            if isinstance(value, bool):
                raise SettlementDataError(
                    f"SOURCE_WIDE_QUALIFICATION_GATE_INVALID:{field}"
                )
            try:
                normalized = int(value)
            except (TypeError, ValueError) as exc:
                raise SettlementDataError(
                    f"SOURCE_WIDE_QUALIFICATION_GATE_INVALID:{field}"
                ) from exc
            if normalized < 0:
                raise SettlementDataError(
                    f"SOURCE_WIDE_QUALIFICATION_GATE_INVALID:{field}"
                )
            return normalized

        policy = ScoreGraduationConfig.model_validate(
            config_snapshot["payload"]
        )
        graduation_gate = policy.hard_gates.graduation
        gold_gate = policy.hard_gates.gold
        try:
            required_task_count = int(
                graduation_gate.required_mandatory_task_count
            )
            maximum_l0_count = int(
                graduation_gate.maximum_l0_complaint_count
            )
            maximum_late_count = int(gold_gate.maximum_late_count)
            maximum_early_count = int(gold_gate.maximum_early_count)
            maximum_absent_count = int(gold_gate.maximum_absent_count)
        except AttributeError as exc:
            raise SettlementDataError(
                "SOURCE_WIDE_POLICY_HARD_GATES_UNSUPPORTED"
            ) from exc

        if session.get(TeacherSourceWideRecord, teacher.teacher_id) is None:
            raise SettlementDataError("SOURCE_WIDE_TEACHER_SOURCE_NOT_FOUND")
        source_present = True
        l0_count = persisted_count("l0_complaint_count")
        late_count = persisted_count("late_count")
        early_count = persisted_count("early_count")
        absent_count = persisted_count("absent_count")
        complaint_confirmed = (
            str(gate_results["l0_complaint_evidence_status"])
            == "CONFIRMED"
        )
        attendance_confirmed = (
            str(gate_results["attendance_evidence_status"])
            == "CONFIRMED"
        )
        graduation_current = bool(
            source_present
            and raw_total_score
            >= float(policy.thresholds.graduation_raw_score)
            and assignment_count == expected_count
            and completed_count == required_task_count
            and complaint_confirmed
            and l0_count <= maximum_l0_count
        )
        gold_current = bool(
            graduation_current
            and raw_total_score >= float(policy.thresholds.gold_raw_score)
            and attendance_confirmed
            and late_count <= maximum_late_count
            and early_count <= maximum_early_count
            and absent_count <= maximum_absent_count
        )
        previous_graduation_earned = bool(
            qualification.graduation_qualified
        )
        previous_gold_earned = bool(qualification.gold_qualified)
        grant_decision = resolve_irreversible_qualification_grants(
            previous_graduation_earned=previous_graduation_earned,
            previous_gold_earned=previous_gold_earned,
            graduation_current=graduation_current,
            gold_current=gold_current,
            grants_enabled=qualification_grants_enabled,
        )
        gold_earned = grant_decision.gold_earned
        graduation_earned = grant_decision.graduation_earned
        graduation_qualified_at = qualification.graduation_qualified_at
        graduation_score_locked = qualification.graduation_score_locked
        gold_qualified_at = qualification.gold_qualified_at
        if not previous_graduation_earned and graduation_earned:
            graduation_qualified_at = occurred_at
        if graduation_earned and graduation_score_locked is None:
            graduation_score_locked = 100.0
        if not previous_gold_earned and gold_earned:
            gold_qualified_at = occurred_at

        gate_results.update(
            {
                "source_teacher_present": source_present,
                "mandatory_task_assignment_count": assignment_count,
                "mandatory_task_completed_count": completed_count,
                "mandatory_task_expected_count": expected_count,
                "raw_total_score": raw_total_score,
                "graduation_raw_score_threshold": float(
                    policy.thresholds.graduation_raw_score
                ),
                "gold_raw_score_threshold": float(
                    policy.thresholds.gold_raw_score
                ),
                "irreversible_qualification_grants_enabled": (
                    grant_decision.grants_enabled
                ),
            }
        )
        qualification_values = {
            "graduation_criteria_met": graduation_current,
            "graduation_qualified": graduation_earned,
            "graduation_qualified_at": graduation_qualified_at,
            "graduation_score_locked": graduation_score_locked,
            "gold_criteria_met": gold_current,
            "gold_qualified": gold_earned,
            "gold_qualified_at": gold_qualified_at,
            "score_rule_version": policy.policy_version,
            "gate_results": gate_results,
        }
        qualification_changed = any(
            getattr(qualification, field) != value
            for field, value in qualification_values.items()
        )
        if qualification_changed:
            for field, value in qualification_values.items():
                setattr(qualification, field, value)
            qualification.revision = int(qualification.revision or 0) + 1
            qualification.calculated_at = occurred_at

        teacher_payload = deepcopy(teacher.payload or {})
        teacher_payload.update(
            {
                "graduation_criteria_met": graduation_current,
                "graduation_qualified": graduation_earned,
                "graduation_state": (
                    "GRADUATED" if graduation_earned else "IN_CAMP"
                ),
                "gold_criteria_met": gold_current,
                "gold_qualified": gold_earned,
            }
        )
        teacher_values_changed = bool(
            teacher.payload != teacher_payload
            or teacher.graduation_state
            != ("GRADUATED" if graduation_earned else "IN_CAMP")
            or bool(teacher.gold_qualified) != gold_earned
        )
        teacher.payload = teacher_payload
        teacher.graduation_state = (
            "GRADUATED" if graduation_earned else "IN_CAMP"
        )
        teacher.gold_qualified = gold_earned
        if teacher_values_changed:
            teacher.updated_at = occurred_at
        session.flush()

    @staticmethod
    def _template_points(
        assignment: TaskAssignmentRecord,
        template: TaskTemplateRecord | None,
    ) -> float:
        if template is None:
            raise SettlementDataError("ASSIGNMENT_TEMPLATE_NOT_FOUND")
        payload = template.payload
        if not isinstance(payload, dict):
            raise SettlementDataError("ASSIGNMENT_TEMPLATE_PAYLOAD_INVALID")
        if (
            template.template_id != assignment.task_code
            or payload.get("template_id") != assignment.task_code
            or payload.get("dimension") != ACCOUNT_DIMENSION
            or payload.get("score_type") != "FIXED"
            or template.status not in {"PUBLISHED", "RETIRED"}
        ):
            raise SettlementDataError("ASSIGNMENT_TEMPLATE_CONTRACT_MISMATCH")
        try:
            score = float(payload["score_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SettlementDataError("ASSIGNMENT_TEMPLATE_SCORE_INVALID") from exc
        if not math.isfinite(score) or score <= 0 or score > MAXIMUM_FIXED_GROWTH_POINTS:
            raise SettlementDataError("ASSIGNMENT_TEMPLATE_SCORE_INVALID")
        return score

    @staticmethod
    def _score_config_snapshot(session: Session) -> dict[str, Any]:
        record = session.scalar(
            select(ConfigVersionRecord)
            .where(
                ConfigVersionRecord.config_key == ConfigKey.SCORE_GRADUATION.value,
                ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
            )
            .order_by(ConfigVersionRecord.version_number.desc())
            .with_for_update()
        )
        if record is None:
            raise SettlementDataError("SCORE_GRADUATION_CONFIG_NOT_PUBLISHED")
        normalized = validate_config_payload(ConfigKey.SCORE_GRADUATION, record.payload)
        return {
            "config_key": ConfigKey.SCORE_GRADUATION.value,
            "version_id": record.version_id,
            "version_number": int(record.version_number),
            "policy_version": str(normalized["policy_version"]),
            "payload_sha256": _canonical_hash(normalized),
            "payload": normalized,
        }

    @staticmethod
    def _valid_ledger_score(
        session: Session,
        *,
        teacher_id: str,
        expected_points: dict[str, float],
    ) -> float:
        rows = session.execute(
            select(ScoreEntryRecord, TaskAssignmentRecord)
            .join(
                TaskAssignmentRecord,
                TaskAssignmentRecord.assignment_id == ScoreEntryRecord.task_assignment_id,
            )
            .where(
                ScoreEntryRecord.teacher_id == teacher_id,
                ScoreEntryRecord.dimension == ACCOUNT_DIMENSION,
                ScoreEntryRecord.entry_type == ENTRY_TYPE,
                ScoreEntryRecord.evidence_status == "CONFIRMED",
                TaskAssignmentRecord.teacher_id == teacher_id,
                TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
                TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
                TaskAssignmentRecord.task_code.in_(FIXED_GROWTH_CODES),
                TaskAssignmentRecord.source_mode == "REAL",
                TaskAssignmentRecord.status == "COMPLETED",
            )
        ).all()
        total = 0.0
        for entry, assignment in rows:
            expected = expected_points.get(assignment.assignment_id)
            if expected is None or not math.isclose(
                float(entry.delta_score),
                expected,
                abs_tol=1e-9,
            ):
                raise SettlementDataError("FIXED_TASK_LEDGER_ENTRY_IS_INVALID")
            total += float(entry.delta_score)
        if total < 0 or total > MAXIMUM_FIXED_GROWTH_POINTS + 1e-9:
            raise SettlementDataError("FIXED_TASK_LEDGER_TOTAL_EXCEEDS_30")
        return total

    @staticmethod
    def _write_cutover_audit(
        session: Session,
        *,
        teacher: TeacherRecord,
        assignment: TaskAssignmentRecord,
        previous_untrusted_score: float,
        previous_source_mode: str | None,
        ledger_score: float,
        config_snapshot: dict[str, Any],
    ) -> None:
        event_id = _deterministic_id(
            "SCORE-CUTOVER",
            f"{teacher.teacher_id}:{ACCOUNT_DIMENSION}",
        )
        if session.scalar(
            select(func.count()).select_from(AuditEventRecord).where(
                AuditEventRecord.event_id == event_id
            )
        ):
            return
        occurred_at = _utcnow()
        payload = {
            "schema_version": "score_account_cutover.shared_tasks.v1",
            "teacher_id": teacher.teacher_id,
            "camp_enrollment_id": teacher.camp_enrollment_id,
            "dimension": ACCOUNT_DIMENSION,
            "previous_untrusted_score": previous_untrusted_score,
            "previous_source_mode": previous_source_mode or "UNSPECIFIED",
            "ledger_score_at_cutover": ledger_score,
            "trigger_assignment_id": assignment.assignment_id,
            "cutover_at": occurred_at.isoformat(),
            "score_config": config_snapshot,
        }
        session.add(
            AuditEventRecord(
                event_id=event_id,
                event_type="score.account.cutover.shared_tasks.v1",
                teacher_id=teacher.teacher_id,
                task_id=assignment.assignment_id,
                case_id=None,
                occurred_at=occurred_at,
                actor_type="SYSTEM",
                payload_hash=_canonical_hash(payload),
                payload=payload,
            )
        )

    def _retry_delay_for(
        self,
        *,
        outbox_id: str,
        attempt_number: int,
    ) -> timedelta:
        base_seconds = self.retry_delay.total_seconds() * (
            2 ** max(attempt_number - 1, 0)
        )
        digest = hashlib.sha256(
            f"{outbox_id}:{attempt_number}".encode("utf-8")
        ).digest()
        unit = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
        jitter_factor = 1 + ((unit * 2) - 1) * self.retry_jitter_ratio
        delay_seconds = min(
            max(base_seconds * jitter_factor, 0.0),
            self.max_retry_delay.total_seconds(),
        )
        return timedelta(seconds=delay_seconds)

    def _record_failure(
        self,
        outbox_id: str,
        exc: Exception,
    ) -> str | None:
        return self._record_failures([outbox_id], exc).get(outbox_id)

    def _record_failures(
        self,
        outbox_ids: list[str],
        exc: Exception,
    ) -> dict[str, str | None]:
        if not outbox_ids:
            return {}
        safe_message = (
            f"{type(exc).__name__}:{str(exc)}"
            if isinstance(exc, SettlementDataError)
            else type(exc).__name__
        )[:1000]
        try:
            with self._sessions() as session, session.begin():
                events = list(
                    session.scalars(
                        select(OutboxEventRecord)
                        .where(
                            OutboxEventRecord.outbox_id.in_(outbox_ids),
                            OutboxEventRecord.status
                            == literal_column("'PENDING'"),
                        )
                        .order_by(OutboxEventRecord.outbox_id)
                        .with_for_update(skip_locked=True)
                    ).all()
                )
                states: dict[str, str | None] = {
                    outbox_id: None for outbox_id in outbox_ids
                }
                for event in events:
                    attempt_number = int(event.attempt_count or 0) + 1
                    event.attempt_count = attempt_number
                    event.last_error = safe_message
                    event.published_at = None
                    if attempt_number >= self.max_attempts:
                        event.status = "DEAD_LETTER"
                        event.available_at = _utcnow()
                        states[event.outbox_id] = "DEAD_LETTER"
                        continue
                    event.available_at = (
                        _utcnow()
                        + self._retry_delay_for(
                            outbox_id=event.outbox_id,
                            attempt_number=attempt_number,
                        )
                    )
                    states[event.outbox_id] = "RETRY_SCHEDULED"
                return states
        except Exception:
            # Settlement already rolled back and the event is still PENDING.
            # Failure reporting must never turn a retryable event into data loss.
            return {outbox_id: None for outbox_id in outbox_ids}


def settle_shared_task_scores_once(
    bind: Engine = default_engine,
    *,
    max_events: int = 100,
) -> dict[str, Any]:
    return SharedTaskScoreSettlementWorker(bind).run_once(max_events=max_events)


__all__ = [
    "ACCOUNT_DIMENSION",
    "ENTRY_TYPE",
    "EVENT_TYPE",
    "FIXED_GROWTH_CODES",
    "MAXIMUM_FIXED_GROWTH_POINTS",
    "SharedTaskScoreSettlementWorker",
    "settle_shared_task_scores_once",
]
