BEGIN;

ALTER TABLE tide.task_step_definitions
    DROP CONSTRAINT task_step_definitions_type_check;

ALTER TABLE tide.task_step_definitions
    ADD CONSTRAINT task_step_definitions_type_check
    CHECK (step_type IN (
        'VIDEO', 'DOCUMENT', 'QUIZ', 'CHECKLIST', 'UPLOAD',
        'DEVICE_CHECK', 'EXTERNAL_TRAINING', 'CUSTOM'
    ));

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
    CONSTRAINT quiz_answers_payload_check CHECK (
        jsonb_typeof(answer_payload) IN ('object', 'array', 'string', 'number', 'boolean')
    )
);

COMMIT;
