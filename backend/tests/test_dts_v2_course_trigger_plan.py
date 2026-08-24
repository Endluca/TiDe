from __future__ import annotations

from app.dts_v2_course_source_wide_plan import build_course_source_wide_plan_v2
from app.dts_v2_course_trigger_plan import build_course_trigger_plan_v2

from tests.test_dts_v2_course_source_wide_plan import _state


def _plan(state: dict[str, object] | None = None):
    return build_course_trigger_plan_v2(
        build_course_source_wide_plan_v2(
            source_region="dom",
            source_appoint_id="9001",
            aggregate_state=state or _state(),
        )
    )


def test_substitution_absence_task_belongs_to_old_teacher_before_or_after_end() -> None:
    plan = _plan()

    assert [(item.match_kind, item.teacher_id) for item in plan.matches] == [
        ("ABSENCE_P_REL_ATTENDANCE", "A")
    ]
    match = plan.matches[0]
    assert match.assignment_dedupe_key == "personalized:P-REL-ATTENDANCE:A"
    assert match.plan_evidence["reason_type"] == "No Notification"
    assert match.plan_evidence["no_notice"] is True
    assert match.evidence_discriminator == "501"
    assert match.evidence_discriminator_type == "NUMERIC"
    assert match.plan_evidence["absence_source_row_revision"] == 8

    state = _state()
    course = state["course"]
    rows = state["participations"]
    assert isinstance(course, dict) and isinstance(rows, list)
    course.update(
        completion_teacher_id=None,
        completion_participation_seq=None,
        completion_end_time=None,
        completion_student_token=None,
        completion_is_peak=None,
        source_status="on",
    )
    rows[1]["participation_role"] = "NORMAL"
    rows[1]["participation_status"] = "on"

    assert [item.teacher_id for item in _plan(state).matches] == ["A"]


def test_unfilled_memo_maps_to_the_single_memo_assignment() -> None:
    state = _state()
    rows = state["participations"]
    assert isinstance(rows, list)
    rows[0]["absence_reason_detail"] = "Unfilled Lesson Memo"
    rows[0]["absence_selected_reason_type"] = "Unfilled Lesson Memo"
    rows[0]["no_notice"] = False

    match = _plan(state).matches[0]

    assert match.match_kind == "ABSENCE_P_REL_MEMO"
    assert match.target_task_code == "P-REL-MEMO"
    assert match.assignment_dedupe_key == "personalized:P-REL-MEMO:A"


def test_negative_rating_emits_one_typed_course_contribution_not_an_early_task() -> None:
    state = _state()
    fact = state["course_fact"]
    assert isinstance(fact, dict)
    fact["grading_classification"] = "NEGATIVE"
    fact["negative_score"] = "2"

    matches = _plan(state).matches
    negative = next(item for item in matches if item.match_kind == "NEGATIVE_LABEL_COURSE")

    assert negative.teacher_id == "B"
    assert negative.threshold_required == 2
    assert negative.assignment_dedupe_key == "personalized:P-FB-NEGATIVE:B:7"
    assert negative.evidence_discriminator_type == "NUMERIC"
    assert negative.plan_evidence["label_name"] == "耐心"


def test_latest_general_complaint_and_camera_route_to_completion_teacher() -> None:
    state = _state()
    fact = state["course_fact"]
    complaints = state["complaints"]
    assert isinstance(fact, dict) and isinstance(complaints, list)
    fact.update(
        latest_valid_complaint_id="701",
        latest_valid_complaint_id_type="NUMERIC",
        has_complaint=True,
        has_valid_complaint=True,
        complaint_evidence_status="CONFIRMED",
        latest_category_l2_snapshot="教学问题",
        latest_category_l3_snapshot="教学态度",
        is_camera_off=True,
    )
    complaints.append(
        {
            "source_complaint_id": "701",
            "source_complaint_id_type": "NUMERIC",
            "is_valid": True,
            "complaint_type_grandson": "83",
            "complaint_type_grandson_type": "NUMERIC",
            "category_l1_snapshot": "投诉",
            "category_l2_snapshot": "教学问题",
            "category_l3_snapshot": "教学态度",
            "evidence_status": "CONFIRMED",
            "evidence_error_code": None,
            "complaint_rule_id": "complaint-rule:" + "a" * 64 + ":1",
            "source_sha256": "a" * 64,
            "severity_rank": 3,
        }
    )

    matches = _plan(state).matches
    complaint = next(item for item in matches if item.match_kind == "GENERAL_COMPLAINT")
    camera = next(item for item in matches if item.match_kind == "CAMERA_OFF_NOTIFICATION")

    assert complaint.teacher_id == "B"
    assert complaint.assignment_dedupe_key == "personalized:P-FB-COMPLAINT:B:83"
    assert complaint.evidence_discriminator == "701"
    assert complaint.evidence_discriminator_type == "NUMERIC"
    assert camera.teacher_id == "B"
    assert camera.output_key == "camera-notification:TR-QUALITY-CAMERA-OFF:dom:9001:2"


def test_null_grandson_counts_in_course_fact_but_creates_no_complaint_action() -> None:
    state = _state()
    fact = state["course_fact"]
    complaints = state["complaints"]
    assert isinstance(fact, dict) and isinstance(complaints, list)
    fact.update(
        latest_valid_complaint_id="701",
        latest_valid_complaint_id_type="NUMERIC",
        has_complaint=True,
        has_valid_complaint=True,
        complaint_evidence_status="PENDING_DATA",
    )
    complaints.append(
        {
            "source_complaint_id": "701",
            "source_complaint_id_type": "NUMERIC",
            "is_valid": True,
            "complaint_type_grandson": None,
            "complaint_type_grandson_type": None,
            "category_l2_snapshot": "教学问题",
            "category_l3_snapshot": None,
            "complaint_rule_id": None,
            "source_sha256": None,
            "severity_rank": None,
        }
    )

    assert all("COMPLAINT" not in item.match_kind for item in _plan(state).matches)
