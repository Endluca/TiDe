from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260811_51_g01_tesol_only.py"
    )
    spec = importlib.util.spec_from_file_location(
        "g01_tesol_only_v51",
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
            "assignment_id VARCHAR(160) PRIMARY KEY, "
            "teacher_id VARCHAR(160) NOT NULL, task_code VARCHAR(64) NOT NULL, "
            "template_version_id VARCHAR(160) NOT NULL, "
            "status VARCHAR(24) NOT NULL, row_version INTEGER NOT NULL, "
            "evidence_snapshot JSON NOT NULL)"
        )
    )
    payload = {
        "template_id": "G01",
        "title": "Profile & Credentials Completion",
        "category": "MANDATORY_GROWTH",
        "content_status": "READY",
        "score_type": "FIXED",
        "score_value": 3,
        "benefit": (
            "Your profile and required TESOL learning evidence are complete."
        ),
        "unrelated_field": {"preserved": True},
        **copy,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G01:v1','G01',1,'PUBLISHED',8,:payload,'BEFORE',"
            "'2026-08-10T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,payload,"
            "updated_by,updated_at) VALUES ("
            "'G03:v1','G02',1,'PUBLISHED',9,:payload,'BEFORE',"
            "'2026-08-10T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G02",
                    "title": "Platform Policies",
                    "score_type": "FIXED",
                    "score_value": 2,
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,teacher_id,task_code,template_version_id,status,"
            "row_version,evidence_snapshot) VALUES ("
            "'ASSIGN-G01','TEACHER-1','G01','G01:v1','COMPLETED',7,:evidence)"
        ),
        {"evidence": json.dumps({"preserved": True})},
    )


def _payload(connection) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id='G01:v1'")
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def _assignment(connection) -> tuple:
    return connection.execute(
        text(
            "SELECT teacher_id,task_code,template_version_id,status,row_version,"
            "evidence_snapshot FROM task_assignments "
            "WHERE assignment_id='ASSIGN-G01'"
        )
    ).one()


def test_v51_updates_only_g01_copy_and_round_trips_exactly(tmp_path: Path) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260810_50_g04_sections"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g01-tesol-only-v51.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=migration.OLD_COPY)
        before_payload = _payload(connection)
        before_assignment = _assignment(connection)
        other_template = connection.execute(
            text(
                "SELECT template_id,revision,payload FROM task_templates "
                "WHERE row_id='G03:v1'"
            )
        ).one()
        context = MigrationContext.configure(connection)

        with Operations.context(context):
            migration.upgrade()

        payload = _payload(connection)
        assert {field: payload[field] for field in migration.NEW_COPY} == (
            migration.NEW_COPY
        )
        assert payload["score_type"] == "FIXED"
        assert payload["score_value"] == 3
        assert payload["benefit"] == before_payload["benefit"]
        assert payload["content_status"] == before_payload["content_status"]
        assert payload["unrelated_field"] == before_payload["unrelated_field"]
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G01:v1'")
        ).scalar_one() == 9
        assert _assignment(connection) == before_assignment
        assert connection.execute(
            text(
                "SELECT template_id,revision,payload FROM task_templates "
                "WHERE row_id='G03:v1'"
            )
        ).one() == other_template

        with Operations.context(context):
            migration.downgrade()

        assert _payload(connection) == before_payload
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G01:v1'")
        ).scalar_one() == 8
        assert _assignment(connection) == before_assignment


def test_v51_fails_closed_when_existing_g01_copy_has_drifted(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    drifted = dict(migration.OLD_COPY)
    drifted["completion_standard"] = "Unreviewed replacement"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g01-tesol-only-drift-v51.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=drifted)
        before_payload = _payload(connection)
        before_assignment = _assignment(connection)
        context = MigrationContext.configure(connection)

        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()

        assert _payload(connection) == before_payload
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G01:v1'")
        ).scalar_one() == 8
        assert _assignment(connection) == before_assignment


def test_v51_contains_tesol_only_acl_and_exact_downgrade_restore(
    monkeypatch,
) -> None:
    migration = _load_migration()
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )
    copy_calls: list[tuple[dict[str, str], dict[str, str], int]] = []
    monkeypatch.setattr(
        migration,
        "_apply_copy",
        lambda copy, *, expected_copy, actor, revision_delta: copy_calls.append(
            (dict(copy), dict(expected_copy), revision_delta)
        ),
    )

    migration.upgrade()
    migration.downgrade()

    assert len(executed) == 2
    assert copy_calls == [
        (migration.NEW_COPY, migration.OLD_COPY, 1),
        (migration.OLD_COPY, migration.NEW_COPY, -1),
    ]
    upgrade_sql, downgrade_sql = executed
    assert "REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide" in (
        upgrade_sql
    )
    assert "REVOKE SELECT (" in upgrade_sql
    assert "is_self_introduce" in upgrade_sql
    assert "GRANT SELECT (\n            tchr_id,\n            is_cpl_tesol\n" in (
        upgrade_sql
    )
    assert "has_column_privilege(" in upgrade_sql
    assert "TESOL-only source privileges are invalid" in upgrade_sql

    assert "REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide" in (
        downgrade_sql
    )
    assert (
        "GRANT SELECT (\n            tchr_id,\n            is_cpl_tesol,"
        "\n            is_self_introduce\n"
    ) in downgrade_sql
    assert "restored G01 source privileges are invalid" in downgrade_sql
