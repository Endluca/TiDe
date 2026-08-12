\set ON_ERROR_STOP on

-- Run this file only in a DBA-controlled session after both migration chains.
-- The DBA creates the credential out of band:
--   CREATE ROLE tit_contract_probe LOGIN PASSWORD '...';
-- Passwords never belong in this repository.

SELECT current_database() = :'expected_database' AS database_matches
\gset
\if :database_matches
\else
  \echo 'contract probe grant target database mismatch'
  \quit
\endif

DO $grant_preflight$
DECLARE
    probe_role pg_roles%ROWTYPE;
BEGIN
    SELECT *
    INTO probe_role
    FROM pg_roles
    WHERE rolname = 'tit_contract_probe';

    IF NOT FOUND
       OR NOT probe_role.rolcanlogin
       OR probe_role.rolsuper
       OR probe_role.rolcreatedb
       OR probe_role.rolcreaterole
       OR probe_role.rolreplication
       OR probe_role.rolbypassrls THEN
        RAISE EXCEPTION
            'tit_contract_probe must be a restricted LOGIN role';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_auth_members
        WHERE member = probe_role.oid
    ) THEN
        RAISE EXCEPTION
            'tit_contract_probe must not inherit another database role';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('public.teachers') IS NULL
       OR to_regclass('public.teacher_scorecard_current') IS NULL
       OR to_regclass('public.teacher_lesson_score_current') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.account_onboarding_states') IS NULL
       OR to_regclass('tide.crm_sso_logins') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION
            'both migration chains must complete before probe grants';
    END IF;
END
$grant_preflight$;

BEGIN;

ALTER ROLE tit_contract_probe SET default_transaction_read_only = on;
ALTER ROLE tit_contract_probe SET statement_timeout = '60s';
ALTER ROLE tit_contract_probe SET lock_timeout = '5s';

REVOKE CREATE, TEMPORARY
ON DATABASE :"expected_database"
FROM tit_contract_probe;
GRANT CONNECT
ON DATABASE :"expected_database"
TO tit_contract_probe;

REVOKE CREATE ON SCHEMA public, tide FROM tit_contract_probe;
GRANT USAGE ON SCHEMA public, tide TO tit_contract_probe;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, tide
FROM tit_contract_probe;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, tide
FROM tit_contract_probe;

GRANT SELECT ON
    public.alembic_version,
    public.task_templates,
    public.task_assignments,
    public.teachers,
    public.teacher_scorecard_current,
    public.teacher_lesson_score_current,
    tide.task_execution_versions,
    tide.task_step_definitions,
    tide.task_validation_rules,
    tide.account_onboarding_states,
    tide.schema_migrations
TO tit_contract_probe;

-- SECURITY DEFINER mutation functions must never be inherited through PUBLIC.
REVOKE ALL ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) FROM PUBLIC, tit_contract_probe;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) FROM PUBLIC, tit_contract_probe;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM PUBLIC, tit_contract_probe;
REVOKE ALL ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) FROM PUBLIC, tit_contract_probe;

COMMIT;
