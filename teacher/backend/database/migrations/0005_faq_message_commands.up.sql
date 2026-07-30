BEGIN;

ALTER TABLE tide.qa_messages
    ADD COLUMN reason_code text;

CREATE TABLE tide.qa_message_commands (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES tide.qa_conversations(id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qa_message_commands_idempotency_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT qa_message_commands_idempotency_length_check CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT qa_message_commands_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT qa_message_commands_response_check CHECK (jsonb_typeof(response_body) = 'object')
);

CREATE INDEX qa_message_commands_conversation_time_idx
    ON tide.qa_message_commands (conversation_id, created_at DESC);

COMMIT;
