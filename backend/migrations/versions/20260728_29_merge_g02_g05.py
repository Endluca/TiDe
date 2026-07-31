"""merge G02 lesson preparation and device/network check

Revision ID: 20260728_29_merge_g02_g05
Revises: 20260728_28_teacher_absent
Create Date: 2026-07-28

G02 becomes the single six-point task. G05 is retired but its assignment and
ledger rows remain as historical facts. The merged G02 is completed only when
both legacy assignments were completed.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260728_29_merge_g02_g05"
down_revision: Union[str, None] = "20260728_28_teacher_absent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_CODES = (
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
CURRENT_CODES_SQL = ", ".join(f"'{code}'" for code in CURRENT_CODES)
LEGACY_CODES = tuple(f"G{number:02d}" for number in range(1, 11))
LEGACY_CODES_SQL = ", ".join(f"'{code}'" for code in LEGACY_CODES)
MERGED_TITLE = "Lesson Preparation&Device Network Check"
MERGED_WHY = (
    "Lesson preparation and a verified device and network check are both "
    "required before your first lesson and no later than Day 7."
)
MERGED_HOW = (
    "Complete the lesson-preparation checklist, then test your computer, "
    "network, microphone, speaker and camera in the trusted device-check entry."
)
MERGED_COMPLETION = (
    "Return COMPLETED only after every preparation item is confirmed and the "
    "trusted device and network check result is PASS."
)
MERGED_BENEFIT = (
    "Earn 6 mandatory-growth points once and complete both preparation and "
    "device-readiness requirements."
)


def _replace_baseline_function(codes_sql: str, expected_count: int) -> None:
    op.execute(
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
                RAISE EXCEPTION 'teacher % does not exist', requested_teacher_id
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
                    'teacher % fixed-task baseline is incomplete: %/{expected_count}',
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


def upgrade() -> None:
    op.execute(
        f"""
        DO $catalog$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.task_templates
                WHERE row_id = 'G02:v1'
                  AND template_id = 'G02'
                  AND status = 'PUBLISHED'
            ) OR NOT EXISTS (
                SELECT 1 FROM public.task_templates
                WHERE row_id = 'G05:v1'
                  AND template_id = 'G05'
                  AND status = 'PUBLISHED'
            ) THEN
                RAISE EXCEPTION
                    'G02/G05 merge requires the published v1 legacy templates'
                    USING ERRCODE = '23514';
            END IF;
        END
        $catalog$;

        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write;

        WITH legacy AS (
            SELECT
                g02.assignment_id AS g02_assignment_id,
                g02.status AS g02_status,
                g02.status_reason_code AS g02_reason,
                g02.completed_at AS g02_completed_at,
                g02.status_changed_at AS g02_changed_at,
                g02.why AS g02_why,
                g05.status AS g05_status,
                g05.completed_at AS g05_completed_at
            FROM public.task_assignments AS g02
            JOIN public.task_assignments AS g05
              ON g05.teacher_id = g02.teacher_id
             AND g05.task_code = 'G05'
             AND g05.task_kind = 'FIXED_GROWTH'
            WHERE g02.task_code = 'G02'
              AND g02.task_kind = 'FIXED_GROWTH'
        )
        UPDATE public.task_assignments AS assignment
        SET
            status = CASE
                WHEN legacy.g02_status = 'COMPLETED'
                 AND legacy.g05_status = 'COMPLETED'
                THEN 'COMPLETED'
                WHEN legacy.g02_status IN (
                    'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED'
                ) OR legacy.g05_status IN (
                    'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED'
                )
                THEN 'IN_PROGRESS'
                ELSE 'ASSIGNED'
            END,
            status_reason_code = NULL,
            completed_at = CASE
                WHEN legacy.g02_status = 'COMPLETED'
                 AND legacy.g05_status = 'COMPLETED'
                THEN COALESCE(
                    GREATEST(
                        legacy.g02_completed_at,
                        legacy.g05_completed_at
                    ),
                    legacy.g02_completed_at,
                    legacy.g05_completed_at
                )
                ELSE NULL
            END,
            status_changed_at = clock_timestamp(),
            why = '{MERGED_WHY}',
            priority = 'P1',
            evidence_snapshot = COALESCE(
                assignment.evidence_snapshot,
                '{{}}'::jsonb
            ) || jsonb_build_object(
                'catalog_merge_g02_g05',
                jsonb_build_object(
                    'legacy_g02_status', legacy.g02_status,
                    'legacy_g05_status', legacy.g05_status,
                    'legacy_g02_reason', legacy.g02_reason,
                    'legacy_g02_completed_at', legacy.g02_completed_at,
                    'legacy_g02_status_changed_at', legacy.g02_changed_at,
                    'legacy_g02_why', legacy.g02_why
                )
            ),
            updated_by = 'SYSTEM_MIGRATION_20260728_29',
            row_version = assignment.row_version + 1,
            updated_at = clock_timestamp()
        FROM legacy
        WHERE assignment.assignment_id = legacy.g02_assignment_id;

        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_write;

        UPDATE public.task_templates
        SET
            revision = revision + 1,
            external_task_template_code = 'TIT.G02',
            payload = payload || jsonb_build_object(
                'ops_name_zh', '首课备课与设备网络检测',
                'title', '{MERGED_TITLE}',
                'why_template', '{MERGED_WHY}',
                'how_summary', '{MERGED_HOW}',
                'completion_standard', '{MERGED_COMPLETION}',
                'benefit', '{MERGED_BENEFIT}',
                'score_type', 'FIXED',
                'score_value', 6,
                'stage', 'DAY_1_7',
                'priority', 'P1',
                'due_rule', jsonb_build_object(
                    'type', 'CAMP_DAY_OR_EVENT_DEADLINE',
                    'camp_day', 7,
                    'event', 'BEFORE_FIRST_LESSON',
                    'fallback_hours', 168
                ),
                'external_task_template_code', 'TIT.G02'
            ),
            updated_by = 'SYSTEM_MIGRATION_20260728_29',
            updated_at = clock_timestamp()
        WHERE row_id = 'G02:v1';

        UPDATE public.task_templates
        SET
            status = 'RETIRED',
            revision = revision + 1,
            payload = payload || jsonb_build_object(
                'status', 'RETIRED',
                'retired_reason', 'MERGED_INTO_G02'
            ),
            updated_by = 'SYSTEM_MIGRATION_20260728_29',
            updated_at = clock_timestamp()
        WHERE row_id = 'G05:v1';

        UPDATE public.score_entries
        SET
            delta_score = 6,
            reason_code = 'FIXED_GROWTH_COMPLETED:G02',
            payload = COALESCE(payload, '{{}}'::jsonb)
                || jsonb_build_object(
                    'score_value', 6,
                    'catalog_merge', 'G02_G05'
                )
        WHERE entry_type = 'FIXED_TASK_AWARD'
          AND task_assignment_id IN (
              SELECT assignment_id
              FROM public.task_assignments
              WHERE task_code = 'G02'
                AND task_kind = 'FIXED_GROWTH'
          );
        """
    )
    _replace_baseline_function(CURRENT_CODES_SQL, 9)


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.task_assignments
            DISABLE TRIGGER trg_task_assignment_write;

        UPDATE public.task_assignments
        SET
            status = COALESCE(
                evidence_snapshot #>> '{catalog_merge_g02_g05,legacy_g02_status}',
                status
            ),
            status_reason_code = (
                evidence_snapshot #>> '{catalog_merge_g02_g05,legacy_g02_reason}'
            ),
            completed_at = CASE
                WHEN evidence_snapshot
                    #>> '{catalog_merge_g02_g05,legacy_g02_completed_at}' IS NULL
                THEN NULL
                ELSE (
                    evidence_snapshot
                    #>> '{catalog_merge_g02_g05,legacy_g02_completed_at}'
                )::timestamptz
            END,
            status_changed_at = COALESCE(
                (
                    evidence_snapshot
                    #>> '{catalog_merge_g02_g05,legacy_g02_status_changed_at}'
                )::timestamptz,
                status_changed_at
            ),
            why = COALESCE(
                evidence_snapshot #>> '{catalog_merge_g02_g05,legacy_g02_why}',
                why
            ),
            evidence_snapshot = evidence_snapshot - 'catalog_merge_g02_g05',
            updated_by = 'SYSTEM_MIGRATION_20260728_29_DOWN',
            row_version = row_version + 1,
            updated_at = clock_timestamp()
        WHERE task_code = 'G02'
          AND task_kind = 'FIXED_GROWTH';

        ALTER TABLE public.task_assignments
            ENABLE TRIGGER trg_task_assignment_write;

        UPDATE public.task_templates
        SET
            revision = revision - 1,
            payload = payload || jsonb_build_object(
                'ops_name_zh', '设备与网络检测',
                'title', 'Device & Network Check',
                'why_template',
                    'A verified device and network check is required before your first lesson and no later than Day 7.',
                'how_summary',
                    'Open the trusted device-check entry, test your computer, network, microphone, speaker and camera, then follow any repair guidance.',
                'completion_standard',
                    'Return COMPLETED only after the trusted check result is PASS. An approved exception must return WAIVED, not COMPLETED.',
                'benefit',
                    'Earn 3 mandatory-growth points once and complete the device component of first-lesson readiness.',
                'score_type', 'FIXED',
                'score_value', 3
            ),
            updated_by = 'SYSTEM_MIGRATION_20260728_29_DOWN',
            updated_at = clock_timestamp()
        WHERE row_id = 'G02:v1';

        UPDATE public.task_templates
        SET
            status = 'PUBLISHED',
            revision = revision - 1,
            payload = (payload - 'retired_reason')
                || jsonb_build_object('status', 'PUBLISHED'),
            updated_by = 'SYSTEM_MIGRATION_20260728_29_DOWN',
            updated_at = clock_timestamp()
        WHERE row_id = 'G05:v1';

        UPDATE public.score_entries
        SET
            delta_score = 3,
            payload = (COALESCE(payload, '{}'::jsonb) - 'catalog_merge')
                || jsonb_build_object('score_value', 3)
        WHERE entry_type = 'FIXED_TASK_AWARD'
          AND task_assignment_id IN (
              SELECT assignment_id
              FROM public.task_assignments
              WHERE task_code = 'G02'
                AND task_kind = 'FIXED_GROWTH'
          );
        """
    )
    _replace_baseline_function(LEGACY_CODES_SQL, 10)
    op.execute(
        """
        DO $backfill$
        DECLARE teacher_row record;
        BEGIN
            FOR teacher_row IN
                SELECT teacher_id FROM public.teachers ORDER BY teacher_id
            LOOP
                PERFORM public.ensure_fixed_growth_assignments(
                    teacher_row.teacher_id
                );
            END LOOP;
        END
        $backfill$;
        """
    )
