"""make graduation and gold qualifications irreversible

Revision ID: 20260727_26_irrev_qual
Revises: 20260727_25_teacher_reads
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_26_irrev_qual"
down_revision: Union[str, None] = "20260727_25_teacher_reads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_teacher_scorecard_view(*, use_latched_gold: bool) -> None:
    gold_expression = (
        "t.gold_qualified"
        if use_latched_gold
        else "COALESCE((t.payload ->> 'gold_criteria_met')::boolean, FALSE)"
    )
    graduation_expression = (
        "(t.graduation_state = 'GRADUATED')"
        if use_latched_gold
        else (
            "COALESCE((t.payload ->> 'graduation_criteria_met')::boolean, FALSE)"
        )
    )
    op.execute(
        f"""
        CREATE OR REPLACE VIEW public.teacher_scorecard_current AS
        SELECT
            t.teacher_id,
            t.camp_enrollment_id,
            s.raw_total_score,
            s.public_total_score,
            t.graduation_state,
            {graduation_expression} AS graduation_qualified,
            {gold_expression} AS gold_qualified,
            (
                s.score_policy_snapshot
                #>> '{{thresholds,graduation_raw_score}}'
            )::double precision AS graduation_threshold,
            (
                s.score_policy_snapshot
                #>> '{{thresholds,gold_raw_score}}'
            )::double precision AS gold_threshold,
            progress.mandatory_task_completed_count,
            progress.mandatory_task_total_count,
            s.score_rule_version,
            s.score_policy_sha256,
            s.updated_at AS calculated_at,
            COALESCE(score_dimensions.dimensions, '[]'::jsonb) AS dimensions
        FROM public.teachers AS t
        JOIN public.teacher_metric_snapshots AS s
          ON s.teacher_id = t.teacher_id
         AND s.batch_id = t.source_batch_id
        LEFT JOIN LATERAL (
            SELECT
                count(*) FILTER (
                    WHERE c.dimension = 'NEW_TEACHER_TASK'
                      AND c.unit_count >= 1
                )::integer AS mandatory_task_completed_count,
                count(*) FILTER (
                    WHERE c.dimension = 'NEW_TEACHER_TASK'
                )::integer AS mandatory_task_total_count
            FROM public.score_component_accounts AS c
            WHERE c.teacher_id = t.teacher_id
        ) AS progress ON TRUE
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'code', a.dimension,
                    'score', a.current_score,
                    'score_rule_version', a.score_rule_version,
                    'projection_revision', a.version,
                    'calculated_at', a.updated_at,
                    'components', COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'code', c.component_code,
                                    'source_scope', c.source_scope,
                                    'source_metric', c.source_metric,
                                    'unit_count', c.unit_count,
                                    'points_per_unit', c.points_per_unit,
                                    'score', c.current_score,
                                    'lesson_attributed_count',
                                        c.lesson_attributed_count,
                                    'lesson_attributed_score',
                                        c.lesson_attributed_score,
                                    'unattributed_score',
                                        c.unattributed_score,
                                    'reconciliation_status',
                                        c.reconciliation_status
                                )
                                ORDER BY c.component_code
                            )
                            FROM public.score_component_accounts AS c
                            WHERE c.teacher_id = a.teacher_id
                              AND c.dimension = a.dimension
                        ),
                        '[]'::jsonb
                    )
                )
                ORDER BY CASE a.dimension
                    WHEN 'USER_FEEDBACK' THEN 1
                    WHEN 'RELIABILITY' THEN 2
                    WHEN 'CLASS_QUALITY' THEN 3
                    WHEN 'CAPACITY' THEN 4
                    WHEN 'NEW_TEACHER_TASK' THEN 5
                    ELSE 99
                END
            ) AS dimensions
            FROM public.score_accounts AS a
            WHERE a.teacher_id = t.teacher_id
        ) AS score_dimensions ON TRUE
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.add_column(
        "teachers",
        sa.Column(
            "gold_qualified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE public.teachers
        SET graduation_state = 'GRADUATED'
        WHERE graduation_state = 'GRADUATED'
           OR COALESCE((payload ->> 'graduation_qualified')::boolean, FALSE)
           OR COALESCE((payload ->> 'graduation_criteria_met')::boolean, FALSE)
           OR COALESCE((payload ->> 'gold_qualified')::boolean, FALSE)
           OR COALESCE((payload ->> 'gold_criteria_met')::boolean, FALSE)
        """
    )
    op.execute(
        """
        UPDATE public.teachers
        SET gold_qualified =
            COALESCE((payload ->> 'gold_qualified')::boolean, FALSE)
            OR COALESCE((payload ->> 'gold_criteria_met')::boolean, FALSE)
        """
    )
    op.execute(
        """
        UPDATE public.teachers
        SET payload = jsonb_set(
            jsonb_set(
                payload,
                '{graduation_qualified}',
                to_jsonb(graduation_state = 'GRADUATED'),
                TRUE
            ),
            '{gold_qualified}',
            to_jsonb(gold_qualified),
            TRUE
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_teacher_qualification_reversal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.graduation_state = 'GRADUATED'
               AND NEW.graduation_state <> 'GRADUATED' THEN
                RAISE EXCEPTION
                    'GRADUATION_QUALIFICATION_IRREVERSIBLE';
            END IF;
            IF OLD.gold_qualified AND NOT NEW.gold_qualified THEN
                RAISE EXCEPTION 'GOLD_QUALIFICATION_IRREVERSIBLE';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_teacher_qualification_reversal
        BEFORE UPDATE OF graduation_state, gold_qualified
        ON public.teachers
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_teacher_qualification_reversal()
        """
    )
    _replace_teacher_scorecard_view(use_latched_gold=True)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _replace_teacher_scorecard_view(use_latched_gold=False)
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_guard_teacher_qualification_reversal
        ON public.teachers
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS public.guard_teacher_qualification_reversal()
        """
    )
    op.drop_column("teachers", "gold_qualified")
