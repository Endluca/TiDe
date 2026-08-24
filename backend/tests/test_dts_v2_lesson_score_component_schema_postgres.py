from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_75 = "20260822_75_camp_state_contract"
REVISION_76 = "20260822_76_lesson_score_components"
REVISION_77 = "20260822_77_retire_cpu_network"
REVISION_78 = "20260822_78_pending_score_guard"


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
    assert result.returncode != 0, "lesson component downgrade unexpectedly succeeded"
    assert expected_error in output


@pytest.fixture(scope="module")
def postgres_server(tmp_path_factory):
    if not _postgres_tools_available():
        pytest.skip("local PostgreSQL binaries are required for lesson score guards")

    root = tmp_path_factory.mktemp("lesson-component-postgres")
    data_dir = root / "data"
    log_path = root / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

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
            f"-p {port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield port
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.fixture()
def database_url(postgres_server: int):
    database_name = f"lesson_component_{uuid4().hex[:12]}"
    admin_url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_server,
        database="postgres",
    )
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_server,
        database=database_name,
    ).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE "{database_name}" WITH (FORCE)')
            )
        admin_engine.dispose()


def _seed_revision_75_shape(database_url: str, *, legacy_result: bool = False) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE public.alembic_version (
                    version_num varchar(32) PRIMARY KEY
                );
                INSERT INTO public.alembic_version VALUES ('{REVISION_75}');

                CREATE TABLE public.source_courses (
                    source_region varchar(8) NOT NULL,
                    source_appoint_id varchar(512) NOT NULL,
                    completion_participation_seq integer,
                    completion_teacher_id varchar(64),
                    completion_voided_at timestamptz,
                    completion_conflict_status varchar(32) NOT NULL
                        DEFAULT 'NONE',
                    PRIMARY KEY (source_region, source_appoint_id)
                );

                CREATE TABLE public.source_course_participations (
                    source_region varchar(8) NOT NULL,
                    source_appoint_id varchar(512) NOT NULL,
                    participation_seq integer NOT NULL,
                    teacher_id varchar(64) NOT NULL,
                    participation_role varchar(40) NOT NULL,
                    PRIMARY KEY (
                        source_region, source_appoint_id, participation_seq
                    ),
                    FOREIGN KEY (source_region, source_appoint_id)
                        REFERENCES public.source_courses (
                            source_region, source_appoint_id
                        ) DEFERRABLE INITIALLY DEFERRED
                );

                CREATE TABLE public.score_entries (
                    score_entry_id varchar(128) PRIMARY KEY,
                    lesson_id varchar(128),
                    teacher_id varchar(64) NOT NULL,
                    dimension varchar(32) NOT NULL,
                    entry_type varchar(32) NOT NULL,
                    delta_score double precision NOT NULL,
                    reason_code varchar(128) NOT NULL,
                    evidence_status varchar(32) NOT NULL,
                    score_rule_version varchar(64) NOT NULL,
                    reversal_of_score_entry_id varchar(128),
                    idempotency_key varchar(256) NOT NULL UNIQUE,
                    payload jsonb NOT NULL
                );

                CREATE TABLE public.lesson_score_results (
                    lesson_id varchar(128) PRIMARY KEY,
                    projection_revision integer NOT NULL DEFAULT 1
                );

                CREATE TABLE public.lesson_source_wide (
                    "课程id" varchar(128) PRIMARY KEY,
                    "老师id" varchar(64),
                    "cpu占用过高" boolean,
                    "网络延迟过高" boolean
                );

                CREATE TABLE public.teacher_source_wide (
                    tchr_id varchar(64) PRIMARY KEY
                );

                CREATE TABLE public.outbox_events (
                    outbox_id varchar(160) PRIMARY KEY,
                    event_id varchar(160) NOT NULL UNIQUE,
                    aggregate_type varchar(64) NOT NULL,
                    aggregate_id varchar(160) NOT NULL,
                    event_type varchar(160) NOT NULL,
                    payload jsonb NOT NULL,
                    status varchar(32) NOT NULL,
                    available_at timestamptz NOT NULL,
                    attempt_count integer NOT NULL,
                    last_error text,
                    created_at timestamptz NOT NULL,
                    published_at timestamptz
                );

                CREATE FUNCTION public.test_lesson_source_outbox_trigger()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $trigger$
                BEGIN
                    RETURN NEW;
                END
                $trigger$;

                CREATE TRIGGER trg_lesson_source_wide_outbox_v1
                AFTER UPDATE ON public.lesson_source_wide
                FOR EACH ROW
                EXECUTE FUNCTION public.test_lesson_source_outbox_trigger();

                DO $role$
                BEGIN
                    IF to_regrole('tit_growth_app') IS NULL THEN
                        CREATE ROLE tit_growth_app NOLOGIN;
                    END IF;
                END
                $role$;
                GRANT USAGE ON SCHEMA public TO tit_growth_app;
                GRANT SELECT, INSERT, UPDATE
                ON public.lesson_score_results TO tit_growth_app;

                INSERT INTO public.source_courses (
                    source_region,
                    source_appoint_id,
                    completion_participation_seq,
                    completion_teacher_id
                ) VALUES ('dom', '9001', 1, 'A');
                INSERT INTO public.source_course_participations (
                    source_region,
                    source_appoint_id,
                    participation_seq,
                    teacher_id,
                    participation_role
                ) VALUES ('dom', '9001', 1, 'A', 'COMPLETION');
                """
            )
        )
        if legacy_result:
            connection.execute(
                text(
                    """
                    INSERT INTO public.lesson_score_results (
                        lesson_id, projection_revision
                    ) VALUES ('LEGACY-9001', 7)
                    """
                )
            )
    engine.dispose()


def _payload(
    *,
    region: str = "dom",
    appoint_id: str = "9001",
    seq: int,
    component: str,
    generation: int,
    evidence_hash: str,
) -> str:
    return json.dumps(
        {
            "settlement_contract": "lesson-score-component-v2",
            "source_region": region,
            "source_appoint_id": appoint_id,
            "completion_participation_seq": seq,
            "component_code": component,
            "award_generation": generation,
            "evidence_fingerprint": evidence_hash,
        },
        separators=(",", ":"),
    )


def _insert_entry(
    connection,
    *,
    entry_id: str,
    teacher_id: str,
    component: str,
    generation: int,
    seq: int,
    score: int,
    rule_version: str,
    evidence_hash: str,
    reversal_of: str | None = None,
    reason_code: str | None = None,
) -> None:
    dimensions = {
        "FEEDBACK_PRAISE": "USER_FEEDBACK",
        "PERFECT_COMPLETED": "RELIABILITY",
        "PEAK_COMPLETED": "RELIABILITY",
        "CLASS_QUALITY_HARDWARE": "CLASS_QUALITY",
    }
    is_reversal = reversal_of is not None
    connection.execute(
        text(
            """
            INSERT INTO public.score_entries (
                score_entry_id,
                lesson_id,
                teacher_id,
                dimension,
                entry_type,
                delta_score,
                reason_code,
                evidence_status,
                score_rule_version,
                reversal_of_score_entry_id,
                idempotency_key,
                payload
            ) VALUES (
                :entry_id,
                'LESSON-9001',
                :teacher_id,
                :dimension,
                :entry_type,
                :delta_score,
                :reason_code,
                :evidence_status,
                :rule_version,
                :reversal_of,
                :idempotency_key,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "entry_id": entry_id,
            "teacher_id": teacher_id,
            "dimension": dimensions[component],
            "entry_type": (
                "LESSON_COMPONENT_REVERSAL"
                if is_reversal
                else "LESSON_COMPONENT_AWARD"
            ),
            "delta_score": -score if is_reversal else score,
            "reason_code": reason_code or component,
            "evidence_status": "SOURCE_MISSING" if is_reversal else "CONFIRMED",
            "rule_version": rule_version,
            "reversal_of": reversal_of,
            "idempotency_key": (
                f"lesson-reversal:{reversal_of}"
                if is_reversal
                else (
                    f"lesson:dom:9001:p{seq}:{component}:"
                    f"gen{generation}:{rule_version}"
                )
            ),
            "payload": _payload(
                seq=seq,
                component=component,
                generation=generation,
                evidence_hash=evidence_hash,
            ),
        },
    )


def _insert_settlement(
    connection,
    *,
    seq: int,
    component: str,
    teacher_id: str,
    entry_id: str,
    score: int,
    evidence_hash: str,
    rule_version: str = "score-v1",
    generation: int = 1,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.lesson_score_component_settlements (
                source_region,
                source_appoint_id,
                completion_participation_seq,
                component_code,
                teacher_id,
                status,
                award_generation,
                component_score,
                score_rule_version,
                evidence_fingerprint,
                current_award_score_entry_id,
                materialization_origin,
                materialized_by_run_id,
                award_projection_generation,
                awarded_at
            ) VALUES (
                'dom', '9001', :seq, :component, :teacher_id,
                'AWARDED', :generation, :score, :rule_version,
                :evidence_hash, :entry_id, 'V2_LIVE', NULL, 1,
                '2026-08-22T00:00:00+00'::timestamptz
            )
            """
        ),
        {
            "seq": seq,
            "component": component,
            "teacher_id": teacher_id,
            "generation": generation,
            "score": score,
            "rule_version": rule_version,
            "evidence_hash": evidence_hash,
            "entry_id": entry_id,
        },
    )


def _migrate(database_url: str, target: str = REVISION_76) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    _run_alembic(backend_dir, database_url, "upgrade", target)


def _seed_current_v2_award(
    engine,
    *,
    component: str = "PEAK_COMPLETED",
    score: int = 2,
    entry_id: str = "AWARD-A-1",
    evidence_hash: str = "a" * 64,
    lesson_id: str = "V2-9001",
) -> None:
    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id=entry_id,
            teacher_id="A",
            component=component,
            generation=1,
            seq=1,
            score=score,
            rule_version="score-v1",
            evidence_hash=evidence_hash,
        )
        _insert_settlement(
            connection,
            seq=1,
            component=component,
            teacher_id="A",
            entry_id=entry_id,
            score=score,
            evidence_hash=evidence_hash,
        )
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_score_results (
                    lesson_id, projection_revision, v2_source_region,
                    v2_source_appoint_id, v2_completion_participation_seq
                ) VALUES (:lesson_id, 1, 'dom', '9001', 1)
                """
            ),
            {"lesson_id": lesson_id},
        )


def _error_text(error: BaseException) -> str:
    return str(error).replace("\n", " ")


def test_upgrade_widens_revision_and_keeps_legacy_result_unowned(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url, legacy_result=True)
    _migrate(database_url)
    engine = create_engine(database_url)

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_76
        assert connection.execute(
            text(
                """
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'alembic_version'
                  AND column_name = 'version_num'
                """
            )
        ).scalar_one() == 64
        assert connection.execute(
            text(
                """
                SELECT v2_source_region,
                       v2_source_appoint_id,
                       v2_completion_participation_seq
                FROM public.lesson_score_results
                WHERE lesson_id = 'LEGACY-9001'
                """
            )
        ).one() == (None, None, None)
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_trigger
                WHERE tgname LIKE 'ct_%lesson_score%guard'
                   OR tgname = 'ct_lesson_component_course_guard'
                """
            )
        ).scalar_one() == 4

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.lesson_score_results
                    SET v2_source_region = 'dom',
                        v2_source_appoint_id = '9001',
                        v2_completion_participation_seq = 1
                    WHERE lesson_id = 'LEGACY-9001'
                    """
                )
            )
    assert "DTS_V2_LESSON_SCORE_RESULT_LEGACY_OWNERSHIP_IMMUTABLE" in _error_text(
        captured.value
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_score_results (
                    lesson_id,
                    projection_revision,
                    v2_source_region,
                    v2_source_appoint_id,
                    v2_completion_participation_seq
                ) VALUES ('V2-9001', 1, 'dom', '9001', 1)
                """
            )
        )

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            connection.execute(text("SET ROLE tit_growth_app"))
            connection.execute(
                text(
                    """
                    INSERT INTO public.lesson_score_results (
                        lesson_id,
                        projection_revision,
                        v2_source_region,
                        v2_source_appoint_id,
                        v2_completion_participation_seq
                    ) VALUES ('RUNTIME-V2-9001', 1, 'dom', '9001', 1)
                    """
                )
            )
    assert "DTS_V2_LESSON_SCORE_RESULT_RUNTIME_ROUTE_INACTIVE" in _error_text(
        captured.value
    )
    engine.dispose()


def test_wrong_teacher_double_award_and_wrong_entry_are_rejected(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url)
    engine = create_engine(database_url)
    praise_hash = "a" * 64

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            _insert_entry(
                connection,
                entry_id="AWARD-WRONG-TEACHER",
                teacher_id="B",
                component="FEEDBACK_PRAISE",
                generation=1,
                seq=1,
                score=5,
                rule_version="score-v1",
                evidence_hash=praise_hash,
            )
            _insert_settlement(
                connection,
                seq=1,
                component="FEEDBACK_PRAISE",
                teacher_id="B",
                entry_id="AWARD-WRONG-TEACHER",
                score=5,
                evidence_hash=praise_hash,
            )
    assert "DTS_V2_LESSON_COMPONENT_COMPLETION_MISMATCH" in _error_text(
        captured.value
    )

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            _insert_entry(
                connection,
                entry_id="AWARD-WRONG-SEMANTICS",
                teacher_id="A",
                component="FEEDBACK_PRAISE",
                generation=1,
                seq=1,
                score=5,
                rule_version="score-v1",
                evidence_hash=praise_hash,
                reason_code="PEAK_COMPLETED",
            )
            _insert_settlement(
                connection,
                seq=1,
                component="FEEDBACK_PRAISE",
                teacher_id="A",
                entry_id="AWARD-WRONG-SEMANTICS",
                score=5,
                evidence_hash=praise_hash,
            )
    assert "DTS_V2_LESSON_COMPONENT_AWARD_ENTRY_MISMATCH" in _error_text(
        captured.value
    )

    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="AWARD-A-1",
            teacher_id="A",
            component="FEEDBACK_PRAISE",
            generation=1,
            seq=1,
            score=5,
            rule_version="score-v1",
            evidence_hash=praise_hash,
        )
        _insert_settlement(
            connection,
            seq=1,
            component="FEEDBACK_PRAISE",
            teacher_id="A",
            entry_id="AWARD-A-1",
            score=5,
            evidence_hash=praise_hash,
        )

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.source_course_participations (
                        source_region, source_appoint_id, participation_seq,
                        teacher_id, participation_role
                    ) VALUES ('dom', '9001', 2, 'B', 'NORMAL')
                    """
                )
            )
            _insert_entry(
                connection,
                entry_id="AWARD-B-DUPLICATE",
                teacher_id="B",
                component="FEEDBACK_PRAISE",
                generation=1,
                seq=2,
                score=5,
                rule_version="score-v1",
                evidence_hash=praise_hash,
            )
            _insert_settlement(
                connection,
                seq=2,
                component="FEEDBACK_PRAISE",
                teacher_id="B",
                entry_id="AWARD-B-DUPLICATE",
                score=5,
                evidence_hash=praise_hash,
            )
    assert "uq_lesson_component_one_current_award" in _error_text(captured.value)
    engine.dispose()


def test_transfer_can_settle_before_course_pointer_changes_in_same_transaction(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url)
    engine = create_engine(database_url)
    first_hash = "a" * 64
    reverse_hash = "b" * 64
    second_hash = "c" * 64

    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="AWARD-A-1",
            teacher_id="A",
            component="PEAK_COMPLETED",
            generation=1,
            seq=1,
            score=2,
            rule_version="score-v1",
            evidence_hash=first_hash,
        )
        _insert_settlement(
            connection,
            seq=1,
            component="PEAK_COMPLETED",
            teacher_id="A",
            entry_id="AWARD-A-1",
            score=2,
            evidence_hash=first_hash,
        )
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_score_results (
                    lesson_id, projection_revision, v2_source_region,
                    v2_source_appoint_id, v2_completion_participation_seq
                ) VALUES ('V2-9001', 1, 'dom', '9001', 1)
                """
            )
        )

    with engine.begin() as connection:
        # Settlement changes deliberately precede both participation and course
        # pointer changes.  All cross-table guards inspect the final state.
        _insert_entry(
            connection,
            entry_id="REVERSE-A-1",
            teacher_id="A",
            component="PEAK_COMPLETED",
            generation=1,
            seq=1,
            score=2,
            rule_version="score-v2",
            evidence_hash=reverse_hash,
            reversal_of="AWARD-A-1",
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_component_settlements
                SET status = 'REVERSED',
                    current_award_score_entry_id = NULL,
                    last_reversal_score_entry_id = 'REVERSE-A-1',
                    score_rule_version = 'score-v2',
                    evidence_fingerprint = :reverse_hash,
                    reversed_at = '2026-08-22T01:00:00+00'::timestamptz,
                    award_projection_generation = 2,
                    row_version = 2,
                    updated_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND completion_participation_seq = 1
                  AND component_code = 'PEAK_COMPLETED'
                """
            ),
            {"reverse_hash": reverse_hash},
        )
        _insert_entry(
            connection,
            entry_id="AWARD-B-1",
            teacher_id="B",
            component="PEAK_COMPLETED",
            generation=1,
            seq=2,
            score=2,
            rule_version="score-v2",
            evidence_hash=second_hash,
        )
        _insert_settlement(
            connection,
            seq=2,
            component="PEAK_COMPLETED",
            teacher_id="B",
            entry_id="AWARD-B-1",
            score=2,
            evidence_hash=second_hash,
            rule_version="score-v2",
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_results
                SET v2_completion_participation_seq = 2,
                    projection_revision = 2
                WHERE lesson_id = 'V2-9001';

                UPDATE public.source_course_participations
                SET participation_role = 'SUPERSEDED_COMPLETION'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND participation_seq = 1;

                INSERT INTO public.source_course_participations (
                    source_region, source_appoint_id, participation_seq,
                    teacher_id, participation_role
                ) VALUES ('dom', '9001', 2, 'B', 'COMPLETION');

                UPDATE public.source_courses
                SET completion_participation_seq = 2,
                    completion_teacher_id = 'B'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001';
                """
            )
        )

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT completion_participation_seq, completion_teacher_id
                FROM public.source_courses
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        ).one() == (2, "B")
        assert connection.execute(
            text(
                """
                SELECT completion_participation_seq, teacher_id, status
                FROM public.lesson_score_component_settlements
                ORDER BY completion_participation_seq
                """
            )
        ).all() == [(1, "A", "REVERSED"), (2, "B", "AWARDED")]
        assert connection.execute(
            text(
                """
                SELECT v2_completion_participation_seq, projection_revision
                FROM public.lesson_score_results
                WHERE lesson_id = 'V2-9001'
                """
            )
        ).one() == (2, 2)
    engine.dispose()


def test_downgrade_refuses_materialized_settlement(database_url: str) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url)
    engine = create_engine(database_url)
    evidence_hash = "d" * 64
    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="AWARD-DOWNGRADE",
            teacher_id="A",
            component="PERFECT_COMPLETED",
            generation=1,
            seq=1,
            score=4,
            rule_version="score-v1",
            evidence_hash=evidence_hash,
        )
        _insert_settlement(
            connection,
            seq=1,
            component="PERFECT_COMPLETED",
            teacher_id="A",
            entry_id="AWARD-DOWNGRADE",
            score=4,
            evidence_hash=evidence_hash,
        )
    engine.dispose()

    backend_dir = Path(__file__).resolve().parents[1]
    _run_alembic_expect_failure(
        backend_dir,
        database_url,
        "DTS_V2_LESSON_COMPONENT_DOWNGRADE_DATA_PRESENT",
        "downgrade",
        REVISION_75,
    )
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_76
    engine.dispose()


def test_clean_downgrade_removes_only_v2_surface(database_url: str) -> None:
    _seed_revision_75_shape(database_url, legacy_result=True)
    _migrate(database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    _run_alembic(backend_dir, database_url, "downgrade", REVISION_75)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_75
        assert connection.execute(
            text(
                """
                SELECT to_regclass(
                    'public.lesson_score_component_settlements'
                ) IS NULL
                """
            )
        ).scalar_one() is True
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'lesson_score_results'
                  AND column_name LIKE 'v2_%'
                """
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                """
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'alembic_version'
                  AND column_name = 'version_num'
                """
            )
        ).scalar_one() == 64
        assert connection.execute(
            text(
                """
                SELECT projection_revision
                FROM public.lesson_score_results
                WHERE lesson_id = 'LEGACY-9001'
                """
            )
        ).scalar_one() == 7
    engine.dispose()


def test_rev78_pending_keep_retains_frozen_result_and_award(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    engine = create_engine(database_url)
    _seed_current_v2_award(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'PENDING'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_78
        assert connection.execute(
            text(
                """
                SELECT completion_participation_seq, teacher_id, status,
                       current_award_score_entry_id
                FROM public.lesson_score_component_settlements
                """
            )
        ).one() == (1, "A", "AWARDED", "AWARD-A-1")
        assert connection.execute(
            text(
                """
                SELECT v2_completion_participation_seq, projection_revision
                FROM public.lesson_score_results
                WHERE lesson_id = 'V2-9001'
                """
            )
        ).one() == (1, 1)

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_teacher_id = 'B'
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9001'
                    """
                )
            )
    assert "DTS_V2_LESSON_COMPONENT_COMPLETION_MISMATCH" in _error_text(
        captured.value
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'RESOLVED_KEEP'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT completion_conflict_status,
                       completion_participation_seq,
                       completion_teacher_id
                FROM public.source_courses
                """
            )
        ).one() == ("RESOLVED_KEEP", 1, "A")
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM public.lesson_score_component_settlements
                WHERE status = 'AWARDED'
                """
            )
        ).scalar_one() == 1
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM public.lesson_score_results
                WHERE v2_source_region = 'dom'
                  AND v2_source_appoint_id = '9001'
                """
            )
        ).scalar_one() == 1
    engine.dispose()


def test_rev78_pending_rejects_new_replace_reaward_and_result_writes(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    engine = create_engine(database_url)
    _seed_current_v2_award(engine)
    perfect_award_hash = "1" * 64
    perfect_reverse_hash = "2" * 64

    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="AWARD-PERFECT-1",
            teacher_id="A",
            component="PERFECT_COMPLETED",
            generation=1,
            seq=1,
            score=4,
            rule_version="score-v1",
            evidence_hash=perfect_award_hash,
        )
        _insert_settlement(
            connection,
            seq=1,
            component="PERFECT_COMPLETED",
            teacher_id="A",
            entry_id="AWARD-PERFECT-1",
            score=4,
            evidence_hash=perfect_award_hash,
        )
        _insert_entry(
            connection,
            entry_id="REVERSE-PERFECT-1",
            teacher_id="A",
            component="PERFECT_COMPLETED",
            generation=1,
            seq=1,
            score=4,
            rule_version="score-v1",
            evidence_hash=perfect_reverse_hash,
            reversal_of="AWARD-PERFECT-1",
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_component_settlements
                SET status = 'REVERSED',
                    current_award_score_entry_id = NULL,
                    last_reversal_score_entry_id = 'REVERSE-PERFECT-1',
                    evidence_fingerprint = :evidence_hash,
                    reversed_at = '2026-08-22T01:00:00+00'::timestamptz,
                    award_projection_generation = 2,
                    row_version = 2,
                    updated_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND completion_participation_seq = 1
                  AND component_code = 'PERFECT_COMPLETED'
                """
            ),
            {"evidence_hash": perfect_reverse_hash},
        )
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'PENDING'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            _insert_entry(
                connection,
                entry_id="AWARD-NEW-PENDING",
                teacher_id="A",
                component="FEEDBACK_PRAISE",
                generation=1,
                seq=1,
                score=5,
                rule_version="score-v1",
                evidence_hash="3" * 64,
            )
            _insert_settlement(
                connection,
                seq=1,
                component="FEEDBACK_PRAISE",
                teacher_id="A",
                entry_id="AWARD-NEW-PENDING",
                score=5,
                evidence_hash="3" * 64,
            )
    assert "DTS_V2_LESSON_COMPONENT_PENDING_AWARD_WRITE_FORBIDDEN" in (
        _error_text(captured.value)
    )

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            _insert_entry(
                connection,
                entry_id="REVERSE-PEAK-1",
                teacher_id="A",
                component="PEAK_COMPLETED",
                generation=1,
                seq=1,
                score=2,
                rule_version="score-v2",
                evidence_hash="4" * 64,
                reversal_of="AWARD-A-1",
            )
            _insert_entry(
                connection,
                entry_id="AWARD-PEAK-2",
                teacher_id="A",
                component="PEAK_COMPLETED",
                generation=2,
                seq=1,
                score=2,
                rule_version="score-v2",
                evidence_hash="5" * 64,
            )
            connection.execute(
                text(
                    """
                    UPDATE public.lesson_score_component_settlements
                    SET award_generation = 2,
                        score_rule_version = 'score-v2',
                        evidence_fingerprint = :evidence_hash,
                        current_award_score_entry_id = 'AWARD-PEAK-2',
                        last_reversal_score_entry_id = 'REVERSE-PEAK-1',
                        award_projection_generation = 2,
                        row_version = 2,
                        awarded_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9001'
                      AND completion_participation_seq = 1
                      AND component_code = 'PEAK_COMPLETED'
                    """
                ),
                {"evidence_hash": "5" * 64},
            )
    assert "DTS_V2_LESSON_COMPONENT_PENDING_AWARD_WRITE_FORBIDDEN" in (
        _error_text(captured.value)
    )

    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            _insert_entry(
                connection,
                entry_id="AWARD-PERFECT-2",
                teacher_id="A",
                component="PERFECT_COMPLETED",
                generation=2,
                seq=1,
                score=4,
                rule_version="score-v2",
                evidence_hash="6" * 64,
            )
            connection.execute(
                text(
                    """
                    UPDATE public.lesson_score_component_settlements
                    SET status = 'AWARDED',
                        award_generation = 2,
                        score_rule_version = 'score-v2',
                        evidence_fingerprint = :evidence_hash,
                        current_award_score_entry_id = 'AWARD-PERFECT-2',
                        award_projection_generation = 3,
                        row_version = 3,
                        awarded_at = clock_timestamp(),
                        reversed_at = NULL,
                        updated_at = clock_timestamp()
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9001'
                      AND completion_participation_seq = 1
                      AND component_code = 'PERFECT_COMPLETED'
                    """
                ),
                {"evidence_hash": "6" * 64},
            )
    assert "DTS_V2_LESSON_COMPONENT_PENDING_AWARD_WRITE_FORBIDDEN" in (
        _error_text(captured.value)
    )

    for result_write in (
        """
        UPDATE public.lesson_score_results
        SET projection_revision = projection_revision + 1
        WHERE lesson_id = 'V2-9001'
        """,
        """
        INSERT INTO public.lesson_score_results (
            lesson_id, projection_revision, v2_source_region,
            v2_source_appoint_id, v2_completion_participation_seq
        ) VALUES ('V2-PENDING-NEW', 1, 'dom', '9001', 1)
        """,
        """
        DELETE FROM public.lesson_score_results
        WHERE lesson_id = 'V2-9001'
        """,
    ):
        with pytest.raises(DBAPIError) as captured:
            with engine.begin() as connection:
                connection.execute(text(result_write))
        assert "DTS_V2_LESSON_SCORE_RESULT_PENDING_WRITE_FORBIDDEN" in (
            _error_text(captured.value)
        )

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT component_code, status, award_generation,
                       current_award_score_entry_id
                FROM public.lesson_score_component_settlements
                ORDER BY component_code
                """
            )
        ).all() == [
            ("PEAK_COMPLETED", "AWARDED", 1, "AWARD-A-1"),
            ("PERFECT_COMPLETED", "REVERSED", 1, None),
        ]
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM public.score_entries
                WHERE score_entry_id IN (
                    'AWARD-NEW-PENDING', 'REVERSE-PEAK-1',
                    'AWARD-PEAK-2', 'AWARD-PERFECT-2'
                )
                """
            )
        ).scalar_one() == 0
    engine.dispose()


def test_rev78_pending_transfer_reverses_old_and_awards_new_atomically(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    engine = create_engine(database_url)
    _seed_current_v2_award(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'PENDING'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="REVERSE-A-TRANSFER",
            teacher_id="A",
            component="PEAK_COMPLETED",
            generation=1,
            seq=1,
            score=2,
            rule_version="score-v2",
            evidence_hash="b" * 64,
            reversal_of="AWARD-A-1",
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_component_settlements
                SET status = 'REVERSED',
                    current_award_score_entry_id = NULL,
                    last_reversal_score_entry_id = 'REVERSE-A-TRANSFER',
                    score_rule_version = 'score-v2',
                    evidence_fingerprint = :evidence_hash,
                    reversed_at = clock_timestamp(),
                    award_projection_generation = 2,
                    row_version = 2,
                    updated_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND completion_participation_seq = 1
                  AND component_code = 'PEAK_COMPLETED'
                """
            ),
            {"evidence_hash": "b" * 64},
        )
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'RESOLVED_TRANSFER'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE public.source_course_participations
                SET participation_role = 'SUPERSEDED_COMPLETION'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND participation_seq = 1
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO public.source_course_participations (
                    source_region, source_appoint_id, participation_seq,
                    teacher_id, participation_role
                ) VALUES ('dom', '9001', 2, 'B', 'COMPLETION')
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_participation_seq = 2,
                    completion_teacher_id = 'B'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_results
                SET v2_completion_participation_seq = 2,
                    projection_revision = 2
                WHERE lesson_id = 'V2-9001'
                """
            )
        )
        _insert_entry(
            connection,
            entry_id="AWARD-B-TRANSFER",
            teacher_id="B",
            component="PEAK_COMPLETED",
            generation=1,
            seq=2,
            score=2,
            rule_version="score-v2",
            evidence_hash="c" * 64,
        )
        _insert_settlement(
            connection,
            seq=2,
            component="PEAK_COMPLETED",
            teacher_id="B",
            entry_id="AWARD-B-TRANSFER",
            score=2,
            evidence_hash="c" * 64,
            rule_version="score-v2",
        )

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT completion_conflict_status,
                       completion_participation_seq,
                       completion_teacher_id
                FROM public.source_courses
                """
            )
        ).one() == ("RESOLVED_TRANSFER", 2, "B")
        assert connection.execute(
            text(
                """
                SELECT completion_participation_seq, teacher_id, status
                FROM public.lesson_score_component_settlements
                ORDER BY completion_participation_seq
                """
            )
        ).all() == [(1, "A", "REVERSED"), (2, "B", "AWARDED")]
        assert connection.execute(
            text(
                """
                SELECT v2_completion_participation_seq, projection_revision
                FROM public.lesson_score_results
                WHERE lesson_id = 'V2-9001'
                """
            )
        ).one() == (2, 2)
    engine.dispose()


def test_rev78_pending_void_reverses_and_clears_result_atomically(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    engine = create_engine(database_url)
    _seed_current_v2_award(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'PENDING'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with engine.begin() as connection:
        _insert_entry(
            connection,
            entry_id="REVERSE-A-VOID",
            teacher_id="A",
            component="PEAK_COMPLETED",
            generation=1,
            seq=1,
            score=2,
            rule_version="score-v2",
            evidence_hash="d" * 64,
            reversal_of="AWARD-A-1",
        )
        connection.execute(
            text(
                """
                UPDATE public.lesson_score_component_settlements
                SET status = 'REVERSED',
                    current_award_score_entry_id = NULL,
                    last_reversal_score_entry_id = 'REVERSE-A-VOID',
                    score_rule_version = 'score-v2',
                    evidence_fingerprint = :evidence_hash,
                    reversed_at = clock_timestamp(),
                    award_projection_generation = 2,
                    row_version = 2,
                    updated_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND completion_participation_seq = 1
                  AND component_code = 'PEAK_COMPLETED'
                """
            ),
            {"evidence_hash": "d" * 64},
        )
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'RESOLVED_VOID'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )
        connection.execute(
            text(
                """
                DELETE FROM public.lesson_score_results
                WHERE lesson_id = 'V2-9001'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE public.source_course_participations
                SET participation_role = 'VOIDED_COMPLETION'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                  AND participation_seq = 1
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_participation_seq = NULL,
                    completion_teacher_id = NULL,
                    completion_voided_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )

    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT completion_conflict_status,
                       completion_participation_seq,
                       completion_teacher_id,
                       completion_voided_at IS NOT NULL
                FROM public.source_courses
                """
            )
        ).one() == ("RESOLVED_VOID", None, None, True)
        assert connection.execute(
            text(
                """
                SELECT status, current_award_score_entry_id,
                       last_reversal_score_entry_id
                FROM public.lesson_score_component_settlements
                """
            )
        ).one() == ("REVERSED", None, "REVERSE-A-VOID")
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.lesson_score_results
                WHERE v2_source_region = 'dom'
                  AND v2_source_appoint_id = '9001'
                """
            )
        ).scalar_one() == 0
    engine.dispose()


def test_rev78_downgrade_refuses_pending_materialized_scores(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    engine = create_engine(database_url)
    _seed_current_v2_award(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status = 'PENDING'
                WHERE source_region = 'dom'
                  AND source_appoint_id = '9001'
                """
            )
        )
    engine.dispose()

    backend_dir = Path(__file__).resolve().parents[1]
    _run_alembic_expect_failure(
        backend_dir,
        database_url,
        "DTS_V2_PENDING_SCORE_DOWNGRADE_DATA_PRESENT",
        "downgrade",
        REVISION_77,
    )

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_78
    engine.dispose()


def test_rev78_clean_downgrade_restores_rev76_pending_rejection(
    database_url: str,
) -> None:
    _seed_revision_75_shape(database_url)
    _migrate(database_url, REVISION_78)
    backend_dir = Path(__file__).resolve().parents[1]
    _run_alembic(backend_dir, database_url, "downgrade", REVISION_77)

    engine = create_engine(database_url)
    _seed_current_v2_award(engine)
    with pytest.raises(DBAPIError) as captured:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_conflict_status = 'PENDING'
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9001'
                    """
                )
            )
    assert "DTS_V2_LESSON_COMPONENT_COMPLETION_MISMATCH" in _error_text(
        captured.value
    )

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == REVISION_77
        assert connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_trigger
                WHERE tgname IN (
                    'trg_lesson_component_settlement_pending_freeze',
                    'trg_lesson_score_result_v2_pending_freeze'
                )
                  AND NOT tgisinternal
                """
            )
        ).scalar_one() == 0
    engine.dispose()
