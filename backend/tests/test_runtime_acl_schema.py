from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.database import Base
from app import auth_models, config_models, db_models  # noqa: F401


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_40_runtime_acl.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_acl_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_runtime_table_has_one_explicit_privilege_class() -> None:
    migration = _migration_module()
    groups = (
        migration.NO_ACCESS_TABLES,
        migration.READ_ONLY_TABLES,
        migration.READ_INSERT_TABLES,
        migration.INSERT_ONLY_TABLES,
        migration.READ_UPDATE_TABLES,
        migration.READ_INSERT_UPDATE_TABLES,
        migration.READ_INSERT_DELETE_TABLES,
        migration.READ_INSERT_UPDATE_DELETE_TABLES,
        ("operator_accounts",),
    )
    flattened = [table for group in groups for table in group]

    assert len(flattened) == len(set(flattened))
    # Revision 40 classifies the schema that existed at that revision.  The
    # two source-derived tables are introduced and classified by revision 43;
    # do not rewrite historical migrations when the ORM grows.
    retired_after_revision_40 = {
        "agent_decisions",
        "data_import_batches",
        "lesson_dimension_scores",
        "lesson_facts",
        "outbound_outputs",
        "provider_calls",
        "source_records",
        "teacher_metric_snapshots",
    }
    introduced_after_revision_40 = {
        "complaint_rule_imports",
        "dts_ingest_checkpoints",
        "dts_ingest_events",
        "dts_source_rows",
        "dts_dirty_keys",
        "lesson_score_results",
        "teacher_qualifications",
    }
    assert set(flattened) == {
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in introduced_after_revision_40
    } | retired_after_revision_40


def test_source_tables_are_read_only_and_acl_does_not_use_broad_grants() -> None:
    migration = _migration_module()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert set(migration.READ_ONLY_TABLES) == {
        "teacher_source_wide",
        "lesson_source_wide",
        "lesson_facts",
        "complaint_category_rules",
        "personalized_trigger_matches",
        "task_assignments",
        "operator_role_grants",
    }
    assert set(migration.NO_ACCESS_TABLES) == {
        "data_import_batches",
        "source_records",
        "agent_decisions",
        "provider_calls",
        "notification_events",
    }
    assert set(migration.READ_UPDATE_TABLES) == {
        "teachers",
        "teacher_metric_snapshots",
        "notifications",
        "ops_cases",
        "outbound_outputs",
    }
    assert "GRANT ALL" not in source.upper()
    assert "ON ALL TABLES" not in source.upper()
    assert "ALTER DEFAULT PRIVILEGES" not in source.upper()
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC, tit_growth_app" in source
    assert "GRANT UPDATE (password_hash)" in source
    assert "USAGE, SELECT ON SEQUENCE" not in source
    assert "public.audit_events_sequence_seq" in source


def test_runtime_role_guard_rejects_elevated_role_attributes() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for attribute in (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    ):
        assert attribute in source

    assert "pg_auth_members" in source
    assert "must not own objects in schema public" in source
    assert "has_schema_privilege" in source


def test_downgrade_restores_pre_revision_code_managed_grants() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "public.teacher_source_wide" in source
    assert "public.lesson_source_wide" in source
    assert "public.teacher_scorecard_current" in source
    assert "public.teacher_lesson_score_current" in source
    assert "public.score_component_accounts" in source
