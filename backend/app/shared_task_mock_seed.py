from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, func, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    NotificationRecord,
    OpsCaseRecord,
    OutboundOutputRecord,
    OutboxEventRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)


SHARED_TASK_MOCK_SCENARIO = "MOCK_SEED_SHARED_TASKS"
SHARED_TASK_MOCK_TEACHER_ID = "T-1001"
_NOTIFICATION_ID = "MOCK-NOTIFICATION-SHARED-G01"
_CASE_ID = "MOCK-CASE-SHARED-G02"

_TASKS = (
    ("G01", "ASSIGNED"),
    ("G02", "IN_PROGRESS"),
    ("G03", "COMPLETED"),
)

_OUTPUTS = (
    {
        "output_id": "MOCK-OUTPUT-IN-APP-NOTIFICATION",
        "task_code": "G01",
        "output_type": "DELIVERY_INTENT",
        "display_type": "IN_APP_NOTIFICATION",
        "delivery_kind": "IN_APP_NOTIFICATION",
        "audience_type": "TEACHER",
        "recipient_id": SHARED_TASK_MOCK_TEACHER_ID,
        "recipient_name": "Maria Santos (Mock)",
        "channel": "WEBAPP_INBOX",
        "source_id": _NOTIFICATION_ID,
        "case_id": None,
        "status": "CANCELLED",
        "title": "[Mock 调试] G01 站内通知",
        "body": "仅用于输出中心展示；没有向教师端执行真实投递。",
        "last_error": "MOCK_SEED_DELIVERY_DISABLED",
        "requires_human_approval": False,
    },
    {
        "output_id": "MOCK-OUTPUT-TASK-REMINDER",
        "task_code": "G02",
        "output_type": "DELIVERY_INTENT",
        "display_type": "REMINDER",
        "delivery_kind": "REMINDER",
        "audience_type": "TEACHER",
        "recipient_id": SHARED_TASK_MOCK_TEACHER_ID,
        "recipient_name": "Maria Santos (Mock)",
        "channel": "WEBAPP_INBOX",
        "source_id": "ASSIGNMENT",
        "case_id": None,
        "status": "CANCELLED",
        "title": "[Mock 调试] G02 任务提醒",
        "body": "模拟教师任务处理中提醒；本地 Seed 不会安排真实发送。",
        "last_error": "MOCK_SEED_DELIVERY_DISABLED",
        "requires_human_approval": False,
    },
    {
        "output_id": "MOCK-OUTPUT-OPS-CASE",
        "task_code": "G02",
        "output_type": "OPS_REVIEW_CASE",
        "display_type": "OPS_CASE",
        "delivery_kind": "INTERNAL_CASE",
        "audience_type": "OPS",
        "recipient_id": "TIT_GROWTH_OPS",
        "recipient_name": "TIT Growth Operations",
        "channel": "OPS_QUEUE",
        "source_id": _CASE_ID,
        "case_id": _CASE_ID,
        "status": "STORED",
        "title": "[Mock 调试] 教师任务跟进 Case",
        "body": "模拟运营查看教师任务处理中状态；不产生真实运营处置。",
        "last_error": None,
        "requires_human_approval": True,
    },
    {
        "output_id": "MOCK-OUTPUT-EXTERNAL-ACTION",
        "task_code": "G02",
        "output_type": "SYSTEM_ACTION_REQUEST",
        "display_type": "EXTERNAL_ACTION_REQUEST",
        "delivery_kind": "DEBUG_ONLY",
        "audience_type": "EXTERNAL_SYSTEM",
        "recipient_id": "NO_REAL_RECIPIENT",
        "recipient_name": "Mock external system",
        "channel": "DEBUG_ONLY",
        "source_id": _CASE_ID,
        "case_id": _CASE_ID,
        "status": "ACTION_PENDING",
        "title": "[Mock 调试] 外部动作请求",
        "body": "仅展示动作结构；execution_allowed=false，不能真实执行。",
        "last_error": None,
        "requires_human_approval": True,
    },
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_test_environment(bind: Engine) -> None:
    if (
        os.getenv("APP_ENV", "").strip().lower() != "test"
        or bind.dialect.name != "sqlite"
    ):
        raise RuntimeError(
            "Shared task Mock seed is restricted to the disposable SQLite test database"
        )


def _published_template(session: Any, task_code: str) -> TaskTemplateRecord:
    template = session.get(TaskTemplateRecord, f"{task_code}:v1")
    if template is None or (
        template.template_id,
        template.template_version,
        template.status,
        template.integration_mode,
    ) != (task_code, 1, "PUBLISHED", "INBOUND_STATUS_ONLY"):
        raise RuntimeError(f"Published inbound template {task_code}:v1 is required")
    return template


def _assignment_id(task_code: str) -> str:
    return f"MOCK-SHARED-{task_code}-{SHARED_TASK_MOCK_TEACHER_ID}"


def _validate_assignment(
    assignment: TaskAssignmentRecord,
    template: TaskTemplateRecord,
) -> None:
    expected = {
        "assignment_id": _assignment_id(template.template_id),
        "teacher_id": SHARED_TASK_MOCK_TEACHER_ID,
        "task_code": template.template_id,
        "template_version_id": template.row_id,
        "task_kind": "FIXED_GROWTH",
        "creator_system": "TRIGGER_CENTER",
        "source_mode": "MOCK",
        "dedupe_key": f"fixed:{SHARED_TASK_MOCK_TEACHER_ID}:{template.template_id}",
        "created_by": "MOCK_SEED",
    }
    if any(getattr(assignment, field) != value for field, value in expected.items()):
        raise RuntimeError(
            f"Shared task Mock assignment {assignment.assignment_id} conflicts with the seed"
        )


def _seed_assignments(session: Any, started_at: datetime) -> dict[str, TaskAssignmentRecord]:
    teacher = session.get(TeacherRecord, SHARED_TASK_MOCK_TEACHER_ID)
    if teacher is None or teacher.data_mode != "MOCK" or teacher.source_batch_id is not None:
        raise RuntimeError("Reserved local Mock teacher T-1001 is required")

    result: dict[str, TaskAssignmentRecord] = {}
    for offset, (task_code, target_status) in enumerate(_TASKS):
        template = _published_template(session, task_code)
        dedupe_key = f"fixed:{SHARED_TASK_MOCK_TEACHER_ID}:{task_code}"
        assignment = session.scalar(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.dedupe_key == dedupe_key
            )
        )
        if assignment is None:
            assigned_at = started_at + timedelta(seconds=offset)
            template_payload = template.payload if isinstance(template.payload, dict) else {}
            assignment = TaskAssignmentRecord(
                assignment_id=_assignment_id(task_code),
                teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
                task_code=task_code,
                template_version_id=template.row_id,
                task_kind="FIXED_GROWTH",
                creator_system="TRIGGER_CENTER",
                status="ASSIGNED",
                priority=str(template_payload.get("priority") or "P1"),
                why="[Mock 调试] "
                + str(template_payload.get("why_template") or "Fixed growth task"),
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=None,
                source_mode="MOCK",
                dedupe_key=dedupe_key,
                created_by="MOCK_SEED",
                updated_by="MOCK_SEED",
                row_version=1,
                assigned_at=assigned_at,
                status_changed_at=assigned_at,
                completed_at=None,
                created_at=assigned_at,
                updated_at=assigned_at,
            )
            session.add(assignment)
            session.flush()
        else:
            _validate_assignment(assignment, template)

        transitionable = {
            "IN_PROGRESS": {"ASSIGNED", "VIEWED", "FAILED"},
            "COMPLETED": {
                "ASSIGNED",
                "VIEWED",
                "IN_PROGRESS",
                "SUBMITTED",
                "UNDER_REVIEW",
                "FAILED",
            },
        }
        if assignment.status in transitionable.get(target_status, set()):
            changed_at = started_at + timedelta(minutes=offset + 1)
            assignment.status = target_status
            assignment.status_reason_code = None
            assignment.status_changed_at = changed_at
            assignment.completed_at = changed_at if target_status == "COMPLETED" else None
            assignment.updated_by = "MOCK_SEED"
            session.flush()
        result[task_code] = assignment
    return result


def _mock_meta(**extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "scenario": SHARED_TASK_MOCK_SCENARIO,
        "origin": "MOCK_SEED",
        "source": "MOCK_SEED",
        "source_mode": "MOCK",
        "mock_only": True,
        "delivery_disabled": True,
        "execution_allowed": False,
    }


def _seed_notification_and_case(
    session: Any,
    assignments: dict[str, TaskAssignmentRecord],
    created_at: datetime,
) -> None:
    notification_payload = _mock_meta(
        notification_id=_NOTIFICATION_ID,
        task_id=assignments["G01"].assignment_id,
        teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
        channel="WEBAPP_INBOX",
        priority="P1",
        status="CANCELLED",
        requested_at=_iso(created_at),
        stored_at=None,
        read_at=None,
        clicked_at=None,
        response_due_at=None,
        failure_reason="MOCK_SEED_DELIVERY_DISABLED",
    )
    notification = session.get(NotificationRecord, _NOTIFICATION_ID)
    if notification is None:
        session.add(
            NotificationRecord(
                notification_id=_NOTIFICATION_ID,
                task_id=assignments["G01"].assignment_id,
                teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
                channel="WEBAPP_INBOX",
                priority="P1",
                status="CANCELLED",
                requested_at=created_at,
                failure_reason="MOCK_SEED_DELIVERY_DISABLED",
                payload=notification_payload,
            )
        )
    elif notification.payload.get("origin") != "MOCK_SEED":
        raise RuntimeError("Mock notification ID conflicts with existing data")

    case_payload = _mock_meta(
        case_id=_CASE_ID,
        case_type="MOCK_TASK_FOLLOW_UP",
        teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
        task_id=assignments["G02"].assignment_id,
        priority="P2",
        status="OPEN",
        summary="模拟教师正在处理 G02，供运营行动台检查 Case 展示。",
        recommended_action="仅查看，不执行真实跟进。",
        source_reason="MOCK_SEED",
        external_action_status="MOCK_ONLY_NOT_REQUESTED",
        created_at=_iso(created_at),
        updated_at=_iso(created_at),
    )
    case = session.get(OpsCaseRecord, _CASE_ID)
    if case is None:
        session.add(
            OpsCaseRecord(
                case_id=_CASE_ID,
                case_type="MOCK_TASK_FOLLOW_UP",
                teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
                task_id=assignments["G02"].assignment_id,
                priority="P2",
                status="OPEN",
                source_reason="MOCK_SEED",
                external_action_status="MOCK_ONLY_NOT_REQUESTED",
                created_at=created_at,
                payload=case_payload,
                updated_at=created_at,
            )
        )
    elif case.payload.get("origin") != "MOCK_SEED":
        raise RuntimeError("Mock case ID conflicts with existing data")
    else:
        case.case_type = "MOCK_TASK_FOLLOW_UP"
        case.teacher_id = SHARED_TASK_MOCK_TEACHER_ID
        case.task_id = assignments["G02"].assignment_id
        case.priority = "P2"
        case.status = "OPEN"
        case.source_reason = "MOCK_SEED"
        case.external_action_status = "MOCK_ONLY_NOT_REQUESTED"
        case.payload = case_payload
        case.updated_at = created_at


def _seed_outputs(
    session: Any,
    assignments: dict[str, TaskAssignmentRecord],
    created_at: datetime,
) -> list[OutboundOutputRecord]:
    result: list[OutboundOutputRecord] = []
    for offset, spec in enumerate(_OUTPUTS):
        assignment = assignments[spec["task_code"]]
        source_id = assignment.assignment_id if spec["source_id"] == "ASSIGNMENT" else spec["source_id"]
        output = session.get(OutboundOutputRecord, spec["output_id"])
        if output is None:
            output = OutboundOutputRecord(
                output_id=spec["output_id"],
                output_type=spec["output_type"],
                display_type=spec["display_type"],
                delivery_kind=spec["delivery_kind"],
                audience_type=spec["audience_type"],
                recipient_id=spec["recipient_id"],
                recipient_name=spec["recipient_name"],
                channel=spec["channel"],
                source_type="MOCK_SEED",
                source_id=source_id,
                teacher_id=SHARED_TASK_MOCK_TEACHER_ID,
                task_id=assignment.assignment_id,
                case_id=spec["case_id"],
                status=spec["status"],
                title=spec["title"],
                body=spec["body"],
                scheduled_at=None,
                created_at=created_at + timedelta(seconds=offset),
                sent_at=None,
                delivered_at=None,
                attempt_count=0,
                max_attempts=1,
                next_retry_at=None,
                last_error=spec["last_error"],
                retryable=False,
                requires_human_approval=spec["requires_human_approval"],
                payload=_mock_meta(
                    schema_version="shared_task_mock_output.v1",
                    assignment_id=assignment.assignment_id,
                    task_code=spec["task_code"],
                    debug_output_kind=spec["display_type"],
                    note="Local display only; no real recipient or system is called.",
                ),
                idempotency_key=f"mock-seed:output:{spec['display_type'].lower()}",
                updated_at=created_at + timedelta(seconds=offset),
            )
            session.add(output)
        elif output.source_type != "MOCK_SEED" or output.payload.get("origin") != "MOCK_SEED":
            raise RuntimeError(f"Mock output ID {output.output_id} conflicts with existing data")
        result.append(output)
    return result


def _cancel_assignment_outbox(session: Any, assignment_ids: set[str]) -> None:
    records = session.scalars(
        select(OutboxEventRecord).where(
            OutboxEventRecord.aggregate_id.in_(assignment_ids)
        )
    ).all()
    for record in records:
        record.payload = _mock_meta(**deepcopy(record.payload or {}))
        record.status = "CANCELLED"
        record.last_error = "MOCK_SEED_DELIVERY_DISABLED"


def seed_shared_task_mock_outputs(bind: Engine | None = None) -> dict[str, Any]:
    """Idempotently seed one isolated shared-task test slice."""

    selected_engine = bind or default_engine
    _require_test_environment(selected_engine)
    started_at = _now()
    with session_scope(selected_engine) as session:
        score_count = int(session.scalar(select(func.count()).select_from(ScoreEntryRecord)) or 0)
        assignments = _seed_assignments(session, started_at)
        output_at = started_at + timedelta(minutes=4)
        _seed_notification_and_case(session, assignments, output_at)
        outputs = _seed_outputs(session, assignments, output_at)
        _cancel_assignment_outbox(
            session,
            {assignment.assignment_id for assignment in assignments.values()},
        )
        if int(session.scalar(select(func.count()).select_from(ScoreEntryRecord)) or 0) != score_count:
            raise RuntimeError("Mock seed must not settle fixed-task points")

        return {
            "scenario": SHARED_TASK_MOCK_SCENARIO,
            "teacher_id": SHARED_TASK_MOCK_TEACHER_ID,
            "assignment_ids": sorted(item.assignment_id for item in assignments.values()),
            "assignment_statuses": {
                code: assignment.status for code, assignment in assignments.items()
            },
            "output_ids": sorted(item.output_id for item in outputs),
            "output_display_types": sorted(item.display_type for item in outputs),
            "score_entries_written": 0,
            "real_delivery_enabled": False,
        }
