from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.database import Base
from app import auth_models, config_models, db_models  # noqa: F401


MIGRATION_42_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_42_runtime_acl_effective.py"
)
MIGRATION_43_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_43_source_results.py"
)


def _migration_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_effective_acl_covers_every_root_table_and_all_score_views() -> None:
    migration = _migration_module(
        MIGRATION_42_PATH,
        "runtime_acl_effective_migration",
    )
    source_results = _migration_module(
        MIGRATION_43_PATH,
        "source_results_migration_for_acl",
    )

    assert len(migration.revision) <= 32
    assert source_results.down_revision == migration.revision
    managed = set(migration.MANAGED_RELATIONS) | {
        "lesson_score_results",
        "teacher_qualifications",
        "complaint_rule_imports",
    }
    assert {table.name for table in Base.metadata.sorted_tables} <= managed
    assert set(migration.ROOT_RUNTIME_NO_ACCESS_VIEWS) == {
        "teacher_scorecard_current",
        "teacher_lesson_score_current",
    }


def test_effective_acl_removes_public_and_legacy_view_grants() -> None:
    source = MIGRATION_42_PATH.read_text(encoding="utf-8")

    assert "FROM PUBLIC" in source
    assert "FROM tit_growth_app" in source
    assert "aclexplode" in source
    assert "privileges.grantee = 0" in source
    assert "has_table_privilege" in source
    assert "Public grants are intentionally not recreated" in source
    assert "GRANT ALL" not in source.upper()
