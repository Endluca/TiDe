-- Formal DMS migration: public rev102 -> rev103.
-- Restore the runtime-control singleton removed by the rev101 history reset.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public102-to-103-time-recheck-schedule',0)
);

DO $$
DECLARE public_head text;
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev103 migration requires a writable transaction';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regclass(
          'public.dts_teacher_time_recheck_schedule'
        ) IS NULL THEN
    RAISE EXCEPTION 'public rev103 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head IS DISTINCT FROM
       '20260825_102_dts_ingest_batch_throughput' THEN
    RAISE EXCEPTION
      'public rev103 DMS requires public head 102; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND state IS DISTINCT FROM 'idle'
      AND usename='tit_growth_app'
  ) THEN
    RAISE EXCEPTION 'public rev103 migration requires application stopped';
  END IF;
END
$$;

INSERT INTO public.dts_teacher_time_recheck_schedule(
  schedule_id,last_enqueued_business_date,last_enqueued_generation,
  last_enqueued_at,row_version
) VALUES ('PRIMARY',NULL,NULL,NULL,1)
ON CONFLICT (schedule_id) DO NOTHING;

UPDATE public.alembic_version
SET version_num='20260825_103_reseed_time_recheck_schedule'
WHERE version_num='20260825_102_dts_ingest_batch_throughput';

DO $$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM
          '20260825_103_reseed_time_recheck_schedule'
     OR (SELECT count(*)
         FROM public.dts_teacher_time_recheck_schedule)<>1
     OR NOT EXISTS (
       SELECT 1
       FROM public.dts_teacher_time_recheck_schedule
       WHERE schedule_id='PRIMARY' AND row_version>=1
     ) THEN
    RAISE EXCEPTION 'public rev103 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT public.dts_v2_teacher_time_recheck_health_v1(900)
  AS teacher_time_recheck_health;
