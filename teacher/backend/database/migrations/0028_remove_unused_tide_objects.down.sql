BEGIN;

-- Structural rollback only.  Migration 0028 refuses populated tables, so
-- there is intentionally no business data to restore.
CREATE TABLE tide.outcome_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL
        REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_version text NOT NULL,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    graduation_status text,
    probation_status text,
    excellence_status text,
    capacity_milestone_status text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_latest boolean NOT NULL DEFAULT true,
    CONSTRAINT outcome_projections_key
        UNIQUE (teacher_binding_id, source_version),
    CONSTRAINT outcome_projections_payload_check
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE UNIQUE INDEX outcome_projections_latest_key
    ON tide.outcome_projections (teacher_binding_id)
    WHERE is_latest;

CREATE TABLE tide.camp_enrollment_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL
        REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    camp_enrollment_id text NOT NULL,
    source_version text NOT NULL,
    enrollment_status text,
    started_at timestamptz,
    ends_at timestamptz,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT camp_enrollment_projections_key
        UNIQUE (teacher_binding_id, camp_enrollment_id, source_version),
    CONSTRAINT camp_enrollment_projections_payload_check
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE tide.audit_events (
    id uuid PRIMARY KEY,
    actor_type text NOT NULL,
    actor_ref text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    reason_code text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_events_actor_check
        CHECK (actor_type IN ('TEACHER', 'SYSTEM', 'ADMIN', 'INTEGRATION')),
    CONSTRAINT audit_events_outcome_check
        CHECK (outcome IN ('SUCCESS', 'FAILURE', 'DENIED')),
    CONSTRAINT audit_events_metadata_check
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX audit_events_resource_time_idx
    ON tide.audit_events (resource_type, resource_id, occurred_at DESC);

CREATE TABLE tide.task_template_files (
    file_id uuid NOT NULL
        REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    purpose text NOT NULL,
    step_key text,
    position integer NOT NULL DEFAULT 1,
    execution_version_id uuid NOT NULL
        REFERENCES tide.task_execution_versions(id) ON DELETE CASCADE,
    CONSTRAINT task_template_files_pkey
        PRIMARY KEY (execution_version_id, file_id, purpose),
    CONSTRAINT task_template_files_position_check CHECK (position > 0)
);

CREATE TABLE tide.file_migrations (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL
        REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    source_provider text NOT NULL,
    target_provider text NOT NULL,
    target_object_key text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    verified_sha256 text,
    started_at timestamptz,
    completed_at timestamptz,
    last_error_code text,
    CONSTRAINT file_migrations_target_key
        UNIQUE (target_provider, target_object_key),
    CONSTRAINT file_migrations_status_check
        CHECK (status IN ('PENDING', 'RUNNING', 'VERIFIED', 'FAILED'))
);

CREATE TABLE tide.teacher_photo_runs (
    id uuid PRIMARY KEY,
    task_assignment_id varchar NOT NULL
        REFERENCES public.task_assignments(assignment_id) ON DELETE RESTRICT,
    account_id uuid NOT NULL
        REFERENCES tide.user_accounts(id) ON DELETE RESTRICT,
    original_file_id uuid NOT NULL
        REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    final_file_id uuid UNIQUE
        REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL DEFAULT 'UPLOADING',
    decision text,
    criteria_version text NOT NULL,
    teacher_message text,
    checks jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    ai_run_id uuid REFERENCES tide.ai_runs(id) ON DELETE RESTRICT,
    filter_preset text,
    filter_strength numeric(4, 3),
    source_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz,
    processed_at timestamptz,
    processing_owner text,
    lease_expires_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teacher_photo_runs_idempotency_key
        UNIQUE (account_id, idempotency_key),
    CONSTRAINT teacher_photo_runs_request_hash_check
        CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT teacher_photo_runs_status_check CHECK (status IN (
        'UPLOADING', 'CHECKING', 'RETRY_REQUIRED', 'BEAUTIFYING',
        'READY', 'UNDER_REVIEW', 'PROCESSING_FAILED'
    )),
    CONSTRAINT teacher_photo_runs_decision_check
        CHECK (decision IS NULL OR decision IN ('PASS', 'RETRY', 'ERROR')),
    CONSTRAINT teacher_photo_runs_checks_check
        CHECK (jsonb_typeof(checks) = 'array'),
    CONSTRAINT teacher_photo_runs_confidence_check
        CHECK (jsonb_typeof(confidence_summary) = 'object'),
    CONSTRAINT teacher_photo_runs_metrics_check
        CHECK (jsonb_typeof(source_metrics) = 'object'),
    CONSTRAINT teacher_photo_runs_strength_check
        CHECK (filter_strength IS NULL OR filter_strength IN (0.600, 0.750, 1.000)),
    CONSTRAINT teacher_photo_runs_ready_check CHECK (
        status <> 'READY' OR (
            decision = 'PASS' AND final_file_id IS NOT NULL AND
            filter_preset IS NOT NULL AND filter_strength IS NOT NULL AND
            processed_at IS NOT NULL
        )
    ),
    CONSTRAINT teacher_photo_runs_retry_check
        CHECK (status <> 'RETRY_REQUIRED' OR decision = 'RETRY'),
    CONSTRAINT teacher_photo_runs_beauty_check CHECK (
        status NOT IN ('BEAUTIFYING', 'PROCESSING_FAILED')
        OR decision = 'PASS'
    ),
    CONSTRAINT teacher_photo_runs_attempt_count_check
        CHECK (attempt_count >= 0),
    CONSTRAINT teacher_photo_runs_lease_pair_check CHECK (
        (processing_owner IS NULL AND lease_expires_at IS NULL)
        OR (processing_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
    )
);

CREATE INDEX teacher_photo_runs_task_time_idx
    ON tide.teacher_photo_runs (task_assignment_id, submitted_at DESC);

CREATE UNIQUE INDEX teacher_photo_runs_ready_task_key
    ON tide.teacher_photo_runs (task_assignment_id)
    WHERE status = 'READY';

CREATE INDEX teacher_photo_runs_pending_claim_idx
    ON tide.teacher_photo_runs (next_attempt_at, submitted_at, id)
    WHERE status IN ('CHECKING', 'BEAUTIFYING');

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

DO $unused_tide_object_restore_grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
        EXECUTE
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
            || 'tide.outcome_projections, '
            || 'tide.camp_enrollment_projections, '
            || 'tide.audit_events, '
            || 'tide.task_template_files, '
            || 'tide.file_migrations, '
            || 'tide.teacher_photo_runs, '
            || 'tide.analytics_actor_task_journey_v1, '
            || 'tide.analytics_task_assignment_funnel_v1, '
            || 'tide.analytics_task_funnel_v1, '
            || 'tide.analytics_task_step_funnel_v1, '
            || 'tide.analytics_content_quality_v1 '
            || 'TO tit_teacher_crud';
    END IF;
END
$unused_tide_object_restore_grants$;

COMMIT;
