from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_module(filename: str, module_name: str):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(
        module_name,
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_migration():
    return _load_module(
        "20260810_50_g04_independent_sections.py",
        "g04_independent_sections_v50",
    )


def _prepare_database(connection, *, copy: dict[str, str]) -> None:
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
        "template_id": "G04",
        "title": "Lesson Preparation&Device Network Check",
        "why_template": (
            "Complete lesson preparation and confirm that your teaching setup "
            "is ready before class."
        ),
        "score_value": 3,
        "content_status": "READY",
        "unrelated_field": {"preserved": True},
        **copy,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G02:v1','G04',1,'PUBLISHED',8,:payload,'BEFORE',"
            "'2026-08-05T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G04:v1','G03',1,'PUBLISHED',8,:payload,'BEFORE',"
            "'2026-08-05T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G03",
                    "title": "How to handle different types of students",
                    "content_status": "PENDING_JIAHE",
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,task_code,status,row_version"
            ") VALUES ('ASSIGN-G04','G04','COMPLETED',7)"
        )
    )


def _payload(connection) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id='G02:v1'")
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v50_updates_only_g04_copy_and_preserves_task_facts(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    copy_migration = _load_module(
        "20260728_32_mandatory_task_teacher_copy.py",
        "mandatory_task_teacher_copy_v32_for_g04",
    )
    assert migration.down_revision == "20260807_49_unused_columns"
    assert migration.OLD_COPY == {
        field: copy_migration.NEW_COPY["G04"][field]
        for field in migration.OLD_COPY
    }

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g04-independent-sections-v50.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=migration.OLD_COPY)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        payload = _payload(connection)
        assert {field: payload[field] for field in migration.NEW_COPY} == (
            migration.NEW_COPY
        )
        assert payload["title"] == "Lesson Preparation&Device Network Check"
        assert payload["why_template"] == (
            "Complete lesson preparation and confirm that your teaching setup "
            "is ready before class."
        )
        assert payload["score_value"] == 3
        assert payload["content_status"] == "READY"
        assert payload["unrelated_field"] == {"preserved": True}
        assert connection.execute(
            text(
                "SELECT revision FROM task_templates WHERE row_id='G02:v1'"
            )
        ).scalar_one() == 9
        assert connection.execute(
            text(
                "SELECT template_id,revision FROM task_templates "
                "WHERE row_id='G04:v1'"
            )
        ).one() == ("G03", 8)
        assert connection.execute(
            text(
                "SELECT status,row_version FROM task_assignments "
                "WHERE assignment_id='ASSIGN-G04'"
            )
        ).one() == ("COMPLETED", 7)

        with Operations.context(context):
            migration.downgrade()

        downgraded = _payload(connection)
        assert {field: downgraded[field] for field in migration.OLD_COPY} == (
            migration.OLD_COPY
        )
        assert downgraded["score_value"] == 3
        assert downgraded["why_template"] == payload["why_template"]
        assert connection.execute(
            text(
                "SELECT revision FROM task_templates WHERE row_id='G02:v1'"
            )
        ).scalar_one() == 8
        assert connection.execute(
            text(
                "SELECT status,row_version FROM task_assignments "
                "WHERE assignment_id='ASSIGN-G04'"
            )
        ).one() == ("COMPLETED", 7)


def test_v50_fails_closed_when_existing_g04_copy_has_drifted(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    drifted = dict(migration.OLD_COPY)
    drifted["how_summary"] = "Unreviewed replacement copy"

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g04-copy-drift-v50.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=drifted)
        context = MigrationContext.configure(connection)
        with pytest.raises(
            RuntimeError,
            match="unreviewed teacher-facing copy drift",
        ):
            with Operations.context(context):
                migration.upgrade()

        assert _payload(connection)["how_summary"] == "Unreviewed replacement copy"
        assert connection.execute(
            text(
                "SELECT revision FROM task_templates WHERE row_id='G02:v1'"
            )
        ).scalar_one() == 8
