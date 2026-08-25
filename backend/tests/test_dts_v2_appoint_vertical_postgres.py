from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConsumerSettings,
    protect_domestic_student_ids,
)
from app.dts_source_contract_v2 import with_v2_source_image_completeness
from app.dts_v2_course_projector import (
    DtsV2CourseProjector,
    DtsV2CourseProjectorError,
    _read_source_current,
)
from app.dts_v2_shadow_source_writer import (
    DtsV2ShadowSourceWriter,
    DtsV2ShadowSourceWriterError,
)
from test_dts_v2_shadow_source_writer_postgres import (
    ACTIVE_EPOCH,
    POSTGRES_BINARIES,
    _install_scope_v3_stubs,
    TOPIC,
    _install_revision_65_fixture,
    _run_alembic,
    _seed_epochs,
)
from dts_v2_test_profiles import SYNTHETIC_APPOINT_SOURCE_FIELDS


pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


@pytest.fixture
def v2_postgres_engine(tmp_path: Path):
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
            "20260822_69_dts_v2_source_guard",
        )
        with engine.begin() as connection:
            # The projector follows the current rev89 evidence contract while
            # this focused fixture deliberately stops at rev69.  Add only the
            # two later evidence inputs needed to exercise the older course
            # state machine in isolation.
            connection.execute(
                text(
                    """
                    ALTER TABLE public.source_courses
                      ADD COLUMN appoint_evidence_status varchar(32)
                        NOT NULL DEFAULT 'SOURCE_MISSING',
                      ADD COLUMN teacher_region_evidence_status varchar(32)
                        NOT NULL DEFAULT 'SOURCE_MISSING'
                    """
                )
            )
            _install_scope_v3_stubs(connection)
            _seed_epochs(connection)
        yield engine
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


def _settings() -> DtsConsumerSettings:
    return DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.invalid:9092",),
        topic=TOPIC,
        group_id="dom-v2-course-projector-test",
        account="test-account",
        password="test-password",
        execution_region="cn",
        domestic_student_hmac_key="22" * 32,
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="PostgreSQL server binaries are unavailable",
)
def test_source_current_is_readable_by_select_only_domain_role(
    v2_postgres_engine,
) -> None:
    with v2_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "GRANT SELECT ON TABLE public.dts_source_rows "
                "TO tit_growth_app"
            )
        )
        connection.execute(
            text(
                "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER ON TABLE "
                "public.dts_source_rows FROM tit_growth_app"
            )
        )
        connection.execute(text("SET LOCAL ROLE tit_growth_app"))

        assert connection.execute(
            text(
                "SELECT has_table_privilege(current_user,"
                "'public.dts_source_rows','UPDATE')"
            )
        ).scalar_one() is False
        assert _read_source_current(
            connection,
            source_region="dom",
            source_table="dom_appoint",
            source_appoint_id="999999999",
        ) is None


def _complete_row(
    appoint_id: int,
    teacher_id: int,
    *,
    status: str = "on",
    end_time: str | None = None,
) -> dict[str, Any]:
    row = {
        field_name: None
        for field_name in SYNTHETIC_APPOINT_SOURCE_FIELDS
    }
    row.update(
        {
            "id": appoint_id,
            "t_id": teacher_id,
            "s_id": f"student-{appoint_id}",
            "date": "2026-08-23",
            "time": "10:00:00",
            "end_time": end_time,
            "week": 6,
            "status": status,
            "use_point": "buy",
        }
    )
    return row


def _complete_event(
    *,
    operation: str,
    offset: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="dom",
        topic=TOPIC,
        partition=0,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=1_787_600_000 + offset,
        source_txid=f"tx-{offset}",
        source_position=f"opaque-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="dom_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": "NUMERIC",
            "t_id": "NUMERIC",
            "s_id": "TEXT",
            "date": "TEMPORAL",
            "time": "TEMPORAL",
            "end_time": "TEMPORAL",
            "week": "NUMERIC",
            "status": "TEXT",
            "use_point": "TEXT",
        },
    )
    event = with_v2_source_image_completeness(event)
    event = protect_domestic_student_ids(event, _settings())
    assert event.source_images_complete is True
    return event


def _sparse_event(
    *,
    offset: int,
    before: dict[str, Any],
    after: dict[str, Any],
    source_field_types: dict[str, str],
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="dom",
        topic=TOPIC,
        partition=0,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=1_787_600_000 + offset,
        source_txid=f"tx-{offset}",
        source_position=f"opaque-{offset}",
        operation="UPDATE",
        database_name="source",
        schema_name="public",
        table_name="dom_appoint",
        before=before,
        after=after,
        source_field_types=source_field_types,
    )
    event = with_v2_source_image_completeness(event)
    assert event.source_images_complete is False
    assert event.source_image_profile_id is None
    return protect_domestic_student_ids(event, _settings())


def _course(
    connection,
    appoint_id: str,
    *,
    source_region: str = "dom",
) -> dict[str, Any]:
    return dict(
        connection.execute(
            text(
                """
                SELECT source_status, raw_end_time, end_time,
                       completion_end_time, current_teacher_id,
                       current_participation_seq, completion_teacher_id,
                       completion_participation_seq,
                       completion_conflict_status, evidence_status,
                       source_is_deleted,
                       initial_completion_snapshot,
                       last_applied_source_revision, row_version
                FROM public.source_courses
                WHERE source_region = :source_region
                  AND source_appoint_id = :appoint_id
                """
            ),
            {
                "source_region": source_region,
                "appoint_id": appoint_id,
            },
        ).mappings().one()
    )


def _participations(
    connection,
    appoint_id: str,
    *,
    source_region: str = "dom",
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT participation_seq, teacher_id, participation_status,
                       participation_role, is_current, ended_at, row_version
                FROM public.source_course_participations
                WHERE source_region = :source_region
                  AND source_appoint_id = :appoint_id
                ORDER BY participation_seq
                """
            ),
            {
                "source_region": source_region,
                "appoint_id": appoint_id,
            },
        ).mappings()
    ]


def _ovs_event(
    *,
    operation: str,
    offset: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="ovs",
        topic=TOPIC,
        partition=0,
        offset=offset,
        record_id=20_000 + offset,
        source_timestamp=1_787_700_000 + offset,
        source_txid=f"ovs-tx-{offset}",
        source_position=f"ovs-opaque-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="ovs_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": "NUMERIC",
            "t_id": "NUMERIC",
            "s_id": "TEXT",
            "date": "TEMPORAL",
            "time": "TEMPORAL",
            "end_time": "TEMPORAL",
            "week": "NUMERIC",
            "status": "TEXT",
            "use_point": "TEXT",
        },
    )
    completed = with_v2_source_image_completeness(event)
    assert completed.source_images_complete is True
    return completed


def _ovs_complete_row(
    appoint_id: int,
    teacher_id: int,
    *,
    status: str = "on",
    end_time: str | None = None,
) -> dict[str, Any]:
    row = {
        field_name: None
        for field_name in SYNTHETIC_APPOINT_SOURCE_FIELDS
    }
    row.update(
        {
            "id": appoint_id,
            "t_id": teacher_id,
            "s_id": f"ovs-student-{appoint_id}",
            "date": "2026-08-23",
            "time": "11:00:00",
            "end_time": end_time,
            "week": 6,
            "status": status,
            "use_point": "buy",
        }
    )
    return row


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for v2 vertical checks",
)
def test_sparse_appoint_versions_project_one_step_and_freeze_first_end(
    v2_postgres_engine,
) -> None:
    engine = v2_postgres_engine
    writer = DtsV2ShadowSourceWriter(enabled=True)
    projector = DtsV2CourseProjector(enabled=True)
    row_a = _complete_row(5001, 10)
    events = [
        _complete_event(
            operation="INSERT",
            offset=1,
            before=None,
            after=row_a,
        ),
        _sparse_event(
            offset=2,
            before={"id": 5001, "t_id": 10},
            after={"id": 5001, "t_id": 20},
            source_field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
        ),
        _sparse_event(
            offset=3,
            before={"id": 5001, "t_id": 20},
            after={"id": 5001, "t_id": 10},
            source_field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
        ),
        _sparse_event(
            offset=4,
            before={
                "id": 5001,
                "t_id": 10,
                "status": "on",
                "end_time": None,
            },
            after={
                "id": 5001,
                "t_id": 10,
                "status": "end",
                "end_time": "2026-08-23 10:30:00",
            },
            source_field_types={
                "id": "NUMERIC",
                "t_id": "NUMERIC",
                "status": "TEXT",
                "end_time": "TEMPORAL",
            },
        ),
        _sparse_event(
            offset=5,
            before={"id": 5001, "t_id": 10},
            after={"id": 5001, "t_id": 30},
            source_field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
        ),
    ]

    with engine.begin() as connection:
        ignored = writer.apply_appoint_cdc(
            connection,
            _sparse_event(
                offset=80,
                before={"id": 8001, "t_id": 80},
                after={"id": 8001, "t_id": 81},
                source_field_types={
                    "id": "NUMERIC",
                    "t_id": "NUMERIC",
                },
            ),
            ACTIVE_EPOCH,
        )
        assert (ignored.status, ignored.source_row_revision) == (
            "IGNORED_MISSING_CURRENT",
            None,
        )
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.dts_source_rows
                WHERE source_region='dom' AND source_table='dom_appoint'
                  AND source_key='8001'
                """
            )
        ).scalar_one() == 0

    for revision, event in enumerate(events, start=1):
        with engine.begin() as connection:
            result = writer.apply_appoint_cdc(
                connection,
                event,
                ACTIVE_EPOCH,
            )
            assert (result.status, result.source_row_revision) == (
                "APPLIED",
                revision,
            )

    # Historical sparse replay is checked against its immutable version, not
    # incorrectly rebuilt from the later C/end current row.
    with engine.begin() as connection:
        replay = writer.apply_appoint_cdc(
            connection,
            events[1],
            ACTIVE_EPOCH,
        )
        assert (replay.status, replay.source_row_revision) == ("REPLAYED", 2)

    with engine.begin() as connection:
        with pytest.raises(
            DtsV2CourseProjectorError,
            match="DTS_V2_COURSE_PROJECTOR_DISABLED",
        ):
            DtsV2CourseProjector().project_next(
                connection,
                source_region="dom",
                source_appoint_id="5001",
            )

    expected_states = [
        [(1, "10", "on", "NORMAL", True)],
        [
            (1, "10", "t_absent", "NORMAL", False),
            (2, "20", "on", "NORMAL", True),
        ],
        [
            (1, "10", "t_absent", "NORMAL", False),
            (2, "20", "t_absent", "NORMAL", False),
            (3, "10", "on", "NORMAL", True),
        ],
        [
            (1, "10", "t_absent", "NORMAL", False),
            (2, "20", "t_absent", "NORMAL", False),
            (3, "10", "end", "COMPLETION", True),
        ],
        [
            (1, "10", "t_absent", "NORMAL", False),
            (2, "20", "t_absent", "NORMAL", False),
            (3, "10", "end", "COMPLETION", False),
            (4, "30", "end", "PENDING_CORRECTION", True),
        ],
    ]
    for revision, expected in enumerate(expected_states, start=1):
        with engine.begin() as connection:
            projected = projector.project_next(
                connection,
                source_region="dom",
                source_appoint_id="5001",
            )
            assert (projected.status, projected.source_row_revision) == (
                "APPLIED",
                revision,
            )
        with engine.connect() as connection:
            course = _course(connection, "5001")
            rows = _participations(connection, "5001")
            assert [
                (
                    row["participation_seq"],
                    row["teacher_id"],
                    row["participation_status"],
                    row["participation_role"],
                    row["is_current"],
                )
                for row in rows
            ] == expected
            assert course["last_applied_source_revision"] == revision

    with engine.connect() as connection:
        final_course = _course(connection, "5001")
        assert (
            final_course["current_teacher_id"],
            final_course["current_participation_seq"],
            final_course["completion_teacher_id"],
            final_course["completion_participation_seq"],
            final_course["completion_conflict_status"],
            final_course["evidence_status"],
        ) == ("30", 4, "10", 3, "PENDING", "SOURCE_CONFLICT")
        assert final_course["raw_end_time"] == "2026-08-23 10:30:00"
        assert final_course["end_time"] is None
        assert final_course["completion_end_time"] is None
        assert final_course["initial_completion_snapshot"]["teacher_id"] == "10"
        before_replay_versions = (
            final_course["row_version"],
            [row["row_version"] for row in _participations(connection, "5001")],
        )

    with engine.begin() as connection:
        replay = projector.project_next(
            connection,
            source_region="dom",
            source_appoint_id="5001",
        )
        assert (replay.status, replay.source_row_revision) == ("REPLAYED", 5)
    with engine.connect() as connection:
        assert (
            _course(connection, "5001")["row_version"],
            [row["row_version"] for row in _participations(connection, "5001")],
        ) == before_replay_versions
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.dts_ingest_events
                UNION ALL SELECT count(*) FROM public.dts_ingest_checkpoints
                UNION ALL SELECT count(*) FROM public.dts_dirty_keys
                UNION ALL SELECT count(*) FROM public.outbox_events
                """
            )
        ).scalars().all() == [0, 0, 0, 0]


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for v2 vertical checks",
)
def test_ovs_substitution_end_freeze_post_end_conflict_and_delete(
    v2_postgres_engine,
) -> None:
    engine = v2_postgres_engine
    writer = DtsV2ShadowSourceWriter(enabled=True)
    projector = DtsV2CourseProjector(enabled=True)
    row_a = _ovs_complete_row(5101, 110)
    row_b = _ovs_complete_row(5101, 120)
    row_b_end = _ovs_complete_row(
        5101,
        120,
        status="end",
        end_time="2026-08-23 11:30:00",
    )
    row_c_end = _ovs_complete_row(
        5101,
        130,
        status="end",
        end_time="2026-08-23 11:30:00",
    )
    events = (
        _ovs_event(operation="INSERT", offset=11, before=None, after=row_a),
        _ovs_event(operation="UPDATE", offset=12, before=row_a, after=row_b),
        _ovs_event(
            operation="UPDATE",
            offset=13,
            before=row_b,
            after=row_b_end,
        ),
        _ovs_event(
            operation="UPDATE",
            offset=14,
            before=row_b_end,
            after=row_c_end,
        ),
        _ovs_event(
            operation="DELETE",
            offset=15,
            before=row_c_end,
            after=None,
        ),
    )

    for revision, event in enumerate(events, start=1):
        with engine.begin() as connection:
            written = writer.apply_appoint_cdc(
                connection,
                event,
                ACTIVE_EPOCH,
            )
            assert written.source_row_revision == revision
        with engine.begin() as connection:
            projected = projector.project_next(
                connection,
                source_region="ovs",
                source_appoint_id="5101",
            )
            assert projected.source_row_revision == revision

    with engine.connect() as connection:
        course = _course(connection, "5101", source_region="ovs")
        rows = _participations(
            connection,
            "5101",
            source_region="ovs",
        )

    assert (
        course["completion_teacher_id"],
        course["completion_participation_seq"],
        course["completion_conflict_status"],
        course["source_is_deleted"],
    ) == ("120", 2, "PENDING", True)
    assert [
        (
            row["teacher_id"],
            row["participation_status"],
            row["participation_role"],
            row["is_current"],
        )
        for row in rows
    ] == [
        ("110", "t_absent", "NORMAL", False),
        ("120", "end", "COMPLETION", False),
        ("130", "end", "PENDING_CORRECTION", False),
    ]


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for v2 vertical checks",
)
def test_projector_transaction_rollback_and_database_rejects_revision_gap(
    v2_postgres_engine,
) -> None:
    engine = v2_postgres_engine
    writer = DtsV2ShadowSourceWriter(enabled=True)
    projector = DtsV2CourseProjector(enabled=True)

    rollback_event = _complete_event(
        operation="INSERT",
        offset=101,
        before=None,
        after=_complete_row(6001, 60),
    )
    gap_event = _complete_event(
        operation="INSERT",
        offset=201,
        before=None,
        after=_complete_row(7001, 70),
    )
    with engine.begin() as connection:
        writer.apply_appoint_cdc(connection, rollback_event, ACTIVE_EPOCH)
        writer.apply_appoint_cdc(connection, gap_event, ACTIVE_EPOCH)

    with pytest.raises(RuntimeError, match="force caller rollback"):
        with engine.begin() as connection:
            projector.project_next(
                connection,
                source_region="dom",
                source_appoint_id="6001",
            )
            raise RuntimeError("force caller rollback")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.source_courses
                WHERE source_region = 'dom' AND source_appoint_id = '6001'
                """
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.source_course_participations
                WHERE source_region = 'dom' AND source_appoint_id = '6001'
                """
            )
        ).scalar_one() == 0

    with engine.begin() as connection:
        applied = projector.project_next(
            connection,
            source_region="dom",
            source_appoint_id="6001",
        )
        assert (applied.status, applied.source_row_revision) == ("APPLIED", 1)

    with engine.connect() as connection:
        gap_transaction = connection.begin()
        current = connection.execute(
            text(
                """
                SELECT source_row, source_field_types,
                       source_schema_profile_id, source_key_data,
                       source_key_type, source_key_numeric, source_key_text
                FROM public.dts_source_rows
                WHERE source_region = 'dom'
                  AND source_table = 'dom_appoint'
                  AND source_key = '7001'
                FOR UPDATE
                """
            )
        ).mappings().one()
        source_timestamp_seconds = 1_787_600_202
        source_timestamp = datetime.fromtimestamp(
            source_timestamp_seconds,
            timezone.utc,
        )
        position = {
            "v": 1,
            "source_timestamp": source_timestamp.strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "record_id_type": "numeric",
            "record_id": "10202",
            "source_partition_epoch_id": ACTIVE_EPOCH,
            "topic": TOPIC,
            "partition_id": 0,
            "offset_value": 202,
        }
        payload = {
            "source_region": "dom",
            "source_table": "dom_appoint",
            "source_key_type": current["source_key_type"],
            "source_key": "7001",
            "source_schema_profile_id": current[
                "source_schema_profile_id"
            ],
            "source_field_types": current["source_field_types"],
            "operation": "UPDATE",
            "before_row": current["source_row"],
            "after_row": current["source_row"],
        }
        payload_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        parameters = {
            "epoch_id": ACTIVE_EPOCH,
            "topic": TOPIC,
            "source_timestamp": source_timestamp,
            "source_timestamp_seconds": source_timestamp_seconds,
            "position": json.dumps(position, separators=(",", ":")),
            "legacy_position": json.dumps(position, separators=(",", ":")),
            "source_row": json.dumps(
                current["source_row"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "source_field_types": json.dumps(
                current["source_field_types"],
                sort_keys=True,
                separators=(",", ":"),
            ),
            "source_schema_profile_id": current[
                "source_schema_profile_id"
            ],
            "source_key_data": json.dumps(
                current["source_key_data"],
                separators=(",", ":"),
            ),
            "source_key_numeric": Decimal("7001"),
            "payload_hash": payload_hash,
        }
        connection.execute(
            text(
                """
                INSERT INTO public.dts_source_row_versions (
                    source_region, source_partition_epoch_id, topic,
                    partition_id, offset_value, version_kind, source_table,
                    source_schema_profile_id, source_field_types, source_key,
                    source_key_data, source_key_type, source_key_numeric,
                    source_key_text, operation, before_row, after_row,
                    source_timestamp, record_id_type, record_id_numeric,
                    record_id_text, source_position, source_row_revision,
                    snapshot_id, snapshot_as_of, covered_through_offsets,
                    diff_step, source_table_publish_generation,
                    protected_source_row_hash
                ) VALUES (
                    'dom', :epoch_id, :topic, 0, 202, 'CDC', 'dom_appoint',
                    :source_schema_profile_id,
                    CAST(:source_field_types AS jsonb), '7001',
                    CAST(:source_key_data AS jsonb), 'NUMERIC',
                    :source_key_numeric, NULL, 'UPDATE',
                    CAST(:source_row AS jsonb), CAST(:source_row AS jsonb),
                    :source_timestamp, 'numeric', 10202, NULL,
                    CAST(:position AS jsonb), 3,
                    NULL, NULL, NULL, NULL, NULL, :payload_hash
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                UPDATE public.dts_source_rows
                SET source_timestamp = :source_timestamp_seconds,
                    last_record_id = 10202,
                    source_position = :legacy_position,
                    last_topic = :topic,
                    last_partition = 0,
                    last_offset = 202,
                    row_version = row_version + 1,
                    source_row_revision = 3,
                    last_source_partition_epoch_id = :epoch_id,
                    last_version_kind = 'CDC',
                    source_position_v2 = CAST(:position AS jsonb),
                    record_id_type = 'numeric',
                    record_id_numeric = 10202,
                    record_id_text = NULL,
                    source_timestamp_v2 = :source_timestamp,
                    source_payload_hash = :payload_hash,
                    provenance_state = 'V2_CONFIRMED',
                    source_field_types = CAST(:source_field_types AS jsonb),
                    updated_at = clock_timestamp()
                WHERE source_region = 'dom'
                  AND source_table = 'dom_appoint'
                  AND source_key = '7001'
                """
            ),
            parameters,
        )
        with pytest.raises(
            DBAPIError,
            match="DTS_V2_SOURCE_REVISION_GAP",
        ):
            gap_transaction.commit()

    with engine.begin() as connection:
        projected = projector.project_next(
            connection,
            source_region="dom",
            source_appoint_id="7001",
        )
        assert (projected.status, projected.source_row_revision) == (
            "APPLIED",
            1,
        )
    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.source_courses
                WHERE source_region = 'dom' AND source_appoint_id = '7001'
                """
            )
        ).scalar_one() == 1
