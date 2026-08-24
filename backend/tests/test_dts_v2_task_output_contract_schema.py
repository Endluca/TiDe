from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_91_task_output_contract.py"
)


def test_revision_91_installs_the_typed_task_output_contract() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260822_91_task_output_contract"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_90_completion_correction"'
    ) in source
    for function_name in (
        "reconcile_course_trigger_matches_v2",
        "reconcile_blacklist_threshold_v2",
        "materialize_task_plan_v2",
        "rebuild_task_plan_v2",
    ):
        assert function_name in source
    for field_name in (
        "plan_evidence",
        "plan_evidence_hash",
        "output_key",
        "source_ref",
        "task_assignment_id",
        "ops_case_id",
        "notification_id",
        "eligible_since_at",
        "teacher_copy_config_key",
        "materialization_seed_payload_hash",
    ):
        assert field_name in source
    assert "teacher_personalized_copy" in source
    assert "uq_config_version_identity_key" in source
    assert "DROP INDEX IF EXISTS public." in source
    assert "chr(0)" not in source


def test_revision_91_keeps_runtime_writes_behind_commands() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in source
    assert (
        "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER\n"
        "          ON TABLE public.personalized_trigger_matches"
    ) in source
    assert (
        "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER\n"
        "          ON TABLE public.task_assignments"
    ) in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "INSERT INTO public.config_versions" not in source
