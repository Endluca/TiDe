from __future__ import annotations

from datetime import timedelta
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


def _seed_revision_71_shape(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE public.alembic_version (
                version_num varchar(32) PRIMARY KEY
            );
            INSERT INTO public.alembic_version
            VALUES ('20260822_71_dom_teacher_area');

            CREATE TABLE public.teachers (
                teacher_id varchar(64) PRIMARY KEY,
                source_snapshot_label varchar(128),
                online_status varchar(32),
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT ck_teachers_online_status_v2 CHECK (
                    online_status IS NULL OR online_status IN
                    ('NEW', 'EXISTING', 'LEFT', 'BLOCKED')
                )
            );
            CREATE INDEX ix_teachers_source_snapshot_label
            ON public.teachers (source_snapshot_label);

            CREATE TABLE public.teacher_source_wide (
                tchr_id varchar(64) PRIMARY KEY,
                status text,
                status_on_date date,
                teach_area_type text
            );

            CREATE TABLE public.teacher_qualifications (
                teacher_id varchar(64) PRIMARY KEY
                    REFERENCES public.teachers(teacher_id),
                graduation_qualified boolean NOT NULL DEFAULT false,
                graduation_score_locked double precision,
                revision integer NOT NULL DEFAULT 1,
                CONSTRAINT ck_teacher_qualification_graduation_score_locked_v2
                    CHECK (
                        graduation_score_locked IS NULL OR (
                            graduation_qualified IS TRUE
                            AND graduation_score_locked = 100.0
                        )
                    )
            );

            CREATE FUNCTION public.guard_qualification_revision_v72_test()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $function$
            BEGIN
                IF NEW.revision <> OLD.revision + 1 THEN
                    RAISE EXCEPTION
                        'QUALIFICATION_REVISION_MUST_INCREMENT';
                END IF;
                RETURN NEW;
            END
            $function$;
            CREATE TRIGGER trg_guard_qualification_revision_v72_test
            BEFORE UPDATE ON public.teacher_qualifications
            FOR EACH ROW
            EXECUTE FUNCTION public.guard_qualification_revision_v72_test();

            INSERT INTO public.teachers (
                teacher_id, source_snapshot_label, online_status
            ) VALUES
                ('T-DAY-0', 'SOURCE_WIDE_CURRENT', 'BLOCKED'),
                ('T-DAY-29', 'SOURCE_WIDE_CURRENT', 'BLOCKED'),
                ('T-DAY-30', 'SOURCE_WIDE_CURRENT', 'BLOCKED'),
                ('T-OFF', 'SOURCE_WIDE_CURRENT', 'NEW'),
                ('T-HEI', 'SOURCE_WIDE_CURRENT', 'NEW'),
                ('T-UNKNOWN', 'SOURCE_WIDE_CURRENT', 'EXISTING'),
                ('T-STATUS-MISSING', 'SOURCE_WIDE_CURRENT', 'EXISTING'),
                ('T-FUTURE', 'SOURCE_WIDE_CURRENT', 'EXISTING'),
                ('T-SOURCE-MISSING', 'SOURCE_WIDE_CURRENT', 'NEW'),
                ('T-LOCAL', 'LOCAL_MOCK_DEMO', 'LEFT'),
                ('T-QUAL-CANDIDATE', 'LOCAL_MOCK_DEMO', NULL),
                ('T-LOCK-CANDIDATE', 'LOCAL_MOCK_DEMO', NULL);
            UPDATE public.teachers
            SET payload = '{"preserved":"yes"}'::jsonb
            WHERE teacher_id = 'T-DAY-29';

            WITH business_clock AS (
                SELECT
                    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date
                        AS business_date
            )
            INSERT INTO public.teacher_source_wide (
                tchr_id, status, status_on_date, teach_area_type
            )
            SELECT fixture.tchr_id,
                   fixture.status,
                   CASE
                       WHEN fixture.day_offset IS NULL THEN NULL
                       ELSE clock.business_date - fixture.day_offset
                   END,
                   'dom'
            FROM (
                VALUES
                    ('T-DAY-0', ' On ', 0),
                    ('T-DAY-29', 'oN', 29),
                    ('T-DAY-30', 'ON', 30),
                    ('T-OFF', ' OFF ', NULL),
                    ('T-HEI', ' Hei ', NULL),
                    ('T-UNKNOWN', 'paused', 60),
                    ('T-STATUS-MISSING', NULL, 60),
                    ('T-FUTURE', 'on', -1),
                    ('T-LOCAL', 'on', 60)
            ) AS fixture(tchr_id, status, day_offset)
            CROSS JOIN business_clock AS clock;

            INSERT INTO public.teacher_qualifications (
                teacher_id, graduation_qualified,
                graduation_score_locked, revision
            ) VALUES
                ('T-LOCAL', true, NULL, 7),
                ('T-QUAL-CANDIDATE', false, NULL, 2),
                ('T-LOCK-CANDIDATE', false, NULL, 4);
            """
        )
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for teacher-state guards",
)
def test_revision_72_backfill_constraints_and_safe_downgrade(
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
            _seed_revision_71_shape(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_72_teacher_online_lock",
        )

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_72_teacher_online_lock"
            statuses = dict(
                connection.execute(
                    text(
                        """
                        SELECT teacher_id, online_status
                        FROM public.teachers
                        ORDER BY teacher_id
                        """
                    )
                ).all()
            )
            assert statuses == {
                "T-DAY-0": "NEW",
                "T-DAY-29": "NEW",
                "T-DAY-30": "EXISTING",
                "T-FUTURE": None,
                "T-HEI": "BLOCKED",
                "T-LOCAL": "LEFT",
                "T-LOCK-CANDIDATE": None,
                "T-OFF": "LEFT",
                "T-QUAL-CANDIDATE": None,
                "T-SOURCE-MISSING": None,
                "T-STATUS-MISSING": None,
                "T-UNKNOWN": None,
            }

            business_date = connection.execute(
                text(
                    """
                    SELECT
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date
                    """
                )
            ).scalar_one()
            day_29_payload = connection.execute(
                text(
                    """
                    SELECT payload
                    FROM public.teachers
                    WHERE teacher_id = 'T-DAY-29'
                    """
                )
            ).scalar_one()
            assert day_29_payload == {
                "preserved": "yes",
                "employment_status": "oN",
                "online_status_onboard_date": str(
                    business_date - timedelta(days=29)
                ),
                "online_status_evidence_status": "CONFIRMED",
                "online_status_business_date": str(business_date),
            }
            missing_payload = connection.execute(
                text(
                    """
                    SELECT payload
                    FROM public.teachers
                    WHERE teacher_id = 'T-SOURCE-MISSING'
                    """
                )
            ).scalar_one()
            assert missing_payload == {
                "employment_status": None,
                "online_status_onboard_date": None,
                "online_status_evidence_status": "SOURCE_MISSING",
                "online_status_business_date": str(business_date),
            }
            assert connection.execute(
                text(
                    """
                    SELECT payload
                    FROM public.teachers
                    WHERE teacher_id = 'T-LOCAL'
                    """
                )
            ).scalar_one() == {}

            qualified = connection.execute(
                text(
                    """
                    SELECT graduation_score_locked, revision
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-LOCAL'
                    """
                )
            ).one()
            assert qualified == (100.0, 8)

            index_definition = connection.execute(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname =
                        'ix_teachers_source_snapshot_online_status_teacher'
                    """
                )
            ).scalar_one()
            assert (
                "(source_snapshot_label, online_status, teacher_id)"
                in index_definition
            )

        with pytest.raises(
            DBAPIError,
            match=(
                "ck_teacher_qualification_"
                "graduation_requires_score_locked_v2"
            ),
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_qualifications
                        SET graduation_qualified = true,
                            revision = revision + 1
                        WHERE teacher_id = 'T-QUAL-CANDIDATE'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="ck_teacher_qualification_graduation_score_locked_v2",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_qualifications
                        SET graduation_score_locked = 100.0,
                            revision = revision + 1
                        WHERE teacher_id = 'T-LOCK-CANDIDATE'
                        """
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_qualifications
                    SET graduation_qualified = true,
                        graduation_score_locked = 100.0,
                        revision = revision + 1
                    WHERE teacher_id = 'T-QUAL-CANDIDATE'
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT graduation_qualified,
                           graduation_score_locked, revision
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-QUAL-CANDIDATE'
                    """
                )
            ).one() == (True, 100.0, 3)

        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_71_dom_teacher_area",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_71_dom_teacher_area"
            # Revision rollback removes enforcement objects, never the facts.
            assert connection.execute(
                text(
                    """
                    SELECT online_status
                    FROM public.teachers
                    WHERE teacher_id = 'T-DAY-30'
                    """
                )
            ).scalar_one() == "EXISTING"
            assert connection.execute(
                text(
                    """
                    SELECT payload ->> 'online_status_onboard_date'
                    FROM public.teachers
                    WHERE teacher_id = 'T-DAY-30'
                    """
                )
            ).scalar_one() == str(business_date - timedelta(days=30))
            assert connection.execute(
                text(
                    """
                    SELECT graduation_score_locked
                    FROM public.teacher_qualifications
                    WHERE teacher_id = 'T-LOCAL'
                    """
                )
            ).scalar_one() == 100.0
            assert connection.execute(
                text(
                    """
                    SELECT to_regclass(
                        'public.ix_teachers_source_snapshot_online_status_teacher'
                    )
                    """
                )
            ).scalar_one() is None
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint
                    WHERE conname =
                        'ck_teacher_qualification_graduation_requires_score_locked_v2'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint
                    WHERE conname =
                        'ck_teacher_qualification_graduation_score_locked_v2'
                    """
                )
            ).scalar_one() == 1
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
