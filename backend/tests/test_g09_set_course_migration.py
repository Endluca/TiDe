from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from app.task_catalog import MANDATORY_TASKS, TASK_COPY


PUBLIC_63_G09_COPY = {
    "ops_name_zh": "SET 教学基础",
    "title": "SET Teaching Fundamentals",
    "why_template": "Learn the fundamentals of SET teaching.",
    "how_summary": (
        "Watch the in-platform Mock video slot and complete the five-question "
        "Mock check."
    ),
    "completion_standard": (
        "The Mock video is watched in full and the five-question check reaches "
        "80%."
    ),
    "benefit": "You understand the SET teaching foundation.",
}


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260819_65_g09_set_course.py"
    )
    spec = importlib.util.spec_from_file_location(
        "g09_set_course_v65",
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
    payload = {
        "template_id": "G09",
        "category": "MANDATORY_GROWTH",
        "score_type": "FIXED",
        "score_value": 5,
        "content_status": "READY",
        "unrelated_field": {"preserved": True},
        **PUBLIC_63_G09_COPY,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G10:v1','G09',1,'PUBLISHED',17,:payload,"
            "'BEFORE','2026-08-18T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G09:v1','G08',1,'PUBLISHED',12,:payload,"
            "'BEFORE','2026-08-18T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G08",
                    "category": "MANDATORY_GROWTH",
                    "score_type": "FIXED",
                    "score_value": 5,
                    "content_status": "READY",
                    "title": "Global Communicator Training",
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,task_code,status,row_version) VALUES "
            "('ASSIGN-G09','G09','IN_PROGRESS',8)"
        )
    )


def _payload(connection, row_id: str) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id=:row_id"),
        {"row_id": row_id},
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v65_updates_only_g09_copy_and_preserves_facts(tmp_path: Path) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260819_64_g05_g08_courses"
    assert migration.OLD_COPY == PUBLIC_63_G09_COPY
    task = next(item for item in MANDATORY_TASKS if item[0] == "G09")
    why, how, standard, benefit = TASK_COPY["G09"]
    assert migration.NEW_COPY == {
        "ops_name_zh": task[1],
        "title": task[2],
        "why_template": why,
        "how_summary": how,
        "completion_standard": standard,
        "benefit": benefit,
    }
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'g09-set-v65.db'}")

    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)

        with Operations.context(context):
            migration.upgrade()

        payload = _payload(connection, "G10:v1")
        assert {field: payload[field] for field in migration.NEW_COPY} == (
            migration.NEW_COPY
        )
        assert payload["unrelated_field"] == {"preserved": True}
        assert payload["score_value"] == 5
        assert payload["content_status"] == "READY"
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G10:v1'")
        ).scalar_one() == 18
        assert _payload(connection, "G09:v1")["title"] == (
            "Global Communicator Training"
        )
        assert connection.execute(
            text(
                "SELECT task_code,status,row_version FROM task_assignments "
                "WHERE assignment_id='ASSIGN-G09'"
            )
        ).one() == ("G09", "IN_PROGRESS", 8)

        with Operations.context(context):
            migration.downgrade()

        payload = _payload(connection, "G10:v1")
        assert {field: payload[field] for field in migration.OLD_COPY} == (
            migration.OLD_COPY
        )
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G10:v1'")
        ).scalar_one() == 17


def test_v65_fails_closed_before_write_on_copy_or_identity_drift(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g09-set-drift-v65.db'}"
    )

    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        drifted = {
            **_payload(connection, "G10:v1"),
            "how_summary": "Unreviewed replacement",
        }
        connection.execute(
            text(
                "UPDATE task_templates SET payload=:payload "
                "WHERE row_id='G10:v1'"
            ),
            {"payload": json.dumps(drifted)},
        )

        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G10:v1'")
        ).scalar_one() == 17

        restored = {
            **_payload(connection, "G10:v1"),
            **migration.OLD_COPY,
        }
        connection.execute(
            text(
                "UPDATE task_templates SET payload=:payload,template_id='WRONG' "
                "WHERE row_id='G10:v1'"
            ),
            {"payload": json.dumps(restored)},
        )
        with pytest.raises(RuntimeError, match="stable published"):
            with Operations.context(context):
                migration.upgrade()
