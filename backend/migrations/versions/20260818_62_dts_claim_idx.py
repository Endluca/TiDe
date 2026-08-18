"""add index-aligned DTS dirty-key claims

Revision ID: 20260818_62_dts_claim_idx
Revises: 20260814_61_teacher_copy
Create Date: 2026-08-18

The original ready index cannot serve the global FIFO order across the
combined PENDING/RETRY predicate.  Production evidence showed a parallel
full-table scan and top-N sort for every claimed key.  Build state-specific
partial indexes concurrently so live DTS ingestion remains writable while
the multi-million-row indexes are created.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260818_62_dts_claim_idx"
down_revision: Union[str, None] = "20260814_61_teacher_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PENDING_INDEX = "ix_dts_dirty_keys_pending_fifo"
RETRY_INDEX = "ix_dts_dirty_keys_retry_due"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside Alembic's migration
    # transaction. Remove only these new revision-owned names first so an
    # interrupted concurrent build (which PostgreSQL can leave INVALID) is
    # safely rebuilt instead of being mistaken for a completed migration.
    with op.get_context().autocommit_block():
        op.drop_index(
            RETRY_INDEX,
            table_name="dts_dirty_keys",
            schema="public",
            if_exists=True,
            postgresql_concurrently=True,
        )
        op.drop_index(
            PENDING_INDEX,
            table_name="dts_dirty_keys",
            schema="public",
            if_exists=True,
            postgresql_concurrently=True,
        )
        op.create_index(
            PENDING_INDEX,
            "dts_dirty_keys",
            ["last_seen_at", "key_type", "key_part_1", "key_part_2"],
            schema="public",
            unique=False,
            postgresql_concurrently=True,
            postgresql_where=sa.text("status = 'PENDING'"),
        )
        op.create_index(
            RETRY_INDEX,
            "dts_dirty_keys",
            [
                "next_attempt_at",
                "last_seen_at",
                "key_type",
                "key_part_1",
                "key_part_2",
            ],
            schema="public",
            unique=False,
            postgresql_concurrently=True,
            postgresql_where=sa.text(
                "status = 'RETRY' "
                "AND next_attempt_at < 'infinity'::timestamptz"
            ),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.drop_index(
            RETRY_INDEX,
            table_name="dts_dirty_keys",
            schema="public",
            if_exists=True,
            postgresql_concurrently=True,
        )
        op.drop_index(
            PENDING_INDEX,
            table_name="dts_dirty_keys",
            schema="public",
            if_exists=True,
            postgresql_concurrently=True,
        )
