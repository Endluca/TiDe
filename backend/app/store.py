from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    AuditEventRecord,
    ComplaintCategoryRuleRecord,
    IdempotencyRecord,
    LessonSourceWideRecord,
    NotificationEventRecord,
    NotificationRecord,
    OpsCaseRecord,
    OpsDecisionRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from .mock_data import seed_teachers
from .personalized_rules import normalize_text
from .task_catalog import MANDATORY_TASK_CODE_SET


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _camp_enrollment_id(teacher_id: str) -> str:
    return f"CAMP-{teacher_id}"


def _normalize_ops_case_payload(record: OpsCaseRecord) -> dict[str, Any]:
    """Hydrate a case payload from authoritative columns for compatibility reads."""

    return {
        **deepcopy(record.payload or {}),
        "case_id": record.case_id,
        "case_type": record.case_type,
        "teacher_id": record.teacher_id,
        "task_id": record.task_id,
        "priority": record.priority,
        "status": record.status,
        "source_reason": record.source_reason,
        "external_action_status": record.external_action_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _normalize_notification_payload(record: NotificationRecord) -> dict[str, Any]:
    """Hydrate task-backed and task-less notifications through one read shape."""

    return {
        **deepcopy(record.payload or {}),
        "notification_id": record.notification_id,
        "task_id": record.task_id,
        "source_ref": record.source_ref,
        "teacher_id": record.teacher_id,
        "channel": record.channel,
        "priority": record.priority,
        "status": record.status,
        "requested_at": record.requested_at.isoformat(),
        "stored_at": record.stored_at.isoformat() if record.stored_at else None,
        "read_at": record.read_at.isoformat() if record.read_at else None,
        "clicked_at": record.clicked_at.isoformat() if record.clicked_at else None,
        "response_due_at": (
            record.response_due_at.isoformat() if record.response_due_at else None
        ),
        "failure_reason": record.failure_reason,
    }


def _normalize_audit_event_payload(record: AuditEventRecord) -> dict[str, Any]:
    """Merge immutable audit columns into legacy payload-only event rows."""

    return {
        **deepcopy(record.payload or {}),
        "event_id": record.event_id,
        "event_type": record.event_type,
        "teacher_id": record.teacher_id,
        "task_id": record.task_id,
        "case_id": record.case_id,
        "occurred_at": record.occurred_at.isoformat(),
        "actor_type": record.actor_type,
    }


class DatabaseStore:
    """SQLAlchemy-backed persistence with a compatibility working set.

    The service still operates on dictionaries so the frozen API contract stays
    unchanged. Every successful command checkpoints that working set into
    normalized tables. A new store instance reconstructs the same state from the
    database, so process restarts no longer erase domain facts.
    """

    def __init__(self, bind: Engine | None = None, *, seed_on_empty: bool = False) -> None:
        self.engine = bind or default_engine
        self._clear_working_set()
        loaded = self.reload()
        if not loaded and seed_on_empty:
            self.reset()

    def _clear_working_set(self) -> None:
        self.teachers: dict[str, dict] = {}
        self._teacher_payload_hashes: dict[str, str] = {}
        self.last_persist_stats: dict[str, int] = {
            "teachers_written": 0,
            "score_accounts_written": 0,
        }
        # template_versions is the immutable/versioned authoring fact set.
        # templates contains only the latest published version per template_id
        # and remains the bounded Agent/task-issuance runtime view.
        self.template_versions: dict[str, dict] = {}
        self.templates: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.executions: dict[str, dict] = {}
        self.ops_cases: dict[str, dict] = {}
        self.ops_decisions: dict[str, dict] = {}
        self.notifications: dict[str, dict] = {}
        self.notification_by_task: dict[str, str] = {}
        self.notification_events: dict[str, dict] = {}
        self.notification_event_results: dict[str, dict] = {}
        self.events: list[dict] = []
        self.dedupe_keys: dict[str, str] = {}
        self.command_results: dict[str, dict] = {}
        self.command_hashes: dict[str, str] = {}
        self.command_event_results: dict[str, dict] = {}
        self.command_event_hashes: dict[str, str] = {}
        self.notification_event_hashes: dict[str, str] = {}
        self.provider_event_results: dict[str, dict] = {}
        self.provider_event_hashes: dict[str, str] = {}
        self.global_event_hashes: dict[str, str] = {}
        self.global_event_types: dict[str, str] = {}
        self.agent_plans: dict[str, dict] = {}
        self.outbound_outputs: dict[str, dict] = {}
        self.outbox_events: dict[str, dict] = {}
        self.provider_calls: dict[str, dict] = {}

    def _merge_teacher_graph(self, session: Any, teacher: dict[str, Any]) -> int:
        teacher_id = teacher["teacher_id"]
        camp_id = teacher.get("camp_enrollment_id") or _camp_enrollment_id(teacher_id)
        graduation_state = teacher.get("graduation_state", "IN_CAMP")
        session.merge(
            TeacherRecord(
                teacher_id=teacher_id,
                camp_enrollment_id=camp_id,
                name=teacher.get("name", teacher_id),
                country=teacher.get("country"),
                timezone=teacher.get("timezone") or "UTC",
                camp_day=int(teacher.get("camp_day", 0)),
                graduation_state=graduation_state,
                gold_qualified=bool(teacher.get("gold_qualified", False)),
                total_score=float(teacher.get("total_score", 0)),
                graduation_threshold=float(teacher.get("graduation_threshold", 0)),
                data_mode=teacher.get("data_mode", "MOCK"),
                source_snapshot_label=teacher.get("source_snapshot_label"),
                payload={
                    **deepcopy(teacher),
                    "graduation_state": graduation_state,
                },
                updated_at=_parse_datetime(teacher.get("updated_at")) or _now(),
            )
        )
        score_accounts_written = 0
        for dimension in teacher.get("dimensions", []):
            score_accounts_written += 1
            code = dimension["code"]
            session.merge(
                ScoreAccountRecord(
                    teacher_id=teacher_id,
                    dimension=code,
                    current_score=float(dimension.get("score", 0)),
                    score_rule_version=dimension.get(
                        "score_rule_version",
                        "mock_score_v1"
                        if teacher.get("data_mode", "MOCK") == "MOCK"
                        else "new_teacher_30d_20260720",
                    ),
                    version=1,
                    payload=deepcopy(dimension),
                )
            )
        for entry in teacher.get("score_entries", []):
            entry_id = entry["score_entry_id"]
            session.merge(
                ScoreEntryRecord(
                    score_entry_id=entry_id,
                    camp_enrollment_id=camp_id,
                    lesson_id=entry.get("lesson_id"),
                    source_region=entry.get("source_region"),
                    source_appoint_id=entry.get("source_appoint_id"),
                    participation_seq=entry.get("participation_seq"),
                    teacher_id=teacher_id,
                    dimension=entry["dimension"],
                    entry_type=entry.get("entry_type", "INITIAL"),
                    delta_score=float(entry.get("delta_score", entry.get("delta", 0))),
                    reason_code=entry.get("reason_code", "UNKNOWN"),
                    evidence_status=entry.get("evidence_status", "CONFIRMED"),
                    score_rule_version=str(
                        entry.get("score_rule_version", entry.get("rule_version", 1))
                    ),
                    occurred_at=_parse_datetime(entry.get("occurred_at")),
                    reversal_of_score_entry_id=entry.get("reversal_of_score_entry_id"),
                    task_assignment_id=entry.get("task_assignment_id"),
                    projection_origin=entry.get(
                        "projection_origin", "V1_COMPAT_LIVE"
                    ),
                    materialized_by_run_id=entry.get("materialized_by_run_id"),
                    projection_generation=entry.get("projection_generation"),
                    idempotency_key=entry.get("idempotency_key", entry_id),
                    payload=deepcopy(entry),
                )
            )
        return score_accounts_written

    def _seed_reset_records(self, session: Any) -> None:
        """Seed local teacher/data examples without reviving retired tasks."""

        for teacher in seed_teachers():
            teacher = deepcopy(teacher)
            teacher.setdefault("data_mode", "MOCK")
            teacher.setdefault("source_snapshot_label", "LOCAL_MOCK_DEMO")
            self._merge_teacher_graph(session, teacher)

    def reset(self) -> None:
        """Reset the disposable SQLite test harness only.

        Runtime PostgreSQL must never be populated with the historical Mock
        teacher graph. Tests own an isolated SQLite database and may explicitly
        use this reset path.
        """

        if (
            os.getenv("APP_ENV", "").strip().lower() != "test"
            or self.engine.dialect.name != "sqlite"
        ):
            raise RuntimeError(
                "Mock domain reset is restricted to the disposable SQLite test database"
            )

        with session_scope(self.engine) as session:
            self._seed_reset_records(session)
        if not self.reload():
            raise RuntimeError("reset committed without the required Mock seed records")

    def reload(self) -> bool:
        self._clear_working_set()
        with session_scope(self.engine) as session:
            teachers = session.scalars(select(TeacherRecord)).all()
            if not teachers:
                return False
            self.teachers = {}
            for item in teachers:
                payload = deepcopy(item.payload)
                payload["graduation_state"] = item.graduation_state
                payload["graduation_qualified"] = (
                    item.graduation_state == "GRADUATED"
                )
                payload["gold_qualified"] = bool(item.gold_qualified)
                payload.setdefault("data_mode", item.data_mode)
                payload.setdefault(
                    "source_snapshot_label",
                    item.source_snapshot_label
                    or ("LOCAL_MOCK_DEMO" if item.data_mode == "MOCK" else None),
                )
                self.teachers[item.teacher_id] = payload
            self._teacher_payload_hashes = {
                teacher_id: _payload_hash(payload)
                for teacher_id, payload in self.teachers.items()
            }
            # The legacy Action-Schema template/task working set is retired.
            # Shared assignments stay in PostgreSQL and are read through the
            # transactional task service, never copied into process memory.
            self.template_versions = {}
            self.templates = {}
            self.tasks = {}
            self.executions = {}
            self.ops_cases = {
                item.case_id: _normalize_ops_case_payload(item)
                for item in session.scalars(select(OpsCaseRecord)).all()
            }
            self.ops_decisions = {
                item.decision_id: deepcopy(item.payload)
                for item in session.scalars(select(OpsDecisionRecord)).all()
            }
            self.notifications = {
                item.notification_id: _normalize_notification_payload(item)
                for item in session.scalars(select(NotificationRecord)).all()
            }
            self.notification_by_task = {
                item["task_id"]: item["notification_id"]
                for item in self.notifications.values()
                if item.get("task_id")
            }
            notification_events = session.scalars(select(NotificationEventRecord)).all()
            self.notification_events = {
                item.notification_event_id: deepcopy(item.payload) for item in notification_events
            }
            self.events = [
                _normalize_audit_event_payload(item)
                for item in session.scalars(select(AuditEventRecord).order_by(AuditEventRecord.sequence)).all()
            ]
            outbox = session.scalars(select(OutboxEventRecord)).all()
            self.outbox_events = {
                item.outbox_id: {
                    "outbox_id": item.outbox_id,
                    "event_id": item.event_id,
                    "aggregate_type": item.aggregate_type,
                    "aggregate_id": item.aggregate_id,
                    "event_type": item.event_type,
                    "payload": deepcopy(item.payload),
                    "status": item.status,
                    "available_at": item.available_at.isoformat(),
                    "attempt_count": item.attempt_count,
                    "last_error": item.last_error,
                    "created_at": item.created_at.isoformat(),
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                }
                for item in outbox
            }
            for record in session.scalars(select(IdempotencyRecord)).all():
                if record.scope == "TASK_DEDUPE" and record.resource_id:
                    self.dedupe_keys[record.idempotency_key] = record.resource_id
                elif record.scope == "COMMAND_KEY":
                    self.command_hashes[record.idempotency_key] = record.request_hash
                    self.command_results[record.idempotency_key] = deepcopy(record.response_payload or {})
                elif record.scope == "COMMAND_EVENT":
                    self.command_event_hashes[record.idempotency_key] = record.request_hash
                    self.command_event_results[record.idempotency_key] = deepcopy(record.response_payload or {})
                elif record.scope == "NOTIFICATION_EVENT":
                    self.notification_event_hashes[record.idempotency_key] = record.request_hash
                    self.notification_event_results[record.idempotency_key] = deepcopy(record.response_payload or {})
                elif record.scope == "PROVIDER_EVENT":
                    self.provider_event_hashes[record.idempotency_key] = record.request_hash
                    self.provider_event_results[record.idempotency_key] = deepcopy(record.response_payload or {})
                elif record.scope == "GLOBAL_EVENT":
                    self.global_event_hashes[record.idempotency_key] = record.request_hash
                    self.global_event_types[record.idempotency_key] = str(
                        (record.response_payload or {}).get("event_type", "UNKNOWN")
                    )
        return True

    def score_account_values(
        self,
        teacher_ids: set[str] | list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Project mandatory-growth points directly from shared task status.

        ``task_assignments`` is the current status fact and each assignment pins
        the exact ``task_templates`` row that supplies its configured points.
        Score-account rows remain the audit/settlement ledger, but the operator
        view must not lag behind the shared table while its worker catches up.
        """

        normalized_ids = {str(teacher_id) for teacher_id in teacher_ids if str(teacher_id)}
        if not normalized_ids:
            return {}
        fixed_codes = MANDATORY_TASK_CODE_SET
        with session_scope(self.engine) as session:
            rows = session.execute(
                select(TaskAssignmentRecord, TaskTemplateRecord)
                .join(
                    TaskTemplateRecord,
                    TaskTemplateRecord.row_id
                    == TaskAssignmentRecord.template_version_id,
                )
                .where(
                    TaskAssignmentRecord.teacher_id.in_(normalized_ids),
                    TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
                    TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
                    TaskAssignmentRecord.source_mode == "REAL",
                    TaskAssignmentRecord.task_code.in_(fixed_codes),
                )
            ).all()

            by_teacher: dict[
                str, dict[str, tuple[TaskAssignmentRecord, float]]
            ] = {teacher_id: {} for teacher_id in normalized_ids}
            invalid_teachers: set[str] = set()
            for assignment, template in rows:
                # Keep a fail-closed projection even when every assignment row
                # points at an unusable template payload.  Without this entry,
                # callers would fall back to the historical snapshot score and
                # make an invalid shared-task baseline look trustworthy.
                assignments_by_code = by_teacher.setdefault(assignment.teacher_id, {})
                payload = template.payload if isinstance(template.payload, dict) else {}
                try:
                    score_value = float(payload["score_value"])
                except (KeyError, TypeError, ValueError):
                    invalid_teachers.add(assignment.teacher_id)
                    continue
                if (
                    template.row_id != assignment.template_version_id
                    or template.template_id != assignment.task_code
                    or payload.get("template_id") != assignment.task_code
                    or payload.get("dimension") != "NEW_TEACHER_TASK"
                    or payload.get("score_type") != "FIXED"
                    or template.status not in {"PUBLISHED", "RETIRED"}
                    or not math.isfinite(score_value)
                    or score_value <= 0
                ):
                    invalid_teachers.add(assignment.teacher_id)
                    continue
                assignments_by_code[assignment.task_code] = (assignment, score_value)

            result: dict[str, dict[str, dict[str, Any]]] = {}
            for teacher_id, assignments_by_code in by_teacher.items():
                completed = {
                    code: score_value
                    for code, (assignment, score_value) in assignments_by_code.items()
                    if assignment.status == "COMPLETED"
                }
                baseline_complete = set(assignments_by_code) == fixed_codes
                source_mode = (
                    "TASK_STATUS_INVALID"
                    if teacher_id in invalid_teachers
                    else "SYSTEM_TASK_STATUS"
                    if baseline_complete
                    else "TASK_BASELINE_INCOMPLETE"
                )
                result.setdefault(teacher_id, {})["NEW_TEACHER_TASK"] = {
                    "score": round(sum(completed.values()), 2),
                    "source_mode": source_mode,
                    "score_rule_version": "shared-fixed-growth.current-status.v1",
                    "assignment_count": len(assignments_by_code),
                    "completed_count": len(completed),
                    "expected_count": len(fixed_codes),
                }

            lessons = list(
                session.scalars(
                    select(LessonSourceWideRecord).where(
                        LessonSourceWideRecord.teacher_id.in_(normalized_ids),
                        LessonSourceWideRecord.lesson_status == "end",
                    )
                ).all()
            )
            complaint_categories = {
                normalize_text(lesson.complaint_category_l3)
                for lesson in lessons
                if normalize_text(lesson.complaint_category_l3)
            }
            complaint_rules: dict[str, ComplaintCategoryRuleRecord] = {}
            if complaint_categories:
                rule_rows = session.scalars(
                    select(ComplaintCategoryRuleRecord)
                    .where(
                        ComplaintCategoryRuleRecord.category_l3_normalized.in_(
                            complaint_categories
                        )
                    )
                    .order_by(
                        ComplaintCategoryRuleRecord.created_at.desc(),
                        ComplaintCategoryRuleRecord.rule_id.desc(),
                    )
                ).all()
                for rule in rule_rows:
                    complaint_rules.setdefault(
                        normalize_text(rule.category_l3_normalized),
                        rule,
                    )

            complaint_counts: dict[str, dict[str, int]] = {}
            for lesson in lessons:
                counts = complaint_counts.setdefault(
                    lesson.teacher_id,
                    {"lesson_count": 0, "l0_count": 0, "unmapped_count": 0},
                )
                counts["lesson_count"] += 1
                categories = (
                    lesson.complaint_category_l1,
                    lesson.complaint_category_l2,
                    lesson.complaint_category_l3,
                )
                if not any(normalize_text(value) for value in categories):
                    continue
                key = normalize_text(lesson.complaint_category_l3)
                rule = complaint_rules.get(key)
                if rule is None:
                    counts["unmapped_count"] += 1
                    continue
                if normalize_text(rule.source_level).upper() in {"P0", "L0"}:
                    counts["l0_count"] += 1

            for teacher_id, counts in complaint_counts.items():
                lesson_count = counts["lesson_count"]
                l0_count = counts["l0_count"]
                unmapped_count = counts["unmapped_count"]
                result.setdefault(teacher_id, {})["L0_COMPLAINT"] = {
                    "count": l0_count,
                    "source_mode": (
                        "DERIVED_REAL"
                        if unmapped_count == 0
                        else "COMPLAINT_LEVEL_MAPPING_INCOMPLETE"
                    ),
                    "source_field": (
                        "lesson_source_wide.投诉三级分类+"
                        "complaint_category_rules.source_level"
                    ),
                    "lesson_count": lesson_count,
                    "unmapped_complaint_count": unmapped_count,
                }
            return result

    def shared_assignment_counts(self) -> dict[str, int]:
        """Return current counts from the shared PostgreSQL task ledger."""

        terminal_statuses = {"COMPLETED", "FAILED", "EXPIRED", "WAIVED", "CANCELLED"}
        with session_scope(self.engine) as session:
            statuses = session.scalars(select(TaskAssignmentRecord.status)).all()
        return {
            "total": len(statuses),
            "active": sum(status not in terminal_statuses for status in statuses),
            "completed": sum(status == "COMPLETED" for status in statuses),
        }

    def persist(self) -> None:
        self._ensure_outbox()
        teacher_hashes = {
            teacher_id: _payload_hash(teacher)
            for teacher_id, teacher in self.teachers.items()
        }
        changed_teacher_ids = [
            teacher_id
            for teacher_id, payload_hash in teacher_hashes.items()
            if self._teacher_payload_hashes.get(teacher_id) != payload_hash
        ]
        score_accounts_written = 0
        with session_scope(self.engine) as session:
            for teacher_id in changed_teacher_ids:
                teacher = self.teachers[teacher_id]
                teacher_id = teacher["teacher_id"]
                camp_id = teacher.get("camp_enrollment_id") or _camp_enrollment_id(teacher_id)
                graduation_state = teacher.get("graduation_state", "IN_CAMP")
                session.merge(
                    TeacherRecord(
                        teacher_id=teacher_id,
                        camp_enrollment_id=camp_id,
                        name=teacher.get("name", teacher_id),
                        country=teacher.get("country"),
                        timezone=teacher.get("timezone") or "UTC",
                        camp_day=int(teacher.get("camp_day", 0)),
                        graduation_state=graduation_state,
                        gold_qualified=bool(teacher.get("gold_qualified", False)),
                        total_score=float(teacher.get("total_score", 0)),
                        graduation_threshold=float(teacher.get("graduation_threshold", 0)),
                        data_mode=teacher.get("data_mode", "MOCK"),
                        source_snapshot_label=teacher.get("source_snapshot_label"),
                        payload={
                            **deepcopy(teacher),
                            "graduation_state": graduation_state,
                        },
                        updated_at=_parse_datetime(teacher.get("updated_at")) or _now(),
                    )
                )
                for dimension in teacher.get("dimensions", []):
                    score_accounts_written += 1
                    code = dimension["code"]
                    session.merge(
                        ScoreAccountRecord(
                            teacher_id=teacher_id,
                            dimension=code,
                            current_score=float(dimension.get("score", 0)),
                            score_rule_version=dimension.get(
                                "score_rule_version",
                                "mock_score_v1" if teacher.get("data_mode", "MOCK") == "MOCK"
                                else "new_teacher_30d_20260720",
                            ),
                            version=1,
                            payload=deepcopy(dimension),
                        )
                    )
                for entry in teacher.get("score_entries", []):
                    entry_id = entry["score_entry_id"]
                    session.merge(
                        ScoreEntryRecord(
                            score_entry_id=entry_id,
                            camp_enrollment_id=camp_id,
                            lesson_id=entry.get("lesson_id"),
                            source_region=entry.get("source_region"),
                            source_appoint_id=entry.get("source_appoint_id"),
                            participation_seq=entry.get("participation_seq"),
                            teacher_id=teacher_id,
                            dimension=entry["dimension"],
                            entry_type=entry.get("entry_type", "INITIAL"),
                            delta_score=float(entry.get("delta_score", entry.get("delta", 0))),
                            reason_code=entry.get("reason_code", "UNKNOWN"),
                            evidence_status=entry.get("evidence_status", "CONFIRMED"),
                            score_rule_version=str(entry.get("score_rule_version", entry.get("rule_version", 1))),
                            occurred_at=_parse_datetime(entry.get("occurred_at")),
                            reversal_of_score_entry_id=entry.get("reversal_of_score_entry_id"),
                            task_assignment_id=entry.get("task_assignment_id"),
                            projection_origin=entry.get(
                                "projection_origin", "V1_COMPAT_LIVE"
                            ),
                            materialized_by_run_id=entry.get("materialized_by_run_id"),
                            projection_generation=entry.get("projection_generation"),
                            idempotency_key=entry.get("idempotency_key", entry_id),
                            payload=deepcopy(entry),
                        )
                    )

            for notification in self.notifications.values():
                session.merge(
                    NotificationRecord(
                        notification_id=notification["notification_id"],
                        task_id=notification["task_id"],
                        teacher_id=notification["teacher_id"],
                        channel=notification["channel"],
                        priority=notification["priority"],
                        status=notification["status"],
                        requested_at=_parse_datetime(notification["requested_at"]) or _now(),
                        stored_at=_parse_datetime(notification.get("stored_at")),
                        read_at=_parse_datetime(notification.get("read_at")),
                        clicked_at=_parse_datetime(notification.get("clicked_at")),
                        response_due_at=_parse_datetime(notification.get("response_due_at")),
                        failure_reason=notification.get("failure_reason"),
                        payload=deepcopy(notification),
                    )
                )
            for event_id, event in self.notification_events.items():
                session.merge(
                    NotificationEventRecord(
                        notification_event_id=event_id,
                        notification_id=event["notification_id"],
                        delivery_status=event["delivery_status"],
                        occurred_at=_parse_datetime(event["occurred_at"]) or _now(),
                        failure_reason=event.get("failure_reason"),
                        request_hash=event["request_hash"],
                        payload=deepcopy(event),
                    )
                )

            for case in self.ops_cases.values():
                session.merge(
                    OpsCaseRecord(
                        case_id=case["case_id"],
                        case_type=case["case_type"],
                        teacher_id=case["teacher_id"],
                        task_id=case.get("task_id"),
                        priority=case["priority"],
                        status=case["status"],
                        source_reason=case.get("source_reason"),
                        external_action_status=case.get("external_action_status", "NOT_REQUESTED"),
                        created_at=_parse_datetime(case["created_at"]) or _now(),
                        payload=deepcopy(case),
                    )
                )
            for decision_id, decision in self.ops_decisions.items():
                session.merge(
                    OpsDecisionRecord(
                        decision_id=decision_id,
                        case_id=decision["case_id"],
                        decision=decision["decision"],
                        note=decision.get("note", ""),
                        decided_at=_parse_datetime(decision["decided_at"]) or _now(),
                        actor_type=decision.get("actor_type", "OPS_USER"),
                        payload=deepcopy(decision),
                    )
                )

            existing_audit_ids = set(session.scalars(select(AuditEventRecord.event_id)).all())
            for event in self.events:
                event_id = event["event_id"]
                occurred_at = _parse_datetime(event["occurred_at"]) or _now()
                if event_id not in existing_audit_ids:
                    session.add(
                        AuditEventRecord(
                            event_id=event_id,
                            event_type=event["event_type"],
                            teacher_id=event.get("teacher_id"),
                            task_id=event.get("task_id"),
                            case_id=event.get("case_id") or event.get("payload", {}).get("case_id"),
                            occurred_at=occurred_at,
                            actor_type=event.get("actor_type", "SYSTEM"),
                            payload_hash=_payload_hash(event),
                            payload=deepcopy(event),
                        )
                    )
            for outbox in self.outbox_events.values():
                session.merge(
                    OutboxEventRecord(
                        outbox_id=outbox["outbox_id"],
                        event_id=outbox["event_id"],
                        aggregate_type=outbox["aggregate_type"],
                        aggregate_id=outbox["aggregate_id"],
                        event_type=outbox["event_type"],
                        payload=deepcopy(outbox["payload"]),
                        status=outbox.get("status", "PENDING"),
                        available_at=_parse_datetime(outbox["available_at"]) or _now(),
                        attempt_count=int(outbox.get("attempt_count", 0)),
                        last_error=outbox.get("last_error"),
                        created_at=_parse_datetime(outbox["created_at"]) or _now(),
                        published_at=_parse_datetime(outbox.get("published_at")),
                    )
                )
            self._persist_idempotency(session)
        self._teacher_payload_hashes.update(
            {teacher_id: teacher_hashes[teacher_id] for teacher_id in changed_teacher_ids}
        )
        self.last_persist_stats = {
            "teachers_written": len(changed_teacher_ids),
            "score_accounts_written": score_accounts_written,
        }

    def _persist_idempotency(self, session: Any) -> None:
        for key, resource_id in self.dedupe_keys.items():
            session.merge(
                IdempotencyRecord(
                    scope="TASK_DEDUPE",
                    idempotency_key=key,
                    request_hash=_payload_hash({"resource_id": resource_id}),
                    resource_id=resource_id,
                )
            )
        groups = [
            ("COMMAND_KEY", self.command_hashes, self.command_results),
            ("COMMAND_EVENT", self.command_event_hashes, self.command_event_results),
            ("NOTIFICATION_EVENT", self.notification_event_hashes, self.notification_event_results),
            ("PROVIDER_EVENT", self.provider_event_hashes, self.provider_event_results),
        ]
        for scope, hashes, results in groups:
            for key, request_hash in hashes.items():
                session.merge(
                    IdempotencyRecord(
                        scope=scope,
                        idempotency_key=key,
                        request_hash=request_hash,
                        response_payload=deepcopy(results.get(key)),
                    )
                )
        for event_id, request_hash in self.global_event_hashes.items():
            session.merge(
                IdempotencyRecord(
                    scope="GLOBAL_EVENT",
                    idempotency_key=event_id,
                    request_hash=request_hash,
                    response_payload={"event_type": self.global_event_types[event_id]},
                )
            )

    def _ensure_outbox(self) -> None:
        known_event_ids = {item["event_id"] for item in self.outbox_events.values()}
        for event in self.events:
            if event["event_id"] in known_event_ids:
                continue
            event_type = event["event_type"]
            if event_type.startswith("ops_case."):
                aggregate_id = event.get("case_id") or event.get("task_id") or "SYSTEM"
            elif event_type.startswith("system_action."):
                aggregate_id = event.get("request_id") or event.get("case_id") or "SYSTEM"
            elif event_type.startswith("delivery_intent."):
                aggregate_id = event.get("delivery_intent_id") or event.get("task_id") or "SYSTEM"
            elif event_type.startswith("notification."):
                notification = event.get("notification") or {}
                aggregate_id = (
                    event.get("notification_id")
                    or notification.get("notification_id")
                    or notification.get("task_id")
                    or "SYSTEM"
                )
            else:
                task = event.get("task") or {}
                aggregate_id = (
                    event.get("task_id")
                    or task.get("task_id")
                    or event.get("case_id")
                    or event.get("teacher_id")
                    or "SYSTEM"
                )
            linked_output = next(
                (
                    output
                    for output in self.outbound_outputs.values()
                    if (output.get("payload") or {}).get("event_id") == event["event_id"]
                ),
                None,
            )
            available_at = event["occurred_at"]
            if (
                event_type == "delivery_intent.requested.v1"
                and linked_output is not None
                and linked_output.get("scheduled_at")
            ):
                available_at = linked_output["scheduled_at"]
            outbox_status = (
                "CANCELLED"
                if linked_output is not None and linked_output.get("status") == "CANCELLED"
                else "PENDING"
            )
            prefix = event["event_type"].split(".", 1)[0].upper()
            outbox_id = f"OUTBOX-{event['event_id']}"
            self.outbox_events[outbox_id] = {
                "outbox_id": outbox_id,
                "event_id": event["event_id"],
                "aggregate_type": prefix,
                "aggregate_id": aggregate_id,
                "event_type": event["event_type"],
                "payload": deepcopy(event),
                "status": outbox_status,
                "available_at": available_at,
                "attempt_count": 0,
                "last_error": None,
                "created_at": event["occurred_at"],
                "published_at": None,
            }

    def snapshot(self) -> dict:
        return deepcopy(
            {
                "teachers": list(self.teachers.values()),
                "templates": list(self.template_versions.values()),
                "tasks": list(self.tasks.values()),
                "executions": list(self.executions.values()),
                "ops_cases": list(self.ops_cases.values()),
                "ops_decisions": list(self.ops_decisions.values()),
                "notifications": list(self.notifications.values()),
                "agent_plans": list(self.agent_plans.values()),
                "outbound_outputs": list(self.outbound_outputs.values()),
                "outbox_events": list(self.outbox_events.values()),
                "events": self.events,
            }
        )

    def _refresh_runtime_templates(self) -> None:
        published: dict[str, dict] = {}
        for template in self.template_versions.values():
            if template.get("status") != "PUBLISHED":
                continue
            current = published.get(template["template_id"])
            if current is None or int(template["template_version"]) > int(current["template_version"]):
                published[template["template_id"]] = template
        self.templates = published


# Compatibility alias for existing service type hints. Runtime storage is SQL-backed.
InMemoryStore = DatabaseStore
store = DatabaseStore()
