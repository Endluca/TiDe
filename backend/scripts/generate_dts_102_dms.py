from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


BACKEND = Path(__file__).resolve().parents[1]
FROM_REVISION = "20260824_101_dts_single_pipeline_reset"
TO_REVISION = "20260825_102_dts_ingest_batch_throughput"
OUTPUT = (
    BACKEND
    / "migrations"
    / "dms"
    / "20260825_public101_to_102_dts_ingest_batch_throughput.sql"
)
NAMED_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$")
TRAILING_DUPLICATE_SEMICOLON = re.compile(r";;(?=[ \t]*$)", re.MULTILINE)

HEADER = """-- Formal DMS migration: public rev101 -> rev102.
-- This migration preserves all post-2026-08-18 consumed facts and replaces
-- per-event persistence overhead with the SINGLE_PIPELINE batch path.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '15min';

SELECT pg_advisory_xact_lock(
    hashtextextended('tit:dts-public101-to-102-batch-throughput', 0)
);

DO $public_101_102_preflight$
DECLARE public_head text;
DECLARE teacher_head text;
DECLARE teacher_count integer;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'public rev102 migration requires a writable transaction';
    END IF;
    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL
       OR to_regrole('tit_dts_ingest_runtime') IS NULL THEN
        RAISE EXCEPTION 'public rev102 migration prerequisites are missing';
    END IF;
    SELECT CASE WHEN count(*)=1 THEN min(version_num) END
    INTO public_head FROM public.alembic_version;
    IF public_head IS DISTINCT FROM
         '20260824_101_dts_single_pipeline_reset' THEN
        RAISE EXCEPTION 'public rev102 DMS requires public head 101; current=%',
          coalesce(public_head,'<invalid>');
    END IF;
    SELECT count(*),max(migration_id) FILTER (
      WHERE migration_order=(SELECT max(migration_order)
                             FROM tide.schema_migrations)
    ) INTO teacher_count,teacher_head FROM tide.schema_migrations;
    IF teacher_count<>38
       OR teacher_head IS DISTINCT FROM '0043_p_rel_execution_catalog' THEN
        RAISE EXCEPTION
          'public rev102 DMS requires exact 38-row teacher ledger ending at 0043; count=%, head=%',
          teacher_count,coalesce(teacher_head,'<invalid>');
    END IF;
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_stat_activity
      WHERE pid<>pg_catalog.pg_backend_pid()
        AND state IS DISTINCT FROM 'idle'
        AND usename IN (
          'tit_dts_ingest_runtime','tit_growth_app','tit_teacher_crud',
          'tide_support_ticket_owner'
        )
    ) THEN
      RAISE EXCEPTION 'public rev102 migration requires all runtimes stopped';
    END IF;
END
$public_101_102_preflight$;

CREATE TEMP TABLE dts_rev102_fact_fence(
  relation_name text PRIMARY KEY,row_count bigint NOT NULL
) ON COMMIT DROP;
INSERT INTO dts_rev102_fact_fence(relation_name,row_count) VALUES
  ('dts_ingest_events',(SELECT count(*) FROM public.dts_ingest_events)),
  ('dts_ingest_checkpoints',(SELECT count(*) FROM public.dts_ingest_checkpoints)),
  ('dts_source_row_versions',(SELECT count(*) FROM public.dts_source_row_versions)),
  ('dts_source_rows',(SELECT count(*) FROM public.dts_source_rows)),
  ('dts_dirty_key_inputs',(SELECT count(*) FROM public.dts_dirty_key_inputs));

"""

FOOTER = """

DO $public_101_102_postflight$
BEGIN
  IF (SELECT count(*) FROM public.alembic_version)<>1
     OR (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM
          '20260825_102_dts_ingest_batch_throughput'
     OR to_regprocedure(
          'public.enqueue_dirty_from_source_revisions_batch_v3(jsonb)'
        ) IS NULL
     OR to_regprocedure(
          'public.dts_active_source_scope_tables_v1(text,text[])'
        ) IS NULL
     OR EXISTS (
       SELECT 1 FROM dts_rev102_fact_fence fence
       WHERE fence.row_count IS DISTINCT FROM CASE fence.relation_name
         WHEN 'dts_ingest_events' THEN
           (SELECT count(*) FROM public.dts_ingest_events)
         WHEN 'dts_ingest_checkpoints' THEN
           (SELECT count(*) FROM public.dts_ingest_checkpoints)
         WHEN 'dts_source_row_versions' THEN
           (SELECT count(*) FROM public.dts_source_row_versions)
         WHEN 'dts_source_rows' THEN
           (SELECT count(*) FROM public.dts_source_rows)
         WHEN 'dts_dirty_key_inputs' THEN
           (SELECT count(*) FROM public.dts_dirty_key_inputs)
       END
     ) OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_trigger
       WHERE tgrelid IN (
         'public.dts_dirty_keys'::regclass,
         'public.dts_dirty_key_inputs'::regclass
       ) AND tgname IN (
         'trg_sync_v1_compat_dirty_input_v1',
         'trg_v1_compat_processing_commit_v1'
       ) AND NOT tgisinternal
     ) THEN
    RAISE EXCEPTION 'public rev102 postflight verification failed';
  END IF;
END
$public_101_102_postflight$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT migration_id,migration_order
FROM tide.schema_migrations
ORDER BY migration_order DESC LIMIT 1;
"""


def main() -> None:
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://placeholder:placeholder@127.0.0.1:1/placeholder"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            f"{FROM_REVISION}:{TO_REVISION}",
            "--sql",
        ],
        cwd=BACKEND,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    body = result.stdout.strip()
    if not body.startswith("BEGIN;") or not body.endswith("COMMIT;"):
        raise RuntimeError("unexpected Alembic offline SQL envelope")
    body = body.removeprefix("BEGIN;").removesuffix("COMMIT;").strip()
    sql_source = HEADER + body + FOOTER
    sql_source = NAMED_DOLLAR_QUOTE.sub("$$", sql_source)
    sql_source = TRAILING_DUPLICATE_SEMICOLON.sub(";", sql_source)
    sql_source = "\n".join(
        line.rstrip() for line in sql_source.splitlines()
    ) + "\n"
    OUTPUT.write_text(sql_source, encoding="utf-8")


if __name__ == "__main__":
    main()
