from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app import auth_models, db_models


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260807_49_remove_unused_columns.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "remove_unused_columns_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_orm_omits_recoverable_duplicate_columns() -> None:
    complaint_columns = db_models.ComplaintCategoryRuleRecord.__table__.columns
    session_columns = auth_models.OperatorSession.__table__.columns

    assert "learning_title" not in complaint_columns
    assert "learning_url" not in complaint_columns
    assert "last_seen_at" not in session_columns


def test_revision_49_is_locked_lossless_and_never_cascades() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "20260807_48_schema_cleanup" in source
    assert "IN ACCESS EXCLUSIVE MODE" in source
    assert "last_seen_at IS DISTINCT FROM created_at" in source
    assert "complaint rule cannot be losslessly restored from raw_rows" in source
    assert "source_row_number" in source
    assert "Course Title in the Learning Hub" in source
    assert "normalize(CASE" in source
    assert "learning_title =" in source
    assert "learning_url =" in source
    assert "SET last_seen_at = created_at" in source
    assert "DROP COLUMN CASCADE" not in source.upper()


def test_revision_49_is_postgresql_only(monkeypatch) -> None:
    migration = _migration_module()
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    monkeypatch.setattr(
        migration,
        "_guard_upgrade",
        lambda: (_ for _ in ()).throw(AssertionError("must be a no-op")),
    )
    monkeypatch.setattr(
        migration,
        "_guard_downgrade",
        lambda: (_ for _ in ()).throw(AssertionError("must be a no-op")),
    )

    migration.upgrade()
    migration.downgrade()
