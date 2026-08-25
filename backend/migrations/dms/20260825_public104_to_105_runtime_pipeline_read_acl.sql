-- Formal DMS migration: public rev104 -> rev105.
-- The Outbox runtime must read the qualification gate from pipeline control.
-- It remains unable to mutate pipeline mode, generation or gate state.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public104-to-105-pipeline-read-acl',0)
);

DO $$
DECLARE public_head text;
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev105 migration requires a writable transaction';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regclass('public.dts_pipeline_control') IS NULL
     OR to_regrole('tit_growth_app') IS NULL THEN
    RAISE EXCEPTION 'public rev105 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head IS DISTINCT FROM '20260825_104_runtime_table_acl' THEN
    RAISE EXCEPTION 'public rev105 DMS requires public head 104; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND state IS DISTINCT FROM 'idle'
      AND usename='tit_growth_app'
  ) THEN
    RAISE EXCEPTION 'public rev105 migration requires application stopped';
  END IF;
END
$$;

GRANT SELECT ON TABLE public.dts_pipeline_control TO tit_growth_app;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER
ON TABLE public.dts_pipeline_control FROM tit_growth_app;

UPDATE public.alembic_version
SET version_num='20260825_105_pipeline_read_acl'
WHERE version_num='20260825_104_runtime_table_acl';

DO $$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM '20260825_105_pipeline_read_acl'
     OR NOT has_table_privilege(
          'tit_growth_app','public.dts_pipeline_control','SELECT'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_pipeline_control','INSERT'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_pipeline_control','UPDATE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_pipeline_control','DELETE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.dts_pipeline_control','TRUNCATE'
        )
     OR has_schema_privilege('tit_growth_app','public','CREATE') THEN
    RAISE EXCEPTION 'public rev105 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT
  has_table_privilege(
    'tit_growth_app','public.dts_pipeline_control','SELECT'
  ) AS can_select,
  has_table_privilege(
    'tit_growth_app','public.dts_pipeline_control','UPDATE'
  ) AS can_update,
  has_table_privilege(
    'tit_growth_app','public.dts_pipeline_control','DELETE'
  ) AS can_delete;
