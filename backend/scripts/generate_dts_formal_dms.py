from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import sys


BACKEND = Path(__file__).resolve().parents[1]
FROM_REVISION = "20260819_65_g09_set_course"
TO_REVISION = "20260823_100_scope_snapshot_diff"
OUTPUT = (
    BACKEND
    / "migrations"
    / "dms"
    / "20260824_public65_to_100_dts_domain_schema.sql"
)
RESET_MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260824_101_dts_single_pipeline_reset.py"
)

NAMED_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$")
TRAILING_DUPLICATE_SEMICOLON = re.compile(r";;(?=[ \t]*$)", re.MULTILINE)

HEADER = """-- Formal DMS migration: public rev65 -> rev100.
-- Release commit: flow/release@2881507
-- Execute before teacher 0042->0043 and public 100->101 reset.
-- The whole file is one transaction and requires the exact production heads
-- observed on 2026-08-24: public rev65 and teacher 0042 (37 rows).

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

SELECT pg_advisory_xact_lock(
    hashtextextended('tit:dts-public65-to-100-formal-migration', 0)
);

DO $public_65_100_preflight$
DECLARE
    public_head text;
    teacher_head text;
    teacher_count integer;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'public rev65->100 requires a writable transaction';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'public rev65->100 requires public and tide ledgers';
    END IF;

    SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
    INTO public_head
    FROM public.alembic_version;

    IF public_head IS DISTINCT FROM '20260819_65_g09_set_course' THEN
        RAISE EXCEPTION
            'public rev65->100 requires public head 65; current=%',
            coalesce(public_head, '<invalid>');
    END IF;

    SELECT count(*), max(migration_id) FILTER (
        WHERE migration_order = (
            SELECT max(migration_order) FROM tide.schema_migrations
        )
    )
    INTO teacher_count, teacher_head
    FROM tide.schema_migrations;

    IF teacher_count <> 37
       OR teacher_head IS DISTINCT FROM '0042_g09_set_kuozhi_course'
       OR NOT EXISTS (
           SELECT 1
           FROM tide.schema_migrations
           WHERE migration_order = 37
             AND migration_id = '0042_g09_set_kuozhi_course'
       ) THEN
        RAISE EXCEPTION
            'public rev65->100 requires exact 37-row teacher ledger ending at 0042; count=%, head=%',
            teacher_count,
            coalesce(teacher_head, '<invalid>');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_stat_activity
        WHERE pid <> pg_catalog.pg_backend_pid()
          AND state IS DISTINCT FROM 'idle'
          AND usename IN (
              'tit_dts_ingest_runtime',
              'tit_growth_app',
              'tit_teacher_crud',
              'tide_support_ticket_owner'
          )
    ) THEN
        RAISE EXCEPTION 'public rev65->100 requires all DTS/application runtimes stopped';
    END IF;
END
$public_65_100_preflight$;

"""

FOOTER = """

DO $public_65_100_postflight$
BEGIN
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR (SELECT version_num FROM public.alembic_version)
            IS DISTINCT FROM '20260823_100_scope_snapshot_diff'
       OR to_regclass('public.dts_source_snapshot_desired_rows') IS NULL
       OR to_regprocedure(
            'public.publish_source_snapshot_candidate_v3(text,text,text,text,bigint,bigint,text,text)'
          ) IS NULL
       OR (SELECT count(*) FROM tide.schema_migrations) <> 37
       OR NOT EXISTS (
            SELECT 1 FROM tide.schema_migrations
            WHERE migration_order=37
              AND migration_id='0042_g09_set_kuozhi_course'
          ) THEN
        RAISE EXCEPTION 'public rev65->100 postflight verification failed';
    END IF;
END
$public_65_100_postflight$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT migration_id, migration_order
FROM tide.schema_migrations
ORDER BY migration_order DESC
LIMIT 1;
"""


def _load_consumed_fact_roots() -> tuple[str, ...]:
    module = ast.parse(RESET_MIGRATION.read_text(encoding="utf-8"))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name)
            and target.id == "CONSUMED_FACT_ROOTS"
            for target in statement.targets
        ):
            roots = ast.literal_eval(statement.value)
            if (
                isinstance(roots, tuple)
                and roots
                and all(isinstance(root, str) for root in roots)
            ):
                return roots
    raise RuntimeError("CONSUMED_FACT_ROOTS not found in reset migration")


def _render_early_consumed_history_reset() -> str:
    root_values = ",".join(
        f"('public.{root}')" for root in _load_consumed_fact_roots()
    )
    return f"""
-- The rollout intentionally discards every historical consumed fact. Clear
-- the rev65 facts before installing rev66-rev100 constraints; waiting until
-- rev101 would make legacy rows block those intermediate schema revisions.
SET CONSTRAINTS ALL IMMEDIATE;

DO $early_clear_all_consumed_history$
DECLARE
    relation_list text;
BEGIN
    WITH RECURSIVE root_names(name) AS (
        VALUES {root_values}
    ), roots(relid) AS (
        SELECT to_regclass(name)
        FROM root_names
        WHERE to_regclass(name) IS NOT NULL
    ), closure(relid) AS (
        SELECT relid FROM roots
        UNION
        SELECT dependency.conrelid
        FROM pg_catalog.pg_constraint AS dependency
        JOIN closure AS parent ON parent.relid=dependency.confrelid
        WHERE dependency.contype='f'
    )
    SELECT string_agg(
               format('%I.%I',namespace.nspname,relation.relname),
               ',' ORDER BY namespace.nspname,relation.relname
           )
    INTO relation_list
    FROM closure
    JOIN pg_catalog.pg_class AS relation ON relation.oid=closure.relid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid=relation.relnamespace
    WHERE relation.relkind IN ('r','p');

    IF relation_list IS NULL THEN
        RAISE EXCEPTION 'EARLY_CLEAR_ALL_CONSUMED_HISTORY_FOUND_NO_TABLES';
    END IF;

    EXECUTE 'TRUNCATE TABLE ' || relation_list || ' RESTART IDENTITY';
END
$early_clear_all_consumed_history$;

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
    sql_source = HEADER + _render_early_consumed_history_reset() + body + FOOTER
    # Alibaba DMS onequery recognizes bare $$ PL/pgSQL bodies but splits
    # named dollar quotes (for example $function$) at their inner semicolons.
    # Alembic emits named tags, so normalize every body delimiter in the final
    # DMS artifact. PostgreSQL treats the bare delimiter equivalently here.
    sql_source = NAMED_DOLLAR_QUOTE.sub("$$", sql_source)
    # op.execute() payloads already end in a semicolon and Alembic's offline
    # renderer adds another one. DMS tries to build an execution plan for the
    # resulting empty statement and reports "Multiple SQL statements: []".
    sql_source = TRAILING_DUPLICATE_SEMICOLON.sub(";", sql_source)
    sql_source = "\n".join(
        line.rstrip() for line in sql_source.splitlines()
    ) + "\n"
    OUTPUT.write_text(sql_source, encoding="utf-8")


if __name__ == "__main__":
    main()
