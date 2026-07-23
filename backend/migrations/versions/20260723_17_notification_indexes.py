"""formalize notification idempotency and teacher-timeline indexes

Revision ID: 20260723_17_notification_indexes
Revises: 20260723_16_mandatory_catalog
Create Date: 2026-07-23

The company test database already received these two operational indexes while
the notification read/write path was optimized.  This migration makes that
physical state reproducible from Alembic and aligns it with SQLAlchemy metadata.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_17_notification_indexes"
down_revision: Union[str, None] = "20260723_16_mandatory_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT notification_id, request_hash
            FROM notification_events
            GROUP BY notification_id, request_hash
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "notification index migration blocked: duplicate delivery events exist"
        )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_notification_events_notification_request_hash
        ON notification_events (notification_id, request_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_notifications_teacher_requested_desc
        ON notifications (teacher_id, requested_at DESC, notification_id DESC)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_notifications_teacher_requested_desc"
    )
    op.execute(
        "DROP INDEX IF EXISTS uq_notification_events_notification_request_hash"
    )
