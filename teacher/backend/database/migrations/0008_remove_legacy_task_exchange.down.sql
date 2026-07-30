BEGIN;

CREATE TABLE tide.task_templates (
    id uuid PRIMARY KEY,
    task_code text NOT NULL UNIQUE,
    task_kind text NOT NULL,
    owner text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    data_origin text NOT NULL DEFAULT 'MOCK',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tide.task_template_versions (
    id uuid PRIMARY KEY,
    template_id uuid NOT NULL REFERENCES tide.task_templates(id) ON DELETE RESTRICT,
    version integer NOT NULL,
    execution_contract_version text NOT NULL,
    language text NOT NULL DEFAULT 'en',
    title text NOT NULL,
    why text NOT NULL,
    what_to_do text NOT NULL,
    completion_standard text NOT NULL,
    outcome text NOT NULL,
    content_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (template_id, version)
);

CREATE TABLE tide.teacher_tasks (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE RESTRICT,
    template_version_id uuid NOT NULL REFERENCES tide.task_template_versions(id) ON DELETE RESTRICT,
    task_code text NOT NULL,
    source_type text NOT NULL,
    status text NOT NULL DEFAULT 'AVAILABLE',
    state_version bigint NOT NULL DEFAULT 1,
    available_at timestamptz,
    due_at timestamptz,
    completed_at timestamptz,
    data_origin text NOT NULL DEFAULT 'MOCK',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tide.task_step_definitions
    ADD COLUMN template_version_id uuid REFERENCES tide.task_template_versions(id) ON DELETE CASCADE;
ALTER TABLE tide.task_validation_rules
    ADD COLUMN template_version_id uuid REFERENCES tide.task_template_versions(id) ON DELETE CASCADE;
ALTER TABLE tide.task_template_files
    ADD COLUMN template_version_id uuid REFERENCES tide.task_template_versions(id) ON DELETE CASCADE;

ALTER TABLE tide.task_attempts
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT;
ALTER TABLE tide.task_step_progress
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE;
ALTER TABLE tide.video_progress
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE;
ALTER TABLE tide.task_submissions
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT;
ALTER TABLE tide.task_completions
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT;
ALTER TABLE tide.task_command_receipts
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE;
ALTER TABLE tide.file_upload_intents
    ADD COLUMN teacher_task_id uuid REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE;

CREATE TABLE tide.external_assignments (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    assignment_id text NOT NULL UNIQUE,
    external_task_id text NOT NULL UNIQUE,
    teacher_id text NOT NULL,
    task_code text NOT NULL,
    title text NOT NULL,
    why text NOT NULL,
    what_to_do text NOT NULL,
    completion_standard text NOT NULL,
    outcome text NOT NULL,
    priority text NOT NULL,
    due_at timestamptz,
    payload_hash text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tide.task_status_events (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    from_status text,
    to_status text NOT NULL,
    state_version bigint NOT NULL,
    reason_code text,
    actor_type text NOT NULL,
    actor_ref text,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tide.integration_events (
    id uuid PRIMARY KEY,
    provider_event_id text NOT NULL UNIQUE,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    event_type text NOT NULL,
    assignment_id text,
    external_task_id text NOT NULL,
    teacher_id text,
    task_code text NOT NULL,
    status text,
    sequence bigint,
    reason_code text,
    result_code text,
    occurred_at timestamptz NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_hash text NOT NULL,
    data_origin text NOT NULL DEFAULT 'MOCK',
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
