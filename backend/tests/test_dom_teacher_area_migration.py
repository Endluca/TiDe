from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_71_dom_teacher_area.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dom_teacher_area_v71",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_71_normalizes_only_teacher_area_and_verifies_upgrade(
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

    migration.upgrade()

    assert migration.revision == "20260822_71_dom_teacher_area"
    assert migration.down_revision == "20260822_70_qualification_schema"
    assert len(executed) == 1
    sql = executed[0]
    assert "LOCK TABLE public.teacher_source_wide" in sql
    assert "SET teach_area_type = 'dom'" in sql
    assert "WHERE teach_area_type = 'dmo'" in sql
    assert "DOM_TEACHER_AREA_UPGRADE_INCOMPLETE" in sql
    assert "teachers" not in sql.replace("teacher_source_wide", "")


def test_revision_71_downgrade_is_the_verified_code_rollback_inverse(
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

    migration.downgrade()

    assert len(executed) == 1
    sql = executed[0]
    assert "SET teach_area_type = 'dmo'" in sql
    assert "WHERE teach_area_type = 'dom'" in sql
    assert "DOM_TEACHER_AREA_DOWNGRADE_INCOMPLETE" in sql
