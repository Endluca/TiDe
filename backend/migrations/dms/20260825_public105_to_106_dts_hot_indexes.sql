-- Formal DMS migration: public rev105 -> rev106.
-- Services must be stopped. This replaces two write-heavy DTS access paths
-- without changing ledger, source/current, dirty or checkpoint semantics.

BEGIN;

SET LOCAL lock_timeout = '30s';
SET LOCAL statement_timeout = '30min';

SELECT pg_advisory_xact_lock(
  hashtextextended('tit:dts-public105-to-106-hot-indexes',0)
);

DO $$
DECLARE public_head text;
BEGIN
  IF current_setting('transaction_read_only')::boolean THEN
    RAISE EXCEPTION 'public rev106 migration requires a writable transaction';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL
     OR to_regclass('public.dts_ingest_events') IS NULL
     OR to_regclass('public.dts_dirty_keys') IS NULL THEN
    RAISE EXCEPTION 'public rev106 migration prerequisites are missing';
  END IF;
  SELECT CASE WHEN count(*)=1 THEN min(version_num) END
  INTO public_head FROM public.alembic_version;
  IF public_head IS DISTINCT FROM '20260825_105_pipeline_read_acl' THEN
    RAISE EXCEPTION 'public rev106 DMS requires public head 105; current=%',
      coalesce(public_head,'<invalid>');
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_stat_activity
    WHERE pid<>pg_catalog.pg_backend_pid()
      AND usename IN ('tit_growth_app','tit_dts_ingest_runtime')
  ) THEN
    RAISE EXCEPTION 'public rev106 migration requires DTS and app stopped';
  END IF;
  IF to_regclass(
       'public.ix_dts_ingest_events_source_table_processed'
     ) IS NULL
     OR to_regclass('public.ix_dts_dirty_keys_ready_v2') IS NULL
     OR to_regclass('public.ix_dts_dirty_keys_pending_fifo_v2') IS NULL
     OR to_regclass('public.ix_dts_dirty_keys_retry_due_v2') IS NULL
     OR to_regclass(
          'public.ix_dts_ingest_events_processed_at_brin'
        ) IS NOT NULL
     OR to_regclass('public.ix_dts_dirty_keys_ready_v3') IS NOT NULL THEN
    RAISE EXCEPTION 'public rev106 source index shape is invalid';
  END IF;
END
$$;

LOCK TABLE public.dts_ingest_events,public.dts_dirty_keys
IN SHARE ROW EXCLUSIVE MODE;

CREATE INDEX ix_dts_ingest_events_processed_at_brin
ON public.dts_ingest_events USING brin (processed_at)
WITH (pages_per_range=64);

CREATE INDEX ix_dts_dirty_keys_ready_v3
ON public.dts_dirty_keys (
  next_attempt_at,updated_at,source_region,key_type,key_part_1,key_part_2
)
WHERE status IN ('PENDING','RETRY');

DROP INDEX public.ix_dts_ingest_events_source_table_processed;
DROP INDEX public.ix_dts_dirty_keys_pending_fifo_v2;
DROP INDEX public.ix_dts_dirty_keys_retry_due_v2;
DROP INDEX public.ix_dts_dirty_keys_ready_v2;

UPDATE public.alembic_version
SET version_num='20260825_106_dts_hot_indexes'
WHERE version_num='20260825_105_pipeline_read_acl';

DO $$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version)
          IS DISTINCT FROM '20260825_106_dts_hot_indexes'
     OR to_regclass(
          'public.ix_dts_ingest_events_source_table_processed'
        ) IS NOT NULL
     OR to_regclass('public.ix_dts_dirty_keys_ready_v2') IS NOT NULL
     OR to_regclass('public.ix_dts_dirty_keys_pending_fifo_v2') IS NOT NULL
     OR to_regclass('public.ix_dts_dirty_keys_retry_due_v2') IS NOT NULL
     OR NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_index AS index_state
          JOIN pg_catalog.pg_class AS index_class
            ON index_class.oid=index_state.indexrelid
          JOIN pg_catalog.pg_am AS access_method
            ON access_method.oid=index_class.relam
          WHERE index_state.indexrelid=
                  'public.ix_dts_ingest_events_processed_at_brin'::regclass
            AND index_state.indisvalid
            AND index_state.indisready
            AND access_method.amname='brin'
        )
     OR NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_index AS index_state
          JOIN pg_catalog.pg_class AS index_class
            ON index_class.oid=index_state.indexrelid
          JOIN pg_catalog.pg_am AS access_method
            ON access_method.oid=index_class.relam
          WHERE index_state.indexrelid=
                  'public.ix_dts_dirty_keys_ready_v3'::regclass
            AND index_state.indisvalid
            AND index_state.indisready
            AND index_state.indpred IS NOT NULL
            AND access_method.amname='btree'
        ) THEN
    RAISE EXCEPTION 'public rev106 postflight verification failed';
  END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT
  pg_size_pretty(
    pg_relation_size('public.ix_dts_ingest_events_processed_at_brin')
  ) AS ledger_brin_size,
  pg_size_pretty(
    pg_relation_size('public.ix_dts_dirty_keys_ready_v3')
  ) AS dirty_ready_size;
