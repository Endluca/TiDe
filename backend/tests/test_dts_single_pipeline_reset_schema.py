from __future__ import annotations

from pathlib import Path

from app.db_models import DtsPipelineResetAuditRecord


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260824_101_dts_single_pipeline_reset.py"
)


def test_reset_migration_is_destructive_event_only_and_forward_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260824_101_dts_single_pipeline_reset"' in source
    assert "CLEAR_ALL_CONSUMED_HISTORY" in source
    assert "POST_CONFIGURED_START" in source
    assert "TRUNCATE TABLE" in source
    assert "initialize_dts_event_stream_v1" in source
    assert "FIRST_COMMITTED_EVENT_PER_STREAM" in source
    assert "READY_SINGLE_PIPELINE" in source
    assert "bootstrap_dts_v2_primary_fresh_v1" not in source
    assert "V1_COMPAT_DUAL_CAPTURE" not in source
    assert "ROLLED_BACK" not in source
    assert "destructive and forward-only" in source


def test_reset_audit_model_records_scope_without_cutover_fields() -> None:
    table = DtsPipelineResetAuditRecord.__table__
    assert table.name == "dts_pipeline_reset_audits"
    assert set(table.columns.keys()) == {
        "reset_id",
        "source_profile_manifest_sha256",
        "history_policy",
        "event_scope",
        "reset_at",
        "reset_by",
    }
