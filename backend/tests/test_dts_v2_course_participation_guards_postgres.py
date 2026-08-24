from __future__ import annotations

import json
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

from test_dts_v2_shadow_source_writer_postgres import (
    _install_revision_65_fixture,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
EPOCH_ID = "epoch:v1:test"
TOPIC = "topic-v2"


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
    assert result.returncode != 0, "guard downgrade unexpectedly succeeded"
    assert expected_error in output


def _position(offset: int) -> str:
    return json.dumps({"position": offset}, separators=(",", ":"))


def _insert_version(
    connection: Connection,
    *,
    course_id: str,
    offset: int,
    revision: int,
    source_table: str = "dom_appoint",
    version_kind: str = "CDC",
    teacher_id: str | None = "teacher-A",
    teacher_id_type: str = "TEXT",
    source_status: str = "on",
    operation: str | None = None,
    before_teacher_id: str | None = None,
    before_source_status: str | None = None,
    before_row_present: bool = False,
    end_time: str | None = None,
    student_token: str | None = None,
    lesson_date: str | None = None,
    lesson_time: str | None = None,
    week: int | None = None,
) -> None:
    snapshot_diff = version_kind == "SNAPSHOT_DIFF"
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_row_versions (
                source_region, source_partition_epoch_id, topic, partition_id,
                offset_value, version_kind, source_table, source_schema_profile_id,
                source_field_types, source_key, source_key_data, source_key_type,
                source_key_numeric, source_key_text, operation, before_row,
                after_row, source_timestamp, record_id_type, record_id_numeric,
                record_id_text, source_position, source_row_revision, snapshot_id,
                snapshot_as_of, covered_through_offsets, diff_step,
                source_table_publish_generation, protected_source_row_hash
            ) VALUES (
                'dom', :epoch_id, :topic, 0, :offset, :version_kind,
                :source_table, 'dom_appoint:v1',
                jsonb_strip_nulls(jsonb_build_object(
                    'id', 'NUMERIC', 'status', 'TEXT',
                    't_id', CAST(:teacher_id_type AS text),
                    'end_time', CASE WHEN CAST(:end_time AS text) IS NULL
                        THEN NULL ELSE 'TEMPORAL' END,
                    'student_token',
                        CASE WHEN CAST(:student_token AS text) IS NULL
                        THEN NULL ELSE 'TEXT' END,
                    'date', CASE WHEN CAST(:lesson_date AS text) IS NULL
                        THEN NULL ELSE 'TEMPORAL' END,
                    'time', CASE WHEN CAST(:lesson_time AS text) IS NULL
                        THEN NULL ELSE 'TEMPORAL' END,
                    'week', CASE WHEN CAST(:week AS integer) IS NULL
                        THEN NULL ELSE 'NUMERIC' END
                )),
                CAST(:course_id AS text),
                jsonb_build_object(
                    'id', CAST(CAST(:course_id AS text) AS numeric)
                ),
                'NUMERIC', CAST(CAST(:course_id AS text) AS numeric), NULL,
                :operation, CAST(:before_row AS jsonb),
                jsonb_build_object(
                    'id', CAST(CAST(:course_id AS text) AS numeric),
                    't_id', CAST(:teacher_id AS text),
                    'status', CAST(:source_status AS text),
                    'end_time', CAST(:end_time AS text),
                    'student_token', CAST(:student_token AS text),
                    'date', CAST(:lesson_date AS text),
                    'time', CAST(:lesson_time AS text),
                    'week', CAST(:week AS integer)
                ),
                '2026-08-22T00:00:00+00'::timestamptz,
                'none', NULL, NULL, CAST(:position AS jsonb),
                :revision, :snapshot_id, NULL, NULL, :diff_step,
                :publish_generation, repeat('a', 64)
            )
            """
        ),
        {
            "epoch_id": EPOCH_ID,
            "topic": TOPIC,
            "offset": offset,
            "version_kind": version_kind,
            "source_table": source_table,
            "course_id": course_id,
            "teacher_id": teacher_id,
            "teacher_id_type": teacher_id_type,
            "source_status": source_status,
            "operation": operation
            or ("SNAPSHOT_BOOTSTRAP_PRESENT" if snapshot_diff else "INSERT"),
            "before_row": (
                None
                if before_teacher_id is None and not before_row_present
                else json.dumps(
                    {
                        "id": int(course_id),
                        "t_id": before_teacher_id,
                        "status": before_source_status or source_status,
                    },
                    separators=(",", ":"),
                )
            ),
            "position": _position(offset),
            "revision": revision,
            "snapshot_id": "snapshot-1" if snapshot_diff else None,
            "diff_step": 1 if snapshot_diff else None,
            "publish_generation": 1 if snapshot_diff else None,
            "end_time": end_time,
            "student_token": student_token,
            "lesson_date": lesson_date,
            "lesson_time": lesson_time,
            "week": week,
        },
    )


def _insert_course(
    connection: Connection,
    course_id: str,
    *,
    current_teacher_id: str | None = None,
    current_teacher_id_type: str | None = None,
    current_participation_seq: int | None = None,
    completion_teacher_id: str | None = None,
    completion_teacher_id_type: str | None = None,
    completion_participation_seq: int | None = None,
    completion_offset: int | None = None,
    completion_revision: int | None = None,
    initial_snapshot: str | None = None,
    last_offset: int | None = None,
    last_revision: int | None = None,
    resolved_offset: int | None = None,
    resolved_revision: int | None = None,
    conflict_status: str | None = None,
    completion_frozen_at_override: str | None = None,
    completion_end_time: str | None = None,
    completion_student_token: str | None = None,
    completion_is_peak: bool | None = None,
    completion_lesson_local_date: str | None = None,
    completion_lesson_local_time: str | None = None,
    normalize_initial_snapshot: bool = True,
) -> None:
    if (
        normalize_initial_snapshot
        and initial_snapshot is not None
        and completion_participation_seq is not None
    ):
        snapshot_data = json.loads(initial_snapshot)
        if isinstance(snapshot_data, dict):
            snapshot_data.setdefault("use_point", None)
            snapshot_data.setdefault("end_time", completion_end_time)
            snapshot_data.setdefault("student_token", completion_student_token)
            snapshot_data.setdefault(
                "lesson_local_date", completion_lesson_local_date
            )
            snapshot_data.setdefault(
                "lesson_local_time", completion_lesson_local_time
            )
            snapshot_data.setdefault("is_peak", completion_is_peak)
            initial_snapshot = json.dumps(
                snapshot_data,
                separators=(",", ":"),
            )
    connection.execute(
        text(
            """
            INSERT INTO public.source_courses (
                source_region, source_appoint_id, current_teacher_id,
                current_teacher_id_type, current_participation_seq,
                completion_teacher_id, completion_teacher_id_type,
                completion_participation_seq, completion_frozen_at,
                completion_end_time, completion_student_token,
                completion_is_peak, completion_lesson_local_date,
                completion_lesson_local_time,
                completion_source_position, completion_source_revision,
                initial_completion_snapshot, last_applied_event_position,
                last_applied_source_revision,
                conflict_resolved_against_position,
                conflict_resolved_against_revision,
                completion_conflict_status
            ) VALUES (
                'dom', :course_id, :current_teacher_id,
                :current_teacher_id_type, :current_participation_seq,
                :completion_teacher_id, :completion_teacher_id_type,
                :completion_participation_seq,
                CASE WHEN CAST(:completion_revision AS bigint) IS NULL THEN NULL
                     WHEN CAST(:completion_frozen_at_override AS text) IS NULL
                     THEN '2026-08-22T00:00:00+00'::timestamptz
                     ELSE CAST(:completion_frozen_at_override AS timestamptz)
                END,
                CAST(:completion_end_time AS timestamptz),
                CAST(:completion_student_token AS text),
                CAST(:completion_is_peak AS boolean),
                CAST(:completion_lesson_local_date AS date),
                CAST(:completion_lesson_local_time AS time),
                CAST(:completion_position AS jsonb),
                CAST(:completion_revision AS bigint),
                CAST(:initial_snapshot AS jsonb), CAST(:last_position AS jsonb),
                :last_revision, CAST(:resolved_position AS jsonb),
                :resolved_revision, COALESCE(:conflict_status, 'NONE')
            )
            """
        ),
        {
            "course_id": course_id,
            "current_teacher_id": current_teacher_id,
            "current_teacher_id_type": (
                current_teacher_id_type
                if current_teacher_id is not None
                else None
            ) or ("TEXT" if current_teacher_id is not None else None),
            "current_participation_seq": current_participation_seq,
            "completion_teacher_id": completion_teacher_id,
            "completion_teacher_id_type": (
                completion_teacher_id_type
                if completion_teacher_id is not None
                else None
            ) or ("TEXT" if completion_teacher_id is not None else None),
            "completion_participation_seq": completion_participation_seq,
            "completion_revision": completion_revision,
            "completion_frozen_at_override": completion_frozen_at_override,
            "completion_position": (
                None if completion_offset is None else _position(completion_offset)
            ),
            "initial_snapshot": initial_snapshot,
            "last_position": None if last_offset is None else _position(last_offset),
            "last_revision": last_revision,
            "resolved_position": (
                None if resolved_offset is None else _position(resolved_offset)
            ),
            "resolved_revision": resolved_revision,
            "conflict_status": conflict_status,
            "completion_end_time": completion_end_time,
            "completion_student_token": completion_student_token,
            "completion_is_peak": completion_is_peak,
            "completion_lesson_local_date": completion_lesson_local_date,
            "completion_lesson_local_time": completion_lesson_local_time,
        },
    )


def _insert_participation(
    connection: Connection,
    course_id: str,
    *,
    seq: int,
    teacher_id: str,
    teacher_id_type: str = "TEXT",
    offset: int,
    revision: int,
    is_current: bool,
    role: str = "NORMAL",
    status: str | None = "on",
    phase: str = "AFTER",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_participations (
                source_region, source_appoint_id, participation_seq, teacher_id,
                teacher_id_type, participation_status, participation_role,
                is_current, assigned_at,
                assigned_at_evidence_status, ended_at, absence_reason_detail,
                no_notice, source_deleted, assignment_source_partition_epoch_id,
                assignment_event_topic, assignment_event_partition,
                assignment_event_offset, assignment_source_row_revision,
                assignment_event_phase
            ) VALUES (
                'dom', :course_id, :seq, :teacher_id, :teacher_id_type,
                :status, :role,
                :is_current, NULL, 'SOURCE_MISSING', NULL, NULL, NULL, false,
                :epoch_id, :topic, 0, :offset, :revision, :phase
            )
            """
        ),
        {
            "course_id": course_id,
            "seq": seq,
            "teacher_id": teacher_id,
            "teacher_id_type": teacher_id_type,
            "status": status,
            "role": role,
            "is_current": is_current,
            "epoch_id": EPOCH_ID,
            "topic": TOPIC,
            "offset": offset,
            "revision": revision,
            "phase": phase,
        },
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for deferred guard checks",
)
def test_revision_68_enforces_deferred_course_participation_guards_on_postgresql(
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
            _install_revision_65_fixture(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_68_course_part_guards",
        )

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_68_course_part_guards"
            privileges = connection.execute(
                text(
                    """
                    SELECT has_table_privilege(
                        role_name,
                        'public.' || table_name,
                        privilege_name
                    )
                    FROM unnest(
                        ARRAY['tit_growth_app', 'tit_dts_ingest_runtime']
                    ) AS roles(role_name)
                    CROSS JOIN unnest(
                        ARRAY['source_courses', 'source_course_participations']
                    ) AS tables(table_name)
                    CROSS JOIN unnest(
                        ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']
                    ) AS privileges(privilege_name)
                    """
                )
            ).scalars().all()
            assert len(privileges) == 16
            assert not any(privileges)
            function_privileges = connection.execute(
                text(
                    """
                    SELECT has_function_privilege(
                        role_name,
                        function_name,
                        'EXECUTE'
                    )
                    FROM unnest(
                        ARRAY['tit_growth_app', 'tit_dts_ingest_runtime']
                    ) AS roles(role_name)
                    CROSS JOIN unnest(
                        ARRAY[
                            'public.dts_v2_course_participation_bidirectional_guard()',
                            'public.dts_v2_course_participation_provenance_guard()',
                            'public.dts_v2_source_course_history_guard()',
                            'public.dts_v2_initial_completion_snapshot_guard()'
                        ]
                    ) AS functions(function_name)
                    """
                )
            ).scalars().all()
            assert len(function_privileges) == 8
            assert not any(function_privileges)
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_partition_epochs (
                        source_region, source_partition_epoch_id, topic,
                        partition_id, epoch_kind, status, stream_generation_id,
                        epoch_opening_id, epoch_sequence, start_offset,
                        v2_epoch_bootstrap_floor, activation_mode
                    ) VALUES (
                        'dom', :epoch_id, :topic, 0, 'BROKER', 'ACTIVE',
                        'generation-1', 'opening-1', 1, 0, 0, 'H0_BOOTSTRAP'
                    )
                    """
                ),
                {"epoch_id": EPOCH_ID, "topic": TOPIC},
            )
            for (
                course_id,
                offset,
                revision,
                source_table,
                version_kind,
                source_status,
            ) in (
                ("9001", 1, 1, "dom_appoint", "CDC", "on"),
                ("9002", 2, 1, "dom_appoint", "CDC", "on"),
                ("9003", 3, 1, "dom_teacher", "CDC", "on"),
                ("9004", 4, 1, "dom_appoint", "CDC", "on"),
                ("9005", 5, 1, "dom_appoint", "CDC", "on"),
                ("9006", 6, 1, "dom_appoint", "CDC", "end"),
                ("9007", 7, 1, "dom_appoint", "CDC", "on"),
                ("9007", 107, 2, "dom_appoint", "SNAPSHOT_DIFF", "on"),
                ("9008", 8, 1, "dom_appoint", "CDC", "end"),
                ("9009", 9, 1, "dom_appoint", "CDC", "on"),
                ("9010", 10, 1, "dom_appoint", "CDC", "on"),
                ("9011", 11, 1, "dom_appoint", "CDC", "on"),
                ("9012", 12, 1, "dom_appoint", "CDC", "end"),
                ("9014", 14, 1, "dom_appoint", "CDC", "end"),
                ("9016", 16, 1, "dom_appoint", "CDC", "end"),
                ("9017", 17, 1, "dom_appoint", "CDC", "on"),
                ("9018", 18, 1, "dom_appoint", "CDC", "on"),
                ("9019", 19, 1, "dom_appoint", "CDC", "end"),
                ("9022", 22, 1, "dom_appoint", "CDC", "on"),
                ("9023", 23, 1, "dom_appoint", "CDC", "on"),
                ("9033", 39, 1, "dom_appoint", "CDC", "on"),
                ("9034", 40, 1, "dom_appoint", "CDC", "on"),
            ):
                _insert_version(
                    connection,
                    course_id=course_id,
                    offset=offset,
                    revision=revision,
                    source_table=source_table,
                    version_kind=version_kind,
                    source_status=source_status,
                    teacher_id=(
                        None
                        if course_id == "9016"
                        else (
                            "teacher-B"
                            if (
                                (course_id == "9007" and revision == 2)
                                or course_id in {"9010", "9014"}
                            )
                            else "teacher-A"
                        )
                    ),
                    operation="UPDATE" if course_id == "9010" else None,
                    before_teacher_id=(
                        "teacher-A" if course_id == "9010" else None
                    ),
                )
            _insert_version(
                connection,
                course_id="9026",
                offset=26,
                revision=1,
                source_status="on",
                teacher_id="teacher-A",
            )
            _insert_version(
                connection,
                course_id="9026",
                offset=27,
                revision=2,
                source_status="end",
                teacher_id="teacher-A",
                operation="UPDATE",
                before_teacher_id="teacher-A",
                before_source_status="on",
            )
            _insert_version(
                connection,
                course_id="9016",
                offset=36,
                revision=2,
                source_status="end",
                teacher_id="teacher-B",
                operation="UPDATE",
                before_teacher_id=None,
                before_source_status="end",
                before_row_present=True,
            )
            _insert_version(
                connection,
                course_id="9027",
                offset=28,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
            )
            _insert_version(
                connection,
                course_id="9027",
                offset=29,
                revision=2,
                source_status="end",
                teacher_id="teacher-B",
                operation="UPDATE",
                before_teacher_id="teacher-A",
                before_source_status="end",
            )
            _insert_version(
                connection,
                course_id="9027",
                offset=30,
                revision=3,
                source_status="end",
                teacher_id="teacher-C",
                operation="UPDATE",
                before_teacher_id="teacher-B",
                before_source_status="end",
            )
            _insert_version(
                connection,
                course_id="9027",
                offset=31,
                revision=4,
                source_status="end",
                teacher_id="teacher-D",
                operation="UPDATE",
                before_teacher_id="teacher-C",
                before_source_status="end",
            )
            _insert_version(
                connection,
                course_id="9028",
                offset=32,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
            )
            _insert_version(
                connection,
                course_id="9029",
                offset=33,
                revision=1,
                source_status="on",
                teacher_id="10",
                teacher_id_type="NUMERIC",
            )
            _insert_version(
                connection,
                course_id="9030",
                offset=34,
                revision=1,
                source_status="end",
                teacher_id="10",
                teacher_id_type="NUMERIC",
            )
            _insert_version(
                connection,
                course_id="9031",
                offset=35,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
            )
            _insert_version(
                connection,
                course_id="9032",
                offset=37,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
            )
            _insert_version(
                connection,
                course_id="9032",
                offset=38,
                revision=2,
                source_status="end",
                teacher_id="teacher-A",
                operation="UPDATE",
                before_teacher_id="teacher-A",
                before_source_status="end",
            )
            _insert_version(
                connection,
                course_id="9035",
                offset=41,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
                end_time="2026-08-22T18:30:00+08:00",
                student_token="dom:v1:" + "b" * 64,
                lesson_date="2026-08-22",
                lesson_time="18:30:00",
                week=6,
            )
            _insert_version(
                connection,
                course_id="9036",
                offset=42,
                revision=1,
                source_status="end",
                teacher_id="teacher-A",
                end_time="2026-08-22 18:30:00",
            )

        with pytest.raises(DBAPIError, match="COMPLETION_POINTER_MISMATCH"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9001",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=1,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9001",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=1,
                    revision=1,
                    is_current=False,
                )

        with pytest.raises(DBAPIError, match="CURRENT_REVERSE_ORPHAN"):
            with engine.begin() as connection:
                _insert_course(connection, "9002")
                _insert_participation(
                    connection,
                    "9002",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=2,
                    revision=1,
                    is_current=True,
                )

        with pytest.raises(DBAPIError, match="PARTICIPATION_PROVENANCE_INVALID"):
            with engine.begin() as connection:
                _insert_course(connection, "9003")
                _insert_participation(
                    connection,
                    "9003",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=3,
                    revision=1,
                    is_current=False,
                )

        with pytest.raises(
            DBAPIError,
            match="PARTICIPATION_TEACHER_PROVENANCE_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(connection, "9009")
                _insert_participation(
                    connection,
                    "9009",
                    seq=1,
                    teacher_id="teacher-B",
                    offset=9,
                    revision=1,
                    is_current=False,
                )

        # Canonical text alone is not a teacher identity.  NUMERIC 10 and
        # TEXT "10" must not be interchangeable at the source-version edge.
        with pytest.raises(
            DBAPIError,
            match="PARTICIPATION_TEACHER_PROVENANCE_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(connection, "9029")
                _insert_participation(
                    connection,
                    "9029",
                    seq=1,
                    teacher_id="10",
                    teacher_id_type="TEXT",
                    offset=33,
                    revision=1,
                    is_current=False,
                )

        with pytest.raises(DBAPIError, match="PARTICIPATION_PROVENANCE_INVALID"):
            with engine.begin() as connection:
                _insert_course(connection, "9004")
                _insert_participation(
                    connection,
                    "9004",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=4,
                    revision=2,
                    is_current=False,
                )

        with pytest.raises(DBAPIError, match="LAST_POSITION_INVALID"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9005",
                    last_offset=999,
                    last_revision=1,
                )

        with pytest.raises(DBAPIError, match="COMPLETION_STATUS_INVALID"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9008",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=8,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9008",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=8,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="on",
                )

        # A forged completion participation cannot turn a source status=on
        # image into an end fact merely by storing participation_status=end.
        with pytest.raises(
            DBAPIError,
            match="COMPLETION_SOURCE_STATUS_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9011",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=11,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9011",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=11,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        with pytest.raises(DBAPIError, match="COMPLETION_SNAPSHOT_REQUIRED"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9012",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=12,
                    completion_revision=1,
                )
                _insert_participation(
                    connection,
                    "9012",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=12,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        with pytest.raises(
            DBAPIError,
            match="COURSE_COMPLETION_FROZEN_AT_SOURCE_MISMATCH",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9012",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=12,
                    completion_revision=1,
                    completion_frozen_at_override=(
                        "2026-08-22T00:00:01+00"
                    ),
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A",'
                        '"teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9012",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=12,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9013",
                    initial_snapshot=(
                        '{"status":"on","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                        ),
                    )

        # A non-null frozen teacher cannot be preloaded as an unattached
        # snapshot.  The only pointer-less PENDING exception is a genuine
        # first end image whose teacher is missing.
        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_POINTER_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9028",
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A",'
                        '"teacher_id_type":"TEXT"}'
                    ),
                    last_offset=32,
                    last_revision=1,
                    conflict_status="PENDING",
                )

        with pytest.raises(
            DBAPIError,
            match="MISSING_TEACHER_SOURCE_INVALID",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9028",
                    initial_snapshot=(
                        '{"status":"end","teacher_id":null,'
                        '"teacher_id_type":null}'
                    ),
                    last_offset=32,
                    last_revision=1,
                    conflict_status="PENDING",
                )

        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_TEACHER_MISMATCH",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9014",
                    completion_teacher_id="teacher-B",
                    completion_participation_seq=1,
                    completion_offset=14,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9014",
                    seq=1,
                    teacher_id="teacher-B",
                    offset=14,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        with pytest.raises(DBAPIError, match="LAST_REVISION_WITHOUT_POSITION"):
            with engine.begin() as connection:
                _insert_course(connection, "9017", last_revision=1)

        with pytest.raises(DBAPIError, match="LAST_POSITION_WITHOUT_REVISION"):
            with engine.begin() as connection:
                _insert_course(connection, "9020", last_offset=20)

        with pytest.raises(
            DBAPIError,
            match="RESOLVED_REVISION_WITHOUT_POSITION",
        ):
            with engine.begin() as connection:
                _insert_course(connection, "9018", resolved_revision=1)

        with pytest.raises(
            DBAPIError,
            match="RESOLVED_POSITION_WITHOUT_REVISION",
        ):
            with engine.begin() as connection:
                _insert_course(connection, "9021", resolved_offset=21)

        with pytest.raises(DBAPIError, match="RESOLVED_POSITION_INVALID"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9022",
                    resolved_offset=999,
                    resolved_revision=1,
                )

        with pytest.raises(DBAPIError, match="COMPLETION_POSITION_INVALID"):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9019",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=999,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )
                _insert_participation(
                    connection,
                    "9019",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=19,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        # Revision 67's immediate pointer-group check and revision 68's
        # deferred history guard jointly make the completion pair total.
        with pytest.raises(
            DBAPIError,
            match="ck_source_course_completion_pointer_group",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9024",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )

        with pytest.raises(
            DBAPIError,
            match="ck_source_course_completion_pointer_group",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9025",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=25,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}'
                    ),
                )

        with engine.begin() as connection:
            _insert_course(
                connection,
                "9006",
                current_teacher_id="teacher-A",
                current_participation_seq=1,
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=6,
                completion_revision=1,
                initial_snapshot='{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}',
                last_offset=6,
                last_revision=1,
            )
            _insert_participation(
                connection,
                "9006",
                seq=1,
                teacher_id="teacher-A",
                offset=6,
                revision=1,
                is_current=True,
                role="COMPLETION",
                status="end",
            )
            _insert_course(
                connection,
                "9035",
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=41,
                completion_revision=1,
                initial_snapshot=json.dumps(
                    {
                        "status": "end",
                        "teacher_id": "teacher-A",
                        "teacher_id_type": "TEXT",
                        "end_time": "2026-08-22T18:30:00+08:00",
                        "student_token": "dom:v1:" + "b" * 64,
                        "lesson_local_date": "2026-08-22",
                        "lesson_local_time": "18:30:00",
                        "is_peak": True,
                    },
                    separators=(",", ":"),
                ),
                last_offset=41,
                last_revision=1,
                completion_end_time="2026-08-22T18:30:00+08:00",
                completion_student_token="dom:v1:" + "b" * 64,
                completion_is_peak=True,
                completion_lesson_local_date="2026-08-22",
                completion_lesson_local_time="18:30:00",
            )
            _insert_participation(
                connection,
                "9035",
                seq=1,
                teacher_id="teacher-A",
                offset=41,
                revision=1,
                is_current=False,
                role="COMPLETION",
                status="end",
            )
            _insert_course(
                connection,
                "9036",
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=42,
                completion_revision=1,
                initial_snapshot=(
                    '{"status":"end","teacher_id":"teacher-A",'
                    '"teacher_id_type":"TEXT",'
                    '"end_time":"2026-08-22 18:30:00"}'
                ),
                last_offset=42,
                last_revision=1,
                completion_end_time=None,
            )
            _insert_participation(
                connection,
                "9036",
                seq=1,
                teacher_id="teacher-A",
                offset=42,
                revision=1,
                is_current=False,
                role="COMPLETION",
                status="end",
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT
                        completion_end_time =
                            '2026-08-22T18:30:00+08:00'::timestamptz,
                        completion_student_token,
                        completion_is_peak,
                        completion_lesson_local_date::text,
                        completion_lesson_local_time::text
                    FROM public.source_courses
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9035'
                    """
                )
            ).one() == (
                True,
                "dom:v1:" + "b" * 64,
                True,
                "2026-08-22",
                "18:30:00",
            )
            assert connection.execute(
                text(
                    """
                    SELECT completion_end_time
                    FROM public.source_courses
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9036'
                    """
                )
            ).scalar_one() is None

        with pytest.raises(
            DBAPIError,
            match="COURSE_COMPLETION_FROZEN_AT_IMMUTABLE",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET completion_frozen_at =
                                completion_frozen_at + interval '1 second',
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9006'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9019",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=19,
                    completion_revision=1,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A",'
                        '"teacher_id_type":"TEXT"}'
                    ),
                    last_offset=19,
                    last_revision=1,
                    normalize_initial_snapshot=False,
                )
                _insert_participation(
                    connection,
                    "9019",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=19,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        # The initial completion JSON is an exact normalized source fact, not
        # an extensible caller payload.  A forged non-null business field must
        # fail even when teacher/status and every pointer are otherwise valid.
        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9019",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=19,
                    completion_revision=1,
                    initial_snapshot=json.dumps(
                        {
                            "status": "end",
                            "teacher_id": "teacher-A",
                            "teacher_id_type": "TEXT",
                            "use_point": "buy",
                        },
                        separators=(",", ":"),
                    ),
                    last_offset=19,
                    last_revision=1,
                )
                _insert_participation(
                    connection,
                    "9019",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=19,
                    revision=1,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        # Flattened completion facts are not a caller-owned cache.  Even with
        # a valid completion pointer and a monotonic row version, a value that
        # is absent from the protected completion source image must be rejected.
        with pytest.raises(
            DBAPIError,
            match="COURSE_COMPLETION_DERIVED_FACT_MISMATCH",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET completion_end_time =
                                '2026-08-22T08:00:00+00'::timestamptz,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9006'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="COURSE_COMPLETION_DERIVED_FACT_MISMATCH",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET completion_is_peak = false,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9035'
                        """
                    )
                )

        # A historical, non-current participation has no reverse course
        # pointer.  Its identity and assignment provenance must nevertheless
        # remain immutable; otherwise it can be moved to another valid appoint
        # version without violating any foreign key.
        with engine.begin() as connection:
            _insert_course(connection, "9033", last_offset=39, last_revision=1)
            _insert_course(connection, "9034", last_offset=40, last_revision=1)
            _insert_participation(
                connection,
                "9033",
                seq=1,
                teacher_id="teacher-A",
                offset=39,
                revision=1,
                is_current=False,
            )

        with pytest.raises(
            DBAPIError,
            match="PARTICIPATION_IDENTITY_IMMUTABLE",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET source_appoint_id = '9034',
                            assignment_event_offset = 40,
                            assignment_source_row_revision = 1,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9033'
                          AND participation_seq = 1
                        """
                    )
                )

        # The same canonical characters remain a NUMERIC identity end to end:
        # source version, course pointers, snapshot, and participation.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9030",
                current_teacher_id="10",
                current_teacher_id_type="NUMERIC",
                current_participation_seq=1,
                completion_teacher_id="10",
                completion_teacher_id_type="NUMERIC",
                completion_participation_seq=1,
                completion_offset=34,
                completion_revision=1,
                initial_snapshot=(
                    '{"status":"end","teacher_id":"10",'
                    '"teacher_id_type":"NUMERIC"}'
                ),
                last_offset=34,
                last_revision=1,
            )
            _insert_participation(
                connection,
                "9030",
                seq=1,
                teacher_id="10",
                teacher_id_type="NUMERIC",
                offset=34,
                revision=1,
                is_current=True,
                role="COMPLETION",
                status="end",
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT c.current_teacher_id_type,
                           c.completion_teacher_id_type,
                           p.teacher_id_type
                    FROM public.source_courses AS c
                    JOIN public.source_course_participations AS p
                      ON p.source_region = c.source_region
                     AND p.source_appoint_id = c.source_appoint_id
                     AND p.participation_seq = c.completion_participation_seq
                     AND p.teacher_id_type = c.completion_teacher_id_type
                    WHERE c.source_region = 'dom'
                      AND c.source_appoint_id = '9030'
                    """
                )
            ).one() == ("NUMERIC", "NUMERIC", "NUMERIC")

        with pytest.raises(
            DBAPIError,
            match="COURSE_ROW_VERSION_NOT_MONOTONIC",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET source_status = 'end'
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9006'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="PARTICIPATION_ROW_VERSION_NOT_MONOTONIC",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET absence_reason_detail = 'No Notification'
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9006'
                          AND participation_seq = 1
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="COURSE_LAST_REVISION_REGRESSION",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET last_applied_event_position = NULL,
                            last_applied_source_revision = NULL,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9006'
                        """
                    )
                )

        # PENDING freezes the participation identity, not only the canonical
        # teacher characters.  A later participation for the same teacher
        # still requires an explicit TRANSFER decision before it can own end.
        # Even an identical later end image cannot replace the first end as
        # initial completion provenance.
        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9032",
                    completion_teacher_id="teacher-A",
                    completion_participation_seq=1,
                    completion_offset=38,
                    completion_revision=2,
                    initial_snapshot=(
                        '{"status":"end","teacher_id":"teacher-A",'
                        '"teacher_id_type":"TEXT"}'
                    ),
                    last_offset=38,
                    last_revision=2,
                )
                _insert_participation(
                    connection,
                    "9032",
                    seq=1,
                    teacher_id="teacher-A",
                    offset=38,
                    revision=2,
                    is_current=False,
                    role="COMPLETION",
                    status="end",
                )

        with engine.begin() as connection:
            _insert_course(
                connection,
                "9032",
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=37,
                completion_revision=1,
                initial_snapshot=(
                    '{"status":"end","teacher_id":"teacher-A",'
                    '"teacher_id_type":"TEXT"}'
                ),
                last_offset=38,
                last_revision=2,
                conflict_status="PENDING",
            )
            _insert_participation(
                connection,
                "9032",
                seq=1,
                teacher_id="teacher-A",
                offset=37,
                revision=1,
                is_current=False,
                role="COMPLETION",
                status="end",
            )
            _insert_participation(
                connection,
                "9032",
                seq=2,
                teacher_id="teacher-A",
                offset=38,
                revision=2,
                is_current=False,
                role="PENDING_CORRECTION",
                status="end",
            )

        with pytest.raises(
            DBAPIError,
            match="PENDING_MUST_INHERIT_RESOLUTION",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET participation_role = 'SUPERSEDED_COMPLETION',
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9032'
                          AND participation_seq = 1
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET participation_role = 'COMPLETION',
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9032'
                          AND participation_seq = 2
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET completion_participation_seq = 2,
                            completion_source_position =
                                CAST(:position AS jsonb),
                            completion_source_revision = 2,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9032'
                        """
                    ),
                    {"position": _position(38)},
                )

        # A→B→C may be corrected against current r3 while explicitly
        # transferring completion to historical participation B/seq2.  The
        # completion revision is the correction boundary, not B's assignment.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9027",
                current_teacher_id="teacher-C",
                current_participation_seq=3,
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=28,
                completion_revision=1,
                initial_snapshot='{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}',
                last_offset=30,
                last_revision=3,
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_conflict_status = 'PENDING',
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                    """
                )
            )
            _insert_participation(
                connection,
                "9027",
                seq=1,
                teacher_id="teacher-A",
                offset=28,
                revision=1,
                is_current=False,
                role="COMPLETION",
                status="end",
            )
            _insert_participation(
                connection,
                "9027",
                seq=2,
                teacher_id="teacher-B",
                offset=29,
                revision=2,
                is_current=False,
                role="PENDING_CORRECTION",
                status="end",
            )
            _insert_participation(
                connection,
                "9027",
                seq=3,
                teacher_id="teacher-C",
                offset=30,
                revision=3,
                is_current=True,
                role="PENDING_CORRECTION",
                status="end",
            )

        with engine.begin() as connection:
            for participation_seq, role in (
                (1, "SUPERSEDED_COMPLETION"),
                (2, "COMPLETION"),
                (3, "REJECTED_CORRECTION"),
            ):
                connection.execute(
                    text(
                        """
                        UPDATE public.source_course_participations
                        SET participation_role = :role,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9027'
                          AND participation_seq = :participation_seq
                        """
                    ),
                    {
                        "role": role,
                        "participation_seq": participation_seq,
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_teacher_id = 'teacher-B',
                        completion_teacher_id_type = 'TEXT',
                        completion_participation_seq = 2,
                        completion_source_position = CAST(:position AS jsonb),
                        completion_source_revision = 3,
                        completion_conflict_status = 'RESOLVED_TRANSFER',
                        conflict_resolved_against_position =
                            CAST(:position AS jsonb),
                        conflict_resolved_against_revision = 3,
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                    """
                ),
                {"position": _position(30)},
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT c.completion_teacher_id,
                           c.completion_participation_seq,
                           c.completion_source_revision,
                           p.participation_role
                    FROM public.source_courses AS c
                    JOIN public.source_course_participations AS p
                      ON p.source_region = c.source_region
                     AND p.source_appoint_id = c.source_appoint_id
                     AND p.participation_seq = c.completion_participation_seq
                    WHERE c.source_region = 'dom'
                      AND c.source_appoint_id = '9027'
                    """
                )
            ).one() == ("teacher-B", 2, 3, "COMPLETION")

        with pytest.raises(
            DBAPIError,
            match="COMPLETION_CORRECTION_BOUNDARY_INVALID",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET completion_source_position = CAST(:position AS jsonb),
                            completion_source_revision = 2,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9027'
                        """
                    ),
                    {"position": _position(29)},
                )

        # A later source assignment reopens the same correction Case.  The
        # earlier transfer provenance remains valid while the status is
        # PENDING and after a subsequent KEEP resolves against the newer r4.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_course_participations
                    SET is_current = false,
                        ended_at = '2026-08-22T01:00:00+00'::timestamptz,
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                      AND participation_seq = 3
                    """
                )
            )
            _insert_participation(
                connection,
                "9027",
                seq=4,
                teacher_id="teacher-D",
                offset=31,
                revision=4,
                is_current=True,
                role="PENDING_CORRECTION",
                status="end",
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET current_teacher_id = 'teacher-D',
                        current_teacher_id_type = 'TEXT',
                        current_participation_seq = 4,
                        last_applied_event_position = CAST(:position AS jsonb),
                        last_applied_source_revision = 4,
                        completion_conflict_status = 'PENDING',
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                    """
                ),
                {"position": _position(31)},
            )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_course_participations
                    SET participation_role = 'REJECTED_CORRECTION',
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                      AND participation_seq = 4;
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_conflict_status = 'RESOLVED_KEEP',
                        conflict_resolved_against_position =
                            CAST(:position AS jsonb),
                        conflict_resolved_against_revision = 4,
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                    """
                ),
                {"position": _position(31)},
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT completion_teacher_id, completion_source_revision,
                           completion_conflict_status,
                           conflict_resolved_against_revision
                    FROM public.source_courses
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9027'
                    """
                )
            ).one() == ("teacher-B", 3, "RESOLVED_KEEP", 4)

        with pytest.raises(
            DBAPIError,
            match="COURSE_RESOLVED_REVISION_REGRESSION",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET conflict_resolved_against_position = NULL,
                            conflict_resolved_against_revision = NULL,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9027'
                        """
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="COURSE_RESOLVED_REVISION_REGRESSION",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET conflict_resolved_against_position =
                                CAST(:position AS jsonb),
                            conflict_resolved_against_revision = 3,
                            row_version = row_version + 1
                        WHERE source_region = 'dom'
                          AND source_appoint_id = '9027'
                        """
                    ),
                    {"position": _position(30)},
                )

        # The teacher may have been assigned by r1 while r2 changes only the
        # appoint status from on to end.  Assignment provenance stays on r1;
        # the course completion pointer is what must prove r2/status=end.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9026",
                current_teacher_id="teacher-A",
                current_participation_seq=1,
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=27,
                completion_revision=2,
                initial_snapshot='{"status":"end","teacher_id":"teacher-A","teacher_id_type":"TEXT"}',
                last_offset=27,
                last_revision=2,
            )
            _insert_participation(
                connection,
                "9026",
                seq=1,
                teacher_id="teacher-A",
                offset=26,
                revision=1,
                is_current=True,
                role="COMPLETION",
                status="end",
            )

        # The first authoritative end may have no teacher.  Its initial
        # snapshot is retained, while a later controlled correction may
        # establish the completion participation without rewriting history.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9016",
                initial_snapshot='{"status":"end","teacher_id":null,"teacher_id_type":null}',
                last_offset=16,
                last_revision=1,
                conflict_status="PENDING",
            )

        # A valid source boundary cannot be preloaded as if a correction had
        # already been approved; it must transition from an existing PENDING
        # course fact.
        with pytest.raises(
            DBAPIError,
            match="FIRST_RESOLUTION_REQUIRES_HISTORY",
        ):
            with engine.begin() as connection:
                _insert_course(
                    connection,
                    "9023",
                    resolved_offset=23,
                    resolved_revision=1,
                    conflict_status="PENDING",
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_teacher_id = 'teacher-B',
                        completion_teacher_id_type = 'TEXT',
                        completion_participation_seq = 1,
                        completion_frozen_at =
                            '2026-08-22T00:00:00+00'::timestamptz,
                        completion_source_position = CAST(:position AS jsonb),
                        completion_source_revision = 2,
                        completion_conflict_status = 'RESOLVED_TRANSFER',
                        conflict_resolved_against_position =
                            CAST(:position AS jsonb),
                        conflict_resolved_against_revision = 2,
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9016'
                    """
                ),
                {"position": _position(36)},
            )
            _insert_participation(
                connection,
                "9016",
                seq=1,
                teacher_id="teacher-B",
                offset=36,
                revision=2,
                is_current=False,
                role="COMPLETION",
                status="end",
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT initial_completion_snapshot,
                           completion_teacher_id,
                           completion_participation_seq
                    FROM public.source_courses
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9016'
                    """
                )
            ).one() == (
                {
                    "status": "end",
                    "teacher_id": None,
                    "teacher_id_type": None,
                },
                "teacher-B",
                1,
            )

        # VOID is the other legal snapshot/pointer exception: current
        # completion ownership is cleared, but the immutable first-end image
        # remains available for audit.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9031",
                completion_teacher_id="teacher-A",
                completion_participation_seq=1,
                completion_offset=35,
                completion_revision=1,
                initial_snapshot=(
                    '{"status":"end","teacher_id":"teacher-A",'
                    '"teacher_id_type":"TEXT"}'
                ),
                last_offset=35,
                last_revision=1,
                conflict_status="PENDING",
            )
            _insert_participation(
                connection,
                "9031",
                seq=1,
                teacher_id="teacher-A",
                offset=35,
                revision=1,
                is_current=False,
                role="COMPLETION",
                status="end",
            )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_course_participations
                    SET participation_role = 'VOIDED_COMPLETION',
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9031'
                      AND participation_seq = 1
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET completion_teacher_id = NULL,
                        completion_teacher_id_type = NULL,
                        completion_participation_seq = NULL,
                        completion_frozen_at = NULL,
                        completion_source_position = NULL,
                        completion_source_revision = NULL,
                        completion_conflict_status = 'RESOLVED_VOID',
                        conflict_resolved_against_position =
                            CAST(:position AS jsonb),
                        conflict_resolved_against_revision = 1,
                        completion_voided_at = clock_timestamp(),
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9031'
                    """
                ),
                {"position": _position(35)},
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT initial_completion_snapshot,
                           completion_teacher_id,
                           completion_teacher_id_type,
                           completion_participation_seq,
                           completion_conflict_status
                    FROM public.source_courses
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9031'
                    """
                )
            ).one() == (
                {
                    "status": "end",
                    "teacher_id": "teacher-A",
                    "teacher_id_type": "TEXT",
                    "use_point": None,
                    "end_time": None,
                    "student_token": None,
                    "lesson_local_date": None,
                    "lesson_local_time": None,
                    "is_peak": None,
                },
                None,
                None,
                None,
                "RESOLVED_VOID",
            )

        with pytest.raises(
            DBAPIError,
            match="INITIAL_COMPLETION_SNAPSHOT_IMMUTABLE",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.source_courses
                        SET initial_completion_snapshot = '{"status":"changed"}'::jsonb,
                            row_version = row_version + 1
                        WHERE source_region = 'dom' AND source_appoint_id = '9006'
                        """
                    )
                )

        with engine.begin() as connection:
            _insert_course(
                connection,
                "9007",
                current_teacher_id="teacher-A",
                current_participation_seq=1,
            )
            _insert_participation(
                connection,
                "9007",
                seq=1,
                teacher_id="teacher-A",
                offset=7,
                revision=1,
                is_current=True,
            )

        # One complete CDC UPDATE may create the recovered BEFORE teacher and
        # the newly assigned AFTER teacher in the same source revision.
        with engine.begin() as connection:
            _insert_course(
                connection,
                "9010",
                current_teacher_id="teacher-B",
                current_participation_seq=2,
                last_offset=10,
                last_revision=1,
            )
            _insert_participation(
                connection,
                "9010",
                seq=1,
                teacher_id="teacher-A",
                offset=10,
                revision=1,
                is_current=False,
                status="t_absent",
                phase="BEFORE",
            )
            _insert_participation(
                connection,
                "9010",
                seq=2,
                teacher_id="teacher-B",
                offset=10,
                revision=1,
                is_current=True,
                phase="AFTER",
            )

        # Both reciprocal trigger registrations observe the final B state only.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_course_participations
                    SET is_current = false,
                        row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9007'
                      AND participation_seq = 1;
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET current_teacher_id = 'teacher-B',
                        current_teacher_id_type = 'TEXT',
                        current_participation_seq = 2,
                        last_applied_event_position = CAST(:position AS jsonb),
                        last_applied_source_revision = 2,
                        row_version = row_version + 1
                    WHERE source_region = 'dom' AND source_appoint_id = '9007';
                    """
                ),
                {"position": _position(107)},
            )
            _insert_participation(
                connection,
                "9007",
                seq=2,
                teacher_id="teacher-B",
                offset=107,
                revision=2,
                is_current=True,
                phase="SNAPSHOT_DIFF",
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT current_teacher_id, current_participation_seq
                    FROM public.source_courses
                    WHERE source_region = 'dom' AND source_appoint_id = '9007'
                    """
                )
            ).one() == ("teacher-B", 2)
            assert connection.execute(
                text(
                    """
                    SELECT participation_seq, teacher_id
                    FROM public.source_course_participations
                    WHERE source_region = 'dom' AND source_appoint_id = '9007'
                      AND is_current IS TRUE
                    """
                )
            ).one() == (2, "teacher-B")
            assert connection.execute(
                text(
                    """
                    SELECT participation_seq, teacher_id,
                           participation_status, is_current,
                           assignment_event_phase
                    FROM public.source_course_participations
                    WHERE source_region = 'dom'
                      AND source_appoint_id = '9010'
                    ORDER BY participation_seq
                    """
                )
            ).all() == [
                (1, "teacher-A", "t_absent", False, "BEFORE"),
                (2, "teacher-B", "on", True, "AFTER"),
            ]

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing course-participation guard downgrade: shadow data exists",
            "downgrade",
            "20260822_67_course_part",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        public.source_course_participations,
                        public.source_courses
                    """
                )
            )
        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_67_course_part",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_67_course_part"
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgrelid = ANY (ARRAY[
                        'public.source_courses'::regclass,
                        'public.source_course_participations'::regclass
                    ])
                      AND tgname IN (
                        'ct_source_course_bidirectional_guard',
                        'ct_source_course_participation_bidirectional_guard',
                        'ct_source_course_participation_provenance_guard',
                        'ct_source_course_history_guard',
                        'ct_source_course_initial_completion_snapshot_guard'
                    )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM unnest(ARRAY[
                        'public.dts_v2_course_participation_bidirectional_guard()',
                        'public.dts_v2_course_participation_provenance_guard()',
                        'public.dts_v2_source_course_history_guard()',
                        'public.dts_v2_initial_completion_snapshot_guard()'
                    ]) AS functions(function_name)
                    WHERE to_regprocedure(function_name) IS NOT NULL
                    """
                )
            ).scalar_one() == 0
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
