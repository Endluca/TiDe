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
        / "20260728_32_mandatory_task_teacher_copy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mandatory_task_copy_v32",
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
            "template_id VARCHAR(64) PRIMARY KEY, status VARCHAR(24) NOT NULL, "
            "revision INTEGER NOT NULL, payload JSON NOT NULL, "
            "updated_by VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE task_assignments ("
            "assignment_id VARCHAR(160) PRIMARY KEY, task_code VARCHAR(64) NOT NULL, "
            "task_kind VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, "
            "why TEXT NOT NULL, row_version INTEGER NOT NULL, "
            "updated_by VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    for task_code, fields in migration.OLD_COPY.items():
        connection.execute(
            text(
                "INSERT INTO task_templates("
                "template_id,status,revision,payload,updated_by,updated_at"
                ") VALUES (:task_code,'PUBLISHED',3,:payload,'BEFORE',"
                "'2026-07-28T00:00:00Z')"
            ),
            {
                "task_code": task_code,
                "payload": json.dumps(
                    {
                        "template_id": task_code,
                        "title": task_code,
                        "score_value": 1,
                        **fields,
                    }
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO task_assignments("
                "assignment_id,task_code,task_kind,status,why,row_version,"
                "updated_by,updated_at"
                ") VALUES (:assignment_id,:task_code,'FIXED_GROWTH','COMPLETED',"
                ":why,7,'BEFORE','2026-07-28T00:00:00Z')"
            ),
            {
                "assignment_id": f"ASSIGN-{task_code}",
                "task_code": task_code,
                "why": fields["why_template"],
            },
        )


def _template_payloads(connection) -> dict[str, dict]:
    return {
        code: json.loads(payload) if isinstance(payload, str) else payload
        for code, payload in connection.execute(
            text(
                "SELECT template_id,payload FROM task_templates "
                "ORDER BY template_id"
            )
        ).all()
    }


def test_v32_updates_copy_without_changing_task_status_or_scores(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260728_31_task_why_evidence"

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'mandatory-copy-v32.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        payloads = _template_payloads(connection)
        for task_code, fields in migration.NEW_COPY.items():
            assert {
                key: payloads[task_code][key]
                for key in (
                    "why_template",
                    "how_summary",
                    "completion_standard",
                    "benefit",
                )
            } == fields
            assert payloads[task_code]["content_status"] == (
                "PENDING_JIAHE" if task_code == "G03" else "READY"
            )
            assert payloads[task_code]["score_value"] == 1

        assignments = connection.execute(
            text(
                "SELECT task_code,status,why,row_version "
                "FROM task_assignments ORDER BY task_code"
            )
        ).mappings()
        for assignment in assignments:
            assert assignment["status"] == "COMPLETED"
            assert assignment["why"] == migration.NEW_COPY[
                assignment["task_code"]
            ]["why_template"]
            assert assignment["row_version"] == 8

        assert {
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM task_templates")
            ).all()
        } == {4}

        with Operations.context(context):
            migration.downgrade()

        downgraded = _template_payloads(connection)
        for task_code, fields in migration.OLD_COPY.items():
            assert "content_status" not in downgraded[task_code]
            assert downgraded[task_code]["why_template"] == fields["why_template"]
        assert {
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM task_templates")
            ).all()
        } == {3}
        assert {
            row[0]
            for row in connection.execute(
                text("SELECT row_version FROM task_assignments")
            ).all()
        } == {9}
