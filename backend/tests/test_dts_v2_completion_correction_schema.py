from __future__ import annotations

from pathlib import Path

from app.db_models import (
    CompletionCorrectionPointerUpgradeArchiveRecord,
    SourceCourseRecord,
)


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260822_90_completion_correction.py"
)


def test_revision_90_follows_teacher_materializer_and_is_mode_aware() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_90_completion_correction"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_89_teacher_materializer"'
    ) in source
    assert "control_mode='V1_COMPAT_DUAL_CAPTURE'" in source
    assert "control_mode='ROLLED_BACK'" in source
    assert "control_mode<>'V2_PRIMARY'" in source
    assert "'outcome','SHADOW_ONLY'" in source
    assert "DTS_V2_ROLLBACK_COMPLETION_POINTER_INVALID" in source


def test_revision_90_installs_the_two_protected_command_surfaces() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "reconcile_completion_conflict_case_v2(" in source
    assert "apply_completion_correction_decision_v2(" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,public" in source
    assert "DTS_V2_COMPLETION_CORRECTION_IDEMPOTENCY_CONFLICT" in source
    assert "STALE_CASE_REVISION" in source
    assert "version_row.source_position IS DISTINCT FROM request_position" in source
    assert "current_source.source_position_v2 IS DISTINCT FROM" in source
    assert "CORRECTION_PROJECTION_MAINTENANCE" in source
    assert "FROM PUBLIC,tit_dts_ingest_runtime" in source
    assert "TO tit_growth_app;" in source
    assert "TO tit_growth_app" in source


def test_revision_90_correction_is_atomic_and_does_not_emit_general_actions() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    for decision in (
        "KEEP_FROZEN_COMPLETION",
        "UPDATE_COMPLETION_SNAPSHOT",
        "TRANSFER_COMPLETION",
        "VOID_COMPLETION",
    ):
        assert decision in source
    assert "reverse_lesson_components_for_correction_v2" in source
    assert "reconcile_favorite_attribution_v2" in source
    assert "materialize_favorite_observation_v2" in source
    assert "rebuild_lesson_score_result_v2" in source
    assert "rebuild_teacher_score_and_qualification_v2" in source
    assert "publish_domain_aggregate_revision_v2" in source
    assert "'COURSE'" in source
    assert "'PARTICIPATION'" in source
    assert "APPLIED_PENDING_PROJECTION" in source
    assert "INSERT INTO public.task_assignments" not in source
    assert "INSERT INTO public.notification" not in source


def test_course_pointer_and_upgrade_archive_match_migration_contract() -> None:
    course = SourceCourseRecord.__table__
    archive = CompletionCorrectionPointerUpgradeArchiveRecord.__table__

    assert course.c.completion_conflict_case_id.type.length == 768
    assert "fk_source_course_completion_conflict_case_v2" in {
        constraint.name for constraint in course.constraints
    }
    assert archive.comment == (
        "Owner-only rollback evidence for compatibility pointers cleared "
        "when rev90 establishes mode-aware Case ownership."
    )
    assert archive.c.prior_case_id.type.length == 768


def test_normal_projector_no_longer_owns_visible_case_pointer() -> None:
    projector = (
        BACKEND / "app" / "dts_v2_course_projector.py"
    ).read_text(encoding="utf-8")
    owned_block = projector.split("_COURSE_OWNED_COLUMNS = (", 1)[1].split(
        ")", 1
    )[0]

    assert "completion_conflict_case_id" not in owned_block
    assert '"completion_conflict_case_id": state.correction_case_key' not in projector
