from __future__ import annotations

from app.personalized_rules import ComplaintRule, evaluate_lesson, normalize_text


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
        {"课程id": 1, "缺席原因明细": "Unfilled Lesson Memo"},
        complaint_rules=RULES,
    )
    assert [(item.task_code, item.title) for item in memo] == [
        ("P-REL-MEMO", "出席（未填写lesson-memo）问题")
    ]

    memo_with_late = evaluate_lesson(
        {"课程id": 11, "缺席原因明细": "Unfilled Lesson Memo", "迟到": 1},
        complaint_rules=RULES,
    )
    assert len(memo_with_late) == 1
    assert memo_with_late[0].evidence["concurrent_attendance_signals"] == ["迟到"]

    attendance = evaluate_lesson(
        {"课程id": 2, "缺席原因明细": "Power Failure", "迟到": 1},
        complaint_rules=RULES,
    )
    assert len(attendance) == 1
    assert attendance[0].task_code == "P-REL-ATTENDANCE"
    assert "Power Failure" in attendance[0].why
    assert "迟到" in attendance[0].why


def test_complaint_rank_routes_to_ops_or_teacher() -> None:
    severe = evaluate_lesson(
        {
            "课程id": 3,
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学态度问题",
            "投诉三级分类": "外教向学员借钱",
        },
        complaint_rules=RULES,
    )
    assert severe[0].output_type == "OPS_CASE"
    assert severe[0].priority == "P0"

    general = evaluate_lesson(
        {
            "课程id": 4,
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学技巧问题",
            "投诉三级分类": "语速过快",
        },
        complaint_rules=RULES,
    )
    assert general[0].task_code == "P-FB-COMPLAINT"
    assert general[0].title == "一般投诉-语速过快问题"


def test_attendance_and_network_complaints_skip_general_complaint_route() -> None:
    attendance = evaluate_lesson(
        {
            "课程id": 5,
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
            "投诉一级分类": "关于老师",
            "投诉二级分类": "网络设备问题",
            "投诉三级分类": "网络卡顿",
        },
        complaint_rules=RULES,
    )
    assert quality[0].domain == "CLASS_QUALITY"
    assert quality[0].output_type == "NOTIFICATION"


def test_quality_flags_are_merged_into_one_explainable_reminder() -> None:
    decisions = evaluate_lesson(
        {"课程id": 7, "未开摄像头": 1, "cpu占用过高": 1, "网络延迟过高": 0},
        complaint_rules=RULES,
    )
    assert len(decisions) == 1
    assert decisions[0].title == "课中质量问题"
    assert decisions[0].evidence["anomalies"] == ["未开摄像头", "CPU 占用过高"]


def test_unknown_complaint_stops_in_pending_data() -> None:
    decisions = evaluate_lesson(
        {
            "课程id": 8,
            "投诉一级分类": "关于老师",
            "投诉二级分类": "教学技巧问题",
            "投诉三级分类": "未知分类",
        },
        complaint_rules=RULES,
    )
    assert decisions[0].output_type == "PENDING_DATA"
