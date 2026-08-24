from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL
from sqlalchemy.exc import DBAPIError


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
DOM_STUDENT = "dom:v1:" + "a" * 64


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
    assert result.returncode != 0
    assert expected_error in output


def _seed_revision_73_shape(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE ROLE tit_growth_app NOLOGIN;
            CREATE ROLE tit_dts_ingest_runtime NOLOGIN;

            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version
            VALUES ('20260822_73_retire_false_early');

            CREATE TABLE public.dts_source_row_versions (
                source_region varchar(8) NOT NULL,
                source_partition_epoch_id varchar(160) NOT NULL,
                topic varchar(512) NOT NULL,
                partition_id integer NOT NULL,
                offset_value bigint NOT NULL,
                source_table varchar(128) NOT NULL,
                operation varchar(48) NOT NULL,
                source_row_revision bigint,
                PRIMARY KEY (
                    source_region, source_partition_epoch_id, topic,
                    partition_id, offset_value
                )
            );

            CREATE TABLE public.source_courses (
                source_region varchar(8) NOT NULL,
                source_appoint_id varchar(512) NOT NULL,
                completion_participation_seq integer,
                completion_teacher_id varchar(64),
                completion_teacher_id_type varchar(16),
                completion_student_token varchar(128),
                completion_end_time timestamptz,
                PRIMARY KEY (source_region, source_appoint_id)
            );

            CREATE TABLE public.source_course_participations (
                source_region varchar(8) NOT NULL,
                source_appoint_id varchar(512) NOT NULL,
                participation_seq integer NOT NULL,
                teacher_id varchar(64) NOT NULL,
                teacher_id_type varchar(16) NOT NULL,
                participation_role varchar(40) NOT NULL,
                PRIMARY KEY (
                    source_region, source_appoint_id, participation_seq
                ),
                CONSTRAINT uq_source_course_participation_teacher_identity
                    UNIQUE (
                        source_region, source_appoint_id, participation_seq,
                        teacher_id, teacher_id_type
                    ),
                FOREIGN KEY (source_region, source_appoint_id)
                    REFERENCES public.source_courses (
                        source_region, source_appoint_id
                    ) DEFERRABLE INITIALLY DEFERRED
            );

            CREATE TABLE public.score_entries (
                score_entry_id varchar(128) PRIMARY KEY
            );
            """
        )
    )


def _insert_relationship_version(
    connection: Connection,
    *,
    offset: int,
    revision: int,
    operation: str,
    region: str = "dom",
    source_table: str | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_row_versions (
                source_region, source_partition_epoch_id, topic,
                partition_id, offset_value, source_table, operation,
                source_row_revision
            ) VALUES (
                :region, 'epoch-1', 'relationship-topic', 0, :offset,
                :source_table, :operation, :revision
            )
            """
        ),
        {
            "region": region,
            "offset": offset,
            "source_table": source_table
            or f"{region}_teacher_favorite",
            "operation": operation,
            "revision": revision,
        },
    )


def _insert_relationship_event(
    connection: Connection,
    *,
    offset: int,
    revision: int,
    operation: str,
    old_pair: bool,
    new_pair: bool,
    student_token: str = DOM_STUDENT,
) -> int:
    return int(
        connection.execute(
            text(
                """
                INSERT INTO public.teacher_student_relationship_events (
                    source_region, source_partition_epoch_id, topic,
                    partition_id, offset_value, source_table,
                    source_record_id, source_record_id_type,
                    source_record_id_numeric, source_record_id_text,
                    source_row_revision, relationship_type, operation,
                    old_teacher_id, old_teacher_id_type, old_student_token,
                    new_teacher_id, new_teacher_id_type, new_student_token,
                    effective_at, effective_time_evidence_status,
                    source_timestamp
                ) VALUES (
                    'dom', 'epoch-1', 'relationship-topic', 0, :offset,
                    'dom_teacher_favorite', CAST(:offset AS text), 'NUMERIC',
                    CAST(:offset AS numeric), NULL, :revision,
                    'FAVORITE', :operation,
                    CASE WHEN :old_pair THEN 'T1' END,
                    CASE WHEN :old_pair THEN 'TEXT' END,
                    CASE WHEN :old_pair THEN :student_token END,
                    CASE WHEN :new_pair THEN 'T1' END,
                    CASE WHEN :new_pair THEN 'TEXT' END,
                    CASE WHEN :new_pair THEN :student_token END,
                    '2026-08-20T10:00:00+00'::timestamptz,
                    'CONFIRMED',
                    '2026-08-22T00:00:00+00'::timestamptz
                )
                RETURNING event_sequence
                """
            ),
            {
                "offset": offset,
                "revision": revision,
                "operation": operation,
                "old_pair": old_pair,
                "new_pair": new_pair,
                "student_token": student_token,
            },
        ).scalar_one()
    )


def _insert_relationship_current(
    connection: Connection,
    *,
    event_sequence: int,
    offset: int,
    revision: int,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.teacher_student_relationship_current (
                source_region, teacher_id, teacher_id_type, student_token,
                is_favorited, is_blocked, last_business_effective_at,
                effective_time_evidence_status, last_event_sequence,
                last_source_partition_epoch_id, last_topic,
                last_partition_id, last_offset_value,
                last_source_row_revision
            ) VALUES (
                'dom', 'T1', 'TEXT', :student_token, true, false,
                '2026-08-20T10:00:00+00'::timestamptz, 'CONFIRMED',
                :event_sequence, 'epoch-1', 'relationship-topic', 0,
                :offset, :revision
            )
            """
        ),
        {
            "student_token": DOM_STUDENT,
            "event_sequence": event_sequence,
            "offset": offset,
            "revision": revision,
        },
    )


def _seed_course(
    connection: Connection,
    course_id: str,
    *,
    region: str = "dom",
    teacher_id: str = "T1",
    student_token: str = DOM_STUDENT,
    completion_end_time: str = "2026-08-20T10:00:00+00",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.source_courses (
                source_region, source_appoint_id,
                completion_participation_seq, completion_teacher_id,
                completion_teacher_id_type, completion_student_token,
                completion_end_time
            ) VALUES (
                :region, :course_id, 1, :teacher_id, 'TEXT',
                :student_token, CAST(:completion_end_time AS timestamptz)
            )
            """
        ),
        {
            "region": region,
            "course_id": course_id,
            "teacher_id": teacher_id,
            "student_token": student_token,
            "completion_end_time": completion_end_time,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_participations (
                source_region, source_appoint_id, participation_seq,
                teacher_id, teacher_id_type, participation_role
            ) VALUES (
                :region, :course_id, 1, :teacher_id, 'TEXT', 'COMPLETION'
            )
            """
        ),
        {
            "region": region,
            "course_id": course_id,
            "teacher_id": teacher_id,
        },
    )


def _insert_observation(
    connection: Connection,
    course_id: str,
    *,
    status: str,
    observed_at: str = "2026-08-21T10:00:00+00",
    region: str = "dom",
    teacher_id: str = "T1",
    student_token: str = DOM_STUDENT,
) -> None:
    if status == "CONFIRMED_TRUE":
        relation_state = True
        evidence_status = "CONFIRMED"
        error_code = None
        completed_revision = 1
        next_attempt_at = None
    elif status == "CONFIRMED_FALSE":
        relation_state = False
        evidence_status = "CONFIRMED"
        error_code = None
        completed_revision = 1
        next_attempt_at = None
    elif status == "WAITING_HISTORY":
        relation_state = None
        evidence_status = "HISTORY_INCOMPLETE"
        error_code = "PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE"
        completed_revision = 1
        next_attempt_at = None
    else:
        assert status == "PENDING"
        relation_state = None
        evidence_status = "PENDING"
        error_code = None
        completed_revision = 0
        next_attempt_at = observed_at

    connection.execute(
        text(
            """
            INSERT INTO public.course_favorite_observations (
                source_region, source_appoint_id, observation_revision,
                appoint_id_type, appoint_id_numeric, appoint_id_text_sort,
                teacher_id, teacher_id_type, student_token,
                completion_participation_seq, observed_at, relation_state,
                relation_evidence_status, relation_error_code, status,
                required_evidence_revision, completed_evidence_revision,
                required_evidence_fingerprint, attempt_count,
                next_attempt_at, dead_generation, rule_version,
                materialization_origin, materialized_by_run_id,
                created_projection_generation,
                serving_projection_generation, is_serving
            ) VALUES (
                :region, CAST(:course_id AS text), 1, 'NUMERIC',
                CAST(:course_id AS numeric), NULL,
                :teacher_id, 'TEXT', :student_token, 1,
                CAST(:observed_at AS timestamptz), :relation_state,
                :evidence_status, :error_code, :status, 1,
                :completed_revision, repeat('b', 64), 0,
                CAST(:next_attempt_at AS timestamptz), 0,
                'favorite-rule-v1', 'V2_LIVE', NULL, 1, 1, true
            )
            """
        ),
        {
            "region": region,
            "course_id": course_id,
            "teacher_id": teacher_id,
            "student_token": student_token,
            "observed_at": observed_at,
            "relation_state": relation_state,
            "evidence_status": evidence_status,
            "error_code": error_code,
            "status": status,
            "completed_revision": completed_revision,
            "next_attempt_at": next_attempt_at,
        },
    )


def _insert_attribution(
    connection: Connection,
    course_id: str,
    *,
    score_entry_id: str,
    region: str = "dom",
    teacher_id: str = "T1",
    student_token: str = DOM_STUDENT,
) -> None:
    connection.execute(
        text(
            "INSERT INTO public.score_entries VALUES (:score_entry_id)"
        ),
        {"score_entry_id": score_entry_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO public.course_favorite_attributions (
                source_region, teacher_id, teacher_id_type, student_token,
                source_appoint_id, observation_revision,
                completion_participation_seq, status, hold_reason, points,
                rule_version, award_generation, current_score_entry_id,
                last_reversal_score_entry_id, recompute_reason,
                materialization_origin, materialized_by_run_id,
                award_projection_generation, awarded_at
            ) VALUES (
                :region, :teacher_id, 'TEXT', :student_token,
                :course_id, 1, 1, 'AWARDED', NULL, 5,
                'favorite-rule-v1', 1, :score_entry_id, NULL,
                'INITIAL_CONFIRMED_TRUE', 'V2_LIVE', NULL, 1,
                '2026-08-21T10:00:00+00'::timestamptz
            )
            """
        ),
        {
            "region": region,
            "teacher_id": teacher_id,
            "student_token": student_token,
            "course_id": course_id,
            "score_entry_id": score_entry_id,
        },
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for deferred favorite guards",
)
def test_revision_74_relationship_and_favorite_constraints_on_postgresql(
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
            f"-p {postgres_port} -c listen_addresses=127.0.0.1 -c fsync=off",
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
            _seed_revision_73_shape(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_74_favorite_schema",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_74_favorite_schema"
            privileges = connection.execute(
                text(
                    """
                    SELECT has_table_privilege(
                        role_name, 'public.' || table_name, privilege_name
                    )
                    FROM unnest(ARRAY[
                        'tit_growth_app', 'tit_dts_ingest_runtime'
                    ]) AS roles(role_name)
                    CROSS JOIN unnest(ARRAY[
                        'teacher_student_relationship_events',
                        'teacher_student_relationship_current',
                        'course_favorite_observations',
                        'course_favorite_attributions'
                    ]) AS tables(table_name)
                    CROSS JOIN unnest(ARRAY[
                        'SELECT', 'INSERT', 'UPDATE', 'DELETE'
                    ]) AS privileges(privilege_name)
                    """
                )
            ).scalars().all()
            assert len(privileges) == 32
            assert not any(privileges)

        # An empty schema can be rolled back and recreated deterministically.
        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_73_retire_false_early",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_73_retire_false_early"
            assert connection.execute(
                text(
                    "SELECT to_regclass("
                    "'public.course_favorite_observations')"
                )
            ).scalar_one() is None
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_74_favorite_schema",
        )

        # Relationship current is independent of courses and must point to the
        # exact last ingested relationship event for the regional pair.
        with engine.begin() as connection:
            _insert_relationship_version(
                connection, offset=1, revision=1, operation="INSERT"
            )
            sequence = _insert_relationship_event(
                connection,
                offset=1,
                revision=1,
                operation="INSERT",
                old_pair=False,
                new_pair=True,
            )
            _insert_relationship_current(
                connection,
                event_sequence=sequence,
                offset=1,
                revision=1,
            )
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT is_favorited FROM "
                    "public.teacher_student_relationship_current"
                )
            ).scalar_one() is True
            assert connection.execute(
                text("SELECT count(*) FROM public.source_courses")
            ).scalar_one() == 0

        with pytest.raises(DBAPIError, match="RELATIONSHIP_EVENT_IMMUTABLE"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE public.teacher_student_relationship_events "
                        "SET effective_at = effective_at + interval '1 second'"
                    )
                )

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                _insert_relationship_version(
                    connection, offset=2, revision=2, operation="UPDATE"
                )
                _insert_relationship_event(
                    connection,
                    offset=2,
                    revision=2,
                    operation="UPDATE",
                    old_pair=True,
                    new_pair=True,
                    student_token="raw-dom-student-id",
                )

        with engine.begin() as connection:
            _insert_relationship_version(
                connection, offset=2, revision=2, operation="UPDATE"
            )
            sequence = _insert_relationship_event(
                connection,
                offset=2,
                revision=2,
                operation="UPDATE",
                old_pair=True,
                new_pair=True,
            )
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_student_relationship_current
                    SET last_event_sequence = :sequence,
                        last_offset_value = 2,
                        last_source_row_revision = 2,
                        row_version = 2
                    WHERE source_region = 'dom'
                      AND teacher_id = 'T1'
                      AND student_token = :student_token
                    """
                ),
                {"sequence": sequence, "student_token": DOM_STUDENT},
            )

        with pytest.raises(
            DBAPIError,
            match="RELATIONSHIP_CURRENT_NOT_LATEST_EVENT",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_student_relationship_current
                        SET last_business_effective_at =
                                last_business_effective_at
                                + interval '1 second',
                            row_version = 3
                        WHERE source_region = 'dom'
                          AND teacher_id = 'T1'
                          AND student_token = :student_token
                        """
                    ),
                    {"student_token": DOM_STUDENT},
                )

        with pytest.raises(
            DBAPIError,
            match="RELATIONSHIP_CURRENT_NOT_LATEST_EVENT",
        ):
            with engine.begin() as connection:
                _insert_relationship_version(
                    connection, offset=3, revision=3, operation="UPDATE"
                )
                _insert_relationship_event(
                    connection,
                    offset=3,
                    revision=3,
                    operation="UPDATE",
                    old_pair=True,
                    new_pair=True,
                )

        # Bootstrap snapshot operations are first-class provenance, not values
        # that overflow a short CRUD-only operation column.
        with engine.begin() as connection:
            _insert_relationship_version(
                connection,
                offset=4,
                revision=3,
                operation="SNAPSHOT_BOOTSTRAP_PRESENT",
            )
            sequence = _insert_relationship_event(
                connection,
                offset=4,
                revision=3,
                operation="SNAPSHOT_BOOTSTRAP_PRESENT",
                old_pair=False,
                new_pair=True,
            )
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_student_relationship_current
                    SET last_event_sequence = :sequence,
                        last_offset_value = 4,
                        last_source_row_revision = 3,
                        row_version = 3
                    WHERE source_region = 'dom'
                      AND teacher_id = 'T1'
                      AND student_token = :student_token
                    """
                ),
                {"sequence": sequence, "student_token": DOM_STUDENT},
            )

        # Wrong observation time is rejected at deferred commit.
        with pytest.raises(
            DBAPIError,
            match="FAVORITE_OBSERVATION_COMPLETION_MISMATCH",
        ):
            with engine.begin() as connection:
                _seed_course(connection, "30")
                _insert_observation(
                    connection,
                    "30",
                    status="PENDING",
                    observed_at="2026-08-21T09:59:59+00",
                )

        # A matching teacher key is insufficient: the frozen pointer must
        # reference a participation whose role is exactly COMPLETION.
        with pytest.raises(
            DBAPIError,
            match="FAVORITE_OBSERVATION_COMPLETION_MISMATCH",
        ):
            with engine.begin() as connection:
                _seed_course(connection, "31")
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET participation_role = 'SUBSTITUTE'
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '31'
                          AND participation_seq = 1
                        """
                    )
                )
                _insert_observation(connection, "31", status="PENDING")

        # False and insufficient history are distinct persisted conclusions.
        with engine.begin() as connection:
            _seed_course(connection, "20")
            _seed_course(connection, "21")
            _insert_observation(connection, "20", status="CONFIRMED_FALSE")
            _insert_observation(connection, "21", status="WAITING_HISTORY")
        with engine.begin() as connection:
            evidence = connection.execute(
                text(
                    """
                    SELECT source_appoint_id, status, relation_state,
                           relation_evidence_status
                    FROM public.course_favorite_observations
                    WHERE source_appoint_id IN ('20', '21')
                    ORDER BY source_appoint_id
                    """
                )
            ).all()
            assert evidence == [
                ("20", "CONFIRMED_FALSE", False, "CONFIRMED"),
                ("21", "WAITING_HISTORY", None, "HISTORY_INCOMPLETE"),
            ]

        # A claim token is globally unique within the observation queue; the
        # same lease cannot accidentally own two rows.
        with engine.begin() as connection:
            _seed_course(connection, "40")
            _seed_course(connection, "41")
            _insert_observation(connection, "40", status="PENDING")
            _insert_observation(connection, "41", status="PENDING")
        with pytest.raises(
            DBAPIError,
            match="uq_favorite_observation_lease_token",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.course_favorite_observations
                        SET status = 'EVALUATING', attempt_count = 1,
                            next_attempt_at = NULL,
                            claimed_evidence_revision = 1,
                            lease_owner = 'worker-1',
                            lease_token = 'duplicate-lease-token',
                            lease_acquired_at =
                                '2026-08-21T10:00:00+00'::timestamptz,
                            lease_expires_at =
                                '2026-08-21T10:05:00+00'::timestamptz,
                            row_version = 2
                        WHERE source_region = 'dom'
                          AND source_appoint_id IN ('40', '41')
                        """
                    )
                )

        # Same-time numeric IDs choose 9 before 10 regardless of insert order.
        with engine.begin() as connection:
            _seed_course(connection, "10")
            _seed_course(connection, "9")
            _insert_observation(connection, "10", status="CONFIRMED_TRUE")
            _insert_observation(connection, "9", status="CONFIRMED_TRUE")

        with pytest.raises(
            DBAPIError,
            match="FAVORITE_ATTRIBUTION_NOT_CANONICAL_FIRST",
        ):
            with engine.begin() as connection:
                _insert_attribution(
                    connection,
                    "10",
                    score_entry_id="favorite-award-wrong-course",
                )

        with engine.begin() as connection:
            _insert_attribution(
                connection,
                "9",
                score_entry_id="favorite-award-first-course",
            )

        # HISTORY STALE holds the exact award and restoration does not open a
        # new generation or score entry.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_observations
                    SET status = 'WAITING_HISTORY', relation_state = NULL,
                        relation_evidence_status = 'HISTORY_INCOMPLETE',
                        relation_error_code =
                            'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE',
                        row_version = 2
                    WHERE source_region = 'dom' AND source_appoint_id = '9'
                      AND observation_revision = 1
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_attributions
                    SET status = 'AWARDED_PENDING_EVIDENCE',
                        hold_reason = 'WAITING_HISTORY', row_version = 2,
                        recompute_reason = 'HISTORY_STALE'
                    WHERE source_region = 'dom' AND teacher_id = 'T1'
                      AND student_token = :student_token;
                    """
                ),
                {"student_token": DOM_STUDENT},
            )

        # A score-rule publication re-awards the held generation in place:
        # it stays held on the same observation but uses a fresh award and a
        # unique reversal of the prior generation.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.score_entries (score_entry_id)
                    VALUES
                        ('favorite-reversal-rule-v1'),
                        ('favorite-award-rule-v2')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_attributions
                    SET points = 6,
                        rule_version = 'favorite-rule-v2',
                        award_generation = 2,
                        current_score_entry_id =
                            'favorite-award-rule-v2',
                        last_reversal_score_entry_id =
                            'favorite-reversal-rule-v1',
                        recompute_reason = 'RULE_CHANGED_WHILE_HELD',
                        award_projection_generation = 2,
                        awarded_at =
                            '2026-08-22T01:00:00+00'::timestamptz,
                        row_version = 3
                    WHERE source_region = 'dom' AND teacher_id = 'T1'
                      AND student_token = :student_token
                    """
                ),
                {"student_token": DOM_STUDENT},
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_observations
                    SET status = 'CONFIRMED_TRUE', relation_state = true,
                        relation_evidence_status = 'CONFIRMED',
                        relation_error_code = NULL, row_version = 3
                    WHERE source_region = 'dom' AND source_appoint_id = '9'
                      AND observation_revision = 1
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_attributions
                    SET status = 'AWARDED', hold_reason = NULL,
                        row_version = 4,
                        recompute_reason = 'HISTORY_RESTORED'
                    WHERE source_region = 'dom' AND teacher_id = 'T1'
                      AND student_token = :student_token;
                    """
                ),
                {"student_token": DOM_STUDENT},
            )
        with engine.begin() as connection:
            restored = connection.execute(
                text(
                    """
                    SELECT status, award_generation, current_score_entry_id,
                           points, rule_version
                    FROM public.course_favorite_attributions
                    WHERE source_region = 'dom' AND teacher_id = 'T1'
                      AND student_token = :student_token
                    """
                ),
                {"student_token": DOM_STUDENT},
            ).one()
            assert restored == (
                "AWARDED",
                2,
                "favorite-award-rule-v2",
                6,
                "favorite-rule-v2",
            )

        # The same rule-reaward invariant also applies to a fully confirmed
        # award. The next generation cannot reuse either ledger entry.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.score_entries (score_entry_id)
                    VALUES
                        ('favorite-reversal-rule-v2'),
                        ('favorite-award-rule-v3')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.course_favorite_attributions
                    SET points = 7,
                        rule_version = 'favorite-rule-v3',
                        award_generation = 3,
                        current_score_entry_id =
                            'favorite-award-rule-v3',
                        last_reversal_score_entry_id =
                            'favorite-reversal-rule-v2',
                        recompute_reason = 'RULE_CHANGED_CONFIRMED',
                        award_projection_generation = 3,
                        awarded_at =
                            '2026-08-22T02:00:00+00'::timestamptz,
                        row_version = 5
                    WHERE source_region = 'dom' AND teacher_id = 'T1'
                      AND student_token = :student_token
                    """
                ),
                {"student_token": DOM_STUDENT},
            )

        # A later cancel/refavorite current-state change cannot be used to
        # reverse a course whose frozen +24h observation remains true.
        with pytest.raises(
            DBAPIError,
            match="FAVORITE_REVERSAL_WITHOUT_INVALIDATION",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO public.score_entries VALUES "
                        "('favorite-reversal-without-evidence')"
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE public.course_favorite_attributions
                        SET status = 'REVERSED', hold_reason = NULL,
                            last_reversal_score_entry_id =
                                'favorite-reversal-without-evidence',
                            recompute_reason =
                                'LATE_CURRENT_RELATIONSHIP_CHANGE',
                            reversed_at =
                                '2026-08-22T03:00:00+00'::timestamptz,
                            row_version = 6
                        WHERE source_region = 'dom' AND teacher_id = 'T1'
                          AND student_token = :student_token
                        """
                    ),
                    {"student_token": DOM_STUDENT},
                )

        with pytest.raises(
            DBAPIError,
            match="FAVORITE_ATTRIBUTION_DELETE_DENIED",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM public.course_favorite_attributions "
                        "WHERE source_region = 'dom'"
                    )
                )

        # The same source appoint ID can independently exist in OVS.
        with engine.begin() as connection:
            _seed_course(
                connection,
                "9",
                region="ovs",
                student_token="student-ovs",
            )
            _insert_observation(
                connection,
                "9",
                status="CONFIRMED_TRUE",
                region="ovs",
                student_token="student-ovs",
            )
            _insert_attribution(
                connection,
                "9",
                score_entry_id="favorite-award-ovs",
                region="ovs",
                student_token="student-ovs",
            )
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM "
                    "public.course_favorite_attributions"
                )
            ).scalar_one() == 2

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing relationship/favorite schema downgrade: shadow data exists",
            "downgrade",
            "20260822_73_retire_false_early",
        )
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
