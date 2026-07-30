BEGIN;

CREATE TABLE tide.file_upload_intents (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES tide.file_objects(id) ON DELETE CASCADE,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE,
    step_key text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    upload_received_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT file_upload_intents_file_key UNIQUE (file_id),
    CONSTRAINT file_upload_intents_idempotency_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT file_upload_intents_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT file_upload_intents_step_key_check CHECK (char_length(step_key) BETWEEN 1 AND 128),
    CONSTRAINT file_upload_intents_idempotency_length_check CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT file_upload_intents_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX file_upload_intents_task_idx
    ON tide.file_upload_intents (teacher_task_id, step_key, created_at DESC);

COMMIT;
