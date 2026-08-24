from __future__ import annotations

from app.personalized_rules import ComplaintRule, evaluate_lesson, normalize_text
from app.teacher_copy import contains_han


RULES = {
    normalize_text("外教向学员借钱"): ComplaintRule(
        level2_name="教学态度问题",
        level3_name="外教向学员借钱",
        source_level_code="P0",
        severity_rank=0,
        route_domain="COMPLAINT",
    ),
    normalize_text("语速过快"): ComplaintRule(
        level2_name="教学技巧问题",
        level3_name="语速过快",
        source_level_code="P4",
        severity_rank=4,
        route_domain="COMPLAINT",
    ),
}


def test_lesson_memo_and_attendance_signals_are_exact() -> None:
    memo = evaluate_lesson(
        {
            "课程id": 1,
            "课程状态": "t_absent",
            "缺席原因明细": "Unfilled Lesson Memo",
        },
        complaint_rules=RULES,
    )
    assert [(item.task_code, item.title) for item in memo] == [
        ("P-REL-MEMO", "Missing Lesson Memo")
    ]
    assert "blank Lesson Memo" in memo[0].why
    assert "unfilled Lesson Memo" not in memo[0].why

    memo_with_late = evaluate_lesson(
        {
            "课程id": 11,
            "课程状态": "t_absent",
            "缺席原因明细": "Unfilled Lesson Memo",
            "迟到": 1,
        },
        complaint_rules=RULES,
    )
    assert len(memo_with_late) == 1
    assert memo_with_late[0].evidence["concurrent_attendance_signals"] == ["迟到"]

    attendance = evaluate_lesson(
        {
            "课程id": 2,
            "课程状态": "t_absent",
            "缺席原因明细": "Power Failure",
            "迟到": 1,
        },
        complaint_rules=RULES,
    )
    assert len(attendance) == 1
    assert attendance[0].task_code == "P-REL-ATTENDANCE"
    assert "absence reason" in attendance[0].why
    assert "late arrival" in attendance[0].why
    assert not contains_han(attendance[0].why)


def test_absence_reason_does_not_create_task_without_absent_status() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 12,
            "课程状态": "end",
            "缺席原因明细": "Unfilled Lesson Memo",
        },
        complaint_rules=RULES,
    )

    assert decisions == []


def test_penalty_never_creates_an_attendance_task_by_itself() -> None:
    pre_end = evaluate_lesson(
        {
            "课程id": 13,
            "课程状态": "on",
            "迟到": True,
            "早退": True,
        },
        complaint_rules=RULES,
    )
    completed = evaluate_lesson(
        {
            "课程id": 14,
            "课程状态": "end",
            "迟到": True,
        },
        complaint_rules=RULES,
    )

    assert pre_end == []
    assert completed == []


def test_complaint_rank_routes_to_ops_or_teacher() -> None:
    severe = evaluate_lesson(
        {
            "课程id": 3,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学态度问题",
            "投诉三级分类": "外教向学员借钱",
        },
        complaint_rules=RULES,
    )
    assert severe[0].output_type == "OPS_CASE"
    assert severe[0].priority == "P0"
    assert not contains_han(severe[0].why)

    general = evaluate_lesson(
        {
            "课程id": 4,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学技巧问题",
            "投诉三级分类": "语速过快",
        },
        complaint_rules=RULES,
    )
    assert general[0].task_code == "P-FB-COMPLAINT"
    assert general[0].title == "General Complaint - Speaking Too Fast"
    assert not contains_han(general[0].title)
    assert not contains_han(general[0].why)


def test_attendance_and_network_complaints_skip_general_complaint_route() -> None:
    attendance = evaluate_lesson(
        {
            "课程id": 5,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "出席问题",
            "投诉三级分类": "迟到",
        },
        complaint_rules=RULES,
    )
    assert attendance[0].domain == "RELIABILITY"
    assert attendance[0].task_code == "P-REL-ATTENDANCE"

    quality = evaluate_lesson(
        {
            "课程id": 6,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "网络设备问题",
            "投诉三级分类": "网络卡顿",
        },
        complaint_rules=RULES,
    )
    assert quality[0].domain == "CLASS_QUALITY"
    assert quality[0].output_type == "NOTIFICATION"
    assert not contains_han(quality[0].why)


def test_quality_flags_are_merged_into_one_explainable_reminder() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 7,
            "课程状态": "end",
            "未开摄像头": 1,
            "cpu占用过高": 1,
            "网络延迟过高": 0,
        },
        complaint_rules=RULES,
    )
    assert len(decisions) == 1
    assert decisions[0].title == "In-Class Quality Alert"
    assert decisions[0].evidence["anomalies"] == ["未开摄像头", "CPU 占用过高"]
    assert not contains_han(decisions[0].why)


def test_unknown_complaint_stops_in_pending_data() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 8,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学技巧问题",
            "投诉三级分类": "未知分类",
        },
        complaint_rules=RULES,
    )
    assert decisions[0].output_type == "PENDING_DATA"
    assert not contains_han(decisions[0].why)


def test_missing_complaint_level3_is_pending_even_for_attendance_category() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 9,
            "课程状态": "end",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "出席问题",
            "投诉三级分类": None,
        },
        complaint_rules=RULES,
    )

    assert len(decisions) == 1
    assert decisions[0].output_type == "PENDING_DATA"
    assert decisions[0].rule_code == "TR-FB-COMPLAINT-CATEGORY-MISSING"
    assert decisions[0].task_code is None
    assert decisions[0].evidence["missing_field"] == "投诉三级分类"


def test_complaint_and_camera_wait_for_frozen_completion_owner() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 10,
            "课程状态": "on",
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学技巧问题",
            "投诉三级分类": "语速过快",
            "未开摄像头": 1,
        },
        complaint_rules=RULES,
    )

    assert decisions == []
