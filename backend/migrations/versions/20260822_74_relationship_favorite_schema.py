"""add the inert relationship and favorite-attribution shadow schema.

Revision ID: 20260822_74_favorite_schema
Revises: 20260822_73_retire_false_early
Create Date: 2026-08-22

This revision is schema-only.  It persists the append-only teacher/student
relationship timeline, its current projection, end+24h favorite observations,
and the lifetime favorite award attribution.  Deferred guards make completion
transfers and observation/attribution transitions atomic without activating a
runtime writer, consumer, score settler, or read route.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_74_favorite_schema"
down_revision: Union[str, None] = "20260822_73_retire_false_early"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SHADOW_SCHEMA_ONLY = True
RUNTIME_ROUTE_ACTIVATED = False
RELATIONSHIP_EVENT_HISTORY_IMMUTABLE = True
DEFERRED_FAVORITE_INTEGRITY_GUARD_IMPLEMENTED = True


def _assert_upgrade_preconditions() -> None:
    op.execute(
        """
        DO $favorite_schema_preflight$
        BEGIN
            IF to_regclass('public.teacher_student_relationship_events')
                    IS NOT NULL
               OR to_regclass('public.teacher_student_relationship_current')
                    IS NOT NULL
               OR to_regclass('public.course_favorite_observations')
                    IS NOT NULL
               OR to_regclass('public.course_favorite_attributions')
                    IS NOT NULL
               OR to_regprocedure(
                    'public.dts_v2_typed_id_valid(text,text)'
                  ) IS NOT NULL
               OR to_regprocedure(
                    'public.dts_v2_assert_favorite_course(text,text)'
                  ) IS NOT NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_SCHEMA_ALREADY_PRESENT';
            END IF;
        END
        $favorite_schema_preflight$;
        """
    )


def _create_support_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_typed_id_valid(
            id_type text,
            id_value text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            numeric_value numeric;
        BEGIN
            IF id_type = 'TEXT' THEN
                RETURN id_value IS NOT NULL AND id_value <> '';
            END IF;
            IF id_type IS DISTINCT FROM 'NUMERIC'
               OR id_value IS NULL
               OR lower(id_value) IN (
                    'nan', 'infinity', '+infinity', '-infinity',
                    'inf', '+inf', '-inf'
               ) THEN
                RETURN false;
            END IF;
            numeric_value := id_value::numeric;
            RETURN trim_scale(numeric_value)::text = id_value;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RETURN false;
        END
        $function$;
        """
    )


def _create_relationship_events() -> None:
    op.create_table(
        "teacher_student_relationship_events",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column(
            "source_partition_epoch_id", sa.String(length=160), nullable=False
        ),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("offset_value", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_sequence",
            sa.BigInteger(),
            sa.Identity(start=1),
            nullable=False,
        ),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=False),
        sa.Column("source_record_id_type", sa.String(length=16), nullable=False),
        sa.Column("source_record_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("source_record_id_text", sa.Text(), nullable=True),
        sa.Column("source_row_revision", sa.BigInteger(), nullable=False),
        sa.Column("relationship_type", sa.String(length=16), nullable=False),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("old_teacher_id", sa.String(length=64), nullable=True),
        sa.Column("old_teacher_id_type", sa.String(length=16), nullable=True),
        sa.Column("new_teacher_id", sa.String(length=64), nullable=True),
        sa.Column("new_teacher_id_type", sa.String(length=16), nullable=True),
        sa.Column("old_student_token", sa.String(length=128), nullable=True),
        sa.Column("new_student_token", sa.String(length=128), nullable=True),
        sa.Column("old_valid_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_valid_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_valid_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_valid_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_is_valid_forever", sa.Boolean(), nullable=True),
        sa.Column("new_is_valid_forever", sa.Boolean(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "effective_time_evidence_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
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
            name="pk_teacher_student_relationship_events",
        ),
        sa.UniqueConstraint(
            "event_sequence",
            name="uq_teacher_student_relationship_event_sequence",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_partition_epoch_id",
                "topic",
                "partition_id",
                "offset_value",
            ],
            [
                "public.dts_source_row_versions.source_region",
                "public.dts_source_row_versions.source_partition_epoch_id",
                "public.dts_source_row_versions.topic",
                "public.dts_source_row_versions.partition_id",
                "public.dts_source_row_versions.offset_value",
            ],
            name="fk_relationship_event_source_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_relationship_event_region",
        ),
        sa.CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0 "
            "AND source_row_revision >= 1",
            name="ck_relationship_event_source_numbers",
        ),
        sa.CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "source_record_id_type, source_record_id) IS TRUE "
            "AND CASE source_record_id_type "
            "WHEN 'NUMERIC' THEN source_record_id_numeric IS NOT NULL "
            "AND source_record_id_text IS NULL "
            "AND source_record_id_numeric = source_record_id::numeric "
            "WHEN 'TEXT' THEN source_record_id_numeric IS NULL "
            "AND source_record_id_text = source_record_id "
            "ELSE false END",
            name="ck_relationship_event_typed_source_id",
        ),
        sa.CheckConstraint(
            "relationship_type IN ('FAVORITE', 'BLOCK') "
            "AND ((source_region = 'dom' AND source_table IN ("
            "'dom_teacher_favorite', 'dom_teacher_blacklist')) "
            "OR (source_region = 'ovs' AND source_table IN ("
            "'ovs_teacher_favorite', 'ovs_teacher_blacklist'))) "
            "AND ((relationship_type = 'FAVORITE' "
            "AND source_table LIKE '%_teacher_favorite') "
            "OR (relationship_type = 'BLOCK' "
            "AND source_table LIKE '%_teacher_blacklist'))",
            name="ck_relationship_event_route",
        ),
        sa.CheckConstraint(
            "operation IN ("
            "'INSERT', 'UPDATE', 'DELETE', 'SNAPSHOT_INSERT', "
            "'SNAPSHOT_UPDATE', 'SNAPSHOT_DELETE', "
            "'SNAPSHOT_BOOTSTRAP_PRESENT', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE')",
            name="ck_relationship_event_operation",
        ),
        sa.CheckConstraint(
            "((old_teacher_id IS NULL AND old_teacher_id_type IS NULL "
            "AND old_student_token IS NULL) "
            "OR (old_teacher_id IS NOT NULL "
            "AND public.dts_v2_typed_id_valid("
            "old_teacher_id_type, old_teacher_id) IS TRUE "
            "AND old_student_token IS NOT NULL "
            "AND old_student_token <> '')) "
            "AND ((new_teacher_id IS NULL AND new_teacher_id_type IS NULL "
            "AND new_student_token IS NULL) "
            "OR (new_teacher_id IS NOT NULL "
            "AND public.dts_v2_typed_id_valid("
            "new_teacher_id_type, new_teacher_id) IS TRUE "
            "AND new_student_token IS NOT NULL "
            "AND new_student_token <> ''))",
            name="ck_relationship_event_pair_shapes",
        ),
        sa.CheckConstraint(
            "(operation IN ("
            "'INSERT', 'SNAPSHOT_INSERT', 'SNAPSHOT_BOOTSTRAP_PRESENT') "
            "AND old_teacher_id IS NULL AND new_teacher_id IS NOT NULL) "
            "OR (operation IN ('UPDATE', 'SNAPSHOT_UPDATE') "
            "AND old_teacher_id IS NOT NULL "
            "AND new_teacher_id IS NOT NULL) "
            "OR (operation IN ("
            "'DELETE', 'SNAPSHOT_DELETE', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE') "
            "AND old_teacher_id IS NOT NULL AND new_teacher_id IS NULL)",
            name="ck_relationship_event_crud_images",
        ),
        sa.CheckConstraint(
            "source_region = 'ovs' OR ("
            "(old_student_token IS NULL OR old_student_token "
            "~ '^dom:v1:[0-9a-f]{64}$') "
            "AND (new_student_token IS NULL OR new_student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'))",
            name="ck_relationship_event_dom_student_tokens",
        ),
        sa.CheckConstraint(
            "(old_valid_start_at IS NULL OR old_valid_end_at IS NULL "
            "OR old_valid_end_at >= old_valid_start_at) "
            "AND (new_valid_start_at IS NULL OR new_valid_end_at IS NULL "
            "OR new_valid_end_at >= new_valid_start_at)",
            name="ck_relationship_event_valid_intervals",
        ),
        sa.CheckConstraint(
            "(effective_time_evidence_status = 'CONFIRMED' "
            "AND effective_at IS NOT NULL) "
            "OR (effective_time_evidence_status = 'SOURCE_MISSING' "
            "AND effective_at IS NULL)",
            name="ck_relationship_event_effective_evidence",
        ),
        schema="public",
    )
    op.create_index(
        "ix_relationship_events_pair_sequence",
        "teacher_student_relationship_events",
        ["source_region", "event_sequence"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_relationship_events_business_time",
        "teacher_student_relationship_events",
        ["source_region", "relationship_type", "effective_at"],
        unique=False,
        schema="public",
    )


def _create_relationship_current() -> None:
    op.create_table(
        "teacher_student_relationship_current",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id_type", sa.String(length=16), nullable=False),
        sa.Column("student_token", sa.String(length=128), nullable=False),
        sa.Column("is_favorited", sa.Boolean(), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=True),
        sa.Column(
            "last_business_effective_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "effective_time_evidence_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("last_event_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_source_partition_epoch_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("last_topic", sa.String(length=512), nullable=False),
        sa.Column("last_partition_id", sa.Integer(), nullable=False),
        sa.Column("last_offset_value", sa.BigInteger(), nullable=False),
        sa.Column("last_source_row_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "teacher_id",
            "student_token",
            name="pk_teacher_student_relationship_current",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "last_source_partition_epoch_id",
                "last_topic",
                "last_partition_id",
                "last_offset_value",
            ],
            [
                "public.teacher_student_relationship_events.source_region",
                "public.teacher_student_relationship_events.source_partition_epoch_id",
                "public.teacher_student_relationship_events.topic",
                "public.teacher_student_relationship_events.partition_id",
                "public.teacher_student_relationship_events.offset_value",
            ],
            name="fk_relationship_current_latest_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_relationship_current_region",
        ),
        sa.CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_relationship_current_typed_teacher",
        ),
        sa.CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_relationship_current_dom_student_token",
        ),
        sa.CheckConstraint(
            "student_token <> '' AND last_event_sequence >= 1 "
            "AND last_partition_id >= 0 AND last_offset_value >= 0 "
            "AND last_source_row_revision >= 1 AND row_version >= 1",
            name="ck_relationship_current_numbers_and_identity",
        ),
        sa.CheckConstraint(
            "effective_time_evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING')",
            name="ck_relationship_current_evidence",
        ),
        schema="public",
    )
    op.create_index(
        "ix_relationship_current_teacher_favorite",
        "teacher_student_relationship_current",
        ["source_region", "teacher_id", "student_token"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("is_favorited IS TRUE"),
    )
    op.create_index(
        "ix_relationship_current_teacher_block",
        "teacher_student_relationship_current",
        ["source_region", "teacher_id", "student_token"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("is_blocked IS TRUE"),
    )


def _create_favorite_observations() -> None:
    op.create_table(
        "course_favorite_observations",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("observation_revision", sa.BigInteger(), nullable=False),
        sa.Column("appoint_id_type", sa.String(length=16), nullable=False),
        sa.Column("appoint_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("appoint_id_text_sort", sa.LargeBinary(), nullable=True),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id_type", sa.String(length=16), nullable=False),
        sa.Column("student_token", sa.String(length=128), nullable=False),
        sa.Column("completion_participation_seq", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relation_state", sa.Boolean(), nullable=True),
        sa.Column(
            "relation_evidence_status", sa.String(length=32), nullable=False
        ),
        sa.Column("relation_error_code", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_evidence_revision", sa.BigInteger(), nullable=False),
        sa.Column("claimed_evidence_revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "completed_evidence_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "required_evidence_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_token", sa.String(length=160), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "dead_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("technical_case_id", sa.String(length=160), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("materialization_origin", sa.String(length=32), nullable=False),
        sa.Column("materialized_by_run_id", sa.String(length=160), nullable=True),
        sa.Column(
            "created_projection_generation", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "serving_projection_generation", sa.BigInteger(), nullable=False
        ),
        sa.Column("is_serving", sa.Boolean(), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            "observation_revision",
            name="pk_course_favorite_observations",
        ),
        sa.UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "observation_revision",
            "teacher_id",
            "teacher_id_type",
            "student_token",
            "completion_participation_seq",
            name="uq_favorite_observation_attribution_identity",
        ),
        sa.UniqueConstraint(
            "lease_token",
            name="uq_favorite_observation_lease_token",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            [
                "public.source_courses.source_region",
                "public.source_courses.source_appoint_id",
            ],
            name="fk_favorite_observation_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
                "teacher_id",
                "teacher_id_type",
            ],
            [
                "public.source_course_participations.source_region",
                "public.source_course_participations.source_appoint_id",
                "public.source_course_participations.participation_seq",
                "public.source_course_participations.teacher_id",
                "public.source_course_participations.teacher_id_type",
            ],
            name="fk_favorite_observation_completion_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_favorite_observation_region",
        ),
        sa.CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_favorite_observation_typed_teacher",
        ),
        sa.CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_favorite_observation_dom_student_token",
        ),
        sa.CheckConstraint(
            "student_token <> '' AND observation_revision >= 1 "
            "AND completion_participation_seq >= 1 "
            "AND required_evidence_revision >= 1 "
            "AND completed_evidence_revision >= 0 "
            "AND completed_evidence_revision <= required_evidence_revision "
            "AND (claimed_evidence_revision IS NULL "
            "OR (claimed_evidence_revision >= 1 "
            "AND claimed_evidence_revision <= required_evidence_revision)) "
            "AND attempt_count BETWEEN 0 AND 8 "
            "AND dead_generation >= 0 AND row_version >= 1 "
            "AND created_projection_generation >= 0 "
            "AND serving_projection_generation >= 0",
            name="ck_favorite_observation_numbers",
        ),
        sa.CheckConstraint(
            "required_evidence_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND trim(rule_version) <> ''",
            name="ck_favorite_observation_version_evidence",
        ),
        sa.CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "appoint_id_type, source_appoint_id) IS TRUE "
            "AND CASE appoint_id_type "
            "WHEN 'NUMERIC' THEN appoint_id_numeric IS NOT NULL "
            "AND appoint_id_numeric = source_appoint_id::numeric "
            "AND appoint_id_text_sort IS NULL "
            "WHEN 'TEXT' THEN appoint_id_numeric IS NULL "
            "AND appoint_id_text_sort = convert_to("
            "source_appoint_id, 'UTF8') "
            "ELSE false END",
            name="ck_favorite_observation_typed_appoint",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'EVALUATING', 'CONFIRMED_TRUE', "
            "'CONFIRMED_FALSE', 'WAITING_HISTORY', 'WAITING_EVIDENCE', "
            "'RETRY', 'DEAD', 'INVALIDATED', 'VOIDED')",
            name="ck_favorite_observation_status",
        ),
        sa.CheckConstraint(
            "relation_evidence_status IN ("
            "'PENDING', 'CONFIRMED', 'HISTORY_INCOMPLETE', "
            "'SOURCE_MISSING')",
            name="ck_favorite_observation_evidence_status",
        ),
        sa.CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL) "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_favorite_observation_origin",
        ),
        sa.CheckConstraint(
            "((status = 'PENDING' AND attempt_count = 0 "
            "AND next_attempt_at IS NOT NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NULL) "
            "OR (status = 'RETRY' AND attempt_count BETWEEN 1 AND 7 "
            "AND next_attempt_at IS NOT NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NOT NULL) "
            "OR (status = 'EVALUATING' AND attempt_count BETWEEN 1 AND 8 "
            "AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NOT NULL "
            "AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) "
            "OR (status = 'DEAD' AND attempt_count = 8 "
            "AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NOT NULL AND dead_generation >= 1 "
            "AND technical_case_id IS NOT NULL) "
            "OR (status IN ('CONFIRMED_TRUE', 'CONFIRMED_FALSE', "
            "'WAITING_HISTORY', 'WAITING_EVIDENCE', "
            "'INVALIDATED', 'VOIDED') AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL))",
            name="ck_favorite_observation_worker_shape",
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED_TRUE' "
            "AND relation_state IS TRUE "
            "AND relation_evidence_status = 'CONFIRMED' "
            "AND relation_error_code IS NULL "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'CONFIRMED_FALSE' "
            "AND relation_state IS FALSE "
            "AND relation_evidence_status = 'CONFIRMED' "
            "AND relation_error_code IS NULL "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'WAITING_HISTORY' "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'HISTORY_INCOMPLETE' "
            "AND relation_error_code = "
            "'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE' "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'WAITING_EVIDENCE' "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'SOURCE_MISSING' "
            "AND relation_error_code = "
            "'SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING' "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status IN ('PENDING', 'EVALUATING', 'RETRY', 'DEAD') "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'PENDING' "
            "AND relation_error_code IS NULL) "
            "OR status IN ('INVALIDATED', 'VOIDED')",
            name="ck_favorite_observation_evidence_shape",
        ),
        sa.CheckConstraint(
            "((status IN ('INVALIDATED', 'VOIDED') "
            "AND terminal_reason IS NOT NULL AND terminal_at IS NOT NULL "
            "AND is_serving IS FALSE) "
            "OR (status NOT IN ('INVALIDATED', 'VOIDED') "
            "AND terminal_reason IS NULL AND terminal_at IS NULL))",
            name="ck_favorite_observation_terminal_shape",
        ),
        schema="public",
    )
    op.create_index(
        "uq_favorite_observation_current",
        "course_favorite_observations",
        ["source_region", "source_appoint_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text(
            "status NOT IN ('INVALIDATED', 'VOIDED')"
        ),
    )
    op.create_index(
        "ix_favorite_observation_due",
        "course_favorite_observations",
        ["next_attempt_at", "observed_at", "source_region"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("status IN ('PENDING', 'RETRY')"),
    )
    op.create_index(
        "ix_favorite_observation_expired_lease",
        "course_favorite_observations",
        ["lease_expires_at", "source_region"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("status = 'EVALUATING'"),
    )
    op.create_index(
        "ix_favorite_observation_candidate_numeric",
        "course_favorite_observations",
        [
            "source_region",
            "teacher_id",
            "student_token",
            "observed_at",
            "appoint_id_numeric",
            "observation_revision",
        ],
        unique=False,
        schema="public",
        postgresql_where=sa.text(
            "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'NUMERIC'"
        ),
    )
    op.create_index(
        "ix_favorite_observation_candidate_text",
        "course_favorite_observations",
        [
            "source_region",
            "teacher_id",
            "student_token",
            "observed_at",
            "appoint_id_text_sort",
            "observation_revision",
        ],
        unique=False,
        schema="public",
        postgresql_where=sa.text(
            "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'TEXT'"
        ),
    )


def _create_favorite_attributions() -> None:
    op.create_table(
        "course_favorite_attributions",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id_type", sa.String(length=16), nullable=False),
        sa.Column("student_token", sa.String(length=128), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("observation_revision", sa.BigInteger(), nullable=False),
        sa.Column("completion_participation_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("hold_reason", sa.String(length=40), nullable=True),
        sa.Column("points", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("award_generation", sa.BigInteger(), nullable=False),
        sa.Column("current_score_entry_id", sa.String(length=128), nullable=False),
        sa.Column(
            "last_reversal_score_entry_id", sa.String(length=128), nullable=True
        ),
        sa.Column("recompute_reason", sa.Text(), nullable=False),
        sa.Column("materialization_origin", sa.String(length=32), nullable=False),
        sa.Column("materialized_by_run_id", sa.String(length=160), nullable=True),
        sa.Column("award_projection_generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "awarded_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "teacher_id",
            "student_token",
            name="pk_course_favorite_attributions",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "observation_revision",
                "teacher_id",
                "teacher_id_type",
                "student_token",
                "completion_participation_seq",
            ],
            [
                "public.course_favorite_observations.source_region",
                "public.course_favorite_observations.source_appoint_id",
                "public.course_favorite_observations.observation_revision",
                "public.course_favorite_observations.teacher_id",
                "public.course_favorite_observations.teacher_id_type",
                "public.course_favorite_observations.student_token",
                "public.course_favorite_observations.completion_participation_seq",
            ],
            name="fk_favorite_attribution_observation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
                "teacher_id",
                "teacher_id_type",
            ],
            [
                "public.source_course_participations.source_region",
                "public.source_course_participations.source_appoint_id",
                "public.source_course_participations.participation_seq",
                "public.source_course_participations.teacher_id",
                "public.source_course_participations.teacher_id_type",
            ],
            name="fk_favorite_attribution_completion_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["current_score_entry_id"],
            ["public.score_entries.score_entry_id"],
            name="fk_favorite_attribution_current_score_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_reversal_score_entry_id"],
            ["public.score_entries.score_entry_id"],
            name="fk_favorite_attribution_reversal_score_entry",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_favorite_attribution_region",
        ),
        sa.CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_favorite_attribution_typed_teacher",
        ),
        sa.CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_favorite_attribution_dom_student_token",
        ),
        sa.CheckConstraint(
            "student_token <> '' AND observation_revision >= 1 "
            "AND completion_participation_seq >= 1 "
            "AND award_generation >= 1 AND points > 0 "
            "AND award_projection_generation >= 0 AND row_version >= 1 "
            "AND trim(rule_version) <> '' "
            "AND trim(recompute_reason) <> '' "
            "AND (last_reversal_score_entry_id IS NULL "
            "OR last_reversal_score_entry_id <> current_score_entry_id)",
            name="ck_favorite_attribution_values",
        ),
        sa.CheckConstraint(
            "(status = 'AWARDED' AND hold_reason IS NULL "
            "AND reversed_at IS NULL) "
            "OR (status = 'AWARDED_PENDING_EVIDENCE' "
            "AND hold_reason IN ("
            "'REVALIDATION_PENDING', 'WAITING_HISTORY', "
            "'WAITING_EVIDENCE') AND reversed_at IS NULL) "
            "OR (status = 'REVERSED' AND hold_reason IS NULL "
            "AND last_reversal_score_entry_id IS NOT NULL "
            "AND reversed_at IS NOT NULL)",
            name="ck_favorite_attribution_status_shape",
        ),
        sa.CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL) "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_favorite_attribution_origin",
        ),
        schema="public",
    )
    op.create_index(
        "uq_favorite_attribution_current_course",
        "course_favorite_attributions",
        ["source_region", "source_appoint_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text(
            "status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')"
        ),
    )


def _create_relationship_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_relationship_event_immutable_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION
                'DTS_V2_RELATIONSHIP_EVENT_IMMUTABLE: relationship events are append-only';
        END
        $function$;

        CREATE FUNCTION public.dts_v2_relationship_current_lifecycle_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'DTS_V2_RELATIONSHIP_CURRENT_DELETE_DENIED: retain the false/unknown current fact';
            END IF;
            IF ROW(
                NEW.source_region, NEW.teacher_id, NEW.student_token
            ) IS DISTINCT FROM ROW(
                OLD.source_region, OLD.teacher_id, OLD.student_token
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_RELATIONSHIP_CURRENT_IDENTITY_IMMUTABLE';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 THEN
                RAISE EXCEPTION
                    'DTS_V2_RELATIONSHIP_CURRENT_ROW_VERSION_INVALID';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_relationship_event_provenance_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            source_version record;
        BEGIN
            SELECT source_table, operation, source_row_revision
            INTO source_version
            FROM public.dts_source_row_versions
            WHERE source_region = NEW.source_region
              AND source_partition_epoch_id =
                    NEW.source_partition_epoch_id
              AND topic = NEW.topic
              AND partition_id = NEW.partition_id
              AND offset_value = NEW.offset_value;

            IF NOT FOUND
               OR source_version.source_table IS DISTINCT FROM
                    NEW.source_table
               OR source_version.operation IS DISTINCT FROM NEW.operation
               OR source_version.source_row_revision IS DISTINCT FROM
                    NEW.source_row_revision THEN
                RAISE EXCEPTION
                    'DTS_V2_RELATIONSHIP_EVENT_PROVENANCE_INVALID';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_assert_relationship_current(
            guard_region text,
            guard_teacher_type text,
            guard_teacher_id text,
            guard_student_token text
        )
        RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            latest_event record;
            current_row record;
        BEGIN
            SELECT *
            INTO latest_event
            FROM public.teacher_student_relationship_events AS event
            WHERE event.source_region = guard_region
              AND (
                    ROW(
                        event.old_teacher_id_type,
                        event.old_teacher_id,
                        event.old_student_token
                    ) = ROW(
                        guard_teacher_type,
                        guard_teacher_id,
                        guard_student_token
                    )
                    OR ROW(
                        event.new_teacher_id_type,
                        event.new_teacher_id,
                        event.new_student_token
                    ) = ROW(
                        guard_teacher_type,
                        guard_teacher_id,
                        guard_student_token
                    )
              )
            ORDER BY event.event_sequence DESC
            LIMIT 1;

            IF NOT FOUND THEN
                IF EXISTS (
                    SELECT 1
                    FROM public.teacher_student_relationship_current AS current
                    WHERE current.source_region = guard_region
                      AND current.teacher_id = guard_teacher_id
                      AND current.student_token = guard_student_token
                ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_RELATIONSHIP_CURRENT_WITHOUT_EVENT';
                END IF;
                RETURN;
            END IF;

            SELECT *
            INTO current_row
            FROM public.teacher_student_relationship_current AS current
            WHERE current.source_region = guard_region
              AND current.teacher_id = guard_teacher_id
              AND current.student_token = guard_student_token;

            IF NOT FOUND
               OR current_row.teacher_id_type IS DISTINCT FROM
                    guard_teacher_type
               OR current_row.last_business_effective_at IS DISTINCT FROM
                    latest_event.effective_at
               OR current_row.effective_time_evidence_status IS DISTINCT FROM
                    latest_event.effective_time_evidence_status
               OR current_row.last_event_sequence IS DISTINCT FROM
                    latest_event.event_sequence
               OR ROW(
                    current_row.last_source_partition_epoch_id,
                    current_row.last_topic,
                    current_row.last_partition_id,
                    current_row.last_offset_value,
                    current_row.last_source_row_revision
                  ) IS DISTINCT FROM ROW(
                    latest_event.source_partition_epoch_id,
                    latest_event.topic,
                    latest_event.partition_id,
                    latest_event.offset_value,
                    latest_event.source_row_revision
                  ) THEN
                RAISE EXCEPTION
                    'DTS_V2_RELATIONSHIP_CURRENT_NOT_LATEST_EVENT';
            END IF;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_relationship_current_pointer_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_TABLE_NAME = 'teacher_student_relationship_events' THEN
                IF NEW.old_teacher_id IS NOT NULL THEN
                    PERFORM public.dts_v2_assert_relationship_current(
                        NEW.source_region,
                        NEW.old_teacher_id_type,
                        NEW.old_teacher_id,
                        NEW.old_student_token
                    );
                END IF;
                IF NEW.new_teacher_id IS NOT NULL
                   AND ROW(
                        NEW.new_teacher_id_type,
                        NEW.new_teacher_id,
                        NEW.new_student_token
                   ) IS DISTINCT FROM ROW(
                        NEW.old_teacher_id_type,
                        NEW.old_teacher_id,
                        NEW.old_student_token
                   ) THEN
                    PERFORM public.dts_v2_assert_relationship_current(
                        NEW.source_region,
                        NEW.new_teacher_id_type,
                        NEW.new_teacher_id,
                        NEW.new_student_token
                    );
                END IF;
            ELSE
                PERFORM public.dts_v2_assert_relationship_current(
                    COALESCE(NEW.source_region, OLD.source_region),
                    COALESCE(NEW.teacher_id_type, OLD.teacher_id_type),
                    COALESCE(NEW.teacher_id, OLD.teacher_id),
                    COALESCE(NEW.student_token, OLD.student_token)
                );
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE TRIGGER trg_relationship_event_immutable
        BEFORE UPDATE OR DELETE
        ON public.teacher_student_relationship_events
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_relationship_event_immutable_guard();

        CREATE TRIGGER trg_relationship_current_lifecycle
        BEFORE UPDATE OR DELETE
        ON public.teacher_student_relationship_current
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_relationship_current_lifecycle_guard();

        CREATE CONSTRAINT TRIGGER ct_relationship_event_provenance
        AFTER INSERT ON public.teacher_student_relationship_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_relationship_event_provenance_guard();

        CREATE CONSTRAINT TRIGGER ct_relationship_event_current_pointer
        AFTER INSERT ON public.teacher_student_relationship_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_relationship_current_pointer_guard();

        CREATE CONSTRAINT TRIGGER ct_relationship_current_latest_event
        AFTER INSERT OR UPDATE OR DELETE
        ON public.teacher_student_relationship_current
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_relationship_current_pointer_guard();
        """
    )


def _create_favorite_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_favorite_observation_lifecycle_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_OBSERVATION_DELETE_DENIED';
            END IF;
            IF ROW(
                NEW.source_region,
                NEW.source_appoint_id,
                NEW.observation_revision,
                NEW.appoint_id_type,
                NEW.appoint_id_numeric,
                NEW.appoint_id_text_sort,
                NEW.teacher_id,
                NEW.teacher_id_type,
                NEW.student_token,
                NEW.completion_participation_seq,
                NEW.observed_at
            ) IS DISTINCT FROM ROW(
                OLD.source_region,
                OLD.source_appoint_id,
                OLD.observation_revision,
                OLD.appoint_id_type,
                OLD.appoint_id_numeric,
                OLD.appoint_id_text_sort,
                OLD.teacher_id,
                OLD.teacher_id_type,
                OLD.student_token,
                OLD.completion_participation_seq,
                OLD.observed_at
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_OBSERVATION_IDENTITY_IMMUTABLE';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_OBSERVATION_ROW_VERSION_INVALID';
            END IF;
            IF OLD.status IN ('INVALIDATED', 'VOIDED')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_OBSERVATION_TERMINAL';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_favorite_attribution_lifecycle_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_ATTRIBUTION_DELETE_DENIED: lifetime award identity must be retained';
            END IF;
            IF ROW(
                NEW.source_region, NEW.teacher_id, NEW.student_token,
                NEW.teacher_id_type
            ) IS DISTINCT FROM ROW(
                OLD.source_region, OLD.teacher_id, OLD.student_token,
                OLD.teacher_id_type
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_ATTRIBUTION_IDENTITY_IMMUTABLE';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_ATTRIBUTION_ROW_VERSION_INVALID';
            END IF;

            IF OLD.status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')
               AND NEW.status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE') THEN
                IF NEW.award_generation = OLD.award_generation THEN
                    IF ROW(
                        NEW.source_appoint_id,
                        NEW.observation_revision,
                        NEW.completion_participation_seq,
                        NEW.points,
                        NEW.rule_version,
                        NEW.current_score_entry_id,
                        NEW.last_reversal_score_entry_id,
                        NEW.materialization_origin,
                        NEW.materialized_by_run_id,
                        NEW.award_projection_generation,
                        NEW.awarded_at
                    ) IS DISTINCT FROM ROW(
                        OLD.source_appoint_id,
                        OLD.observation_revision,
                        OLD.completion_participation_seq,
                        OLD.points,
                        OLD.rule_version,
                        OLD.current_score_entry_id,
                        OLD.last_reversal_score_entry_id,
                        OLD.materialization_origin,
                        OLD.materialized_by_run_id,
                        OLD.award_projection_generation,
                        OLD.awarded_at
                    ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_FAVORITE_HELD_AWARD_MUTATED';
                    END IF;
                ELSIF NEW.award_generation = OLD.award_generation + 1 THEN
                    IF NEW.status IS DISTINCT FROM OLD.status
                       OR NEW.hold_reason IS DISTINCT FROM OLD.hold_reason
                       OR ROW(
                        NEW.source_appoint_id,
                        NEW.observation_revision,
                        NEW.completion_participation_seq
                    ) IS DISTINCT FROM ROW(
                        OLD.source_appoint_id,
                        OLD.observation_revision,
                        OLD.completion_participation_seq
                    )
                       OR NEW.rule_version IS NOT DISTINCT FROM
                            OLD.rule_version
                       OR NEW.current_score_entry_id =
                            OLD.current_score_entry_id
                       OR NEW.last_reversal_score_entry_id IS NULL
                       OR NEW.last_reversal_score_entry_id =
                            NEW.current_score_entry_id THEN
                        RAISE EXCEPTION
                            'DTS_V2_FAVORITE_RULE_REAWARD_INVALID';
                    END IF;
                ELSE
                    RAISE EXCEPTION
                        'DTS_V2_FAVORITE_ACTIVE_GENERATION_INVALID';
                END IF;
            END IF;

            IF OLD.status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')
               AND NEW.status = 'REVERSED'
               AND ROW(
                    NEW.source_appoint_id,
                    NEW.observation_revision,
                    NEW.completion_participation_seq,
                    NEW.points,
                    NEW.rule_version,
                    NEW.award_generation,
                    NEW.current_score_entry_id
               ) IS DISTINCT FROM ROW(
                    OLD.source_appoint_id,
                    OLD.observation_revision,
                    OLD.completion_participation_seq,
                    OLD.points,
                    OLD.rule_version,
                    OLD.award_generation,
                    OLD.current_score_entry_id
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_REVERSAL_IDENTITY_MUTATED';
            END IF;

            IF OLD.status = 'REVERSED'
               AND NEW.status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')
               AND (
                    NEW.award_generation <> OLD.award_generation + 1
                    OR NEW.current_score_entry_id =
                        OLD.current_score_entry_id
                    OR NEW.last_reversal_score_entry_id IS DISTINCT FROM
                        OLD.last_reversal_score_entry_id
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_NEW_GENERATION_INVALID';
            END IF;
            IF OLD.status = 'REVERSED'
               AND NEW.status = 'REVERSED'
               AND ROW(
                    NEW.source_appoint_id,
                    NEW.observation_revision,
                    NEW.completion_participation_seq,
                    NEW.points,
                    NEW.rule_version,
                    NEW.award_generation,
                    NEW.current_score_entry_id,
                    NEW.last_reversal_score_entry_id
               ) IS DISTINCT FROM ROW(
                    OLD.source_appoint_id,
                    OLD.observation_revision,
                    OLD.completion_participation_seq,
                    OLD.points,
                    OLD.rule_version,
                    OLD.award_generation,
                    OLD.current_score_entry_id,
                    OLD.last_reversal_score_entry_id
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_REVERSED_HISTORY_MUTATED';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_assert_favorite_course(
            guard_region text,
            guard_appoint_id text
        )
        RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            course_row record;
            observation_row record;
            attribution_row record;
            referenced_observation record;
            selected_observation record;
            completion_role text;
            revision_min bigint;
            revision_max bigint;
            revision_count bigint;
        BEGIN
            SELECT *
            INTO course_row
            FROM public.source_courses
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id;

            SELECT min(observation_revision), max(observation_revision), count(*)
            INTO revision_min, revision_max, revision_count
            FROM public.course_favorite_observations
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id;
            IF revision_count > 0
               AND (revision_min <> 1 OR revision_count <> revision_max) THEN
                RAISE EXCEPTION
                    'DTS_V2_FAVORITE_OBSERVATION_REVISION_GAP';
            END IF;

            FOR observation_row IN
                SELECT *
                FROM public.course_favorite_observations
                WHERE source_region = guard_region
                  AND source_appoint_id = guard_appoint_id
                  AND status NOT IN ('INVALIDATED', 'VOIDED')
            LOOP
                SELECT participation_role
                INTO completion_role
                FROM public.source_course_participations
                WHERE source_region = guard_region
                  AND source_appoint_id = guard_appoint_id
                  AND participation_seq =
                        observation_row.completion_participation_seq
                  AND teacher_id = observation_row.teacher_id
                  AND teacher_id_type = observation_row.teacher_id_type;
                IF course_row.source_appoint_id IS NULL
                   OR course_row.completion_participation_seq IS NULL
                   OR course_row.completion_teacher_id IS NULL
                   OR course_row.completion_teacher_id_type IS NULL
                   OR course_row.completion_student_token IS NULL
                   OR course_row.completion_end_time IS NULL
                   OR completion_role IS DISTINCT FROM 'COMPLETION'
                   OR ROW(
                        observation_row.completion_participation_seq,
                        observation_row.teacher_id,
                        observation_row.teacher_id_type,
                        observation_row.student_token,
                        observation_row.observed_at
                   ) IS DISTINCT FROM ROW(
                        course_row.completion_participation_seq,
                        course_row.completion_teacher_id,
                        course_row.completion_teacher_id_type,
                        course_row.completion_student_token,
                        course_row.completion_end_time + interval '24 hours'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_FAVORITE_OBSERVATION_COMPLETION_MISMATCH';
                END IF;
            END LOOP;

            FOR attribution_row IN
                SELECT *
                FROM public.course_favorite_attributions
                WHERE source_region = guard_region
                  AND source_appoint_id = guard_appoint_id
                  AND status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')
            LOOP
                IF course_row.source_appoint_id IS NULL
                   OR ROW(
                        attribution_row.completion_participation_seq,
                        attribution_row.teacher_id,
                        attribution_row.teacher_id_type,
                        attribution_row.student_token
                   ) IS DISTINCT FROM ROW(
                        course_row.completion_participation_seq,
                        course_row.completion_teacher_id,
                        course_row.completion_teacher_id_type,
                        course_row.completion_student_token
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_FAVORITE_ATTRIBUTION_COMPLETION_MISMATCH';
                END IF;

                SELECT *
                INTO referenced_observation
                FROM public.course_favorite_observations
                WHERE source_region = attribution_row.source_region
                  AND source_appoint_id = attribution_row.source_appoint_id
                  AND observation_revision =
                        attribution_row.observation_revision;

                IF attribution_row.status = 'AWARDED' THEN
                    IF referenced_observation.status IS DISTINCT FROM
                            'CONFIRMED_TRUE' THEN
                        RAISE EXCEPTION
                            'DTS_V2_FAVORITE_AWARD_REQUIRES_CONFIRMED_TRUE';
                    END IF;
                    SELECT *
                    INTO selected_observation
                    FROM public.course_favorite_observations AS candidate
                    WHERE candidate.source_region = attribution_row.source_region
                      AND candidate.teacher_id = attribution_row.teacher_id
                      AND candidate.teacher_id_type =
                            attribution_row.teacher_id_type
                      AND candidate.student_token =
                            attribution_row.student_token
                      AND candidate.status = 'CONFIRMED_TRUE'
                    ORDER BY
                        candidate.observed_at ASC,
                        CASE candidate.appoint_id_type
                            WHEN 'NUMERIC' THEN 0 ELSE 1
                        END ASC,
                        candidate.appoint_id_numeric ASC NULLS LAST,
                        candidate.appoint_id_text_sort ASC NULLS LAST,
                        candidate.observation_revision ASC
                    LIMIT 1;
                    IF NOT FOUND
                       OR ROW(
                            selected_observation.source_appoint_id,
                            selected_observation.observation_revision
                       ) IS DISTINCT FROM ROW(
                            attribution_row.source_appoint_id,
                            attribution_row.observation_revision
                       ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_FAVORITE_ATTRIBUTION_NOT_CANONICAL_FIRST';
                    END IF;
                ELSE
                    IF referenced_observation.status IN (
                        'PENDING', 'EVALUATING', 'RETRY', 'DEAD'
                    ) THEN
                        IF attribution_row.hold_reason IS DISTINCT FROM
                                'REVALIDATION_PENDING' THEN
                            RAISE EXCEPTION
                                'DTS_V2_FAVORITE_HOLD_REASON_MISMATCH';
                        END IF;
                    ELSIF referenced_observation.status = 'WAITING_HISTORY' THEN
                        IF attribution_row.hold_reason IS DISTINCT FROM
                                'WAITING_HISTORY' THEN
                            RAISE EXCEPTION
                                'DTS_V2_FAVORITE_HOLD_REASON_MISMATCH';
                        END IF;
                    ELSIF referenced_observation.status = 'WAITING_EVIDENCE' THEN
                        IF attribution_row.hold_reason IS DISTINCT FROM
                                'WAITING_EVIDENCE' THEN
                            RAISE EXCEPTION
                                'DTS_V2_FAVORITE_HOLD_REASON_MISMATCH';
                        END IF;
                    ELSE
                        RAISE EXCEPTION
                            'DTS_V2_FAVORITE_HELD_OBSERVATION_STATUS_INVALID';
                    END IF;
                END IF;
            END LOOP;

            FOR attribution_row IN
                SELECT *
                FROM public.course_favorite_attributions
                WHERE source_region = guard_region
                  AND source_appoint_id = guard_appoint_id
                  AND status = 'REVERSED'
            LOOP
                SELECT *
                INTO referenced_observation
                FROM public.course_favorite_observations
                WHERE source_region = attribution_row.source_region
                  AND source_appoint_id = attribution_row.source_appoint_id
                  AND observation_revision =
                        attribution_row.observation_revision;
                IF referenced_observation.status = 'CONFIRMED_TRUE' THEN
                    RAISE EXCEPTION
                        'DTS_V2_FAVORITE_REVERSAL_WITHOUT_INVALIDATION';
                END IF;
            END LOOP;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_favorite_course_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            PERFORM public.dts_v2_assert_favorite_course(
                COALESCE(NEW.source_region, OLD.source_region),
                COALESCE(NEW.source_appoint_id, OLD.source_appoint_id)
            );
            RETURN NULL;
        END
        $function$;

        CREATE TRIGGER trg_favorite_observation_lifecycle
        BEFORE UPDATE OR DELETE ON public.course_favorite_observations
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_observation_lifecycle_guard();

        CREATE TRIGGER trg_favorite_attribution_lifecycle
        BEFORE UPDATE OR DELETE ON public.course_favorite_attributions
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_attribution_lifecycle_guard();

        CREATE CONSTRAINT TRIGGER ct_favorite_observation_course_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.course_favorite_observations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_course_guard();

        CREATE CONSTRAINT TRIGGER ct_favorite_attribution_course_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.course_favorite_attributions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_course_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_favorite_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.source_courses
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_course_guard();

        CREATE CONSTRAINT TRIGGER ct_source_participation_favorite_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.source_course_participations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_favorite_course_guard();
        """
    )


def _lock_schema_from_runtime() -> None:
    op.execute(
        """
        COMMENT ON TABLE public.teacher_student_relationship_events IS
            'DTS v2 shadow: append-only typed relationship history; no runtime writer is active';
        COMMENT ON TABLE public.teacher_student_relationship_current IS
            'DTS v2 shadow: course-independent relationship current state';
        COMMENT ON TABLE public.course_favorite_observations IS
            'DTS v2 shadow: authoritative completion end plus 24-hour observation';
        COMMENT ON TABLE public.course_favorite_attributions IS
            'DTS v2 shadow: one lifetime current favorite award per regional teacher/student pair';

        REVOKE ALL PRIVILEGES ON TABLE
            public.teacher_student_relationship_events,
            public.teacher_student_relationship_current,
            public.course_favorite_observations,
            public.course_favorite_attributions
        FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON SEQUENCE
            public.teacher_student_relationship_events_event_sequence_seq
        FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_typed_id_valid(text, text),
            public.dts_v2_relationship_event_immutable_guard(),
            public.dts_v2_relationship_current_lifecycle_guard(),
            public.dts_v2_relationship_event_provenance_guard(),
            public.dts_v2_assert_relationship_current(text, text, text, text),
            public.dts_v2_relationship_current_pointer_guard(),
            public.dts_v2_favorite_observation_lifecycle_guard(),
            public.dts_v2_favorite_attribution_lifecycle_guard(),
            public.dts_v2_assert_favorite_course(text, text),
            public.dts_v2_favorite_course_guard()
        FROM PUBLIC;

        DO $favorite_schema_acl$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_growth_app',
                'tit_dts_ingest_runtime',
                'tit_teacher_crud'
            ] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.teacher_student_relationship_events, '
                        'public.teacher_student_relationship_current, '
                        'public.course_favorite_observations, '
                        'public.course_favorite_attributions FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON SEQUENCE '
                        'public.teacher_student_relationship_events_event_sequence_seq '
                        'FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON FUNCTION '
                        'public.dts_v2_typed_id_valid(text, text), '
                        'public.dts_v2_relationship_event_immutable_guard(), '
                        'public.dts_v2_relationship_current_lifecycle_guard(), '
                        'public.dts_v2_relationship_event_provenance_guard(), '
                        'public.dts_v2_assert_relationship_current(text, text, text, text), '
                        'public.dts_v2_relationship_current_pointer_guard(), '
                        'public.dts_v2_favorite_observation_lifecycle_guard(), '
                        'public.dts_v2_favorite_attribution_lifecycle_guard(), '
                        'public.dts_v2_assert_favorite_course(text, text), '
                        'public.dts_v2_favorite_course_guard() FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $favorite_schema_acl$;
        """
    )


def _assert_upgrade_shape() -> None:
    op.execute(
        """
        DO $favorite_schema_shape$
        BEGIN
            IF to_regclass('public.teacher_student_relationship_events')
                    IS NULL
               OR to_regclass('public.teacher_student_relationship_current')
                    IS NULL
               OR to_regclass('public.course_favorite_observations')
                    IS NULL
               OR to_regclass('public.course_favorite_attributions')
                    IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid =
                        'public.course_favorite_attributions'::regclass
                      AND tgname = 'ct_favorite_attribution_course_guard'
                      AND tgdeferrable AND tginitdeferred
               )
               OR NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid =
                        'public.teacher_student_relationship_events'::regclass
                      AND tgname = 'trg_relationship_event_immutable'
               ) THEN
                RAISE EXCEPTION 'DTS_V2_FAVORITE_SCHEMA_SHAPE_INVALID';
            END IF;
        END
        $favorite_schema_shape$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _assert_upgrade_preconditions()
    _create_support_functions()
    _create_relationship_events()
    _create_relationship_current()
    _create_favorite_observations()
    _create_favorite_attributions()
    _create_relationship_guards()
    _create_favorite_guards()
    _lock_schema_from_runtime()
    _assert_upgrade_shape()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.course_favorite_attributions,
            public.course_favorite_observations,
            public.teacher_student_relationship_current,
            public.teacher_student_relationship_events
        IN ACCESS EXCLUSIVE MODE;

        DO $favorite_schema_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.teacher_student_relationship_events LIMIT 1
            ) OR EXISTS (
                SELECT 1 FROM public.teacher_student_relationship_current LIMIT 1
            ) OR EXISTS (
                SELECT 1 FROM public.course_favorite_observations LIMIT 1
            ) OR EXISTS (
                SELECT 1 FROM public.course_favorite_attributions LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'refusing relationship/favorite schema downgrade: shadow data exists';
            END IF;
        END
        $favorite_schema_downgrade_guard$;

        DROP TRIGGER ct_source_participation_favorite_guard
            ON public.source_course_participations;
        DROP TRIGGER ct_source_course_favorite_guard ON public.source_courses;
        DROP TRIGGER ct_favorite_attribution_course_guard
            ON public.course_favorite_attributions;
        DROP TRIGGER ct_favorite_observation_course_guard
            ON public.course_favorite_observations;
        DROP TRIGGER trg_favorite_attribution_lifecycle
            ON public.course_favorite_attributions;
        DROP TRIGGER trg_favorite_observation_lifecycle
            ON public.course_favorite_observations;

        DROP TRIGGER ct_relationship_current_latest_event
            ON public.teacher_student_relationship_current;
        DROP TRIGGER ct_relationship_event_current_pointer
            ON public.teacher_student_relationship_events;
        DROP TRIGGER ct_relationship_event_provenance
            ON public.teacher_student_relationship_events;
        DROP TRIGGER trg_relationship_current_lifecycle
            ON public.teacher_student_relationship_current;
        DROP TRIGGER trg_relationship_event_immutable
            ON public.teacher_student_relationship_events;

        DROP FUNCTION public.dts_v2_favorite_course_guard();
        DROP FUNCTION public.dts_v2_assert_favorite_course(text, text);
        DROP FUNCTION public.dts_v2_favorite_attribution_lifecycle_guard();
        DROP FUNCTION public.dts_v2_favorite_observation_lifecycle_guard();
        DROP FUNCTION public.dts_v2_relationship_current_pointer_guard();
        DROP FUNCTION public.dts_v2_assert_relationship_current(
            text, text, text, text
        );
        DROP FUNCTION public.dts_v2_relationship_event_provenance_guard();
        DROP FUNCTION public.dts_v2_relationship_current_lifecycle_guard();
        DROP FUNCTION public.dts_v2_relationship_event_immutable_guard();
        """
    )
    op.drop_table("course_favorite_attributions", schema="public")
    op.drop_table("course_favorite_observations", schema="public")
    op.drop_table("teacher_student_relationship_current", schema="public")
    op.drop_table("teacher_student_relationship_events", schema="public")
    op.execute("DROP FUNCTION public.dts_v2_typed_id_valid(text, text)")
