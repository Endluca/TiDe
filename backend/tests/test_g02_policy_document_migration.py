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
        / "20260811_57_g02_policy_document.py"
    )
    spec = importlib.util.spec_from_file_location(
        "g02_policy_document_v57",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


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
        "template_id": "G02",
        "title": "Platform Policies",
        "why_template": "Learn the essential classroom and account-safety rules.",
        "score_value": 2,
        "content_status": "READY",
        "unrelated_field": {"preserved": True},
        **copy,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G03:v1','G02',1,'PUBLISHED',8,:payload,'BEFORE',"
            "'2026-08-10T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G02:v1','G04',1,'PUBLISHED',9,:payload,'BEFORE',"
            "'2026-08-10T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G04",
                    "title": "Lesson Preparation&Device Network Check",
                    "score_value": 3,
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments(assignment_id,task_code,status,row_version) "
            "VALUES ('ASSIGN-G02','G02','COMPLETED',7)"
        )
    )


def _payload(connection) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id='G03:v1'")
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v57_updates_only_g02_copy_and_preserves_task_facts(tmp_path: Path) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260811_56_p_fb_negative_copy"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g02-policy-document-v57.db'}"
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
        assert payload["title"] == "Platform Policies"
        assert payload["score_value"] == 2
        assert payload["content_status"] == "READY"
        assert payload["unrelated_field"] == {"preserved": True}
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G03:v1'")
        ).scalar_one() == 9
        assert connection.execute(
            text("SELECT template_id,revision FROM task_templates WHERE row_id='G02:v1'")
        ).one() == ("G04", 9)
        assert connection.execute(
            text("SELECT status,row_version FROM task_assignments WHERE assignment_id='ASSIGN-G02'")
        ).one() == ("COMPLETED", 7)

        with Operations.context(context):
            migration.downgrade()

        assert {field: _payload(connection)[field] for field in migration.OLD_COPY} == (
            migration.OLD_COPY
        )
        assert connection.execute(
            text("SELECT status,row_version FROM task_assignments WHERE assignment_id='ASSIGN-G02'")
        ).one() == ("COMPLETED", 7)


def test_v57_fails_closed_for_copy_drift_or_wrong_stable_identity(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g02-policy-document-drift-v57.db'}"
    )
    with engine.begin() as connection:
        drifted = dict(migration.OLD_COPY)
        drifted["how_summary"] = "Unreviewed replacement"
        _prepare_database(connection, copy=drifted)
        context = MigrationContext.configure(connection)
        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()
        assert _payload(connection)["how_summary"] == "Unreviewed replacement"

        connection.execute(
            text("UPDATE task_templates SET template_id='G99' WHERE row_id='G03:v1'")
        )
        with pytest.raises(RuntimeError, match="published two-point G02"):
            with Operations.context(context):
                migration.upgrade()
