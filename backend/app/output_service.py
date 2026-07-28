from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, String, cast, func, or_, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    AuditEventRecord,
    NotificationRecord,
    OutboundOutputRecord,
    OutboxEventRecord,
)
from .errors import DomainError


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _output_payload(record: OutboundOutputRecord) -> dict[str, Any]:
    """Project authoritative output columns over any legacy payload copy."""

    return {
        **deepcopy(record.payload or {}),
        "output_id": record.output_id,
        "output_type": record.output_type,
        "display_type": record.display_type,
        "delivery_kind": record.delivery_kind,
        "audience_type": record.audience_type,
        "recipient_id": record.recipient_id,
        "recipient_name": record.recipient_name,
        "channel": record.channel,
        "source_type": record.source_type,
        "source_id": record.source_id,
        "teacher_id": record.teacher_id,
        "task_id": record.task_id,
        "case_id": record.case_id,
        "status": record.status,
        "title": record.title,
        "body": record.body,
        "scheduled_at": _iso(record.scheduled_at),
        "created_at": _iso(record.created_at),
        "sent_at": _iso(record.sent_at),
        "delivered_at": _iso(record.delivered_at),
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "next_retry_at": _iso(record.next_retry_at),
        "last_error": record.last_error,
        "retryable": record.retryable,
        "requires_human_approval": record.requires_human_approval,
        "idempotency_key": record.idempotency_key,
        "updated_at": _iso(record.updated_at),
        "payload": deepcopy(record.payload or {}),
    }


class OutputService:
    """Database-backed output reads and atomic retry commands."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    def list_outputs(
        self,
        *,
        type_filter: str | None = None,
        status: str | None = None,
        teacher_id: str | None = None,
        keyword: str | None = None,
        operational_only: bool = True,
        include_task_assignments: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            statement = select(OutboundOutputRecord)
            if type_filter:
                statement = statement.where(
                    or_(
                        OutboundOutputRecord.output_type == type_filter,
                        OutboundOutputRecord.display_type == type_filter,
                    )
                )
            if status:
                statement = statement.where(OutboundOutputRecord.status == status)
            if teacher_id:
                statement = statement.where(
                    OutboundOutputRecord.teacher_id == teacher_id
                )
            if operational_only:
                searchable_copy = func.lower(
                    func.coalesce(OutboundOutputRecord.title, "")
                    + " "
                    + func.coalesce(OutboundOutputRecord.body, "")
                )
                statement = statement.where(
                    OutboundOutputRecord.display_type != "PROVIDER_REQUEST",
                    ~searchable_copy.contains("mock"),
                    ~searchable_copy.contains("模拟"),
                    ~searchable_copy.contains("调试"),
                )
            if not include_task_assignments:
                statement = statement.where(
                    OutboundOutputRecord.display_type != "TASK_ASSIGNMENT"
                )
            needle = (keyword or "").strip()
            if needle:
                pattern = f"%{needle}%"
                statement = statement.where(
                    or_(
                        OutboundOutputRecord.output_id.ilike(pattern),
                        OutboundOutputRecord.title.ilike(pattern),
                        OutboundOutputRecord.body.ilike(pattern),
                        OutboundOutputRecord.recipient_id.ilike(pattern),
                        OutboundOutputRecord.recipient_name.ilike(pattern),
                        OutboundOutputRecord.teacher_id.ilike(pattern),
                        OutboundOutputRecord.source_type.ilike(pattern),
                        OutboundOutputRecord.source_id.ilike(pattern),
                        cast(OutboundOutputRecord.payload, String).ilike(pattern),
                    )
                )

            total = int(
                session.scalar(
                    select(func.count()).select_from(statement.subquery())
                )
                or 0
            )
            count_rows = session.execute(
                statement.with_only_columns(
                    OutboundOutputRecord.display_type,
                    func.count(),
                )
                .group_by(OutboundOutputRecord.display_type)
                .order_by(None)
            ).all()
            records = session.scalars(
                statement.order_by(
                    OutboundOutputRecord.created_at.desc(),
                    OutboundOutputRecord.output_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [_output_payload(item) for item in records],
                "total": total,
                "page": page,
                "page_size": page_size,
                "counts_by_type": {
                    str(display_type): int(count)
                    for display_type, count in count_rows
                },
            }

    def summary(self) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            def grouped(field: Any) -> dict[str, int]:
                return {
                    str(key): int(count)
                    for key, count in session.execute(
                        select(field, func.count())
                        .select_from(OutboundOutputRecord)
                        .group_by(field)
                        .order_by(field)
                    ).all()
                }

            return {
                "as_of": _iso(datetime.now(timezone.utc)),
                "total": int(
                    session.scalar(
                        select(func.count()).select_from(OutboundOutputRecord)
                    )
                    or 0
                ),
                "by_type": grouped(OutboundOutputRecord.output_type),
                "by_output_type": grouped(OutboundOutputRecord.output_type),
                "by_display_type": grouped(OutboundOutputRecord.display_type),
                "by_status": grouped(OutboundOutputRecord.status),
                "failed_retryable_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(OutboundOutputRecord)
                        .where(
                            OutboundOutputRecord.status == "FAILED",
                            OutboundOutputRecord.retryable.is_(True),
                        )
                    )
                    or 0
                ),
                "waiting_human_approval_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(OutboundOutputRecord)
                        .where(
                            OutboundOutputRecord.status == "ACTION_PENDING",
                            OutboundOutputRecord.requires_human_approval.is_(True),
                        )
                    )
                    or 0
                ),
                "planned_reminder_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(OutboundOutputRecord)
                        .where(
                            OutboundOutputRecord.display_type == "REMINDER",
                            OutboundOutputRecord.status == "PLANNED",
                        )
                    )
                    or 0
                ),
            }

    def retry_output(
        self,
        output_id: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Retry once under a row lock and create one transactional outbox fact."""

        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as session:
            output = session.scalar(
                select(OutboundOutputRecord)
                .where(OutboundOutputRecord.output_id == output_id)
                .with_for_update()
            )
            if output is None:
                raise DomainError(
                    "TASK_NOT_OWNED",
                    "output.error.not_found",
                    status_code=404,
                )
            if output.requires_human_approval:
                raise DomainError(
                    "COMMAND_NOT_ALLOWED",
                    "output.error.human_approval_required",
                    status_code=409,
                )
            if output.status != "FAILED" or not output.retryable:
                raise DomainError(
                    "COMMAND_NOT_ALLOWED",
                    "output.error.retry_not_allowed",
                    status_code=409,
                    details={
                        "status": output.status,
                        "retryable": output.retryable,
                    },
                )
            if output.attempt_count >= output.max_attempts:
                raise DomainError(
                    "COMMAND_NOT_ALLOWED",
                    "output.error.retry_limit_reached",
                    status_code=409,
                )

            linked_notification: NotificationRecord | None = None
            if output.display_type == "IN_APP_NOTIFICATION":
                notification_id = output.source_id or str(
                    (output.payload or {}).get("notification_id") or ""
                )
                linked_notification = session.scalar(
                    select(NotificationRecord)
                    .where(NotificationRecord.notification_id == notification_id)
                    .with_for_update()
                )
                if (
                    linked_notification is None
                    or linked_notification.status != "INTEGRATION_FAILED"
                ):
                    raise DomainError(
                        "COMMAND_NOT_ALLOWED",
                        "notification.error.retry_state_mismatch",
                        status_code=409,
                    )

            output.status = "REQUESTED"
            output.attempt_count += 1
            output.next_retry_at = None
            output.last_error = None
            output.sent_at = None
            output.delivered_at = None
            output.updated_at = now
            if isinstance(output.payload, dict) and output.payload.get("output_id"):
                output.payload = {
                    **deepcopy(output.payload),
                    "status": output.status,
                    "attempt_count": output.attempt_count,
                    "next_retry_at": None,
                    "last_error": None,
                    "sent_at": None,
                    "delivered_at": None,
                    "updated_at": _iso(now),
                }

            if linked_notification is not None:
                linked_notification.status = "REQUESTED"
                linked_notification.requested_at = now
                linked_notification.failure_reason = None
                linked_notification.stored_at = None
                linked_notification.read_at = None
                linked_notification.clicked_at = None
                linked_notification.response_due_at = None

            event_id = f"EVT-{uuid4().hex}"
            event_payload = {
                "event_id": event_id,
                "event_type": "outbound_output.retry_requested.v1",
                "occurred_at": _iso(now),
                "teacher_id": output.teacher_id,
                "task_id": output.task_id,
                "case_id": output.case_id,
                "actor_type": "OPERATOR" if actor_id else "SYSTEM",
                "actor_id": actor_id,
                "payload": {
                    "output_id": output_id,
                    "attempt_count": output.attempt_count,
                    "display_type": output.display_type,
                    "actor_id": actor_id,
                },
            }
            session.add(
                AuditEventRecord(
                    event_id=event_id,
                    event_type=event_payload["event_type"],
                    teacher_id=output.teacher_id,
                    task_id=output.task_id,
                    case_id=output.case_id,
                    occurred_at=now,
                    actor_type=event_payload["actor_type"],
                    payload_hash=_payload_hash(event_payload),
                    payload=event_payload,
                )
            )
            session.add(
                OutboxEventRecord(
                    outbox_id=f"OUTBOX-{event_id}",
                    event_id=event_id,
                    aggregate_type="OUTBOUND_OUTPUT",
                    aggregate_id=output_id,
                    event_type=event_payload["event_type"],
                    payload=event_payload,
                    status="PENDING",
                    available_at=now,
                    attempt_count=0,
                    last_error=None,
                    created_at=now,
                    published_at=None,
                )
            )
            session.flush()
            return _output_payload(output)
