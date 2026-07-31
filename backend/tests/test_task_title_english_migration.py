from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260729_35_task_title_english.py"
    )
    spec = importlib.util.spec_from_file_location(
        "task_title_english_v35",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_v35_migrates_personalized_titles_without_touching_evidence(
    tmp_path: Path,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260729_34_body_evidence"

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'task-title-v35.db'}"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE task_assignments ("
                "assignment_id VARCHAR(128) PRIMARY KEY, "
                "task_code VARCHAR(64) NOT NULL, "
                "task_kind VARCHAR(32) NOT NULL, "
                "display_title VARCHAR(500), "
                "evidence_snapshot JSON NOT NULL, "
                "row_version INTEGER NOT NULL, "
                "updated_by VARCHAR(128) NOT NULL)"
            )
        )
        fixtures = [
            ("A1", "P-REL-MEMO", "出席（未填写lesson-memo）问题"),
            ("A2", "P-REL-ATTENDANCE", "出席问题"),
            ("A3", "P-FB-BLACKLIST", "拉黑问题"),
            ("A4", "P-FB-NEGATIVE", "差评-缺少互动问题"),
            (
                "A5",
                "P-FB-COMPLAINT",
                "一般投诉-未及时回应学员问题问题",
            ),
        ]
        for assignment_id, task_code, title in fixtures:
            connection.execute(
                text(
                    "INSERT INTO task_assignments("
                    "assignment_id,task_code,task_kind,display_title,"
                    "evidence_snapshot,row_version,updated_by"
                    ") VALUES (:assignment_id,:task_code,"
                    "'PERSONALIZED_IMPROVEMENT',:title,"
                    "json('{\"raw_label\":\"中文证据\"}'),1,'BEFORE')"
                ),
                {
                    "assignment_id": assignment_id,
                    "task_code": task_code,
                    "title": title,
                },
            )

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        rows = connection.execute(
            text(
                "SELECT assignment_id,display_title,evidence_snapshot,"
                "row_version FROM task_assignments ORDER BY assignment_id"
            )
        ).mappings().all()
        assert [row["display_title"] for row in rows] == [
            "Missing Lesson Memo",
            "Attendance Improvement",
            "Blacklist Prevention",
            "Negative Feedback - Insufficient Interaction",
            "General Complaint - Did Not Respond to the Student Promptly",
        ]
        assert all(
            not _HAN_CHARACTER.search(row["display_title"])
            for row in rows
        )
        assert all("中文证据" in row["evidence_snapshot"] for row in rows)
        assert {row["row_version"] for row in rows} == {2}
