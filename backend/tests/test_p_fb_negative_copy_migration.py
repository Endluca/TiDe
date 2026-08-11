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
        / "20260811_56_p_fb_negative_copy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "p_fb_negative_copy_v56",
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
        "template_id": "P-FB-NEGATIVE",
        "title": "Feedback Improvement",
        "why_template": (
            "The same negative-feedback signal has appeared more than once "
            "for this teacher."
        ),
        "category": "PERSONALIZED_IMPROVEMENT",
        "content_status": "READY",
        "score_type": "ZERO",
        "score_value": 0,
        "benefit": (
            "This task carries no points. It targets a repeated "
            "learner-feedback issue."
        ),
        "unrelated_field": {"preserved": True},
        **copy,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'P-FB-NEGATIVE:v1','P-FB-NEGATIVE',1,'PUBLISHED',5,:payload,"
            "'BEFORE','2026-08-10T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'P-FB-COMPLAINT:v1','P-FB-COMPLAINT',1,'PUBLISHED',4,:payload,"
            "'BEFORE','2026-08-10T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "P-FB-COMPLAINT",
                    "title": "Complaint Improvement",
                    "score_type": "ZERO",
                    "score_value": 0,
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,task_code,status,row_version"
            ") VALUES ("
            "'ASSIGN-P-FB-NEGATIVE','P-FB-NEGATIVE','IN_PROGRESS',7)"
        )
    )


def _payload(connection, row_id: str = "P-FB-NEGATIVE:v1") -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id=:row_id"),
        {"row_id": row_id},
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v56_updates_only_copy_and_preserves_template_and_assignment_facts(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260811_55_source_wide_v12"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'p-fb-negative-copy-v56.db'}"
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
        assert payload["title"] == "Feedback Improvement"
        assert payload["why_template"] == (
            "The same negative-feedback signal has appeared more than once "
            "for this teacher."
        )
        assert payload["score_type"] == "ZERO"
        assert payload["score_value"] == 0
        assert payload["content_status"] == "READY"
        assert payload["benefit"] == (
            "This task carries no points. It targets a repeated "
            "learner-feedback issue."
        )
        assert payload["unrelated_field"] == {"preserved": True}
        assert connection.execute(
            text(
                "SELECT revision FROM task_templates "
                "WHERE row_id='P-FB-NEGATIVE:v1'"
            )
        ).scalar_one() == 6
        assert connection.execute(
            text(
                "SELECT template_id,revision FROM task_templates "
                "WHERE row_id='P-FB-COMPLAINT:v1'"
            )
        ).one() == ("P-FB-COMPLAINT", 4)
        assert connection.execute(
            text(
                "SELECT status,row_version FROM task_assignments "
                "WHERE assignment_id='ASSIGN-P-FB-NEGATIVE'"
            )
        ).one() == ("IN_PROGRESS", 7)

        with Operations.context(context):
            migration.downgrade()

        assert {
            field: _payload(connection)[field] for field in migration.OLD_COPY
        } == migration.OLD_COPY
        assert connection.execute(
            text(
                "SELECT revision FROM task_templates "
                "WHERE row_id='P-FB-NEGATIVE:v1'"
            )
        ).scalar_one() == 5
        assert connection.execute(
            text(
                "SELECT status,row_version FROM task_assignments "
                "WHERE assignment_id='ASSIGN-P-FB-NEGATIVE'"
            )
        ).one() == ("IN_PROGRESS", 7)


def test_v56_fails_closed_for_copy_drift_or_wrong_stable_identity(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'p-fb-negative-copy-drift-v56.db'}"
    )
    with engine.begin() as connection:
        drifted = dict(migration.OLD_COPY)
        drifted["completion_standard"] = "Unreviewed replacement"
        _prepare_database(connection, copy=drifted)
        context = MigrationContext.configure(connection)
        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()
        assert _payload(connection)["completion_standard"] == (
            "Unreviewed replacement"
        )

        connection.execute(
            text(
                "UPDATE task_templates SET template_id='P-FB-COMPLAINT' "
                "WHERE row_id='P-FB-NEGATIVE:v1'"
            )
        )
        with pytest.raises(RuntimeError, match="zero-point personalized"):
            with Operations.context(context):
                migration.upgrade()
