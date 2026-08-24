from __future__ import annotations

from pathlib import Path

from app.db_models import LessonScoreResultRecord, SourceCourseParticipationRecord


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260822_88_dts_v2_score_projection.py"
)


def test_revision_88_is_additive_and_follows_favorite_runtime() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_88_v2_score_projection"' in source
    assert 'down_revision: Union[str, None] = "20260822_87_favorite_runtime"' in source
    assert "CREATE VIEW public.teacher_scorecard_v1_compat_v1" in source
    assert "CREATE VIEW public.teacher_scorecard_v2_v1" in source
    assert "CREATE VIEW public.teacher_lesson_score_v1_compat_v1" in source
    assert "CREATE VIEW public.teacher_lesson_score_v2_v1" in source
    assert "CREATE OR REPLACE VIEW public.teacher_scorecard_current" not in source
    assert "CREATE OR REPLACE VIEW public.teacher_lesson_score_current" not in source


def test_revision_88_installs_generation_and_ledger_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "v2_teacher_id" in source
    assert "v2_projection_generation" in source
    assert "fk_lesson_score_result_v2_score_owner" in source
    assert "DEFERRABLE" not in source or "deferrable=True" in source
    assert "teacher_score_projection_vector_v2" in source
    assert "rebuild_lesson_score_result_v2" in source
    assert "rebuild_teacher_score_and_qualification_v2" in source
    assert "DTS_V2_SCORE_PROJECTION_VECTOR_STALE" in source
    assert "DTS_V2_SCORE_LEDGER_COMPONENT_UNMAPPED" in source
    assert "DTS_V2_SCORE_ACCOUNT_LEDGER_MISMATCH" in source
    assert "least(raw_total,200)" in source
    assert "total_score=raw_total::double precision" in source
    assert "total_score=least(raw_total,200)" not in source
    assert "LEFT/BLOCKED" not in source  # status never gates ledger aggregation


def test_revision_88_qualification_keeps_all_hard_evidence_gates() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "TASK_BASELINE_INCOMPLETE" in source
    assert "mandatory_completed=9" in source
    assert "complaint_evidence_complete" in source
    assert "l0_complaint_count=0" in source
    assert "attendance_evidence_complete" in source
    assert "wide_row.late_cnt<=1" in source
    assert "wide_row.early_cnt=0" in source
    assert "wide_row.absent_cnt=0" in source
    assert "graduation_score_locked" in source
    assert "control_row.qualification_grants_enabled" in source
    assert (
        "control_row.qualification_grants_enabled\n"
        "              AND graduation_criteria" in source
    )
    assert (
        "control_row.qualification_grants_enabled AND gold_criteria"
        in source
    )
    assert "'qualification_grants_enabled'" in source
    assert "coalesce(\n              public.teacher_qualifications.gold_qualified_at" in source


def test_revision_88_does_not_reactivate_retired_cpu_network_sources() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "hardware_evidence text := 'NOT_APPLICABLE'" in source
    assert "NULL::boolean AS hardware_quality_passed" in source
    assert "cpu_evidence_status='CONFIRMED'" not in source
    assert "network_evidence_status='CONFIRMED'" not in source


def test_revision_88_acl_uses_the_shared_application_runtime() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "TO {OUTBOX_RUNTIME_ROLE}" in source
    assert 'OUTBOX_RUNTIME_ROLE = "tit_growth_app"' in source
    assert "GRANT SELECT ON TABLE {view_list}" in source
    assert "DTS_V2_SCORE_GROWTH_EXECUTE_LEAK" not in source


def test_orm_maps_exact_v2_score_owner_group() -> None:
    result_columns = LessonScoreResultRecord.__table__.c
    assert "v2_teacher_id" in result_columns
    assert "v2_projection_generation" in result_columns

    result_constraints = {
        constraint.name for constraint in LessonScoreResultRecord.__table__.constraints
    }
    participation_constraints = {
        constraint.name
        for constraint in SourceCourseParticipationRecord.__table__.constraints
    }
    assert "fk_lesson_score_result_v2_score_owner" in result_constraints
    assert "ck_lesson_score_result_v2_ownership" in result_constraints
    assert (
        "uq_source_course_participation_score_owner_v2"
        in participation_constraints
    )
