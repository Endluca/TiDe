from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260807_46_teacher_g01_source.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "teacher_source_g01_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_only_two_nullable_boolean_facts_and_minimum_acl(
    monkeypatch,
) -> None:
    migration = _migration_module()
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

    migration.upgrade()

    assert migration.down_revision == "20260806_45_source_runtime_acl"
    assert len(migration.revision) <= 32
    assert [(table, column.name, kwargs) for table, column, kwargs in added] == [
        ("teacher_source_wide", "is_cpl_tesol", {"schema": "public"}),
        ("teacher_source_wide", "is_self_introduce", {"schema": "public"}),
    ]
    for _table, column, _kwargs in added:
        assert type(column.type) is sa.Boolean
        assert column.nullable is True
        assert column.default is None
        assert column.server_default is None

    sql = "\n".join(executed)
    assert "GRANT SELECT (" in sql
    assert "tchr_id" in sql
    assert "is_cpl_tesol" in sql
    assert "is_self_introduce" in sql
    assert "real_name" in sql
    assert "REVOKE ALL PRIVILEGES" in sql
    assert "public.teacher_metric_snapshots" in sql
    assert "GRANT ALL" not in sql.upper()


def test_downgrade_refuses_data_loss_and_drops_only_the_two_columns(
    monkeypatch,
) -> None:
    migration = _migration_module()
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

    migration.downgrade()

    assert dropped == [
        ("teacher_source_wide", "is_self_introduce", {"schema": "public"}),
        ("teacher_source_wide", "is_cpl_tesol", {"schema": "public"}),
    ]
    sql = "\n".join(executed)
    assert "refusing to drop active G01 source facts or pending events" in sql
    assert "GRANT SELECT ON TABLE public.teacher_metric_snapshots" in sql
