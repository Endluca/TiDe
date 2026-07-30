-- Local-only fixtures for Shiwen-owned read models.
-- Production and company-test databases use Shiwen's real views.
BEGIN;

CREATE SCHEMA IF NOT EXISTS tide_mock_source;

DROP VIEW IF EXISTS public.teacher_lesson_score_current;
DROP VIEW IF EXISTS public.teacher_scorecard_current;
DROP VIEW IF EXISTS tide_mock_source.teacher_identity_v1;

CREATE VIEW tide_mock_source.teacher_identity_v1 AS
SELECT
    teacher.teacher_id,
    teacher.camp_enrollment_id,
    teacher.name,
    teacher.timezone,
    teacher.camp_day,
    teacher.graduation_state,
    teacher.data_mode,
    teacher.updated_at AS source_updated_at
FROM public.teachers teacher;

DROP VIEW IF EXISTS tide_mock_source.teacher_metrics_v1;
DROP VIEW IF EXISTS tide_mock_source.teacher_courses_v1;
DROP VIEW IF EXISTS tide_mock_source.score_policy_versions_v1;

CREATE VIEW public.teacher_scorecard_current AS
WITH latest_metric AS (
    SELECT DISTINCT ON (snapshot.teacher_id)
        snapshot.teacher_id,
        snapshot.user_feedback_score,
        snapshot.reliability_score,
        snapshot.class_quality_score,
        snapshot.metric_inputs,
        snapshot.score_policy_snapshot,
        snapshot.score_rule_version,
        snapshot.updated_at
    FROM public.teacher_metric_snapshots snapshot
    ORDER BY
        snapshot.teacher_id,
        snapshot.updated_at DESC,
        snapshot.created_at DESC,
        snapshot.snapshot_id DESC
),
task_rollup AS (
    SELECT
        teacher.teacher_id,
        count(assignment.assignment_id)::integer AS mandatory_task_total_count,
        count(*) FILTER (
            WHERE assignment.status = 'COMPLETED'
        )::integer AS mandatory_task_completed_count,
        COALESCE(
            sum(
                CASE
                    WHEN assignment.status = 'COMPLETED'
                        THEN (template.payload ->> 'score_value')::numeric
                    ELSE 0
                END
            ),
            0
        ) AS task_score,
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'code', assignment.task_code,
                    'unit_count', CASE
                        WHEN assignment.status = 'COMPLETED' THEN 1
                        ELSE 0
                    END,
                    'points_per_unit',
                        (template.payload ->> 'score_value')::numeric,
                    'score', CASE
                        WHEN assignment.status = 'COMPLETED'
                            THEN (template.payload ->> 'score_value')::numeric
                        ELSE 0
                    END,
                    'source_scope', 'TASK',
                    'source_metric', 'task_assignments.status',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', CASE
                        WHEN assignment.status = 'COMPLETED'
                            THEN (template.payload ->> 'score_value')::numeric
                        ELSE 0
                    END,
                    'reconciliation_status', 'MATCHED'
                )
                ORDER BY assignment.task_code
            ) FILTER (WHERE assignment.assignment_id IS NOT NULL),
            '[]'::jsonb
        ) AS task_components
    FROM public.teachers teacher
    LEFT JOIN public.task_assignments assignment
      ON assignment.teacher_id = teacher.teacher_id
     AND assignment.task_code ~ '^G0[1-9]$'
    LEFT JOIN public.task_templates template
      ON template.row_id = assignment.template_version_id
    GROUP BY teacher.teacher_id
),
score_source AS (
    SELECT
        teacher.teacher_id,
        teacher.camp_enrollment_id,
        teacher.graduation_state,
        teacher.graduation_threshold,
        COALESCE(metric.user_feedback_score, 0)::numeric AS user_feedback_score,
        COALESCE(metric.reliability_score, 0)::numeric AS reliability_score,
        COALESCE(metric.class_quality_score, 0)::numeric AS class_quality_score,
        CASE
            WHEN COALESCE(
                NULLIF(metric.metric_inputs ->> 'peak_slot_cnt', '')::numeric,
                0
            ) >= 40 THEN 10::numeric
            ELSE 0::numeric
        END AS capacity_score,
        task.task_score,
        task.mandatory_task_completed_count,
        task.mandatory_task_total_count,
        task.task_components,
        COALESCE(metric.metric_inputs, '{}'::jsonb) AS metric_inputs,
        COALESCE(metric.score_policy_snapshot, '{}'::jsonb) AS score_policy,
        COALESCE(metric.score_rule_version, 'LOCAL_FIXTURE_V1') AS score_rule_version,
        COALESCE(metric.updated_at, teacher.updated_at) AS calculated_at
    FROM public.teachers teacher
    LEFT JOIN latest_metric metric
      ON metric.teacher_id = teacher.teacher_id
    JOIN task_rollup task
      ON task.teacher_id = teacher.teacher_id
)
SELECT
    source.teacher_id,
    source.camp_enrollment_id,
    total.raw_total_score,
    LEAST(200::numeric, total.raw_total_score) AS public_total_score,
    source.graduation_state,
    total.raw_total_score >= source.graduation_threshold AS graduation_qualified,
    total.raw_total_score >= 200 AS gold_qualified,
    source.graduation_threshold,
    200::numeric AS gold_threshold,
    source.mandatory_task_completed_count,
    source.mandatory_task_total_count,
    source.score_rule_version,
    source.calculated_at,
    jsonb_build_array(
        jsonb_build_object(
            'code', 'USER_FEEDBACK',
            'score', source.user_feedback_score,
            'score_rule_version', source.score_rule_version,
            'projection_revision', 1,
            'calculated_at', source.calculated_at,
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'FEEDBACK_PRAISE',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'feedback_praise_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,feedback_praise,points_per_unit}', '')::numeric,
                        5
                    ),
                    'score', COALESCE(
                        NULLIF(source.metric_inputs ->> 'feedback_praise_cnt', '')::numeric,
                        0
                    ) * COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,feedback_praise,points_per_unit}', '')::numeric,
                        5
                    ),
                    'source_scope', 'LESSON',
                    'source_metric', 'feedback_praise_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', source.user_feedback_score,
                    'reconciliation_status', 'SOURCE_FIXTURE'
                ),
                jsonb_build_object(
                    'code', 'FEEDBACK_FAVORITE',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'feedback_favorite_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,feedback_favorite,points_per_unit}', '')::numeric,
                        5
                    ),
                    'score', 0,
                    'source_scope', 'LESSON',
                    'source_metric', 'feedback_favorite_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', 0,
                    'reconciliation_status', 'SOURCE_FIXTURE'
                ),
                jsonb_build_object(
                    'code', 'FEEDBACK_REBOOK_15D',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'completed_again_student_15d_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,feedback_rebook_15d,points_per_unit}', '')::numeric,
                        8
                    ),
                    'score', 0,
                    'source_scope', 'LESSON',
                    'source_metric', 'completed_again_student_15d_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', 0,
                    'reconciliation_status', 'SOURCE_FIXTURE'
                )
            )
        ),
        jsonb_build_object(
            'code', 'RELIABILITY',
            'score', source.reliability_score,
            'score_rule_version', source.score_rule_version,
            'projection_revision', 1,
            'calculated_at', source.calculated_at,
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'ON_TIME_COMPLETED',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'on_time_completed_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,reliability_on_time,points_per_unit}', '')::numeric,
                        2
                    ),
                    'score', source.reliability_score,
                    'source_scope', 'LESSON',
                    'source_metric', 'on_time_completed_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', source.reliability_score,
                    'reconciliation_status', 'SOURCE_FIXTURE'
                ),
                jsonb_build_object(
                    'code', 'PEAK_COMPLETED',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'peak_completed_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', COALESCE(
                        NULLIF(source.score_policy #>> '{scoring_items,reliability_peak,points_per_unit}', '')::numeric,
                        1
                    ),
                    'score', 0,
                    'source_scope', 'LESSON',
                    'source_metric', 'peak_completed_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', 0,
                    'reconciliation_status', 'SOURCE_FIXTURE'
                )
            )
        ),
        jsonb_build_object(
            'code', 'CLASS_QUALITY',
            'score', source.class_quality_score,
            'score_rule_version', source.score_rule_version,
            'projection_revision', 1,
            'calculated_at', source.calculated_at,
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'CLASS_QUALITY_PERFECT_COUNT',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'perfect_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', 2,
                    'score', source.class_quality_score,
                    'source_scope', 'LESSON',
                    'source_metric', 'perfect_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', source.class_quality_score,
                    'reconciliation_status', 'SOURCE_MISSING'
                )
            )
        ),
        jsonb_build_object(
            'code', 'CAPACITY',
            'score', source.capacity_score,
            'score_rule_version', source.score_rule_version,
            'projection_revision', 1,
            'calculated_at', source.calculated_at,
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'CAPACITY_PEAK_SLOT_40',
                    'unit_count', COALESCE(
                        NULLIF(source.metric_inputs ->> 'peak_slot_cnt', '')::numeric,
                        0
                    ),
                    'points_per_unit', NULL,
                    'score', source.capacity_score,
                    'source_scope', 'TEACHER',
                    'source_metric', 'peak_slot_cnt',
                    'lesson_attributed_count', 0,
                    'lesson_attributed_score', 0,
                    'unattributed_score', source.capacity_score,
                    'reconciliation_status', 'MATCHED'
                )
            )
        ),
        jsonb_build_object(
            'code', 'NEW_TEACHER_TASK',
            'score', source.task_score,
            'score_rule_version', source.score_rule_version,
            'projection_revision', 1,
            'calculated_at', source.calculated_at,
            'components', source.task_components
        )
    ) AS dimensions
FROM score_source source
CROSS JOIN LATERAL (
    SELECT (
        source.user_feedback_score
        + source.reliability_score
        + source.class_quality_score
        + source.capacity_score
        + source.task_score
    )::numeric AS raw_total_score
) total;

CREATE VIEW public.teacher_lesson_score_current AS
WITH latest_metric AS (
    SELECT DISTINCT ON (snapshot.teacher_id)
        snapshot.teacher_id,
        snapshot.score_policy_snapshot,
        snapshot.score_rule_version
    FROM public.teacher_metric_snapshots snapshot
    ORDER BY
        snapshot.teacher_id,
        snapshot.updated_at DESC,
        snapshot.created_at DESC,
        snapshot.snapshot_id DESC
),
lesson_source AS (
    SELECT
        lesson.*,
        row_number() OVER (
            PARTITION BY lesson.teacher_id
            ORDER BY lesson.scheduled_start_at, lesson.lesson_id
        )::integer AS lesson_sequence,
        count(*) OVER (
            PARTITION BY lesson.teacher_id
        )::integer AS lesson_count,
        COALESCE(metric.score_policy_snapshot, '{}'::jsonb) AS score_policy,
        COALESCE(metric.score_rule_version, 'LOCAL_FIXTURE_V1') AS score_rule_version
    FROM public.lesson_facts lesson
    LEFT JOIN latest_metric metric
      ON metric.teacher_id = lesson.teacher_id
),
lesson_points AS (
    SELECT
        lesson.*,
        CASE
            WHEN lesson.valid_for_scoring
             AND lesson.has_positive_feedback_tag IS TRUE
                THEN COALESCE(
                    NULLIF(lesson.score_policy #>> '{scoring_items,feedback_praise,points_per_unit}', '')::numeric,
                    5
                )
            ELSE 0::numeric
        END AS praise_score,
        CASE
            WHEN lesson.valid_for_scoring
             AND lesson.is_favorited IS TRUE
                THEN COALESCE(
                    NULLIF(lesson.score_policy #>> '{scoring_items,feedback_favorite,points_per_unit}', '')::numeric,
                    5
                )
            ELSE 0::numeric
        END AS favorite_score,
        CASE
            WHEN lesson.valid_for_scoring
             AND lesson.is_rebooked IS TRUE
                THEN COALESCE(
                    NULLIF(lesson.score_policy #>> '{scoring_items,feedback_rebook_15d,points_per_unit}', '')::numeric,
                    8
                )
            ELSE 0::numeric
        END AS rebook_score,
        CASE
            WHEN lesson.valid_for_scoring
             AND lower(lesson.lesson_lifecycle_status) = 'end'
             AND lesson.is_late IS FALSE
             AND lesson.is_early IS FALSE
             AND lesson.is_false_early_leave IS FALSE
                THEN COALESCE(
                    NULLIF(lesson.score_policy #>> '{scoring_items,reliability_on_time,points_per_unit}', '')::numeric,
                    2
                )
            ELSE 0::numeric
        END AS on_time_score,
        CASE
            WHEN lesson.valid_for_scoring
             AND lower(lesson.lesson_lifecycle_status) = 'end'
             AND lesson.is_peak IS TRUE
                THEN COALESCE(
                    NULLIF(lesson.score_policy #>> '{scoring_items,reliability_peak,points_per_unit}', '')::numeric,
                    1
                )
            ELSE 0::numeric
        END AS peak_score
    FROM lesson_source lesson
)
SELECT
    lesson.teacher_id,
    lesson.lesson_id,
    lesson.lesson_sequence,
    lesson.lesson_count,
    lesson.source_appoint_id,
    lesson.scheduled_start_at,
    lesson.lesson_local_date,
    lesson.lesson_local_time,
    lesson.lesson_lifecycle_status,
    lesson.valid_for_scoring,
    lesson.evidence_status,
    (
        lesson.praise_score
        + lesson.favorite_score
        + lesson.rebook_score
        + lesson.on_time_score
        + lesson.peak_score
    )::numeric AS lesson_total_score,
    lesson.score_rule_version,
    lesson.updated_at,
    jsonb_build_object(
        'attendance', jsonb_build_object(
            'is_late', lesson.is_late,
            'is_early', lesson.is_early,
            'is_false_early_leave', lesson.is_false_early_leave
        ),
        'user_feedback', jsonb_build_object(
            'has_positive_feedback_tag', lesson.has_positive_feedback_tag,
            'is_favorited', lesson.is_favorited,
            'is_rebooked', lesson.is_rebooked
        ),
        'classroom_quality', jsonb_build_object(
            'is_camera_off', lesson.is_camera_off,
            'is_cpu_usage_high', lesson.is_cpu_usage_high,
            'is_network_delay_high', lesson.is_network_delay_high
        ),
        'capacity', jsonb_build_object('is_peak', lesson.is_peak)
    ) AS business_facts,
    jsonb_build_array(
        jsonb_build_object(
            'code', 'USER_FEEDBACK',
            'score', lesson.praise_score + lesson.favorite_score + lesson.rebook_score,
            'evidence_status', lesson.evidence_status,
            'evidence_coverage', 'FULL',
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'FEEDBACK_PRAISE',
                    'score', lesson.praise_score,
                    'points_per_unit', COALESCE(
                        NULLIF(lesson.score_policy #>> '{scoring_items,feedback_praise,points_per_unit}', '')::numeric,
                        5
                    ),
                    'awarded', lesson.praise_score > 0,
                    'evidence_status', lesson.evidence_status
                ),
                jsonb_build_object(
                    'code', 'FEEDBACK_FAVORITE',
                    'score', lesson.favorite_score,
                    'points_per_unit', COALESCE(
                        NULLIF(lesson.score_policy #>> '{scoring_items,feedback_favorite,points_per_unit}', '')::numeric,
                        5
                    ),
                    'awarded', lesson.favorite_score > 0,
                    'evidence_status', lesson.evidence_status
                ),
                jsonb_build_object(
                    'code', 'FEEDBACK_REBOOK_15D',
                    'score', lesson.rebook_score,
                    'points_per_unit', COALESCE(
                        NULLIF(lesson.score_policy #>> '{scoring_items,feedback_rebook_15d,points_per_unit}', '')::numeric,
                        8
                    ),
                    'awarded', lesson.rebook_score > 0,
                    'evidence_status', lesson.evidence_status
                )
            )
        ),
        jsonb_build_object(
            'code', 'RELIABILITY',
            'score', lesson.on_time_score + lesson.peak_score,
            'evidence_status', lesson.evidence_status,
            'evidence_coverage', 'FULL',
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'ON_TIME_COMPLETED',
                    'score', lesson.on_time_score,
                    'points_per_unit', COALESCE(
                        NULLIF(lesson.score_policy #>> '{scoring_items,reliability_on_time,points_per_unit}', '')::numeric,
                        2
                    ),
                    'awarded', lesson.on_time_score > 0,
                    'evidence_status', lesson.evidence_status
                ),
                jsonb_build_object(
                    'code', 'PEAK_COMPLETED',
                    'score', lesson.peak_score,
                    'points_per_unit', COALESCE(
                        NULLIF(lesson.score_policy #>> '{scoring_items,reliability_peak,points_per_unit}', '')::numeric,
                        1
                    ),
                    'awarded', lesson.peak_score > 0,
                    'evidence_status', lesson.evidence_status
                )
            )
        ),
        jsonb_build_object(
            'code', 'CLASS_QUALITY',
            'score', 0,
            'evidence_status', 'SOURCE_MISSING',
            'evidence_coverage', NULL,
            'components', jsonb_build_array(
                jsonb_build_object(
                    'code', 'CLASS_QUALITY_PERFECT_COUNT',
                    'score', 0,
                    'points_per_unit', 2,
                    'awarded', false,
                    'evidence_status', 'SOURCE_MISSING'
                )
            )
        )
    ) AS dimensions
FROM lesson_points lesson;

REVOKE ALL ON public.teacher_scorecard_current FROM PUBLIC;
REVOKE ALL ON public.teacher_lesson_score_current FROM PUBLIC;

COMMIT;
