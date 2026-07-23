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
        / "20260723_16_mandatory_task_catalog.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mandatory_task_catalog_v16",
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
            "revision INTEGER NOT NULL, integration_mode VARCHAR(32) NOT NULL, "
            "external_task_template_code VARCHAR(128) NOT NULL, payload JSON NOT NULL, "
            "updated_by VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE task_assignments ("
            "assignment_id VARCHAR(160) PRIMARY KEY, task_code VARCHAR(64) NOT NULL, "
            "task_kind VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, "
            "priority VARCHAR(8) NOT NULL, why TEXT NOT NULL, row_version INTEGER NOT NULL, "
            "updated_by VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    connection.execute(
        text("CREATE TABLE score_entries (entry_type VARCHAR(32) NOT NULL)")
    )
    for task_code, definition in migration.OLD_DEFINITIONS.items():
        payload = {
            **definition,
            "template_id": task_code,
            "due_rule": {
                "type": "CAMP_DAY_OR_EVENT_DEADLINE",
                "camp_day": definition["camp_day"],
                "event": definition["event"],
                "fallback_hours": 168,
            },
            "external_task_template_code": f"TIT.{task_code}",
            "score_type": "FIXED",
        }
        connection.execute(
            text(
                "INSERT INTO task_templates("
                "row_id,template_id,template_version,status,revision,integration_mode,"
                "external_task_template_code,payload,updated_by,updated_at"
                ") VALUES (:row_id,:template_id,1,'PUBLISHED',1,'INBOUND_STATUS_ONLY',"
                ":external_code,:payload,'SYSTEM_SEED','2026-07-22T00:00:00Z')"
            ),
            {
                "row_id": f"{task_code}:v1",
                "template_id": task_code,
                "external_code": f"TIT.{task_code}",
                "payload": json.dumps(payload),
            },
        )
        connection.execute(
            text(
                "INSERT INTO task_assignments("
                "assignment_id,task_code,task_kind,status,priority,why,row_version,"
                "updated_by,updated_at"
                ") VALUES (:assignment_id,:task_code,'FIXED_GROWTH','ASSIGNED',"
                ":priority,:why,1,'tit_teacher_crud','2026-07-22T00:00:00Z')"
            ),
            {
                "assignment_id": f"ASSIGN-{task_code}",
                "task_code": task_code,
                "priority": definition["priority"],
                "why": definition["why_template"],
            },
        )


def _payloads(connection) -> dict[str, dict]:
    return {
        task_code: json.loads(payload) if isinstance(payload, str) else payload
        for task_code, payload in connection.execute(
            text(
                "SELECT template_id,payload FROM task_templates "
                "ORDER BY template_id"
            )
        ).all()
    }


def test_v16_replaces_free_trial_and_reorders_unstarted_assignments(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260722_15_runtime_mock_cleanup"

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mandatory-v16.db'}")
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        payloads = _payloads(connection)
        assert {
            code: (
                payload["title"],
                payload["score_value"],
                payload["stage"],
            )
            for code, payload in payloads.items()
        } == {
            "G04": ("How to handle different types of students", 1, "DAY_1_7"),
            "G05": ("Lesson Preparation", 3, "DAY_1_7"),
            "G06": ("TTP Orientation", 1, "DAY_8_14"),
            "G07": ("ME Culture & PARSNIP", 2, "DAY_8_14"),
            "G08": ("Reliability Training", 2, "DAY_8_14"),
        }
        assert "Free Trial" not in json.dumps(payloads)
        assignments = connection.execute(
            text(
                "SELECT task_code,priority,why,row_version "
                "FROM task_assignments ORDER BY task_code"
            )
        ).mappings()
        for assignment in assignments:
            definition = migration.NEW_DEFINITIONS[assignment["task_code"]]
            assert assignment["priority"] == definition["priority"]
            assert assignment["why"] == definition["why_template"]
            assert assignment["row_version"] == 2

        with Operations.context(context):
            migration.downgrade()
        downgraded = _payloads(connection)
        assert downgraded["G04"]["title"] == "Lesson Preparation"
        assert downgraded["G08"]["title"] == "Free Trial Training"
        assert {
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM task_templates")
            ).all()
        } == {1}


def test_v16_refuses_to_relabel_a_started_fixed_assignment(tmp_path: Path) -> None:
    migration = _load_migration()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mandatory-v16-block.db'}")
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        connection.execute(
            text(
                "UPDATE task_assignments SET status='IN_PROGRESS' "
                "WHERE task_code='G04'"
            )
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(
            RuntimeError,
            match="affected assignments have started",
        ):
            migration.upgrade()
        assert _payloads(connection)["G04"]["title"] == "Lesson Preparation"


def test_v16_refuses_to_relabel_after_fixed_task_scoring(tmp_path: Path) -> None:
    migration = _load_migration()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mandatory-v16-score.db'}")
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        connection.execute(
            text("INSERT INTO score_entries(entry_type) VALUES ('FIXED_TASK_AWARD')")
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(
            RuntimeError,
            match="fixed-task scores already exist",
        ):
            migration.upgrade()
