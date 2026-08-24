"""add the inert source-course and teacher-participation shadow tables

Revision ID: 20260822_67_course_part
Revises: 20260822_66_dts_v2_shadow
Create Date: 2026-08-22

The two tables are intentionally not connected to a runtime writer or read
route in this revision.  Their local keys, source-version identity foreign
key, pointer foreign keys, and one-current/one-completion uniqueness are
enforced.  Semantic source-version matching, reverse pointers, immutable
completion fields, and lesson-score ownership still require deferred
activation guards; this shadow schema must not be treated as serving.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_67_course_part"
down_revision: Union[str, None] = "20260822_66_dts_v2_shadow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SHADOW_SCHEMA_ONLY = True
DEFERRED_BIDIRECTIONAL_GUARD_IMPLEMENTED = False
DEFERRED_SEMANTIC_GUARDS_IMPLEMENTED = False


def _create_source_courses() -> None:
    op.create_table(
        "source_courses",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("student_token", sa.String(length=128), nullable=True),
        sa.Column("lesson_local_date", sa.Date(), nullable=True),
        sa.Column("lesson_local_time", sa.Time(), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_end_time", sa.Text(), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_status", sa.Text(), nullable=True),
        sa.Column("current_teacher_id", sa.String(length=64), nullable=True),
        sa.Column(
            "current_teacher_id_type", sa.String(length=16), nullable=True
        ),
        sa.Column("current_participation_seq", sa.Integer(), nullable=True),
        sa.Column("is_peak", sa.Boolean(), nullable=True),
        sa.Column("completion_participation_seq", sa.Integer(), nullable=True),
        sa.Column("completion_teacher_id", sa.String(length=64), nullable=True),
        sa.Column(
            "completion_teacher_id_type", sa.String(length=16), nullable=True
        ),
        sa.Column("completion_frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_student_token", sa.String(length=128), nullable=True),
        sa.Column("completion_is_peak", sa.Boolean(), nullable=True),
        sa.Column("completion_lesson_local_date", sa.Date(), nullable=True),
        sa.Column("completion_lesson_local_time", sa.Time(), nullable=True),
        sa.Column("completion_source_position", postgresql.JSONB(), nullable=True),
        sa.Column("completion_source_revision", sa.BigInteger(), nullable=True),
        sa.Column("initial_completion_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("completion_voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completion_conflict_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
        sa.Column(
            "completion_conflict_case_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "conflict_resolved_against_position",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "conflict_resolved_against_revision",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column("conflict_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "source_is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "evidence_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'SOURCE_MISSING'"),
        ),
        sa.Column("last_applied_event_position", postgresql.JSONB(), nullable=True),
        sa.Column("last_applied_source_revision", sa.BigInteger(), nullable=True),
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
            "source_appoint_id",
            name="pk_source_courses",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_source_course_region",
        ),
        sa.CheckConstraint(
            "current_participation_seq IS NULL "
            "OR current_participation_seq >= 1",
            name="ck_source_course_current_seq",
        ),
        sa.CheckConstraint(
            "completion_participation_seq IS NULL "
            "OR completion_participation_seq >= 1",
            name="ck_source_course_completion_seq",
        ),
        sa.CheckConstraint(
            "(current_teacher_id IS NULL "
            "AND current_teacher_id_type IS NULL "
            "AND current_participation_seq IS NULL) "
            "OR (current_teacher_id IS NOT NULL "
            "AND current_teacher_id_type IN ('NUMERIC', 'TEXT') "
            "AND current_participation_seq IS NOT NULL)",
            name="ck_source_course_current_pointer_pair",
        ),
        sa.CheckConstraint(
            "(completion_teacher_id IS NULL "
            "AND completion_teacher_id_type IS NULL "
            "AND completion_participation_seq IS NULL "
            "AND completion_frozen_at IS NULL "
            "AND completion_source_position IS NULL "
            "AND completion_source_revision IS NULL) "
            "OR (completion_teacher_id IS NOT NULL "
            "AND completion_teacher_id_type IN ('NUMERIC', 'TEXT') "
            "AND completion_participation_seq IS NOT NULL "
            "AND completion_frozen_at IS NOT NULL "
            "AND completion_source_position IS NOT NULL "
            "AND completion_source_revision IS NOT NULL "
            "AND completion_source_revision >= 1)",
            name="ck_source_course_completion_pointer_group",
        ),
        sa.CheckConstraint(
            "completion_conflict_status IN ("
            "'NONE', 'PENDING', 'RESOLVED_KEEP', 'RESOLVED_UPDATE', "
            "'RESOLVED_TRANSFER', 'RESOLVED_VOID')",
            name="ck_source_course_conflict_status",
        ),
        sa.CheckConstraint(
            "evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING', 'SOURCE_CONFLICT')",
            name="ck_source_course_evidence_status",
        ),
        sa.CheckConstraint(
            "row_version >= 1 "
            "AND (completion_source_revision IS NULL "
            "OR completion_source_revision >= 1) "
            "AND (conflict_resolved_against_revision IS NULL "
            "OR conflict_resolved_against_revision >= 1) "
            "AND (last_applied_source_revision IS NULL "
            "OR last_applied_source_revision >= 1)",
            name="ck_source_course_revisions",
        ),
        sa.CheckConstraint(
            "student_token IS NULL "
            "OR source_region = 'ovs' "
            "OR student_token ~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_source_course_dom_student_token",
        ),
        sa.CheckConstraint(
            "completion_student_token IS NULL "
            "OR source_region = 'ovs' "
            "OR completion_student_token ~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_source_course_dom_completion_student_token",
        ),
        sa.CheckConstraint(
            "completion_source_position IS NULL "
            "OR jsonb_typeof(completion_source_position) = 'object'",
            name="ck_source_course_completion_position",
        ),
        sa.CheckConstraint(
            "last_applied_event_position IS NULL "
            "OR jsonb_typeof(last_applied_event_position) = 'object'",
            name="ck_source_course_last_applied_position",
        ),
        sa.CheckConstraint(
            "conflict_resolved_against_position IS NULL "
            "OR jsonb_typeof(conflict_resolved_against_position) = 'object'",
            name="ck_source_course_resolved_position",
        ),
        sa.CheckConstraint(
            "initial_completion_snapshot IS NULL "
            "OR jsonb_typeof(initial_completion_snapshot) = 'object'",
            name="ck_source_course_initial_completion_snapshot",
        ),
        sa.CheckConstraint(
            "conflict_fingerprint IS NULL "
            "OR conflict_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_source_course_conflict_fingerprint",
        ),
        schema="public",
    )


def _create_source_course_participations() -> None:
    op.create_table(
        "source_course_participations",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("participation_seq", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id_type", sa.String(length=16), nullable=False),
        sa.Column("participation_status", sa.Text(), nullable=True),
        sa.Column("participation_role", sa.String(length=40), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assigned_at_evidence_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("absence_reason_detail", sa.Text(), nullable=True),
        sa.Column("no_notice", sa.Boolean(), nullable=True),
        sa.Column("source_deleted", sa.Boolean(), nullable=False),
        sa.Column(
            "assignment_source_partition_epoch_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("assignment_event_topic", sa.String(length=512), nullable=False),
        sa.Column("assignment_event_partition", sa.Integer(), nullable=False),
        sa.Column("assignment_event_offset", sa.BigInteger(), nullable=False),
        sa.Column("assignment_source_row_revision", sa.BigInteger(), nullable=False),
        sa.Column("assignment_event_phase", sa.String(length=32), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            "participation_seq",
            name="pk_source_course_participations",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            [
                "public.source_courses.source_region",
                "public.source_courses.source_appoint_id",
            ],
            name="fk_source_course_participation_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "assignment_source_partition_epoch_id",
                "assignment_event_topic",
                "assignment_event_partition",
                "assignment_event_offset",
            ],
            [
                "public.dts_source_row_versions.source_region",
                "public.dts_source_row_versions.source_partition_epoch_id",
                "public.dts_source_row_versions.topic",
                "public.dts_source_row_versions.partition_id",
                "public.dts_source_row_versions.offset_value",
            ],
            name="fk_source_course_participation_source_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
            "teacher_id_type",
            name="uq_source_course_participation_teacher_identity",
        ),
        sa.UniqueConstraint(
            "source_region",
            "assignment_source_partition_epoch_id",
            "assignment_event_topic",
            "assignment_event_partition",
            "assignment_event_offset",
            "assignment_event_phase",
            name="uq_source_course_participation_source_phase",
        ),
        sa.UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "assignment_source_row_revision",
            "assignment_event_phase",
            name="uq_source_course_participation_revision_phase",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_source_course_participation_region",
        ),
        sa.CheckConstraint(
            "teacher_id_type IN ('NUMERIC', 'TEXT')",
            name="ck_source_course_participation_teacher_type",
        ),
        sa.CheckConstraint(
            "participation_seq >= 1 "
            "AND assignment_event_partition >= 0 "
            "AND assignment_event_offset >= 0 "
            "AND assignment_source_row_revision >= 1 "
            "AND row_version >= 1",
            name="ck_source_course_participation_numbers",
        ),
        sa.CheckConstraint(
            "participation_role IN ("
            "'NORMAL', 'COMPLETION', 'PENDING_CORRECTION', "
            "'REJECTED_CORRECTION', 'SUPERSEDED_COMPLETION', "
            "'VOIDED_COMPLETION')",
            name="ck_source_course_participation_role",
        ),
        sa.CheckConstraint(
            "assigned_at_evidence_status IN ('CONFIRMED', 'SOURCE_MISSING')",
            name="ck_source_course_participation_assigned_evidence",
        ),
        sa.CheckConstraint(
            "assignment_event_phase IN ('SNAPSHOT_DIFF', 'BEFORE', 'AFTER')",
            name="ck_source_course_participation_event_phase",
        ),
        sa.CheckConstraint(
            "NOT source_deleted OR NOT is_current",
            name="ck_source_course_participation_deleted_not_current",
        ),
        schema="public",
    )
    op.create_index(
        "uq_source_course_participation_current",
        "source_course_participations",
        ["source_region", "source_appoint_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("is_current IS TRUE"),
    )
    op.create_index(
        "uq_source_course_participation_completion",
        "source_course_participations",
        ["source_region", "source_appoint_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("participation_role = 'COMPLETION'"),
    )
    op.create_index(
        "ix_source_course_participation_teacher_time",
        "source_course_participations",
        [
            "source_region",
            "teacher_id_type",
            "teacher_id",
            "source_appoint_id",
            "participation_seq",
        ],
        unique=False,
        schema="public",
    )

    op.create_foreign_key(
        "fk_source_course_current_participation",
        "source_courses",
        "source_course_participations",
        [
            "source_region",
            "source_appoint_id",
            "current_participation_seq",
            "current_teacher_id",
            "current_teacher_id_type",
        ],
        [
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
            "teacher_id_type",
        ],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_source_course_completion_participation",
        "source_courses",
        "source_course_participations",
        [
            "source_region",
            "source_appoint_id",
            "completion_participation_seq",
            "completion_teacher_id",
            "completion_teacher_id_type",
        ],
        [
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
            "teacher_id_type",
        ],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _lock_shadow_tables_from_runtime() -> None:
    op.execute(
        """
        COMMENT ON TABLE public.source_courses IS
            'DTS v2 shadow only; frozen-field and reverse-pointer guards not activated';
        COMMENT ON TABLE public.source_course_participations IS
            'DTS v2 shadow only; provenance semantic guard not yet activated';

        REVOKE ALL PRIVILEGES ON TABLE
            public.source_courses,
            public.source_course_participations
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $shadow_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.source_courses, '
                    'public.source_course_participations FROM tit_teacher_crud';
            END IF;
        END
        $shadow_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _create_source_courses()
    _create_source_course_participations()
    _lock_shadow_tables_from_runtime()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.source_course_participations,
            public.source_courses
        IN ACCESS EXCLUSIVE MODE;

        DO $course_participation_shadow_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.source_courses LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM public.source_course_participations LIMIT 1
               )
            THEN
                RAISE EXCEPTION
                    'refusing course-participation shadow downgrade: shadow data exists';
            END IF;
        END
        $course_participation_shadow_downgrade_guard$;
        """
    )
    op.drop_constraint(
        "fk_source_course_completion_participation",
        "source_courses",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_source_course_current_participation",
        "source_courses",
        schema="public",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_source_course_participation_teacher_time",
        table_name="source_course_participations",
        schema="public",
    )
    op.drop_index(
        "uq_source_course_participation_completion",
        table_name="source_course_participations",
        schema="public",
    )
    op.drop_index(
        "uq_source_course_participation_current",
        table_name="source_course_participations",
        schema="public",
    )
    op.drop_table("source_course_participations", schema="public")
    op.drop_table("source_courses", schema="public")
