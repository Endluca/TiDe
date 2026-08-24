from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_76 = "20260822_76_lesson_score_components"
REVISION_77 = "20260822_77_retire_cpu_network"


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _run_alembic(backend_dir: Path, database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _seed_revision_76_shape(connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version VALUES ('{REVISION_76}');

            CREATE TABLE public.outbox_events (
                outbox_id text PRIMARY KEY,
                event_id text NOT NULL UNIQUE,
                aggregate_type text NOT NULL,
                aggregate_id text NOT NULL,
                event_type text NOT NULL,
                payload jsonb NOT NULL,
                status text NOT NULL,
                available_at timestamptz NOT NULL,
                attempt_count integer NOT NULL,
                last_error text,
                created_at timestamptz NOT NULL,
                published_at timestamptz
            );

            CREATE TABLE public.teacher_source_wide (
                tchr_id varchar(64) PRIMARY KEY,
                real_name text
            );
            INSERT INTO public.teacher_source_wide VALUES
                ('T-001', 'Sensitive Name Must Not Enter Payload'),
                ('T-002', 'Another Name Must Not Enter Payload');

            CREATE TABLE public.lesson_source_wide (
                "课程id" varchar(128) PRIMARY KEY,
                "老师id" varchar(64) NOT NULL,
                "cpu占用过高" boolean,
                "网络延迟过高" boolean
            );
            INSERT INTO public.lesson_source_wide VALUES
                ('LESSON-BOTH', 'T-001', true, false),
                ('LESSON-NETWORK', 'T-002', NULL, true),
                ('LESSON-EMPTY', 'T-003', NULL, NULL);

            INSERT INTO public.outbox_events (
                outbox_id, event_id, aggregate_type, aggregate_id,
                event_type, payload, status, available_at,
                attempt_count, last_error, created_at, published_at
            ) VALUES (
                'OUT-SW-RECON-77-' || md5('T-001'),
                'EVT-SW-RECON-77-' || md5('T-001'),
                'TEACHER_SOURCE_WIDE',
                'T-001',
                'source_wide.changed.v1',
                jsonb_build_object(
                    'source_table', 'teacher_source_wide',
                    'source_id', 'T-001',
                    'operation', 'UPDATE',
                    'changed_fields', jsonb_build_array('feedback_favorite_cnt'),
                    'old_teacher_id', 'T-001',
                    'new_teacher_id', 'T-001'
                ),
                'PENDING', now(), 0, NULL, now(), NULL
            );

            CREATE FUNCTION public.emit_source_wide_change_v1()
            RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE
                old_row jsonb := '{{}}'::jsonb;
                new_row jsonb := '{{}}'::jsonb;
                changed_fields text[] := ARRAY[]::text[];
                event_token text;
                event_time timestamptz := clock_timestamp();
            BEGIN
                IF TG_OP <> 'INSERT' THEN
                    old_row := to_jsonb(OLD);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    new_row := to_jsonb(NEW);
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    SELECT COALESCE(
                        array_agg(field_name ORDER BY field_name),
                        ARRAY[]::text[]
                    )
                    INTO changed_fields
                    FROM (
                        SELECT key_name AS field_name
                        FROM jsonb_object_keys(old_row || new_row)
                            AS keys(key_name)
                        WHERE old_row -> key_name
                            IS DISTINCT FROM new_row -> key_name
                    ) AS changed;
                    IF cardinality(changed_fields) = 0 THEN
                        RETURN NEW;
                    END IF;
                ELSE
                    SELECT COALESCE(
                        array_agg(key_name ORDER BY key_name),
                        ARRAY[]::text[]
                    )
                    INTO changed_fields
                    FROM jsonb_object_keys(new_row) AS keys(key_name);
                END IF;

                event_token := md5(concat_ws(
                    '|', TG_OP, COALESCE(new_row ->> '课程id', old_row ->> '课程id'),
                    txid_current()::text, event_time::text, random()::text
                ));
                INSERT INTO public.outbox_events (
                    outbox_id, event_id, aggregate_type, aggregate_id,
                    event_type, payload, status, available_at,
                    attempt_count, last_error, created_at, published_at
                ) VALUES (
                    'OUT-SOURCE-' || event_token,
                    'EVT-SOURCE-' || event_token,
                    'LESSON_SOURCE_WIDE',
                    COALESCE(new_row ->> '课程id', old_row ->> '课程id'),
                    'source_wide.changed.v1',
                    jsonb_build_object(
                        'source_table', 'lesson_source_wide',
                        'source_id', COALESCE(
                            new_row ->> '课程id', old_row ->> '课程id'
                        ),
                        'operation', TG_OP,
                        'changed_fields', changed_fields,
                        'old_teacher_id', old_row ->> '老师id',
                        'new_teacher_id', new_row ->> '老师id'
                    ),
                    'PENDING', event_time, 0, NULL, event_time, NULL
                );
                RETURN NEW;
            END
            $function$;

            CREATE TRIGGER trg_lesson_source_wide_outbox_v1
            AFTER INSERT OR UPDATE OR DELETE
            ON public.lesson_source_wide
            FOR EACH ROW
            EXECUTE FUNCTION public.emit_source_wide_change_v1();
            """
        )
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for CPU/network retirement",
)
def test_revision_77_real_postgres_cleanup_outbox_lock_and_downgrade(
    tmp_path: Path,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "postgres-data"
    log_path = tmp_path / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        postgres_port = int(probe.getsockname()[1])

    subprocess.run(
        [
            shutil.which("initdb") or "initdb",
            "-D",
            str(data_dir),
            "-A",
            "trust",
            "-U",
            "postgres",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pg_ctl = shutil.which("pg_ctl") or "pg_ctl"
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            (
                f"-p {postgres_port} -c listen_addresses=127.0.0.1 "
                "-c fsync=off"
            ),
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    database_url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_port,
        database="postgres",
    ).render_as_string(hide_password=False)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _seed_revision_76_shape(connection)

        _run_alembic(backend_dir, database_url, "upgrade", REVISION_77)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_77
            assert connection.execute(
                text(
                    """
                    SELECT "课程id", "cpu占用过高", "网络延迟过高"
                    FROM public.lesson_source_wide
                    ORDER BY "课程id"
                    """
                )
            ).all() == [
                ("LESSON-BOTH", None, None),
                ("LESSON-EMPTY", None, None),
                ("LESSON-NETWORK", None, None),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT convalidated
                    FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'public.lesson_source_wide'::regclass
                      AND conname = 'ck_lesson_source_cpu_network_retired_v1'
                    """
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    """
                    SELECT tgenabled
                    FROM pg_catalog.pg_trigger
                    WHERE tgrelid = 'public.lesson_source_wide'::regclass
                      AND tgname = 'trg_lesson_source_wide_outbox_v1'
                      AND NOT tgisinternal
                    """
                )
            ).scalar_one() == "O"
            outboxes = connection.execute(
                text(
                    """
                    SELECT aggregate_id,
                           payload -> 'changed_fields',
                           status
                    FROM public.outbox_events
                    WHERE payload ->> 'source_table' = 'lesson_source_wide'
                    ORDER BY aggregate_id
                    """
                )
            ).all()
            assert outboxes == [
                (
                    "LESSON-BOTH",
                    ["cpu占用过高", "网络延迟过高"],
                    "PENDING",
                ),
                ("LESSON-NETWORK", ["网络延迟过高"], "PENDING"),
            ]
            teacher_outboxes = connection.execute(
                text(
                    """
                    SELECT aggregate_id, payload, status, attempt_count,
                           last_error, published_at
                    FROM public.outbox_events
                    WHERE payload ->> 'source_table' = 'teacher_source_wide'
                      AND outbox_id LIKE 'OUT-SW-RECON-77-%'
                    ORDER BY aggregate_id
                    """
                )
            ).all()
            assert teacher_outboxes == [
                (
                    "T-001",
                    {
                        "source_table": "teacher_source_wide",
                        "source_id": "T-001",
                        "operation": "UPDATE",
                        "changed_fields": ["feedback_favorite_cnt"],
                        "old_teacher_id": "T-001",
                        "new_teacher_id": "T-001",
                    },
                    "PENDING",
                    0,
                    None,
                    None,
                ),
                (
                    "T-002",
                    {
                        "source_table": "teacher_source_wide",
                        "source_id": "T-002",
                        "operation": "UPDATE",
                        "changed_fields": ["feedback_favorite_cnt"],
                        "old_teacher_id": "T-002",
                        "new_teacher_id": "T-002",
                    },
                    "PENDING",
                    0,
                    None,
                    None,
                ),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT count(*) = count(DISTINCT outbox_id)
                       AND count(*) = count(DISTINCT event_id)
                    FROM public.outbox_events
                    WHERE outbox_id LIKE 'OUT-SW-RECON-77-%'
                    """
                )
            ).scalar_one() is True

        with pytest.raises(DBAPIError) as invalid_update:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.lesson_source_wide
                        SET "cpu占用过高" = false
                        WHERE "课程id" = 'LESSON-BOTH'
                        """
                    )
                )
        assert "ck_lesson_source_cpu_network_retired_v1" in str(
            invalid_update.value
        )

        with pytest.raises(DBAPIError) as invalid_insert:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO public.lesson_source_wide
                            ("课程id", "老师id", "网络延迟过高")
                        VALUES ('LESSON-INVALID', 'T-004', true)
                        """
                    )
                )
        assert "ck_lesson_source_cpu_network_retired_v1" in str(
            invalid_insert.value
        )

        _run_alembic(backend_dir, database_url, "downgrade", REVISION_76)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_76
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'public.lesson_source_wide'::regclass
                      AND conname = 'ck_lesson_source_cpu_network_retired_v1'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT "cpu占用过高", "网络延迟过高"
                    FROM public.lesson_source_wide
                    WHERE "课程id" = 'LESSON-NETWORK'
                    """
                )
            ).one() == (None, None)
            connection.execute(
                text(
                    """
                    UPDATE public.lesson_source_wide
                    SET "cpu占用过高" = false,
                        "网络延迟过高" = true
                    WHERE "课程id" = 'LESSON-BOTH'
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT "cpu占用过高", "网络延迟过高"
                    FROM public.lesson_source_wide
                    WHERE "课程id" = 'LESSON-BOTH'
                    """
                )
            ).one() == (False, True)
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
