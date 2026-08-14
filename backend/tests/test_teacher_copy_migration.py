from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260814_61_teacher_copy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "teacher_copy_v61",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _prepare_database(connection, migration) -> None:
    connection.execute(
        text(
            "CREATE TABLE task_templates ("
            "row_id VARCHAR(160) PRIMARY KEY, template_id VARCHAR(64) NOT NULL, "
            "template_version INTEGER NOT NULL, status VARCHAR(24) NOT NULL, "
            "revision INTEGER NOT NULL, payload JSON NOT NULL, "
            "updated_by VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE task_assignments ("
            "assignment_id VARCHAR(160) PRIMARY KEY, task_code VARCHAR(64) NOT NULL, "
            "status VARCHAR(24) NOT NULL, row_version INTEGER NOT NULL)"
        )
    )

    for index, (row_id, target) in enumerate(migration.TARGETS.items(), start=1):
        payload = {
            "template_id": target["template_id"],
            "category": target["category"],
            "score_type": target["score_type"],
            "score_value": target["score_value"],
            "unrelated_field": {"preserved": row_id},
            **target["old_copy"],
        }
        connection.execute(
            text(
                "INSERT INTO task_templates("
                "row_id,template_id,template_version,status,revision,payload,"
                "updated_by,updated_at) VALUES ("
                ":row_id,:template_id,1,'PUBLISHED',:revision,:payload,"
                "'BEFORE','2026-08-13T00:00:00Z')"
            ),
            {
                "row_id": row_id,
                "template_id": target["template_id"],
                "revision": 10 + index,
                "payload": json.dumps(payload),
            },
        )

    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G08:v1','G07',1,'PUBLISHED',7,:payload,"
            "'BEFORE','2026-08-13T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G07",
                    "category": "MANDATORY_GROWTH",
                    "score_type": "FIXED",
                    "score_value": 3,
                    "title": "Reliability Training",
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,task_code,status,row_version) VALUES "
            "('ASSIGN-G08','G08','IN_PROGRESS',7),"
            "('ASSIGN-MEMO','P-REL-MEMO','ASSIGNED',3)"
        )
    )


def _payload(connection, row_id: str) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id=:row_id"),
        {"row_id": row_id},
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v61_updates_only_reviewed_copy_and_preserves_facts(tmp_path: Path) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260813_60_dom_privacy"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'teacher-copy-v61.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        before_revisions = dict(
            connection.execute(
                text(
                    "SELECT row_id,revision FROM task_templates "
                    "WHERE row_id IN ('G01:v1','G09:v1','P-REL-MEMO:v1',"
                    "'P-REL-ATTENDANCE:v1')"
                )
            ).all()
        )

        with Operations.context(context):
            migration.upgrade()

        for row_id, target in migration.TARGETS.items():
            payload = _payload(connection, row_id)
            assert {
                field: payload[field] for field in target["new_copy"]
            } == target["new_copy"]
            assert payload["unrelated_field"] == {"preserved": row_id}
            assert connection.execute(
                text(
                    "SELECT revision FROM task_templates WHERE row_id=:row_id"
                ),
                {"row_id": row_id},
            ).scalar_one() == before_revisions[row_id] + 1

        assert _payload(connection, "G08:v1")["title"] == "Reliability Training"
        assert connection.execute(
            text(
                "SELECT task_code,status,row_version FROM task_assignments "
                "ORDER BY assignment_id"
            )
        ).all() == [
            ("G08", "IN_PROGRESS", 7),
            ("P-REL-MEMO", "ASSIGNED", 3),
        ]

        with Operations.context(context):
            migration.downgrade()

        for row_id, target in migration.TARGETS.items():
            payload = _payload(connection, row_id)
            assert {
                field: payload[field] for field in target["old_copy"]
            } == target["old_copy"]
            assert connection.execute(
                text(
                    "SELECT revision FROM task_templates WHERE row_id=:row_id"
                ),
                {"row_id": row_id},
            ).scalar_one() == before_revisions[row_id]


def test_v61_fails_before_any_write_for_drift_or_wrong_identity(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'teacher-copy-drift-v61.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        connection.execute(
            text(
                "UPDATE task_templates SET payload=:payload "
                "WHERE row_id='P-REL-ATTENDANCE:v1'"
            ),
            {
                "payload": json.dumps(
                    {
                        **_payload(connection, "P-REL-ATTENDANCE:v1"),
                        "why_template": "Unreviewed replacement",
                    }
                )
            },
        )

        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()
        assert _payload(connection, "G01:v1")["benefit"] == (
            migration.TARGETS["G01:v1"]["old_copy"]["benefit"]
        )
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G01:v1'")
        ).scalar_one() == 11

        attendance_target = migration.TARGETS["P-REL-ATTENDANCE:v1"]
        restored_payload = {
            **_payload(connection, "P-REL-ATTENDANCE:v1"),
            **attendance_target["old_copy"],
        }
        connection.execute(
            text(
                "UPDATE task_templates SET payload=:payload,template_id='WRONG' "
                "WHERE row_id='P-REL-ATTENDANCE:v1'"
            ),
            {"payload": json.dumps(restored_payload)},
        )
        with pytest.raises(RuntimeError, match="stable published template identity"):
            with Operations.context(context):
                migration.upgrade()
