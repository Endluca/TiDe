BEGIN;

CREATE TABLE tide.task_command_receipts (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    teacher_task_id uuid NOT NULL REFERENCES tide.teacher_tasks(id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    command_id text NOT NULL,
    command_type text NOT NULL,
    request_hash text NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_command_receipts_idempotency_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT task_command_receipts_command_id_key UNIQUE (account_id, command_id),
    CONSTRAINT task_command_receipts_idempotency_length_check CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT task_command_receipts_command_id_length_check CHECK (char_length(command_id) BETWEEN 8 AND 128),
    CONSTRAINT task_command_receipts_type_check CHECK (command_type IN ('START', 'PROGRESS', 'SUBMIT', 'RETRY')),
    CONSTRAINT task_command_receipts_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT task_command_receipts_response_check CHECK (jsonb_typeof(response_body) = 'object')
);

CREATE INDEX task_command_receipts_task_time_idx
    ON tide.task_command_receipts (teacher_task_id, created_at DESC);

COMMIT;
