from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260729_33_notification_copy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "notification_english_copy_v33",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _stored_payloads(connection) -> dict[str, dict]:
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


def test_v33_migrates_notification_copy_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260728_32_mandatory_task_copy"

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'notification-copy-v33.db'}"
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
                "title": "课中质量问题",
                "body": "该课程检测到：网络延迟过高。请检查并改善上课环境。",
                "evidence": {"lesson_id": "L1", "raw_label": "网络延迟过高"},
            },
            "N2": {
                "title": "课中质量问题",
                "body": "该课程检测到：未开摄像头、CPU 占用过高。请检查并改善上课环境。",
                "evidence": {"lesson_id": "L2"},
            },
            "N3": {
                "title": "课中质量问题",
                "body": "该课程收到网络设备类投诉：网络卡顿。",
                "evidence": {"lesson_id": "L3"},
            },
            "N4": {
                "title": "In-Class Quality Alert",
                "body": "Existing English body.",
                "evidence": {"lesson_id": "L4"},
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

        migrated = _stored_payloads(connection)
        assert migrated["N1"]["body"] == (
            "This lesson had an in-class quality issue: high network delay "
            "was detected. Check and improve the class setup."
        )
        assert migrated["N2"]["body"] == (
            "This lesson had an in-class quality issue: the camera was off, "
            "high CPU usage was detected. Check and improve the class setup."
        )
        assert migrated["N3"]["body"] == (
            "This lesson received a network or device complaint. "
            "Review the evidence and improve the class setup."
        )
        assert migrated["N4"]["body"] == "Existing English body."
        assert migrated["N1"]["evidence"] == fixtures["N1"]["evidence"]
        assert all(
            not _HAN_CHARACTER.search(payload["title"])
            and not _HAN_CHARACTER.search(payload["body"])
            for payload in migrated.values()
        )

        with Operations.context(context):
            migration.upgrade()
        assert _stored_payloads(connection) == migrated

        with Operations.context(context):
            migration.downgrade()
        assert _stored_payloads(connection) == migrated
