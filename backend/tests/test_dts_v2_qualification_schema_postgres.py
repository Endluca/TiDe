from __future__ import annotations

from datetime import datetime, timezone
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
    assert result.returncode != 0, "qualification downgrade unexpectedly succeeded"
    assert expected_error in output


def _seed_revision_69_shape(
    connection,
    *,
    include_qualified_teacher: bool = True,
) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE public.alembic_version (
                version_num varchar(32) PRIMARY KEY
            );
            DO $role$
            BEGIN
                IF to_regrole('tit_growth_app') IS NULL THEN
                    CREATE ROLE tit_growth_app NOLOGIN;
                END IF;
            END
            $role$;
            GRANT USAGE ON SCHEMA public TO tit_growth_app;
            INSERT INTO public.alembic_version
            VALUES ('20260822_69_dts_v2_source_guard');

            CREATE TABLE public.teachers (
                teacher_id varchar(64) PRIMARY KEY,
                graduation_state varchar(32) NOT NULL,
                gold_qualified boolean NOT NULL DEFAULT false
            );

            CREATE TABLE public.teacher_qualifications (
                teacher_id varchar(64) PRIMARY KEY
                    REFERENCES public.teachers(teacher_id),
                graduation_criteria_met boolean NOT NULL DEFAULT false,
                graduation_qualified boolean NOT NULL DEFAULT false,
                graduation_qualified_at timestamptz,
                gold_criteria_met boolean NOT NULL DEFAULT false,
                gold_qualified boolean NOT NULL DEFAULT false,
                gold_qualified_at timestamptz,
                score_rule_version varchar(64) NOT NULL,
                gate_results jsonb NOT NULL DEFAULT '{}'::jsonb,
                revision integer NOT NULL DEFAULT 1,
                calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                CONSTRAINT ck_teacher_qualification_gold_requires_graduation
                    CHECK (
                        gold_qualified = false
                        OR graduation_qualified = true
                    ),
                CONSTRAINT ck_teacher_qualification_graduation_time
                    CHECK (
                        graduation_qualified = true
                        OR graduation_qualified_at IS NULL
                    ),
                CONSTRAINT ck_teacher_qualification_gold_time
                    CHECK (
                        gold_qualified = true
                        OR gold_qualified_at IS NULL
                    ),
                CONSTRAINT ck_teacher_qualification_revision
                    CHECK (revision >= 1)
            );

            CREATE FUNCTION public.guard_teacher_qualification_fact()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $function$
            BEGIN
                IF OLD.graduation_qualified
                   AND NOT NEW.graduation_qualified THEN
                    RAISE EXCEPTION
                        'GRADUATION_QUALIFICATION_IRREVERSIBLE';
                END IF;
                IF OLD.gold_qualified AND NOT NEW.gold_qualified THEN
                    RAISE EXCEPTION 'GOLD_QUALIFICATION_IRREVERSIBLE';
                END IF;
                IF OLD.graduation_qualified_at IS NOT NULL
                   AND NEW.graduation_qualified_at
                       IS DISTINCT FROM OLD.graduation_qualified_at THEN
                    RAISE EXCEPTION
                        'GRADUATION_QUALIFIED_AT_IMMUTABLE';
                END IF;
                IF OLD.gold_qualified_at IS NOT NULL
                   AND NEW.gold_qualified_at
                       IS DISTINCT FROM OLD.gold_qualified_at THEN
                    RAISE EXCEPTION 'GOLD_QUALIFIED_AT_IMMUTABLE';
                END IF;
                IF NEW.revision <> OLD.revision + 1 THEN
                    RAISE EXCEPTION
                        'QUALIFICATION_REVISION_MUST_INCREMENT';
                END IF;
                RETURN NEW;
            END
            $function$;

            CREATE TRIGGER trg_guard_teacher_qualification_fact
            BEFORE UPDATE ON public.teacher_qualifications
            FOR EACH ROW
            EXECUTE FUNCTION public.guard_teacher_qualification_fact();

            GRANT SELECT, INSERT, UPDATE, DELETE
            ON TABLE public.teacher_qualifications
            TO tit_growth_app;

            INSERT INTO public.teachers (
                teacher_id, graduation_state, gold_qualified
            ) VALUES
                ('T-IN-PROGRESS', 'IN_PROGRESS', false),
                ('T-GRADUATED', 'GRADUATED', false),
                ('T-NOT-IN-CAMP', 'NOT_IN_CAMP', false);

            INSERT INTO public.teacher_qualifications (
                teacher_id,
                graduation_qualified,
                graduation_qualified_at,
                score_rule_version,
                revision
            ) VALUES
                ('T-IN-PROGRESS', false, NULL, 'v1', 3),
                (
                    'T-GRADUATED', true,
                    '2026-08-01T02:03:04+00'::timestamptz,
                    'v1', 7
                ),
                ('T-NOT-IN-CAMP', false, NULL, 'v1', 2);
            """
        )
    )
    if not include_qualified_teacher:
        connection.execute(
            text(
                """
                DELETE FROM public.teacher_qualifications
                WHERE teacher_id = 'T-GRADUATED';
                DELETE FROM public.teachers
                WHERE teacher_id = 'T-GRADUATED';
                """
            )
        )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for qualification guards",
)
def test_revision_70_migrates_and_guards_confirmed_qualification_facts(
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
    downgrade_engine = None
    try:
        with engine.begin() as connection:
            _seed_revision_69_shape(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_70_qualification_schema",
        )

        with engine.begin() as connection:
            teachers = connection.execute(
                text(
                    """
                    SELECT teacher_id, online_status, graduation_state
                    FROM public.teachers
                    ORDER BY teacher_id
                    """
                )
            ).mappings().all()
            assert teachers == [
                {
                    "teacher_id": "T-GRADUATED",
                    "online_status": None,
                    "graduation_state": "GRADUATED",
                },
                {
                    "teacher_id": "T-IN-PROGRESS",
                    "online_status": None,
                    "graduation_state": "IN_PROGRESS",
                },
                {
                    "teacher_id": "T-NOT-IN-CAMP",
                    "online_status": None,
                    "graduation_state": "NOT_IN_CAMP",
                },
            ]

            qualifications = connection.execute(
                text(
                    """
                    SELECT teacher_id, graduation_qualified,
                           graduation_qualified_at,
                           graduation_score_locked, revision
                    FROM public.teacher_qualifications
                    ORDER BY teacher_id
                    """
                )
            ).mappings().all()
            assert qualifications[0]["teacher_id"] == "T-GRADUATED"
            assert qualifications[0]["graduation_score_locked"] == 100.0
            assert qualifications[0]["revision"] == 8
            assert qualifications[0]["graduation_qualified_at"] == datetime(
                2026,
                8,
                1,
                2,
                3,
                4,
                tzinfo=timezone.utc,
            )
            for qualification in qualifications[1:]:
                assert qualification["graduation_qualified"] is False
                assert qualification["graduation_qualified_at"] is None
                assert qualification["graduation_score_locked"] is None

            for online_status in ("NEW", "EXISTING", "LEFT", "BLOCKED"):
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET online_status = :online_status
                        WHERE teacher_id = 'T-IN-PROGRESS'
                        """
                    ),
                    {"online_status": online_status},
                )
            assert connection.execute(
                text(
                    """
                    SELECT has_column_privilege(
                        'tit_growth_app',
                        'public.teacher_qualifications',
                        'graduation_score_locked',
                        'UPDATE'
                    )
                    """
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    """
                    SELECT has_column_privilege(
                        'tit_growth_app',
                        'public.teacher_qualifications',
                        'teacher_id',
                        'UPDATE'
                    )
                    """
                )
            ).scalar_one() is True

        with pytest.raises(DBAPIError, match="ck_teachers_online_status_v2"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET online_status = 'UNKNOWN'
                        WHERE teacher_id = 'T-IN-PROGRESS'
                        """
                    )
                )

        # The compatibility writer may still use its legacy camp value between
        # expand and cutover.  The final two-value CHECK is installed only with
        # the v2 writer contraction, never in this expand migration.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.teachers
                    SET graduation_state = 'IN_PROGRESS'
                    WHERE teacher_id = 'T-IN-PROGRESS'
                    """
                )
            )

            # The old writer can still earn graduation without knowing the
            # new lock column.  A later v2 rebuild must fill the lock before
            # the contraction makes it mandatory.
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_qualifications
                    SET graduation_qualified = true,
                        graduation_qualified_at =
                            '2026-08-22T00:00:00+00'::timestamptz,
                        revision = revision + 1
                    WHERE teacher_id = 'T-IN-PROGRESS'
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT graduation_score_locked
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-IN-PROGRESS'
                    """
                )
            ).scalar_one() is None

        with pytest.raises(
            DBAPIError,
            match="GRADUATION_SCORE_LOCKED_IMMUTABLE",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_qualifications
                        SET graduation_score_locked = NULL,
                            revision = revision + 1
                        WHERE teacher_id = 'T-GRADUATED'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match=(
                "ck_teacher_qualification_graduation_score_locked_v2"
            ),
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_qualifications
                        SET graduation_score_locked = 100.0,
                            revision = revision + 1
                        WHERE teacher_id = 'T-NOT-IN-CAMP'
                        """
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_qualifications
                    SET gold_criteria_met = true,
                        revision = revision + 1
                    WHERE teacher_id = 'T-GRADUATED'
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT graduation_score_locked
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-GRADUATED'
                    """
                )
            ).scalar_one() == 100.0

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing qualification schema downgrade: v2 qualification facts exist",
            "downgrade",
            "20260822_69_dts_v2_source_guard",
        )

        # Existing domain rows alone are not v2 facts.  A second database in
        # the same real PostgreSQL cluster proves that an immediate rollback is
        # allowed when both newly added columns are still NULL.
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text("CREATE DATABASE qualification_downgrade"))
        downgrade_url = URL.create(
            "postgresql+psycopg",
            username="postgres",
            host="127.0.0.1",
            port=postgres_port,
            database="qualification_downgrade",
        ).render_as_string(hide_password=False)
        downgrade_engine = create_engine(downgrade_url)
        with downgrade_engine.begin() as connection:
            _seed_revision_69_shape(
                connection,
                include_qualified_teacher=False,
            )
        _run_alembic(
            backend_dir,
            downgrade_url,
            "upgrade",
            "20260822_70_qualification_schema",
        )
        with downgrade_engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.teachers
                    WHERE online_status IS NOT NULL
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.teacher_qualifications
                    WHERE graduation_score_locked IS NOT NULL
                    """
                )
            ).scalar_one() == 0
        _run_alembic(
            backend_dir,
            downgrade_url,
            "downgrade",
            "20260822_69_dts_v2_source_guard",
        )
        with downgrade_engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_69_dts_v2_source_guard"
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND (
                            (table_name = 'teachers'
                             AND column_name = 'online_status')
                            OR (
                                table_name = 'teacher_qualifications'
                                AND column_name = 'graduation_score_locked'
                            )
                      )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM public.teachers")
            ).scalar_one() == 2
    finally:
        if downgrade_engine is not None:
            downgrade_engine.dispose()
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
