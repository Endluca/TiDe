"""initialize the mandatory-task baseline when a teacher is created

Revision ID: 20260723_18_fixed_baseline
Revises: 20260723_17_notification_indexes
Create Date: 2026-07-23

Every teacher in the growth system must have exactly one assignment for each
published G01-G10 template.  The trigger center owns creation of that baseline;
the teacher application can only read the shared rows and update their
execution status.

The catalog check intentionally fails closed.  A teacher row is not accepted
when the published mandatory catalog is partial, duplicated, or otherwise
inconsistent.  Baseline creation writes task and audit facts only; it does not
create notifications or other outbound-delivery facts.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260723_18_fixed_baseline"
down_revision: Union[str, None] = "20260723_17_notification_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FIXED_TASK_CODES_SQL = ", ".join(f"'G{number:02d}'" for number in range(1, 11))
TASK_ASSIGNMENT_COLUMNS = (
    "assignment_id, teacher_id, task_code, template_version_id, task_kind, "
    "creator_system, status, priority, why, display_title, evidence_snapshot, "
    "due_at, timezone_used, timezone_source, timezone_verified_at, "
    "status_reason_code, source_mode, dedupe_key, created_by, updated_by, "
    "row_version, assigned_at, status_changed_at, completed_at, created_at, updated_at"
)


def _validate_catalog() -> None:
    op.execute(
        f"""
        DO $catalog$
        DECLARE
            published_fixed_count integer;
        BEGIN
            SELECT count(*)
            INTO published_fixed_count
            FROM public.task_templates
            WHERE status = 'PUBLISHED'
              AND payload->>'category' = 'MANDATORY_GROWTH';

            IF published_fixed_count <> 10
               OR EXISTS (
                    SELECT expected.task_code
                    FROM (
                        VALUES
                            ('G01'), ('G02'), ('G03'), ('G04'), ('G05'),
                            ('G06'), ('G07'), ('G08'), ('G09'), ('G10')
                    ) AS expected(task_code)
                    LEFT JOIN public.task_templates AS template
                      ON template.template_id = expected.task_code
                     AND template.status = 'PUBLISHED'
                    WHERE template.row_id IS NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.task_templates
                    WHERE template_id IN ({FIXED_TASK_CODES_SQL})
                      AND status = 'PUBLISHED'
                      AND (
                          output_type <> 'TEACHER_TASK'
                          OR execution_owner <> 'TEACHER_APP'
                          OR integration_mode <> 'INBOUND_STATUS_ONLY'
                          OR source_mode <> 'REAL'
                          OR payload->>'category'
                             IS DISTINCT FROM 'MANDATORY_GROWTH'
                          OR payload->>'priority' NOT IN ('P0', 'P1', 'P2', 'P3')
                          OR nullif(btrim(payload->>'why_template'), '') IS NULL
                      )
               ) THEN
                RAISE EXCEPTION
                    'fixed-task baseline requires exactly one valid published REAL template for each G01-G10'
                    USING ERRCODE = '23514';
            END IF;
        END
        $catalog$;
        """
    )


def _transfer_fixed_assignment_ownership() -> None:
    op.drop_constraint(
        "ck_task_assignment_owner_consistency",
        "task_assignments",
        type_="check",
    )
    op.execute(
        """
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write
        """
    )
    try:
        op.execute(
            """
            UPDATE public.task_assignments
            SET creator_system = 'TRIGGER_CENTER',
                updated_by = 'SYSTEM_MIGRATION_20260723_18',
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE task_kind = 'FIXED_GROWTH'
              AND creator_system <> 'TRIGGER_CENTER'
            """
        )
    finally:
        op.execute(
            """
            ALTER TABLE public.task_assignments
                ENABLE TRIGGER trg_task_assignment_write
            """
        )
    op.create_check_constraint(
        "ck_task_assignment_owner_consistency",
        "task_assignments",
        (
            f"((task_code IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'FIXED_GROWTH' "
            "AND creator_system = 'TRIGGER_CENTER') OR "
            f"(task_code NOT IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
            "AND creator_system = 'TRIGGER_CENTER'))"
        ),
    )


def _create_baseline_functions_and_trigger() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.ensure_fixed_growth_assignments(
            requested_teacher_id text
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            published_fixed_count integer;
            inserted_count integer;
            teacher_fixed_count integer;
        BEGIN
            IF requested_teacher_id IS NULL
               OR nullif(btrim(requested_teacher_id), '') IS NULL THEN
                RAISE EXCEPTION 'teacher_id is required for fixed-task initialization'
                    USING ERRCODE = '23502';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.teachers
                WHERE teacher_id = requested_teacher_id
            ) THEN
                RAISE EXCEPTION 'teacher % does not exist', requested_teacher_id
                    USING ERRCODE = '23503';
            END IF;

            SELECT count(*)
            INTO published_fixed_count
            FROM public.task_templates
            WHERE status = 'PUBLISHED'
              AND payload->>'category' = 'MANDATORY_GROWTH';

            IF published_fixed_count <> 10
               OR EXISTS (
                    SELECT expected.task_code
                    FROM (
                        VALUES
                            ('G01'), ('G02'), ('G03'), ('G04'), ('G05'),
                            ('G06'), ('G07'), ('G08'), ('G09'), ('G10')
                    ) AS expected(task_code)
                    LEFT JOIN public.task_templates AS template
                      ON template.template_id = expected.task_code
                     AND template.status = 'PUBLISHED'
                    WHERE template.row_id IS NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.task_templates
                    WHERE template_id IN ({FIXED_TASK_CODES_SQL})
                      AND status = 'PUBLISHED'
                      AND (
                          output_type <> 'TEACHER_TASK'
                          OR execution_owner <> 'TEACHER_APP'
                          OR integration_mode <> 'INBOUND_STATUS_ONLY'
                          OR source_mode <> 'REAL'
                          OR payload->>'category'
                             IS DISTINCT FROM 'MANDATORY_GROWTH'
                          OR payload->>'priority' NOT IN ('P0', 'P1', 'P2', 'P3')
                          OR nullif(btrim(payload->>'why_template'), '') IS NULL
                      )
               ) THEN
                RAISE EXCEPTION
                    'fixed-task baseline requires exactly one valid published REAL template for each G01-G10'
                    USING ERRCODE = '23514';
            END IF;

            WITH inserted AS (
                INSERT INTO public.task_assignments (
                    teacher_id,
                    task_code,
                    template_version_id,
                    task_kind,
                    creator_system,
                    status,
                    priority,
                    why,
                    display_title,
                    evidence_snapshot,
                    due_at,
                    timezone_used,
                    timezone_source,
                    timezone_verified_at,
                    status_reason_code,
                    source_mode,
                    dedupe_key,
                    created_by,
                    updated_by
                )
                SELECT
                    requested_teacher_id,
                    template.template_id,
                    template.row_id,
                    'FIXED_GROWTH',
                    'TRIGGER_CENTER',
                    'ASSIGNED',
                    template.payload->>'priority',
                    template.payload->>'why_template',
                    NULL,
                    jsonb_build_object(
                        'trigger_event',
                        'NEW_TEACHER_CREATED'
                    ),
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    'REAL',
                    'fixed:' || requested_teacher_id || ':' || template.template_id,
                    'SYSTEM_TEACHER_BASELINE',
                    'SYSTEM_TEACHER_BASELINE'
                FROM public.task_templates AS template
                WHERE template.template_id IN ({FIXED_TASK_CODES_SQL})
                  AND template.status = 'PUBLISHED'
                ORDER BY template.template_id
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING 1
            )
            SELECT count(*) INTO inserted_count FROM inserted;

            SELECT count(*)
            INTO teacher_fixed_count
            FROM public.task_assignments
            WHERE teacher_id = requested_teacher_id
              AND task_code IN ({FIXED_TASK_CODES_SQL})
              AND task_kind = 'FIXED_GROWTH'
              AND creator_system = 'TRIGGER_CENTER'
              AND source_mode = 'REAL';

            IF teacher_fixed_count <> 10 THEN
                RAISE EXCEPTION
                    'teacher % fixed-task baseline is incomplete: %/10',
                    requested_teacher_id,
                    teacher_fixed_count
                    USING ERRCODE = '23514';
            END IF;

            RETURN inserted_count;
        END
        $function$;

        CREATE FUNCTION public.initialize_fixed_growth_assignments_on_teacher()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            PERFORM public.ensure_fixed_growth_assignments(NEW.teacher_id);
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER trg_teacher_initialize_fixed_growth
        AFTER INSERT ON public.teachers
        FOR EACH ROW
        EXECUTE FUNCTION public.initialize_fixed_growth_assignments_on_teacher();

        REVOKE ALL ON FUNCTION public.ensure_fixed_growth_assignments(text)
            FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.initialize_fixed_growth_assignments_on_teacher()
            FROM PUBLIC;
        """
    )


def _backfill_current_teachers() -> None:
    op.execute(
        """
        DO $backfill$
        DECLARE
            teacher_row record;
        BEGIN
            FOR teacher_row IN
                SELECT teacher_id
                FROM public.teachers
                ORDER BY teacher_id
            LOOP
                PERFORM public.ensure_fixed_growth_assignments(
                    teacher_row.teacher_id
                );
            END LOOP;
        END
        $backfill$;
        """
    )
    op.execute(
        f"""
        DO $verify$
        DECLARE
            incomplete_teacher record;
        BEGIN
            SELECT teacher.teacher_id,
                   count(assignment.assignment_id) AS fixed_count
            INTO incomplete_teacher
            FROM public.teachers AS teacher
            LEFT JOIN public.task_assignments AS assignment
              ON assignment.teacher_id = teacher.teacher_id
             AND assignment.task_code IN ({FIXED_TASK_CODES_SQL})
             AND assignment.task_kind = 'FIXED_GROWTH'
             AND assignment.creator_system = 'TRIGGER_CENTER'
             AND assignment.source_mode = 'REAL'
            GROUP BY teacher.teacher_id
            HAVING count(assignment.assignment_id) <> 10
            LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'fixed-task baseline backfill is incomplete for teacher %: %/10',
                    incomplete_teacher.teacher_id,
                    incomplete_teacher.fixed_count
                    USING ERRCODE = '23514';
            END IF;
        END
        $verify$;
        """
    )


def _restrict_teacher_role() -> None:
    op.execute(
        f"""
        DO $permissions$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'tit_teacher_crud'
            ) THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE SELECT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE INSERT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE UPDATE ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE REFERENCES ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';

                EXECUTE 'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_templates TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT UPDATE (
                    status,
                    status_reason_code,
                    status_changed_at,
                    completed_at,
                    updated_by
                ) ON TABLE public.task_assignments TO tit_teacher_crud';

                IF has_table_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'INSERT'
                ) OR has_any_column_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'INSERT'
                ) OR has_table_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'DELETE'
                ) OR has_table_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'UPDATE'
                ) OR has_column_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'priority',
                    'UPDATE'
                ) THEN
                    RAISE EXCEPTION
                        'tit_teacher_crud retains task creation, deletion, or content-write privilege';
                END IF;
            END IF;
        END
        $permissions$;
        """
    )


def upgrade() -> None:
    _validate_catalog()
    _transfer_fixed_assignment_ownership()
    _create_baseline_functions_and_trigger()
    _backfill_current_teachers()
    _restrict_teacher_role()


def _restore_teacher_role() -> None:
    op.execute(
        f"""
        DO $permissions$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'tit_teacher_crud'
            ) THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE SELECT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE INSERT ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE UPDATE ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';
                EXECUTE 'REVOKE REFERENCES ({TASK_ASSIGNMENT_COLUMNS}) ON TABLE public.task_assignments FROM tit_teacher_crud';

                EXECUTE 'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_templates TO tit_teacher_crud';
                EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT INSERT (
                    teacher_id,
                    task_code,
                    template_version_id,
                    task_kind,
                    creator_system,
                    priority,
                    why,
                    due_at,
                    timezone_used,
                    timezone_source,
                    timezone_verified_at,
                    source_mode,
                    dedupe_key
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
                EXECUTE 'GRANT UPDATE (
                    status,
                    status_reason_code,
                    status_changed_at,
                    completed_at,
                    updated_by
                ) ON TABLE public.task_assignments TO tit_teacher_crud';
            END IF;
        END
        $permissions$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_teacher_initialize_fixed_growth
            ON public.teachers;
        DROP FUNCTION IF EXISTS
            public.initialize_fixed_growth_assignments_on_teacher();
        DROP FUNCTION IF EXISTS
            public.ensure_fixed_growth_assignments(text);
        """
    )
    op.drop_constraint(
        "ck_task_assignment_owner_consistency",
        "task_assignments",
        type_="check",
    )
    op.execute(
        """
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write
        """
    )
    try:
        op.execute(
            """
            UPDATE public.task_assignments
            SET creator_system = 'TEACHER_APP',
                updated_by = 'SYSTEM_MIGRATION_DOWNGRADE',
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE task_kind = 'FIXED_GROWTH'
              AND creator_system <> 'TEACHER_APP'
            """
        )
    finally:
        op.execute(
            """
            ALTER TABLE public.task_assignments
                ENABLE TRIGGER trg_task_assignment_write
            """
        )
    op.create_check_constraint(
        "ck_task_assignment_owner_consistency",
        "task_assignments",
        (
            f"((task_code IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'FIXED_GROWTH' "
            "AND creator_system = 'TEACHER_APP') OR "
            f"(task_code NOT IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
            "AND creator_system = 'TRIGGER_CENTER'))"
        ),
    )
    _restore_teacher_role()
