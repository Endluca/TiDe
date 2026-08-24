from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.db_models import LessonSourceWideRecord


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_77_retire_cpu_network.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "retire_cpu_network_v77",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_77_clears_values_before_installing_storage_lock(
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
        "create_check_constraint",
        lambda *args, **kwargs: operations.append(
            ("create_check_constraint", (args, kwargs))
        ),
    )

    migration.upgrade()

    assert migration.revision == "20260822_77_retire_cpu_network"
    assert migration.down_revision == "20260822_76_lesson_score_components"
    assert [kind for kind, _value in operations] == [
        "execute",
        "execute",
        "execute",
        "create_check_constraint",
        "execute",
    ]
    preflight = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in preflight
    assert migration.OUTBOX_TRIGGER in preflight
    assert "tgenabled <> 'D'" in preflight
    cleanup = str(operations[1][1])
    assert "UPDATE public.lesson_source_wide" in cleanup
    assert 'SET "cpu占用过高" = NULL' in cleanup
    assert '"网络延迟过高" = NULL' in cleanup
    reconciliation = str(operations[2][1])
    assert "FROM public.teacher_source_wide AS source" in reconciliation
    assert "feedback_favorite_cnt" in reconciliation
    assert "ON CONFLICT DO NOTHING" in reconciliation
    assert "real_name" not in reconciliation
    check_args, check_kwargs = operations[3][1]
    assert check_args == (
        migration.CONSTRAINT_NAME,
        "lesson_source_wide",
        '"cpu占用过高" IS NULL AND "网络延迟过高" IS NULL',
    )
    assert check_kwargs == {"schema": "public"}
    installed = str(operations[4][1])
    assert "CPU_NETWORK_RETIREMENT_NON_NULL_VALUE_REMAINS" in installed
    assert "CPU_NETWORK_RETIREMENT_RECONCILIATION_OUTBOX_CONFLICT" in installed
    assert "convalidated" in installed
    assert migration.OUTBOX_TRIGGER in installed


def test_revision_77_downgrade_only_removes_the_temporary_lock(
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
        "drop_constraint",
        lambda *args, **kwargs: operations.append(
            ("drop_constraint", (args, kwargs))
        ),
    )

    migration.downgrade()

    assert [kind for kind, _value in operations] == [
        "execute",
        "drop_constraint",
    ]
    guard = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in guard
    assert "UPDATE public.lesson_source_wide" not in guard
    assert operations[1] == (
        "drop_constraint",
        (
            (migration.CONSTRAINT_NAME, "lesson_source_wide"),
            {"schema": "public", "type_": "check"},
        ),
    )


def test_lesson_source_orm_metadata_declares_the_same_null_only_contract() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in LessonSourceWideRecord.__table__.constraints
        if constraint.name
    }

    assert checks["ck_lesson_source_cpu_network_retired_v1"] == (
        '"cpu占用过高" IS NULL AND "网络延迟过高" IS NULL'
    )
