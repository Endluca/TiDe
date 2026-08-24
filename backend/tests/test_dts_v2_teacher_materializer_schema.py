from __future__ import annotations

from pathlib import Path

from app.db_models import (
    ScoreEntryIdempotencyAliasRecord,
    SourceCourseParticipationRecord,
    SourceCourseRecord,
    TeacherSourceWideRecord,
)


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260822_89_teacher_materializer.py"
)


def test_revision_89_follows_score_projection_without_route_cutover() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_89_teacher_materializer"' in source
    assert 'down_revision: Union[str, None] = "20260822_88a_runtime_primary_guard"' in source
    assert "CREATE OR REPLACE VIEW public.teacher_scorecard_current" not in source
    assert "CREATE OR REPLACE VIEW public.teacher_lesson_score_current" not in source


def test_revision_89_installs_regional_vector_history_and_capacity_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    for name in (
        "first_open_slot_evidence_status",
        "first_booked_evidence_status",
        "first_completed_evidence_status",
        "v2_dom_aggregate_revision",
        "v2_ovs_aggregate_revision",
        "v2_projection_generation",
        "v2_materialized_event_id",
        "v2_row_version",
    ):
        assert name in source
    assert "materialize_teacher_source_wide_v2" in source
    assert "dts_v2_runtime_primary_guard_v1('TEACHER')" in source
    assert "DTS_V2_TEACHER_REGIONAL_VECTOR_NONDETERMINISTIC" in source
    assert "ix_dts_source_row_versions_schedule_teacher_after_v2" in source
    assert "score_entry_idempotency_aliases" in source
    assert "CAPACITY_MILESTONE_DEDUPE_CONFLICT" in source
    assert "SCORE_V2_CAPACITY_IDENTITY_INVALID" in source
    assert "DTS_V2_ABSENCE_PROVENANCE_BACKFILL_REQUIRED" in source
    assert "ck_source_course_participation_absence_provenance_v2" in source
    assert "ck_source_course_participation_teacher_region_v2" in source
    assert "ck_source_course_combined_evidence_v2" in source


def test_revision_89_keeps_runtime_dml_behind_one_protected_command() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in source
    assert "TO {OUTBOX_RUNTIME_ROLE}" in source
    assert "REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide" in source
    assert "GRANT SELECT ON TABLE public.teacher_source_wide" in source
    assert "GRANT INSERT ON TABLE public.teacher_source_wide" not in source
    assert "GRANT UPDATE ON TABLE public.teacher_source_wide" not in source
    assert "GRANT SELECT ON TABLE public.dts_pipeline_control" not in source


def test_teacher_source_wide_and_alias_orm_match_new_relations() -> None:
    columns = TeacherSourceWideRecord.__table__.c
    for name in (
        "first_open_slot_evidence_status",
        "first_booked_evidence_status",
        "first_completed_evidence_status",
        "v2_dom_aggregate_revision",
        "v2_ovs_aggregate_revision",
        "v2_projection_generation",
        "v2_materialized_event_id",
        "v2_materialized_at",
        "v2_row_version",
    ):
        assert name in columns
    assert (
        ScoreEntryIdempotencyAliasRecord.__tablename__
        == "score_entry_idempotency_aliases"
    )


def test_course_orm_retains_absence_and_teacher_region_provenance() -> None:
    course = SourceCourseRecord.__table__
    participation = SourceCourseParticipationRecord.__table__
    assert {
        "appoint_evidence_status",
        "teacher_region_evidence_status",
    }.issubset(course.c.keys())
    assert {
        "absence_source_id",
        "absence_source_id_type",
        "absence_source_row_revision",
        "absence_selected_reason_type",
        "teacher_expected_source_region",
        "teacher_region_evidence_status",
        "teacher_profile_source_row_revision",
        "teacher_profile_source_payload_hash",
    }.issubset(participation.c.keys())
    assert {
        constraint.name for constraint in course.constraints
    }.issuperset(
        {
            "ck_source_course_region_evidence_status_v2",
            "ck_source_course_combined_evidence_v2",
        }
    )
    assert {
        constraint.name for constraint in participation.constraints
    }.issuperset(
        {
            "ck_source_course_participation_absence_provenance_v2",
            "ck_source_course_participation_teacher_region_v2",
        }
    )
