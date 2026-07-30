BEGIN;

SET LOCAL lock_timeout = '10s';

-- The operations-side migration keeps task_templates.row_id and assignment_id
-- stable while changing the business task codes. Align the teacher execution
-- routes to those stable template rows without rebuilding execution or progress
-- records.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and tide.task_execution_versions are required before migration 0025';
    END IF;
END
$$;

-- Take every catalog/execution lock before validating the exact set. Otherwise
-- a concurrent writer could change the set between the count and the remap.
LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;

DO $$
BEGIN
    IF to_regclass('tide.system_notification_publications') IS NOT NULL THEN
        EXECUTE
            'LOCK TABLE tide.system_notification_publications IN SHARE MODE';
    END IF;
END
$$;

DO $$
DECLARE
    published_codes text[];
    fixed_execution_count integer;
    fixed_route_count integer;
BEGIN
    SELECT array_agg(template_id ORDER BY template_id)
    INTO published_codes
    FROM public.task_templates
    WHERE status = 'PUBLISHED'
      AND payload->>'category' = 'MANDATORY_GROWTH';

    IF published_codes IS DISTINCT FROM
       ARRAY[
           'G01', 'G02', 'G03', 'G04', 'G05',
           'G06', 'G07', 'G08', 'G09'
       ]::text[] THEN
        RAISE EXCEPTION
            'operations fixed-task catalog must be current G01-G09 before teacher migration 0025: %',
            published_codes;
    END IF;

    IF (
           SELECT count(*)
           FROM (
               VALUES
                   ('G01:v1', 'G01', 'PUBLISHED', 'Profile & Credentials Completion', 3),
                   ('G02:v1', 'G04', 'PUBLISHED', 'Lesson Preparation&Device Network Check', 3),
                   ('G03:v1', 'G02', 'PUBLISHED', 'Platform Policies', 2),
                   ('G04:v1', 'G03', 'PUBLISHED', 'How to handle different types of students', 2),
                   ('G05:v1', 'G00', 'RETIRED', NULL, NULL),
                   ('G06:v1', 'G05', 'PUBLISHED', 'TTP Orientation', 3),
                   ('G07:v1', 'G06', 'PUBLISHED', 'ME Culture & PARSNIP', 4),
                   ('G08:v1', 'G07', 'PUBLISHED', 'Reliability Training', 3),
                   ('G09:v1', 'G08', 'PUBLISHED', 'Cocos Course Training', 5),
                   ('G10:v1', 'G09', 'PUBLISHED', 'SET Teaching Fundamentals', 5)
           ) AS expected(
               row_id,
               task_code,
               expected_status,
               expected_title,
               expected_score
           )
           JOIN public.task_templates AS template
             ON template.row_id = expected.row_id
            AND template.template_id = expected.task_code
            AND template.status = expected.expected_status
            AND (
                expected.task_code = 'G00'
                OR (
                    template.payload->>'title' = expected.expected_title
                    AND (template.payload->>'score_value')::integer
                        = expected.expected_score
                )
            )
       ) <> 10 THEN
        RAISE EXCEPTION
            'operations stable template rows are not the approved semantic mapping for migration 0025';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.task_assignments AS assignment
        LEFT JOIN (
            VALUES
                ('G01:v1', 'G01'),
                ('G02:v1', 'G04'),
                ('G03:v1', 'G02'),
                ('G04:v1', 'G03'),
                ('G05:v1', 'G00'),
                ('G06:v1', 'G05'),
                ('G07:v1', 'G06'),
                ('G08:v1', 'G07'),
                ('G09:v1', 'G08'),
                ('G10:v1', 'G09')
        ) AS expected(row_id, task_code)
          ON expected.row_id = assignment.template_version_id
        WHERE assignment.task_kind = 'FIXED_GROWTH'
          AND (
              expected.row_id IS NULL
              OR assignment.task_code IS DISTINCT FROM expected.task_code
          )
    ) THEN
        RAISE EXCEPTION
            'operations fixed-task assignments are not aligned to their stable template rows';
    END IF;

    SELECT count(*)
    INTO fixed_execution_count
    FROM tide.task_execution_versions
    WHERE shared_template_row_id IN (
        'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
        'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
    );

    IF fixed_execution_count NOT IN (0, 10) THEN
        RAISE EXCEPTION
            'teacher fixed-task executions are partial (%/10); migration 0025 will not recreate or hide missing execution identity',
            fixed_execution_count;
    END IF;

    SELECT count(*)
    INTO fixed_route_count
    FROM tide.task_execution_versions
    WHERE task_code ~ '^G[0-9]{2}$';

    IF fixed_route_count <> fixed_execution_count THEN
        RAISE EXCEPTION
            'teacher fixed-task execution routes (%) do not match the stable fixed-template identities (%)',
            fixed_route_count,
            fixed_execution_count;
    END IF;

    IF to_regclass('tide.system_notification_publications') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM tide.system_notification_publications AS publication
           WHERE publication.status = 'SCHEDULED'
             AND (
                 (
                     publication.action_type = 'TASK_DETAIL'
                     AND publication.action_target IN ('G00', 'G10')
                 )
                 OR COALESCE(
                     publication.audience #> '{task,taskCodes}',
                     '[]'::jsonb
                 ) ?| ARRAY['G00', 'G10']
             )
       ) THEN
        RAISE EXCEPTION
            'scheduled system notifications still reference retired fixed-task codes G00/G10; cancel and recreate them with current semantics';
    END IF;
END
$$;

CREATE TEMP TABLE fixed_execution_semantic_map ON COMMIT DROP AS
SELECT
    execution.id AS execution_id,
    execution.task_code AS previous_task_code,
    template.template_id AS target_task_code
FROM tide.task_execution_versions AS execution
JOIN public.task_templates AS template
  ON template.row_id = execution.shared_template_row_id
JOIN (
    VALUES
        ('G01:v1', 'G01', 'G01'),
        ('G02:v1', 'G02', 'G04'),
        ('G03:v1', 'G03', 'G02'),
        ('G04:v1', 'G04', 'G03'),
        ('G05:v1', 'G05', 'G00'),
        ('G06:v1', 'G06', 'G05'),
        ('G07:v1', 'G07', 'G06'),
        ('G08:v1', 'G08', 'G07'),
        ('G09:v1', 'G09', 'G08'),
        ('G10:v1', 'G10', 'G09')
) AS expected(row_id, previous_task_code, target_task_code)
  ON expected.row_id = template.row_id
 AND expected.target_task_code = template.template_id
WHERE execution.task_code IN (
    expected.previous_task_code,
    expected.target_task_code
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code LIKE 'TMP-0025-%'
    ) THEN
        RAISE EXCEPTION
            'temporary migration 0025 task code is already occupied';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN (
            VALUES
                ('G01:v1', 'G01', 'G01'),
                ('G02:v1', 'G02', 'G04'),
                ('G03:v1', 'G03', 'G02'),
                ('G04:v1', 'G04', 'G03'),
                ('G05:v1', 'G05', 'G00'),
                ('G06:v1', 'G06', 'G05'),
                ('G07:v1', 'G07', 'G06'),
                ('G08:v1', 'G08', 'G07'),
                ('G09:v1', 'G09', 'G08'),
                ('G10:v1', 'G10', 'G09')
        ) AS expected(row_id, previous_task_code, target_task_code)
          ON expected.row_id = execution.shared_template_row_id
        WHERE execution.task_code NOT IN (
            expected.previous_task_code,
            expected.target_task_code
        )
    ) THEN
        RAISE EXCEPTION
            'a stable fixed-task template row has an unknown teacher execution code';
    END IF;

    IF EXISTS (
        SELECT target_task_code
        FROM fixed_execution_semantic_map
        GROUP BY target_task_code
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'more than one teacher execution maps to the same current fixed-task code';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM fixed_execution_semantic_map AS mapping
        JOIN tide.task_execution_versions AS occupied
          ON occupied.task_code = mapping.target_task_code
         AND occupied.id <> mapping.execution_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM fixed_execution_semantic_map AS occupied_mapping
            WHERE occupied_mapping.execution_id = occupied.id
        )
    ) THEN
        RAISE EXCEPTION
            'a non-migrated execution already occupies a current fixed-task code';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN public.task_templates AS template
          ON template.row_id = execution.shared_template_row_id
        WHERE (
            execution.task_code IN (
                'G00', 'G01', 'G02', 'G03', 'G04', 'G05',
                'G06', 'G07', 'G08', 'G09', 'G10'
            )
            OR template.template_id IN (
                'G00', 'G01', 'G02', 'G03', 'G04',
                'G05', 'G06', 'G07', 'G08', 'G09'
            )
        )
          AND NOT EXISTS (
              SELECT 1
              FROM fixed_execution_semantic_map AS mapping
              WHERE mapping.execution_id = execution.id
          )
    ) THEN
        RAISE EXCEPTION
            'a fixed-task execution cannot be mapped through its shared template row';
    END IF;
END
$$;

-- Free every unique business code before applying the permutation.
UPDATE tide.task_execution_versions AS execution
SET
    task_code = 'TMP-0025-' || upper(replace(execution.id::text, '-', '')),
    updated_at = now()
FROM fixed_execution_semantic_map AS mapping
WHERE execution.id = mapping.execution_id;

UPDATE tide.task_execution_versions AS execution
SET
    task_code = mapping.target_task_code,
    status = CASE
        WHEN mapping.target_task_code = 'G00' THEN 'RETIRED'
        ELSE 'ACTIVE'
    END,
    updated_at = now()
FROM fixed_execution_semantic_map AS mapping
WHERE execution.id = mapping.execution_id;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM fixed_execution_semantic_map AS mapping
        JOIN tide.task_execution_versions AS execution
          ON execution.id = mapping.execution_id
        JOIN public.task_templates AS template
          ON template.row_id = execution.shared_template_row_id
        WHERE execution.task_code IS DISTINCT FROM template.template_id
           OR execution.task_code IS DISTINCT FROM mapping.target_task_code
           OR (
               execution.task_code = 'G00'
               AND execution.status <> 'RETIRED'
           )
           OR (
               execution.task_code BETWEEN 'G01' AND 'G09'
               AND execution.status <> 'ACTIVE'
           )
    ) THEN
        RAISE EXCEPTION
            'teacher fixed-task execution semantic alignment verification failed';
    END IF;
END
$$;

-- Keep the immutable event payload as the evidence captured at event time.
-- Analytics v2 resolves the current business semantic through the stable
-- assignment/template identity, while exposing every raw task code for audit.
CREATE OR REPLACE VIEW tide.analytics_task_event_semantics_v2 AS
SELECT
    event.event_id,
    event.anonymous_teacher_id,
    event.task_assignment_id,
    event.event_name,
    event.event_source,
    event.session_id,
    template.template_id AS task_code,
    NULLIF(event.properties->>'taskCode', '') AS raw_task_code,
    template.row_id AS stable_template_row_id,
    CASE
        WHEN template.row_id IS NOT NULL THEN 'STABLE_TEMPLATE'
        ELSE 'UNRESOLVED'
    END AS task_code_resolution,
    event.properties,
    event.occurred_at,
    event.received_at
FROM tide.app_events AS event
LEFT JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = event.task_assignment_id
LEFT JOIN public.task_templates AS template
  ON template.row_id = assignment.template_version_id;

CREATE OR REPLACE VIEW tide.analytics_actor_task_journey_v2 AS
SELECT
    event.event_id,
    event.anonymous_teacher_id,
    event.task_assignment_id,
    event.event_name,
    event.event_source,
    event.session_id,
    event.task_code,
    event.raw_task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    event.properties->>'taskType' AS task_type,
    NULLIF(event.properties->>'templateVersion', '')::integer
        AS template_version,
    event.properties->>'executionContractVersion'
        AS execution_contract_version,
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
FROM tide.analytics_task_event_semantics_v2 AS event;

CREATE OR REPLACE VIEW tide.analytics_task_assignment_funnel_v2 AS
WITH assignment_rollup AS (
    SELECT
        event.task_assignment_id,
        max(event.anonymous_teacher_id) AS anonymous_teacher_id,
        max(event.task_code) AS task_code,
        array_agg(DISTINCT event.raw_task_code ORDER BY event.raw_task_code)
            FILTER (WHERE event.raw_task_code IS NOT NULL) AS raw_task_codes,
        max(event.stable_template_row_id) AS stable_template_row_id,
        max(event.task_code_resolution) AS task_code_resolution,
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
    FROM tide.analytics_task_event_semantics_v2 AS event
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
FROM assignment_rollup AS assignment;

CREATE OR REPLACE VIEW tide.analytics_task_funnel_v2 AS
SELECT
    task_code,
    stable_template_row_id,
    task_code_resolution,
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
    round(avg(completion_duration_ms), 2)
        AS average_completion_duration_ms,
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
FROM tide.analytics_task_assignment_funnel_v2
GROUP BY
    task_code,
    stable_template_row_id,
    task_code_resolution,
    template_version,
    execution_contract_version,
    entry_source,
    display_position,
    device_type,
    language,
    client_version,
    cohort_day;

CREATE OR REPLACE VIEW tide.analytics_task_step_funnel_v2 AS
SELECT
    event.task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    array_agg(DISTINCT event.raw_task_code ORDER BY event.raw_task_code)
        FILTER (WHERE event.raw_task_code IS NOT NULL) AS raw_task_codes,
    NULLIF(event.properties->>'templateVersion', '')::integer
        AS template_version,
    event.properties->>'stepKey' AS step_key,
    event.properties->>'stepType' AS step_type,
    count(*) FILTER (
        WHERE event.event_name = 'TASK_STEP_STARTED'
    ) AS started_count,
    count(*) FILTER (
        WHERE event.event_name = 'TASK_STEP_COMPLETED'
    ) AS completed_count,
    count(*) FILTER (
        WHERE event.event_name = 'TASK_STEP_FAILED'
    ) AS failed_count,
    count(DISTINCT event.anonymous_teacher_id)
        FILTER (WHERE event.event_name = 'TASK_STEP_STARTED')
        AS started_teachers,
    count(DISTINCT event.anonymous_teacher_id)
        FILTER (WHERE event.event_name = 'TASK_STEP_COMPLETED')
        AS completed_teachers
FROM tide.analytics_task_event_semantics_v2 AS event
WHERE event.event_name IN (
    'TASK_STEP_STARTED', 'TASK_STEP_COMPLETED', 'TASK_STEP_FAILED'
)
GROUP BY
    event.task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    NULLIF(event.properties->>'templateVersion', '')::integer,
    event.properties->>'stepKey',
    event.properties->>'stepType';

CREATE OR REPLACE VIEW tide.analytics_content_quality_v2 AS
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
        WHERE event.event_name = 'QUIZ_SUBMITTED'
    ) AS quiz_submissions,
    count(*) FILTER (
        WHERE event.event_name = 'QUIZ_PASSED'
    ) AS quiz_passes,
    count(*) FILTER (
        WHERE event.event_name = 'QUIZ_FAILED'
    ) AS quiz_failures,
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
            WHERE event.event_name = 'QUIZ_PASSED'
        )::numeric
        / NULLIF(
            count(*) FILTER (WHERE event.event_name = 'QUIZ_SUBMITTED'),
            0
        ),
        4
    ) AS quiz_pass_rate,
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
   OR event.event_name LIKE 'QUIZ_%'
   OR event.event_name LIKE 'UPLOAD_%'
   OR event.event_name LIKE 'CAMERA_%'
GROUP BY
    event.task_code,
    event.stable_template_row_id,
    event.task_code_resolution,
    NULLIF(event.properties->>'templateVersion', '')::integer,
    event.properties->>'stepKey';

COMMIT;
