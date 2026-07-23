"""Settle REAL mandatory-growth completions from the shared task outbox.

The teacher app owns writes to ``task_assignments``.  A database trigger
records status changes in the internal outbox, and this worker is
the only path that turns a REAL G01-G10 completion into score ledger rows.
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

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .config_models import ConfigKey, ConfigStatus, ConfigVersionRecord
from .config_service import validate_config_payload
from .database import engine as default_engine
from .db_models import (
    AuditEventRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherMetricSnapshotRecord,
    TeacherRecord,
)


EVENT_TYPE = "task.assignment_changed.shared"
LEGACY_EVENT_TYPE = "task.assignment_changed.shared.v1"
ACCOUNT_DIMENSION = "NEW_TEACHER_TASK"
ENTRY_TYPE = "FIXED_TASK_AWARD"
SYSTEM_SOURCE_MODE = "SYSTEM_TASK_STATUS"
MAXIMUM_FIXED_GROWTH_POINTS = 30.0
FIXED_GROWTH_CODES = tuple(f"G{number:02d}" for number in range(1, 11))
DIRECT_EXTERNAL_SCALE_POLICY_VERSIONS = frozenset({"v3", "v4", "v5", "v6"})
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
    ) -> None:
        self.engine = bind
        self.retry_delay = retry_delay
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
            "score_entries_created": 0,
        }

        for _ in range(max_events):
            outbox_id: str | None = None
            try:
                with self._sessions() as session, session.begin():
                    event = self._claim_next(session)
                    if event is None:
                        break
                    outbox_id = event.outbox_id
                    result["claimed"] += 1
                    outcome = self._process_locked_event(session, event)
                    event.attempt_count = int(event.attempt_count or 0) + 1
                    event.status = "PUBLISHED"
                    event.last_error = None
                    event.published_at = _utcnow()
                    result["published"] += 1
                    if outcome.code == "SETTLED":
                        result["settled"] += 1
                        result["score_entries_created"] += outcome.score_entries_created
                    elif outcome.code == "SKIPPED_NON_COMPLETED":
                        result["skipped_non_completed"] += 1
                    elif outcome.code == "SKIPPED_NON_REAL":
                        result["skipped_non_real"] += 1
                    elif outcome.code == "SKIPPED_NON_FIXED":
                        result["skipped_non_fixed"] += 1
            except Exception as exc:  # the settlement transaction has rolled back
                result["failed"] += 1
                if outbox_id is not None:
                    self._record_failure(outbox_id, exc)

        return result

    def _claim_next(self, session: Session) -> OutboxEventRecord | None:
        return session.scalar(
            select(OutboxEventRecord)
            .where(
                OutboxEventRecord.status == "PENDING",
                OutboxEventRecord.event_type.in_((EVENT_TYPE, LEGACY_EVENT_TYPE)),
                OutboxEventRecord.aggregate_type == "TASK_ASSIGNMENT",
                OutboxEventRecord.available_at <= _utcnow(),
            )
            .order_by(OutboxEventRecord.available_at, OutboxEventRecord.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    def _process_locked_event(
        self,
        session: Session,
        event: OutboxEventRecord,
    ) -> _Outcome:
        payload = event.payload
        if not isinstance(payload, dict):
            raise SettlementDataError("OUTBOX_PAYLOAD_MUST_BE_AN_OBJECT")
        to_status = str(payload.get("to_status") or "")
        if to_status not in LEGAL_TASK_STATUSES:
            raise SettlementDataError("OUTBOX_TO_STATUS_IS_INVALID")
        if to_status != "COMPLETED":
            return _Outcome("SKIPPED_NON_COMPLETED")

        assignment_id = str(payload.get("assignment_id") or event.aggregate_id or "")
        if not assignment_id or assignment_id != event.aggregate_id:
            raise SettlementDataError("OUTBOX_ASSIGNMENT_ID_MISMATCH")
        assignment = session.scalar(
            select(TaskAssignmentRecord)
            .where(TaskAssignmentRecord.assignment_id == assignment_id)
            .with_for_update()
        )
        if assignment is None:
            raise SettlementDataError("TASK_ASSIGNMENT_NOT_FOUND")
        if assignment.status != "COMPLETED" or assignment.completed_at is None:
            raise SettlementDataError("COMPLETED_EVENT_DOES_NOT_MATCH_ASSIGNMENT")
        if (
            assignment.task_kind != "FIXED_GROWTH"
            or assignment.creator_system != "TRIGGER_CENTER"
            or assignment.task_code not in FIXED_GROWTH_CODES
        ):
            return _Outcome("SKIPPED_NON_FIXED")
        if assignment.source_mode != "REAL":
            return _Outcome("SKIPPED_NON_REAL")

        # This row lock serializes different G-task events for one teacher even
        # when multiple workers claim different outbox rows concurrently.
        teacher = session.scalar(
            select(TeacherRecord)
            .where(TeacherRecord.teacher_id == assignment.teacher_id)
            .with_for_update()
        )
        if teacher is None:
            raise SettlementDataError("ASSIGNMENT_TEACHER_NOT_FOUND")

        baseline = list(
            session.scalars(
                select(TaskAssignmentRecord)
                .where(
                    TaskAssignmentRecord.teacher_id == assignment.teacher_id,
                    TaskAssignmentRecord.task_code.in_(FIXED_GROWTH_CODES),
                    TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
                )
                .order_by(TaskAssignmentRecord.task_code)
                .with_for_update()
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
        for item in baseline_by_code.values():
            template = session.scalar(
                select(TaskTemplateRecord)
                .where(TaskTemplateRecord.row_id == item.template_version_id)
                .with_for_update()
            )
            points_by_assignment[item.assignment_id] = self._template_points(item, template)
        score_rule_version = (
            "fixed-task:"
            f"{config_snapshot['policy_version']}:"
            f"{config_snapshot['payload_sha256'][:12]}"
        )
        created = 0
        for item in baseline_by_code.values():
            if item.status != "COMPLETED":
                continue
            existing = session.scalar(
                select(ScoreEntryRecord).where(
                    ScoreEntryRecord.entry_type == ENTRY_TYPE,
                    ScoreEntryRecord.task_assignment_id == item.assignment_id,
                )
            )
            if existing is not None:
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
        if account is None:
            account = ScoreAccountRecord(
                account_id=f"{teacher.teacher_id}:{ACCOUNT_DIMENSION}",
                teacher_id=teacher.teacher_id,
                camp_enrollment_id=teacher.camp_enrollment_id,
                dimension=ACCOUNT_DIMENSION,
                current_score=ledger_score,
                minimum_score=0,
                weight=0,
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
            "last_settled_assignment_id": assignment.assignment_id,
            "score_config": config_snapshot,
        }
        if cutover_payload is not None:
            next_payload["cutover"] = cutover_payload

        account_changed = (
            not math.isclose(float(account.current_score), ledger_score, abs_tol=1e-9)
            or account.camp_enrollment_id != teacher.camp_enrollment_id
            or account.score_rule_version != score_rule_version
            or account.payload != next_payload
        )
        account.current_score = ledger_score
        account.camp_enrollment_id = teacher.camp_enrollment_id
        account.score_rule_version = score_rule_version
        account.payload = next_payload
        if account_changed and account not in session.new:
            account.version = int(account.version or 0) + 1
        account.updated_at = _utcnow()
        session.flush()
        self._synchronize_current_score_projection(
            session,
            teacher=teacher,
            task_score=ledger_score,
            assignment_count=len(baseline_by_code),
            completed_count=sum(
                item.status == "COMPLETED" for item in baseline_by_code.values()
            ),
            expected_count=len(FIXED_GROWTH_CODES),
            score_rule_version=score_rule_version,
            config_snapshot=config_snapshot,
        )
        return _Outcome("SETTLED", score_entries_created=created, account_score=ledger_score)

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
    ) -> None:
        """Persist the current task score into the current teacher projection.

        The imported teacher snapshot starts with zero mandatory-task points.
        Once task status becomes authoritative, replace that historical task
        component instead of leaving the database total behind the API view.
        Historical non-current snapshots remain untouched.
        """

        now = _utcnow()
        snapshot = None
        if teacher.source_batch_id:
            snapshot = session.scalar(
                select(TeacherMetricSnapshotRecord)
                .where(
                    TeacherMetricSnapshotRecord.teacher_id == teacher.teacher_id,
                    TeacherMetricSnapshotRecord.batch_id
                    == teacher.source_batch_id,
                )
                .with_for_update()
            )

        teacher_payload = deepcopy(teacher.payload or {})
        payload_inputs = deepcopy(teacher_payload.get("metric_inputs") or {})
        previous_task_score = float(
            snapshot.new_teacher_task_score
            if snapshot is not None
            else payload_inputs.get("new_teacher_task_score", 0)
        )
        previous_raw_total = float(
            snapshot.raw_total_score
            if snapshot is not None
            else teacher_payload.get("raw_total_score", teacher.total_score)
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
            "batch_id": (
                snapshot.batch_id if snapshot is not None else teacher.source_batch_id
            ),
            "note": (
                "Mandatory-growth points are the configured values of current "
                "COMPLETED G01-G10 assignments."
            ),
        }
        input_updates = {
            "new_teacher_task_score": round(task_score, 2),
            "mandatory_task_assignment_count": assignment_count,
            "mandatory_task_completed_count": completed_count,
            "mandatory_task_expected_count": expected_count,
        }

        if snapshot is not None:
            snapshot_inputs = deepcopy(snapshot.metric_inputs or {})
            snapshot_inputs.update(input_updates)
            snapshot_provenance = deepcopy(snapshot.metric_provenance or {})
            snapshot_provenance.update(
                {
                    "new_teacher_task_score": deepcopy(task_provenance),
                    "mandatory_task_assignment_count": deepcopy(task_provenance),
                    "mandatory_task_completed_count": deepcopy(task_provenance),
                }
            )
            snapshot.new_teacher_task_score = round(task_score, 2)
            snapshot.raw_total_score = raw_total_score
            snapshot.public_total_score = public_total_score
            snapshot.metric_inputs = snapshot_inputs
            snapshot.metric_provenance = snapshot_provenance
            snapshot.updated_at = now

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
        capacity_score = float(
            payload_inputs.get(
                "capacity_score",
                snapshot.capacity_score if snapshot is not None else 0,
            )
        )
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
                "updated_at": now.isoformat(),
            }
        )
        teacher.total_score = raw_total_score
        teacher.payload = teacher_payload
        teacher.updated_at = now
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
            .with_for_update()
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

    def _record_failure(self, outbox_id: str, exc: Exception) -> None:
        safe_message = f"{type(exc).__name__}:{str(exc)}"[:1000]
        try:
            with self._sessions() as session, session.begin():
                event = session.scalar(
                    select(OutboxEventRecord)
                    .where(
                        OutboxEventRecord.outbox_id == outbox_id,
                        OutboxEventRecord.status == "PENDING",
                    )
                    .with_for_update(skip_locked=True)
                )
                if event is None:
                    return
                event.attempt_count = int(event.attempt_count or 0) + 1
                event.last_error = safe_message
                event.available_at = _utcnow() + self.retry_delay
                event.published_at = None
        except Exception:
            # Settlement already rolled back and the event is still PENDING.
            # Failure reporting must never turn a retryable event into data loss.
            return


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
