from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.db_models import LessonScoreResultRecord, TeacherQualificationRecord


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_45_source_runtime_acl.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "source_runtime_acl_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_result_runtime_acl_is_minimal_and_self_verifying(monkeypatch) -> None:
    migration = _migration_module()
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

    migration.upgrade()
    grants, assertions = executed

    assert migration.down_revision == "20260806_44_source_reads"
    assert len(migration.revision) <= 32
    # Revisions 76 and 88 add v2 ownership columns after revision 59 has
    # already replaced this historical column grant with table-level CRUD.
    # They must not be projected backwards into rev45.
    later_v2_ownership_columns = {
        "v2_source_region",
        "v2_source_appoint_id",
        "v2_completion_participation_seq",
        "v2_teacher_id",
        "v2_projection_generation",
    }
    assert set(migration.LESSON_RESULT_UPDATE_COLUMNS) == {
        column.name
        for column in LessonScoreResultRecord.__table__.columns
        if not column.primary_key
        and column.name not in later_v2_ownership_columns
    }
    # graduation_score_locked does not exist at revision 45.  Revision 59 later
    # replaces these column grants with table-level CRUD, so revision 70 does
    # not need to mutate this historical column list.
    assert set(migration.QUALIFICATION_UPDATE_COLUMNS) == {
        column.name
        for column in TeacherQualificationRecord.__table__.columns
        if not column.primary_key and column.name != "graduation_score_locked"
    }
    assert "GRANT SELECT, INSERT ON TABLE" in grants
    assert "GRANT UPDATE (" in grants
    assert "GRANT UPDATE ON TABLE" not in grants
    assert "GRANT ALL" not in grants.upper()
    for forbidden in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert f"'UPDATE', '{forbidden}'" in assertions or f"'{forbidden}'" in assertions
    assert "has_table_privilege" in assertions
    assert "has_column_privilege" in assertions
    assert "lesson_id',\n                'UPDATE'" in assertions
    assert "teacher_id',\n                'UPDATE'" in assertions
    assert "source tables must remain read-only" in assertions


def test_source_result_runtime_acl_downgrade_removes_every_grant(monkeypatch) -> None:
    migration = _migration_module()
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

    migration.downgrade()
    sql = "\n".join(executed)

    assert "REVOKE UPDATE (" in sql
    assert "REVOKE SELECT, INSERT ON TABLE" in sql
    assert "FROM tit_growth_app" in sql
