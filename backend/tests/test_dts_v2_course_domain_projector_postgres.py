from __future__ import annotations

from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.dts_v2_course_domain_projector import (
    _read_label_aggregate_state,
    _upsert_label,
)
from app.dts_v2_source_repository import DtsV2CurrentSourceRow
from test_dts_v2_domain_facts_postgres import (
    POSTGRES_BINARIES,
    REVISION_81,
    _run_alembic,
    _seed_revision_80_shape,
)


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _position(*, record_id: str, offset: int) -> dict[str, Any]:
    return {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "numeric",
        "record_id": record_id,
        "source_partition_epoch_id": "epoch-course-domain-pg",
        "topic": "topic-course-domain-pg",
        "partition_id": 0,
        "offset_value": offset,
    }


def _label_row(
    source_key: int,
    *,
    appoint_id: int,
    label_name: str,
    create_time: str,
    revision: int,
    offset: int,
    is_deleted: bool = False,
) -> DtsV2CurrentSourceRow:
    source_key_text = str(source_key)
    return DtsV2CurrentSourceRow.from_database_row(
        {
            "source_region": "dom",
            "source_table": "dom_grading_label_log",
            "source_key": source_key_text,
            "source_key_type": "NUMERIC",
            "source_row_revision": revision,
            "source_row": {
                "id": source_key,
                "appoint_id": appoint_id,
                "label_id": 5,
                "label_name": label_name,
                "type": 999,
                "status": "not-a-filter",
                "create_time": create_time,
                "dt": create_time,
            },
            "source_field_types": {
                "id": "NUMERIC",
                "appoint_id": "NUMERIC",
                "label_id": "NUMERIC",
                "label_name": "TEXT",
                "type": "NUMERIC",
                "status": "TEXT",
                "create_time": "TEMPORAL",
                "dt": "TEMPORAL",
            },
            "source_position_v2": _position(
                record_id=source_key_text,
                offset=offset,
            ),
            "source_payload_hash": f"{offset:064x}",
            "is_deleted": is_deleted,
            "provenance_state": "V2_CONFIRMED",
        }
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for label current-set test",
)
def test_label_delete_replay_and_course_move_restore_current_set(
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
            _seed_revision_80_shape(connection)
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_81)

        older = _label_row(
            10,
            appoint_id=100,
            label_name="旧名称",
            create_time="2026-08-22T10:00:00+08:00",
            revision=1,
            offset=10,
        )
        newer = _label_row(
            11,
            appoint_id=100,
            label_name="新名称",
            create_time="2026-08-22T11:00:00+08:00",
            revision=1,
            offset=11,
        )
        deleted_newer = _label_row(
            11,
            appoint_id=100,
            label_name="新名称",
            create_time="2026-08-22T11:00:00+08:00",
            revision=2,
            offset=12,
            is_deleted=True,
        )
        moved_older = _label_row(
            10,
            appoint_id=101,
            label_name="旧名称",
            create_time="2026-08-22T10:00:00+08:00",
            revision=2,
            offset=13,
        )

        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO public.source_courses VALUES ('dom','101')")
            )
            assert _upsert_label(
                connection,
                source=older,
                source_appoint_id="100",
                label_id="5",
                label_id_type="NUMERIC",
            )
            assert _upsert_label(
                connection,
                source=newer,
                source_appoint_id="100",
                label_id="5",
                label_id_type="NUMERIC",
            )
            assert _read_label_aggregate_state(
                connection,
                source_region="dom",
                label_id="5",
            ) == {
                "course_labels": [
                    {
                        "source_appoint_id": "100",
                        "label_id_type": "NUMERIC",
                        "label_name_snapshot": "新名称",
                    }
                ]
            }

            assert _upsert_label(
                connection,
                source=deleted_newer,
                source_appoint_id="100",
                label_id="5",
                label_id_type="NUMERIC",
            )
            assert _read_label_aggregate_state(
                connection,
                source_region="dom",
                label_id="5",
            )["course_labels"][0]["label_name_snapshot"] == "旧名称"
            assert not _upsert_label(
                connection,
                source=deleted_newer,
                source_appoint_id="100",
                label_id="5",
                label_id_type="NUMERIC",
            )

            assert _upsert_label(
                connection,
                source=moved_older,
                source_appoint_id="101",
                label_id="5",
                label_id_type="NUMERIC",
            )
            assert _read_label_aggregate_state(
                connection,
                source_region="dom",
                label_id="5",
            ) == {
                "course_labels": [
                    {
                        "source_appoint_id": "101",
                        "label_id_type": "NUMERIC",
                        "label_name_snapshot": "旧名称",
                    }
                ]
            }
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
