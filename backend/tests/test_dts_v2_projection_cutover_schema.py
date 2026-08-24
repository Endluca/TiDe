from __future__ import annotations

from pathlib import Path

from app.db_models import (
    DtsProjectionReadRouteRecord,
    DtsProjectionSwitchAuditRecord,
    DtsSourceProfileApprovalV2Record,
    DtsV2ReconciliationRunRecord,
)


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_95_projection_cutover.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_95_follows_complaint_catalog_and_installs_stable_route() -> None:
    source = _source()

    assert 'revision: str = "20260822_95_projection_cutover"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_94_complaint_catalog_fanout"'
    ) in source
    assert "dts_projection_read_routes" in source
    assert "CREATE VIEW public.teacher_scorecard_current" in source
    assert "CREATE VIEW public.teacher_lesson_score_current" in source
    assert "WITH route AS MATERIALIZED" in source
    assert "CREATE OR REPLACE VIEW public.teacher_scorecard_current" not in source
    assert "CREATE OR REPLACE VIEW public.teacher_lesson_score_current" not in source


def test_cutover_is_database_verified_and_missing_providers_are_unavailable() -> None:
    source = _source()

    for fragment in (
        "dts_v2_full_reconciliation_manifest_v1(timestamptz)",
        "DTS_V2_FULL_RECONCILIATION_UNAVAILABLE",
        "dts_v1_compat_dirty_not_complete_count_v1()",
        "compat_provider text:='UNAVAILABLE'",
        "full_provider text:='UNAVAILABLE'",
        "public.dts_v2_technical_gate_passes_v1",
        "source_profile_approvals_v2",
        "DTS_SOURCE_PROFILE_DEFAULT_APPROVAL_FORBIDDEN",
    ):
        assert fragment in source
    assert "operator-supplied hashes alone are never trusted" in source


def test_exact_fourteen_result_registry_and_legacy_output_blockers_are_present() -> None:
    source = _source()

    for result_type in (
        "TEACHER_WIDE",
        "LESSON_SCORE",
        "LESSON_COMPONENT_SETTLEMENT",
        "FAVORITE_OBSERVATION",
        "FAVORITE_ATTRIBUTION",
        "TRIGGER_MATCH",
        "TASK_PLAN",
        "CASE_PLAN",
        "NOTIFICATION_PLAN",
        "SCORE_ACCOUNT",
        "SCORE_COMPONENT_ACCOUNT",
        "SCORE_ENTRY_PLAN",
        "QUALIFICATION",
        "OUTBOX_COVERAGE",
    ):
        assert result_type in source
    for blocker in (
        "missing_count",
        "conflict_count",
        "duplicate_count",
        "legacy_output_issue_count",
    ):
        assert blocker in source


def test_switch_is_protected_cas_audited_and_rollback_is_non_destructive() -> None:
    source = _source()

    assert "CREATE FUNCTION public.switch_dts_projection_mode_v2(" in source
    assert "SECURITY DEFINER" in source
    assert "pg_advisory_xact_lock(" in source
    assert "p_expected_control_version" in source
    assert "p_expected_route_version" in source
    assert "dts_projection_switch_audits" in source
    assert "'status','REPLAYED'" in source
    assert "SET mode='ROLLED_BACK'" in source
    rollback_body = source.split("ELSE\n            IF control_row.mode<>'V2_PRIMARY'", 1)[1]
    rollback_body = rollback_body.split("target_projection:='V1_COMPAT'", 1)[1]
    rollback_body = rollback_body.split("ELSE\n            target_projection", 1)[0]
    assert "DELETE FROM" not in rollback_body
    assert "TRUNCATE" not in rollback_body


def test_stable_lesson_route_uses_triple_identity_without_lesson_id_alias() -> None:
    source = _source()

    assert "source_region + source_appoint_id + participation_seq" in source
    assert "column_name='lesson_id'" in source  # explicit negative assertion
    stable_view = source.split(
        "CREATE VIEW public.teacher_lesson_score_current AS", 1
    )[1].split("COMMENT ON VIEW", 1)[0]
    assert "lesson_id" not in stable_view
    for normalized_key in (
        "has_positive_feedback_tag",
        "has_negative_feedback_tag",
        "is_favorited",
        "is_rebooked",
        "favorite_attribution_status",
        "evidence_status",
        "evidence_coverage",
        "components",
        "NOT_APPLICABLE",
    ):
        assert normalized_key in stable_view


def test_orm_contains_all_four_immutable_cutover_facts() -> None:
    assert DtsProjectionReadRouteRecord.__tablename__ == (
        "dts_projection_read_routes"
    )
    assert DtsSourceProfileApprovalV2Record.__tablename__ == (
        "dts_source_profile_approvals_v2"
    )
    assert DtsV2ReconciliationRunRecord.__tablename__ == (
        "dts_v2_reconciliation_runs"
    )
    assert DtsProjectionSwitchAuditRecord.__tablename__ == (
        "dts_projection_switch_audits"
    )
