"""add durable DTS ingest state and its restricted runtime ACL

Revision ID: 20260812_57_dts_state
Revises: 20260812_56_lean_roles
Create Date: 2026-08-12

The DTS runtime persists only whitelisted source state.  Kafka offsets are
committed after the database transaction, so the database checkpoint is the
authoritative replay floor when the Kafka commit lags.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260812_57_dts_state"
down_revision: Union[str, None] = "20260812_56_lean_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DTS_STATE_TABLES: tuple[str, ...] = (
    "dts_ingest_checkpoints",
    "dts_ingest_events",
    "dts_source_rows",
    "dts_dirty_keys",
)


def _qualified(values: tuple[str, ...]) -> str:
    return ",\n            ".join(f"public.{value}" for value in values)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.create_table(
        "dts_ingest_checkpoints",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.Column("source_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("source_position", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "topic",
            "partition_id",
            name="pk_dts_ingest_checkpoints",
        ),
        sa.CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_checkpoint_region",
        ),
        sa.CheckConstraint(
            "partition_id >= 0 AND next_offset >= 0",
            name="ck_dts_checkpoint_offsets",
        ),
        schema="public",
    )

    op.create_table(
        "dts_ingest_events",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("offset_value", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("source_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("source_txid", sa.Text(), nullable=False),
        sa.Column("source_position", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("source_database", sa.Text(), nullable=True),
        sa.Column("source_schema", sa.Text(), nullable=True),
        sa.Column("source_table", sa.Text(), nullable=True),
        sa.Column("route_status", sa.String(length=16), nullable=False),
        sa.Column("dirty_key_count", sa.Integer(), nullable=False),
        sa.Column("issue_codes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "topic",
            "partition_id",
            "offset_value",
            name="pk_dts_ingest_events",
        ),
        sa.CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_event_region",
        ),
        sa.CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0 AND dirty_key_count >= 0",
            name="ck_dts_event_offsets",
        ),
        sa.CheckConstraint(
            "route_status IN ('PROCESSED', 'IGNORED')",
            name="ck_dts_event_route_status",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_ingest_events_source_table_processed",
        "dts_ingest_events",
        ["source_region", "source_table", "processed_at"],
        schema="public",
    )

    op.create_table(
        "dts_source_rows",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("source_key_data", postgresql.JSONB(), nullable=False),
        sa.Column("dependency_keys", postgresql.JSONB(), nullable=False),
        sa.Column("source_row", postgresql.JSONB(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("source_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("last_record_id", sa.BigInteger(), nullable=False),
        sa.Column("source_position", sa.Text(), nullable=False),
        sa.Column("last_topic", sa.String(length=512), nullable=False),
        sa.Column("last_partition", sa.Integer(), nullable=False),
        sa.Column("last_offset", sa.BigInteger(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_table",
            "source_key",
            name="pk_dts_source_rows",
        ),
        sa.CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_source_row_region",
        ),
        sa.CheckConstraint(
            "last_partition >= 0 AND last_offset >= 0 AND row_version >= 1",
            name="ck_dts_source_row_version",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_source_rows_table_active",
        "dts_source_rows",
        ["source_region", "source_table", "is_deleted"],
        schema="public",
    )
    op.create_index(
        "ix_dts_source_rows_dependency_keys",
        "dts_source_rows",
        ["dependency_keys"],
        unique=False,
        schema="public",
        postgresql_using="gin",
        postgresql_ops={"dependency_keys": "jsonb_path_ops"},
    )

    op.create_table(
        "dts_dirty_keys",
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_part_1", sa.String(length=256), nullable=False),
        sa.Column("key_part_2", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pending_event_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_source_region", sa.String(length=8), nullable=False),
        sa.Column("last_source_table", sa.String(length=128), nullable=True),
        sa.Column("last_topic", sa.String(length=512), nullable=False),
        sa.Column("last_partition", sa.Integer(), nullable=False),
        sa.Column("last_offset", sa.BigInteger(), nullable=False),
        sa.Column("issue_codes", postgresql.JSONB(), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "key_type",
            "key_part_1",
            "key_part_2",
            name="pk_dts_dirty_keys",
        ),
        sa.CheckConstraint(
            "key_type IN "
            "('COURSE', 'TEACHER', 'TEACHER_STUDENT', 'LABEL', 'COMPLAINT_CATEGORY')",
            name="ck_dts_dirty_key_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY', 'COMPLETED')",
            name="ck_dts_dirty_key_status",
        ),
        sa.CheckConstraint(
            "last_source_region IN ('ovs', 'dom')",
            name="ck_dts_dirty_key_region",
        ),
        sa.CheckConstraint(
            "pending_event_count >= 1 AND attempt_count >= 0 "
            "AND last_partition >= 0 AND last_offset >= 0 AND row_version >= 1",
            name="ck_dts_dirty_key_counters",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_dirty_keys_ready",
        "dts_dirty_keys",
        ["status", "next_attempt_at", "last_seen_at"],
        schema="public",
    )

    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(DTS_STATE_TABLES)}
        FROM PUBLIC, tit_growth_app, tit_teacher_crud, tit_dts_ingest_runtime;

        GRANT SELECT, INSERT, UPDATE ON TABLE
            {_qualified(DTS_STATE_TABLES)}
        TO tit_dts_ingest_runtime;
        """
    )
    op.execute(
        f"""
        DO $dts_state_acl_assertions$
        BEGIN
            IF has_schema_privilege(
                'tit_dts_ingest_runtime', 'public', 'CREATE'
            ) THEN
                RAISE EXCEPTION 'DTS runtime may not create schema objects';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    {", ".join(repr(f"public.{item}") for item in DTS_STATE_TABLES)}
                ]::text[]) AS relation(name),
                unnest(ARRAY['SELECT', 'INSERT', 'UPDATE']::text[])
                    AS privilege(name)
                WHERE NOT has_table_privilege(
                    'tit_dts_ingest_runtime', relation.name, privilege.name
                )
            ) THEN
                RAISE EXCEPTION 'DTS runtime is missing ingest-state privileges';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    {", ".join(repr(f"public.{item}") for item in DTS_STATE_TABLES)}
                ]::text[]) AS relation(name)
                WHERE has_table_privilege(
                    'tit_dts_ingest_runtime', relation.name, 'DELETE'
                ) OR has_table_privilege(
                    'tit_dts_ingest_runtime', relation.name, 'TRUNCATE'
                )
            ) THEN
                RAISE EXCEPTION 'DTS runtime may not erase ingest history';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    {", ".join(repr(f"public.{item}") for item in DTS_STATE_TABLES)}
                ]::text[]) AS relation(name),
                unnest(ARRAY['tit_growth_app', 'tit_teacher_crud']::text[])
                    AS role(name)
                WHERE has_table_privilege(role.name, relation.name, 'SELECT')
                   OR has_table_privilege(role.name, relation.name, 'INSERT')
                   OR has_table_privilege(role.name, relation.name, 'UPDATE')
                   OR has_table_privilege(role.name, relation.name, 'DELETE')
            ) THEN
                RAISE EXCEPTION 'non-DTS runtime can access ingest state';
            END IF;
        END
        $dts_state_acl_assertions$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $dts_state_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.dts_ingest_events LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_source_rows LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_dirty_keys LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_ingest_checkpoints LIMIT 1)
            THEN
                RAISE EXCEPTION
                    'refusing DTS state downgrade: persisted replay state exists';
            END IF;
        END
        $dts_state_downgrade_guard$;
        """
    )
    op.drop_index("ix_dts_dirty_keys_ready", table_name="dts_dirty_keys", schema="public")
    op.drop_table("dts_dirty_keys", schema="public")
    op.drop_index(
        "ix_dts_source_rows_dependency_keys",
        table_name="dts_source_rows",
        schema="public",
    )
    op.drop_index(
        "ix_dts_source_rows_table_active",
        table_name="dts_source_rows",
        schema="public",
    )
    op.drop_table("dts_source_rows", schema="public")
    op.drop_index(
        "ix_dts_ingest_events_source_table_processed",
        table_name="dts_ingest_events",
        schema="public",
    )
    op.drop_table("dts_ingest_events", schema="public")
    op.drop_table("dts_ingest_checkpoints", schema="public")
