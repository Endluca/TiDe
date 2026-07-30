BEGIN;

ALTER TABLE tide.app_events
    ALTER COLUMN teacher_binding_id DROP NOT NULL,
    ADD COLUMN anonymous_teacher_id varchar(64),
    ADD COLUMN event_schema_version integer NOT NULL DEFAULT 1,
    ADD COLUMN event_source varchar(16) NOT NULL DEFAULT 'CLIENT',
    ADD COLUMN session_id varchar(128);

UPDATE tide.app_events
SET
    anonymous_teacher_id =
        md5('tide-analytics:' || COALESCE(teacher_binding_id::text, event_id))
        || md5('tide-analytics:v1:' || COALESCE(teacher_binding_id::text, event_id)),
    session_id = left('legacy-' || event_id, 128)
WHERE anonymous_teacher_id IS NULL OR session_id IS NULL;

ALTER TABLE tide.app_events
    ALTER COLUMN anonymous_teacher_id SET NOT NULL,
    ALTER COLUMN session_id SET NOT NULL,
    ADD CONSTRAINT app_events_schema_version_check
        CHECK (event_schema_version = 1),
    ADD CONSTRAINT app_events_source_check
        CHECK (event_source IN ('CLIENT', 'BACKEND')),
    ADD CONSTRAINT app_events_anonymous_teacher_check
        CHECK (anonymous_teacher_id ~ '^[a-f0-9]{64}$'),
    ADD CONSTRAINT app_events_session_check
        CHECK (char_length(session_id) BETWEEN 8 AND 128);

CREATE UNIQUE INDEX app_events_anonymous_event_key
    ON tide.app_events (anonymous_teacher_id, event_id)
    WHERE teacher_binding_id IS NULL;

CREATE INDEX app_events_name_time_idx
    ON tide.app_events (event_name, occurred_at DESC);

CREATE INDEX app_events_task_name_time_idx
    ON tide.app_events (task_assignment_id, event_name, occurred_at DESC)
    WHERE task_assignment_id IS NOT NULL;

CREATE INDEX app_events_session_time_idx
    ON tide.app_events (session_id, occurred_at);

CREATE INDEX app_events_task_code_version_time_idx
    ON tide.app_events (
        (properties->>'taskCode'),
        (properties->>'templateVersion'),
        occurred_at DESC
    )
    WHERE task_assignment_id IS NOT NULL;

CREATE OR REPLACE VIEW tide.analytics_actor_task_journey_v1 AS
SELECT
    event.anonymous_teacher_id,
    event.task_assignment_id,
    event.event_name,
    event.event_source,
    event.session_id,
    event.properties->>'taskCode' AS task_code,
    event.properties->>'taskType' AS task_type,
    NULLIF(event.properties->>'templateVersion', '')::integer AS template_version,
    event.properties->>'executionContractVersion' AS execution_contract_version,
    event.properties->>'entrySource' AS entry_source,
    event.properties->>'displayPosition' AS display_position,
    event.properties->>'stepKey' AS step_key,
    NULLIF(event.properties->>'attemptNo', '')::integer AS attempt_no,
    event.properties->>'result' AS result,
    event.properties->>'errorCode' AS error_code,
    event.properties->>'deviceType' AS device_type,
    event.properties->>'language' AS language,
    event.properties->>'clientVersion' AS client_version,
    event.occurred_at,
    event.received_at
FROM tide.app_events event;

CREATE OR REPLACE VIEW tide.analytics_task_assignment_funnel_v1 AS
WITH assignment_rollup AS (
    SELECT
        event.task_assignment_id,
        max(event.anonymous_teacher_id) AS anonymous_teacher_id,
        (array_agg(
            event.properties->>'taskCode'
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE NULLIF(event.properties->>'taskCode', '') IS NOT NULL
        ))[1] AS task_code,
        max(NULLIF(event.properties->>'templateVersion', '')::integer)
            AS template_version,
        (array_agg(
            event.properties->>'executionContractVersion'
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE NULLIF(
                event.properties->>'executionContractVersion', ''
            ) IS NOT NULL
        ))[1] AS execution_contract_version,
        COALESCE(
            (array_agg(
                event.properties->>'entrySource'
                ORDER BY event.occurred_at, event.received_at
            ) FILTER (
                WHERE NULLIF(event.properties->>'entrySource', '') IS NOT NULL
            ))[1],
            'UNKNOWN'
        ) AS entry_source,
        COALESCE(
            (array_agg(
                event.properties->>'displayPosition'
                ORDER BY event.occurred_at, event.received_at
            ) FILTER (
                WHERE NULLIF(
                    event.properties->>'displayPosition', ''
                ) IS NOT NULL
            ))[1],
            'UNKNOWN'
        ) AS display_position,
        (array_agg(
            event.properties->>'deviceType'
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE NULLIF(event.properties->>'deviceType', '') IS NOT NULL
        ))[1] AS device_type,
        (array_agg(
            event.properties->>'language'
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE NULLIF(event.properties->>'language', '') IS NOT NULL
        ))[1] AS language,
        (array_agg(
            event.properties->>'clientVersion'
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE NULLIF(event.properties->>'clientVersion', '') IS NOT NULL
        ))[1] AS client_version,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_CARD_IMPRESSION'
        ) AS exposed_at,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_DETAIL_VIEWED'
        ) AS viewed_at,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_STARTED'
        ) AS started_at,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_SUBMITTED'
        ) AS first_submitted_at,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_VALIDATION_PASSED'
        ) AS first_passed_at,
        min(event.occurred_at) FILTER (
            WHERE event.event_name = 'TASK_COMPLETED'
        ) AS completed_at,
        (array_agg(
            event.event_name
            ORDER BY event.occurred_at, event.received_at
        ) FILTER (
            WHERE event.event_name IN (
                'TASK_VALIDATION_PASSED',
                'TASK_VALIDATION_RETRY_REQUIRED',
                'TASK_VALIDATION_FAILED'
            )
        ))[1] AS first_validation_result,
        count(*) FILTER (
            WHERE event.event_name = 'TASK_VALIDATION_RETRY_REQUIRED'
        ) AS retry_required_count,
        count(*) FILTER (
            WHERE event.event_name = 'TASK_EXITED'
        ) AS exit_count,
        count(*) FILTER (
            WHERE event.event_name = 'TASK_PROGRESS_RESUMED'
        ) AS resume_count,
        count(*) FILTER (
            WHERE event.event_name = 'TASK_RETRY_STARTED'
        ) AS retry_started_count,
        max(NULLIF(event.properties->>'attemptNo', '')::integer)
            AS max_attempt_no
    FROM tide.app_events event
    WHERE event.task_assignment_id IS NOT NULL
    GROUP BY event.task_assignment_id
)
SELECT
    assignment.*,
    date_trunc(
        'day',
        COALESCE(
            assignment.exposed_at,
            assignment.viewed_at,
            assignment.started_at,
            assignment.first_submitted_at,
            assignment.completed_at
        )
    ) AS cohort_day,
    assignment.first_validation_result = 'TASK_VALIDATION_PASSED'
        AS first_attempt_passed,
    assignment.first_passed_at IS NOT NULL AS finally_passed,
    CASE
        WHEN assignment.viewed_at IS NOT NULL
          AND assignment.started_at IS NOT NULL
        THEN extract(
            epoch FROM assignment.started_at - assignment.viewed_at
        ) * 1000
    END AS start_delay_ms,
    CASE
        WHEN assignment.started_at IS NOT NULL
          AND assignment.completed_at IS NOT NULL
        THEN extract(
            epoch FROM assignment.completed_at - assignment.started_at
        ) * 1000
    END AS completion_duration_ms
FROM assignment_rollup assignment;

CREATE OR REPLACE VIEW tide.analytics_task_funnel_v1 AS
SELECT
    task_code,
    template_version,
    execution_contract_version,
    entry_source,
    display_position,
    device_type,
    language,
    client_version,
    cohort_day,
    count(*) FILTER (WHERE exposed_at IS NOT NULL) AS exposed_teachers,
    count(*) FILTER (WHERE viewed_at IS NOT NULL) AS viewed_teachers,
    count(*) FILTER (WHERE started_at IS NOT NULL) AS started_teachers,
    count(*) FILTER (WHERE first_submitted_at IS NOT NULL)
        AS submitted_teachers,
    count(*) FILTER (WHERE first_attempt_passed) AS first_passed_teachers,
    count(*) FILTER (WHERE finally_passed) AS finally_passed_teachers,
    count(*) FILTER (WHERE completed_at IS NOT NULL) AS completed_teachers,
    round(
        count(*) FILTER (WHERE first_attempt_passed)::numeric
        / NULLIF(
            count(*) FILTER (WHERE first_validation_result IS NOT NULL),
            0
        ),
        4
    ) AS first_pass_rate,
    round(
        count(*) FILTER (WHERE finally_passed)::numeric
        / NULLIF(count(*) FILTER (WHERE first_submitted_at IS NOT NULL), 0),
        4
    ) AS final_pass_rate,
    round(
        count(*) FILTER (WHERE completed_at IS NOT NULL)::numeric
        / NULLIF(count(*) FILTER (WHERE started_at IS NOT NULL), 0),
        4
    ) AS completion_rate,
    round(avg(start_delay_ms), 2) AS average_start_delay_ms,
    round(avg(completion_duration_ms), 2) AS average_completion_duration_ms,
    sum(exit_count) AS exit_count,
    sum(resume_count) AS resume_count,
    sum(retry_required_count) AS retry_required_count,
    round(
        count(*) FILTER (WHERE exit_count > 0)::numeric
        / NULLIF(count(*) FILTER (WHERE started_at IS NOT NULL), 0),
        4
    ) AS exited_assignment_rate,
    round(
        count(*) FILTER (WHERE resume_count > 0)::numeric
        / NULLIF(count(*) FILTER (WHERE exit_count > 0), 0),
        4
    ) AS resume_after_exit_rate,
    round(
        count(*) FILTER (
            WHERE retry_required_count > 0 OR retry_started_count > 0
        )::numeric
        / NULLIF(count(*) FILTER (WHERE first_submitted_at IS NOT NULL), 0),
        4
    ) AS retry_rate
FROM tide.analytics_task_assignment_funnel_v1
GROUP BY
    task_code,
    template_version,
    execution_contract_version,
    entry_source,
    display_position,
    device_type,
    language,
    client_version,
    cohort_day;

CREATE OR REPLACE VIEW tide.analytics_task_step_funnel_v1 AS
SELECT
    properties->>'taskCode' AS task_code,
    NULLIF(properties->>'templateVersion', '')::integer AS template_version,
    properties->>'stepKey' AS step_key,
    properties->>'stepType' AS step_type,
    count(*) FILTER (WHERE event_name = 'TASK_STEP_STARTED') AS started_count,
    count(*) FILTER (WHERE event_name = 'TASK_STEP_COMPLETED') AS completed_count,
    count(*) FILTER (WHERE event_name = 'TASK_STEP_FAILED') AS failed_count,
    count(DISTINCT anonymous_teacher_id)
        FILTER (WHERE event_name = 'TASK_STEP_STARTED') AS started_teachers,
    count(DISTINCT anonymous_teacher_id)
        FILTER (WHERE event_name = 'TASK_STEP_COMPLETED') AS completed_teachers
FROM tide.app_events
WHERE event_name IN (
    'TASK_STEP_STARTED', 'TASK_STEP_COMPLETED', 'TASK_STEP_FAILED'
)
GROUP BY
    properties->>'taskCode',
    NULLIF(properties->>'templateVersion', '')::integer,
    properties->>'stepKey',
    properties->>'stepType';

CREATE OR REPLACE VIEW tide.analytics_content_quality_v1 AS
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
    count(*) FILTER (WHERE event_name = 'QUIZ_SUBMITTED')
        AS quiz_submissions,
    count(*) FILTER (WHERE event_name = 'QUIZ_PASSED')
        AS quiz_passes,
    count(*) FILTER (WHERE event_name = 'QUIZ_FAILED')
        AS quiz_failures,
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
        count(*) FILTER (WHERE event_name = 'QUIZ_PASSED')::numeric
        / NULLIF(count(*) FILTER (WHERE event_name = 'QUIZ_SUBMITTED'), 0),
        4
    ) AS quiz_pass_rate,
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
   OR event_name LIKE 'QUIZ_%'
   OR event_name LIKE 'UPLOAD_%'
   OR event_name LIKE 'CAMERA_%'
GROUP BY
    properties->>'taskCode',
    NULLIF(properties->>'templateVersion', '')::integer,
    properties->>'stepKey';

CREATE OR REPLACE VIEW tide.analytics_technical_quality_v1 AS
WITH active_sessions AS (
    SELECT
        date_trunc('day', occurred_at) AS event_day,
        properties->>'deviceType' AS device_type,
        properties->>'language' AS language,
        properties->>'clientVersion' AS client_version,
        count(DISTINCT session_id) AS active_session_count,
        count(DISTINCT anonymous_teacher_id) AS active_teacher_count
    FROM tide.app_events
    WHERE event_source = 'CLIENT'
    GROUP BY
        date_trunc('day', occurred_at),
        properties->>'deviceType',
        properties->>'language',
        properties->>'clientVersion'
),
failures AS (
    SELECT
        date_trunc('day', occurred_at) AS event_day,
        properties->>'deviceType' AS device_type,
        properties->>'language' AS language,
        properties->>'clientVersion' AS client_version,
        event_name,
        properties->>'errorCode' AS error_code,
        count(*) AS failure_count,
        count(DISTINCT session_id) AS affected_session_count,
        count(DISTINCT anonymous_teacher_id) AS affected_teacher_count
    FROM tide.app_events
    WHERE event_name IN (
        'PAGE_LOAD_FAILED', 'API_FAILED', 'NETWORK_TIMEOUT',
        'VIDEO_FAILED', 'UPLOAD_FAILED', 'CAMERA_FAILED',
        'FRONTEND_UNCAUGHT_ERROR'
    )
    GROUP BY
        date_trunc('day', occurred_at),
        properties->>'deviceType',
        properties->>'language',
        properties->>'clientVersion',
        event_name,
        properties->>'errorCode'
)
SELECT
    failure.event_day,
    failure.device_type,
    failure.language,
    failure.client_version,
    failure.event_name,
    failure.error_code,
    failure.failure_count,
    failure.affected_session_count,
    failure.affected_teacher_count,
    active.active_session_count,
    active.active_teacher_count,
    round(
        failure.affected_session_count::numeric
        / NULLIF(active.active_session_count, 0),
        4
    ) AS affected_session_rate
FROM failures failure
LEFT JOIN active_sessions active
  ON active.event_day = failure.event_day
 AND active.device_type IS NOT DISTINCT FROM failure.device_type
 AND active.language IS NOT DISTINCT FROM failure.language
 AND active.client_version IS NOT DISTINCT FROM failure.client_version;

CREATE OR REPLACE VIEW tide.analytics_help_usage_v1 AS
WITH daily_active AS (
    SELECT
        date_trunc('day', occurred_at) AS event_day,
        count(DISTINCT anonymous_teacher_id) AS active_teachers
    FROM tide.app_events
    GROUP BY date_trunc('day', occurred_at)
),
help AS (
    SELECT
        date_trunc('day', occurred_at) AS event_day,
        COALESCE(properties->>'entrySource', 'UNKNOWN') AS entry_source,
        count(*) FILTER (WHERE event_name = 'FAQ_OPENED') AS faq_opens,
        count(*) FILTER (
            WHERE event_name = 'FAQ_QUESTION_SUBMITTED'
        ) AS faq_questions,
        count(*) FILTER (WHERE event_name = 'FAQ_MATCHED') AS faq_hits,
        count(*) FILTER (WHERE event_name = 'FAQ_NOT_MATCHED') AS faq_misses,
        count(*) FILTER (WHERE event_name = 'AI_HELP_OPENED') AS ai_help_opens,
        count(DISTINCT anonymous_teacher_id) AS helped_teachers
    FROM tide.app_events
    WHERE event_name IN (
        'FAQ_OPENED', 'FAQ_QUESTION_SUBMITTED', 'FAQ_MATCHED',
        'FAQ_NOT_MATCHED', 'FAQ_FEEDBACK_SUBMITTED', 'AI_HELP_OPENED'
    )
    GROUP BY
        date_trunc('day', occurred_at),
        COALESCE(properties->>'entrySource', 'UNKNOWN')
)
SELECT
    help.*,
    active.active_teachers,
    round(
        help.helped_teachers::numeric
        / NULLIF(active.active_teachers, 0),
        4
    ) AS help_usage_rate,
    round(
        help.faq_hits::numeric
        / NULLIF(help.faq_hits + help.faq_misses, 0),
        4
    ) AS faq_hit_rate
FROM help
JOIN daily_active active USING (event_day);

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
