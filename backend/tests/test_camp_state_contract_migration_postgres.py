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
    assert result.returncode != 0, "unknown camp state migration succeeded"
    assert expected_error in output


def _seed_revision_74_shape(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version
            VALUES ('20260822_74_favorite_schema');

            CREATE TABLE public.teachers (
                teacher_id varchar(64) PRIMARY KEY,
                graduation_state varchar(32) NOT NULL,
                online_status varchar(32),
                gold_qualified boolean NOT NULL DEFAULT false,
                total_score double precision NOT NULL DEFAULT 0,
                payload jsonb NOT NULL DEFAULT '{}'::jsonb
            );
            CREATE TABLE public.teacher_qualifications (
                teacher_id varchar(64) PRIMARY KEY
                    REFERENCES public.teachers(teacher_id),
                graduation_qualified boolean NOT NULL DEFAULT false,
                gold_qualified boolean NOT NULL DEFAULT false,
                revision integer NOT NULL DEFAULT 1
            );

            CREATE VIEW public.teacher_scorecard_current AS
            SELECT
                teacher.teacher_id,
                (
                    CASE
                        WHEN qualification.graduation_qualified
                            THEN 'GRADUATED'
                        ELSE 'IN_PROGRESS'
                    END
                )::varchar(32) AS graduation_state
            FROM public.teachers AS teacher
            LEFT JOIN public.teacher_qualifications AS qualification
              ON qualification.teacher_id = teacher.teacher_id;

            CREATE FUNCTION public.guard_teacher_qualification_reversal()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF OLD.graduation_state = 'GRADUATED'
                   AND NEW.graduation_state <> 'GRADUATED' THEN
                    RAISE EXCEPTION
                        'GRADUATION_QUALIFICATION_IRREVERSIBLE';
                END IF;
                IF OLD.gold_qualified AND NOT NEW.gold_qualified THEN
                    RAISE EXCEPTION 'GOLD_QUALIFICATION_IRREVERSIBLE';
                END IF;
                RETURN NEW;
            END
            $function$;

            CREATE TRIGGER trg_guard_teacher_qualification_reversal
            BEFORE UPDATE OF graduation_state, gold_qualified
            ON public.teachers
            FOR EACH ROW
            EXECUTE FUNCTION public.guard_teacher_qualification_reversal();

            INSERT INTO public.teachers (
                teacher_id, graduation_state, online_status,
                gold_qualified, total_score, payload
            ) VALUES
                (
                    'T-PROGRESS', 'IN_PROGRESS', 'LEFT', false, 40,
                    '{"graduation_state":"IN_PROGRESS"}'::jsonb
                ),
                (
                    'T-NOT-CAMP', 'NOT_IN_CAMP', 'BLOCKED', false, 50,
                    '{"graduation_state":"NOT_IN_CAMP"}'::jsonb
                ),
                (
                    'T-IN-CAMP', 'IN_CAMP', 'EXISTING', false, 60,
                    '{}'::jsonb
                ),
                (
                    'T-GRADUATED', 'GRADUATED', 'LEFT', true, 250,
                    '{"graduation_state":"IN_PROGRESS"}'::jsonb
                ),
                (
                    'T-UNKNOWN', 'PAUSED', 'NEW', false, 0,
                    '{"graduation_state":"PAUSED"}'::jsonb
                );

            INSERT INTO public.teacher_qualifications (
                teacher_id, graduation_qualified, gold_qualified, revision
            ) VALUES ('T-GRADUATED', true, true, 9);
            """
        )
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for camp-state migration",
)
def test_revision_75_real_postgres_backfill_constraint_and_safe_downgrade(
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
            _seed_revision_74_shape(connection)

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "CAMP_STATE_UNKNOWN:{PAUSED}",
            "upgrade",
            "20260822_75_camp_state_contract",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_74_favorite_schema"
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state
                    FROM public.teachers
                    WHERE teacher_id = 'T-PROGRESS'
                    """
                )
            ).scalar_one() == "IN_PROGRESS"
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state
                    FROM public.teacher_scorecard_current
                    WHERE teacher_id = 'T-PROGRESS'
                    """
                )
            ).scalar_one() == "IN_PROGRESS"
            connection.execute(
                text("DELETE FROM public.teachers WHERE teacher_id = 'T-UNKNOWN'")
            )

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_75_camp_state_contract",
        )
        with engine.begin() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT teacher_id, graduation_state,
                           payload ->> 'graduation_state' AS payload_state,
                           online_status, total_score
                    FROM public.teachers
                    ORDER BY teacher_id
                    """
                )
            ).all()
            assert rows == [
                ("T-GRADUATED", "GRADUATED", "GRADUATED", "LEFT", 250.0),
                ("T-IN-CAMP", "IN_CAMP", "IN_CAMP", "EXISTING", 60.0),
                ("T-NOT-CAMP", "IN_CAMP", "IN_CAMP", "BLOCKED", 50.0),
                ("T-PROGRESS", "IN_CAMP", "IN_CAMP", "LEFT", 40.0),
            ]
            scorecard_rows = connection.execute(
                text(
                    """
                    SELECT teacher_id, graduation_state
                    FROM public.teacher_scorecard_current
                    WHERE teacher_id IN ('T-GRADUATED', 'T-PROGRESS')
                    ORDER BY teacher_id
                    """
                )
            ).all()
            assert scorecard_rows == [
                ("T-GRADUATED", "GRADUATED"),
                ("T-PROGRESS", "IN_CAMP"),
            ]
            qualification = connection.execute(
                text(
                    """
                    SELECT graduation_qualified, gold_qualified, revision
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-GRADUATED'
                    """
                )
            ).one()
            assert qualification == (True, True, 9)

            # Online state does not gate score accumulation.
            connection.execute(
                text(
                    """
                    UPDATE public.teachers
                    SET total_score = total_score + 5
                    WHERE online_status IN ('LEFT', 'BLOCKED')
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT teacher_id, total_score
                    FROM public.teachers
                    WHERE teacher_id IN ('T-PROGRESS', 'T-NOT-CAMP')
                    ORDER BY teacher_id
                    """
                )
            ).all() == [("T-NOT-CAMP", 55.0), ("T-PROGRESS", 45.0)]

        with pytest.raises(DBAPIError) as invalid_state:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET graduation_state = 'IN_PROGRESS'
                        WHERE teacher_id = 'T-PROGRESS'
                        """
                    )
                )
        assert "ck_teachers_graduation_state_v2" in str(invalid_state.value)

        with pytest.raises(DBAPIError) as column_only:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET graduation_state = 'GRADUATED'
                        WHERE teacher_id = 'T-PROGRESS'
                        """
                    )
                )
        assert "ck_teachers_graduation_state_v2" in str(column_only.value)

        with pytest.raises(DBAPIError) as payload_only:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET payload = jsonb_set(
                            payload,
                            '{graduation_state}',
                            '"GRADUATED"'::jsonb,
                            true
                        )
                        WHERE teacher_id = 'T-PROGRESS'
                        """
                    )
                )
        assert "ck_teachers_graduation_state_v2" in str(payload_only.value)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.teachers
                    SET graduation_state = 'GRADUATED',
                        payload = jsonb_set(
                            payload,
                            '{graduation_state}',
                            '"GRADUATED"'::jsonb,
                            true
                        )
                    WHERE teacher_id = 'T-IN-CAMP'
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state, payload ->> 'graduation_state'
                    FROM public.teachers
                    WHERE teacher_id = 'T-IN-CAMP'
                    """
                )
            ).one() == ("GRADUATED", "GRADUATED")

        with pytest.raises(DBAPIError) as irreversible:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET graduation_state = 'IN_CAMP'
                        WHERE teacher_id = 'T-GRADUATED'
                        """
                    )
                )
        assert "GRADUATION_QUALIFICATION_IRREVERSIBLE" in str(
            irreversible.value
        )

        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_74_favorite_schema",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_74_favorite_schema"
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'public.teachers'::regclass
                      AND conname = 'ck_teachers_graduation_state_v2'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state, payload ->> 'graduation_state'
                    FROM public.teachers
                    WHERE teacher_id = 'T-GRADUATED'
                    """
                )
            ).one() == ("GRADUATED", "GRADUATED")
            assert connection.execute(
                text(
                    """
                    SELECT graduation_qualified, gold_qualified, revision
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-GRADUATED'
                    """
                )
            ).one() == (True, True, 9)
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state
                    FROM public.teacher_scorecard_current
                    WHERE teacher_id = 'T-PROGRESS'
                    """
                )
            ).scalar_one() == "IN_CAMP"

        with pytest.raises(DBAPIError) as downgrade_irreversible:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teachers
                        SET graduation_state = 'IN_CAMP'
                        WHERE teacher_id = 'T-GRADUATED'
                        """
                    )
                )
        assert "GRADUATION_QUALIFICATION_IRREVERSIBLE" in str(
            downgrade_irreversible.value
        )
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
