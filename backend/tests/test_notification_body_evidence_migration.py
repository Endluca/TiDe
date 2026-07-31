from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260729_34_notification_body_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "notification_body_evidence_v34",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _payloads(connection) -> dict[str, dict]:
    return {
        notification_id: (
            json.loads(payload) if isinstance(payload, str) else payload
        )
        for notification_id, payload in connection.execute(
            text(
                "SELECT notification_id,payload "
                "FROM notifications ORDER BY notification_id"
            )
        ).all()
    }


def test_v34_persists_compact_english_evidence_in_body(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260729_33_notification_copy"

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'notification-body-v34.db'}"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE notifications ("
                "notification_id VARCHAR(128) PRIMARY KEY, payload JSON NOT NULL)"
            )
        )
        fixtures = {
            "N1": {
                "title": "In-Class Quality Alert",
                "body": (
                    "This lesson had an in-class quality issue: high network "
                    "delay was detected. Check and improve the class setup."
                ),
                "evidence": {
                    "lesson_id": "529638921",
                    "anomalies": ["网络延迟过高"],
                },
            },
            "N2": {
                "title": "In-Class Quality Alert",
                "body": (
                    "This lesson received a network or device complaint. "
                    "Review the evidence and improve the class setup."
                ),
                "evidence": {
                    "lesson_id": "L2",
                    "complaint_level3": "麦克风没有声音/卡顿",
                },
            },
            "N3": {
                "title": "In-Class Quality Alert",
                "body": "Existing body. Evidence: Lesson IDs: L3.",
                "evidence": {"lesson_id": "L3"},
            },
        }
        for notification_id, payload in fixtures.items():
            connection.execute(
                text(
                    "INSERT INTO notifications(notification_id,payload) "
                    "VALUES (:notification_id,:payload)"
                ),
                {
                    "notification_id": notification_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        migrated = _payloads(connection)
        assert migrated["N1"]["body"].endswith(
            "Evidence: Lesson IDs: 529638921; "
            "quality anomalies: high network delay."
        )
        assert migrated["N2"]["body"].endswith(
            "Evidence: Lesson IDs: L2; "
            "complaint: Microphone Audio Missing or Unstable."
        )
        assert migrated["N3"]["body"] == fixtures["N3"]["body"]
        assert migrated["N1"]["evidence"] == fixtures["N1"]["evidence"]

        with Operations.context(context):
            migration.upgrade()
        assert _payloads(connection) == migrated
