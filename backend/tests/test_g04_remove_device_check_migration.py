from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql


def _load_module(filename: str, module_name: str):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_migration():
    return _load_module(
        "20260811_54_g04_remove_device_check.py",
        "g04_remove_device_check_v54",
    )


def _prepare_database(
    connection,
    *,
    copy: dict[str, str],
    score_value: int = 3,
) -> None:
    connection.execute(
        text(
            "CREATE TABLE task_templates ("
            "row_id VARCHAR(160) PRIMARY KEY, template_id VARCHAR(64) NOT NULL, "
            "template_version INTEGER NOT NULL, status VARCHAR(24) NOT NULL, "
            "revision INTEGER NOT NULL, output_type VARCHAR(32) NOT NULL, "
            "execution_owner VARCHAR(32) NOT NULL, "
            "integration_mode VARCHAR(32) NOT NULL, "
            "external_task_template_code VARCHAR(128) NOT NULL, "
            "source_mode VARCHAR(24) NOT NULL, payload JSON NOT NULL, "
            "created_by VARCHAR(128) NOT NULL, updated_by VARCHAR(128) NOT NULL, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE task_assignments ("
            "assignment_id VARCHAR(160) PRIMARY KEY, teacher_id VARCHAR(64) NOT NULL, "
            "task_code VARCHAR(64) NOT NULL, template_version_id VARCHAR(160) NOT NULL, "
            "status VARCHAR(24) NOT NULL, row_version INTEGER NOT NULL, "
            "completed_at DATETIME NULL, updated_by VARCHAR(128) NOT NULL)"
        )
    )
    payload = {
        "template_id": "G04",
        "score_type": "FIXED",
        "score_value": score_value,
        "content_status": "READY",
        "stage": "DAY_1_7",
        "priority": "P1",
        "due_rule": {
            "type": "CAMP_DAY_OR_EVENT_DEADLINE",
            "camp_day": 7,
            "event": "BEFORE_FIRST_LESSON",
            "fallback_hours": 168,
        },
        "unrelated_field": {"preserved": True},
        **copy,
    }
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,output_type,"
            "execution_owner,integration_mode,external_task_template_code,"
            "source_mode,payload,created_by,updated_by,created_at,updated_at) "
            "VALUES ('G02:v1','G04',1,'PUBLISHED',9,'TEACHER_TASK',"
            "'TEACHER_APP','INBOUND_STATUS_ONLY','TIT.G04','REAL',:payload,"
            "'BEFORE_CREATOR','BEFORE_UPDATER','2026-08-05T00:00:00Z',"
            "'2026-08-10T00:00:00Z')"
        ),
        {"payload": json.dumps(payload)},
    )
    connection.execute(
        text(
            "INSERT INTO task_templates("
            "row_id,template_id,template_version,status,revision,output_type,"
            "execution_owner,integration_mode,external_task_template_code,"
            "source_mode,payload,created_by,updated_by,created_at,updated_at) "
            "VALUES ('G04:v1','G03',1,'PUBLISHED',8,'TEACHER_TASK',"
            "'TEACHER_APP','INBOUND_STATUS_ONLY','TIT.G03','REAL',:payload,"
            "'OTHER_CREATOR','OTHER_UPDATER','2026-08-05T00:00:00Z',"
            "'2026-08-10T00:00:00Z')"
        ),
        {
            "payload": json.dumps(
                {
                    "template_id": "G03",
                    "title": "How to handle different types of students",
                    "score_value": 2,
                    "content_status": "PENDING_JIAHE",
                }
            )
        },
    )
    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,teacher_id,task_code,template_version_id,status,"
            "row_version,completed_at,updated_by) VALUES ("
            "'ASSIGN-G04','T-1001','G04','G02:v1','COMPLETED',7,"
            "'2026-08-10T01:00:00Z','TEACHER_APP')"
        )
    )


def _payload(connection) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id='G02:v1'")
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def _template_facts(connection, row_id: str):
    return connection.execute(
        text(
            "SELECT row_id,template_id,template_version,status,output_type,"
            "execution_owner,integration_mode,external_task_template_code,"
            "source_mode,created_by,created_at FROM task_templates "
            "WHERE row_id=:row_id"
        ),
        {"row_id": row_id},
    ).one()


def _assignment_facts(connection):
    return connection.execute(
        text(
            "SELECT assignment_id,teacher_id,task_code,template_version_id,"
            "status,row_version,completed_at,updated_by FROM task_assignments "
            "WHERE assignment_id='ASSIGN-G04'"
        )
    ).one()


def test_v54_removes_only_g04_device_copy_and_rejects_downgrade(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    v50 = _load_module(
        "20260810_50_g04_independent_sections.py",
        "g04_independent_sections_v50_for_v54",
    )
    assert migration.revision == "20260811_54_g04_remove_device_check"
    assert migration.down_revision == "20260811_51_g01_tesol_only"
    assert {
        field: migration.OLD_COPY[field]
        for field in v50.NEW_COPY
    } == v50.NEW_COPY

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g04-remove-device-check-v54.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=migration.OLD_COPY)
        template_before = _template_facts(connection, "G02:v1")
        other_template_before = _template_facts(connection, "G04:v1")
        other_payload_before = connection.execute(
            text("SELECT payload FROM task_templates WHERE row_id='G04:v1'")
        ).scalar_one()
        assignment_before = _assignment_facts(connection)
        payload_before = _payload(connection)

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        payload = _payload(connection)
        assert {field: payload[field] for field in migration.NEW_COPY} == (
            migration.NEW_COPY
        )
        assert payload["score_type"] == "FIXED"
        assert payload["score_value"] == 3
        assert payload["content_status"] == "READY"
        assert payload["stage"] == "DAY_1_7"
        assert payload["priority"] == "P1"
        assert payload["due_rule"] == payload_before["due_rule"]
        assert payload["unrelated_field"] == {"preserved": True}
        assert set(payload) == set(payload_before)
        assert _template_facts(connection, "G02:v1") == template_before
        assert _template_facts(connection, "G04:v1") == other_template_before
        assert connection.execute(
            text("SELECT payload FROM task_templates WHERE row_id='G04:v1'")
        ).scalar_one() == other_payload_before
        assert _assignment_facts(connection) == assignment_before
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G02:v1'")
        ).scalar_one() == 10
        joined_copy = " ".join(migration.NEW_COPY.values()).lower()
        assert "courseware" in joined_copy
        assert "photo" in joined_copy
        assert migration.NEW_COPY["how_summary"].index("photo") < (
            migration.NEW_COPY["how_summary"].index("courseware")
        )
        assert migration.NEW_COPY["completion_standard"].index("photo") < (
            migration.NEW_COPY["completion_standard"].index("courseware")
        )
        assert "device" not in joined_copy
        assert "network" not in joined_copy
        assert "microphone" not in joined_copy

        upgraded_template = dict(
            connection.execute(
                text("SELECT * FROM task_templates WHERE row_id='G02:v1'")
            ).mappings().one()
        )
        with pytest.raises(RuntimeError, match="forward-only"):
            with Operations.context(context):
                migration.downgrade()

        assert _payload(connection) == payload
        assert dict(
            connection.execute(
                text("SELECT * FROM task_templates WHERE row_id='G02:v1'")
            ).mappings().one()
        ) == upgraded_template
        assert _template_facts(connection, "G02:v1") == template_before
        assert _assignment_facts(connection) == assignment_before
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G02:v1'")
        ).scalar_one() == 10


def test_v54_locks_the_stable_g04_row_before_copying_payload() -> None:
    migration = _load_migration()

    compiled = str(
        migration._g04_select().compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in compiled


def test_v54_rejects_a_stale_catalog_revision_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g04-stale-revision-v54.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=migration.OLD_COPY)
        stale_row = dict(
            connection.execute(
                text(
                    "SELECT row_id,template_id,template_version,status,"
                    "revision,payload FROM task_templates WHERE row_id='G02:v1'"
                )
            ).mappings().one()
        )
        stale_row["revision"] = int(stale_row["revision"]) - 1
        payload_before = _payload(connection)
        stale_row["payload"] = payload_before
        monkeypatch.setattr(migration, "_g04_row", lambda _bind: stale_row)
        context = MigrationContext.configure(connection)

        with pytest.raises(RuntimeError, match="did not update exactly one"):
            with Operations.context(context):
                migration.upgrade()

        assert _payload(connection) == payload_before
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G02:v1'")
        ).scalar_one() == 9


@pytest.mark.parametrize(
    ("drift_field", "drift_value", "error"),
    [
        ("how_summary", "Unreviewed replacement copy", "copy drift"),
        ("score_value", 4, "published three-point G04 version"),
    ],
)
def test_v54_fails_closed_on_unreviewed_g04_drift(
    tmp_path: Path,
    drift_field: str,
    drift_value: object,
    error: str,
) -> None:
    migration = _load_migration()
    copy = dict(migration.OLD_COPY)
    if drift_field in copy:
        copy[drift_field] = str(drift_value)
        score_value = 3
    else:
        score_value = int(drift_value)

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / f'g04-drift-{drift_field}-v54.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, copy=copy, score_value=score_value)
        payload_before = _payload(connection)
        assignment_before = _assignment_facts(connection)
        revision_before = connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G02:v1'")
        ).scalar_one()
        context = MigrationContext.configure(connection)

        with pytest.raises(RuntimeError, match=error):
            with Operations.context(context):
                migration.upgrade()

        assert _payload(connection) == payload_before
        assert _assignment_facts(connection) == assignment_before
        assert connection.execute(
            text("SELECT revision FROM task_templates WHERE row_id='G02:v1'")
        ).scalar_one() == revision_before
