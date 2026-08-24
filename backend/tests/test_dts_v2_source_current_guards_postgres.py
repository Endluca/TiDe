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
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.exc import DBAPIError


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
EPOCH_ID = "epoch:v2:source-current-test"
TOPIC = "topic-source-current-v2"
SOURCE_KEY = "101"
SNAPSHOT_EPOCH_ID = "epoch:v2:source-current-snapshot-test"
SNAPSHOT_TOPIC = "topic-source-current-v2-snapshot"
SNAPSHOT_ID = "snapshot:v2:source-current-test"
SNAPSHOT_SOURCE_KEY = "404"
FIELD_TYPES = {
    "active": "BOOLEAN",
    "id": "NUMERIC",
    "start": "TEMPORAL",
    "t_id": "TEXT",
}


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


def _seed_external_personalized_catalog(connection: Connection) -> None:
    """Seed the three operational rows required by forward-only revisions."""

    connection.execute(
        text(
            """
            INSERT INTO public.task_templates (
                row_id, template_id, template_version, status, revision,
                output_type, execution_owner, integration_mode,
                external_task_template_code, source_mode, payload,
                created_by, updated_by, created_at, updated_at
            ) VALUES (
                'P-FB-NEGATIVE:v1', 'P-FB-NEGATIVE', 1, 'PUBLISHED', 5,
                'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                'P-FB-NEGATIVE', 'REAL',
                jsonb_build_object(
                    'template_id', 'P-FB-NEGATIVE',
                    'title', 'Feedback Improvement',
                    'category', 'PERSONALIZED_IMPROVEMENT',
                    'content_status', 'READY',
                    'score_type', 'ZERO',
                    'score_value', 0,
                    'how_summary',
                        'Complete the learning activity assigned for the feedback issue shown in the task reason.',
                    'completion_standard',
                        'The teacher app marks the matching learning activity as completed.'
                ),
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                '2026-08-11T00:00:00+00',
                '2026-08-11T00:00:00+00'
            ), (
                'P-REL-MEMO:v1', 'P-REL-MEMO', 1, 'PUBLISHED', 2,
                'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                'P-REL-MEMO', 'REAL',
                jsonb_build_object(
                    'template_id', 'P-REL-MEMO',
                    'title', 'Lesson Memo Improvement',
                    'category', 'PERSONALIZED_IMPROVEMENT',
                    'content_status', 'READY',
                    'score_type', 'ZERO',
                    'score_value', 0,
                    'why_template',
                        'A completed lesson was recorded with an unfilled Lesson Memo.',
                    'benefit',
                        'This task carries no points. It closes the identified Lesson Memo reliability gap.'
                ),
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                '2026-08-11T00:00:00+00',
                '2026-08-11T00:00:00+00'
            ), (
                'P-REL-ATTENDANCE:v1', 'P-REL-ATTENDANCE', 1,
                'PUBLISHED', 2,
                'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                'P-REL-ATTENDANCE', 'REAL',
                jsonb_build_object(
                    'template_id', 'P-REL-ATTENDANCE',
                    'title', 'Attendance Improvement',
                    'category', 'PERSONALIZED_IMPROVEMENT',
                    'content_status', 'READY',
                    'score_type', 'ZERO',
                    'score_value', 0,
                    'why_template',
                        'A lesson record contains a reliability issue such as absence, late arrival or early leave.'
                ),
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                'POSTGRES_SOURCE_GUARD_FIXTURE',
                '2026-08-11T00:00:00+00',
                '2026-08-11T00:00:00+00'
            )
            """
        )
    )


def _row(teacher_id: str) -> dict[str, object]:
    return {
        "active": True,
        "id": int(SOURCE_KEY),
        "start": "2026-08-22T00:00:00Z",
        "t_id": teacher_id,
    }


def _insert_version(
    connection: Connection,
    *,
    revision: int,
    operation: str,
    before_row: dict[str, object] | None,
    after_row: dict[str, object] | None,
    field_types: dict[str, str] | None = None,
    source_key: str = SOURCE_KEY,
    source_key_type: str = "NUMERIC",
    epoch_id: str = EPOCH_ID,
    topic: str = TOPIC,
    version_kind: str = "CDC",
    snapshot_id: str | None = None,
    diff_step: int | None = None,
    source_table_publish_generation: int | None = None,
    offset_value: int | None = None,
) -> None:
    source_key_value: object = (
        int(source_key) if source_key_type == "NUMERIC" else source_key
    )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_row_versions (
                source_region, source_partition_epoch_id, topic, partition_id,
                offset_value, version_kind, source_table,
                source_schema_profile_id, source_field_types, source_key,
                source_key_data, source_key_type, source_key_numeric,
                source_key_text, operation, before_row, after_row,
                source_timestamp, record_id_type, record_id_numeric,
                record_id_text, source_position, source_row_revision,
                snapshot_id, snapshot_as_of, covered_through_offsets,
                diff_step, source_table_publish_generation,
                protected_source_row_hash
            ) VALUES (
                'dom', :epoch_id, :topic, 0, :offset_value, :version_kind,
                'dom_appoint',
                'dom_appoint:v2:test', CAST(:field_types AS jsonb),
                CAST(:source_key AS text),
                CAST(:source_key_data AS jsonb), :source_key_type,
                CAST(:source_key_numeric AS numeric), :source_key_text,
                :operation,
                CAST(:before_row AS jsonb), CAST(:after_row AS jsonb),
                CAST(:source_timestamp AS timestamptz), 'none', NULL, NULL,
                jsonb_build_object('position', :revision), :revision,
                :snapshot_id, NULL, NULL, :diff_step,
                :source_table_publish_generation,
                repeat(substr('abcdef', :revision, 1), 64)
            )
            """
        ),
        {
            "epoch_id": epoch_id,
            "topic": topic,
            "revision": revision,
            "offset_value": (
                revision if offset_value is None else offset_value
            ),
            "version_kind": version_kind,
            "source_key": source_key,
            "source_key_data": json.dumps(
                {"id": source_key_value}, separators=(",", ":")
            ),
            "source_key_type": source_key_type,
            "source_key_numeric": (
                source_key if source_key_type == "NUMERIC" else None
            ),
            "source_key_text": (
                source_key if source_key_type == "TEXT" else None
            ),
            "operation": operation,
            "before_row": None if before_row is None else json.dumps(before_row),
            "after_row": None if after_row is None else json.dumps(after_row),
            "source_timestamp": f"2026-08-22T00:0{revision}:00+00:00",
            "field_types": json.dumps(
                FIELD_TYPES if field_types is None else field_types,
                separators=(",", ":"),
            ),
            "snapshot_id": snapshot_id,
            "diff_step": diff_step,
            "source_table_publish_generation": (
                source_table_publish_generation
            ),
        },
    )


def _upsert_current(
    connection: Connection,
    *,
    revision: int,
    source_row: dict[str, object],
    is_deleted: bool,
    source_key: str = SOURCE_KEY,
    source_key_type: str = "NUMERIC",
    epoch_id: str = EPOCH_ID,
    topic: str = TOPIC,
    version_kind: str = "CDC",
    field_types: dict[str, str] | None = None,
    offset_value: int | None = None,
) -> None:
    source_key_value: object = (
        int(source_key) if source_key_type == "NUMERIC" else source_key
    )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version, source_row_revision,
                last_source_partition_epoch_id, last_version_kind,
                source_position_v2, record_id_type, record_id_numeric,
                record_id_text, source_timestamp_v2, source_payload_hash,
                provenance_state, source_key_type, source_key_numeric,
                source_key_text, source_schema_profile_id, source_field_types
            ) VALUES (
                'dom', 'dom_appoint', CAST(:source_key AS text),
                CAST(:source_key_data AS jsonb),
                '{}'::jsonb, CAST(:source_row AS jsonb), :is_deleted,
                :revision, :revision, CAST(:revision AS text), :topic, 0,
                :offset_value, :revision, :revision, :epoch_id, :version_kind,
                jsonb_build_object('position', :revision), 'none', NULL, NULL,
                CAST(:source_timestamp AS timestamptz),
                repeat(substr('abcdef', :revision, 1), 64), 'V2_CONFIRMED',
                :source_key_type, CAST(:source_key_numeric AS numeric),
                :source_key_text,
                'dom_appoint:v2:test', CAST(:field_types AS jsonb)
            )
            ON CONFLICT (source_region, source_table, source_key) DO UPDATE SET
                source_key_data = EXCLUDED.source_key_data,
                source_row = EXCLUDED.source_row,
                is_deleted = EXCLUDED.is_deleted,
                source_timestamp = EXCLUDED.source_timestamp,
                last_record_id = EXCLUDED.last_record_id,
                source_position = EXCLUDED.source_position,
                last_topic = EXCLUDED.last_topic,
                last_partition = EXCLUDED.last_partition,
                last_offset = EXCLUDED.last_offset,
                row_version = public.dts_source_rows.row_version + 1,
                source_row_revision = EXCLUDED.source_row_revision,
                last_source_partition_epoch_id =
                    EXCLUDED.last_source_partition_epoch_id,
                last_version_kind = EXCLUDED.last_version_kind,
                source_position_v2 = EXCLUDED.source_position_v2,
                record_id_type = EXCLUDED.record_id_type,
                record_id_numeric = EXCLUDED.record_id_numeric,
                record_id_text = EXCLUDED.record_id_text,
                source_timestamp_v2 = EXCLUDED.source_timestamp_v2,
                source_payload_hash = EXCLUDED.source_payload_hash,
                provenance_state = EXCLUDED.provenance_state,
                source_key_type = EXCLUDED.source_key_type,
                source_key_numeric = EXCLUDED.source_key_numeric,
                source_key_text = EXCLUDED.source_key_text,
                source_schema_profile_id = EXCLUDED.source_schema_profile_id,
                source_field_types = EXCLUDED.source_field_types
            """
        ),
        {
            "source_key": source_key,
            "source_key_data": json.dumps(
                {"id": source_key_value}, separators=(",", ":")
            ),
            "source_key_type": source_key_type,
            "source_key_numeric": (
                source_key if source_key_type == "NUMERIC" else None
            ),
            "source_key_text": (
                source_key if source_key_type == "TEXT" else None
            ),
            "source_row": json.dumps(source_row, separators=(",", ":")),
            "is_deleted": is_deleted,
            "revision": revision,
            "offset_value": (
                revision if offset_value is None else offset_value
            ),
            "topic": topic,
            "epoch_id": epoch_id,
            "version_kind": version_kind,
            "source_timestamp": f"2026-08-22T00:0{revision}:00+00:00",
            "field_types": json.dumps(
                FIELD_TYPES if field_types is None else field_types,
                separators=(",", ":"),
            ),
        },
    )


def _expect_transaction_failure(
    engine: Engine,
    statement: str,
    parameters: dict[str, object] | None = None,
) -> None:
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text(statement), parameters or {})


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for deferred source guards",
)
def test_revision_69_guards_the_complete_source_version_current_chain(
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
            for role_name in (
                "tit_growth_app",
                "tit_teacher_crud",
                "tit_dts_ingest_runtime",
                "tide_support_ticket_owner",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} LOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    )
                )

        # Exercise the actual migration chain rather than a stamped mock schema.
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with engine.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_99_blacklist_three_state",
        )

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_99_blacklist_three_state"
            connection.execute(
                text(
                    """
                        INSERT INTO public.dts_source_partition_epochs (
                            source_region, source_partition_epoch_id, topic,
                            partition_id, epoch_kind, status, stream_generation_id,
                            epoch_opening_id, epoch_sequence, start_offset,
                            v2_epoch_bootstrap_floor
                        ) VALUES (
                            'dom', :epoch_id, :topic, 0, 'BROKER', 'ACTIVE',
                            'generation-source-current', 'opening-source-current',
                            1, 0, 0
                        )
                    """
                ),
                {"epoch_id": EPOCH_ID, "topic": TOPIC},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_partition_epochs (
                        source_region, source_partition_epoch_id, topic,
                        partition_id, epoch_kind, status, snapshot_id,
                        source_table
                    ) VALUES (
                        'dom', :epoch_id, :topic, 0, 'SNAPSHOT_DIFF',
                        'SEALED', :snapshot_id, 'dom_appoint'
                    )
                    """
                ),
                {
                    "epoch_id": SNAPSHOT_EPOCH_ID,
                    "topic": SNAPSHOT_TOPIC,
                    "snapshot_id": SNAPSHOT_ID,
                },
            )

            immutable_validator = connection.execute(
                text(
                    """
                    SELECT provolatile
                    FROM pg_proc
                    WHERE oid =
                        'public.dts_v2_source_field_types_valid(jsonb)'::regprocedure
                    """
                )
            ).scalar_one()
            assert immutable_validator == "i"

            table_privileges = connection.execute(
                text(
                    """
                    SELECT role_name, table_name, privilege_name
                    FROM unnest(ARRAY[
                        'tit_growth_app', 'tit_dts_ingest_runtime',
                        'tit_teacher_crud'
                    ]) AS roles(role_name)
                    CROSS JOIN unnest(ARRAY[
                        'dts_source_partition_epochs',
                        'dts_source_row_versions'
                    ]) AS tables(table_name)
                    CROSS JOIN unnest(ARRAY[
                        'SELECT', 'INSERT', 'UPDATE', 'DELETE'
                    ]) AS privileges(privilege_name)
                    WHERE has_table_privilege(
                        role_name, 'public.' || table_name, privilege_name
                    )
                    """
                )
            ).tuples().all()
            assert set(table_privileges) == {
                (
                    "tit_growth_app",
                    "dts_source_partition_epochs",
                    "SELECT",
                ),
                (
                    "tit_growth_app",
                    "dts_source_row_versions",
                    "SELECT",
                ),
                (
                    "tit_dts_ingest_runtime",
                    "dts_source_partition_epochs",
                    "SELECT",
                ),
                (
                    "tit_dts_ingest_runtime",
                    "dts_source_row_versions",
                    "SELECT",
                ),
                (
                    "tit_dts_ingest_runtime",
                    "dts_source_row_versions",
                    "INSERT",
                ),
            }

            function_privileges = connection.execute(
                text(
                    """
                    SELECT role_name, function_name
                    FROM unnest(ARRAY[
                        'tit_growth_app', 'tit_dts_ingest_runtime',
                        'tit_teacher_crud'
                    ]) AS roles(role_name)
                    CROSS JOIN unnest(ARRAY[
                        'public.dts_v2_source_field_types_valid(jsonb)',
                        'public.dts_v2_source_row_transition_valid(text,jsonb,bigint,text,text,jsonb,text,numeric,text,timestamptz,text,text,text,numeric,text,text,jsonb)',
                        'public.dts_v2_assert_source_current_pair(text,text,text)',
                        'public.dts_v2_source_current_pair_guard()'
                    ]) AS functions(function_name)
                    WHERE has_function_privilege(
                        role_name, function_name, 'EXECUTE'
                    )
                    """
                )
            ).tuples().all()
            assert set(function_privileges) == {
                (
                    "tit_dts_ingest_runtime",
                    "public.dts_v2_source_field_types_valid(jsonb)",
                ),
                (
                    "tit_dts_ingest_runtime",
                    "public.dts_v2_source_row_transition_valid(text,jsonb,"
                    "bigint,text,text,jsonb,text,numeric,text,timestamptz,"
                    "text,text,text,numeric,text,text,jsonb)",
                ),
                (
                    "tit_dts_ingest_runtime",
                    "public.dts_v2_assert_source_current_pair(text,text,text)",
                ),
            }

        # A complete INSERT -> UPDATE -> DELETE chain can advance atomically.
        row_a = _row("teacher-A")
        row_b = _row("teacher-B")
        with engine.begin() as connection:
            _insert_version(
                connection,
                revision=1,
                operation="INSERT",
                before_row=None,
                after_row=row_a,
            )
            _upsert_current(
                connection,
                revision=1,
                source_row=row_a,
                is_deleted=False,
            )
        with engine.begin() as connection:
            _insert_version(
                connection,
                revision=2,
                operation="UPDATE",
                before_row=row_a,
                after_row=row_b,
            )
            _upsert_current(
                connection,
                revision=2,
                source_row=row_b,
                is_deleted=False,
            )
        with engine.begin() as connection:
            _insert_version(
                connection,
                revision=3,
                operation="DELETE",
                before_row=row_b,
                after_row=None,
            )
            _upsert_current(
                connection,
                revision=3,
                source_row=row_b,
                is_deleted=True,
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision, is_deleted,
                           source_row ->> 't_id', provenance_state
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = :source_key
                    """
                ),
                {"source_key": SOURCE_KEY},
            ).one() == (3, True, "teacher-B", "V2_CONFIRMED")

        # Appoint identities are numeric in the verified source profile.  The
        # generic typed-key shape must not allow a direct TEXT-key version.
        with pytest.raises(DBAPIError) as version_text_error:
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=4,
                    operation="INSERT",
                    before_row=None,
                    after_row={"id": "appoint-text-version"},
                    source_key="appoint-text-version",
                    source_key_type="TEXT",
                    field_types={"id": "TEXT"},
                )
        assert (
            "ck_dts_source_row_version_profiled_key_type"
            in str(version_text_error.value)
        )

        # The current projection independently rejects the same false source
        # identity, even before the deferred version/current pairing check.
        with pytest.raises(DBAPIError) as current_text_error:
            with engine.begin() as connection:
                _upsert_current(
                    connection,
                    revision=1,
                    source_key="appoint-text-current",
                    source_key_type="TEXT",
                    source_row={"id": "appoint-text-current"},
                    is_deleted=False,
                    field_types={"id": "TEXT"},
                )
        assert (
            "ck_dts_source_row_profiled_key_type"
            in str(current_text_error.value)
        )

        # A bootstrap tombstone keeps its protected BEFORE image as current
        # evidence and must project it as deleted.  A live projection is a
        # semantic mismatch even though both rows are individually shaped.
        tombstone_row = {
            **_row("teacher-tombstone"),
            "id": int(SNAPSHOT_SOURCE_KEY),
        }
        with pytest.raises(DBAPIError) as live_tombstone_error:
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=1,
                    operation="SNAPSHOT_BOOTSTRAP_TOMBSTONE",
                    before_row=tombstone_row,
                    after_row=None,
                    source_key=SNAPSHOT_SOURCE_KEY,
                    epoch_id=SNAPSHOT_EPOCH_ID,
                    topic=SNAPSHOT_TOPIC,
                    version_kind="SNAPSHOT_DIFF",
                    snapshot_id=SNAPSHOT_ID,
                    diff_step=1,
                    source_table_publish_generation=1,
                )
                _upsert_current(
                    connection,
                    revision=1,
                    source_key=SNAPSHOT_SOURCE_KEY,
                    source_row=tombstone_row,
                    is_deleted=False,
                    epoch_id=SNAPSHOT_EPOCH_ID,
                    topic=SNAPSHOT_TOPIC,
                    version_kind="SNAPSHOT_DIFF",
                )
        assert (
            "DTS_V2_SOURCE_CURRENT_VERSION_MISMATCH"
            in str(live_tombstone_error.value)
        )

        with engine.begin() as connection:
            _insert_version(
                connection,
                revision=1,
                operation="SNAPSHOT_BOOTSTRAP_TOMBSTONE",
                before_row=tombstone_row,
                after_row=None,
                source_key=SNAPSHOT_SOURCE_KEY,
                epoch_id=SNAPSHOT_EPOCH_ID,
                topic=SNAPSHOT_TOPIC,
                version_kind="SNAPSHOT_DIFF",
                snapshot_id=SNAPSHOT_ID,
                diff_step=1,
                source_table_publish_generation=1,
            )
            _upsert_current(
                connection,
                revision=1,
                source_key=SNAPSHOT_SOURCE_KEY,
                source_row=tombstone_row,
                is_deleted=True,
                epoch_id=SNAPSHOT_EPOCH_ID,
                topic=SNAPSHOT_TOPIC,
                version_kind="SNAPSHOT_DIFF",
            )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision, is_deleted,
                           source_row ->> 't_id', last_version_kind
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = :source_key
                    """
                ),
                {"source_key": SNAPSHOT_SOURCE_KEY},
            ).one() == (1, True, "teacher-tombstone", "SNAPSHOT_DIFF")

        # A latest/current pair cannot jump over a missing revision.  Reject
        # the rev3 transaction itself; immutable history must never depend on
        # a later gap fill.
        gap_source_key = "505"
        gap_row_a = {**_row("teacher-gap-A"), "id": int(gap_source_key)}
        gap_row_b = {**gap_row_a, "t_id": "teacher-gap-B"}
        with engine.begin() as connection:
            _insert_version(
                connection,
                revision=1,
                operation="INSERT",
                before_row=None,
                after_row=gap_row_a,
                source_key=gap_source_key,
                offset_value=20,
            )
            _upsert_current(
                connection,
                revision=1,
                source_row=gap_row_a,
                is_deleted=False,
                source_key=gap_source_key,
                offset_value=20,
            )
        with pytest.raises(
            DBAPIError,
            match="DTS_V2_SOURCE_REVISION_GAP",
        ):
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=3,
                    operation="UPDATE",
                    before_row=gap_row_a,
                    after_row=gap_row_b,
                    source_key=gap_source_key,
                    offset_value=22,
                )
                _upsert_current(
                    connection,
                    revision=3,
                    source_row=gap_row_b,
                    is_deleted=False,
                    source_key=gap_source_key,
                    offset_value=22,
                )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision, source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = :source_key
                    """
                ),
                {"source_key": gap_source_key},
            ).one() == (1, "teacher-gap-A")
        with pytest.raises(DBAPIError) as gap_field_types_error:
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=2,
                    operation="UPDATE",
                    before_row=gap_row_a,
                    after_row=gap_row_b,
                    source_key=gap_source_key,
                    field_types={},
                    offset_value=21,
                )
        assert (
            "ck_dts_source_row_version_field_type_values"
            in str(gap_field_types_error.value)
        )

        # The field map is an object whose values are from the closed family.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=4,
                    operation="UPDATE",
                    before_row=row_b,
                    after_row=row_b,
                    field_types={"id": "UUID"},
                )

        # A revisioned source fact must at least type its primary identity;
        # an empty map is not evidence merely because it has JSON shape.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=4,
                    operation="UPDATE",
                    before_row=row_b,
                    after_row=row_b,
                    field_types={},
                )

        # A version cannot commit without advancing its current row.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                _insert_version(
                    connection,
                    revision=4,
                    operation="UPDATE",
                    before_row=row_b,
                    after_row=row_a,
                )

        # Partial provenance is neither legacy nor a confirmed v2 projection.
        _expect_transaction_failure(
            engine,
            """
            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version, provenance_state
            ) VALUES (
                'dom', 'dom_appoint', '202', jsonb_build_object('id', 202),
                '{}'::jsonb, jsonb_build_object('id', 202),
                false, 1, 1, 'legacy',
                'legacy-topic', 0, 1, 1, 'V2_CONFIRMED'
            )
            """,
        )

        # Both legacy shapes remain legal during the shadow transition.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_rows (
                        source_region, source_table, source_key,
                        source_key_data, dependency_keys, source_row,
                        is_deleted, source_timestamp, last_record_id,
                        source_position, last_topic, last_partition,
                        last_offset, row_version, provenance_state
                    ) VALUES
                    ('dom', 'dom_teacher', '203',
                     jsonb_build_object('id', 203), '{}'::jsonb,
                     jsonb_build_object('id', 203), false, 1, 1,
                     'legacy', 'legacy-topic', 0, 1, 1, 'LEGACY_PENDING'),
                    ('dom', 'dom_teacher', '204',
                     jsonb_build_object('id', 204), '{}'::jsonb,
                     jsonb_build_object('id', 204), false, 1, 1,
                     'legacy', 'legacy-topic', 0, 1, 1, NULL)
                    """
                )
            )

        # Individually well-formed but semantically false current states fail
        # at the deferred pair guard.
        mismatch_updates = (
            "source_key_data = jsonb_build_object('id', '101'), "
            "source_key_type = 'TEXT', source_key_numeric = NULL, "
            "source_key_text = '101'",
            f"source_payload_hash = repeat('f', 64)",
            "source_row = jsonb_build_object("
            "'id', 101, 't_id', 'wrong', 'active', true, "
            "'start', '2026-08-22T00:00:00Z')",
            "is_deleted = false",
            "source_schema_profile_id = 'dom_appoint:v2:other'",
            "source_field_types = jsonb_build_object('id', 'NUMERIC')",
            "source_position_v2 = jsonb_build_object('position', 999)",
            "record_id_type = 'text', record_id_text = 'record-3'",
            "source_timestamp_v2 = '2026-08-23T00:00:00+00'::timestamptz",
            "last_version_kind = 'SNAPSHOT_DIFF'",
            "source_row_revision = 2, last_offset = 2, "
            "source_position_v2 = jsonb_build_object('position', 2), "
            "source_timestamp_v2 = '2026-08-22T00:02:00+00'::timestamptz, "
            "source_payload_hash = repeat('b', 64), is_deleted = false",
        )
        for update_clause in mismatch_updates:
            _expect_transaction_failure(
                engine,
                f"""
                UPDATE public.dts_source_rows
                SET {update_clause}
                WHERE source_region = 'dom'
                  AND source_table = 'dom_appoint'
                  AND source_key = '{SOURCE_KEY}'
                """,
            )

        # A current row cannot borrow another key's valid event identity.
        _expect_transaction_failure(
            engine,
            f"""
            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version, source_row_revision,
                last_source_partition_epoch_id, last_version_kind,
                source_position_v2, record_id_type, record_id_numeric,
                record_id_text, source_timestamp_v2, source_payload_hash,
                provenance_state, source_key_type, source_key_numeric,
                source_key_text, source_schema_profile_id, source_field_types
            ) VALUES (
                'dom', 'dom_appoint', '303', jsonb_build_object('id', 303),
                '{{}}'::jsonb, jsonb_build_object('id', 303), true, 3, 3, '3',
                '{TOPIC}', 0, 3, 1, 3, '{EPOCH_ID}', 'CDC',
                jsonb_build_object('position', 3), 'none', NULL, NULL,
                '2026-08-22T00:03:00+00'::timestamptz, repeat('c', 64),
                'V2_CONFIRMED', 'NUMERIC', 303, NULL,
                'dom_appoint:v2:test',
                jsonb_build_object(
                    'active', 'BOOLEAN', 'id', 'NUMERIC',
                    'start', 'TEMPORAL', 't_id', 'TEXT'
                )
            )
            """,
        )

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing v2 source-current guard downgrade: shadow data exists",
            "downgrade",
            "20260822_68_course_part_guards",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        public.source_course_participations,
                        public.source_courses,
                        public.dts_source_rows,
                        public.dts_source_row_versions,
                        public.dts_source_partition_epochs
                    CASCADE
                    """
                )
            )
        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_68_course_part_guards",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260822_68_course_part_guards"
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgname IN (
                        'ct_dts_source_version_current_pair_guard',
                        'ct_dts_source_current_version_pair_guard'
                    )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint
                    WHERE conname IN (
                        'ck_dts_source_row_version_field_type_values',
                        'ck_dts_source_row_v2_transition_shape',
                        'fk_dts_source_row_current_version_event'
                    )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM unnest(ARRAY[
                        'public.dts_v2_source_field_types_valid(jsonb)',
                        'public.dts_v2_source_row_transition_valid(text,jsonb,bigint,text,text,jsonb,text,numeric,text,timestamptz,text,text,text,numeric,text,text,jsonb)',
                        'public.dts_v2_assert_source_current_pair(text,text,text)',
                        'public.dts_v2_source_current_pair_guard()'
                    ]) AS functions(function_name)
                    WHERE to_regprocedure(function_name) IS NOT NULL
                    """
                )
            ).scalar_one() == 0
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
