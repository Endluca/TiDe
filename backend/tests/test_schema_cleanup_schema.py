from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app import db_models
from app.database import Base


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260807_48_schema_cleanup.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "schema_cleanup_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(model: type) -> set[str]:
    return set(model.__table__.columns.keys())


def test_runtime_orm_contains_only_the_specialized_import_store() -> None:
    assert _columns(db_models.ComplaintRuleImportRecord) == {
        "source_sha256",
        "source_filename",
        "raw_rows",
        "imported_at",
    }
    for model_name in (
        "DataImportBatchRecord",
        "SourceRecord",
        "AgentDecisionRecord",
        "ProviderCallRecord",
        "OutboundOutputRecord",
    ):
        assert not hasattr(db_models, model_name)
    for table_name in (
        "data_import_batches",
        "source_records",
        "agent_decisions",
        "provider_calls",
        "outbound_outputs",
    ):
        assert table_name not in Base.metadata.tables


def test_redundant_projection_columns_are_absent_and_keys_are_natural() -> None:
    assert "source_batch_id" not in _columns(db_models.TeacherRecord)
    assert {
        "source_record_id",
        "scope_key",
        "created_at",
    }.isdisjoint(_columns(db_models.PersonalizedTriggerMatchRecord))
    assert {
        "batch_id",
        "source_sheet",
        "normalized_level",
        "raw_payload",
    }.isdisjoint(_columns(db_models.ComplaintCategoryRuleRecord))

    score_accounts = db_models.ScoreAccountRecord.__table__
    assert [column.name for column in score_accounts.primary_key.columns] == [
        "teacher_id",
        "dimension",
    ]
    assert {
        "account_id",
        "camp_enrollment_id",
        "minimum_score",
        "weight",
    }.isdisjoint(score_accounts.columns.keys())

    components = db_models.ScoreComponentAccountRecord.__table__
    assert [column.name for column in components.primary_key.columns] == [
        "teacher_id",
        "component_code",
    ]
    assert {
        "component_account_id",
        "camp_enrollment_id",
        "source_teacher_batch_id",
        "source_lesson_batch_id",
    }.isdisjoint(components.columns.keys())
    assert {index.name for index in components.indexes} == {
        "ix_score_component_account_teacher_dimension"
    }


def test_revision_48_is_locked_gated_and_never_cascades() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "20260807_47_legacy_drop" in source
    assert "LOCK TABLE" in source
    assert "IN ACCESS EXCLUSIVE MODE" in source
    assert "refusing to drop populated retired stores" in source
    assert "data_import_batches contains a non-complaint" in source
    assert "score account contains non-redundant legacy values" in source
    assert "score component contains non-redundant legacy values" in source
    assert "personalized trigger match contains non-redundant legacy values" in source
    assert "downgrade is structural-only after complaint imports" in source
    assert "pg_rewrite" in source
    assert "pg_constraint" in source
    assert "pg_proc" in source
    assert "pg_trigger" in source
    assert "pg_inherits" in source
    assert "pg_publication_rel" in source
    assert "DROP TABLE CASCADE" not in source.upper()
    assert "DROP COLUMN CASCADE" not in source.upper()
    assert 'op.drop_table("source_records", schema="public")' in source
    assert "op.drop_table(table_name, schema=\"public\")" in source
    assert "EMPTY_RETIRED_TABLES" in source


def test_revision_48_is_postgresql_only(monkeypatch) -> None:
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
    migration.upgrade()
    migration.downgrade()
