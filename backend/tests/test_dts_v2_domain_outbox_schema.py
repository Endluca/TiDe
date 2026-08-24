from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations/versions/20260822_84_dts_v2_domain_outbox.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("dts_v2_domain_outbox", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_and_runtime_boundary_are_explicit() -> None:
    module = _module()
    assert module.revision == "20260822_84_dts_v2_domain_outbox"
    assert module.down_revision == "20260822_83_dts_v2_scope"
    source = MIGRATION.read_text(encoding="utf-8")
    assert "publish_domain_aggregate_revision_v2" in source
    assert "SECURITY DEFINER" in source
    assert "source_wide.changed.v2" in source
    assert "task.materialization.requested.v2" not in source.split(
        "def _install_publisher", 1
    )[1].split("def _apply_domain_runtime_acl", 1)[0]
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER" in source
    assert "GRANT UPDATE (row_version) ON TABLE public.outbox_events" in source


def test_publisher_allocates_revision_and_outbox_in_one_function() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    publisher = source.split("def _install_publisher", 1)[1].split(
        "def _apply_domain_runtime_acl", 1
    )[0]
    assert "FOR UPDATE" in publisher
    assert "pg_advisory_xact_lock" in publisher
    assert "INSERT INTO public.domain_aggregate_revisions" in publisher
    assert "UPDATE public.domain_aggregate_revisions" in publisher
    assert "INSERT INTO public.outbox_events" in publisher
    assert "DTS_V2_DOMAIN_SOURCE_REVISION_REGRESSION" not in publisher
    assert "dts_v2_domain_coverage_valid" in publisher
    assert "DTS_V2_DOMAIN_COVERAGE_TRIGGER_INVALID" in publisher
    assert "DTS_V2_DOMAIN_PUBLISHER_AGGREGATE_TYPE_INVALID" in publisher
    assert "DTS_V2_DOMAIN_OUTBOX_ID_CONFLICT" in publisher
    assert "public.dts_canonical_json_v1(jsonb)" in source
    assert "public.dts_canonical_json_sha256_v1(jsonb)" in source


def test_outbox_hash_and_privacy_are_database_enforced() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "set_outbox_payload_sha256_v2" in source
    assert "guard_v2_outbox_insert_v2" in source
    assert "DTS_V2_OUTBOX_DIRECT_INSERT_FORBIDDEN" in source
    assert "SECURITY INVOKER" in source
    assert "ck_outbox_payload_sha256_v2" in source
    assert "dts_v2_json_has_forbidden_student_key" in source
    assert "'s_id','sid','stu_id','stuid'" in source
    assert "payload_sha256 IS DISTINCT FROM OLD.payload_sha256" in source
    assert "DOM_RAW" not in source
