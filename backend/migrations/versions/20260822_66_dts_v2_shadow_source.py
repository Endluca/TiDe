"""add the inert v2 DTS source-version shadow schema

Revision ID: 20260822_66_dts_v2_shadow
Revises: 20260819_65_g09_set_course
Create Date: 2026-08-22

This revision is deliberately additive.  It creates the epoch and immutable
source-version registries and adds nullable v2 transition columns beside the
still-authoritative v1 columns.  It does not change an existing primary key,
seed H0/pipeline control, grant a runtime writer, or switch a read path.
Business source-key evidence is persisted independently from the DTS envelope
record identity; neither identity is allowed to stand in for the other.
Each immutable version also carries the source schema profile and observed
field-type map that made the protected row image interpretable.

Release order: first roll the application version whose schema validator
accepts either the complete legacy shape or this complete expanded shape to
all v1 ingest instances; only then apply this revision.  Partial expansion is
rejected by that validator.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_66_dts_v2_shadow"
down_revision: Union[str, None] = "20260822_65a_lesson_region_exp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVENT_TRANSITION_COLUMNS: tuple[str, ...] = (
    "identity_version",
    "source_partition_epoch_id",
    "source_position_v2",
    "event_payload_hash",
)

CHECKPOINT_TRANSITION_COLUMNS: tuple[str, ...] = (
    "source_partition_epoch_id",
    "consumer_group",
    "checkpoint_row_version",
    "is_current_epoch",
)

SOURCE_ROW_TRANSITION_COLUMNS: tuple[str, ...] = (
    "source_row_revision",
    "last_source_partition_epoch_id",
    "last_version_kind",
    "source_position_v2",
    "record_id_type",
    "record_id_numeric",
    "record_id_text",
    "source_timestamp_v2",
    "source_payload_hash",
    "provenance_state",
    "source_key_type",
    "source_key_numeric",
    "source_key_text",
    "source_schema_profile_id",
    "source_field_types",
)

DIRTY_KEY_TRANSITION_COLUMNS: tuple[str, ...] = (
    "source_region",
    "required_work_revision",
    "claimed_through_work_revision",
    "completed_work_revision",
    "last_input_identity_hash",
    "last_input_revision",
    "work_generation",
    "dead_generation",
    "blocked_by",
    "lease_owner_kind",
    "lease_owner",
    "lease_token",
    "lease_expires_at",
)


def _create_shadow_tables() -> None:
    op.create_table(
        "dts_source_partition_epochs",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column(
            "source_partition_epoch_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("epoch_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stream_generation_id", sa.Text(), nullable=True),
        sa.Column("epoch_opening_id", sa.Text(), nullable=True),
        sa.Column("epoch_sequence", sa.BigInteger(), nullable=True),
        sa.Column(
            "predecessor_epoch_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("start_offset", sa.BigInteger(), nullable=True),
        sa.Column("v2_epoch_bootstrap_floor", sa.BigInteger(), nullable=True),
        sa.Column("activation_mode", sa.String(length=32), nullable=True),
        sa.Column("activation_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_id", sa.String(length=160), nullable=True),
        sa.Column("source_table", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_partition_epoch_id",
            "topic",
            "partition_id",
            name="pk_dts_source_partition_epochs",
        ),
        sa.UniqueConstraint(
            "source_region",
            "source_partition_epoch_id",
            name="uq_dts_source_partition_epoch_identity",
        ),
        sa.UniqueConstraint(
            "source_region",
            "topic",
            "partition_id",
            "epoch_sequence",
            name="uq_dts_source_partition_epoch_sequence",
        ),
        sa.UniqueConstraint(
            "source_region",
            "epoch_kind",
            "snapshot_id",
            "source_table",
            name="uq_dts_source_partition_epoch_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "predecessor_epoch_id"],
            [
                "public.dts_source_partition_epochs.source_region",
                "public.dts_source_partition_epochs.source_partition_epoch_id",
            ],
            name="fk_dts_source_partition_epoch_predecessor",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_dts_source_partition_epoch_region",
        ),
        sa.CheckConstraint(
            "partition_id >= 0",
            name="ck_dts_source_partition_epoch_partition",
        ),
        sa.CheckConstraint(
            "epoch_kind IN ('BROKER', 'SNAPSHOT_BASELINE', 'SNAPSHOT_DIFF')",
            name="ck_dts_source_partition_epoch_kind",
        ),
        sa.CheckConstraint(
            "status IN ('BARRIER_PENDING', 'ACTIVE', 'SUPERSEDED', 'SEALED')",
            name="ck_dts_source_partition_epoch_status",
        ),
        sa.CheckConstraint(
            "(epoch_kind = 'BROKER' "
            "AND stream_generation_id IS NOT NULL "
            "AND epoch_opening_id IS NOT NULL "
            "AND epoch_sequence IS NOT NULL "
            "AND epoch_sequence >= 1 "
            "AND start_offset IS NOT NULL "
            "AND start_offset >= 0 "
            "AND v2_epoch_bootstrap_floor IS NOT NULL "
            "AND v2_epoch_bootstrap_floor >= 0 "
            "AND snapshot_id IS NULL "
            "AND source_table IS NULL "
            "AND status IN ('BARRIER_PENDING', 'ACTIVE', 'SUPERSEDED')) "
            "OR (epoch_kind IN ('SNAPSHOT_BASELINE', 'SNAPSHOT_DIFF') "
            "AND stream_generation_id IS NULL "
            "AND epoch_opening_id IS NULL "
            "AND epoch_sequence IS NULL "
            "AND predecessor_epoch_id IS NULL "
            "AND start_offset IS NULL "
            "AND v2_epoch_bootstrap_floor IS NULL "
            "AND activation_mode IS NULL "
            "AND activation_manifest_hash IS NULL "
            "AND snapshot_id IS NOT NULL "
            "AND source_table IS NOT NULL "
            "AND status = 'SEALED')",
            name="ck_dts_source_partition_epoch_shape",
        ),
        sa.CheckConstraint(
            "activation_mode IS NULL "
            "OR activation_mode IN ('H0_BOOTSTRAP', 'SNAPSHOT_MANIFEST')",
            name="ck_dts_source_partition_epoch_activation_mode",
        ),
        sa.CheckConstraint(
            "activation_manifest_hash IS NULL "
            "OR activation_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_partition_epoch_manifest_hash",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_dts_source_partition_epoch_row_version",
        ),
        schema="public",
    )
    op.create_index(
        "uq_dts_source_partition_epoch_active_broker",
        "dts_source_partition_epochs",
        ["source_region", "topic", "partition_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text(
            "epoch_kind = 'BROKER' AND status = 'ACTIVE'"
        ),
    )

    op.create_table(
        "dts_source_row_versions",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column(
            "source_partition_epoch_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("offset_value", sa.BigInteger(), nullable=False),
        sa.Column("version_kind", sa.String(length=32), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column(
            "source_schema_profile_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("source_field_types", postgresql.JSONB(), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("source_key_data", postgresql.JSONB(), nullable=False),
        sa.Column("source_key_type", sa.String(length=16), nullable=False),
        sa.Column("source_key_numeric", sa.Numeric(), nullable=True),
        sa.Column("source_key_text", sa.Text(), nullable=True),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("before_row", postgresql.JSONB(), nullable=True),
        sa.Column("after_row", postgresql.JSONB(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_id_type", sa.String(length=16), nullable=False),
        sa.Column("record_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("record_id_text", sa.Text(), nullable=True),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("source_row_revision", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_id", sa.String(length=160), nullable=True),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("covered_through_offsets", postgresql.JSONB(), nullable=True),
        sa.Column("diff_step", sa.Integer(), nullable=True),
        sa.Column(
            "source_table_publish_generation",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "protected_source_row_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_partition_epoch_id",
            "topic",
            "partition_id",
            "offset_value",
            name="pk_dts_source_row_versions",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_partition_epoch_id",
                "topic",
                "partition_id",
            ],
            [
                "public.dts_source_partition_epochs.source_region",
                "public.dts_source_partition_epochs.source_partition_epoch_id",
                "public.dts_source_partition_epochs.topic",
                "public.dts_source_partition_epochs.partition_id",
            ],
            name="fk_dts_source_row_version_epoch",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_dts_source_row_version_region",
        ),
        sa.CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0",
            name="ck_dts_source_row_version_offset",
        ),
        sa.CheckConstraint(
            "version_kind IN ('CDC', 'BASELINE', 'SNAPSHOT_DIFF')",
            name="ck_dts_source_row_version_kind",
        ),
        sa.CheckConstraint(
            "btrim(source_schema_profile_id) <> ''",
            name="ck_dts_source_row_version_schema_profile",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_field_types) = 'object'",
            name="ck_dts_source_row_version_field_types",
        ),
        sa.CheckConstraint(
            "CASE "
            "WHEN jsonb_typeof(source_key_data) <> 'object' THEN false "
            "WHEN source_key_data <> "
            "jsonb_build_object('id', source_key_data -> 'id') THEN false "
            "WHEN source_key_type = 'NUMERIC' THEN "
            "CASE WHEN jsonb_typeof(source_key_data -> 'id') = 'number' THEN "
            "source_key_numeric IS NOT NULL "
            "AND source_key_text IS NULL "
            "AND source_key_numeric = (source_key_data ->> 'id')::numeric "
            "AND source_key = trim_scale(source_key_numeric)::text "
            "ELSE false END "
            "WHEN source_key_type = 'TEXT' THEN "
            "jsonb_typeof(source_key_data -> 'id') = 'string' "
            "AND source_key_numeric IS NULL "
            "AND source_key_text IS NOT NULL "
            "AND source_key_text <> '' "
            "AND source_key_text = source_key_data ->> 'id' "
            "AND source_key = source_key_text "
            "ELSE false END",
            name="ck_dts_source_row_version_source_key",
        ),
        sa.CheckConstraint(
            "record_id_type IN ('none', 'numeric', 'text') "
            "AND ((record_id_type = 'none' "
            "AND record_id_numeric IS NULL AND record_id_text IS NULL) "
            "OR (record_id_type = 'numeric' "
            "AND record_id_numeric IS NOT NULL AND record_id_text IS NULL) "
            "OR (record_id_type = 'text' "
            "AND record_id_numeric IS NULL AND record_id_text IS NOT NULL))",
            name="ck_dts_source_row_version_record_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_position) = 'object'",
            name="ck_dts_source_row_version_position",
        ),
        sa.CheckConstraint(
            "protected_source_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_row_version_hash",
        ),
        sa.CheckConstraint(
            "(version_kind = 'CDC' "
            "AND operation IN ('INSERT', 'UPDATE', 'DELETE', 'EPOCH_REPLAY') "
            "AND snapshot_id IS NULL "
            "AND ((operation = 'EPOCH_REPLAY' AND source_row_revision IS NULL) "
            "OR (operation <> 'EPOCH_REPLAY' "
            "AND source_row_revision IS NOT NULL "
            "AND source_row_revision >= 1))) "
            "OR (version_kind = 'BASELINE' "
            "AND operation = 'BASELINE' "
            "AND source_row_revision IS NULL "
            "AND snapshot_id IS NOT NULL) "
            "OR (version_kind = 'SNAPSHOT_DIFF' "
            "AND operation IN ('SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE', "
            "'SNAPSHOT_DELETE', 'SNAPSHOT_BOOTSTRAP_PRESENT', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE') "
            "AND source_row_revision IS NOT NULL "
            "AND source_row_revision >= 1 "
            "AND snapshot_id IS NOT NULL)",
            name="ck_dts_source_row_version_lifecycle",
        ),
        sa.CheckConstraint(
            "(version_kind <> 'SNAPSHOT_DIFF' "
            "AND diff_step IS NULL "
            "AND source_table_publish_generation IS NULL) "
            "OR (version_kind = 'SNAPSHOT_DIFF' "
            "AND diff_step IS NOT NULL "
            "AND diff_step IN (1, 2) "
            "AND source_table_publish_generation IS NOT NULL "
            "AND source_table_publish_generation >= 1 "
            "AND ((diff_step = 1 AND mod(offset_value, 2) = 1) "
            "OR (diff_step = 2 "
            "AND mod(offset_value, 2) = 0 "
            "AND operation IN ('SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE', "
            "'SNAPSHOT_DELETE'))))",
            name="ck_dts_source_row_version_diff_step",
        ),
        schema="public",
    )
    op.create_index(
        "uq_dts_source_row_version_snapshot_key",
        "dts_source_row_versions",
        ["source_region", "snapshot_id", "source_table", "source_key"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("version_kind = 'BASELINE'"),
    )
    op.create_index(
        "uq_dts_source_row_version_snapshot_diff_key_step",
        "dts_source_row_versions",
        [
            "source_region",
            "snapshot_id",
            "source_table",
            "source_key",
            "diff_step",
        ],
        unique=True,
        schema="public",
        postgresql_where=sa.text("version_kind = 'SNAPSHOT_DIFF'"),
    )
    op.create_index(
        "uq_dts_source_row_version_source_revision",
        "dts_source_row_versions",
        ["source_region", "source_table", "source_key", "source_row_revision"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("source_row_revision IS NOT NULL"),
    )
    op.create_index(
        "ix_dts_source_row_versions_source_order",
        "dts_source_row_versions",
        [
            "source_region",
            "source_table",
            "source_key",
            "source_row_revision",
        ],
        unique=False,
        schema="public",
    )


def _add_transition_columns() -> None:
    for column in (
        sa.Column("identity_version", sa.String(length=32), nullable=True),
        sa.Column(
            "source_partition_epoch_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("source_position_v2", postgresql.JSONB(), nullable=True),
        sa.Column("event_payload_hash", sa.String(length=64), nullable=True),
    ):
        op.add_column("dts_ingest_events", column, schema="public")

    for column in (
        sa.Column(
            "source_partition_epoch_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("consumer_group", sa.String(length=256), nullable=True),
        sa.Column("checkpoint_row_version", sa.BigInteger(), nullable=True),
        sa.Column("is_current_epoch", sa.Boolean(), nullable=True),
    ):
        op.add_column("dts_ingest_checkpoints", column, schema="public")

    for column in (
        sa.Column("source_row_revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_source_partition_epoch_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("last_version_kind", sa.String(length=32), nullable=True),
        sa.Column("source_position_v2", postgresql.JSONB(), nullable=True),
        sa.Column("record_id_type", sa.String(length=16), nullable=True),
        sa.Column("record_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("record_id_text", sa.Text(), nullable=True),
        sa.Column("source_timestamp_v2", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("provenance_state", sa.String(length=32), nullable=True),
        sa.Column("source_key_type", sa.String(length=16), nullable=True),
        sa.Column("source_key_numeric", sa.Numeric(), nullable=True),
        sa.Column("source_key_text", sa.Text(), nullable=True),
        sa.Column(
            "source_schema_profile_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("source_field_types", postgresql.JSONB(), nullable=True),
    ):
        op.add_column("dts_source_rows", column, schema="public")

    for column in (
        sa.Column("source_region", sa.String(length=8), nullable=True),
        sa.Column("required_work_revision", sa.BigInteger(), nullable=True),
        sa.Column("claimed_through_work_revision", sa.BigInteger(), nullable=True),
        sa.Column("completed_work_revision", sa.BigInteger(), nullable=True),
        sa.Column("last_input_identity_hash", sa.String(length=64), nullable=True),
        sa.Column("last_input_revision", sa.BigInteger(), nullable=True),
        sa.Column("work_generation", sa.BigInteger(), nullable=True),
        sa.Column("dead_generation", sa.BigInteger(), nullable=True),
        sa.Column("blocked_by", postgresql.JSONB(), nullable=True),
        sa.Column("lease_owner_kind", sa.String(length=32), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("dts_dirty_keys", column, schema="public")


def _install_append_only_guard_and_acl() -> None:
    op.execute(
        """
        COMMENT ON TABLE public.dts_source_partition_epochs IS
            'DTS v2 shadow only; no epoch bootstrap or activation path installed';
        COMMENT ON TABLE public.dts_source_row_versions IS
            'DTS v2 shadow only; source schema profile and field types preserve type evidence; typed source key is distinct from record_id; no runtime writer granted';

        CREATE OR REPLACE FUNCTION public.guard_dts_source_row_version_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'DTS_SOURCE_ROW_VERSION_IMMUTABLE';
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.guard_dts_source_row_version_append_only_v2()
        FROM PUBLIC;

        CREATE TRIGGER guard_dts_source_row_version_append_only_v2
        BEFORE UPDATE OR DELETE ON public.dts_source_row_versions
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_source_row_version_append_only_v2();

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_partition_epochs,
            public.dts_source_row_versions
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $shadow_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.dts_source_partition_epochs, '
                    'public.dts_source_row_versions FROM tit_teacher_crud';
            END IF;
        END
        $shadow_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _create_shadow_tables()
    _add_transition_columns()
    _install_append_only_guard_and_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.dts_dirty_keys,
            public.dts_ingest_checkpoints,
            public.dts_ingest_events,
            public.dts_source_partition_epochs,
            public.dts_source_row_versions,
            public.dts_source_rows
        IN ACCESS EXCLUSIVE MODE;

        DO $shadow_source_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.dts_source_row_versions LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM public.dts_source_partition_epochs LIMIT 1
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_ingest_events
                    WHERE identity_version IS NOT NULL
                       OR source_partition_epoch_id IS NOT NULL
                       OR source_position_v2 IS NOT NULL
                       OR event_payload_hash IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_ingest_checkpoints
                    WHERE source_partition_epoch_id IS NOT NULL
                       OR consumer_group IS NOT NULL
                       OR checkpoint_row_version IS NOT NULL
                       OR is_current_epoch IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_source_rows
                    WHERE source_row_revision IS NOT NULL
                       OR last_source_partition_epoch_id IS NOT NULL
                       OR last_version_kind IS NOT NULL
                       OR source_position_v2 IS NOT NULL
                       OR record_id_type IS NOT NULL
                       OR record_id_numeric IS NOT NULL
                       OR record_id_text IS NOT NULL
                       OR source_timestamp_v2 IS NOT NULL
                       OR source_payload_hash IS NOT NULL
                       OR provenance_state IS NOT NULL
                       OR source_key_type IS NOT NULL
                       OR source_key_numeric IS NOT NULL
                       OR source_key_text IS NOT NULL
                       OR source_schema_profile_id IS NOT NULL
                       OR source_field_types IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_dirty_keys
                    WHERE source_region IS NOT NULL
                       OR required_work_revision IS NOT NULL
                       OR claimed_through_work_revision IS NOT NULL
                       OR completed_work_revision IS NOT NULL
                       OR last_input_identity_hash IS NOT NULL
                       OR last_input_revision IS NOT NULL
                       OR work_generation IS NOT NULL
                       OR dead_generation IS NOT NULL
                       OR blocked_by IS NOT NULL
                       OR lease_owner_kind IS NOT NULL
                       OR lease_owner IS NOT NULL
                       OR lease_token IS NOT NULL
                       OR lease_expires_at IS NOT NULL
               )
            THEN
                RAISE EXCEPTION
                    'refusing v2 shadow-source downgrade: shadow data exists';
            END IF;
        END
        $shadow_source_downgrade_guard$;

        DROP TRIGGER IF EXISTS guard_dts_source_row_version_append_only_v2
            ON public.dts_source_row_versions;
        DROP FUNCTION IF EXISTS
            public.guard_dts_source_row_version_append_only_v2();
        """
    )

    for column_name in reversed(DIRTY_KEY_TRANSITION_COLUMNS):
        op.drop_column("dts_dirty_keys", column_name, schema="public")
    for column_name in reversed(SOURCE_ROW_TRANSITION_COLUMNS):
        op.drop_column("dts_source_rows", column_name, schema="public")
    for column_name in reversed(CHECKPOINT_TRANSITION_COLUMNS):
        op.drop_column("dts_ingest_checkpoints", column_name, schema="public")
    for column_name in reversed(EVENT_TRANSITION_COLUMNS):
        op.drop_column("dts_ingest_events", column_name, schema="public")

    op.drop_index(
        "ix_dts_source_row_versions_source_order",
        table_name="dts_source_row_versions",
        schema="public",
    )
    op.drop_index(
        "uq_dts_source_row_version_source_revision",
        table_name="dts_source_row_versions",
        schema="public",
    )
    op.drop_index(
        "uq_dts_source_row_version_snapshot_diff_key_step",
        table_name="dts_source_row_versions",
        schema="public",
    )
    op.drop_index(
        "uq_dts_source_row_version_snapshot_key",
        table_name="dts_source_row_versions",
        schema="public",
    )
    op.drop_table("dts_source_row_versions", schema="public")
    op.drop_index(
        "uq_dts_source_partition_epoch_active_broker",
        table_name="dts_source_partition_epochs",
        schema="public",
    )
    op.drop_table("dts_source_partition_epochs", schema="public")
