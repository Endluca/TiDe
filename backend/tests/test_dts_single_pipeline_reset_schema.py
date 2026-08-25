from __future__ import annotations

from pathlib import Path
import re

from app.db_models import DtsPipelineResetAuditRecord
from test_mr60_dms_sql_postgres import _split_dms_onequery_statements


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260824_101_dts_single_pipeline_reset.py"
)
THROUGHPUT_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260825_102_dts_ingest_batch_throughput.py"
)
TIME_RECHECK_RESEED_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260825_103_reseed_time_recheck_schedule.py"
)
RUNTIME_TABLE_ACL_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260825_104_runtime_table_acl.py"
)
RUNTIME_PIPELINE_READ_ACL_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260825_105_runtime_pipeline_read_acl.py"
)
PUBLIC_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260824_public100_to_101_single_pipeline_reset.sql"
)
PUBLIC_65_100_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260824_public65_to_100_dts_domain_schema.sql"
)
DOM_PRIVACY_ACL_HOTFIX_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260824_public101_dom_privacy_read_acl_hotfix.sql"
)
THROUGHPUT_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260825_public101_to_102_dts_ingest_batch_throughput.sql"
)
TIME_RECHECK_RESEED_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260825_public102_to_103_reseed_time_recheck_schedule.sql"
)
RUNTIME_TABLE_ACL_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260825_public102_or_103_to_104_runtime_table_acl.sql"
)
RUNTIME_PIPELINE_READ_ACL_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260825_public104_to_105_runtime_pipeline_read_acl.sql"
)
TEACHER_DMS = (
    ROOT
    / "migrations"
    / "dms"
    / "20260824_teacher0042_to_0043_p_rel_execution_catalog.sql"
)
TEACHER_CANONICAL = (
    ROOT.parent
    / "teacher"
    / "backend"
    / "database"
    / "migrations"
    / "0043_p_rel_execution_catalog.up.sql"
)


def _assert_dms_onequery_compatible(source: str) -> None:
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*\$", source)
    assert not re.search(r";;[ \t]*$", source, re.MULTILINE)
    assert not re.search(
        r"\b(?:CREATE|ALTER|DROP)\s+ROLE\b|\bCOMMENT\s+ON\s+ROLE\b",
        source,
        re.IGNORECASE,
    )
    statements = _split_dms_onequery_statements(source)
    assert statements[0].startswith("--")
    assert any("BEGIN;" in statement for statement in statements[:2])
    assert any(statement == "COMMIT;" for statement in statements)


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


def test_formal_public_dms_is_pinned_and_fail_closed() -> None:
    source = PUBLIC_DMS.read_text(encoding="utf-8")
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "flow/release@2881507" in source
    assert "TIT_DTS_START_AT=2026-08-18T00:00:00+08:00" in source
    assert "dom-single-20260818-2881507-01" in source
    assert "ovs-single-20260818-2881507-01" in source
    assert "public rev101 DMS requires public head 100" in source
    assert "exact 38-row teacher ledger ending at 0043" in source
    assert "READY_SINGLE_PIPELINE" in source
    assert "UPDATE alembic_version SET version_num=" in source
    assert "public.dts_dirty_keys" in source
    assert "public.lesson_source_wide" in source
    assert "GRANT SELECT ON TABLE" in source
    _assert_dms_onequery_compatible(source)


def test_dom_privacy_acl_hotfix_is_single_statement_and_read_only() -> None:
    source = DOM_PRIVACY_ACL_HOTFIX_DMS.read_text(encoding="utf-8")
    statements = _split_dms_onequery_statements(source)
    assert len(statements) == 1
    assert "20260824_101_dts_single_pipeline_reset" in source
    assert "GRANT SELECT ON TABLE" in source
    assert "public.dts_dirty_keys" in source
    assert "public.lesson_source_wide" in source
    assert "INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER" in source
    assert not re.search(
        r"\b(?:CREATE|ALTER|DROP)\s+ROLE\b|\bCOMMENT\s+ON\s+ROLE\b",
        source,
        re.IGNORECASE,
    )


def test_batch_throughput_migration_and_dms_are_single_pipeline_only() -> None:
    migration = THROUGHPUT_MIGRATION.read_text(encoding="utf-8")
    source = THROUGHPUT_DMS.read_text(encoding="utf-8")
    assert 'revision: str = "20260825_102_dts_ingest_batch_throughput"' in migration
    assert "enqueue_dirty_from_source_revisions_batch_v3" in migration
    assert "dts_active_source_scope_tables_v1" in migration
    assert "DROP TRIGGER IF EXISTS trg_sync_v1_compat_dirty_input_v1" in migration
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "public rev102 DMS requires public head 101" in source
    assert "exact 38-row teacher ledger ending at 0043" in source
    assert "dts_rev102_fact_fence" in source
    assert "public rev102 postflight verification failed" in source
    assert (
        "UPDATE alembic_version SET "
        "version_num='20260825_102_dts_ingest_batch_throughput'" in source
    )
    _assert_dms_onequery_compatible(source)


def test_time_recheck_schedule_is_reseeded_after_destructive_reset() -> None:
    migration = TIME_RECHECK_RESEED_MIGRATION.read_text(encoding="utf-8")
    source = TIME_RECHECK_RESEED_DMS.read_text(encoding="utf-8")
    assert (
        'revision: str = "20260825_103_reseed_time_recheck_schedule"'
        in migration
    )
    assert "20260825_102_dts_ingest_batch_throughput" in migration
    assert "INSERT INTO public.dts_teacher_time_recheck_schedule" in migration
    assert "ON CONFLICT (schedule_id) DO NOTHING" in migration
    assert "DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED" in migration
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "public rev103 DMS requires public head 102" in source
    assert "public rev103 postflight verification failed" in source
    assert (
        "version_num='20260825_103_reseed_time_recheck_schedule'" in source
    )
    _assert_dms_onequery_compatible(source)


def test_runtime_outbox_acl_is_table_level_and_trigger_guarded() -> None:
    migration = RUNTIME_TABLE_ACL_MIGRATION.read_text(encoding="utf-8")
    source = RUNTIME_TABLE_ACL_DMS.read_text(encoding="utf-8")
    assert 'revision: str = "20260825_104_runtime_table_acl"' in migration
    assert "20260825_103_reseed_time_recheck_schedule" in migration
    assert "REVOKE ALL PRIVILEGES (%I) ON TABLE" in migration
    assert "GRANT SELECT,INSERT,UPDATE,DELETE" in migration
    assert "guard_outbox_event_update" in migration
    assert "DTS_V2_OUTBOX_TABLE_ACL_INVALID" in migration
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "public rev104 DMS requires public head 102 or 103" in source
    assert "public rev104 postflight verification failed" in source
    assert "GRANT SELECT,INSERT,UPDATE,DELETE" in source
    assert "REVOKE TRUNCATE,REFERENCES,TRIGGER" in source
    assert "version_num='20260825_104_runtime_table_acl'" in source
    _assert_dms_onequery_compatible(source)


def test_runtime_pipeline_control_is_table_level_read_only() -> None:
    migration = RUNTIME_PIPELINE_READ_ACL_MIGRATION.read_text(
        encoding="utf-8"
    )
    source = RUNTIME_PIPELINE_READ_ACL_DMS.read_text(encoding="utf-8")
    assert (
        'revision: str = "20260825_105_pipeline_read_acl"'
        in migration
    )
    assert "20260825_104_runtime_table_acl" in migration
    assert "GRANT SELECT ON TABLE public.dts_pipeline_control" in migration
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER" in migration
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "public rev105 DMS requires public head 104" in source
    assert "public rev105 postflight verification failed" in source
    assert "version_num='20260825_105_pipeline_read_acl'" in source
    _assert_dms_onequery_compatible(source)


def test_formal_public_65_to_100_dms_matches_observed_heads() -> None:
    source = PUBLIC_65_100_DMS.read_text(encoding="utf-8")
    assert source.count("\nBEGIN;\n") == 1
    assert source.count("\nCOMMIT;\n") == 1
    assert "flow/release@2881507" in source
    assert "public rev65->100 requires public head 65" in source
    assert "exact 37-row teacher ledger ending at 0042" in source
    assert "EARLY_CLEAR_ALL_CONSUMED_HISTORY_FOUND_NO_TABLES" in source
    assert "('public.outbox_events')" in source
    assert "('public.task_assignments')" in source
    assert "('public.teachers')" in source
    assert (
        "UPDATE alembic_version SET "
        "version_num='20260823_100_scope_snapshot_diff'" in source
    )
    assert "public rev65->100 postflight verification failed" in source
    _assert_dms_onequery_compatible(source)


def test_formal_teacher_dms_wraps_exact_canonical_business_sql() -> None:
    wrapper = TEACHER_DMS.read_text(encoding="utf-8")
    canonical = TEACHER_CANONICAL.read_text(encoding="utf-8")
    canonical_body = canonical.removeprefix("BEGIN;\n").removesuffix(
        "\nCOMMIT;\n"
    )
    assert wrapper.count("\nBEGIN;\n") == 1
    assert wrapper.count("\nCOMMIT;\n") == 1
    assert canonical_body in wrapper
    assert "exact 37-row ledger ending at 0042" in wrapper
    assert "'0043_p_rel_execution_catalog',\n    38," in wrapper
    assert (
        "0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf"
        in wrapper
    )
    _assert_dms_onequery_compatible(wrapper)
