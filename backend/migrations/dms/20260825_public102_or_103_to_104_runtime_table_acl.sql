-- Formal DMS migration: public rev102/rev103 -> rev104.
-- Restore the time-recheck singleton when needed and simplify the application
-- Outbox contract to table-level DML.  Immutable fields and transitions remain
-- enforced by guard_outbox_event_update.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public102-or-103-to-104-runtime-table-acl',0)
);

DO $$
DECLARE public_head text;
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev104 migration requires a writable transaction';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regclass('public.outbox_events') IS NULL
     OR to_regclass(
          'public.dts_teacher_time_recheck_schedule'
        ) IS NULL
     OR to_regrole('tit_growth_app') IS NULL
     OR to_regprocedure(
          'public.guard_outbox_event_update()'
        ) IS NULL THEN
    RAISE EXCEPTION 'public rev104 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head NOT IN (
       '20260825_102_dts_ingest_batch_throughput',
       '20260825_103_reseed_time_recheck_schedule'
     ) THEN
    RAISE EXCEPTION
      'public rev104 DMS requires public head 102 or 103; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND state IS DISTINCT FROM 'idle'
      AND usename='tit_growth_app'
  ) THEN
    RAISE EXCEPTION 'public rev104 migration requires application stopped';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_trigger trigger_row
    WHERE trigger_row.tgrelid='public.outbox_events'::regclass
      AND trigger_row.tgname='guard_outbox_event_update'
      AND trigger_row.tgenabled IN ('O','A')
  ) THEN
    RAISE EXCEPTION 'public rev104 Outbox guard trigger is missing or disabled';
  END IF;
END
$$;

INSERT INTO public.dts_teacher_time_recheck_schedule(
  schedule_id,last_enqueued_business_date,last_enqueued_generation,
  last_enqueued_at,row_version
) VALUES ('PRIMARY',NULL,NULL,NULL,1)
ON CONFLICT (schedule_id) DO NOTHING;

DO $$
DECLARE column_name text;
BEGIN
  FOR column_name IN
    SELECT attribute.attname::text
    FROM pg_catalog.pg_attribute attribute
    WHERE attribute.attrelid='public.outbox_events'::regclass
      AND attribute.attnum>0
      AND NOT attribute.attisdropped
  LOOP
    EXECUTE format(
      'REVOKE ALL PRIVILEGES (%I) ON TABLE '
      'public.outbox_events FROM tit_growth_app',
      column_name
    );
  END LOOP;
END
$$;

GRANT SELECT,INSERT,UPDATE,DELETE
ON TABLE public.outbox_events TO tit_growth_app;
REVOKE TRUNCATE,REFERENCES,TRIGGER
ON TABLE public.outbox_events FROM tit_growth_app;

UPDATE public.alembic_version
SET version_num='20260825_104_runtime_table_acl'
WHERE version_num IN (
  '20260825_102_dts_ingest_batch_throughput',
  '20260825_103_reseed_time_recheck_schedule'
);

DO $$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM '20260825_104_runtime_table_acl'
     OR (SELECT count(*)
         FROM public.dts_teacher_time_recheck_schedule)<>1
     OR NOT EXISTS (
       SELECT 1
       FROM public.dts_teacher_time_recheck_schedule
       WHERE schedule_id='PRIMARY' AND row_version>=1
     )
     OR NOT has_table_privilege(
          'tit_growth_app','public.outbox_events','SELECT'
        )
     OR NOT has_table_privilege(
          'tit_growth_app','public.outbox_events','INSERT'
        )
     OR NOT has_table_privilege(
          'tit_growth_app','public.outbox_events','UPDATE'
        )
     OR NOT has_table_privilege(
          'tit_growth_app','public.outbox_events','DELETE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.outbox_events','TRUNCATE'
        )
     OR has_table_privilege(
          'tit_growth_app','public.outbox_events','TRIGGER'
        )
     OR has_schema_privilege('tit_growth_app','public','CREATE')
     OR EXISTS (
       SELECT 1
       FROM pg_catalog.pg_attribute attribute
       CROSS JOIN LATERAL unnest(attribute.attacl)
         privilege_row(acl)
       WHERE attribute.attrelid='public.outbox_events'::regclass
         AND split_part(privilege_row.acl::text,'=',1)
               ='tit_growth_app'
     ) THEN
    RAISE EXCEPTION 'public rev104 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT
  has_table_privilege(
    'tit_growth_app','public.outbox_events','SELECT'
  ) AS can_select,
  has_table_privilege(
    'tit_growth_app','public.outbox_events','INSERT'
  ) AS can_insert,
  has_table_privilege(
    'tit_growth_app','public.outbox_events','UPDATE'
  ) AS can_update,
  has_table_privilege(
    'tit_growth_app','public.outbox_events','DELETE'
  ) AS can_delete,
  has_table_privilege(
    'tit_growth_app','public.outbox_events','TRUNCATE'
  ) AS can_truncate;
