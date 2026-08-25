-- Formal DMS migration: public rev106 -> rev107.
-- The main application must be stopped.  DOM/OVS ingest may remain online.
-- This grants the shared application runtime only
-- the queue evidence reads used by the Domain projector; every mutation
-- remains behind the existing SECURITY DEFINER commands.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public106-to-107-domain-queue-read-acl',0)
);

DO $$
DECLARE public_head text;
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev107 migration requires a writable transaction';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regrole('tit_growth_app') IS NULL
     OR to_regclass('public.dts_dirty_keys') IS NULL
     OR to_regclass('public.dts_dirty_key_inputs') IS NULL THEN
    RAISE EXCEPTION 'public rev107 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head IS DISTINCT FROM '20260825_106_dts_hot_indexes' THEN
    RAISE EXCEPTION 'public rev107 DMS requires public head 106; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND usename='tit_growth_app'
  ) THEN
    RAISE EXCEPTION 'public rev107 migration requires app stopped';
  END IF;
END
$$;

GRANT SELECT ON TABLE
  public.dts_dirty_keys,
  public.dts_dirty_key_inputs
TO tit_growth_app;

REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE
  public.dts_dirty_keys,
  public.dts_dirty_key_inputs
FROM tit_growth_app;

UPDATE public.alembic_version
SET version_num='20260825_107_domain_queue_read_acl'
WHERE version_num='20260825_106_dts_hot_indexes';

DO $$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM '20260825_107_domain_queue_read_acl'
     OR NOT has_table_privilege(
          'tit_growth_app','public.dts_dirty_keys','SELECT'
        )
     OR NOT has_table_privilege(
          'tit_growth_app','public.dts_dirty_key_inputs','SELECT'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_keys','INSERT'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_keys','UPDATE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_keys','DELETE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_key_inputs','INSERT'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_key_inputs','UPDATE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_dirty_key_inputs','DELETE'
        )
     OR has_schema_privilege('tit_growth_app','public','CREATE') THEN
    RAISE EXCEPTION 'public rev107 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT
  has_table_privilege(
    'tit_growth_app','public.dts_dirty_keys','SELECT'
  ) AS can_read_dirty_keys,
  has_table_privilege(
    'tit_growth_app','public.dts_dirty_key_inputs','SELECT'
  ) AS can_read_dirty_inputs;
