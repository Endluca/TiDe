from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from .database import engine as default_engine
from .database import session_scope
from .db_models import AuditEventRecord
from .errors import DomainError


SUPPORT_TICKET_STATES = {"WAITING_OPERATOR", "WAITING_TEACHER", "CLOSED"}
SUPPORT_TICKET_CATEGORIES = {
    "TASK_RULES",
    "LESSON_INFO",
    "SCORE_OR_REVIEW",
    "PRODUCT_FUNCTION",
    "ACCOUNT_LOGIN",
    "MEDIA_UPLOAD_CAMERA",
    "OTHER",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def support_ticket_workflow_state(
    source_status: str,
    messages: list[dict[str, Any]] | None,
) -> str:
    """Derive the operator-facing state without waiting for a teacher refresh."""

    if source_status == "CLOSED":
        return "CLOSED"
    latest = messages[-1] if messages else {}
    return (
        "WAITING_TEACHER"
        if latest.get("sender") == "OPERATOR"
        else "WAITING_OPERATOR"
    )


def _safe_images(images: Any) -> list[dict[str, Any]]:
    if not isinstance(images, list):
        return []
    result: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        result.append(
            {
                "file_id": image.get("file_id"),
                "filename": image.get("filename"),
                "mime_type": image.get("mime_type"),
                "size": image.get("size"),
                "storage_provider": image.get("storage_provider"),
                "deleted_at": image.get("deleted_at"),
                "preview_url": None,
            }
        )
    return result


def _safe_message(message: Any) -> dict[str, Any]:
    source = message if isinstance(message, dict) else {}
    return {
        "message_id": source.get("message_id"),
        "sender": source.get("sender"),
        "content": source.get("content") or "",
        "images": _safe_images(source.get("images")),
        "created_at": source.get("created_at"),
    }


def _payload_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _domain_error(
    code: str,
    message_key: str,
    *,
    status_code: int,
    retryable: bool = False,
) -> DomainError:
    return DomainError(
        code,
        message_key,
        status_code=status_code,
        retryable=retryable,
    )


def _postgres_error(exc: DBAPIError) -> DomainError:
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "40001":
        return _domain_error(
            "SUPPORT_TICKET_VERSION_CONFLICT",
            "support_ticket.error.version_conflict",
            status_code=409,
            retryable=True,
        )
    if sqlstate == "23505":
        return _domain_error(
            "SUPPORT_TICKET_MESSAGE_CONFLICT",
            "support_ticket.error.message_conflict",
            status_code=409,
        )
    if sqlstate == "55000":
        return _domain_error(
            "SUPPORT_TICKET_CLOSED",
            "support_ticket.error.closed",
            status_code=409,
        )
    if sqlstate == "22023":
        return _domain_error(
            "SUPPORT_TICKET_MESSAGE_INVALID",
            "support_ticket.error.message_invalid",
            status_code=422,
        )
    if sqlstate in {"42P01", "42883", "42501"}:
        return _domain_error(
            "SUPPORT_TICKET_SOURCE_UNAVAILABLE",
            "support_ticket.error.source_unavailable",
            status_code=503,
            retryable=True,
        )
    return _domain_error(
        "SUPPORT_TICKET_REQUEST_FAILED",
        "support_ticket.error.request_failed",
        status_code=503,
        retryable=True,
    )


def _ticket_cte(*, include_messages: bool) -> str:
    detail_columns = (
        """
        ticket.problem_context,
        ticket.messages,
"""
        if include_messages
        else ""
    )
    return (
        """
WITH ticket_rows AS (
    SELECT
        ticket.ticket_id,
        ticket.teacher_id,
        teacher.name AS teacher_name,
        ticket.primary_category,
        ticket.secondary_category,
        ticket.problem_location,
"""
        + detail_columns
        + """
        ticket.messages -> -1 AS latest_message,
        jsonb_array_length(
            COALESCE(ticket.messages, '[]'::jsonb)
        ) AS message_count,
        ticket.status AS source_status,
        ticket.last_operator_reply_at,
        ticket.teacher_reply_deadline_at,
        ticket.close_reason,
        ticket.closed_at,
        ticket.image_cleanup_status,
        ticket.images_deleted_at,
        ticket.row_version,
        ticket.created_at,
        ticket.updated_at,
        CASE
            WHEN ticket.status = 'CLOSED' THEN 'CLOSED'
            WHEN COALESCE(ticket.messages -> -1 ->> 'sender', '') = 'OPERATOR'
                THEN 'WAITING_TEACHER'
            ELSE 'WAITING_OPERATOR'
        END AS workflow_state
    FROM public.teacher_support_tickets AS ticket
    LEFT JOIN public.teachers AS teacher
        ON teacher.teacher_id = ticket.teacher_id
)
"""
    )


class SupportTicketService:
    """Read the teacher-owned ticket fact and append operator messages only."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    @staticmethod
    def _summary_payload(row: Any) -> dict[str, int]:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        return {
            "waiting_operator": int(mapping["waiting_operator"] or 0),
            "waiting_teacher": int(mapping["waiting_teacher"] or 0),
            "closed": int(mapping["closed"] or 0),
            "total": int(mapping["total"] or 0),
        }

    @staticmethod
    def _ticket_payload(row: Any, *, include_messages: bool) -> dict[str, Any]:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        raw_messages = mapping.get("messages")
        messages = raw_messages if isinstance(raw_messages, list) else []
        raw_latest_message = (
            messages[-1] if messages else mapping.get("latest_message")
        )
        latest_message = (
            _safe_message(raw_latest_message)
            if isinstance(raw_latest_message, dict)
            else None
        )
        message_count = mapping.get("message_count")
        if message_count is None:
            message_count = len(messages)
        payload = {
            "ticket_id": str(mapping["ticket_id"]),
            "teacher_id": mapping["teacher_id"],
            "teacher_name": mapping.get("teacher_name") or mapping["teacher_id"],
            "primary_category": mapping["primary_category"],
            "secondary_category": mapping["secondary_category"],
            "problem_location": mapping["problem_location"],
            "source_status": mapping["source_status"],
            "workflow_state": mapping.get("workflow_state")
            or support_ticket_workflow_state(mapping["source_status"], messages),
            "latest_message": latest_message,
            "message_count": int(message_count),
            "last_operator_reply_at": _iso(mapping.get("last_operator_reply_at")),
            "teacher_reply_deadline_at": _iso(mapping.get("teacher_reply_deadline_at")),
            "close_reason": mapping.get("close_reason"),
            "closed_at": _iso(mapping.get("closed_at")),
            "image_cleanup_status": mapping["image_cleanup_status"],
            "images_deleted_at": _iso(mapping.get("images_deleted_at")),
            "row_version": int(mapping["row_version"]),
            "created_at": _iso(mapping["created_at"]),
            "updated_at": _iso(mapping["updated_at"]),
        }
        if include_messages:
            payload["problem_context"] = mapping.get("problem_context") or {}
            payload["messages"] = [_safe_message(message) for message in messages]
        return payload

    def summary(self) -> dict[str, int]:
        statement = text(
            _ticket_cte(include_messages=False)
            + """
SELECT
    count(*) FILTER (WHERE workflow_state = 'WAITING_OPERATOR') AS waiting_operator,
    count(*) FILTER (WHERE workflow_state = 'WAITING_TEACHER') AS waiting_teacher,
    count(*) FILTER (WHERE workflow_state = 'CLOSED') AS closed,
    count(*) AS total
FROM ticket_rows
"""
        )
        try:
            with session_scope(self.engine) as session:
                return self._summary_payload(session.execute(statement).one())
        except DBAPIError as exc:
            raise _postgres_error(exc) from exc

    def list_tickets(
        self,
        *,
        workflow_state: str | None = None,
        secondary_category: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if workflow_state and workflow_state not in SUPPORT_TICKET_STATES:
            raise _domain_error(
                "SUPPORT_TICKET_STATE_INVALID",
                "support_ticket.error.state_invalid",
                status_code=422,
            )
        if secondary_category and secondary_category not in SUPPORT_TICKET_CATEGORIES:
            raise _domain_error(
                "SUPPORT_TICKET_CATEGORY_INVALID",
                "support_ticket.error.category_invalid",
                status_code=422,
            )
        normalized_keyword = (keyword or "").strip()
        filters = """
WHERE (
      CAST(:workflow_state AS text) IS NULL
      OR workflow_state = CAST(:workflow_state AS text)
  )
  AND (
      CAST(:secondary_category AS text) IS NULL
      OR secondary_category = CAST(:secondary_category AS text)
  )
  AND (
      :keyword = ''
      OR teacher_id ILIKE :keyword_pattern
      OR COALESCE(teacher_name, '') ILIKE :keyword_pattern
      OR ticket_id::text ILIKE :keyword_pattern
  )
"""
        parameters = {
            "workflow_state": workflow_state,
            "secondary_category": secondary_category,
            "keyword": normalized_keyword,
            "keyword_pattern": f"%{normalized_keyword}%",
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        count_statement = text(
            _ticket_cte(include_messages=False)
            + "SELECT count(*) FROM ticket_rows\n"
            + filters
        )
        list_statement = text(
            _ticket_cte(include_messages=False)
            + """
SELECT *
FROM ticket_rows
"""
            + filters
            + """
ORDER BY
    CASE workflow_state
        WHEN 'WAITING_OPERATOR' THEN 0
        WHEN 'WAITING_TEACHER' THEN 1
        ELSE 2
    END,
    updated_at DESC,
    ticket_id DESC
LIMIT :limit OFFSET :offset
"""
        )
        try:
            with session_scope(self.engine) as session:
                total = int(session.scalar(count_statement, parameters) or 0)
                rows = session.execute(list_statement, parameters).all()
                return {
                    "items": [
                        self._ticket_payload(row, include_messages=False)
                        for row in rows
                    ],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }
        except DBAPIError as exc:
            raise _postgres_error(exc) from exc

    def get_ticket(self, ticket_id: UUID) -> dict[str, Any]:
        statement = text(
            _ticket_cte(include_messages=True)
            + """
SELECT *
FROM ticket_rows
WHERE ticket_id = :ticket_id
"""
        )
        try:
            with session_scope(self.engine) as session:
                row = session.execute(
                    statement,
                    {"ticket_id": str(ticket_id)},
                ).one_or_none()
                if row is None:
                    raise _domain_error(
                        "SUPPORT_TICKET_NOT_FOUND",
                        "support_ticket.error.not_found",
                        status_code=404,
                    )
                return self._ticket_payload(row, include_messages=True)
        except DBAPIError as exc:
            raise _postgres_error(exc) from exc

    def append_operator_reply(
        self,
        *,
        ticket_id: UUID,
        expected_row_version: int,
        message_id: UUID,
        content: str,
        actor_id: str,
    ) -> dict[str, Any]:
        shared_message = {
            "message_id": str(message_id),
            "sender": "OPERATOR",
            "content": content.strip(),
            "images": [],
        }
        statement = text(
            """
SELECT result.*
FROM public.append_teacher_support_ticket_operator_message(
    CAST(:ticket_id AS uuid),
    :expected_row_version,
    CAST(:message AS jsonb)
) AS result
"""
        )
        try:
            with session_scope(self.engine) as session:
                mutation_row = session.execute(
                    statement,
                    {
                        "ticket_id": str(ticket_id),
                        "expected_row_version": expected_row_version,
                        "message": json.dumps(shared_message, ensure_ascii=False),
                    },
                ).one_or_none()
                if (
                    mutation_row is None
                    or mutation_row._mapping.get("ticket_id") is None
                ):
                    raise _domain_error(
                        "SUPPORT_TICKET_NOT_FOUND",
                        "support_ticket.error.not_found",
                        status_code=404,
                    )

                event_id = f"EVT-SUPPORT-{message_id}"
                existing_event = session.scalar(
                    text(
                        "SELECT event_id FROM audit_events WHERE event_id = :event_id"
                    ),
                    {"event_id": event_id},
                )
                if existing_event is None:
                    now = datetime.now(timezone.utc)
                    audit_payload = {
                        "event_id": event_id,
                        "event_type": "support_ticket.operator_replied.v1",
                        "occurred_at": _iso(now),
                        "teacher_id": mutation_row._mapping["teacher_id"],
                        "case_id": str(ticket_id),
                        "actor_type": "OPS_USER",
                        "actor_id": actor_id,
                        "payload": {
                            "ticket_id": str(ticket_id),
                            "message_id": str(message_id),
                            "image_count": 0,
                        },
                    }
                    session.add(
                        AuditEventRecord(
                            event_id=event_id,
                            event_type=audit_payload["event_type"],
                            teacher_id=mutation_row._mapping["teacher_id"],
                            task_id=None,
                            case_id=str(ticket_id),
                            occurred_at=now,
                            actor_type="OPS_USER",
                            payload_hash=_payload_hash(audit_payload),
                            payload=audit_payload,
                        )
                    )
                ticket_row = session.execute(
                    text(
                        _ticket_cte(include_messages=True)
                        + """
SELECT *
FROM ticket_rows
WHERE ticket_id = CAST(:ticket_id AS uuid)
"""
                    ),
                    {"ticket_id": str(ticket_id)},
                ).one()
                return self._ticket_payload(ticket_row, include_messages=True)
        except DBAPIError as exc:
            raise _postgres_error(exc) from exc
