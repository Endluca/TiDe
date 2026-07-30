from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260722_11_teacher_snapshot_profile_fields.py"
    )
    spec = importlib.util.spec_from_file_location(
        "teacher_snapshot_profile_v11", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_v11_adds_nullable_profile_evidence_and_backfills_current_projection(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260722_10_shared_tasks"

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'profile-v11.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE teacher_metric_snapshots ("
                "snapshot_id VARCHAR(192) PRIMARY KEY, batch_id VARCHAR(96) NOT NULL, "
                "teacher_id VARCHAR(64) NOT NULL, raw_payload JSON NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE teachers (teacher_id VARCHAR(64) PRIMARY KEY, "
                "source_batch_id VARCHAR(96), payload JSON NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO teacher_metric_snapshots(snapshot_id,batch_id,teacher_id,raw_payload) "
                "VALUES ('S-1','B-1','T-1',:raw_one),('S-2','B-2','T-2',:raw_two)"
            ),
            {
                "raw_one": json.dumps({"first_booked_dt": "2026-04-03T15:30:00"}),
                "raw_two": json.dumps(
                    {
                        "first_booked_dt": None,
                        "is_cpl_tesol": True,
                        "is_self_introduce": 0,
                    }
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO teachers(teacher_id,source_batch_id,payload) VALUES "
                "('T-1','B-1',:payload_one),('T-2','B-2',:payload_two)"
            ),
            {
                "payload_one": json.dumps({"teacher_id": "T-1"}),
                "payload_two": json.dumps({"teacher_id": "T-2"}),
            },
        )

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        rows = connection.execute(
            text(
                "SELECT snapshot_id,first_booked_date,is_cpl_tesol,is_self_introduce "
                "FROM teacher_metric_snapshots ORDER BY snapshot_id"
            )
        ).mappings().all()
        assert rows[0]["first_booked_date"] == "2026-04-03"
        assert rows[0]["is_cpl_tesol"] is None
        assert rows[0]["is_self_introduce"] is None
        assert rows[1]["first_booked_date"] is None
        assert rows[1]["is_cpl_tesol"] == 1
        assert rows[1]["is_self_introduce"] == 0

        payloads = {
            teacher_id: json.loads(payload) if isinstance(payload, str) else payload
            for teacher_id, payload in connection.execute(
                text("SELECT teacher_id,payload FROM teachers ORDER BY teacher_id")
            ).all()
        }
        assert payloads["T-1"]["first_booked_date"] == "2026-04-03"
        assert payloads["T-1"]["is_cpl_tesol"] is None
        assert payloads["T-1"]["profile_provenance"]["first_booked_date"][
            "source_mode"
        ] == "REAL"
        assert payloads["T-2"]["is_cpl_tesol"] is True
        assert payloads["T-2"]["is_self_introduce"] is False

        columns = {
            item["name"]: item
            for item in inspect(connection).get_columns("teacher_metric_snapshots")
        }
        assert columns["first_booked_date"]["nullable"] is True
        assert columns["is_cpl_tesol"]["nullable"] is True
        assert columns["is_self_introduce"]["nullable"] is True
        assert "ix_teacher_metric_snapshots_first_booked_date" in {
            item["name"]
            for item in inspect(connection).get_indexes("teacher_metric_snapshots")
        }

        with Operations.context(context):
            migration.downgrade()
        downgraded_columns = {
            item["name"]
            for item in inspect(connection).get_columns("teacher_metric_snapshots")
        }
        assert "first_booked_date" not in downgraded_columns
        assert "is_cpl_tesol" not in downgraded_columns
        assert "is_self_introduce" not in downgraded_columns
