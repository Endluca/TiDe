BEGIN;

CREATE TABLE tide.teacher_photo_runs (
    id uuid PRIMARY KEY,
    task_assignment_id varchar NOT NULL REFERENCES public.task_assignments(assignment_id) ON DELETE RESTRICT,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE RESTRICT,
    original_file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    final_file_id uuid UNIQUE REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
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
    CONSTRAINT teacher_photo_runs_idempotency_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT teacher_photo_runs_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT teacher_photo_runs_status_check CHECK (status IN (
        'UPLOADING', 'CHECKING', 'RETRY_REQUIRED', 'BEAUTIFYING',
        'READY', 'UNDER_REVIEW', 'PROCESSING_FAILED'
    )),
    CONSTRAINT teacher_photo_runs_decision_check CHECK (decision IS NULL OR decision IN ('PASS', 'RETRY', 'ERROR')),
    CONSTRAINT teacher_photo_runs_checks_check CHECK (jsonb_typeof(checks) = 'array'),
    CONSTRAINT teacher_photo_runs_confidence_check CHECK (jsonb_typeof(confidence_summary) = 'object'),
    CONSTRAINT teacher_photo_runs_metrics_check CHECK (jsonb_typeof(source_metrics) = 'object'),
    CONSTRAINT teacher_photo_runs_strength_check CHECK (filter_strength IS NULL OR filter_strength IN (0.600, 0.750)),
    CONSTRAINT teacher_photo_runs_ready_check CHECK (
        status <> 'READY' OR (
            decision = 'PASS' AND final_file_id IS NOT NULL AND
            filter_preset IS NOT NULL AND filter_strength IS NOT NULL AND processed_at IS NOT NULL
        )
    ),
    CONSTRAINT teacher_photo_runs_retry_check CHECK (status <> 'RETRY_REQUIRED' OR decision = 'RETRY'),
    CONSTRAINT teacher_photo_runs_beauty_check CHECK (
        status NOT IN ('BEAUTIFYING', 'PROCESSING_FAILED') OR decision = 'PASS'
    )
);

CREATE INDEX teacher_photo_runs_task_time_idx
    ON tide.teacher_photo_runs (task_assignment_id, submitted_at DESC);

CREATE UNIQUE INDEX teacher_photo_runs_ready_task_key
    ON tide.teacher_photo_runs (task_assignment_id)
    WHERE status = 'READY';

COMMIT;
