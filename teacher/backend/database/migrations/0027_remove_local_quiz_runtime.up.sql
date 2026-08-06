BEGIN;

SET LOCAL lock_timeout = '10s';

-- Local exams are retired. Preserve no answer or score data because all exams
-- now run in Kuozhi and TIDE stores only Kuozhi progress snapshots.
DELETE FROM tide.task_step_progress AS progress
USING public.task_assignments AS assignment,
      tide.task_execution_versions AS execution,
      tide.task_step_definitions AS definition
WHERE progress.task_assignment_id = assignment.assignment_id
  AND execution.shared_template_row_id = assignment.template_version_id
  AND definition.execution_version_id = execution.id
  AND definition.step_type = 'QUIZ'
  AND definition.step_key = progress.step_key;

DELETE FROM tide.task_step_definitions
WHERE step_type = 'QUIZ';

ALTER TABLE tide.task_step_definitions
    DROP CONSTRAINT IF EXISTS task_step_definitions_type_check;

ALTER TABLE tide.task_step_definitions
    ADD CONSTRAINT task_step_definitions_type_check
    CHECK (step_type IN (
        'VIDEO', 'DOCUMENT', 'CHECKLIST', 'UPLOAD',
        'DEVICE_CHECK', 'EXTERNAL_TRAINING', 'CUSTOM'
    ));

DROP TABLE IF EXISTS tide.quiz_answers;
DROP TABLE IF EXISTS tide.quiz_attempts;
DROP TABLE IF EXISTS tide.task_quiz_banks;

-- Remove retired local-exam metrics from the current analytics read models.
DROP VIEW IF EXISTS tide.analytics_content_quality_v1;
CREATE VIEW tide.analytics_content_quality_v1 AS
SELECT
    properties->>'taskCode' AS task_code,
    NULLIF(properties->>'templateVersion', '')::integer AS template_version,
    properties->>'stepKey' AS step_key,
    count(*) FILTER (WHERE event_name IN ('VIDEO_PLAYED', 'VIDEO_RESUMED'))
        AS video_starts,
    count(*) FILTER (WHERE event_name = 'VIDEO_COMPLETED')
        AS video_completions,
    count(*) FILTER (WHERE event_name IN ('VIDEO_STALLED', 'VIDEO_FAILED'))
        AS video_failures,
    count(*) FILTER (WHERE event_name = 'UPLOAD_STARTED')
        AS upload_starts,
    count(*) FILTER (WHERE event_name = 'UPLOAD_SUCCEEDED')
        AS upload_successes,
    count(*) FILTER (WHERE event_name = 'UPLOAD_FAILED')
        AS upload_failures,
    count(*) FILTER (WHERE event_name = 'CAMERA_OPENED')
        AS camera_opens,
    count(*) FILTER (WHERE event_name = 'CAMERA_FAILED')
        AS camera_failures,
    round(
        count(*) FILTER (WHERE event_name = 'VIDEO_COMPLETED')::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event_name IN ('VIDEO_PLAYED', 'VIDEO_RESUMED')
            ),
            0
        ),
        4
    ) AS video_completion_rate,
    round(
        count(*) FILTER (WHERE event_name = 'UPLOAD_FAILED')::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event_name IN ('UPLOAD_SUCCEEDED', 'UPLOAD_FAILED')
            ),
            0
        ),
        4
    ) AS upload_failure_rate,
    round(
        count(*) FILTER (WHERE event_name = 'CAMERA_FAILED')::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event_name IN ('CAMERA_OPENED', 'CAMERA_FAILED')
            ),
            0
        ),
        4
    ) AS camera_failure_rate
FROM tide.app_events
WHERE event_name LIKE 'VIDEO_%'
   OR event_name LIKE 'UPLOAD_%'
   OR event_name LIKE 'CAMERA_%'
GROUP BY
    properties->>'taskCode',
    NULLIF(properties->>'templateVersion', '')::integer,
    properties->>'stepKey';

DROP VIEW IF EXISTS tide.analytics_content_quality_v2;
CREATE VIEW tide.analytics_content_quality_v2 AS
SELECT
    event.task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    array_agg(DISTINCT event.raw_task_code ORDER BY event.raw_task_code)
        FILTER (WHERE event.raw_task_code IS NOT NULL) AS raw_task_codes,
    NULLIF(event.properties->>'templateVersion', '')::integer
        AS template_version,
    event.properties->>'stepKey' AS step_key,
    count(*) FILTER (
        WHERE event.event_name IN ('VIDEO_PLAYED', 'VIDEO_RESUMED')
    ) AS video_starts,
    count(*) FILTER (
        WHERE event.event_name = 'VIDEO_COMPLETED'
    ) AS video_completions,
    count(*) FILTER (
        WHERE event.event_name IN ('VIDEO_STALLED', 'VIDEO_FAILED')
    ) AS video_failures,
    count(*) FILTER (
        WHERE event.event_name = 'UPLOAD_STARTED'
    ) AS upload_starts,
    count(*) FILTER (
        WHERE event.event_name = 'UPLOAD_SUCCEEDED'
    ) AS upload_successes,
    count(*) FILTER (
        WHERE event.event_name = 'UPLOAD_FAILED'
    ) AS upload_failures,
    count(*) FILTER (
        WHERE event.event_name = 'CAMERA_OPENED'
    ) AS camera_opens,
    count(*) FILTER (
        WHERE event.event_name = 'CAMERA_FAILED'
    ) AS camera_failures,
    round(
        count(*) FILTER (
            WHERE event.event_name = 'VIDEO_COMPLETED'
        )::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event.event_name IN ('VIDEO_PLAYED', 'VIDEO_RESUMED')
            ),
            0
        ),
        4
    ) AS video_completion_rate,
    round(
        count(*) FILTER (
            WHERE event.event_name = 'UPLOAD_FAILED'
        )::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event.event_name IN ('UPLOAD_SUCCEEDED', 'UPLOAD_FAILED')
            ),
            0
        ),
        4
    ) AS upload_failure_rate,
    round(
        count(*) FILTER (
            WHERE event.event_name = 'CAMERA_FAILED'
        )::numeric
        / NULLIF(
            count(*) FILTER (
                WHERE event.event_name IN ('CAMERA_OPENED', 'CAMERA_FAILED')
            ),
            0
        ),
        4
    ) AS camera_failure_rate
FROM tide.analytics_task_event_semantics_v2 AS event
WHERE event.event_name LIKE 'VIDEO_%'
   OR event.event_name LIKE 'UPLOAD_%'
   OR event.event_name LIKE 'CAMERA_%'
GROUP BY
    event.task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    NULLIF(event.properties->>'templateVersion', '')::integer,
    event.properties->>'stepKey';

COMMIT;
