BEGIN;

DO $password_rollback_guard$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.user_accounts
        WHERE password_hash IS NULL
    ) THEN
        RAISE EXCEPTION
            'cannot roll back CRM SSO migration while passwordless SSO accounts exist';
    END IF;
END
$password_rollback_guard$;

DROP TABLE IF EXISTS tide.crm_sso_logins;

ALTER TABLE tide.auth_sessions
    DROP CONSTRAINT IF EXISTS auth_sessions_auth_method_check,
    DROP COLUMN IF EXISTS auth_method;

ALTER TABLE tide.user_accounts
    DROP CONSTRAINT IF EXISTS user_accounts_created_via_check,
    DROP COLUMN IF EXISTS created_via,
    ALTER COLUMN password_hash SET NOT NULL;

COMMIT;
