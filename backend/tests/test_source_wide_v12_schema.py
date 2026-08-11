from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260811_55_source_wide_contract_v12.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("source_wide_v12", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_upgrade_drops_only_the_eight_retired_teacher_columns(monkeypatch) -> None:
    migration = _load_migration()
    dropped: list[tuple[str, str, dict]] = []
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column, **kwargs: dropped.append((table, column, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260811_55_source_wide_v12"
    assert migration.down_revision == "20260811_54_g04_remove_device_check"
    assert [column for _table, column, _kwargs in dropped] == list(
        migration.RETIRED_TEACHER_COLUMNS
    )
    assert {table for table, _column, _kwargs in dropped} == {
        "teacher_source_wide"
    }
    assert all(kwargs == {"schema": "public"} for _t, _c, kwargs in dropped)
    sql = "\n".join(executed)
    assert "expected %, actual %" in sql
    assert "lesson_source_wide must remain the confirmed 23-column contract" in sql
    assert "actual_teacher_count <> 63" in sql
    assert "actual_teacher_count <> 55" in sql
    assert "CASCADE" not in sql.upper()


def test_downgrade_is_schema_only_and_refuses_existing_rows(monkeypatch) -> None:
    migration = _load_migration()
    added: list[tuple[str, sa.Column, dict]] = []
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column, **kwargs: added.append((table, column, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.downgrade()

    assert [column.name for _table, column, _kwargs in added] == list(
        migration.RETIRED_TEACHER_COLUMNS
    )
    assert all(table == "teacher_source_wide" for table, _column, _kwargs in added)
    assert all(kwargs == {"schema": "public"} for _t, _c, kwargs in added)
    sql = "\n".join(executed)
    assert "removed column values cannot be restored for existing rows" in sql
