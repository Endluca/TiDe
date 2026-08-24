from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from app.db_models import LessonSourceWideRecord
from app.source_contracts import LESSON_SOURCE_FIELDS


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_73_retire_false_early_contract.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "retire_false_early_contract_v73",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_73_rewrites_dependency_before_dropping_column(
    monkeypatch,
) -> None:
    migration = _load_migration()
    operations: list[tuple[str, object]] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: operations.append(("execute", str(statement))),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda *args, **kwargs: operations.append(
            ("drop_column", (args, kwargs))
        ),
    )

    migration.upgrade()

    assert migration.revision == "20260822_73_retire_false_early"
    assert migration.down_revision == "20260822_72_teacher_online_lock"
    assert [kind for kind, _value in operations] == [
        "execute",
        "execute",
        "execute",
        "drop_column",
        "execute",
    ]
    sql = "\n".join(
        str(value) for kind, value in operations if kind == "execute"
    )
    assert "IN ACCESS EXCLUSIVE MODE" in sql
    assert "pg_catalog.pg_depend" in sql
    assert "pg_catalog.pg_rewrite" in sql
    assert "public.teacher_lesson_score_current" in sql
    assert "FALSE_EARLY_LEAVE_DEPENDENT_VIEWS_UNEXPECTED" in sql
    assert "FALSE_EARLY_LEAVE_VIEW_DEPENDENCY_REMAINS" in sql
    assert "CREATE OR REPLACE VIEW" in sql
    assert "lesson_column_count <> 23" in sql
    assert "CASCADE" not in sql.upper()
    assert operations[3] == (
        "drop_column",
        (("lesson_source_wide", "假早退"), {"schema": "public"}),
    )


def test_revision_73_downgrade_is_empty_only_and_restores_exact_shape(
    monkeypatch,
) -> None:
    migration = _load_migration()
    operations: list[tuple[str, object]] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: operations.append(("execute", str(statement))),
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: operations.append(
            ("add_column", (args, kwargs))
        ),
    )

    migration.downgrade()

    assert [kind for kind, _value in operations] == [
        "execute",
        "add_column",
        "execute",
        "execute",
    ]
    guard_sql = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in guard_sql
    assert "FROM public.lesson_source_wide" in guard_sql
    assert "retired values cannot be restored for existing lessons" in guard_sql
    add_args, add_kwargs = operations[1][1]
    assert add_args[0] == "lesson_source_wide"
    assert isinstance(add_args[1], sa.Column)
    assert add_args[1].name == "假早退"
    assert isinstance(add_args[1].type, sa.Boolean)
    assert add_args[1].nullable is True
    assert add_kwargs == {"schema": "public"}
    restore_sql = str(operations[2][1])
    assert "CREATE OR REPLACE VIEW" in restore_sql
    assert "is_false_early_leave" in restore_sql
    assert "source.\\\"假早退\\\"" in restore_sql
    assert "lesson_column_count <> 24" in str(operations[3][1])


def test_current_python_contract_has_no_false_early_leave_fact() -> None:
    assert len(LESSON_SOURCE_FIELDS) == 22
    assert "假早退" not in LESSON_SOURCE_FIELDS
    assert "假早退" not in LessonSourceWideRecord.__table__.c
    assert not hasattr(LessonSourceWideRecord, "is_false_early_leave")
