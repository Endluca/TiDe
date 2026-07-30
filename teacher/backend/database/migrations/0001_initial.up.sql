BEGIN;

CREATE SCHEMA IF NOT EXISTS tide;

CREATE TABLE tide.user_accounts (
    id uuid PRIMARY KEY,
    email text NOT NULL,
    normalized_email text NOT NULL,
    password_hash text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING_VERIFICATION',
    email_verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_accounts_normalized_email_key UNIQUE (normalized_email),
    CONSTRAINT user_accounts_status_check CHECK (status IN ('PENDING_VERIFICATION', 'ACTIVE', 'LOCKED', 'DISABLED')),
    CONSTRAINT user_accounts_email_normalized_check CHECK (normalized_email = lower(btrim(normalized_email)))
);

CREATE TABLE tide.teacher_bindings (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE RESTRICT,
    teacher_id text NOT NULL,
    source_system text NOT NULL DEFAULT 'SHIWEN',
    status text NOT NULL DEFAULT 'ACTIVE',
    bound_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teacher_bindings_account_key UNIQUE (account_id),
    CONSTRAINT teacher_bindings_teacher_key UNIQUE (teacher_id),
    CONSTRAINT teacher_bindings_status_check CHECK (status IN ('ACTIVE', 'CORRECTED', 'REVOKED')),
    CONSTRAINT teacher_bindings_end_check CHECK ((status = 'ACTIVE' AND ended_at IS NULL) OR status <> 'ACTIVE')
);

CREATE TABLE tide.binding_audit_events (
    id uuid PRIMARY KEY,
    binding_id uuid REFERENCES tide.teacher_bindings(id) ON DELETE SET NULL,
    account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    previous_teacher_id text,
    new_teacher_id text,
    actor_type text NOT NULL,
    actor_ref text,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT binding_audit_events_type_check CHECK (event_type IN ('BOUND', 'CORRECTED', 'REVOKED', 'RESTORED')),
    CONSTRAINT binding_audit_events_actor_check CHECK (actor_type IN ('SYSTEM', 'ADMIN', 'SUPPORT'))
);

CREATE TABLE tide.auth_tokens (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    purpose text NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT auth_tokens_hash_key UNIQUE (token_hash),
    CONSTRAINT auth_tokens_purpose_check CHECK (purpose IN ('EMAIL_VERIFY', 'PASSWORD_RESET')),
    CONSTRAINT auth_tokens_expiry_check CHECK (expires_at > created_at)
);

CREATE INDEX auth_tokens_account_purpose_idx ON tide.auth_tokens (account_id, purpose, created_at DESC);

CREATE TABLE tide.auth_sessions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    refresh_token_hash text NOT NULL,
    device_summary text,
    ip_hash text,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    CONSTRAINT auth_sessions_refresh_hash_key UNIQUE (refresh_token_hash),
    CONSTRAINT auth_sessions_expiry_check CHECK (expires_at > created_at)
);

CREATE INDEX auth_sessions_account_active_idx ON tide.auth_sessions (account_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE tide.auth_security_events (
    id uuid PRIMARY KEY,
    account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    outcome text NOT NULL,
    ip_hash text,
    device_summary text,
    reason_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT auth_security_events_outcome_check CHECK (outcome IN ('SUCCESS', 'FAILURE', 'BLOCKED'))
);

CREATE INDEX auth_security_events_account_time_idx ON tide.auth_security_events (account_id, created_at DESC);

CREATE TABLE tide.email_deliveries (
    id uuid PRIMARY KEY,
    account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
    purpose text NOT NULL,
    template_id text NOT NULL,
    recipient_email_hash text NOT NULL,
    provider_message_id text,
    status text NOT NULL DEFAULT 'PENDING',
    attempt_count integer NOT NULL DEFAULT 0,
    last_error_code text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT email_deliveries_purpose_check CHECK (purpose IN ('EMAIL_VERIFY', 'PASSWORD_RESET')),
    CONSTRAINT email_deliveries_status_check CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED')),
    CONSTRAINT email_deliveries_attempt_check CHECK (attempt_count >= 0)
);

CREATE TABLE tide.task_templates (
    id uuid PRIMARY KEY,
    task_code text NOT NULL,
    task_kind text NOT NULL,
    owner text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    data_origin text NOT NULL DEFAULT 'REAL',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_templates_code_key UNIQUE (task_code),
    CONSTRAINT task_templates_kind_check CHECK (task_kind IN ('FIXED', 'PERSONALIZED')),
    CONSTRAINT task_templates_owner_check CHECK (owner IN ('ECHO', 'JIAHE', 'SHARED')),
    CONSTRAINT task_templates_status_check CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED')),
    CONSTRAINT task_templates_origin_check CHECK (data_origin IN ('REAL', 'MOCK')),
    CONSTRAINT task_templates_fixed_code_check CHECK (task_kind <> 'FIXED' OR task_code ~ '^G(0[1-9]|10)$')
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
    CONSTRAINT task_template_versions_number_key UNIQUE (template_id, version),
    CONSTRAINT task_template_versions_status_check CHECK (status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT task_template_versions_number_check CHECK (version > 0),
    CONSTRAINT task_template_versions_content_check CHECK (jsonb_typeof(content_config) = 'object'),
    CONSTRAINT task_template_versions_publish_check CHECK ((status = 'PUBLISHED' AND published_at IS NOT NULL) OR status <> 'PUBLISHED')
);

CREATE TABLE tide.task_step_definitions (
    id uuid PRIMARY KEY,
    template_version_id uuid NOT NULL REFERENCES tide.task_template_versions(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    position integer NOT NULL,
    step_type text NOT NULL,
    title text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_step_definitions_key UNIQUE (template_version_id, step_key),
    CONSTRAINT task_step_definitions_position_key UNIQUE (template_version_id, position),
    CONSTRAINT task_step_definitions_position_check CHECK (position > 0),
    CONSTRAINT task_step_definitions_type_check CHECK (step_type IN ('VIDEO', 'DOCUMENT', 'QUIZ', 'CHECKLIST', 'UPLOAD', 'DEVICE_CHECK', 'EXTERNAL_TRAINING', 'CUSTOM')),
    CONSTRAINT task_step_definitions_config_check CHECK (jsonb_typeof(config) = 'object')
);

CREATE TABLE tide.task_validation_rules (
    id uuid PRIMARY KEY,
    template_version_id uuid NOT NULL REFERENCES tide.task_template_versions(id) ON DELETE CASCADE,
    rule_key text NOT NULL,
    rule_type text NOT NULL,
    rule_version text NOT NULL,
    position integer NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    teacher_failure_copy text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_validation_rules_key UNIQUE (template_version_id, rule_key),
    CONSTRAINT task_validation_rules_position_key UNIQUE (template_version_id, position),
    CONSTRAINT task_validation_rules_position_check CHECK (position > 0),
    CONSTRAINT task_validation_rules_config_check CHECK (jsonb_typeof(config) = 'object')
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
    viewed_at timestamptz,
    started_at timestamptz,
    submitted_at timestamptz,
    completed_at timestamptz,
    terminal_at timestamptz,
    data_origin text NOT NULL DEFAULT 'REAL',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teacher_tasks_source_check CHECK (source_type IN ('FIXED', 'PERSONALIZED')),
    CONSTRAINT teacher_tasks_status_check CHECK (status IN ('AVAILABLE', 'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')),
    CONSTRAINT teacher_tasks_state_version_check CHECK (state_version > 0),
    CONSTRAINT teacher_tasks_origin_check CHECK (data_origin IN ('REAL', 'MOCK')),
    CONSTRAINT teacher_tasks_due_check CHECK (due_at IS NULL OR available_at IS NULL OR due_at > available_at),
    CONSTRAINT teacher_tasks_completion_check CHECK ((status = 'COMPLETED' AND completed_at IS NOT NULL) OR status <> 'COMPLETED')
);

CREATE UNIQUE INDEX teacher_tasks_fixed_lifetime_key ON tide.teacher_tasks (teacher_binding_id, task_code) WHERE source_type = 'FIXED';
CREATE INDEX teacher_tasks_teacher_status_idx ON tide.teacher_tasks (teacher_binding_id, status, updated_at DESC);

CREATE TABLE tide.external_assignments (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    assignment_id text NOT NULL,
    external_task_id text NOT NULL,
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
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_assignments_task_key UNIQUE (teacher_task_id),
    CONSTRAINT external_assignments_assignment_key UNIQUE (assignment_id),
    CONSTRAINT external_assignments_external_task_key UNIQUE (external_task_id),
    CONSTRAINT external_assignments_priority_check CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT'))
);

CREATE TABLE tide.task_attempts (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    attempt_no integer NOT NULL,
    status text NOT NULL DEFAULT 'IN_PROGRESS',
    started_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    ended_at timestamptz,
    result_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_attempts_number_key UNIQUE (teacher_task_id, attempt_no),
    CONSTRAINT task_attempts_number_check CHECK (attempt_no > 0),
    CONSTRAINT task_attempts_status_check CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'PASSED', 'FAILED', 'ABANDONED'))
);

CREATE TABLE tide.task_step_progress (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    status text NOT NULL DEFAULT 'NOT_STARTED',
    percent smallint NOT NULL DEFAULT 0,
    progress_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_step_progress_key UNIQUE (teacher_task_id, step_key),
    CONSTRAINT task_step_progress_status_check CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'FAILED')),
    CONSTRAINT task_step_progress_percent_check CHECK (percent BETWEEN 0 AND 100),
    CONSTRAINT task_step_progress_summary_check CHECK (jsonb_typeof(progress_summary) = 'object')
);

CREATE TABLE tide.video_progress (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    media_version text NOT NULL,
    duration_seconds integer NOT NULL,
    resume_seconds integer NOT NULL DEFAULT 0,
    max_contiguous_seconds integer NOT NULL DEFAULT 0,
    first_full_watch_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT video_progress_key UNIQUE (teacher_task_id, step_key),
    CONSTRAINT video_progress_duration_check CHECK (duration_seconds > 0),
    CONSTRAINT video_progress_resume_check CHECK (resume_seconds BETWEEN 0 AND duration_seconds),
    CONSTRAINT video_progress_contiguous_check CHECK (max_contiguous_seconds BETWEEN 0 AND duration_seconds)
);

CREATE TABLE tide.quiz_attempts (
    id uuid PRIMARY KEY,
    task_attempt_id uuid NOT NULL REFERENCES tide.task_attempts(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    question_set_version text NOT NULL,
    score numeric(7,2),
    passed boolean,
    started_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    CONSTRAINT quiz_attempts_key UNIQUE (task_attempt_id, step_key)
);

CREATE TABLE tide.quiz_answers (
    id uuid PRIMARY KEY,
    quiz_attempt_id uuid NOT NULL REFERENCES tide.quiz_attempts(id) ON DELETE CASCADE,
    question_key text NOT NULL,
    answer_payload jsonb NOT NULL,
    is_correct boolean,
    evaluated_at timestamptz,
    CONSTRAINT quiz_answers_key UNIQUE (quiz_attempt_id, question_key),
    CONSTRAINT quiz_answers_payload_check CHECK (jsonb_typeof(answer_payload) IN ('object', 'array', 'string', 'number', 'boolean'))
);

CREATE TABLE tide.checklist_attempts (
    id uuid PRIMARY KEY,
    task_attempt_id uuid NOT NULL REFERENCES tide.task_attempts(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    checklist_version text NOT NULL,
    completed_at timestamptz,
    CONSTRAINT checklist_attempts_key UNIQUE (task_attempt_id, step_key)
);

CREATE TABLE tide.checklist_item_results (
    id uuid PRIMARY KEY,
    checklist_attempt_id uuid NOT NULL REFERENCES tide.checklist_attempts(id) ON DELETE CASCADE,
    item_key text NOT NULL,
    checked boolean NOT NULL,
    checked_at timestamptz,
    CONSTRAINT checklist_item_results_key UNIQUE (checklist_attempt_id, item_key)
);

CREATE TABLE tide.device_check_runs (
    id uuid PRIMARY KEY,
    task_attempt_id uuid NOT NULL REFERENCES tide.task_attempts(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    check_version text NOT NULL,
    status text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT device_check_runs_status_check CHECK (status IN ('RUNNING', 'PASSED', 'FAILED', 'ERROR'))
);

CREATE TABLE tide.device_check_item_results (
    id uuid PRIMARY KEY,
    device_check_run_id uuid NOT NULL REFERENCES tide.device_check_runs(id) ON DELETE CASCADE,
    item_key text NOT NULL,
    status text NOT NULL,
    measured_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    teacher_message text,
    CONSTRAINT device_check_item_results_key UNIQUE (device_check_run_id, item_key),
    CONSTRAINT device_check_item_results_status_check CHECK (status IN ('PASSED', 'FAILED', 'ERROR', 'SKIPPED')),
    CONSTRAINT device_check_item_results_summary_check CHECK (jsonb_typeof(measured_summary) = 'object')
);

CREATE TABLE tide.task_submissions (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    task_attempt_id uuid NOT NULL REFERENCES tide.task_attempts(id) ON DELETE RESTRICT,
    submission_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_status text NOT NULL DEFAULT 'PENDING',
    result_code text,
    rule_version text NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    CONSTRAINT task_submissions_payload_check CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT task_submissions_validation_check CHECK (validation_status IN ('PENDING', 'UNDER_REVIEW', 'PASSED', 'FAILED', 'ERROR'))
);

CREATE INDEX task_submissions_task_time_idx ON tide.task_submissions (teacher_task_id, submitted_at DESC);

CREATE TABLE tide.task_completions (
    id uuid PRIMARY KEY,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE RESTRICT,
    task_submission_id uuid REFERENCES tide.task_submissions(id) ON DELETE RESTRICT,
    completion_source text NOT NULL,
    result_version text NOT NULL,
    trusted_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_completions_task_key UNIQUE (teacher_task_id),
    CONSTRAINT task_completions_source_check CHECK (completion_source IN ('RULE_ENGINE', 'AI_REVIEW', 'TRUSTED_EXTERNAL', 'MANUAL_CORRECTION'))
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
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_status_events_version_key UNIQUE (teacher_task_id, state_version),
    CONSTRAINT task_status_events_actor_check CHECK (actor_type IN ('TEACHER', 'SYSTEM', 'AI', 'TRUSTED_EXTERNAL', 'ADMIN')),
    CONSTRAINT task_status_events_version_check CHECK (state_version > 0)
);

CREATE TABLE tide.file_objects (
    id uuid PRIMARY KEY,
    uploader_account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
    storage_provider text NOT NULL,
    object_key text NOT NULL,
    original_filename text NOT NULL,
    mime_type text NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 text NOT NULL,
    visibility text NOT NULL DEFAULT 'PRIVATE',
    status text NOT NULL DEFAULT 'PENDING',
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    deleted_at timestamptz,
    CONSTRAINT file_objects_provider_key UNIQUE (storage_provider, object_key),
    CONSTRAINT file_objects_provider_check CHECK (storage_provider IN ('LOCAL', 'OSS')),
    CONSTRAINT file_objects_visibility_check CHECK (visibility IN ('PRIVATE', 'PUBLIC_ASSET')),
    CONSTRAINT file_objects_status_check CHECK (status IN ('PENDING', 'READY', 'QUARANTINED', 'DELETED')),
    CONSTRAINT file_objects_size_check CHECK (size_bytes >= 0),
    CONSTRAINT file_objects_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE tide.task_template_files (
    template_version_id uuid NOT NULL REFERENCES tide.task_template_versions(id) ON DELETE CASCADE,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    purpose text NOT NULL,
    step_key text,
    position integer NOT NULL DEFAULT 1,
    PRIMARY KEY (template_version_id, file_id, purpose),
    CONSTRAINT task_template_files_position_check CHECK (position > 0)
);

CREATE TABLE tide.task_submission_files (
    submission_id uuid NOT NULL REFERENCES tide.task_submissions(id) ON DELETE CASCADE,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    purpose text NOT NULL,
    position integer NOT NULL DEFAULT 1,
    PRIMARY KEY (submission_id, file_id, purpose),
    CONSTRAINT task_submission_files_position_check CHECK (position > 0)
);

CREATE TABLE tide.file_access_events (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT file_access_events_action_check CHECK (action IN ('UPLOAD', 'PREVIEW', 'DOWNLOAD', 'DELETE')),
    CONSTRAINT file_access_events_outcome_check CHECK (outcome IN ('SUCCESS', 'DENIED', 'FAILED'))
);

CREATE TABLE tide.file_migrations (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    source_provider text NOT NULL,
    target_provider text NOT NULL,
    target_object_key text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    verified_sha256 text,
    started_at timestamptz,
    completed_at timestamptz,
    last_error_code text,
    CONSTRAINT file_migrations_target_key UNIQUE (target_provider, target_object_key),
    CONSTRAINT file_migrations_status_check CHECK (status IN ('PENDING', 'RUNNING', 'VERIFIED', 'FAILED'))
);

CREATE TABLE tide.ai_prompt_versions (
    id uuid PRIMARY KEY,
    capability text NOT NULL,
    version text NOT NULL,
    rule_version text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    prompt_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    CONSTRAINT ai_prompt_versions_key UNIQUE (capability, version),
    CONSTRAINT ai_prompt_versions_status_check CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED'))
);

CREATE TABLE tide.ai_runs (
    id uuid PRIMARY KEY,
    capability text NOT NULL,
    caller_module text NOT NULL,
    prompt_version_id uuid REFERENCES tide.ai_prompt_versions(id) ON DELETE RESTRICT,
    provider text NOT NULL,
    model text NOT NULL,
    gateway_ref text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    latency_ms integer,
    input_units integer,
    output_units integer,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT ai_runs_status_check CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT ai_runs_latency_check CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE TABLE tide.image_reviews (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE RESTRICT,
    submission_id uuid NOT NULL REFERENCES tide.task_submissions(id) ON DELETE RESTRICT,
    ai_run_id uuid REFERENCES tide.ai_runs(id) ON DELETE RESTRICT,
    criteria_version text NOT NULL,
    decision text NOT NULL,
    teacher_reason text NOT NULL,
    confidence_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT image_reviews_decision_check CHECK (decision IN ('PASS', 'RETRY', 'ERROR')),
    CONSTRAINT image_reviews_confidence_check CHECK (jsonb_typeof(confidence_summary) = 'object')
);

CREATE TABLE tide.image_review_items (
    id uuid PRIMARY KEY,
    image_review_id uuid NOT NULL REFERENCES tide.image_reviews(id) ON DELETE CASCADE,
    criterion_key text NOT NULL,
    result text NOT NULL,
    teacher_message text,
    CONSTRAINT image_review_items_key UNIQUE (image_review_id, criterion_key),
    CONSTRAINT image_review_items_result_check CHECK (result IN ('PASS', 'FAIL', 'UNKNOWN'))
);

CREATE TABLE tide.qa_conversations (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'OPEN',
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    CONSTRAINT qa_conversations_status_check CHECK (status IN ('OPEN', 'CLOSED', 'ARCHIVED'))
);

CREATE TABLE tide.qa_messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES tide.qa_conversations(id) ON DELETE CASCADE,
    role text NOT NULL,
    body text NOT NULL,
    faq_hit boolean NOT NULL DEFAULT false,
    ai_run_id uuid REFERENCES tide.ai_runs(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qa_messages_role_check CHECK (role IN ('TEACHER', 'ASSISTANT', 'SYSTEM'))
);

CREATE INDEX qa_messages_conversation_time_idx ON tide.qa_messages (conversation_id, created_at);

CREATE TABLE tide.knowledge_documents (
    id uuid PRIMARY KEY,
    document_key text NOT NULL,
    version integer NOT NULL,
    title text NOT NULL,
    language text NOT NULL DEFAULT 'en',
    authority_level text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    content_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    CONSTRAINT knowledge_documents_key UNIQUE (document_key, version),
    CONSTRAINT knowledge_documents_version_check CHECK (version > 0),
    CONSTRAINT knowledge_documents_status_check CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED'))
);

CREATE TABLE tide.knowledge_chunks (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES tide.knowledge_documents(id) ON DELETE CASCADE,
    chunk_key text NOT NULL,
    position integer NOT NULL,
    body text NOT NULL,
    retrieval_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT knowledge_chunks_key UNIQUE (document_id, chunk_key),
    CONSTRAINT knowledge_chunks_position_key UNIQUE (document_id, position),
    CONSTRAINT knowledge_chunks_position_check CHECK (position > 0),
    CONSTRAINT knowledge_chunks_metadata_check CHECK (jsonb_typeof(retrieval_metadata) = 'object')
);

CREATE TABLE tide.qa_source_links (
    message_id uuid NOT NULL REFERENCES tide.qa_messages(id) ON DELETE CASCADE,
    knowledge_chunk_id uuid NOT NULL REFERENCES tide.knowledge_chunks(id) ON DELETE RESTRICT,
    position integer NOT NULL,
    PRIMARY KEY (message_id, knowledge_chunk_id),
    CONSTRAINT qa_source_links_position_check CHECK (position > 0)
);

CREATE TABLE tide.qa_feedback (
    id uuid PRIMARY KEY,
    message_id uuid NOT NULL REFERENCES tide.qa_messages(id) ON DELETE CASCADE,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE RESTRICT,
    resolved boolean NOT NULL,
    reason_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qa_feedback_message_key UNIQUE (message_id)
);

CREATE TABLE tide.faq_gaps (
    id uuid PRIMARY KEY,
    normalized_question text NOT NULL,
    category text,
    occurrence_count integer NOT NULL DEFAULT 1,
    review_status text NOT NULL DEFAULT 'PENDING',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT faq_gaps_question_key UNIQUE (normalized_question),
    CONSTRAINT faq_gaps_count_check CHECK (occurrence_count > 0),
    CONSTRAINT faq_gaps_status_check CHECK (review_status IN ('PENDING', 'APPROVED', 'REJECTED', 'RESOLVED'))
);

CREATE TABLE tide.metric_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_system text NOT NULL DEFAULT 'SHIWEN',
    source_version text NOT NULL,
    source_updated_at timestamptz,
    data_as_of timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    public_total_score numeric(7,2),
    base_score numeric(7,2),
    dimensions jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_latest boolean NOT NULL DEFAULT true,
    CONSTRAINT metric_projections_source_key UNIQUE (teacher_binding_id, source_version),
    CONSTRAINT metric_projections_dimensions_check CHECK (jsonb_typeof(dimensions) = 'object'),
    CONSTRAINT metric_projections_payload_check CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT metric_projections_score_check CHECK (public_total_score IS NULL OR public_total_score BETWEEN 0 AND 200)
);

CREATE UNIQUE INDEX metric_projections_latest_key ON tide.metric_projections (teacher_binding_id) WHERE is_latest;

CREATE TABLE tide.course_attribution_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_lesson_id text NOT NULL,
    source_version text NOT NULL,
    lesson_started_at timestamptz,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    dimension_changes jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT course_attribution_projections_key UNIQUE (teacher_binding_id, source_lesson_id, source_version),
    CONSTRAINT course_attribution_projections_dimensions_check CHECK (jsonb_typeof(dimension_changes) = 'object'),
    CONSTRAINT course_attribution_projections_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE tide.outcome_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_version text NOT NULL,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    graduation_status text,
    probation_status text,
    excellence_status text,
    capacity_milestone_status text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_latest boolean NOT NULL DEFAULT true,
    CONSTRAINT outcome_projections_key UNIQUE (teacher_binding_id, source_version),
    CONSTRAINT outcome_projections_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE UNIQUE INDEX outcome_projections_latest_key ON tide.outcome_projections (teacher_binding_id) WHERE is_latest;

CREATE TABLE tide.camp_enrollment_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    camp_enrollment_id text NOT NULL,
    source_version text NOT NULL,
    enrollment_status text,
    started_at timestamptz,
    ends_at timestamptz,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT camp_enrollment_projections_key UNIQUE (teacher_binding_id, camp_enrollment_id, source_version),
    CONSTRAINT camp_enrollment_projections_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE tide.message_projections (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_notification_id text NOT NULL,
    source_version text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    related_task_code text,
    expires_at timestamptz,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT message_projections_key UNIQUE (teacher_binding_id, source_notification_id, source_version),
    CONSTRAINT message_projections_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE tide.message_reads (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid NOT NULL REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_notification_id text NOT NULL,
    first_opened_at timestamptz NOT NULL,
    last_opened_at timestamptz NOT NULL,
    open_count integer NOT NULL DEFAULT 1,
    CONSTRAINT message_reads_key UNIQUE (teacher_binding_id, source_notification_id),
    CONSTRAINT message_reads_count_check CHECK (open_count > 0),
    CONSTRAINT message_reads_time_check CHECK (last_opened_at >= first_opened_at)
);

CREATE TABLE tide.source_read_status (
    id uuid PRIMARY KEY,
    teacher_binding_id uuid REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
    source_system text NOT NULL,
    projection_type text NOT NULL,
    last_success_at timestamptz,
    last_failure_at timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_read_status_key UNIQUE NULLS NOT DISTINCT (teacher_binding_id, source_system, projection_type),
    CONSTRAINT source_read_status_failure_check CHECK (consecutive_failures >= 0)
);

CREATE TABLE tide.integration_inbox_receipts (
    id uuid PRIMARY KEY,
    source_system text NOT NULL,
    request_id text NOT NULL,
    assignment_id text,
    payload_hash text NOT NULL,
    processing_status text NOT NULL DEFAULT 'RECEIVED',
    response_status integer,
    response_body jsonb,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT integration_inbox_receipts_request_key UNIQUE (source_system, request_id),
    CONSTRAINT integration_inbox_receipts_status_check CHECK (processing_status IN ('RECEIVED', 'ACCEPTED', 'REJECTED', 'FAILED')),
    CONSTRAINT integration_inbox_receipts_response_check CHECK (response_body IS NULL OR jsonb_typeof(response_body) = 'object')
);

CREATE TABLE tide.integration_events (
    id uuid PRIMARY KEY,
    provider_event_id text NOT NULL,
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
    payload jsonb NOT NULL,
    payload_hash text NOT NULL,
    data_origin text NOT NULL DEFAULT 'REAL',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT integration_events_provider_key UNIQUE (provider_event_id),
    CONSTRAINT integration_events_type_check CHECK (event_type IN ('PERSONALIZED_STATUS', 'FIXED_TASK_COMPLETED')),
    CONSTRAINT integration_events_payload_check CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT integration_events_origin_check CHECK (data_origin IN ('REAL', 'MOCK')),
    CONSTRAINT integration_events_shape_check CHECK (
        (event_type = 'PERSONALIZED_STATUS' AND assignment_id IS NOT NULL AND status IS NOT NULL AND sequence IS NOT NULL AND teacher_id IS NULL)
        OR
        (event_type = 'FIXED_TASK_COMPLETED' AND assignment_id IS NULL AND status IS NULL AND sequence IS NULL AND teacher_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX integration_events_assignment_sequence_key ON tide.integration_events (assignment_id, sequence) WHERE event_type = 'PERSONALIZED_STATUS';
CREATE UNIQUE INDEX integration_events_fixed_completion_key ON tide.integration_events (teacher_id, task_code) WHERE event_type = 'FIXED_TASK_COMPLETED' AND data_origin = 'REAL';

CREATE FUNCTION tide.enforce_integration_event_origin()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    task_origin text;
BEGIN
    SELECT data_origin INTO task_origin
    FROM tide.teacher_tasks
    WHERE id = NEW.teacher_task_id;

    IF task_origin IS DISTINCT FROM NEW.data_origin THEN
        RAISE EXCEPTION 'integration event origin must match teacher task origin';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER integration_events_origin_guard
BEFORE INSERT OR UPDATE OF teacher_task_id, data_origin
ON tide.integration_events
FOR EACH ROW
EXECUTE FUNCTION tide.enforce_integration_event_origin();

CREATE TABLE tide.outbox_deliveries (
    id uuid PRIMARY KEY,
    integration_event_id uuid NOT NULL REFERENCES tide.integration_events(id) ON DELETE RESTRICT,
    target_system text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    attempt_count integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    lease_owner text,
    lease_expires_at timestamptz,
    last_http_status integer,
    last_error_code text,
    last_response_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    CONSTRAINT outbox_deliveries_event_target_key UNIQUE (integration_event_id, target_system),
    CONSTRAINT outbox_deliveries_status_check CHECK (status IN ('PENDING', 'PROCESSING', 'DELIVERED', 'RETRY', 'DEAD')),
    CONSTRAINT outbox_deliveries_attempt_check CHECK (attempt_count >= 0)
);

CREATE FUNCTION tide.enforce_outbox_target()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_origin text;
BEGIN
    SELECT data_origin INTO event_origin
    FROM tide.integration_events
    WHERE id = NEW.integration_event_id;

    IF event_origin = 'MOCK' AND NEW.target_system <> 'SHIWEN_MOCK' THEN
        RAISE EXCEPTION 'mock integration events can only target SHIWEN_MOCK';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER outbox_deliveries_target_guard
BEFORE INSERT OR UPDATE OF integration_event_id, target_system
ON tide.outbox_deliveries
FOR EACH ROW
EXECUTE FUNCTION tide.enforce_outbox_target();

CREATE INDEX outbox_deliveries_ready_idx ON tide.outbox_deliveries (next_attempt_at, created_at) WHERE status IN ('PENDING', 'RETRY');

CREATE TABLE tide.external_receipts (
    id uuid PRIMARY KEY,
    integration_event_id uuid NOT NULL REFERENCES tide.integration_events(id) ON DELETE RESTRICT,
    provider_event_id text NOT NULL,
    accepted boolean NOT NULL,
    external_status text,
    latest_sequence bigint,
    response_hash text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_receipts_event_key UNIQUE (integration_event_id),
    CONSTRAINT external_receipts_provider_key UNIQUE (provider_event_id)
);

CREATE TABLE tide.dead_letters (
    id uuid PRIMARY KEY,
    outbox_delivery_id uuid NOT NULL REFERENCES tide.outbox_deliveries(id) ON DELETE RESTRICT,
    final_error_code text NOT NULL,
    final_error_summary text,
    resolution_status text NOT NULL DEFAULT 'OPEN',
    resolved_by text,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dead_letters_delivery_key UNIQUE (outbox_delivery_id),
    CONSTRAINT dead_letters_status_check CHECK (resolution_status IN ('OPEN', 'RETRY_APPROVED', 'RESOLVED', 'IGNORED'))
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
    CONSTRAINT audit_events_actor_check CHECK (actor_type IN ('TEACHER', 'SYSTEM', 'ADMIN', 'INTEGRATION')),
    CONSTRAINT audit_events_outcome_check CHECK (outcome IN ('SUCCESS', 'FAILURE', 'DENIED')),
    CONSTRAINT audit_events_metadata_check CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX audit_events_resource_time_idx ON tide.audit_events (resource_type, resource_id, occurred_at DESC);

COMMIT;
