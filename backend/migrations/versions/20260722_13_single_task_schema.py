"""collapse retired task tables into one current task model

Revision ID: 20260722_13_single_tasks
Revises: 20260722_12_task_score
Create Date: 2026-07-22

The retired transport/runtime tables were emptied by revision 10.  This
migration removes those physical shells, promotes the approved template table
to ``task_templates``, and keeps ``task_assignments`` as the only task fact.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_13_single_tasks"
down_revision: Union[str, None] = "20260722_12_task_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)
FIXED_TASK_CODES_SQL = ", ".join(f"'G{number:02d}'" for number in range(1, 11))
SOURCE_MODES_SQL = "'REAL', 'DERIVED_REAL', 'MOCK', 'MOCK_SIMULATION', 'MOCK_PROXY'"
RETIRED_TASK_TABLES = (
    "task_status_callbacks_v2",
    "task_publications_v2",
    "fixed_task_status_events_v2",
    "fixed_task_instances_v2",
    "trigger_policies_v2",
    "task_runtime_events",
    "task_executions",
)


def _validate_upgrade_source(bind: sa.Connection) -> None:
    checks = (*RETIRED_TASK_TABLES, "task_templates")
    counts = {
        table: int(
            bind.execute(sa.text(f'SELECT count(*) FROM public."{table}"')).scalar_one()
        )
        for table in checks
    }
    nonempty = {table: count for table, count in counts.items() if count}
    if nonempty:
        raise RuntimeError(
            f"retired task tables must be empty before physical cleanup: {nonempty}"
        )

    template_count = int(
        bind.exec_driver_sql(
            f"""
                SELECT count(*)
                FROM public.task_output_templates_v2
                WHERE template_id IN ({FIXED_TASK_CODES_SQL})
                  AND template_version = 1
                  AND row_id = template_id || ':v1'
                  AND status = 'PUBLISHED'
                  AND integration_mode = 'INBOUND_STATUS_ONLY'
            """
        ).scalar_one()
    )
    all_template_count = int(
        bind.execute(
            sa.text("SELECT count(*) FROM public.task_output_templates_v2")
        ).scalar_one()
    )
    if template_count != 10 or all_template_count != 10:
        raise RuntimeError(
            "single-task migration requires exactly the approved G01-G10 templates"
        )

    invalid_assignment_count = int(
        bind.execute(
            sa.text(
                f"""
                SELECT count(*)
                FROM public.task_assignments
                WHERE task_code NOT IN ({FIXED_TASK_CODES_SQL})
                   OR task_kind <> 'FIXED_GROWTH'
                   OR creator_system <> 'TEACHER_APP'
                   OR trigger_evaluation_id IS NOT NULL
                   OR result_code IS NOT NULL
                """
            )
        ).scalar_one()
    )
    if invalid_assignment_count:
        raise RuntimeError(
            "single-task migration found assignments outside the approved current model"
        )


def _drop_versioned_guards(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS trg_task_assignment_audit_v1
            ON public.task_assignments;
        DROP TRIGGER IF EXISTS trg_task_assignment_reject_delete_v1
            ON public.task_assignments;
        DROP TRIGGER IF EXISTS trg_task_assignment_write_v1
            ON public.task_assignments;
        DROP FUNCTION IF EXISTS public.audit_task_assignment_write_v1();
        DROP FUNCTION IF EXISTS public.reject_task_assignment_delete_v1();
        DROP FUNCTION IF EXISTS public.enforce_task_assignment_write_v1();
        """
    )


def _rename_template_table_to_current(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        """
        ALTER TABLE public.task_output_templates_v2 RENAME TO task_templates;
        ALTER TABLE public.task_templates
            RENAME CONSTRAINT task_output_templates_v2_pkey TO task_templates_pkey;
        ALTER TABLE public.task_templates
            RENAME CONSTRAINT uq_task_output_template_v2_version TO uq_task_template_version;
        ALTER TABLE public.task_templates
            RENAME CONSTRAINT ck_task_output_template_v2_status TO ck_task_template_status;
        ALTER TABLE public.task_templates
            RENAME CONSTRAINT ck_task_output_template_v2_execution_owner
            TO ck_task_template_execution_owner;
        ALTER TABLE public.task_templates
            RENAME CONSTRAINT ck_task_output_template_v2_integration_mode
            TO ck_task_template_integration_mode;
        ALTER INDEX public.ix_task_output_template_v2_status
            RENAME TO ix_task_template_status;
        ALTER INDEX public.ix_task_output_templates_v2_template_id
            RENAME TO ix_task_templates_template_id;
        ALTER INDEX public.ix_task_output_templates_v2_external_task_template_code
            RENAME TO ix_task_templates_external_task_template_code;
        """
    )


def _create_current_guards(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION public.enforce_task_assignment_write()
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

        CREATE TRIGGER trg_task_assignment_write
        BEFORE INSERT OR UPDATE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write();

        CREATE FUNCTION public.reject_task_assignment_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'task assignment %% cannot be deleted', OLD.assignment_id
                USING ERRCODE = '42501';
        END
        $function$;

        CREATE TRIGGER trg_task_assignment_reject_delete
        BEFORE DELETE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.reject_task_assignment_delete();

        CREATE FUNCTION public.audit_task_assignment_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
            audit_payload jsonb;
            internal_payload jsonb;
            audit_event_id text;
            internal_event_id text;
        BEGIN
            audit_payload := jsonb_build_object(
                'schema_version', 'shared_task_assignment_audit',
                'assignment_id', NEW.assignment_id,
                'operation', TG_OP,
                'db_actor', actor_name,
                'updated_by', NEW.updated_by,
                'before', CASE
                    WHEN TG_OP = 'UPDATE' THEN jsonb_build_object(
                        'status', OLD.status,
                        'status_reason_code', OLD.status_reason_code,
                        'completed_at', OLD.completed_at,
                        'row_version', OLD.row_version
                    )
                    ELSE NULL
                END,
                'after', jsonb_build_object(
                    'status', NEW.status,
                    'status_reason_code', NEW.status_reason_code,
                    'completed_at', NEW.completed_at,
                    'row_version', NEW.row_version
                )
            );
            audit_event_id := 'TA-AUD-' || encode(
                sha256(convert_to(
                    NEW.assignment_id || ':' || NEW.row_version::text || ':audit',
                    'UTF8'
                )),
                'hex'
            );

            INSERT INTO public.audit_events (
                event_id, event_type, teacher_id, task_id, case_id,
                occurred_at, actor_type, payload_hash, payload
            ) VALUES (
                audit_event_id,
                CASE
                    WHEN TG_OP = 'INSERT' THEN 'task.assignment.created.shared'
                    ELSE 'task.assignment.updated.shared'
                END,
                NEW.teacher_id,
                NEW.assignment_id,
                NULL,
                clock_timestamp(),
                CASE
                    WHEN actor_name = 'tit_teacher_crud' THEN 'TEACHER_APP'
                    ELSE 'SYSTEM'
                END,
                encode(sha256(convert_to(audit_payload::text, 'UTF8')), 'hex'),
                audit_payload
            );

            IF TG_OP = 'UPDATE' AND (
                NEW.status IS DISTINCT FROM OLD.status
                OR NEW.status_reason_code IS DISTINCT FROM OLD.status_reason_code
                OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
            ) THEN
                internal_payload := jsonb_build_object(
                    'schema_version', 'task_assignment_changed.shared',
                    'assignment_id', NEW.assignment_id,
                    'teacher_id', NEW.teacher_id,
                    'task_code', NEW.task_code,
                    'task_kind', NEW.task_kind,
                    'from_status', OLD.status,
                    'to_status', NEW.status,
                    'status_reason_code', NEW.status_reason_code,
                    'completed_at', NEW.completed_at,
                    'row_version', NEW.row_version
                );
                internal_event_id := 'TA-OUT-' || encode(
                    sha256(convert_to(
                        NEW.assignment_id || ':' || NEW.row_version::text || ':outbox',
                        'UTF8'
                    )),
                    'hex'
                );
                INSERT INTO public.outbox_events (
                    outbox_id, event_id, aggregate_type, aggregate_id,
                    event_type, payload, status, available_at, attempt_count,
                    last_error, created_at, published_at
                ) VALUES (
                    internal_event_id,
                    internal_event_id,
                    'TASK_ASSIGNMENT',
                    NEW.assignment_id,
                    'task.assignment_changed.shared',
                    internal_payload,
                    'PENDING',
                    clock_timestamp(),
                    0,
                    NULL,
                    clock_timestamp(),
                    NULL
                );
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE TRIGGER trg_task_assignment_audit
        AFTER INSERT OR UPDATE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.audit_task_assignment_write();

        REVOKE ALL ON FUNCTION public.enforce_task_assignment_write() FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.reject_task_assignment_delete() FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.audit_task_assignment_write() FROM PUBLIC;
        """
    )


def _restrict_teacher_role(bind: sa.Connection, *, template_table: str) -> None:
    update_columns = (
        "status, status_reason_code, result_code, status_changed_at, "
        "completed_at, updated_by"
        if template_table == "task_output_templates_v2"
        else "status, status_reason_code, status_changed_at, completed_at, updated_by"
    )
    bind.exec_driver_sql(
        f"""
        DO $permissions$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM tit_teacher_crud';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM tit_teacher_crud';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM tit_teacher_crud';
                EXECUTE 'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.{template_table} TO tit_teacher_crud';
                EXECUTE 'GRANT INSERT (
                    teacher_id, task_code, template_version_id, task_kind,
                    creator_system, priority, why, due_at, timezone_used,
                    timezone_source, timezone_verified_at, source_mode, dedupe_key
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT UPDATE ({update_columns})
                    ON TABLE public.task_assignments TO tit_teacher_crud';
            END IF;
        END
        $permissions$;
        """
    )


def _verify_current_schema(bind: sa.Connection) -> None:
    retired = {
        table: bind.execute(
            sa.text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
        ).scalar_one()
        for table in (*RETIRED_TASK_TABLES, "task_output_templates_v2")
    }
    present = {table: value for table, value in retired.items() if value is not None}
    if present:
        raise RuntimeError(f"retired task tables still exist: {present}")
    for table in ("task_templates", "task_assignments"):
        if bind.execute(
            sa.text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
        ).scalar_one() is None:
            raise RuntimeError(f"current task table is missing: {table}")


def upgrade() -> None:
    bind = op.get_bind()
    _validate_upgrade_source(bind)
    _drop_versioned_guards(bind)

    for table in RETIRED_TASK_TABLES:
        op.drop_table(table)
    op.drop_table("task_templates")

    op.drop_constraint(
        "ck_task_assignment_result_code",
        "task_assignments",
        type_="check",
    )
    op.drop_column("task_assignments", "trigger_evaluation_id")
    op.drop_column("task_assignments", "result_code")

    _rename_template_table_to_current(bind)
    bind.exec_driver_sql(
        """
        UPDATE public.task_templates
        SET payload = jsonb_set(
            payload - 'accepted_callback_statuses',
            '{source_refs}',
            jsonb_build_array('contracts/教师端共享任务表契约.md'),
            true
        )
        """
    )
    _create_current_guards(bind)
    _restrict_teacher_role(bind, template_table="task_templates")
    _verify_current_schema(bind)


def _rename_template_table_to_legacy_name(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        """
        ALTER TABLE public.task_templates RENAME TO task_output_templates_v2;
        ALTER TABLE public.task_output_templates_v2
            RENAME CONSTRAINT task_templates_pkey TO task_output_templates_v2_pkey;
        ALTER TABLE public.task_output_templates_v2
            RENAME CONSTRAINT uq_task_template_version TO uq_task_output_template_v2_version;
        ALTER TABLE public.task_output_templates_v2
            RENAME CONSTRAINT ck_task_template_status TO ck_task_output_template_v2_status;
        ALTER TABLE public.task_output_templates_v2
            RENAME CONSTRAINT ck_task_template_execution_owner
            TO ck_task_output_template_v2_execution_owner;
        ALTER TABLE public.task_output_templates_v2
            RENAME CONSTRAINT ck_task_template_integration_mode
            TO ck_task_output_template_v2_integration_mode;
        ALTER INDEX public.ix_task_template_status
            RENAME TO ix_task_output_template_v2_status;
        ALTER INDEX public.ix_task_templates_template_id
            RENAME TO ix_task_output_templates_v2_template_id;
        ALTER INDEX public.ix_task_templates_external_task_template_code
            RENAME TO ix_task_output_templates_v2_external_task_template_code;
        """
    )


def _create_retired_tables() -> None:
    op.create_table(
        "task_templates",
        sa.Column("row_id", sa.String(length=160), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("publish_status", sa.String(length=24), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("task_category", sa.String(length=48), nullable=False),
        sa.Column("audience", sa.String(length=24), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("completion_method", sa.String(length=32), nullable=False),
        sa.Column("verification_mode", sa.String(length=32), nullable=False),
        sa.Column("action_schema", JSON_VALUE, nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "template_id", "template_version", name="uq_task_template_version"
        ),
    )
    op.create_index("ix_task_templates_template_id", "task_templates", ["template_id"])

    op.create_table(
        "trigger_policies_v2",
        sa.Column("row_id", sa.String(length=192), nullable=False),
        sa.Column("trigger_rule_id", sa.String(length=96), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("signal_code", sa.String(length=128), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("source_mode", sa.String(length=24), nullable=False),
        sa.Column("manual_gate", sa.Boolean(), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT_PENDING_CONFIRMATION', 'PUBLISHED', 'RETIRED')",
            name="ck_trigger_policy_v2_status",
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "trigger_rule_id", "policy_version", name="uq_trigger_policy_v2_version"
        ),
    )
    op.create_index(
        "ix_trigger_policy_v2_match", "trigger_policies_v2", ["status", "signal_code"]
    )
    for column in ("trigger_rule_id", "signal_code", "template_id"):
        op.create_index(f"ix_trigger_policies_v2_{column}", "trigger_policies_v2", [column])

    op.create_table(
        "task_publications_v2",
        sa.Column("publication_id", sa.String(length=128), nullable=False),
        sa.Column("assignment_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("trigger_rule_id", sa.String(length=96), nullable=False),
        sa.Column("trigger_rule_version", sa.Integer(), nullable=False),
        sa.Column("publication_status", sa.String(length=32), nullable=False),
        sa.Column("is_preview", sa.Boolean(), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=True),
        sa.Column("latest_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("trigger_reason_snapshot", JSON_VALUE, nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_status IN ('PREVIEW_ONLY', 'PENDING_DISPATCH', 'RECEIVED', "
            "'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', "
            "'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')",
            name="ck_task_publication_v2_status",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["teachers.teacher_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("publication_id"),
        sa.UniqueConstraint("assignment_id", name="uq_task_publication_v2_assignment"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_publication_v2_idempotency"),
    )
    op.create_index(
        "ix_task_publication_v2_output",
        "task_publications_v2",
        ["publication_status", "teacher_id", "is_preview"],
    )
    for column in (
        "teacher_id",
        "template_id",
        "trigger_rule_id",
        "publication_status",
        "external_task_id",
    ):
        op.create_index(f"ix_task_publications_v2_{column}", "task_publications_v2", [column])

    op.create_table(
        "task_status_callbacks_v2",
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("publication_id", sa.String(length=128), nullable=False),
        sa.Column("assignment_id", sa.String(length=128), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["task_publications_v2.publication_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider_event_id"),
        sa.UniqueConstraint(
            "publication_id", "sequence", name="uq_task_status_callback_v2_sequence"
        ),
    )
    op.create_index(
        "ix_task_status_callback_v2_publication_time",
        "task_status_callbacks_v2",
        ["publication_id", "occurred_at"],
    )
    for column in ("assignment_id", "external_task_id"):
        op.create_index(
            f"ix_task_status_callbacks_v2_{column}", "task_status_callbacks_v2", [column]
        )

    op.create_table(
        "fixed_task_instances_v2",
        sa.Column("fixed_task_instance_id", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("task_code", sa.String(length=64), nullable=False),
        sa.Column("template_row_id", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("latest_status", sa.String(length=32), nullable=False),
        sa.Column("latest_sequence", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_entry_id", sa.String(length=128), nullable=True),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"task_code IN ({FIXED_TASK_CODES_SQL})",
            name="ck_fixed_task_instance_v2_task_code",
        ),
        sa.CheckConstraint(
            "latest_status = 'COMPLETED'", name="ck_fixed_task_instance_v2_status"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["teachers.teacher_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["template_row_id"],
            ["task_output_templates_v2.row_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fixed_task_instance_id"),
        sa.UniqueConstraint(
            "source_system", "external_task_id", name="uq_fixed_task_instance_v2_external"
        ),
    )
    op.create_index(
        "ix_fixed_task_instance_v2_teacher_task",
        "fixed_task_instances_v2",
        ["teacher_id", "task_code", "updated_at"],
    )
    for column in ("teacher_id", "task_code"):
        op.create_index(
            f"ix_fixed_task_instances_v2_{column}", "fixed_task_instances_v2", [column]
        )

    op.create_table(
        "fixed_task_status_events_v2",
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("fixed_task_instance_id", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("task_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("result_code", sa.String(length=128), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'COMPLETED'", name="ck_fixed_task_status_event_v2_status"),
        sa.CheckConstraint(
            "result_code = 'VERIFIED_COMPLETED'",
            name="ck_fixed_task_status_event_v2_result_code",
        ),
        sa.ForeignKeyConstraint(
            ["fixed_task_instance_id"],
            ["fixed_task_instances_v2.fixed_task_instance_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider_event_id"),
        sa.UniqueConstraint(
            "fixed_task_instance_id",
            "sequence",
            name="uq_fixed_task_status_event_v2_sequence",
        ),
    )
    op.create_index(
        "ix_fixed_task_status_event_v2_instance_time",
        "fixed_task_status_events_v2",
        ["fixed_task_instance_id", "occurred_at"],
    )
    for column in ("teacher_id", "task_code"):
        op.create_index(
            f"ix_fixed_task_status_events_v2_{column}",
            "fixed_task_status_events_v2",
            [column],
        )

    op.create_table(
        "task_executions",
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("runtime_status", sa.String(length=32), nullable=False),
        sa.Column("verification_result", sa.String(length=32), nullable=True),
        sa.Column("runtime_sequence", sa.Integer(), nullable=False),
        sa.Column("due_status", sa.String(length=16), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task_assignments.assignment_id"]
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_task_executions_runtime_status", "task_executions", ["runtime_status"])

    op.create_table(
        "task_runtime_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("execution_contract_version", sa.Integer(), nullable=False),
        sa.Column("runtime_sequence", sa.Integer(), nullable=False),
        sa.Column("runtime_event_code", sa.String(length=48), nullable=False),
        sa.Column("runtime_status", sa.String(length=32), nullable=False),
        sa.Column("verification_result", sa.String(length=32), nullable=True),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task_assignments.assignment_id"]
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "task_id", "runtime_sequence", name="uq_task_runtime_sequence"
        ),
    )
    op.create_index("ix_task_runtime_events_task_id", "task_runtime_events", ["task_id"])


def _create_versioned_compatibility_guards(bind: sa.Connection) -> None:
    # Downgrade is a schema rollback for local recovery.  Reuse the same state
    # machine while restoring revision-12 names and the two nullable columns.
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION public.enforce_task_assignment_write_v1()
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
            FROM public.task_output_templates_v2
            WHERE row_id = NEW.template_version_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'task template version %% does not exist', NEW.template_version_id
                    USING ERRCODE = '23503';
            END IF;
            IF template_code <> NEW.task_code OR template_status <> 'PUBLISHED' THEN
                RAISE EXCEPTION 'task and template are inconsistent' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'INSERT' THEN
                NEW.row_version := 1;
                NEW.updated_at := clock_timestamp();
                IF NEW.status <> 'ASSIGNED' THEN
                    RAISE EXCEPTION 'new task assignments must start at ASSIGNED'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status IN ('COMPLETED', 'EXPIRED', 'WAIVED', 'CANCELLED') THEN
                RAISE EXCEPTION 'terminal task assignment is immutable' USING ERRCODE = '23514';
            END IF;
            IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.task_code IS DISTINCT FROM OLD.task_code
               OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
               OR NEW.task_kind IS DISTINCT FROM OLD.task_kind
               OR NEW.creator_system IS DISTINCT FROM OLD.creator_system
               OR NEW.trigger_evaluation_id IS DISTINCT FROM OLD.trigger_evaluation_id
               OR NEW.why IS DISTINCT FROM OLD.why
               OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
               OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key THEN
                RAISE EXCEPTION 'immutable task fields cannot change' USING ERRCODE = '23514';
            END IF;
            IF NEW.row_version <> OLD.row_version THEN
                RAISE EXCEPTION 'stale row_version' USING ERRCODE = '40001';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NEW.status_changed_at IS NULL
                   OR NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at
                   OR NEW.status_changed_at < OLD.status_changed_at THEN
                    RAISE EXCEPTION 'status change requires a monotonic status_changed_at'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status = 'COMPLETED' THEN
                    NEW.completed_at := COALESCE(NEW.completed_at, NEW.status_changed_at);
                END IF;
            ELSIF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
                RAISE EXCEPTION 'status_changed_at cannot change without status'
                    USING ERRCODE = '23514';
            END IF;
            NEW.row_version := OLD.row_version + 1;
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END
        $function$;
        CREATE TRIGGER trg_task_assignment_write_v1
        BEFORE INSERT OR UPDATE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write_v1();

        CREATE FUNCTION public.reject_task_assignment_delete_v1()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'task assignment cannot be deleted' USING ERRCODE = '42501';
        END
        $function$;
        CREATE TRIGGER trg_task_assignment_reject_delete_v1
        BEFORE DELETE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.reject_task_assignment_delete_v1();

        CREATE FUNCTION public.audit_task_assignment_write_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            event_identifier text;
            event_payload jsonb;
        BEGIN
            event_payload := jsonb_build_object(
                'schema_version', 'shared_task_assignment_audit.v1',
                'assignment_id', NEW.assignment_id,
                'operation', TG_OP,
                'after', jsonb_build_object(
                    'status', NEW.status,
                    'status_reason_code', NEW.status_reason_code,
                    'result_code', NEW.result_code,
                    'completed_at', NEW.completed_at,
                    'row_version', NEW.row_version
                )
            );
            event_identifier := 'TA-AUD-' || encode(
                sha256(convert_to(
                    NEW.assignment_id || ':' || NEW.row_version::text || ':audit', 'UTF8'
                )), 'hex'
            );
            INSERT INTO public.audit_events (
                event_id, event_type, teacher_id, task_id, case_id,
                occurred_at, actor_type, payload_hash, payload
            ) VALUES (
                event_identifier,
                CASE WHEN TG_OP = 'INSERT'
                    THEN 'task.assignment.created.shared.v1'
                    ELSE 'task.assignment.updated.shared.v1' END,
                NEW.teacher_id, NEW.assignment_id, NULL, clock_timestamp(), 'SYSTEM',
                encode(sha256(convert_to(event_payload::text, 'UTF8')), 'hex'),
                event_payload
            ) ON CONFLICT (event_id) DO NOTHING;
            RETURN NULL;
        END
        $function$;
        CREATE TRIGGER trg_task_assignment_audit_v1
        AFTER INSERT OR UPDATE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.audit_task_assignment_write_v1();

        REVOKE ALL ON FUNCTION public.enforce_task_assignment_write_v1() FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.reject_task_assignment_delete_v1() FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.audit_task_assignment_write_v1() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS trg_task_assignment_audit ON public.task_assignments;
        DROP TRIGGER IF EXISTS trg_task_assignment_reject_delete ON public.task_assignments;
        DROP TRIGGER IF EXISTS trg_task_assignment_write ON public.task_assignments;
        DROP FUNCTION IF EXISTS public.audit_task_assignment_write();
        DROP FUNCTION IF EXISTS public.reject_task_assignment_delete();
        DROP FUNCTION IF EXISTS public.enforce_task_assignment_write();
        """
    )
    bind.exec_driver_sql(
        f"""
        UPDATE public.task_templates
        SET payload = jsonb_set(
            payload,
            '{{accepted_callback_statuses}}',
            '["COMPLETED"]'::jsonb,
            true
        )
        WHERE template_id IN ({FIXED_TASK_CODES_SQL})
        """
    )

    _rename_template_table_to_legacy_name(bind)
    op.add_column(
        "task_assignments",
        sa.Column("trigger_evaluation_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "task_assignments",
        sa.Column("result_code", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_task_assignment_result_code",
        "task_assignments",
        "result_code IS NULL",
    )
    _create_retired_tables()
    _create_versioned_compatibility_guards(bind)
    _restrict_teacher_role(bind, template_table="task_output_templates_v2")
