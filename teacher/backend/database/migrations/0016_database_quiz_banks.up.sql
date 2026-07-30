BEGIN;

CREATE TABLE tide.task_quiz_banks (
    quiz_bank_id uuid PRIMARY KEY,
    bank_key text NOT NULL,
    question_set_version text NOT NULL,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    pass_score numeric(5,2) NOT NULL,
    questions jsonb NOT NULL,
    answer_key jsonb NOT NULL,
    source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_checksum char(64) NOT NULL,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_quiz_banks_key UNIQUE (bank_key, question_set_version),
    CONSTRAINT task_quiz_banks_status_check
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT task_quiz_banks_pass_score_check
        CHECK (pass_score >= 0 AND pass_score <= 100),
    CONSTRAINT task_quiz_banks_questions_check
        CHECK (jsonb_typeof(questions) = 'array' AND jsonb_array_length(questions) > 0),
    CONSTRAINT task_quiz_banks_answer_key_check
        CHECK (jsonb_typeof(answer_key) = 'object'),
    CONSTRAINT task_quiz_banks_source_check
        CHECK (jsonb_typeof(source_metadata) = 'object'),
    CONSTRAINT task_quiz_banks_checksum_check
        CHECK (content_checksum ~ '^[0-9a-f]{64}$'),
    CONSTRAINT task_quiz_banks_publish_check
        CHECK (
            (status = 'PUBLISHED' AND published_at IS NOT NULL)
            OR (status <> 'PUBLISHED' AND published_at IS NULL)
        )
);

CREATE INDEX task_quiz_banks_lookup_idx
    ON tide.task_quiz_banks (bank_key, question_set_version, status);

COMMIT;
