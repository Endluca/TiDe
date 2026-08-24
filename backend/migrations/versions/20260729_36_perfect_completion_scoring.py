"""make perfect completion a lesson-attributed reliability fact

Revision ID: 20260729_36_perfect_score
Revises: 20260729_35_task_title_en
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260729_36_perfect_score"
down_revision: Union[str, None] = "20260729_35_task_title_en"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_teacher_lesson_view(*, require_empty_absence_reason: bool) -> None:
    absence_condition = (
        "AND NULLIF(btrim(f.absence_reason_detail), '') IS NULL"
        if require_empty_absence_reason
        else ""
    )
    op.execute(
        f"""
        CREATE VIEW public.teacher_lesson_score_current AS
        SELECT
            f.teacher_id,
            f.lesson_id,
            row_number() OVER (
                PARTITION BY f.teacher_id
                ORDER BY f.scheduled_start_at, f.lesson_id
            )::integer AS lesson_sequence,
            count(*) OVER (
                PARTITION BY f.teacher_id
            )::integer AS lesson_count,
            f.source_appoint_id,
            f.scheduled_start_at,
            f.lesson_local_date,
            f.lesson_local_time,
            f.lesson_lifecycle_status,
            f.valid_for_scoring,
            f.evidence_status,
            COALESCE(scores.lesson_total_score, 0) AS lesson_total_score,
            scores.score_rule_version,
            scores.updated_at,
            jsonb_build_object(
                'attendance', jsonb_build_object(
                    'lesson_lifecycle_status', f.lesson_lifecycle_status,
                    'is_late', f.is_late,
                    'is_early', f.is_early,
                    'is_false_early_leave', f.is_false_early_leave,
                    'absence_reason_detail', f.absence_reason_detail
                ),
                'user_feedback', jsonb_build_object(
                    'has_positive_feedback_tag',
                        f.has_positive_feedback_tag,
                    'positive_tag_value', f.positive_tag_value,
                    'has_negative_feedback_tag',
                        f.has_negative_feedback_tag,
                    'negative_tag_values', f.negative_tag_values,
                    'feedback_detail', f.feedback_detail,
                    'is_favorited', f.is_favorited,
                    'is_rebooked', f.is_rebooked,
                    'is_blocked', f.is_blocked
                ),
                'classroom_quality', jsonb_build_object(
                    'is_camera_off', f.is_camera_off,
                    'is_cpu_usage_high', f.is_cpu_usage_high,
                    'is_network_delay_high', f.is_network_delay_high,
                    'is_perfect', perfect_fact.is_perfect
                ),
                'capacity', jsonb_build_object(
                    'is_peak', f.is_peak
                ),
                'complaint', jsonb_build_object(
                    'category_l1', f.complaint_category_l1,
                    'category_l2', f.complaint_category_l2,
                    'category_l3', f.complaint_category_l3,
                    'level', f.complaint_source_level,
                    'route', f.complaint_route
                )
            ) AS business_facts,
            COALESCE(scores.dimensions, '[]'::jsonb) AS dimensions,
            perfect_fact.is_perfect
        FROM public.lesson_facts AS f
        LEFT JOIN LATERAL (
            SELECT
                sum(s.current_score) AS lesson_total_score,
                max(s.score_rule_version) AS score_rule_version,
                max(s.updated_at) AS updated_at,
                jsonb_agg(
                    jsonb_build_object(
                        'code', s.dimension,
                        'score', s.current_score,
                        'evidence_status', s.evidence_status,
                        'evidence_coverage', s.evidence_coverage,
                        'components', COALESCE(
                            s.payload -> 'business_facts',
                            '[]'::jsonb
                        )
                    )
                    ORDER BY CASE s.dimension
                        WHEN 'USER_FEEDBACK' THEN 1
                        WHEN 'RELIABILITY' THEN 2
                        WHEN 'CLASS_QUALITY' THEN 3
                        ELSE 99
                    END
                ) AS dimensions
            FROM public.lesson_dimension_scores AS s
            WHERE s.teacher_id = f.teacher_id
              AND s.lesson_id = f.lesson_id
        ) AS scores ON TRUE
        CROSS JOIN LATERAL (
            SELECT (
                lower(btrim(COALESCE(f.lesson_lifecycle_status, ''))) = 'end'
                {absence_condition}
                AND f.is_late IS FALSE
                AND f.is_early IS FALSE
            ) AS is_perfect
        ) AS perfect_fact
        """
    )


def _grant_view_read() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app'
            ) THEN
                GRANT SELECT ON TABLE public.teacher_lesson_score_current
                TO tit_growth_app;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                GRANT SELECT ON TABLE public.teacher_lesson_score_current
                TO tit_teacher_crud;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                GRANT SELECT ON TABLE public.teacher_lesson_score_current
                TO tit_teacher_crud;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP VIEW IF EXISTS public.teacher_lesson_score_current")
    _create_teacher_lesson_view(require_empty_absence_reason=False)
    _grant_view_read()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP VIEW IF EXISTS public.teacher_lesson_score_current")
    _create_teacher_lesson_view(require_empty_absence_reason=True)
    _grant_view_read()
