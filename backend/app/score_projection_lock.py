from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


_LOCK_NAMESPACE = 544954
_LOCK_KEY = 1


def acquire_score_projection_lock(session: Session) -> None:
    """Serialize PostgreSQL score settlement and full score-model rebuilds."""

    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
        {"namespace": _LOCK_NAMESPACE, "key": _LOCK_KEY},
    )
