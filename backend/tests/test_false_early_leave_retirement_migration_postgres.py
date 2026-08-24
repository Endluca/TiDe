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


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")


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


def _run_alembic_expect_failure(
    backend_dir: Path,
    database_url: str,
    expected_error: str,
    *args: str,
) -> None:
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
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, "populated false-early downgrade succeeded"
    assert expected_error in output


def _seed_revision_72_shape(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE public.alembic_version (
                version_num varchar(32) PRIMARY KEY
            );
            INSERT INTO public.alembic_version
            VALUES ('20260822_72_teacher_online_lock');

            CREATE TABLE public.lesson_source_wide (
                "课程id" text PRIMARY KEY,
                "早退" boolean,
                "假早退" boolean,
                "缺席原因明细" text,
                c01 text, c02 text, c03 text, c04 text, c05 text,
                c06 text, c07 text, c08 text, c09 text, c10 text,
                c11 text, c12 text, c13 text, c14 text, c15 text,
                c16 text, c17 text, c18 text, c19 text, c20 text
            );
            CREATE TABLE public.lesson_score_results (
                lesson_id text PRIMARY KEY
            );
            CREATE VIEW public.teacher_lesson_score_current AS
            SELECT
                source."课程id" AS lesson_id,
                jsonb_build_object(
                    'attendance', jsonb_build_object(
                        'is_early', source."早退",
                        'is_false_early_leave', source."假早退",
                        'absence_reason_detail', source."缺席原因明细"
                    )
                ) AS business_facts
            FROM public.lesson_source_wide AS source
            JOIN public.lesson_score_results AS result
              ON result.lesson_id = source."课程id";

            INSERT INTO public.lesson_score_results VALUES ('LESSON-1');
            INSERT INTO public.lesson_source_wide (
                "课程id", "早退", "假早退", "缺席原因明细"
            ) VALUES ('LESSON-1', false, true, 'No Notification');
            """
        )
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for false-early retirement",
)
def test_revision_73_real_postgres_view_column_and_downgrade_guards(
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
            _seed_revision_72_shape(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_73_retire_false_early",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_73_retire_false_early"
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                    """
                )
            ).scalar_one() == 23
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
                    """
                )
            ).scalar_one() == 0
            attendance = connection.execute(
                text(
                    """
                    SELECT business_facts -> 'attendance'
                    FROM public.teacher_lesson_score_current
                    WHERE lesson_id = 'LESSON-1'
                    """
                )
            ).scalar_one()
            assert attendance == {
                "is_early": False,
                "absence_reason_detail": "No Notification",
            }

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "retired values cannot be restored for existing lessons",
            "downgrade",
            "20260822_72_teacher_online_lock",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_73_retire_false_early"
            connection.execute(
                text("DELETE FROM public.lesson_source_wide")
            )

        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_72_teacher_online_lock",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_72_teacher_online_lock"
            restored = connection.execute(
                text(
                    """
                    SELECT data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
                    """
                )
            ).one()
            assert restored == ("boolean", "YES")
            connection.execute(
                text(
                    """
                    INSERT INTO public.lesson_source_wide (
                        "课程id", "早退", "假早退", "缺席原因明细"
                    ) VALUES ('LESSON-1', false, true, 'No Notification')
                    """
                )
            )
            attendance = connection.execute(
                text(
                    """
                    SELECT business_facts -> 'attendance'
                    FROM public.teacher_lesson_score_current
                    WHERE lesson_id = 'LESSON-1'
                    """
                )
            ).scalar_one()
            assert attendance["is_false_early_leave"] is True
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
