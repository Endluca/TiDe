from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from app.task_catalog import MANDATORY_TASKS, TASK_COPY


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260819_64_g05_g08_courses.py"
    )
    spec = importlib.util.spec_from_file_location(
        "g05_g08_course_copy_v64",
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
            "task_kind VARCHAR(32) NOT NULL, why VARCHAR(500) NOT NULL, "
            "display_title VARCHAR(500), status VARCHAR(24) NOT NULL, "
            "row_version INTEGER NOT NULL, updated_by VARCHAR(128) NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
    )

    for index, (row_id, target) in enumerate(migration.TARGETS.items(), start=1):
        payload = {
            "template_id": target["template_id"],
            "category": target["category"],
            "score_type": target["score_type"],
            "score_value": target["score_value"],
            "content_status": "READY",
            "unrelated_field": {"preserved": row_id},
            **target["old_copy"],
        }
        connection.execute(
            text(
                "INSERT INTO task_templates("
                "row_id,template_id,template_version,status,revision,payload,"
                "updated_by,updated_at) VALUES ("
                ":row_id,:template_id,1,'PUBLISHED',:revision,:payload,"
                "'BEFORE','2026-08-14T00:00:00Z')"
            ),
            {
                "row_id": row_id,
                "template_id": target["template_id"],
                "revision": 20 + index,
                "payload": json.dumps(payload),
            },
        )

    connection.execute(
        text(
            "INSERT INTO task_assignments("
            "assignment_id,task_code,task_kind,why,display_title,status,"
            "row_version,updated_by,updated_at) VALUES "
            "('G08-OLD','G08','FIXED_GROWTH',:old_why,:old_title,'COMPLETED',"
            "7,'BEFORE','2026-08-14T00:00:00Z'),"
            "('G08-NEW','G08','FIXED_GROWTH',:new_why,NULL,'ASSIGNED',"
            "2,'BEFORE','2026-08-14T00:00:00Z'),"
            "('G05','G05','FIXED_GROWTH','Understand TTP.',NULL,'IN_PROGRESS',"
            "4,'BEFORE','2026-08-14T00:00:00Z')"
        ),
        {
            "old_why": migration.OLD_G08_WHY,
            "old_title": migration.OLD_G08_TITLE,
            "new_why": migration.NEW_G08_WHY,
        },
    )


def _payload(connection, row_id: str) -> dict:
    value = connection.execute(
        text("SELECT payload FROM task_templates WHERE row_id=:row_id"),
        {"row_id": row_id},
    ).scalar_one()
    return json.loads(value) if isinstance(value, str) else value


def test_v64_updates_only_g05_g08_course_copy_and_preserves_lifecycle(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260819_63_dts_direct_privacy"
    catalog_names = {
        item[0]: {"ops_name_zh": item[1], "title": item[2]}
        for item in MANDATORY_TASKS
    }
    for row_id, task_code in (("G06:v1", "G05"), ("G09:v1", "G08")):
        why, how, standard, benefit = TASK_COPY[task_code]
        assert migration.TARGETS[row_id]["new_copy"] == {
            **catalog_names[task_code],
            "why_template": why,
            "how_summary": how,
            "completion_standard": standard,
            "benefit": benefit,
        }
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g05-g08-course-copy-v64.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        before_revisions = dict(
            connection.execute(
                text("SELECT row_id,revision FROM task_templates")
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

        assert connection.execute(
            text(
                "SELECT why,display_title,status,row_version "
                "FROM task_assignments WHERE assignment_id='G08-OLD'"
            )
        ).one() == (
            migration.NEW_G08_WHY,
            migration.NEW_G08_TITLE,
            "COMPLETED",
            8,
        )
        assert connection.execute(
            text(
                "SELECT why,display_title,status,row_version "
                "FROM task_assignments WHERE assignment_id='G08-NEW'"
            )
        ).one() == (
            migration.NEW_G08_WHY,
            None,
            "ASSIGNED",
            2,
        )
        assert connection.execute(
            text(
                "SELECT status,row_version FROM task_assignments "
                "WHERE assignment_id='G05'"
            )
        ).one() == ("IN_PROGRESS", 4)

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

        assert connection.execute(
            text(
                "SELECT why,status,row_version FROM task_assignments "
                "WHERE assignment_id='G08-OLD'"
            )
        ).one() == (migration.NEW_G08_WHY, "COMPLETED", 8)


def test_v64_fails_closed_for_template_or_assignment_copy_drift(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'g05-g08-copy-drift-v64.db'}"
    )
    with engine.begin() as connection:
        _prepare_database(connection, migration)
        context = MigrationContext.configure(connection)
        connection.execute(
            text(
                "UPDATE task_templates SET payload=:payload WHERE row_id='G06:v1'"
            ),
            {
                "payload": json.dumps(
                    {
                        **_payload(connection, "G06:v1"),
                        "how_summary": "Unexpected G05 copy",
                    }
                )
            },
        )

        with pytest.raises(RuntimeError, match="copy drift"):
            with Operations.context(context):
                migration.upgrade()

        g05_target = migration.TARGETS["G06:v1"]
        connection.execute(
            text("UPDATE task_templates SET payload=:payload WHERE row_id='G06:v1'"),
            {
                "payload": json.dumps(
                    {
                        **_payload(connection, "G06:v1"),
                        **g05_target["old_copy"],
                    }
                )
            },
        )
        connection.execute(
            text(
                "UPDATE task_assignments SET why='Unexpected G08 reason' "
                "WHERE assignment_id='G08-OLD'"
            )
        )

        with pytest.raises(RuntimeError, match="assignment copy drift"):
            with Operations.context(context):
                migration.upgrade()
