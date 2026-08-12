-- Local-only fixture for the shared TIT tables.
-- The real shared database owns these public tables; application migrations must
-- never recreate or alter them in production.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
        CREATE ROLE tit_teacher_crud NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
        CREATE ROLE tit_growth_app NOLOGIN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'tide_support_ticket_owner'
    ) THEN
        CREATE ROLE tide_support_ticket_owner
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tide_sys_admin') THEN
        CREATE ROLE tide_sys_admin
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

GRANT tide_support_ticket_owner TO tide_sys_admin;

CREATE TABLE IF NOT EXISTS public.teachers (
    teacher_id varchar PRIMARY KEY,
    camp_enrollment_id varchar NOT NULL UNIQUE,
    name varchar NOT NULL,
    country varchar,
    timezone varchar NOT NULL DEFAULT 'Asia/Shanghai',
    camp_day integer NOT NULL DEFAULT 1,
    graduation_state varchar NOT NULL DEFAULT 'IN_PROGRESS',
    total_score double precision NOT NULL DEFAULT 0,
    graduation_threshold double precision NOT NULL DEFAULT 100,
    data_mode varchar NOT NULL DEFAULT 'MOCK',
    source_snapshot_label varchar,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.teacher_metric_snapshots (
    snapshot_id varchar PRIMARY KEY,
    batch_id varchar NOT NULL,
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    snapshot_label varchar NOT NULL,
    source_row_number integer NOT NULL,
    data_mode varchar NOT NULL DEFAULT 'MIXED',
    is_cpl_tesol boolean,
    is_self_introduce boolean,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teacher_metric_snapshots_batch_teacher_key UNIQUE (batch_id, teacher_id)
);

-- Minimal local/test subset of the TiDe-owned 55-column teacher source table.
-- Production structure is owned exclusively by the root Alembic chain.
CREATE TABLE IF NOT EXISTS public.teacher_source_wide (
    tchr_id varchar(64) PRIMARY KEY,
    real_name text,
    is_cpl_tesol boolean,
    is_self_introduce boolean
);

CREATE OR REPLACE VIEW public.teacher_g01_status_current AS
SELECT tchr_id, is_cpl_tesol
FROM public.teacher_source_wide;
REVOKE ALL ON public.teacher_g01_status_current FROM PUBLIC;

CREATE TABLE IF NOT EXISTS public.lesson_facts (
    lesson_id varchar PRIMARY KEY,
    source_appoint_id varchar NOT NULL UNIQUE,
    camp_enrollment_id varchar NOT NULL,
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    scheduled_start_at timestamptz,
    scheduled_end_at timestamptz,
    lesson_local_date date,
    lesson_local_time time,
    lesson_lifecycle_status varchar NOT NULL,
    valid_for_scoring boolean NOT NULL DEFAULT true,
    evidence_status varchar NOT NULL DEFAULT 'CONFIRMED',
    data_mode varchar NOT NULL DEFAULT 'MOCK',
    student_id_hash varchar,
    is_late boolean,
    is_early boolean,
    is_false_early_leave boolean,
    has_positive_feedback_tag boolean,
    is_favorited boolean,
    is_rebooked boolean,
    is_camera_off boolean,
    is_cpu_usage_high boolean,
    is_network_delay_high boolean,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.lesson_dimension_scores (
    score_state_id varchar PRIMARY KEY,
    camp_enrollment_id varchar NOT NULL,
    lesson_id varchar NOT NULL REFERENCES public.lesson_facts(lesson_id),
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    dimension varchar NOT NULL,
    current_score double precision NOT NULL,
    evidence_status varchar NOT NULL,
    evidence_coverage varchar,
    score_rule_version varchar NOT NULL,
    current_revision integer NOT NULL,
    score_as_of timestamptz,
    last_score_entry_id varchar,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.config_versions (
    version_id varchar PRIMARY KEY,
    config_key varchar NOT NULL,
    version_number integer NOT NULL,
    status varchar NOT NULL,
    high_impact boolean NOT NULL DEFAULT true,
    payload jsonb NOT NULL,
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_version_id varchar REFERENCES public.config_versions(version_id),
    created_by varchar NOT NULL,
    updated_by varchar NOT NULL,
    validated_by varchar,
    published_by varchar,
    retired_by varchar,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    published_at timestamptz,
    retired_at timestamptz,
    CONSTRAINT config_versions_number_key UNIQUE (config_key, version_number),
    CONSTRAINT config_versions_key_check CHECK (
        config_key IN ('SCORE_GRADUATION', 'AGENT_POLICY', 'DELIVERY_POLICY')
    ),
    CONSTRAINT config_versions_status_check CHECK (
        status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'RETIRED')
    )
);

CREATE TABLE IF NOT EXISTS public.task_templates (
    row_id varchar PRIMARY KEY,
    template_id varchar NOT NULL,
    template_version integer NOT NULL,
    status varchar NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    output_type varchar NOT NULL DEFAULT 'TEACHER_TASK',
    execution_owner varchar NOT NULL DEFAULT 'TEACHER_APP',
    external_task_template_code varchar NOT NULL,
    source_mode varchar NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by varchar NOT NULL,
    updated_by varchar NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    integration_mode varchar NOT NULL DEFAULT 'INBOUND_STATUS_ONLY',
    CONSTRAINT task_templates_version_key UNIQUE (template_id, template_version),
    CONSTRAINT task_templates_status_check CHECK (status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT task_templates_source_mode_check CHECK (source_mode IN ('REAL', 'DERIVED_REAL', 'MOCK', 'MOCK_SIMULATION', 'MOCK_PROXY')),
    CONSTRAINT task_templates_owner_check CHECK (execution_owner = 'TEACHER_APP'),
    CONSTRAINT task_templates_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS public.task_assignments (
    assignment_id varchar PRIMARY KEY DEFAULT gen_random_uuid()::text,
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    task_code varchar NOT NULL,
    template_version_id varchar NOT NULL REFERENCES public.task_templates(row_id),
    task_kind varchar NOT NULL,
    creator_system varchar NOT NULL,
    status varchar NOT NULL DEFAULT 'ASSIGNED',
    priority varchar NOT NULL,
    why text NOT NULL,
    due_at timestamptz,
    timezone_used varchar,
    timezone_source varchar,
    timezone_verified_at timestamptz,
    status_reason_code varchar,
    source_mode varchar NOT NULL,
    dedupe_key varchar NOT NULL UNIQUE,
    created_by varchar NOT NULL DEFAULT current_user,
    updated_by varchar NOT NULL DEFAULT current_user,
    row_version integer NOT NULL DEFAULT 1,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    status_changed_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_assignments_kind_check CHECK (task_kind IN ('FIXED_GROWTH', 'PERSONALIZED_IMPROVEMENT')),
    CONSTRAINT task_assignments_creator_check CHECK (creator_system IN ('TEACHER_APP', 'TRIGGER_CENTER')),
    CONSTRAINT task_assignments_status_check CHECK (status IN ('ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')),
    CONSTRAINT task_assignments_priority_check CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
    CONSTRAINT task_assignments_source_mode_check CHECK (source_mode IN ('REAL', 'DERIVED_REAL', 'MOCK', 'MOCK_SIMULATION', 'MOCK_PROXY')),
    CONSTRAINT task_assignments_due_timezone_check CHECK (
        (due_at IS NULL AND timezone_used IS NULL AND timezone_source IS NULL AND timezone_verified_at IS NULL)
        OR
        (due_at IS NOT NULL AND timezone_used IS NOT NULL AND timezone_source IS NOT NULL AND timezone_verified_at IS NOT NULL)
    ),
    CONSTRAINT task_assignments_completion_check CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL)),
    CONSTRAINT task_assignments_reason_check CHECK (status NOT IN ('FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED') OR status_reason_code IS NOT NULL),
    CONSTRAINT task_assignments_fixed_owner_check CHECK (
        (task_code ~ '^G0[0-9]$' AND task_kind = 'FIXED_GROWTH' AND creator_system = 'TRIGGER_CENTER')
        OR
        (task_code !~ '^G0[0-9]$' AND task_kind = 'PERSONALIZED_IMPROVEMENT' AND creator_system = 'TRIGGER_CENTER')
    ),
    CONSTRAINT task_assignments_fixed_dedupe_check CHECK (
        task_kind <> 'FIXED_GROWTH' OR dedupe_key = 'fixed:' || teacher_id || ':' || task_code
    )
);

ALTER TABLE public.task_assignments
    ADD COLUMN IF NOT EXISTS display_title varchar,
    ADD COLUMN IF NOT EXISTS evidence_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS task_assignments_fixed_teacher_task_key
    ON public.task_assignments (teacher_id, task_code)
    WHERE task_kind = 'FIXED_GROWTH';
CREATE INDEX IF NOT EXISTS task_assignments_teacher_status_idx
    ON public.task_assignments (teacher_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.audit_events (
    sequence bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    event_id varchar NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
    event_type varchar NOT NULL,
    teacher_id varchar,
    task_id varchar,
    actor varchar NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.outbox_events (
    outbox_id varchar PRIMARY KEY DEFAULT gen_random_uuid()::text,
    event_id varchar NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
    event_type varchar NOT NULL,
    aggregate_type varchar NOT NULL,
    aggregate_id varchar NOT NULL,
    status varchar NOT NULL DEFAULT 'PENDING',
    occurred_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.score_entries (
    score_entry_id varchar PRIMARY KEY,
    camp_enrollment_id varchar NOT NULL,
    lesson_id varchar REFERENCES public.lesson_facts(lesson_id),
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    dimension varchar NOT NULL,
    entry_type varchar NOT NULL,
    delta_score double precision NOT NULL,
    reason_code varchar NOT NULL,
    evidence_status varchar NOT NULL,
    score_rule_version varchar NOT NULL,
    occurred_at timestamptz,
    reversal_of_score_entry_id varchar REFERENCES public.score_entries(score_entry_id),
    idempotency_key varchar NOT NULL UNIQUE,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    task_assignment_id varchar REFERENCES public.task_assignments(assignment_id)
);

CREATE TABLE IF NOT EXISTS public.notifications (
    notification_id varchar PRIMARY KEY,
    task_id varchar UNIQUE REFERENCES public.task_assignments(assignment_id),
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id),
    channel varchar NOT NULL,
    priority varchar NOT NULL,
    status varchar NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    stored_at timestamptz,
    read_at timestamptz,
    clicked_at timestamptz,
    response_due_at timestamptz,
    failure_reason text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_ref varchar UNIQUE,
    CONSTRAINT ck_notification_source CHECK (
        task_id IS NOT NULL OR source_ref IS NOT NULL
    )
);

CREATE TABLE IF NOT EXISTS public.notification_events (
    notification_event_id varchar PRIMARY KEY,
    notification_id varchar NOT NULL REFERENCES public.notifications(notification_id),
    delivery_status varchar NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    failure_reason text,
    request_hash varchar NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT notification_events_request_key UNIQUE (notification_id, request_hash)
);

CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    template_row public.task_templates%ROWTYPE;
    transition_allowed boolean;
    actor_name text := COALESCE(NULLIF(current_setting('role', true), 'none'), session_user);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'task assignments cannot be deleted';
    END IF;

    SELECT * INTO template_row
    FROM public.task_templates
    WHERE row_id = NEW.template_version_id;
    IF NOT FOUND OR template_row.status <> 'PUBLISHED' OR template_row.template_id <> NEW.task_code THEN
        RAISE EXCEPTION 'assignment must reference a published template for the same task code';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'ASSIGNED' OR NEW.row_version <> 1 THEN
            RAISE EXCEPTION 'new assignment must start at ASSIGNED with row_version 1';
        END IF;
        NEW.created_by := actor_name;
        NEW.updated_by := actor_name;
        RETURN NEW;
    END IF;

    IF (NEW.assignment_id, NEW.teacher_id, NEW.task_code, NEW.template_version_id,
        NEW.task_kind, NEW.creator_system, NEW.priority, NEW.why, NEW.due_at,
        NEW.timezone_used, NEW.timezone_source, NEW.timezone_verified_at,
        NEW.source_mode, NEW.dedupe_key, NEW.created_by, NEW.assigned_at, NEW.created_at)
       IS DISTINCT FROM
       (OLD.assignment_id, OLD.teacher_id, OLD.task_code, OLD.template_version_id,
        OLD.task_kind, OLD.creator_system, OLD.priority, OLD.why, OLD.due_at,
        OLD.timezone_used, OLD.timezone_source, OLD.timezone_verified_at,
        OLD.source_mode, OLD.dedupe_key, OLD.created_by, OLD.assigned_at, OLD.created_at) THEN
        RAISE EXCEPTION 'immutable assignment fields cannot be changed';
    END IF;

    IF OLD.status IN ('COMPLETED', 'EXPIRED', 'WAIVED', 'CANCELLED') THEN
        RAISE EXCEPTION 'terminal assignment cannot be changed';
    END IF;
    IF actor_name = 'tit_teacher_crud' AND NEW.status IN ('WAIVED', 'CANCELLED') THEN
        RAISE EXCEPTION 'teacher role cannot waive or cancel assignments';
    END IF;

    transition_allowed := CASE OLD.status
        WHEN 'ASSIGNED' THEN NEW.status IN ('VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
        WHEN 'VIEWED' THEN NEW.status IN ('IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
        WHEN 'IN_PROGRESS' THEN NEW.status IN ('SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
        WHEN 'SUBMITTED' THEN NEW.status IN ('UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
        WHEN 'UNDER_REVIEW' THEN NEW.status IN ('COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
        WHEN 'FAILED' THEN NEW.status IN ('IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED')
        ELSE false
    END;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT transition_allowed THEN
        RAISE EXCEPTION 'invalid assignment status transition: % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.row_version <> OLD.row_version THEN
        RAISE EXCEPTION 'caller cannot change row_version';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status_changed_at <= OLD.status_changed_at THEN
        RAISE EXCEPTION 'status_changed_at must advance with status';
    END IF;

    NEW.row_version := OLD.row_version + 1;
    NEW.updated_at := now();
    NEW.updated_by := actor_name;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION public.audit_task_assignment_write()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    actor_name text := COALESCE(NULLIF(current_setting('role', true), 'none'), session_user);
BEGIN
    INSERT INTO public.audit_events (event_type, teacher_id, task_id, actor, payload)
    VALUES (
        CASE WHEN TG_OP = 'INSERT' THEN 'TASK_ASSIGNMENT_CREATED' ELSE 'TASK_ASSIGNMENT_UPDATED' END,
        NEW.teacher_id,
        NEW.assignment_id,
        actor_name,
        jsonb_build_object('status', NEW.status, 'rowVersion', NEW.row_version)
    );
    IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO public.outbox_events (event_type, aggregate_type, aggregate_id, payload)
        VALUES (
            'TASK_ASSIGNMENT_STATUS_CHANGED',
            'TASK_ASSIGNMENT',
            NEW.assignment_id,
            jsonb_build_object('teacherId', NEW.teacher_id, 'taskCode', NEW.task_code, 'status', NEW.status, 'rowVersion', NEW.row_version)
        );
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS enforce_task_assignment_write ON public.task_assignments;
CREATE TRIGGER enforce_task_assignment_write
    BEFORE INSERT OR UPDATE OR DELETE ON public.task_assignments
    FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write();

DROP TRIGGER IF EXISTS audit_task_assignment_write ON public.task_assignments;
CREATE TRIGGER audit_task_assignment_write
    AFTER INSERT OR UPDATE ON public.task_assignments
    FOR EACH ROW EXECUTE FUNCTION public.audit_task_assignment_write();

COMMIT;
