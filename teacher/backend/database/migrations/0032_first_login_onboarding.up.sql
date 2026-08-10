BEGIN;

CREATE TABLE tide.account_onboarding_states (
    account_id uuid NOT NULL
        REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    guide_code varchar(64) NOT NULL,
    guide_version integer NOT NULL,
    status varchar(32) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash varchar(64),
    acknowledged_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT account_onboarding_states_pkey
        PRIMARY KEY (account_id, guide_code, guide_version),
    CONSTRAINT account_onboarding_states_account_idempotency_key
        UNIQUE (account_id, idempotency_key),
    CONSTRAINT account_onboarding_states_guide_code_check
        CHECK (
            char_length(guide_code) BETWEEN 1 AND 64
            AND guide_code = btrim(guide_code)
            AND guide_code IN (
                'FIRST_LOGIN',
                'MY_TIDE_OVERVIEW',
                'SCORE_DETAILS',
                'TASK_PATH',
                'TASK_RESULT',
                'MESSAGES_TICKETS',
                'HELP_ROUTES',
                'PERSONALIZED_TASK_FIRST'
            )
        ),
    CONSTRAINT account_onboarding_states_guide_version_check
        CHECK (guide_version > 0),
    CONSTRAINT account_onboarding_states_status_check
        CHECK (
            status IN ('COMPLETED', 'SKIPPED', 'MIGRATED_EXISTING')
        ),
    CONSTRAINT account_onboarding_states_migrated_existing_check
        CHECK (
            status <> 'MIGRATED_EXISTING'
            OR guide_code = 'FIRST_LOGIN'
        ),
    CONSTRAINT account_onboarding_states_idempotency_key_check
        CHECK (
            char_length(idempotency_key) BETWEEN 8 AND 128
            AND idempotency_key = btrim(idempotency_key)
        ),
    CONSTRAINT account_onboarding_states_request_hash_check
        CHECK (
            (
                status = 'MIGRATED_EXISTING'
                AND request_hash IS NULL
            )
            OR (
                status IN ('COMPLETED', 'SKIPPED')
                AND request_hash IS NOT NULL
                AND request_hash ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT account_onboarding_states_time_check
        CHECK (
            updated_at >= created_at
            AND acknowledged_at <= updated_at
        )
);

INSERT INTO tide.account_onboarding_states (
    account_id,
    guide_code,
    guide_version,
    status,
    idempotency_key,
    request_hash,
    acknowledged_at,
    created_at,
    updated_at
)
SELECT
    account.id,
    'FIRST_LOGIN',
    1,
    'MIGRATED_EXISTING',
    'migration:0032:first-login:v1',
    NULL,
    min(security_event.created_at),
    now(),
    now()
FROM tide.user_accounts AS account
JOIN tide.auth_security_events AS security_event
  ON security_event.account_id = account.id
 AND security_event.event_type = 'LOGIN'
 AND security_event.outcome = 'SUCCESS'
GROUP BY account.id
ON CONFLICT (account_id, guide_code, guide_version) DO NOTHING;

COMMIT;
