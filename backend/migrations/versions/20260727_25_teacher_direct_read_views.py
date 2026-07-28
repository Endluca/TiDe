"""make teacher score views directly consumable

Revision ID: 20260727_25_teacher_reads
Revises: 20260727_24_component_jsonb
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260727_25_teacher_reads"
down_revision: Union[str, None] = "20260727_24_component_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _grant_read_views() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_lesson_score_current
                TO tit_growth_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_lesson_score_current
                TO tit_teacher_crud;
            END IF;
        END
        $$;
        """
    )


def _create_direct_read_views() -> None:
    op.execute(
        """
        CREATE VIEW public.teacher_scorecard_current AS
        SELECT
            t.teacher_id,
            t.camp_enrollment_id,
            s.raw_total_score,
            s.public_total_score,
            t.graduation_state,
            COALESCE(
                (t.payload ->> 'graduation_criteria_met')::boolean,
                FALSE
            ) AS graduation_qualified,
            COALESCE(
                (t.payload ->> 'gold_criteria_met')::boolean,
                FALSE
            ) AS gold_qualified,
            (
                s.score_policy_snapshot
                #>> '{thresholds,graduation_raw_score}'
            )::double precision AS graduation_threshold,
            (
                s.score_policy_snapshot
                #>> '{thresholds,gold_raw_score}'
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
    op.execute(
        """
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
                    'is_perfect_completion', NULL,
                    'is_perfect_completion_source_status', 'SOURCE_MISSING'
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
            COALESCE(scores.dimensions, '[]'::jsonb) AS dimensions
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
        """
    )
    _grant_read_views()


def _create_normalized_views() -> None:
    op.execute(
        """
        CREATE VIEW public.teacher_scorecard_current AS
        SELECT
            t.teacher_id,
            s.raw_total_score,
            s.public_total_score,
            s.score_rule_version,
            s.score_policy_sha256,
            s.updated_at AS calculated_at
        FROM public.teachers AS t
        JOIN public.teacher_metric_snapshots AS s
          ON s.teacher_id = t.teacher_id
         AND s.batch_id = t.source_batch_id
        """
    )
    op.execute(
        """
        CREATE VIEW public.teacher_score_dimension_current AS
        SELECT
            teacher_id,
            dimension,
            current_score,
            score_rule_version,
            version AS projection_revision,
            updated_at AS calculated_at,
            payload
        FROM public.score_accounts
        """
    )
    op.execute(
        """
        CREATE VIEW public.teacher_score_component_current AS
        SELECT
            teacher_id,
            dimension,
            component_code,
            source_scope,
            source_metric,
            unit_count,
            points_per_unit,
            current_score,
            lesson_attributed_count,
            lesson_attributed_score,
            unattributed_score,
            reconciliation_status,
            score_rule_version,
            source_teacher_batch_id,
            source_lesson_batch_id,
            projection_revision,
            calculated_at,
            payload
        FROM public.score_component_accounts
        """
    )
    op.execute(
        """
        CREATE VIEW public.teacher_lesson_score_current AS
        SELECT
            s.teacher_id,
            s.lesson_id,
            f.lesson_local_date,
            f.lesson_local_time,
            f.lesson_lifecycle_status,
            s.dimension,
            s.current_score,
            s.evidence_status,
            s.evidence_coverage,
            s.score_rule_version,
            s.current_revision,
            s.score_as_of,
            s.updated_at,
            s.payload
        FROM public.lesson_dimension_scores AS s
        JOIN public.lesson_facts AS f ON f.lesson_id = s.lesson_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_score_dimension_current,
                    public.teacher_score_component_current,
                    public.teacher_lesson_score_current
                TO tit_growth_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_score_dimension_current,
                    public.teacher_score_component_current,
                    public.teacher_lesson_score_current
                TO tit_teacher_crud;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name in (
        "teacher_lesson_score_current",
        "teacher_score_component_current",
        "teacher_score_dimension_current",
        "teacher_scorecard_current",
    ):
        op.execute(f"DROP VIEW IF EXISTS public.{name}")
    _create_direct_read_views()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name in (
        "teacher_lesson_score_current",
        "teacher_scorecard_current",
    ):
        op.execute(f"DROP VIEW IF EXISTS public.{name}")
    _create_normalized_views()
