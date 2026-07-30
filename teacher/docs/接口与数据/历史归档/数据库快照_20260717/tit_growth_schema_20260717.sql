--
-- PostgreSQL database dump
--

-- Dumped from database version 16.14 (Homebrew)
-- Dumped by pg_dump version 16.14 (Homebrew)
-- TIT Growth v0.2 schema-only export; contains no business data or credentials.
-- Designed for PostgreSQL 16 and can be executed from psql, DBeaver, or DataGrip.

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_decisions (
    plan_id character varying(128) NOT NULL,
    plan_key character varying(512) NOT NULL,
    route character varying(24) NOT NULL,
    planner character varying(64) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    constraints jsonb NOT NULL,
    selected_template_ids jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_events (
    sequence integer NOT NULL,
    event_id character varying(128) NOT NULL,
    event_type character varying(128) NOT NULL,
    teacher_id character varying(64),
    task_id character varying(128),
    case_id character varying(128),
    occurred_at timestamp with time zone NOT NULL,
    actor_type character varying(32) NOT NULL,
    payload_hash character varying(64) NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: audit_events_sequence_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_events_sequence_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_events_sequence_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_events_sequence_seq OWNED BY public.audit_events.sequence;


--
-- Name: config_publication_audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_publication_audits (
    audit_id character varying(128) NOT NULL,
    version_id character varying(128) NOT NULL,
    config_key character varying(64) NOT NULL,
    action character varying(32) NOT NULL,
    actor_id character varying(128) NOT NULL,
    from_status character varying(24),
    to_status character varying(24) NOT NULL,
    payload_hash character varying(64) NOT NULL,
    detail text NOT NULL,
    occurred_at timestamp with time zone NOT NULL
);


--
-- Name: config_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_versions (
    version_id character varying(128) NOT NULL,
    config_key character varying(64) NOT NULL,
    version_number integer NOT NULL,
    status character varying(24) NOT NULL,
    high_impact boolean NOT NULL,
    payload jsonb NOT NULL,
    validation_errors jsonb NOT NULL,
    source_version_id character varying(128),
    created_by character varying(128) NOT NULL,
    updated_by character varying(128) NOT NULL,
    validated_by character varying(128),
    published_by character varying(128),
    retired_by character varying(128),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    validated_at timestamp with time zone,
    published_at timestamp with time zone,
    retired_at timestamp with time zone,
    CONSTRAINT ck_config_versions_key CHECK (((config_key)::text = ANY ((ARRAY['SCORE_GRADUATION'::character varying, 'AGENT_POLICY'::character varying, 'DELIVERY_POLICY'::character varying])::text[]))),
    CONSTRAINT ck_config_versions_status CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'VALIDATED'::character varying, 'PUBLISHED'::character varying, 'RETIRED'::character varying])::text[])))
);


--
-- Name: idempotency_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_records (
    scope character varying(48) NOT NULL,
    idempotency_key character varying(256) NOT NULL,
    request_hash character varying(64) NOT NULL,
    resource_id character varying(128),
    response_payload jsonb,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone
);


--
-- Name: lesson_dimension_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lesson_dimension_scores (
    score_state_id character varying(256) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    lesson_id character varying(128) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    dimension character varying(32) NOT NULL,
    current_score double precision NOT NULL,
    evidence_status character varying(32) NOT NULL,
    evidence_coverage character varying(32),
    score_rule_version character varying(64) NOT NULL,
    current_revision integer NOT NULL,
    score_as_of timestamp with time zone,
    last_score_entry_id character varying(128),
    payload jsonb NOT NULL
);


--
-- Name: lesson_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lesson_facts (
    lesson_id character varying(128) NOT NULL,
    source_appoint_id character varying(128) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    scheduled_start_at timestamp with time zone,
    scheduled_end_at timestamp with time zone,
    lesson_lifecycle_status character varying(48) NOT NULL,
    valid_for_scoring boolean NOT NULL,
    evidence_status character varying(32) NOT NULL,
    data_mode character varying(16) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: notification_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_events (
    notification_event_id character varying(128) NOT NULL,
    notification_id character varying(128) NOT NULL,
    delivery_status character varying(24) NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    failure_reason text,
    request_hash character varying(64) NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    notification_id character varying(128) NOT NULL,
    task_id character varying(128) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    channel character varying(32) NOT NULL,
    priority character varying(8) NOT NULL,
    status character varying(32) NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    stored_at timestamp with time zone,
    read_at timestamp with time zone,
    clicked_at timestamp with time zone,
    response_due_at timestamp with time zone,
    failure_reason text,
    payload jsonb NOT NULL
);


--
-- Name: operator_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operator_accounts (
    operator_id character varying(36) NOT NULL,
    username character varying(128) NOT NULL,
    display_name character varying(255),
    password_hash character varying(512) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: operator_role_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operator_role_grants (
    grant_id character varying(36) NOT NULL,
    operator_id character varying(36) NOT NULL,
    role character varying(48) NOT NULL,
    granted_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT ck_operator_role_valid CHECK (((role)::text = ANY ((ARRAY['VIEWER'::character varying, 'CASE_OPERATOR'::character varying, 'SENIOR_REVIEWER'::character varying, 'CONFIG_PUBLISHER'::character varying, 'EXTERNAL_ACTION_APPROVER'::character varying, 'AUDITOR'::character varying])::text[])))
);


--
-- Name: operator_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operator_sessions (
    session_id character varying(36) NOT NULL,
    operator_id character varying(36) NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: ops_cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ops_cases (
    case_id character varying(128) NOT NULL,
    case_type character varying(64) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    task_id character varying(128),
    priority character varying(8) NOT NULL,
    status character varying(32) NOT NULL,
    source_reason character varying(64),
    external_action_status character varying(48) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: ops_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ops_decisions (
    decision_id character varying(160) NOT NULL,
    case_id character varying(128) NOT NULL,
    decision character varying(64) NOT NULL,
    note text NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    actor_type character varying(24) NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: outbound_outputs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_outputs (
    output_id character varying(128) NOT NULL,
    output_type character varying(32) NOT NULL,
    display_type character varying(40) NOT NULL,
    delivery_kind character varying(40),
    audience_type character varying(32) NOT NULL,
    recipient_id character varying(128),
    recipient_name character varying(255),
    channel character varying(40),
    source_type character varying(48) NOT NULL,
    source_id character varying(128) NOT NULL,
    teacher_id character varying(64),
    task_id character varying(128),
    case_id character varying(128),
    status character varying(32) NOT NULL,
    title character varying(500) NOT NULL,
    body text NOT NULL,
    scheduled_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    sent_at timestamp with time zone,
    delivered_at timestamp with time zone,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    next_retry_at timestamp with time zone,
    last_error text,
    retryable boolean NOT NULL,
    requires_human_approval boolean NOT NULL,
    payload jsonb NOT NULL,
    idempotency_key character varying(256) NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: outbox_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_events (
    outbox_id character varying(128) NOT NULL,
    event_id character varying(128) NOT NULL,
    aggregate_type character varying(48) NOT NULL,
    aggregate_id character varying(128) NOT NULL,
    event_type character varying(128) NOT NULL,
    payload jsonb NOT NULL,
    status character varying(24) NOT NULL,
    available_at timestamp with time zone NOT NULL,
    attempt_count integer NOT NULL,
    last_error text,
    created_at timestamp with time zone NOT NULL,
    published_at timestamp with time zone
);


--
-- Name: provider_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_calls (
    provider_call_id character varying(128) NOT NULL,
    provider_event_id character varying(128),
    task_id character varying(128) NOT NULL,
    provider_id character varying(128) NOT NULL,
    call_type character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    request_payload jsonb NOT NULL,
    result_payload jsonb,
    created_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: score_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.score_accounts (
    account_id character varying(160) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    dimension character varying(32) NOT NULL,
    current_score double precision NOT NULL,
    minimum_score double precision NOT NULL,
    weight double precision NOT NULL,
    score_rule_version character varying(64) NOT NULL,
    version integer NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: score_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.score_entries (
    score_entry_id character varying(128) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    lesson_id character varying(128),
    teacher_id character varying(64) NOT NULL,
    dimension character varying(32) NOT NULL,
    entry_type character varying(32) NOT NULL,
    delta_score double precision NOT NULL,
    reason_code character varying(128) NOT NULL,
    evidence_status character varying(32) NOT NULL,
    score_rule_version character varying(64) NOT NULL,
    occurred_at timestamp with time zone,
    recorded_at timestamp with time zone NOT NULL,
    reversal_of_score_entry_id character varying(128),
    idempotency_key character varying(256) NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: task_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_assignments (
    task_id character varying(128) NOT NULL,
    obligation_id character varying(128) NOT NULL,
    teacher_id character varying(64) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    template_id character varying(64) NOT NULL,
    template_version integer NOT NULL,
    assignment_revision integer NOT NULL,
    execution_contract_version integer NOT NULL,
    assignment_status character varying(32) NOT NULL,
    priority character varying(8) NOT NULL,
    display_rank integer NOT NULL,
    is_primary boolean NOT NULL,
    assigned_at timestamp with time zone,
    original_due_at timestamp with time zone,
    due_at timestamp with time zone,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: task_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_executions (
    task_id character varying(128) NOT NULL,
    runtime_status character varying(32) NOT NULL,
    verification_result character varying(32),
    runtime_sequence integer NOT NULL,
    due_status character varying(16) NOT NULL,
    attempt_no integer NOT NULL,
    last_event_at timestamp with time zone,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: task_runtime_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_runtime_events (
    event_id character varying(128) NOT NULL,
    task_id character varying(128) NOT NULL,
    execution_contract_version integer NOT NULL,
    runtime_sequence integer NOT NULL,
    runtime_event_code character varying(48) NOT NULL,
    runtime_status character varying(32) NOT NULL,
    verification_result character varying(32),
    provider_event_id character varying(128),
    occurred_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: task_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_templates (
    row_id character varying(160) NOT NULL,
    template_id character varying(64) NOT NULL,
    template_version integer NOT NULL,
    publish_status character varying(24) NOT NULL,
    output_type character varying(32) NOT NULL,
    task_category character varying(48) NOT NULL,
    audience character varying(24) NOT NULL,
    dimension character varying(32) NOT NULL,
    completion_method character varying(32) NOT NULL,
    verification_mode character varying(32) NOT NULL,
    action_schema jsonb NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: teachers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teachers (
    teacher_id character varying(64) NOT NULL,
    camp_enrollment_id character varying(96) NOT NULL,
    name character varying(255) NOT NULL,
    country character varying(128),
    timezone character varying(64) NOT NULL,
    camp_day integer NOT NULL,
    graduation_state character varying(32) NOT NULL,
    total_score double precision NOT NULL,
    graduation_threshold double precision NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: audit_events sequence; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events ALTER COLUMN sequence SET DEFAULT nextval('public.audit_events_sequence_seq'::regclass);


--
-- Name: agent_decisions agent_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_decisions
    ADD CONSTRAINT agent_decisions_pkey PRIMARY KEY (plan_id);


--
-- Name: agent_decisions agent_decisions_plan_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_decisions
    ADD CONSTRAINT agent_decisions_plan_key_key UNIQUE (plan_key);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_events audit_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_event_id_key UNIQUE (event_id);


--
-- Name: audit_events audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (sequence);


--
-- Name: config_publication_audits config_publication_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_publication_audits
    ADD CONSTRAINT config_publication_audits_pkey PRIMARY KEY (audit_id);


--
-- Name: config_versions config_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_pkey PRIMARY KEY (version_id);


--
-- Name: idempotency_records idempotency_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_pkey PRIMARY KEY (scope, idempotency_key);


--
-- Name: lesson_dimension_scores lesson_dimension_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_dimension_scores
    ADD CONSTRAINT lesson_dimension_scores_pkey PRIMARY KEY (score_state_id);


--
-- Name: lesson_facts lesson_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_facts
    ADD CONSTRAINT lesson_facts_pkey PRIMARY KEY (lesson_id);


--
-- Name: notification_events notification_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_pkey PRIMARY KEY (notification_event_id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (notification_id);


--
-- Name: notifications notifications_task_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_task_id_key UNIQUE (task_id);


--
-- Name: operator_accounts operator_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_accounts
    ADD CONSTRAINT operator_accounts_pkey PRIMARY KEY (operator_id);


--
-- Name: operator_role_grants operator_role_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_role_grants
    ADD CONSTRAINT operator_role_grants_pkey PRIMARY KEY (grant_id);


--
-- Name: operator_sessions operator_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_sessions
    ADD CONSTRAINT operator_sessions_pkey PRIMARY KEY (session_id);


--
-- Name: ops_cases ops_cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_cases
    ADD CONSTRAINT ops_cases_pkey PRIMARY KEY (case_id);


--
-- Name: ops_decisions ops_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_decisions
    ADD CONSTRAINT ops_decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: outbound_outputs outbound_outputs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_outputs
    ADD CONSTRAINT outbound_outputs_pkey PRIMARY KEY (output_id);


--
-- Name: outbox_events outbox_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_event_id_key UNIQUE (event_id);


--
-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (outbox_id);


--
-- Name: provider_calls provider_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_calls
    ADD CONSTRAINT provider_calls_pkey PRIMARY KEY (provider_call_id);


--
-- Name: provider_calls provider_calls_provider_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_calls
    ADD CONSTRAINT provider_calls_provider_event_id_key UNIQUE (provider_event_id);


--
-- Name: score_accounts score_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_accounts
    ADD CONSTRAINT score_accounts_pkey PRIMARY KEY (account_id);


--
-- Name: score_entries score_entries_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_entries
    ADD CONSTRAINT score_entries_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: score_entries score_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_entries
    ADD CONSTRAINT score_entries_pkey PRIMARY KEY (score_entry_id);


--
-- Name: task_assignments task_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_assignments
    ADD CONSTRAINT task_assignments_pkey PRIMARY KEY (task_id);


--
-- Name: task_executions task_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_executions
    ADD CONSTRAINT task_executions_pkey PRIMARY KEY (task_id);


--
-- Name: task_runtime_events task_runtime_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_runtime_events
    ADD CONSTRAINT task_runtime_events_pkey PRIMARY KEY (event_id);


--
-- Name: task_templates task_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT task_templates_pkey PRIMARY KEY (row_id);


--
-- Name: teachers teachers_camp_enrollment_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_camp_enrollment_id_key UNIQUE (camp_enrollment_id);


--
-- Name: teachers teachers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_pkey PRIMARY KEY (teacher_id);


--
-- Name: config_versions uq_config_version_number; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT uq_config_version_number UNIQUE (config_key, version_number);


--
-- Name: lesson_dimension_scores uq_lesson_dimension_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_dimension_scores
    ADD CONSTRAINT uq_lesson_dimension_state UNIQUE (camp_enrollment_id, lesson_id, dimension);


--
-- Name: operator_role_grants uq_operator_role_grant; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_role_grants
    ADD CONSTRAINT uq_operator_role_grant UNIQUE (operator_id, role);


--
-- Name: outbound_outputs uq_outbound_output_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_outputs
    ADD CONSTRAINT uq_outbound_output_idempotency UNIQUE (idempotency_key);


--
-- Name: score_accounts uq_score_account_teacher_dimension; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_accounts
    ADD CONSTRAINT uq_score_account_teacher_dimension UNIQUE (teacher_id, dimension);


--
-- Name: task_runtime_events uq_task_runtime_sequence; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_runtime_events
    ADD CONSTRAINT uq_task_runtime_sequence UNIQUE (task_id, runtime_sequence);


--
-- Name: task_templates uq_task_template_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT uq_task_template_version UNIQUE (template_id, template_version);


--
-- Name: ix_agent_decisions_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_decisions_teacher_id ON public.agent_decisions USING btree (teacher_id);


--
-- Name: ix_audit_events_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_events_case_id ON public.audit_events USING btree (case_id);


--
-- Name: ix_audit_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_events_event_type ON public.audit_events USING btree (event_type);


--
-- Name: ix_audit_events_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_events_task_id ON public.audit_events USING btree (task_id);


--
-- Name: ix_audit_events_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_events_teacher_id ON public.audit_events USING btree (teacher_id);


--
-- Name: ix_config_audit_version_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_audit_version_time ON public.config_publication_audits USING btree (version_id, occurred_at);


--
-- Name: ix_config_key_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_key_status ON public.config_versions USING btree (config_key, status);


--
-- Name: ix_config_publication_audits_config_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_publication_audits_config_key ON public.config_publication_audits USING btree (config_key);


--
-- Name: ix_config_publication_audits_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_publication_audits_version_id ON public.config_publication_audits USING btree (version_id);


--
-- Name: ix_config_versions_config_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_versions_config_key ON public.config_versions USING btree (config_key);


--
-- Name: ix_config_versions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_config_versions_status ON public.config_versions USING btree (status);


--
-- Name: ix_lesson_dimension_scores_camp_enrollment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lesson_dimension_scores_camp_enrollment_id ON public.lesson_dimension_scores USING btree (camp_enrollment_id);


--
-- Name: ix_lesson_dimension_scores_lesson_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lesson_dimension_scores_lesson_id ON public.lesson_dimension_scores USING btree (lesson_id);


--
-- Name: ix_lesson_dimension_scores_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lesson_dimension_scores_teacher_id ON public.lesson_dimension_scores USING btree (teacher_id);


--
-- Name: ix_lesson_facts_camp_enrollment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lesson_facts_camp_enrollment_id ON public.lesson_facts USING btree (camp_enrollment_id);


--
-- Name: ix_lesson_facts_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lesson_facts_teacher_id ON public.lesson_facts USING btree (teacher_id);


--
-- Name: ix_notification_events_notification_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_events_notification_id ON public.notification_events USING btree (notification_id);


--
-- Name: ix_notifications_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_status ON public.notifications USING btree (status);


--
-- Name: ix_notifications_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_teacher_id ON public.notifications USING btree (teacher_id);


--
-- Name: ix_operator_accounts_username; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_operator_accounts_username ON public.operator_accounts USING btree (username);


--
-- Name: ix_operator_role_grants_operator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_role_grants_operator_id ON public.operator_role_grants USING btree (operator_id);


--
-- Name: ix_operator_role_grants_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_role_grants_role ON public.operator_role_grants USING btree (role);


--
-- Name: ix_operator_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_sessions_expires_at ON public.operator_sessions USING btree (expires_at);


--
-- Name: ix_operator_sessions_operator_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_sessions_operator_active ON public.operator_sessions USING btree (operator_id, revoked_at, expires_at);


--
-- Name: ix_operator_sessions_operator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_sessions_operator_id ON public.operator_sessions USING btree (operator_id);


--
-- Name: ix_operator_sessions_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_operator_sessions_token_hash ON public.operator_sessions USING btree (token_hash);


--
-- Name: ix_ops_cases_case_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ops_cases_case_type ON public.ops_cases USING btree (case_type);


--
-- Name: ix_ops_cases_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ops_cases_status ON public.ops_cases USING btree (status);


--
-- Name: ix_ops_cases_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ops_cases_task_id ON public.ops_cases USING btree (task_id);


--
-- Name: ix_ops_cases_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ops_cases_teacher_id ON public.ops_cases USING btree (teacher_id);


--
-- Name: ix_ops_decisions_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ops_decisions_case_id ON public.ops_decisions USING btree (case_id);


--
-- Name: ix_outbound_outputs_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_case_id ON public.outbound_outputs USING btree (case_id);


--
-- Name: ix_outbound_outputs_display_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_display_type ON public.outbound_outputs USING btree (display_type);


--
-- Name: ix_outbound_outputs_output_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_output_type ON public.outbound_outputs USING btree (output_type);


--
-- Name: ix_outbound_outputs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_status ON public.outbound_outputs USING btree (status);


--
-- Name: ix_outbound_outputs_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_task_id ON public.outbound_outputs USING btree (task_id);


--
-- Name: ix_outbound_outputs_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbound_outputs_teacher_id ON public.outbound_outputs USING btree (teacher_id);


--
-- Name: ix_outbox_events_aggregate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_events_aggregate_id ON public.outbox_events USING btree (aggregate_id);


--
-- Name: ix_outbox_events_aggregate_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_events_aggregate_type ON public.outbox_events USING btree (aggregate_type);


--
-- Name: ix_outbox_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_events_event_type ON public.outbox_events USING btree (event_type);


--
-- Name: ix_outbox_events_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_events_status ON public.outbox_events USING btree (status);


--
-- Name: ix_outputs_type_status_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outputs_type_status_teacher ON public.outbound_outputs USING btree (output_type, status, teacher_id);


--
-- Name: ix_provider_calls_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_provider_calls_task_id ON public.provider_calls USING btree (task_id);


--
-- Name: ix_score_accounts_camp_enrollment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_score_accounts_camp_enrollment_id ON public.score_accounts USING btree (camp_enrollment_id);


--
-- Name: ix_score_accounts_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_score_accounts_teacher_id ON public.score_accounts USING btree (teacher_id);


--
-- Name: ix_score_entries_camp_enrollment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_score_entries_camp_enrollment_id ON public.score_entries USING btree (camp_enrollment_id);


--
-- Name: ix_score_entries_lesson_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_score_entries_lesson_id ON public.score_entries USING btree (lesson_id);


--
-- Name: ix_score_entries_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_score_entries_teacher_id ON public.score_entries USING btree (teacher_id);


--
-- Name: ix_task_assignments_assignment_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_assignment_status ON public.task_assignments USING btree (assignment_status);


--
-- Name: ix_task_assignments_camp_enrollment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_camp_enrollment_id ON public.task_assignments USING btree (camp_enrollment_id);


--
-- Name: ix_task_assignments_obligation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_obligation_id ON public.task_assignments USING btree (obligation_id);


--
-- Name: ix_task_assignments_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_priority ON public.task_assignments USING btree (priority);


--
-- Name: ix_task_assignments_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_teacher_id ON public.task_assignments USING btree (teacher_id);


--
-- Name: ix_task_assignments_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_assignments_template_id ON public.task_assignments USING btree (template_id);


--
-- Name: ix_task_executions_runtime_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_executions_runtime_status ON public.task_executions USING btree (runtime_status);


--
-- Name: ix_task_runtime_events_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_runtime_events_task_id ON public.task_runtime_events USING btree (task_id);


--
-- Name: ix_task_templates_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_templates_template_id ON public.task_templates USING btree (template_id);


--
-- Name: uq_one_published_config_per_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_published_config_per_key ON public.config_versions USING btree (config_key) WHERE ((status)::text = 'PUBLISHED'::text);


--
-- Name: config_publication_audits config_publication_audits_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_publication_audits
    ADD CONSTRAINT config_publication_audits_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.config_versions(version_id);


--
-- Name: config_versions config_versions_source_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_source_version_id_fkey FOREIGN KEY (source_version_id) REFERENCES public.config_versions(version_id);


--
-- Name: lesson_dimension_scores lesson_dimension_scores_lesson_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_dimension_scores
    ADD CONSTRAINT lesson_dimension_scores_lesson_id_fkey FOREIGN KEY (lesson_id) REFERENCES public.lesson_facts(lesson_id);


--
-- Name: lesson_dimension_scores lesson_dimension_scores_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_dimension_scores
    ADD CONSTRAINT lesson_dimension_scores_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: lesson_facts lesson_facts_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lesson_facts
    ADD CONSTRAINT lesson_facts_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: notification_events notification_events_notification_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.notifications(notification_id);


--
-- Name: notifications notifications_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_assignments(task_id);


--
-- Name: notifications notifications_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: operator_role_grants operator_role_grants_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_role_grants
    ADD CONSTRAINT operator_role_grants_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operator_accounts(operator_id) ON DELETE CASCADE;


--
-- Name: operator_sessions operator_sessions_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_sessions
    ADD CONSTRAINT operator_sessions_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operator_accounts(operator_id) ON DELETE CASCADE;


--
-- Name: ops_cases ops_cases_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_cases
    ADD CONSTRAINT ops_cases_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: ops_decisions ops_decisions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_decisions
    ADD CONSTRAINT ops_decisions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.ops_cases(case_id);


--
-- Name: score_accounts score_accounts_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_accounts
    ADD CONSTRAINT score_accounts_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: score_entries score_entries_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_entries
    ADD CONSTRAINT score_entries_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: task_assignments task_assignments_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_assignments
    ADD CONSTRAINT task_assignments_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(teacher_id);


--
-- Name: task_executions task_executions_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_executions
    ADD CONSTRAINT task_executions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_assignments(task_id);


--
-- Name: task_runtime_events task_runtime_events_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_runtime_events
    ADD CONSTRAINT task_runtime_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_assignments(task_id);


--
-- PostgreSQL database dump complete
--

