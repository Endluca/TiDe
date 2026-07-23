"""add real lesson evidence and deterministic personalized-trigger facts

Revision ID: 20260722_14_lesson_evidence
Revises: 20260722_13_single_tasks
Create Date: 2026-07-22

The source workbook remains immutable evidence in ``source_records``.  The
lesson, complaint-rule and trigger-match tables are queryable projections of
that evidence.  This revision also repairs the teacher database role: it may
update task status columns, never task content such as ``priority``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_14_lesson_evidence"
down_revision: Union[str, None] = "20260722_13_single_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)
FIXED_TASK_CODES_SQL = ", ".join(f"'G{number:02d}'" for number in range(1, 11))

TASK_ASSIGNMENT_COLUMNS = (
    "assignment_id, teacher_id, task_code, template_version_id, task_kind, "
    "creator_system, status, priority, why, display_title, evidence_snapshot, "
    "due_at, timezone_used, timezone_source, timezone_verified_at, "
    "status_reason_code, source_mode, dedupe_key, created_by, updated_by, "
    "row_version, assigned_at, status_changed_at, completed_at, created_at, updated_at"
)


def _create_source_tables() -> None:
    op.create_table(
        "source_records",
        sa.Column("source_record_id", sa.String(length=160), nullable=False),
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("source_sheet", sa.String(length=128), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("business_key", sa.String(length=256), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=True),
        sa.Column("lesson_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["data_import_batches.batch_id"],
            name="fk_source_record_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source_record_id", name="source_records_pkey"),
        sa.UniqueConstraint(
            "batch_id",
            "source_sheet",
            "source_row_number",
            name="uq_source_record_batch_sheet_row",
        ),
    )
    op.create_index("ix_source_records_batch_id", "source_records", ["batch_id"])
    op.create_index("ix_source_records_teacher_id", "source_records", ["teacher_id"])
    op.create_index("ix_source_records_lesson_id", "source_records", ["lesson_id"])
    op.create_index("ix_source_records_occurred_at", "source_records", ["occurred_at"])
    op.create_index("ix_source_records_row_sha256", "source_records", ["row_sha256"])
    op.create_index(
        "ix_source_record_business_key",
        "source_records",
        ["batch_id", "business_key"],
    )
    op.create_index(
        "ix_source_record_teacher_time",
        "source_records",
        ["teacher_id", "occurred_at"],
    )

    op.create_table(
        "complaint_category_rules",
        sa.Column("rule_id", sa.String(length=160), nullable=False),
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("source_sheet", sa.String(length=128), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("category_l1", sa.String(length=255), nullable=True),
        sa.Column("category_l2", sa.String(length=255), nullable=True),
        sa.Column("category_l3", sa.String(length=500), nullable=False),
        sa.Column("category_l3_normalized", sa.String(length=500), nullable=False),
        sa.Column("source_level", sa.String(length=32), nullable=False),
        sa.Column("normalized_level", sa.String(length=2), nullable=False),
        sa.Column("severity_rank", sa.Integer(), nullable=False),
        sa.Column("default_route", sa.String(length=32), nullable=False),
        sa.Column("learning_title", sa.String(length=500), nullable=True),
        sa.Column("learning_url", sa.Text(), nullable=True),
        sa.Column("raw_payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "normalized_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
            name="ck_complaint_rule_level",
        ),
        sa.CheckConstraint(
            "severity_rank BETWEEN 0 AND 4",
            name="ck_complaint_rule_rank",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["data_import_batches.batch_id"],
            name="fk_complaint_rule_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rule_id", name="complaint_category_rules_pkey"),
        sa.UniqueConstraint(
            "batch_id",
            "category_l3_normalized",
            name="uq_complaint_rule_batch_l3",
        ),
    )
    op.create_index(
        "ix_complaint_category_rules_batch_id",
        "complaint_category_rules",
        ["batch_id"],
    )
    op.create_index(
        "ix_complaint_rule_l3_current",
        "complaint_category_rules",
        ["category_l3_normalized", "batch_id"],
    )


def _extend_lesson_facts() -> None:
    columns = (
        sa.Column("lesson_local_date", sa.Date(), nullable=True),
        sa.Column("lesson_local_time", sa.Time(), nullable=True),
        sa.Column("student_id_hash", sa.String(length=64), nullable=True),
        sa.Column("is_late", sa.Boolean(), nullable=True),
        sa.Column("is_early", sa.Boolean(), nullable=True),
        sa.Column("is_false_early_leave", sa.Boolean(), nullable=True),
        sa.Column("negative_score", sa.Float(), nullable=True),
        sa.Column("has_negative_feedback_tag", sa.Boolean(), nullable=True),
        sa.Column("negative_tag_value", sa.String(length=255), nullable=True),
        sa.Column("absence_reason_detail", sa.String(length=512), nullable=True),
        sa.Column("complaint_category_l1", sa.String(length=255), nullable=True),
        sa.Column("complaint_category_l2", sa.String(length=255), nullable=True),
        sa.Column("complaint_category_l3", sa.String(length=500), nullable=True),
        sa.Column("complaint_source_level", sa.String(length=32), nullable=True),
        sa.Column("complaint_level_rank", sa.Integer(), nullable=True),
        sa.Column("complaint_route", sa.String(length=32), nullable=True),
        sa.Column("complaint_rule_id", sa.String(length=160), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=True),
        sa.Column("is_favorited", sa.Boolean(), nullable=True),
        sa.Column("has_positive_feedback_tag", sa.Boolean(), nullable=True),
        sa.Column("positive_tag_value", sa.String(length=255), nullable=True),
        sa.Column("is_rebooked", sa.Boolean(), nullable=True),
        sa.Column("is_camera_off", sa.Boolean(), nullable=True),
        sa.Column("is_cpu_usage_high", sa.Boolean(), nullable=True),
        sa.Column("is_network_delay_high", sa.Boolean(), nullable=True),
        sa.Column("source_batch_id", sa.String(length=96), nullable=True),
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
    )
    for column in columns:
        op.add_column("lesson_facts", column)

    op.create_foreign_key(
        "fk_lesson_fact_complaint_rule",
        "lesson_facts",
        "complaint_category_rules",
        ["complaint_rule_id"],
        ["rule_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_lesson_fact_source_batch",
        "lesson_facts",
        "data_import_batches",
        ["source_batch_id"],
        ["batch_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_lesson_fact_source_record",
        "lesson_facts",
        "source_records",
        ["source_record_id"],
        ["source_record_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_lesson_fact_source_record",
        "lesson_facts",
        ["source_record_id"],
    )
    op.create_check_constraint(
        "ck_lesson_fact_complaint_rank",
        "lesson_facts",
        "complaint_level_rank IS NULL OR complaint_level_rank BETWEEN 0 AND 4",
    )
    op.create_index(
        "ix_lesson_facts_student_id_hash",
        "lesson_facts",
        ["student_id_hash"],
    )
    op.create_index(
        "ix_lesson_facts_complaint_rule_id",
        "lesson_facts",
        ["complaint_rule_id"],
    )
    op.create_index(
        "ix_lesson_facts_source_batch_id",
        "lesson_facts",
        ["source_batch_id"],
    )
    op.create_index(
        "ix_lesson_fact_teacher_local_date",
        "lesson_facts",
        ["teacher_id", "lesson_local_date"],
    )
    op.create_index(
        "ix_lesson_fact_negative_tag",
        "lesson_facts",
        ["teacher_id", "negative_tag_value"],
    )
    op.create_index(
        "ix_lesson_fact_complaint_l3",
        "lesson_facts",
        ["complaint_category_l3"],
    )


def _create_trigger_match_table() -> None:
    op.create_table(
        "personalized_trigger_matches",
        sa.Column("trigger_match_id", sa.String(length=160), nullable=False),
        sa.Column("trigger_code", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("lesson_id", sa.String(length=128), nullable=True),
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
        sa.Column("complaint_rule_id", sa.String(length=160), nullable=True),
        sa.Column("scope_key", sa.String(length=256), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("output_title", sa.String(length=500), nullable=False),
        sa.Column("output_id", sa.String(length=160), nullable=True),
        sa.Column("match_status", sa.String(length=24), nullable=False),
        sa.Column("evidence_snapshot", JSON_VALUE, nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "output_type IN ('TEACHER_TASK', 'OPS_CASE', 'NOTIFICATION', 'PENDING_DATA')",
            name="ck_personalized_trigger_match_output_type",
        ),
        sa.CheckConstraint(
            "match_status IN ('MATCHED', 'MATERIALIZED', 'SUPPRESSED', 'FAILED', "
            "'PENDING_DATA')",
            name="ck_personalized_trigger_match_status",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.teacher_id"],
            name="fk_personalized_trigger_match_teacher",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["lesson_facts.lesson_id"],
            name="fk_personalized_trigger_match_lesson",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.source_record_id"],
            name="fk_personalized_trigger_match_source_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complaint_rule_id"],
            ["complaint_category_rules.rule_id"],
            name="fk_personalized_trigger_match_complaint_rule",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "trigger_match_id",
            name="personalized_trigger_matches_pkey",
        ),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_personalized_trigger_match_dedupe",
        ),
    )
    op.create_index(
        "ix_personalized_trigger_matches_trigger_code",
        "personalized_trigger_matches",
        ["trigger_code"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_teacher_id",
        "personalized_trigger_matches",
        ["teacher_id"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_lesson_id",
        "personalized_trigger_matches",
        ["lesson_id"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_source_record_id",
        "personalized_trigger_matches",
        ["source_record_id"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_complaint_rule_id",
        "personalized_trigger_matches",
        ["complaint_rule_id"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_output_id",
        "personalized_trigger_matches",
        ["output_id"],
    )
    op.create_index(
        "ix_personalized_trigger_matches_matched_at",
        "personalized_trigger_matches",
        ["matched_at"],
    )
    op.create_index(
        "ix_personalized_trigger_match_ops",
        "personalized_trigger_matches",
        ["match_status", "output_type", "matched_at"],
    )
    op.create_index(
        "ix_personalized_trigger_match_teacher",
        "personalized_trigger_matches",
        ["teacher_id", "matched_at"],
    )


def _protect_assignment_content(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        f"""
        CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            template_code text;
            template_status text;
            template_integration_mode text;
        BEGIN
            SELECT template_id, status, integration_mode
            INTO template_code, template_status, template_integration_mode
            FROM public.task_templates
            WHERE row_id = NEW.template_version_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'task template version %% does not exist', NEW.template_version_id
                    USING ERRCODE = '23503';
            END IF;
            IF template_code <> NEW.task_code OR template_status <> 'PUBLISHED' THEN
                RAISE EXCEPTION 'task %%, template %%, and published status are inconsistent',
                    NEW.task_code, NEW.template_version_id
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.task_kind = 'FIXED_GROWTH'
               AND template_integration_mode <> 'INBOUND_STATUS_ONLY' THEN
                RAISE EXCEPTION 'fixed task %% must use an inbound-only template', NEW.task_code
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.task_kind = 'PERSONALIZED_IMPROVEMENT'
               AND template_integration_mode <> 'OUTBOUND_MANAGED' THEN
                RAISE EXCEPTION 'personalized task %% must use a trigger-center template', NEW.task_code
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'INSERT' THEN
                NEW.row_version := 1;
                NEW.updated_at := clock_timestamp();
                IF NEW.status <> 'ASSIGNED' THEN
                    RAISE EXCEPTION 'new task assignments must start at ASSIGNED'
                        USING ERRCODE = '23514';
                END IF;
                IF current_user = 'tit_teacher_crud' THEN
                    IF NEW.task_kind <> 'FIXED_GROWTH'
                       OR NEW.creator_system <> 'TEACHER_APP'
                       OR NEW.task_code NOT IN ({FIXED_TASK_CODES_SQL}) THEN
                        RAISE EXCEPTION 'teacher app may create G01-G10 fixed tasks only'
                            USING ERRCODE = '42501';
                    END IF;
                    NEW.created_by := current_user;
                    NEW.updated_by := current_user;
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status IN ('COMPLETED', 'EXPIRED', 'WAIVED', 'CANCELLED') THEN
                RAISE EXCEPTION 'terminal task assignment %% is immutable', OLD.assignment_id
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.task_code IS DISTINCT FROM OLD.task_code
               OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
               OR NEW.task_kind IS DISTINCT FROM OLD.task_kind
               OR NEW.creator_system IS DISTINCT FROM OLD.creator_system
               OR NEW.why IS DISTINCT FROM OLD.why
               OR (to_jsonb(NEW) -> 'display_title') IS DISTINCT FROM
                  (to_jsonb(OLD) -> 'display_title')
               OR (to_jsonb(NEW) -> 'evidence_snapshot') IS DISTINCT FROM
                  (to_jsonb(OLD) -> 'evidence_snapshot')
               OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
               OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'immutable task assignment identity/content fields cannot change'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.row_version <> OLD.row_version THEN
                RAISE EXCEPTION 'stale or caller-modified row_version for assignment %%', OLD.assignment_id
                    USING ERRCODE = '40001';
            END IF;

            IF current_user = 'tit_teacher_crud' THEN
                IF NEW.status IN ('WAIVED', 'CANCELLED')
                   AND NEW.status IS DISTINCT FROM OLD.status THEN
                    RAISE EXCEPTION 'teacher app cannot waive or cancel assignments'
                        USING ERRCODE = '42501';
                END IF;
                NEW.updated_by := current_user;
            END IF;

            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NEW.status_changed_at IS NULL
                   OR NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at
                   OR NEW.status_changed_at < OLD.status_changed_at THEN
                    RAISE EXCEPTION 'status change requires a monotonic status_changed_at'
                        USING ERRCODE = '23514';
                END IF;

                IF NOT (
                    (OLD.status = 'ASSIGNED' AND NEW.status IN (
                        'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'VIEWED' AND NEW.status IN (
                        'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'IN_PROGRESS' AND NEW.status IN (
                        'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                        'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'SUBMITTED' AND NEW.status IN (
                        'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                        'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'UNDER_REVIEW' AND NEW.status IN (
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'FAILED' AND NEW.status IN (
                        'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED'
                    ))
                ) THEN
                    RAISE EXCEPTION 'invalid task status transition: %% -> %%', OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.status = 'COMPLETED' THEN
                    NEW.completed_at := COALESCE(
                        NEW.completed_at,
                        NEW.status_changed_at,
                        clock_timestamp()
                    );
                END IF;
            ELSIF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
                RAISE EXCEPTION 'status_changed_at cannot change without a status transition'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.completed_at IS NOT NULL
               AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                RAISE EXCEPTION 'completed_at is immutable once set'
                    USING ERRCODE = '23514';
            END IF;

            NEW.row_version := OLD.row_version + 1;
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.enforce_task_assignment_write() FROM PUBLIC;
        """
    )


def _repair_teacher_role(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        f"""
        DO $permissions$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                -- Remove both table-level grants and any historical column grants.
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_templates FROM tit_teacher_crud';
                EXECUTE 'REVOKE SELECT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE INSERT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE UPDATE ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE REFERENCES ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';

                EXECUTE 'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_templates TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT INSERT (
                    teacher_id, task_code, template_version_id, task_kind,
                    creator_system, priority, why, due_at, timezone_used,
                    timezone_source, timezone_verified_at, source_mode, dedupe_key
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT UPDATE (
                    status, status_reason_code, status_changed_at,
                    completed_at, updated_by
                ) ON TABLE public.task_assignments TO tit_teacher_crud';

                IF has_table_privilege(
                    'tit_teacher_crud', 'public.task_assignments', 'UPDATE'
                ) OR has_column_privilege(
                    'tit_teacher_crud', 'public.task_assignments', 'priority', 'UPDATE'
                ) OR has_table_privilege(
                    'tit_teacher_crud', 'public.task_templates', 'UPDATE'
                ) THEN
                    RAISE EXCEPTION
                        'tit_teacher_crud still has a task-content UPDATE privilege';
                END IF;
            END IF;
        END
        $permissions$;
        """
    )


def upgrade() -> None:
    op.add_column(
        "data_import_batches",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default="TEACHER_SNAPSHOT",
        ),
    )
    op.add_column(
        "data_import_batches",
        sa.Column(
            "sync_mode",
            sa.String(length=24),
            nullable=False,
            server_default="MANUAL_BASELINE",
        ),
    )
    op.drop_constraint(
        "uq_teacher_import_content_sheet",
        "data_import_batches",
        type_="unique",
    )
    op.drop_constraint(
        "ck_data_import_batch_mixed",
        "data_import_batches",
        type_="check",
    )
    op.create_unique_constraint(
        "uq_data_import_content_sheet",
        "data_import_batches",
        ["source_sha256", "source_sheet"],
    )
    op.create_check_constraint(
        "ck_data_import_batch_sync_mode",
        "data_import_batches",
        "sync_mode IN ('MANUAL_BASELINE', 'API_DAILY')",
    )
    op.create_check_constraint(
        "ck_data_import_batch_data_mode",
        "data_import_batches",
        "data_mode IN ('REAL', 'MIXED')",
    )
    op.alter_column("data_import_batches", "source_kind", server_default=None)
    op.alter_column("data_import_batches", "sync_mode", server_default=None)

    _create_source_tables()
    _extend_lesson_facts()
    _create_trigger_match_table()

    op.add_column(
        "task_assignments",
        sa.Column("display_title", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_assignments",
        sa.Column(
            "evidence_snapshot",
            JSON_VALUE,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.alter_column("notifications", "task_id", existing_type=sa.String(128), nullable=True)
    op.add_column(
        "notifications",
        sa.Column("source_ref", sa.String(length=256), nullable=True),
    )
    op.create_unique_constraint(
        "uq_notification_source_ref",
        "notifications",
        ["source_ref"],
    )
    op.create_check_constraint(
        "ck_notification_source",
        "notifications",
        "task_id IS NOT NULL OR source_ref IS NOT NULL",
    )

    bind = op.get_bind()
    _protect_assignment_content(bind)
    _repair_teacher_role(bind)


def downgrade() -> None:
    bind = op.get_bind()
    notification_without_task = int(
        bind.execute(
            sa.text("SELECT count(*) FROM notifications WHERE task_id IS NULL")
        ).scalar_one()
    )
    if notification_without_task:
        raise RuntimeError(
            "cannot downgrade while source-only notifications exist: "
            f"{notification_without_task} rows"
        )

    op.drop_constraint("ck_notification_source", "notifications", type_="check")
    op.drop_constraint("uq_notification_source_ref", "notifications", type_="unique")
    op.drop_column("notifications", "source_ref")
    op.alter_column("notifications", "task_id", existing_type=sa.String(128), nullable=False)

    op.drop_column("task_assignments", "evidence_snapshot")
    op.drop_column("task_assignments", "display_title")

    for index_name in (
        "ix_personalized_trigger_match_teacher",
        "ix_personalized_trigger_match_ops",
        "ix_personalized_trigger_matches_matched_at",
        "ix_personalized_trigger_matches_output_id",
        "ix_personalized_trigger_matches_complaint_rule_id",
        "ix_personalized_trigger_matches_source_record_id",
        "ix_personalized_trigger_matches_lesson_id",
        "ix_personalized_trigger_matches_teacher_id",
        "ix_personalized_trigger_matches_trigger_code",
    ):
        op.drop_index(index_name, table_name="personalized_trigger_matches")
    op.drop_table("personalized_trigger_matches")

    for index_name in (
        "ix_lesson_fact_complaint_l3",
        "ix_lesson_fact_negative_tag",
        "ix_lesson_fact_teacher_local_date",
        "ix_lesson_facts_source_batch_id",
        "ix_lesson_facts_complaint_rule_id",
        "ix_lesson_facts_student_id_hash",
    ):
        op.drop_index(index_name, table_name="lesson_facts")
    op.drop_constraint("ck_lesson_fact_complaint_rank", "lesson_facts", type_="check")
    op.drop_constraint("uq_lesson_fact_source_record", "lesson_facts", type_="unique")
    op.drop_constraint("fk_lesson_fact_source_record", "lesson_facts", type_="foreignkey")
    op.drop_constraint("fk_lesson_fact_source_batch", "lesson_facts", type_="foreignkey")
    op.drop_constraint("fk_lesson_fact_complaint_rule", "lesson_facts", type_="foreignkey")
    for column_name in (
        "source_record_id",
        "source_batch_id",
        "is_network_delay_high",
        "is_cpu_usage_high",
        "is_camera_off",
        "is_rebooked",
        "positive_tag_value",
        "has_positive_feedback_tag",
        "is_favorited",
        "is_blocked",
        "complaint_rule_id",
        "complaint_route",
        "complaint_level_rank",
        "complaint_source_level",
        "complaint_category_l3",
        "complaint_category_l2",
        "complaint_category_l1",
        "absence_reason_detail",
        "negative_tag_value",
        "has_negative_feedback_tag",
        "negative_score",
        "is_false_early_leave",
        "is_early",
        "is_late",
        "student_id_hash",
        "lesson_local_time",
        "lesson_local_date",
    ):
        op.drop_column("lesson_facts", column_name)

    op.drop_index("ix_complaint_rule_l3_current", table_name="complaint_category_rules")
    op.drop_index("ix_complaint_category_rules_batch_id", table_name="complaint_category_rules")
    op.drop_table("complaint_category_rules")

    for index_name in (
        "ix_source_record_teacher_time",
        "ix_source_record_business_key",
        "ix_source_records_row_sha256",
        "ix_source_records_occurred_at",
        "ix_source_records_lesson_id",
        "ix_source_records_teacher_id",
        "ix_source_records_batch_id",
    ):
        op.drop_index(index_name, table_name="source_records")
    op.drop_table("source_records")

    op.drop_constraint(
        "ck_data_import_batch_data_mode",
        "data_import_batches",
        type_="check",
    )
    op.drop_constraint(
        "ck_data_import_batch_sync_mode",
        "data_import_batches",
        type_="check",
    )
    op.drop_constraint(
        "uq_data_import_content_sheet",
        "data_import_batches",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_data_import_batch_mixed",
        "data_import_batches",
        "data_mode = 'MIXED'",
    )
    op.create_unique_constraint(
        "uq_teacher_import_content_sheet",
        "data_import_batches",
        ["source_sha256", "source_sheet"],
    )
    op.drop_column("data_import_batches", "sync_mode")
    op.drop_column("data_import_batches", "source_kind")

    # Keep the repaired least-privilege policy after a schema downgrade.
    bind.exec_driver_sql(
        """
        DO $permissions$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_templates FROM tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_templates TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT INSERT (
                    teacher_id, task_code, template_version_id, task_kind,
                    creator_system, priority, why, due_at, timezone_used,
                    timezone_source, timezone_verified_at, source_mode, dedupe_key
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT UPDATE (
                    status, status_reason_code, status_changed_at,
                    completed_at, updated_by
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
            END IF;
        END
        $permissions$;
        """
    )
