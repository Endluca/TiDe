BEGIN;

ALTER TABLE tide.user_accounts
    ALTER COLUMN password_hash DROP NOT NULL,
    ADD COLUMN created_via varchar(32) NOT NULL DEFAULT 'LOCAL';

ALTER TABLE tide.user_accounts
    ADD CONSTRAINT user_accounts_created_via_check
    CHECK (created_via IN ('LOCAL', 'CRM_SSO'));

ALTER TABLE tide.auth_sessions
    ADD COLUMN auth_method varchar(32) NOT NULL DEFAULT 'PASSWORD';

ALTER TABLE tide.auth_sessions
    ADD CONSTRAINT auth_sessions_auth_method_check
    CHECK (auth_method IN ('PASSWORD', 'CRM_SSO'));

CREATE TABLE tide.crm_sso_logins (
    id uuid PRIMARY KEY,
    jti_hash char(64) NOT NULL,
    issuer varchar(64) NOT NULL,
    audience varchar(64) NOT NULL,
    account_id uuid NOT NULL
        REFERENCES tide.user_accounts(id) ON DELETE CASCADE,
    exchange_code_hash char(64) NOT NULL,
    redirect_path varchar(512) NOT NULL,
    assertion_expires_at timestamptz NOT NULL,
    exchange_expires_at timestamptz NOT NULL,
    exchanged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_sso_logins_jti_hash_key UNIQUE (jti_hash),
    CONSTRAINT crm_sso_logins_exchange_code_hash_key
        UNIQUE (exchange_code_hash),
    CONSTRAINT crm_sso_logins_hash_check CHECK (
        jti_hash ~ '^[0-9a-f]{64}$'
        AND exchange_code_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT crm_sso_logins_issuer_check CHECK (
        issuer = btrim(issuer)
        AND char_length(issuer) BETWEEN 1 AND 64
    ),
    CONSTRAINT crm_sso_logins_audience_check CHECK (
        audience = btrim(audience)
        AND char_length(audience) BETWEEN 1 AND 64
    ),
    CONSTRAINT crm_sso_logins_redirect_check CHECK (
        redirect_path IN ('/', '/path', '/messages')
        OR redirect_path ~ '^/task/[A-Za-z0-9_-]{1,128}$'
    ),
    CONSTRAINT crm_sso_logins_expiry_check CHECK (
        assertion_expires_at > created_at
        AND exchange_expires_at > created_at
        AND exchange_expires_at <= assertion_expires_at
    ),
    CONSTRAINT crm_sso_logins_exchange_time_check CHECK (
        exchanged_at IS NULL OR exchanged_at >= created_at
    )
);

CREATE INDEX crm_sso_logins_account_time_idx
    ON tide.crm_sso_logins (account_id, created_at DESC);

DO $grant_runtime$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
        REVOKE ALL ON tide.crm_sso_logins FROM tit_teacher_crud;
        GRANT SELECT, INSERT, UPDATE ON tide.crm_sso_logins
            TO tit_teacher_crud;
    END IF;
END
$grant_runtime$;

COMMIT;
