from __future__ import annotations

import pytest

from app.dts_v2_course_source_wide_plan import (
    DtsV2CourseSourceWidePlanError,
    build_course_source_wide_plan_v2,
)


def _state() -> dict[str, object]:
    return {
        "course": {
            "student_token": "dom:v1:" + "c" * 64,
            "lesson_local_date": "2026-08-20",
            "lesson_local_time": "18:30:00",
            "scheduled_start_at": "2026-08-20T10:30:00+00:00",
            "source_status": "end",
            "is_peak": True,
            "current_teacher_id": "B",
            "current_participation_seq": 2,
            "completion_teacher_id": "B",
            "completion_participation_seq": 2,
            "completion_end_time": "2026-08-20T11:00:00+00:00",
            "completion_student_token": "dom:v1:" + "c" * 64,
            "completion_is_peak": True,
            "completion_lesson_local_date": "2026-08-20",
            "completion_lesson_local_time": "18:30:00",
            "source_is_deleted": False,
            "completion_conflict_status": "NONE",
            "evidence_status": "CONFIRMED",
            "appoint_evidence_status": "CONFIRMED",
            "teacher_region_evidence_status": "CONFIRMED",
            "row_version": 11,
        },
        "course_fact": {
            "current_grading_source_id": "601",
            "current_grading_source_id_type": "NUMERIC",
            "grading_classification": "POSITIVE",
            "grading_evidence_status": "CONFIRMED",
            "negative_score": None,
            "has_complaint": False,
            "has_valid_complaint": False,
            "latest_valid_complaint_id": None,
            "latest_valid_complaint_id_type": None,
            "complaint_evidence_status": "CONFIRMED",
            "latest_category_l1_snapshot": "投诉",
            "latest_category_l2_snapshot": "出席问题",
            "latest_category_l3_snapshot": "教师缺席",
            "is_camera_off": False,
            "camera_evidence_status": "CONFIRMED",
            "is_cpu_usage_high": None,
            "cpu_evidence_status": "SOURCE_MISSING",
            "is_network_delay_high": None,
            "network_evidence_status": "SOURCE_MISSING",
            "row_version": 7,
        },
        "labels": [
            {
                "label_id": "7",
                "label_id_type": "NUMERIC",
                "label_name_snapshot": "耐心",
            }
        ],
        "complaints": [],
        "participations": [
            {
                "participation_seq": 1,
                "teacher_id": "A",
                "teacher_id_type": "NUMERIC",
                "participation_status": "t_absent",
                "participation_role": "NORMAL",
                "is_current": False,
                "source_deleted": False,
                "absence_reason_detail": "No Notification",
                "no_notice": True,
                "absence_source_id": "501",
                "absence_source_id_type": "NUMERIC",
                "absence_source_row_revision": 8,
                "absence_selected_reason_type": "No Notification",
                "teacher_expected_source_region": "dom",
                "teacher_region_evidence_status": "CONFIRMED",
                "teacher_profile_source_row_revision": 5,
                "teacher_profile_source_payload_hash": "d" * 64,
                "is_late": None,
                "late_evidence_status": "SOURCE_MISSING",
                "is_early": None,
                "early_evidence_status": "SOURCE_MISSING",
                "row_version": 3,
                "participation_fact_row_version": 2,
            },
            {
                "participation_seq": 2,
                "teacher_id": "B",
                "teacher_id_type": "NUMERIC",
                "participation_status": "end",
                "participation_role": "COMPLETION",
                "is_current": True,
                "source_deleted": False,
                "absence_reason_detail": None,
                "no_notice": None,
                "absence_source_id": None,
                "absence_source_id_type": None,
                "absence_source_row_revision": None,
                "absence_selected_reason_type": None,
                "teacher_expected_source_region": "dom",
                "teacher_region_evidence_status": "CONFIRMED",
                "teacher_profile_source_row_revision": 6,
                "teacher_profile_source_payload_hash": "e" * 64,
                "is_late": False,
                "late_evidence_status": "CONFIRMED",
                "is_early": False,
                "early_evidence_status": "CONFIRMED",
                "row_version": 4,
                "participation_fact_row_version": 5,
            },
        ],
    }


def test_substitution_preserves_absent_row_and_scores_only_frozen_teacher() -> None:
    plan = build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=_state(),
    )

    assert plan.affected_teacher_ids == ("A", "B")
    assert plan.course_row_version == 11
    assert plan.course_fact_row_version == 7
    assert len(plan.participation_rows) == 2
    absent, completed = plan.participation_rows
    assert absent.participation_status == "t_absent"
    assert absent.no_notice is True
    assert absent.visible_to_teacher is True
    assert absent.valid_for_scoring is False
    assert absent.score_conditions == ()
    assert completed.valid_for_scoring is True
    assert completed.participation_row_version == 4
    assert completed.participation_fact_row_version == 5
    assert completed.is_perfect is True
    assert completed.complaint_category_l1 == "投诉"
    assert completed.complaint_category_l2 == "出席问题"
    assert completed.complaint_category_l3 == "教师缺席"
    assert {
        condition.component_code: condition.should_award
        for condition in completed.score_conditions
    } == {
        "FEEDBACK_PRAISE": True,
        "PERFECT_COMPLETED": True,
        "PEAK_COMPLETED": True,
        "CLASS_QUALITY_HARDWARE": None,
    }
    assert plan.favorite_observation_required is True
    assert plan.favorite_observation_blocker is None


def test_non_end_course_still_projects_facts_but_has_no_score_owner() -> None:
    state = _state()
    course = state["course"]
    assert isinstance(course, dict)
    course["source_status"] = "on"
    course["completion_teacher_id"] = None
    course["completion_participation_seq"] = None
    course["completion_end_time"] = None
    course["completion_student_token"] = None
    course["completion_is_peak"] = None
    rows = state["participations"]
    assert isinstance(rows, list)
    rows[1]["participation_role"] = "NORMAL"
    rows[1]["participation_status"] = "on"

    plan = build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=state,
    )

    assert all(row.valid_for_scoring is False for row in plan.participation_rows)
    assert plan.participation_rows[1].grading_classification == "POSITIVE"
    assert plan.favorite_observation_required is False
    assert plan.favorite_observation_blocker == "COMPLETION_NOT_FROZEN"


def test_unknown_penalty_is_not_perfect_and_hardware_never_awards_now() -> None:
    state = _state()
    rows = state["participations"]
    assert isinstance(rows, list)
    rows[1]["is_early"] = None
    rows[1]["early_evidence_status"] = "SOURCE_MISSING"

    plan = build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=state,
    )
    completed = plan.participation_rows[1]
    assert completed.is_perfect is None
    conditions = {item.component_code: item for item in completed.score_conditions}
    assert conditions["PERFECT_COMPLETED"].should_award is None
    assert conditions["CLASS_QUALITY_HARDWARE"].should_award is None


def test_missing_completion_student_blocks_favorite_observation() -> None:
    state = _state()
    course = state["course"]
    assert isinstance(course, dict)
    course["completion_student_token"] = None

    plan = build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=state,
    )
    assert plan.favorite_observation_required is False
    assert plan.favorite_observation_blocker == (
        "SOURCE_MISSING:COMPLETION_STUDENT_TOKEN"
    )


def test_teacher_region_conflict_keeps_facts_but_stops_new_settlement() -> None:
    state = _state()
    course = state["course"]
    rows = state["participations"]
    assert isinstance(course, dict)
    assert isinstance(rows, list)
    course["teacher_region_evidence_status"] = "SOURCE_CONFLICT"
    course["evidence_status"] = "SOURCE_CONFLICT"
    rows[1]["teacher_expected_source_region"] = "ovs"
    rows[1]["teacher_region_evidence_status"] = "SOURCE_CONFLICT"

    plan = build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=state,
    )

    completed = plan.participation_rows[1]
    assert completed.visible_to_teacher is True
    assert completed.grading_classification == "POSITIVE"
    assert completed.labels[0]["label_name_snapshot"] == "耐心"
    assert completed.teacher_expected_source_region == "ovs"
    assert completed.teacher_region_evidence_status == "SOURCE_CONFLICT"
    assert completed.evidence_status == "SOURCE_CONFLICT"
    assert completed.valid_for_scoring is False
    assert completed.score_conditions == ()
    assert plan.favorite_observation_required is False
    assert plan.favorite_observation_blocker == (
        "SOURCE_CONFLICT:TEACHER_REGION_OR_APPOINT"
    )


def test_completion_pointer_must_match_one_completion_participation() -> None:
    state = _state()
    rows = state["participations"]
    assert isinstance(rows, list)
    rows[1]["teacher_id"] = "C"

    with pytest.raises(
        DtsV2CourseSourceWidePlanError,
        match="COMPLETION_ROW_INVALID",
    ):
        build_course_source_wide_plan_v2(
            source_region="dom",
            source_appoint_id="9001",
            aggregate_state=state,
        )
