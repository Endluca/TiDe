"""align the published G01-G09 points with the current shared contract

Revision ID: 20260729_38_catalog_scores
Revises: 20260729_37_read_perf
Create Date: 2026-07-29

Earlier task-code migrations preserved the legacy per-task point distribution.
The total remained 30, which hid the mismatch from total-only checks.  This
forward normalization changes a published template only when no fixed-task
award or non-zero task score projection exists.  A database that has already
settled the legacy distribution must use the governed score-policy
recalculation path instead of silently rewriting earned facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260729_38_catalog_scores"
down_revision: Union[str, None] = "20260729_37_read_perf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $catalog_alignment$
        DECLARE
            actual_codes text[];
            mismatch_count integer;
            fixed_award_count integer;
            nonzero_task_account_count integer;
            nonzero_task_component_count integer;
        BEGIN
            SELECT array_agg(template_id ORDER BY template_id)
            INTO actual_codes
            FROM public.task_templates
            WHERE status = 'PUBLISHED'
              AND source_mode = 'REAL'
              AND payload->>'category' = 'MANDATORY_GROWTH';

            IF actual_codes IS DISTINCT FROM
               ARRAY[
                   'G01','G02','G03','G04','G05',
                   'G06','G07','G08','G09'
               ]::text[] THEN
                RAISE EXCEPTION
                    'cannot align fixed-task points: published catalog is %',
                    actual_codes;
            END IF;

            SELECT count(*)
            INTO mismatch_count
            FROM public.task_templates AS template
            JOIN (
                VALUES
                    ('G01', 3), ('G02', 2), ('G03', 2),
                    ('G04', 3), ('G05', 3), ('G06', 4),
                    ('G07', 3), ('G08', 5), ('G09', 5)
            ) AS approved(task_code, points)
              ON approved.task_code = template.template_id
            WHERE template.status = 'PUBLISHED'
              AND (
                  template.payload->>'score_value'
              )::numeric IS DISTINCT FROM approved.points;

            IF mismatch_count = 0 THEN
                RETURN;
            END IF;

            SELECT count(*)
            INTO fixed_award_count
            FROM public.score_entries
            WHERE entry_type = 'FIXED_TASK_AWARD';

            SELECT count(*)
            INTO nonzero_task_account_count
            FROM public.score_accounts
            WHERE dimension = 'NEW_TEACHER_TASK'
              AND abs(current_score) > 0.000001;

            SELECT count(*)
            INTO nonzero_task_component_count
            FROM public.score_component_accounts
            WHERE dimension = 'NEW_TEACHER_TASK'
              AND abs(current_score) > 0.000001;

            IF fixed_award_count > 0
               OR nonzero_task_account_count > 0
               OR nonzero_task_component_count > 0 THEN
                RAISE EXCEPTION
                    'fixed-task point alignment requires governed recalculation: awards=%, accounts=%, components=%',
                    fixed_award_count,
                    nonzero_task_account_count,
                    nonzero_task_component_count;
            END IF;

            UPDATE public.task_templates AS template
            SET
                payload = template.payload || jsonb_build_object(
                    'score_value', approved.points,
                    'revision', template.revision + 1,
                    'updated_by',
                        'SYSTEM_MIGRATION_20260729_CATALOG_SCORES',
                    'updated_at', now()
                ),
                revision = template.revision + 1,
                updated_by =
                    'SYSTEM_MIGRATION_20260729_CATALOG_SCORES',
                updated_at = now()
            FROM (
                VALUES
                    ('G01', 3), ('G02', 2), ('G03', 2),
                    ('G04', 3), ('G05', 3), ('G06', 4),
                    ('G07', 3), ('G08', 5), ('G09', 5)
            ) AS approved(task_code, points)
            WHERE template.template_id = approved.task_code
              AND template.status = 'PUBLISHED'
              AND (
                  template.payload->>'score_value'
              )::numeric IS DISTINCT FROM approved.points;
        END
        $catalog_alignment$
        """
    )


def downgrade() -> None:
    # Do not restore the known-wrong legacy point distribution.  This revision
    # changes no schema, and production data rollback uses a governed forward
    # repair or the pre-release database backup.
    return

