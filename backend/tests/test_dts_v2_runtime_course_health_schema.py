from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_92_runtime_course_health.py"
)


def test_revision_92_installs_protected_course_and_fixed_health_surface() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260822_92_runtime_course_health"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_91_task_output_contract"'
    ) in source
    assert "materialize_course_source_wide_v2" in source
    assert "dts_v2_runtime_primary_guard_v1('COURSE')" in source
    for name in (
        "dts_v2_domain_runtime_health_v1",
        "dts_v2_outbox_runtime_health_v1",
        "dts_v2_favorite_runtime_health_v1",
    ):
        assert name in source
    for field in (
        "protocol_version",
        "mode",
        "projection_generation",
        "runnable_count",
        "active_lease_count",
        "expired_lease_count",
        "business_wait_count",
        "dead_count",
        "stale_runnable_count",
        "oldest_runnable_age_seconds",
        "oldest_active_lease_age_seconds",
    ):
        assert field in source


def test_revision_92_keeps_runtime_roles_without_raw_dml_or_control_read() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in source
    assert "DTS_V2_COURSE_MATERIALIZER_CALLER_FORBIDDEN" in source
    assert "DTS_V2_COURSE_MATERIALIZATION_HASH_MISMATCH" in source
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER ON TABLE" in source
    assert "REVOKE SELECT ON TABLE public.dts_pipeline_control" in source
    assert "GRANT EXECUTE ON FUNCTION {COURSE_COMMAND}" in source
    assert "GRANT INSERT ON TABLE public.lesson_source_wide" not in source
    assert "GRANT INSERT ON TABLE public.score_entries" not in source
