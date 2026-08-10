BEGIN;

CREATE OR REPLACE VIEW tide.analytics_task_business_change_v1 AS
SELECT
    assignment.assignment_id AS task_assignment_id,
    assignment.teacher_id,
    assignment.task_code,
    assignment.completed_at,
    before_snapshot.snapshot_label AS before_snapshot,
    after_7d.snapshot_label AS after_7d_snapshot,
    after_30d.snapshot_label AS after_30d_snapshot,
    after_7d.public_total_score - before_snapshot.public_total_score
        AS score_change_7d,
    after_30d.public_total_score - before_snapshot.public_total_score
        AS score_change_30d,
    after_7d.lessons_completed - before_snapshot.lessons_completed
        AS lessons_change_7d,
    after_30d.lessons_completed - before_snapshot.lessons_completed
        AS lessons_change_30d,
    after_7d.user_feedback_score - before_snapshot.user_feedback_score
        AS feedback_score_change_7d,
    after_30d.user_feedback_score - before_snapshot.user_feedback_score
        AS feedback_score_change_30d,
    'CORRELATION_ONLY_NOT_CAUSATION'::text AS analysis_notice
FROM public.task_assignments assignment
LEFT JOIN LATERAL (
    SELECT snapshot.*
    FROM public.teacher_metric_snapshots snapshot
    WHERE snapshot.teacher_id = assignment.teacher_id
      AND snapshot.created_at <= assignment.completed_at
    ORDER BY snapshot.created_at DESC
    LIMIT 1
) before_snapshot ON true
LEFT JOIN LATERAL (
    SELECT snapshot.*
    FROM public.teacher_metric_snapshots snapshot
    WHERE snapshot.teacher_id = assignment.teacher_id
      AND snapshot.created_at > assignment.completed_at
      AND snapshot.created_at <= assignment.completed_at + interval '7 days'
    ORDER BY snapshot.created_at DESC
    LIMIT 1
) after_7d ON true
LEFT JOIN LATERAL (
    SELECT snapshot.*
    FROM public.teacher_metric_snapshots snapshot
    WHERE snapshot.teacher_id = assignment.teacher_id
      AND snapshot.created_at > assignment.completed_at
      AND snapshot.created_at <= assignment.completed_at + interval '30 days'
    ORDER BY snapshot.created_at DESC
    LIMIT 1
) after_30d ON true
WHERE assignment.completed_at IS NOT NULL;

COMMIT;
