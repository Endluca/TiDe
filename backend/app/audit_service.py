from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, func, literal_column, or_, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import AuditEventRecord


def _iso(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_payload(record: AuditEventRecord) -> dict[str, Any]:
    return {
        **deepcopy(record.payload or {}),
        "event_id": record.event_id,
        "event_type": record.event_type,
        "teacher_id": record.teacher_id,
        "task_id": record.task_id,
        "case_id": record.case_id,
        "occurred_at": _iso(record.occurred_at),
        "actor_type": record.actor_type,
    }


class AuditService:
    """Server-side audit pagination without loading the event ledger into RAM."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    def list_event_page(
        self,
        *,
        page: int,
        page_size: int,
        teacher_id: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            statement = select(AuditEventRecord)
            if teacher_id:
                statement = statement.where(
                    AuditEventRecord.teacher_id == teacher_id
                )
            needle = (keyword or "").strip()
            if needle:
                pattern = f"%{needle}%"
                if session.bind.dialect.name == "postgresql":
                    # This expression is covered by the structured trigram
                    # index. Deliberately exclude the arbitrary JSON payload:
                    # searching it forced full-table JSON serialization and
                    # also made the search surface depend on unstable fields.
                    separator = literal_column("' '")
                    empty_text = literal_column("''")
                    search_text = (
                        AuditEventRecord.event_id
                        + separator
                        + AuditEventRecord.event_type
                        + separator
                        + func.coalesce(
                            AuditEventRecord.teacher_id, empty_text
                        )
                        + separator
                        + func.coalesce(
                            AuditEventRecord.task_id, empty_text
                        )
                        + separator
                        + func.coalesce(
                            AuditEventRecord.case_id, empty_text
                        )
                        + separator
                        + AuditEventRecord.actor_type
                    )
                    statement = statement.where(
                        search_text.ilike(pattern)
                    )
                else:
                    statement = statement.where(
                        or_(
                            AuditEventRecord.event_id.ilike(pattern),
                            AuditEventRecord.event_type.ilike(pattern),
                            AuditEventRecord.teacher_id.ilike(pattern),
                            AuditEventRecord.task_id.ilike(pattern),
                            AuditEventRecord.case_id.ilike(pattern),
                            AuditEventRecord.actor_type.ilike(pattern),
                        )
                    )
            total = int(
                session.scalar(
                    select(func.count()).select_from(statement.subquery())
                )
                or 0
            )
            records = session.scalars(
                statement.order_by(
                    AuditEventRecord.sequence.desc(),
                    AuditEventRecord.event_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [_event_payload(item) for item in records],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
