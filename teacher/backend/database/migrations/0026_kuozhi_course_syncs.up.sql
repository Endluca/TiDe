BEGIN;

CREATE TABLE tide.kuozhi_course_syncs (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL
        REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    task_assignment_id varchar NOT NULL
        REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    command_id text NOT NULL,
    request_hash text NOT NULL,
    mapping_version integer NOT NULL,
    sync_status varchar NOT NULL,
    completion_decision boolean NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kuozhi_course_syncs_idempotency_key
        UNIQUE (account_id, idempotency_key),
    CONSTRAINT kuozhi_course_syncs_command_id
        UNIQUE (account_id, command_id),
    CONSTRAINT kuozhi_course_syncs_idempotency_length_check
        CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT kuozhi_course_syncs_command_length_check
        CHECK (char_length(command_id) BETWEEN 8 AND 128),
    CONSTRAINT kuozhi_course_syncs_request_hash_check
        CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT kuozhi_course_syncs_mapping_version_check
        CHECK (mapping_version > 0),
    CONSTRAINT kuozhi_course_syncs_status_check
        CHECK (sync_status IN ('AVAILABLE', 'PARTIAL', 'NO_DATA')),
    CONSTRAINT kuozhi_course_syncs_response_check
        CHECK (jsonb_typeof(response_body) = 'object'),
    CONSTRAINT kuozhi_course_syncs_real_mode_check
        CHECK (response_body->>'dataMode' = 'REAL')
);

CREATE INDEX kuozhi_course_syncs_assignment_time_idx
    ON tide.kuozhi_course_syncs (
        task_assignment_id, created_at DESC, id DESC
    );

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
        EXECUTE 'REVOKE ALL ON tide.kuozhi_course_syncs FROM tit_teacher_crud';
        EXECUTE 'GRANT SELECT, INSERT ON tide.kuozhi_course_syncs TO tit_teacher_crud';
    END IF;
END
$$;

COMMIT;
