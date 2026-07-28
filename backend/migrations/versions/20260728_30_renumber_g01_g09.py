"""renumber the current mandatory catalog to contiguous G01-G09

Revision ID: 20260728_30_renumber_g01_g09
Revises: 20260728_29_merge_g02_g05
Create Date: 2026-07-28

The merged lesson-preparation and device/network task becomes G04. The other
current tasks keep their business order and shift into a contiguous G01-G09
catalog. The retired pre-merge G05 record is retained as hidden G00 history.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260728_30_renumber_g01_g09"
down_revision: Union[str, None] = "20260728_29_merge_g02_g05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_CODES = tuple(f"G{number:02d}" for number in range(1, 10))
CURRENT_CODES_SQL = ", ".join(f"'{code}'" for code in CURRENT_CODES)
CURRENT_STORAGE_CODES = ("G00", *CURRENT_CODES)
CURRENT_STORAGE_CODES_SQL = ", ".join(
    f"'{code}'" for code in CURRENT_STORAGE_CODES
)
PREVIOUS_CURRENT_CODES = (
    "G01",
    "G02",
    "G03",
    "G04",
    "G06",
    "G07",
    "G08",
    "G09",
    "G10",
)
PREVIOUS_CURRENT_CODES_SQL = ", ".join(
    f"'{code}'" for code in PREVIOUS_CURRENT_CODES
)
PREVIOUS_STORAGE_CODES = tuple(f"G{number:02d}" for number in range(1, 11))
PREVIOUS_STORAGE_CODES_SQL = ", ".join(
    f"'{code}'" for code in PREVIOUS_STORAGE_CODES
)


def _replace_baseline_function(codes_sql: str, expected_count: int) -> None:
    op.get_bind().exec_driver_sql(
        f"""
        CREATE OR REPLACE FUNCTION public.ensure_fixed_growth_assignments(
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
                RAISE EXCEPTION 'teacher %% does not exist', requested_teacher_id
                    USING ERRCODE = '23503';
            END IF;

            SELECT count(*)
            INTO published_fixed_count
            FROM public.task_templates
            WHERE status = 'PUBLISHED'
              AND payload->>'category' = 'MANDATORY_GROWTH';

            IF published_fixed_count <> {expected_count}
               OR EXISTS (
                    SELECT expected.task_code
                    FROM unnest(ARRAY[{codes_sql}]) AS expected(task_code)
                    LEFT JOIN public.task_templates AS template
                      ON template.template_id = expected.task_code
                     AND template.status = 'PUBLISHED'
                    WHERE template.row_id IS NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.task_templates
                    WHERE template_id IN ({codes_sql})
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
                    'fixed-task baseline does not match the current mandatory catalog'
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
                    jsonb_build_object('trigger_event', 'NEW_TEACHER_CREATED'),
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
                WHERE template.template_id IN ({codes_sql})
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
              AND task_code IN ({codes_sql})
              AND task_kind = 'FIXED_GROWTH'
              AND creator_system = 'TRIGGER_CENTER'
              AND source_mode = 'REAL';

            IF teacher_fixed_count <> {expected_count} THEN
                RAISE EXCEPTION
                    'teacher %% fixed-task baseline is incomplete: %%/{expected_count}',
                    requested_teacher_id,
                    teacher_fixed_count
                    USING ERRCODE = '23514';
            END IF;

            RETURN inserted_count;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.ensure_fixed_growth_assignments(text)
            FROM PUBLIC;
        """
    )


def _replace_assignment_constraints(storage_codes_sql: str) -> None:
    op.get_bind().exec_driver_sql(
        f"""
        ALTER TABLE public.task_assignments
            ADD CONSTRAINT ck_task_assignment_owner_consistency CHECK (
                (
                    task_code IN ({storage_codes_sql})
                    AND task_kind = 'FIXED_GROWTH'
                    AND creator_system = 'TRIGGER_CENTER'
                ) OR (
                    task_code NOT IN ({storage_codes_sql})
                    AND task_kind = 'PERSONALIZED_IMPROVEMENT'
                    AND creator_system = 'TRIGGER_CENTER'
                )
            );

        ALTER TABLE public.task_assignments
            ADD CONSTRAINT ck_task_assignment_fixed_dedupe CHECK (
                task_kind <> 'FIXED_GROWTH'
                OR dedupe_key = 'fixed:' || teacher_id || ':' || task_code
            );
        """
    )


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DO $catalog$
        BEGIN
            IF (
                SELECT count(*)
                FROM public.task_templates
                WHERE row_id IN (
                    'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
                    'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
                )
            ) <> 10
            OR (
                SELECT count(*)
                FROM public.task_templates
                WHERE row_id = 'G05:v1'
                  AND status = 'RETIRED'
            ) <> 1
            OR (
                SELECT count(*)
                FROM public.task_templates
                WHERE row_id <> 'G05:v1'
                  AND row_id IN (
                    'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1',
                    'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
                  )
                  AND status = 'PUBLISHED'
            ) <> 9
            THEN
                RAISE EXCEPTION
                    'mandatory catalog is not ready for contiguous renumbering'
                    USING ERRCODE = '23514';
            END IF;
        END
        $catalog$;

        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write;
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_audit;
        ALTER TABLE public.task_assignments
            DROP CONSTRAINT ck_task_assignment_owner_consistency;
        ALTER TABLE public.task_assignments
            DROP CONSTRAINT ck_task_assignment_fixed_dedupe;

        UPDATE public.task_assignments
        SET
            task_code = 'TMP-' || task_code,
            dedupe_key = 'fixed:' || teacher_id || ':TMP-' || task_code
        WHERE task_kind = 'FIXED_GROWTH'
          AND template_version_id IN (
              'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
              'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
          );

        UPDATE public.task_templates
        SET template_id = 'TMP-' || template_id
        WHERE row_id IN (
            'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
            'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
        );

        UPDATE public.task_templates
        SET
            template_id = CASE row_id
                WHEN 'G01:v1' THEN 'G01'
                WHEN 'G02:v1' THEN 'G04'
                WHEN 'G03:v1' THEN 'G02'
                WHEN 'G04:v1' THEN 'G03'
                WHEN 'G05:v1' THEN 'G00'
                WHEN 'G06:v1' THEN 'G05'
                WHEN 'G07:v1' THEN 'G06'
                WHEN 'G08:v1' THEN 'G07'
                WHEN 'G09:v1' THEN 'G08'
                WHEN 'G10:v1' THEN 'G09'
            END,
            external_task_template_code = 'TIT.' || CASE row_id
                WHEN 'G01:v1' THEN 'G01'
                WHEN 'G02:v1' THEN 'G04'
                WHEN 'G03:v1' THEN 'G02'
                WHEN 'G04:v1' THEN 'G03'
                WHEN 'G05:v1' THEN 'G00'
                WHEN 'G06:v1' THEN 'G05'
                WHEN 'G07:v1' THEN 'G06'
                WHEN 'G08:v1' THEN 'G07'
                WHEN 'G09:v1' THEN 'G08'
                WHEN 'G10:v1' THEN 'G09'
            END,
            payload = payload || jsonb_build_object(
                'template_id',
                CASE row_id
                    WHEN 'G01:v1' THEN 'G01'
                    WHEN 'G02:v1' THEN 'G04'
                    WHEN 'G03:v1' THEN 'G02'
                    WHEN 'G04:v1' THEN 'G03'
                    WHEN 'G05:v1' THEN 'G00'
                    WHEN 'G06:v1' THEN 'G05'
                    WHEN 'G07:v1' THEN 'G06'
                    WHEN 'G08:v1' THEN 'G07'
                    WHEN 'G09:v1' THEN 'G08'
                    WHEN 'G10:v1' THEN 'G09'
                END,
                'external_task_template_code',
                'TIT.' || CASE row_id
                    WHEN 'G01:v1' THEN 'G01'
                    WHEN 'G02:v1' THEN 'G04'
                    WHEN 'G03:v1' THEN 'G02'
                    WHEN 'G04:v1' THEN 'G03'
                    WHEN 'G05:v1' THEN 'G00'
                    WHEN 'G06:v1' THEN 'G05'
                    WHEN 'G07:v1' THEN 'G06'
                    WHEN 'G08:v1' THEN 'G07'
                    WHEN 'G09:v1' THEN 'G08'
                    WHEN 'G10:v1' THEN 'G09'
                END,
                'catalog_previous_task_code',
                replace(row_id, ':v1', '')
            ),
            revision = revision + 1,
            updated_by = 'SYSTEM_MIGRATION_20260728_30',
            updated_at = clock_timestamp()
        WHERE row_id IN (
            'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
            'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
        );

        UPDATE public.task_templates
        SET payload = payload || jsonb_build_object(
            'retired_reason', 'LEGACY_PRE_MERGE_G05_ARCHIVE',
            'status', 'RETIRED'
        )
        WHERE row_id = 'G05:v1';

        UPDATE public.task_assignments
        SET
            task_code = CASE template_version_id
                WHEN 'G01:v1' THEN 'G01'
                WHEN 'G02:v1' THEN 'G04'
                WHEN 'G03:v1' THEN 'G02'
                WHEN 'G04:v1' THEN 'G03'
                WHEN 'G05:v1' THEN 'G00'
                WHEN 'G06:v1' THEN 'G05'
                WHEN 'G07:v1' THEN 'G06'
                WHEN 'G08:v1' THEN 'G07'
                WHEN 'G09:v1' THEN 'G08'
                WHEN 'G10:v1' THEN 'G09'
            END,
            dedupe_key = 'fixed:' || teacher_id || ':' ||
                CASE template_version_id
                    WHEN 'G01:v1' THEN 'G01'
                    WHEN 'G02:v1' THEN 'G04'
                    WHEN 'G03:v1' THEN 'G02'
                    WHEN 'G04:v1' THEN 'G03'
                    WHEN 'G05:v1' THEN 'G00'
                    WHEN 'G06:v1' THEN 'G05'
                    WHEN 'G07:v1' THEN 'G06'
                    WHEN 'G08:v1' THEN 'G07'
                    WHEN 'G09:v1' THEN 'G08'
                    WHEN 'G10:v1' THEN 'G09'
                END,
            evidence_snapshot = COALESCE(
                evidence_snapshot,
                '{}'::jsonb
            ) || jsonb_build_object(
                'catalog_renumber_g01_g09',
                jsonb_build_object(
                    'previous_task_code',
                    replace(template_version_id, ':v1', '')
                )
            ),
            updated_by = 'SYSTEM_MIGRATION_20260728_30',
            row_version = row_version + 1,
            updated_at = clock_timestamp()
        WHERE task_kind = 'FIXED_GROWTH'
          AND template_version_id IN (
              'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
              'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
          );

        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_write;
        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_audit;

        UPDATE public.score_entries AS entry
        SET
            reason_code = 'FIXED_GROWTH_COMPLETED:' || assignment.task_code,
            payload = COALESCE(entry.payload, '{}'::jsonb)
                || jsonb_build_object(
                    'task_code', assignment.task_code,
                    'catalog_renumber', 'G01_G09'
                )
        FROM public.task_assignments AS assignment
        WHERE entry.entry_type = 'FIXED_TASK_AWARD'
          AND entry.task_assignment_id = assignment.assignment_id
          AND assignment.task_kind = 'FIXED_GROWTH';
        """
    )
    _replace_assignment_constraints(CURRENT_STORAGE_CODES_SQL)
    _replace_baseline_function(CURRENT_CODES_SQL, 9)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write;
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_audit;
        ALTER TABLE public.task_assignments
            DROP CONSTRAINT ck_task_assignment_owner_consistency;
        ALTER TABLE public.task_assignments
            DROP CONSTRAINT ck_task_assignment_fixed_dedupe;

        UPDATE public.task_assignments
        SET
            task_code = 'TMP-' || task_code,
            dedupe_key = 'fixed:' || teacher_id || ':TMP-' || task_code
        WHERE task_kind = 'FIXED_GROWTH'
          AND template_version_id IN (
              'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
              'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
          );

        UPDATE public.task_templates
        SET template_id = 'TMP-' || template_id
        WHERE row_id IN (
            'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
            'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
        );

        UPDATE public.task_templates
        SET
            template_id = replace(row_id, ':v1', ''),
            external_task_template_code = 'TIT.' || replace(row_id, ':v1', ''),
            payload = (
                payload - 'catalog_previous_task_code'
            ) || jsonb_build_object(
                'template_id', replace(row_id, ':v1', ''),
                'external_task_template_code',
                'TIT.' || replace(row_id, ':v1', '')
            ),
            revision = revision - 1,
            updated_by = 'SYSTEM_MIGRATION_20260728_30_DOWN',
            updated_at = clock_timestamp()
        WHERE row_id IN (
            'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
            'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
        );

        UPDATE public.task_templates
        SET payload = payload || jsonb_build_object(
            'retired_reason', 'MERGED_INTO_G02',
            'status', 'RETIRED'
        )
        WHERE row_id = 'G05:v1';

        UPDATE public.task_assignments
        SET
            task_code = replace(template_version_id, ':v1', ''),
            dedupe_key = 'fixed:' || teacher_id || ':' ||
                replace(template_version_id, ':v1', ''),
            evidence_snapshot = evidence_snapshot - 'catalog_renumber_g01_g09',
            updated_by = 'SYSTEM_MIGRATION_20260728_30_DOWN',
            row_version = row_version + 1,
            updated_at = clock_timestamp()
        WHERE task_kind = 'FIXED_GROWTH'
          AND template_version_id IN (
              'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
              'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
          );

        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_write;
        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_audit;

        UPDATE public.score_entries AS entry
        SET
            reason_code = 'FIXED_GROWTH_COMPLETED:' || assignment.task_code,
            payload = (COALESCE(entry.payload, '{}'::jsonb) - 'catalog_renumber')
                || jsonb_build_object('task_code', assignment.task_code)
        FROM public.task_assignments AS assignment
        WHERE entry.entry_type = 'FIXED_TASK_AWARD'
          AND entry.task_assignment_id = assignment.assignment_id
          AND assignment.task_kind = 'FIXED_GROWTH';
        """
    )
    _replace_assignment_constraints(PREVIOUS_STORAGE_CODES_SQL)
    _replace_baseline_function(PREVIOUS_CURRENT_CODES_SQL, 9)
