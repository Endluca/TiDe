-- Formal DMS migration: public rev107 -> rev108.
-- The main application must be stopped.  DOM/OVS ingest may remain online.
-- This grants the existing application role EXECUTE on the immutable typed-id
-- validator used by relationship-table CHECK constraints.  It adds no table
-- mutation, schema creation, or new database role.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public107-to-108-domain-validator-acl',0)
);

DO $$
DECLARE
  public_head text;
  validator_oid regprocedure;
  validator_volatility "char";
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev108 migration requires a writable transaction';
  END IF;
  validator_oid := to_regprocedure(
    'public.dts_v2_typed_id_valid(text,text)'
  );
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regrole('tit_growth_app') IS NULL
     OR validator_oid IS NULL THEN
    RAISE EXCEPTION 'public rev108 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head IS DISTINCT FROM '20260825_107_domain_queue_read_acl' THEN
    RAISE EXCEPTION 'public rev108 DMS requires public head 107; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND usename='tit_growth_app'
  ) THEN
    RAISE EXCEPTION 'public rev108 migration requires app stopped';
  END IF;
  SELECT provolatile
  INTO validator_volatility
  FROM pg_catalog.pg_proc
  WHERE oid=validator_oid;
  IF validator_volatility IS DISTINCT FROM 'i' THEN
    RAISE EXCEPTION 'public rev108 validator must be immutable';
  END IF;
END
$$;

REVOKE ALL PRIVILEGES ON FUNCTION
  public.dts_v2_typed_id_valid(text, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  public.dts_v2_typed_id_valid(text, text)
TO tit_growth_app;

UPDATE public.alembic_version
SET version_num='20260826_108_domain_validator_acl'
WHERE version_num='20260825_107_domain_queue_read_acl';

DO $$
DECLARE
  public_can_execute boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_proc AS function_row
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(
        function_row.proacl,
        pg_catalog.acldefault('f', function_row.proowner)
      )
    ) AS privilege_row
    WHERE function_row.oid = to_regprocedure(
            'public.dts_v2_typed_id_valid(text,text)'
          )
      AND privilege_row.grantee = 0
      AND privilege_row.privilege_type = 'EXECUTE'
  )
  INTO public_can_execute;

  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM '20260826_108_domain_validator_acl'
     OR NOT has_function_privilege(
          'tit_growth_app',
          'public.dts_v2_typed_id_valid(text,text)',
          'EXECUTE'
        )
     OR public_can_execute
     OR has_schema_privilege('tit_growth_app','public','CREATE') THEN
    RAISE EXCEPTION 'public rev108 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT
  has_function_privilege(
    'tit_growth_app',
    'public.dts_v2_typed_id_valid(text,text)',
    'EXECUTE'
  ) AS can_execute_typed_id_validator,
  has_schema_privilege(
    'tit_growth_app','public','CREATE'
  ) AS can_create_in_public;
