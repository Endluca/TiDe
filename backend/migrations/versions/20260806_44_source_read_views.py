"""switch teacher-facing score views to source-wide projections

Revision ID: 20260806_44_source_reads
Revises: 20260806_43_source_results
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op


revision: str = "20260806_44_source_reads"
down_revision: str | None = "20260806_43_source_results"
branch_labels: str | None = None
depends_on: str | None = None


TEACHER_SCORECARD_COLUMNS: tuple[str, ...] = (
    "teacher_id",
    "camp_enrollment_id",
    "raw_total_score",
    "public_total_score",
    "graduation_state",
    "graduation_qualified",
    "gold_qualified",
    "graduation_threshold",
    "gold_threshold",
    "mandatory_task_completed_count",
    "mandatory_task_total_count",
    "score_rule_version",
    "score_policy_sha256",
    "calculated_at",
    "dimensions",
)

TEACHER_LESSON_SCORE_COLUMNS: tuple[str, ...] = (
    "teacher_id",
    "lesson_id",
    "lesson_sequence",
    "lesson_count",
    "source_appoint_id",
    "scheduled_start_at",
    "lesson_local_date",
    "lesson_local_time",
    "lesson_lifecycle_status",
    "valid_for_scoring",
    "evidence_status",
    "lesson_total_score",
    "score_rule_version",
    "updated_at",
    "business_facts",
    "dimensions",
    "is_perfect",
)

_KNOWN_DIMENSIONS = (
    "USER_FEEDBACK",
    "RELIABILITY",
    "CLASS_QUALITY",
    "CAPACITY",
    "NEW_TEACHER_TASK",
)


def _grant_read_views() -> None:
    op.execute(
        """
        DO $source_read_view_grants$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_lesson_score_current
                TO tit_teacher_crud;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tide_business_app'
            ) THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_lesson_score_current
                TO tide_business_app;
            END IF;
        END
        $source_read_view_grants$;
        """
    )


def _create_source_read_views() -> None:
    known_dimensions = ", ".join(
        f"'{dimension}'" for dimension in _KNOWN_DIMENSIONS
    )
    op.execute(
        f"""
        CREATE OR REPLACE VIEW public.teacher_scorecard_current AS
        SELECT
            COALESCE(
                source.tchr_id,
                teacher.teacher_id
            )::varchar(64) AS teacher_id,
            teacher.camp_enrollment_id,
            (
                qualification.gate_results ->> 'raw_total_score'
            )::double precision AS raw_total_score,
            (
                teacher.payload ->> 'external_display_score'
            )::double precision AS public_total_score,
            (
                CASE
                    WHEN qualification.graduation_qualified
                        THEN 'GRADUATED'
                    ELSE 'IN_PROGRESS'
                END
            )::varchar(32) AS graduation_state,
            qualification.graduation_qualified,
            qualification.gold_qualified,
            (
                qualification.gate_results
                ->> 'graduation_raw_score_threshold'
            )::double precision AS graduation_threshold,
            (
                qualification.gate_results
                ->> 'gold_raw_score_threshold'
            )::double precision AS gold_threshold,
            (
                qualification.gate_results
                ->> 'mandatory_task_completed_count'
            )::integer AS mandatory_task_completed_count,
            (
                qualification.gate_results
                ->> 'mandatory_task_expected_count'
            )::integer AS mandatory_task_total_count,
            qualification.score_rule_version,
            NULLIF(
                teacher.payload ->> 'score_policy_sha256',
                ''
            )::varchar(64) AS score_policy_sha256,
            GREATEST(
                qualification.calculated_at,
                score_dimensions.calculated_at
            ) AS calculated_at,
            score_dimensions.dimensions
        FROM public.teachers AS teacher
        LEFT JOIN public.teacher_source_wide AS source
          ON source.tchr_id = teacher.teacher_id
        JOIN public.teacher_qualifications AS qualification
          ON qualification.teacher_id = teacher.teacher_id
        JOIN LATERAL (
            SELECT
                count(*)::integer AS dimension_count,
                max(
                    GREATEST(
                        account.updated_at,
                        COALESCE(
                            component_projection.calculated_at,
                            account.updated_at
                        )
                    )
                ) AS calculated_at,
                jsonb_agg(
                    jsonb_build_object(
                        'code', account.dimension,
                        'score', account.current_score,
                        'score_rule_version', account.score_rule_version,
                        'projection_revision', account.version,
                        'calculated_at', account.updated_at,
                        'components', COALESCE(
                            component_projection.components,
                            '[]'::jsonb
                        )
                    )
                    ORDER BY CASE account.dimension
                        WHEN 'USER_FEEDBACK' THEN 1
                        WHEN 'RELIABILITY' THEN 2
                        WHEN 'CLASS_QUALITY' THEN 3
                        WHEN 'CAPACITY' THEN 4
                        WHEN 'NEW_TEACHER_TASK' THEN 5
                        ELSE 99
                    END
                ) AS dimensions
            FROM public.score_accounts AS account
            LEFT JOIN LATERAL (
                SELECT
                    max(component.calculated_at) AS calculated_at,
                    jsonb_agg(
                        jsonb_build_object(
                            'code', component.component_code,
                            'source_scope', component.source_scope,
                            'source_metric', component.source_metric,
                            'unit_count', component.unit_count,
                            'points_per_unit', component.points_per_unit,
                            'score', component.current_score,
                            'lesson_attributed_count',
                                component.lesson_attributed_count,
                            'lesson_attributed_score',
                                component.lesson_attributed_score,
                            'unattributed_score',
                                component.unattributed_score,
                            'reconciliation_status',
                                component.reconciliation_status
                        )
                        ORDER BY component.component_code
                    ) AS components
                FROM public.score_component_accounts AS component
                WHERE component.teacher_id = account.teacher_id
                  AND component.dimension = account.dimension
                  AND (
                        (
                            account.dimension = 'USER_FEEDBACK'
                            AND component.component_code IN (
                                'FEEDBACK_PRAISE',
                                'FEEDBACK_FAVORITE'
                            )
                        )
                        OR (
                            account.dimension = 'RELIABILITY'
                            AND component.component_code IN (
                                'PERFECT_COMPLETED',
                                'PEAK_COMPLETED'
                            )
                        )
                        OR (
                            account.dimension = 'CLASS_QUALITY'
                            AND component.component_code =
                                'CLASS_QUALITY_HARDWARE'
                        )
                        OR (
                            account.dimension = 'CAPACITY'
                            AND component.component_code =
                                'CAPACITY_PEAK_SLOT_40'
                        )
                        OR (
                            account.dimension = 'NEW_TEACHER_TASK'
                            AND component.component_code IN (
                                'G01', 'G02', 'G03', 'G04', 'G05',
                                'G06', 'G07', 'G08', 'G09'
                            )
                        )
                  )
            ) AS component_projection ON TRUE
            WHERE account.teacher_id = teacher.teacher_id
              AND account.dimension IN ({known_dimensions})
        ) AS score_dimensions
          ON score_dimensions.dimension_count = 5
        WHERE teacher.source_snapshot_label = 'SOURCE_WIDE_CURRENT'
          AND teacher.payload ? 'external_display_score'
          AND qualification.gate_results ?& ARRAY[
                'raw_total_score',
                'graduation_raw_score_threshold',
                'gold_raw_score_threshold',
                'mandatory_task_completed_count',
                'mandatory_task_expected_count'
          ]
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW public.teacher_lesson_score_current AS
        SELECT
            source."老师id"::varchar(64) AS teacher_id,
            source."课程id" AS lesson_id,
            row_number() OVER (
                PARTITION BY source."老师id"
                ORDER BY
                    source."上课日期" NULLS LAST,
                    source."上课时间" NULLS LAST,
                    source."课程id"
            )::integer AS lesson_sequence,
            count(*) OVER (
                PARTITION BY source."老师id"
            )::integer AS lesson_count,
            source."课程id" AS source_appoint_id,
            CASE
                WHEN source."上课日期" IS NOT NULL
                 AND source."上课时间" IS NOT NULL
                THEN (
                    source."上课日期" + source."上课时间"
                ) AT TIME ZONE 'UTC'
                ELSE NULL
            END AS scheduled_start_at,
            source."上课日期" AS lesson_local_date,
            source."上课时间" AS lesson_local_time,
            COALESCE(
                NULLIF(btrim(source."课程状态"), ''),
                'SOURCE_MISSING'
            )::varchar(48) AS lesson_lifecycle_status,
            lower(btrim(COALESCE(source."课程状态", ''))) IN (
                'end', 'ended', 'complete', 'completed', 'finished',
                '已完课', '完课'
            ) AS valid_for_scoring,
            evidence.lesson_evidence_status::varchar(32) AS evidence_status,
            result.lesson_total_score,
            result.score_rule_version::text AS score_rule_version,
            result.calculated_at AS updated_at,
            jsonb_build_object(
                'attendance', jsonb_build_object(
                    'lesson_lifecycle_status', source."课程状态",
                    'is_late', source."迟到",
                    'is_early', source."早退",
                    'is_false_early_leave', source."假早退",
                    'absence_reason_detail', source."缺席原因明细"
                ),
                'user_feedback', jsonb_build_object(
                    'has_positive_feedback_tag', source."好评标签",
                    'positive_tag_value', NULL,
                    'has_negative_feedback_tag', source."差评标签",
                    'negative_tag_values', '[]'::jsonb,
                    'feedback_detail', source."评价详情",
                    'is_favorited', source."收藏",
                    'is_rebooked', NULL,
                    'is_blocked', source."是否拉黑"
                ),
                'classroom_quality', jsonb_build_object(
                    'is_camera_off', source."未开摄像头",
                    'is_cpu_usage_high', source."cpu占用过高",
                    'is_network_delay_high', source."网络延迟过高",
                    'hardware_quality_passed',
                        quality.hardware_quality_passed,
                    'is_perfect', quality.is_perfect
                ),
                'capacity', jsonb_build_object(
                    'is_peak', source."是否高峰"
                ),
                'complaint', jsonb_build_object(
                    'category_l1', source."投诉一级分类",
                    'category_l2', source."投诉二级分类",
                    'category_l3', source."投诉三级分类",
                    'level', complaint_rule.source_level,
                    'route', complaint_rule.default_route
                )
            ) AS business_facts,
            dimension_projection.dimensions,
            quality.is_perfect
        FROM public.lesson_source_wide AS source
        JOIN public.lesson_score_results AS result
          ON result.lesson_id = source."课程id"
        CROSS JOIN LATERAL (
            SELECT
                (
                    lower(btrim(COALESCE(source."课程状态", ''))) = 'end'
                    AND source."迟到" IS FALSE
                    AND source."早退" IS FALSE
                ) AS is_perfect,
                CASE
                    WHEN source."未开摄像头" IS NULL
                      OR source."cpu占用过高" IS NULL
                      OR source."网络延迟过高" IS NULL
                        THEN NULL
                    ELSE
                        source."未开摄像头" IS FALSE
                        AND source."cpu占用过高" IS FALSE
                        AND source."网络延迟过高" IS FALSE
                END AS hardware_quality_passed
        ) AS quality
        CROSS JOIN LATERAL (
            SELECT
                COALESCE(
                    result.dimensions #>>
                        '{USER_FEEDBACK,components,0,evidence_status}',
                    'SOURCE_MISSING'
                ) AS praise_status,
                COALESCE(
                    result.dimensions #>>
                        '{USER_FEEDBACK,components,1,evidence_status}',
                    'SOURCE_MISSING'
                ) AS favorite_status,
                COALESCE(
                    result.dimensions #>>
                        '{RELIABILITY,components,0,evidence_status}',
                    'SOURCE_MISSING'
                ) AS perfect_status,
                COALESCE(
                    result.dimensions #>>
                        '{RELIABILITY,components,1,evidence_status}',
                    'SOURCE_MISSING'
                ) AS peak_status,
                COALESCE(
                    result.dimensions #>>
                        '{CLASS_QUALITY,components,0,evidence_status}',
                    'SOURCE_MISSING'
                ) AS hardware_status
        ) AS component_evidence
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN component_evidence.praise_status = 'CONFIRMED'
                 AND component_evidence.favorite_status = 'CONFIRMED'
                 AND component_evidence.perfect_status = 'CONFIRMED'
                 AND component_evidence.peak_status = 'CONFIRMED'
                 AND component_evidence.hardware_status = 'CONFIRMED'
                    THEN 'CONFIRMED'
                WHEN component_evidence.praise_status = 'SOURCE_MISSING'
                 AND component_evidence.favorite_status = 'SOURCE_MISSING'
                 AND component_evidence.perfect_status = 'SOURCE_MISSING'
                 AND component_evidence.peak_status = 'SOURCE_MISSING'
                 AND component_evidence.hardware_status = 'SOURCE_MISSING'
                    THEN 'SOURCE_MISSING'
                ELSE 'PARTIAL'
            END AS lesson_evidence_status
        ) AS evidence
        CROSS JOIN LATERAL (
            SELECT jsonb_build_array(
                jsonb_build_object(
                    'code', 'USER_FEEDBACK',
                    'score', result.user_feedback_score,
                    'evidence_status', CASE
                        WHEN component_evidence.praise_status = 'CONFIRMED'
                         AND component_evidence.favorite_status = 'CONFIRMED'
                            THEN 'CONFIRMED'
                        WHEN component_evidence.praise_status = 'SOURCE_MISSING'
                         AND component_evidence.favorite_status = 'SOURCE_MISSING'
                            THEN 'SOURCE_MISSING'
                        ELSE 'PARTIAL'
                    END,
                    'evidence_coverage', concat(
                        (component_evidence.praise_status = 'CONFIRMED')::integer
                        + (component_evidence.favorite_status = 'CONFIRMED')::integer,
                        '/2'
                    ),
                    'components', jsonb_build_array(
                        COALESCE(
                            result.dimensions #>
                                '{USER_FEEDBACK,components,0}',
                            '{}'::jsonb
                        ) || jsonb_build_object(
                            'code', 'FEEDBACK_PRAISE'
                        ),
                        COALESCE(
                            result.dimensions #>
                                '{USER_FEEDBACK,components,1}',
                            '{}'::jsonb
                        ) || jsonb_build_object(
                            'code', 'FEEDBACK_FAVORITE'
                        )
                    )
                ),
                jsonb_build_object(
                    'code', 'RELIABILITY',
                    'score', result.reliability_score,
                    'evidence_status', CASE
                        WHEN component_evidence.perfect_status = 'CONFIRMED'
                         AND component_evidence.peak_status = 'CONFIRMED'
                            THEN 'CONFIRMED'
                        WHEN component_evidence.perfect_status = 'SOURCE_MISSING'
                         AND component_evidence.peak_status = 'SOURCE_MISSING'
                            THEN 'SOURCE_MISSING'
                        ELSE 'PARTIAL'
                    END,
                    'evidence_coverage', concat(
                        (component_evidence.perfect_status = 'CONFIRMED')::integer
                        + (component_evidence.peak_status = 'CONFIRMED')::integer,
                        '/2'
                    ),
                    'components', jsonb_build_array(
                        COALESCE(
                            result.dimensions #>
                                '{RELIABILITY,components,0}',
                            '{}'::jsonb
                        ) || jsonb_build_object(
                            'code', 'PERFECT_COMPLETED'
                        ),
                        COALESCE(
                            result.dimensions #>
                                '{RELIABILITY,components,1}',
                            '{}'::jsonb
                        ) || jsonb_build_object(
                            'code', 'PEAK_COMPLETED'
                        )
                    )
                ),
                jsonb_build_object(
                    'code', 'CLASS_QUALITY',
                    'score', result.class_quality_score,
                    'evidence_status',
                        component_evidence.hardware_status,
                    'evidence_coverage', concat(
                        (component_evidence.hardware_status = 'CONFIRMED')::integer,
                        '/1'
                    ),
                    'components', jsonb_build_array(
                        COALESCE(
                            result.dimensions #>
                                '{CLASS_QUALITY,components,0}',
                            '{}'::jsonb
                        ) || jsonb_build_object(
                            'code', 'CLASS_QUALITY_HARDWARE'
                        )
                    )
                )
            ) AS dimensions
        ) AS dimension_projection
        LEFT JOIN LATERAL (
            SELECT
                rule.source_level,
                rule.default_route
            FROM public.complaint_category_rules AS rule
            WHERE rule.category_l3 = source."投诉三级分类"
               OR rule.category_l3_normalized =
                    btrim(source."投诉三级分类")
            ORDER BY
                rule.created_at DESC,
                rule.rule_id DESC
            LIMIT 1
        ) AS complaint_rule ON TRUE
        WHERE jsonb_typeof(result.dimensions) = 'object'
        """
    )


def _create_legacy_read_views() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW public.teacher_scorecard_current AS
        SELECT
            teacher.teacher_id,
            teacher.camp_enrollment_id,
            snapshot.raw_total_score,
            snapshot.public_total_score,
            teacher.graduation_state,
            teacher.graduation_state = 'GRADUATED' AS graduation_qualified,
            teacher.gold_qualified,
            (
                snapshot.score_policy_snapshot
                #>> '{thresholds,graduation_raw_score}'
            )::double precision AS graduation_threshold,
            (
                snapshot.score_policy_snapshot
                #>> '{thresholds,gold_raw_score}'
            )::double precision AS gold_threshold,
            progress.mandatory_task_completed_count,
            progress.mandatory_task_total_count,
            snapshot.score_rule_version,
            snapshot.score_policy_sha256,
            snapshot.updated_at AS calculated_at,
            COALESCE(score_dimensions.dimensions, '[]'::jsonb) AS dimensions
        FROM public.teachers AS teacher
        JOIN public.teacher_metric_snapshots AS snapshot
          ON snapshot.teacher_id = teacher.teacher_id
         AND snapshot.batch_id = teacher.source_batch_id
        LEFT JOIN LATERAL (
            SELECT
                count(*) FILTER (
                    WHERE component.dimension = 'NEW_TEACHER_TASK'
                      AND component.unit_count >= 1
                )::integer AS mandatory_task_completed_count,
                count(*) FILTER (
                    WHERE component.dimension = 'NEW_TEACHER_TASK'
                )::integer AS mandatory_task_total_count
            FROM public.score_component_accounts AS component
            WHERE component.teacher_id = teacher.teacher_id
        ) AS progress ON TRUE
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'code', account.dimension,
                    'score', account.current_score,
                    'score_rule_version', account.score_rule_version,
                    'projection_revision', account.version,
                    'calculated_at', account.updated_at,
                    'components', COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'code', component.component_code,
                                    'source_scope', component.source_scope,
                                    'source_metric', component.source_metric,
                                    'unit_count', component.unit_count,
                                    'points_per_unit', component.points_per_unit,
                                    'score', component.current_score,
                                    'lesson_attributed_count',
                                        component.lesson_attributed_count,
                                    'lesson_attributed_score',
                                        component.lesson_attributed_score,
                                    'unattributed_score',
                                        component.unattributed_score,
                                    'reconciliation_status',
                                        component.reconciliation_status
                                )
                                ORDER BY component.component_code
                            )
                            FROM public.score_component_accounts AS component
                            WHERE component.teacher_id = account.teacher_id
                              AND component.dimension = account.dimension
                        ),
                        '[]'::jsonb
                    )
                )
                ORDER BY CASE account.dimension
                    WHEN 'USER_FEEDBACK' THEN 1
                    WHEN 'RELIABILITY' THEN 2
                    WHEN 'CLASS_QUALITY' THEN 3
                    WHEN 'CAPACITY' THEN 4
                    WHEN 'NEW_TEACHER_TASK' THEN 5
                    ELSE 99
                END
            ) AS dimensions
            FROM public.score_accounts AS account
            WHERE account.teacher_id = teacher.teacher_id
        ) AS score_dimensions ON TRUE
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW public.teacher_lesson_score_current AS
        SELECT
            fact.teacher_id,
            fact.lesson_id,
            row_number() OVER (
                PARTITION BY fact.teacher_id
                ORDER BY fact.scheduled_start_at, fact.lesson_id
            )::integer AS lesson_sequence,
            count(*) OVER (
                PARTITION BY fact.teacher_id
            )::integer AS lesson_count,
            fact.source_appoint_id,
            fact.scheduled_start_at,
            fact.lesson_local_date,
            fact.lesson_local_time,
            fact.lesson_lifecycle_status,
            fact.valid_for_scoring,
            fact.evidence_status,
            COALESCE(scores.lesson_total_score, 0) AS lesson_total_score,
            scores.score_rule_version,
            scores.updated_at,
            jsonb_build_object(
                'attendance', jsonb_build_object(
                    'lesson_lifecycle_status', fact.lesson_lifecycle_status,
                    'is_late', fact.is_late,
                    'is_early', fact.is_early,
                    'is_false_early_leave', fact.is_false_early_leave,
                    'absence_reason_detail', fact.absence_reason_detail
                ),
                'user_feedback', jsonb_build_object(
                    'has_positive_feedback_tag',
                        fact.has_positive_feedback_tag,
                    'positive_tag_value', fact.positive_tag_value,
                    'has_negative_feedback_tag',
                        fact.has_negative_feedback_tag,
                    'negative_tag_values', fact.negative_tag_values,
                    'feedback_detail', fact.feedback_detail,
                    'is_favorited', fact.is_favorited,
                    'is_rebooked', fact.is_rebooked,
                    'is_blocked', fact.is_blocked
                ),
                'classroom_quality', jsonb_build_object(
                    'is_camera_off', fact.is_camera_off,
                    'is_cpu_usage_high', fact.is_cpu_usage_high,
                    'is_network_delay_high', fact.is_network_delay_high,
                    'is_perfect', perfect_fact.is_perfect
                ),
                'capacity', jsonb_build_object(
                    'is_peak', fact.is_peak
                ),
                'complaint', jsonb_build_object(
                    'category_l1', fact.complaint_category_l1,
                    'category_l2', fact.complaint_category_l2,
                    'category_l3', fact.complaint_category_l3,
                    'level', fact.complaint_source_level,
                    'route', fact.complaint_route
                )
            ) AS business_facts,
            COALESCE(scores.dimensions, '[]'::jsonb) AS dimensions,
            perfect_fact.is_perfect
        FROM public.lesson_facts AS fact
        LEFT JOIN LATERAL (
            SELECT
                sum(score.current_score) AS lesson_total_score,
                max(score.score_rule_version) AS score_rule_version,
                max(score.updated_at) AS updated_at,
                jsonb_agg(
                    jsonb_build_object(
                        'code', score.dimension,
                        'score', score.current_score,
                        'evidence_status', score.evidence_status,
                        'evidence_coverage', score.evidence_coverage,
                        'components', COALESCE(
                            score.payload -> 'business_facts',
                            '[]'::jsonb
                        )
                    )
                    ORDER BY CASE score.dimension
                        WHEN 'USER_FEEDBACK' THEN 1
                        WHEN 'RELIABILITY' THEN 2
                        WHEN 'CLASS_QUALITY' THEN 3
                        ELSE 99
                    END
                ) AS dimensions
            FROM public.lesson_dimension_scores AS score
            WHERE score.teacher_id = fact.teacher_id
              AND score.lesson_id = fact.lesson_id
        ) AS scores ON TRUE
        CROSS JOIN LATERAL (
            SELECT (
                lower(btrim(COALESCE(fact.lesson_lifecycle_status, ''))) = 'end'
                AND fact.is_late IS FALSE
                AND fact.is_early IS FALSE
            ) AS is_perfect
        ) AS perfect_fact
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _create_source_read_views()
    _grant_read_views()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _create_legacy_read_views()
    _grant_read_views()
