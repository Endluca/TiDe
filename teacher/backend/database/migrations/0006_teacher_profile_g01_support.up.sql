BEGIN;

CREATE TABLE tide.teacher_identity_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    camp_enrollment_id text NOT NULL,
    source_version text NOT NULL,
    display_name text NOT NULL,
    timezone text,
    camp_day integer,
    graduation_state text,
    source_updated_at timestamptz NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    is_latest boolean NOT NULL DEFAULT true,
    CONSTRAINT teacher_identity_projections_source_key UNIQUE (teacher_binding_id, source_version),
    CONSTRAINT teacher_identity_projections_camp_day_check CHECK (camp_day IS NULL OR camp_day >= 0)
);

CREATE UNIQUE INDEX teacher_identity_projections_latest_key
    ON tide.teacher_identity_projections (teacher_binding_id)
    WHERE is_latest;

CREATE TABLE tide.g01_review_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_version text NOT NULL,
    self_intro_status text NOT NULL,
    tesol_status text NOT NULL,
    source_updated_at timestamptz NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    is_latest boolean NOT NULL DEFAULT true,
    CONSTRAINT g01_review_projections_source_key UNIQUE (teacher_binding_id, source_version),
    CONSTRAINT g01_review_projections_self_intro_check CHECK (self_intro_status IN ('WAITING', 'IN_REVIEW', 'APPROVED', 'NEEDS_CHANGES', 'UNAVAILABLE')),
    CONSTRAINT g01_review_projections_tesol_check CHECK (tesol_status IN ('WAITING', 'IN_REVIEW', 'APPROVED', 'NEEDS_CHANGES', 'UNAVAILABLE'))
);

CREATE UNIQUE INDEX g01_review_projections_latest_key
    ON tide.g01_review_projections (teacher_binding_id)
    WHERE is_latest;

CREATE TABLE tide.support_requests (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE RESTRICT,
    request_type text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    related_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'OPEN',
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT support_requests_idempotency_key UNIQUE (teacher_binding_id, idempotency_key),
    CONSTRAINT support_requests_type_check CHECK (request_type IN ('HELP', 'APPEAL', 'RECHECK')),
    CONSTRAINT support_requests_status_check CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CLOSED')),
    CONSTRAINT support_requests_subject_check CHECK (char_length(subject) BETWEEN 1 AND 300),
    CONSTRAINT support_requests_body_check CHECK (char_length(body) BETWEEN 1 AND 5000)
);

CREATE INDEX support_requests_teacher_time_idx
    ON tide.support_requests (teacher_binding_id, created_at DESC);

CREATE TABLE tide.client_events (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    event_name text NOT NULL,
    event_id text NOT NULL,
    task_instance_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE SET NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT client_events_event_key UNIQUE (teacher_binding_id, event_id),
    CONSTRAINT client_events_name_check CHECK (char_length(event_name) BETWEEN 1 AND 128),
    CONSTRAINT client_events_properties_check CHECK (jsonb_typeof(properties) = 'object')
);

CREATE INDEX client_events_teacher_time_idx
    ON tide.client_events (teacher_binding_id, occurred_at DESC);

COMMIT;
