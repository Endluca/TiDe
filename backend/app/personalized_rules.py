from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping


ATTENDANCE_COMPLAINT_CATEGORY = "出席问题"
QUALITY_COMPLAINT_CATEGORY = "网络设备问题"
UNFILLED_LESSON_MEMO = "Unfilled Lesson Memo"


def normalize_text(value: Any) -> str:
    """Normalize matching text without inventing fuzzy business equivalence."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return " ".join(normalized.split())


def is_true_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return normalize_text(value).lower() in {"1", "true", "yes", "y", "是"}


@dataclass(frozen=True)
class ComplaintRule:
    level2_name: str
    level3_name: str
    source_level_code: str
    severity_rank: int
    route_domain: str


@dataclass(frozen=True)
class TriggerDecision:
    rule_code: str
    domain: str
    output_type: str
    title: str
    priority: str
    why: str
    evidence: dict[str, Any]
    task_code: str | None = None


def _attendance_decision(*, reasons: list[str], evidence: dict[str, Any]) -> TriggerDecision:
    return TriggerDecision(
        rule_code="TR-REL-ATTENDANCE",
        domain="RELIABILITY",
        output_type="TEACHER_TASK",
        task_code="P-REL-ATTENDANCE",
        title="出席问题",
        priority="P1",
        why=f"该课程出现出席异常：{'、'.join(reasons)}。请参加培训并完成 quiz。",
        evidence=evidence,
    )


def evaluate_lesson(
    row: Mapping[str, Any],
    *,
    complaint_rules: Mapping[str, ComplaintRule],
) -> list[TriggerDecision]:
    """Evaluate one lesson with exact, explainable rules only.

    Aggregate rules (repeated negative-feedback label and distinct-student
    blacklist) deliberately live outside this function.
    """

    decisions: list[TriggerDecision] = []
    lesson_id = normalize_text(row.get("课程id"))

    absence_reason = normalize_text(row.get("缺席原因明细"))
    if absence_reason == UNFILLED_LESSON_MEMO:
        concurrent_attendance = [
            label
            for field, label in (("迟到", "迟到"), ("早退", "早退"), ("假早退", "假早退"))
            if is_true_flag(row.get(field))
        ]
        concurrent_note = (
            f" 同一课程还记录了：{'、'.join(concurrent_attendance)}。"
            if concurrent_attendance
            else ""
        )
        decisions.append(
            TriggerDecision(
                rule_code="TR-REL-LESSON-MEMO",
                domain="RELIABILITY",
                output_type="TEACHER_TASK",
                task_code="P-REL-MEMO",
                title="出席（未填写lesson-memo）问题",
                priority="P1",
                why=(
                    "该课程的缺席原因明细为 Unfilled Lesson Memo，请完成 lesson-memo 教学。"
                    + concurrent_note
                ),
                evidence={
                    "lesson_id": lesson_id,
                    "absence_reason_detail": absence_reason,
                    "concurrent_attendance_signals": concurrent_attendance,
                },
            )
        )

    attendance_reasons: list[str] = []
    if absence_reason and absence_reason != UNFILLED_LESSON_MEMO:
        attendance_reasons.append(f"缺席原因：{absence_reason}")
    if absence_reason != UNFILLED_LESSON_MEMO:
        for field, label in (("迟到", "迟到"), ("早退", "早退"), ("假早退", "假早退")):
            if is_true_flag(row.get(field)):
                attendance_reasons.append(label)
    if attendance_reasons:
        decisions.append(
            _attendance_decision(
                reasons=attendance_reasons,
                evidence={
                    "lesson_id": lesson_id,
                    "absence_reason_detail": absence_reason or None,
                    "is_late": is_true_flag(row.get("迟到")),
                    "is_early": is_true_flag(row.get("早退")),
                    "is_fake_early": is_true_flag(row.get("假早退")),
                },
            )
        )

    complaint_l2 = normalize_text(row.get("投诉二级分类"))
    complaint_l3 = normalize_text(row.get("投诉三级分类"))
    has_complaint = any(
        normalize_text(row.get(field))
        for field in ("投诉一级分类", "投诉二级分类", "投诉三级分类")
    )
    if has_complaint and complaint_l2 == ATTENDANCE_COMPLAINT_CATEGORY:
        decisions.append(
            _attendance_decision(
                reasons=[f"投诉：{complaint_l3 or complaint_l2}"],
                evidence={
                    "lesson_id": lesson_id,
                    "complaint_level2": complaint_l2,
                    "complaint_level3": complaint_l3 or None,
                },
            )
        )
    elif has_complaint and complaint_l2 == QUALITY_COMPLAINT_CATEGORY:
        decisions.append(
            TriggerDecision(
                rule_code="TR-QUALITY-COMPLAINT",
                domain="CLASS_QUALITY",
                output_type="NOTIFICATION",
                title="课中质量问题",
                priority="P1",
                why=f"该课程收到网络设备类投诉：{complaint_l3 or complaint_l2}。",
                evidence={
                    "lesson_id": lesson_id,
                    "complaint_level2": complaint_l2,
                    "complaint_level3": complaint_l3 or None,
                },
            )
        )
    elif has_complaint:
        complaint_rule = complaint_rules.get(normalize_text(complaint_l3))
        if complaint_rule is None:
            decisions.append(
                TriggerDecision(
                    rule_code="TR-FB-COMPLAINT-UNMAPPED",
                    domain="USER_FEEDBACK",
                    output_type="PENDING_DATA",
                    title="投诉分类待确认",
                    priority="P1",
                    why="投诉三级分类在现行处罚规则中没有精确匹配，未自动发布任务。",
                    evidence={
                        "lesson_id": lesson_id,
                        "complaint_level2": complaint_l2 or None,
                        "complaint_level3": complaint_l3 or None,
                    },
                )
            )
        elif complaint_rule.severity_rank <= 1:
            decisions.append(
                TriggerDecision(
                    rule_code="TR-FB-SEVERE-COMPLAINT",
                    domain="USER_FEEDBACK",
                    output_type="OPS_CASE",
                    title=f"严重投诉-{complaint_l3}问题",
                    priority="P0" if complaint_rule.severity_rank == 0 else "P1",
                    why=(
                        f"该课程投诉三级分类为“{complaint_l3}”，处罚表等级为"
                        f" {complaint_rule.source_level_code}，需运营处理。"
                    ),
                    evidence={
                        "lesson_id": lesson_id,
                        "complaint_level2": complaint_l2 or None,
                        "complaint_level3": complaint_l3,
                        "source_level_code": complaint_rule.source_level_code,
                        "severity_rank": complaint_rule.severity_rank,
                    },
                )
            )
        else:
            decisions.append(
                TriggerDecision(
                    rule_code="TR-FB-GENERAL-COMPLAINT",
                    domain="USER_FEEDBACK",
                    output_type="TEACHER_TASK",
                    task_code="P-FB-COMPLAINT",
                    title=f"一般投诉-{complaint_l3}问题",
                    priority="P1",
                    why=(
                        f"该课程投诉三级分类为“{complaint_l3}”，处罚表等级为"
                        f" {complaint_rule.source_level_code}，请完成对应学习任务。"
                    ),
                    evidence={
                        "lesson_id": lesson_id,
                        "complaint_level2": complaint_l2 or None,
                        "complaint_level3": complaint_l3,
                        "source_level_code": complaint_rule.source_level_code,
                        "severity_rank": complaint_rule.severity_rank,
                    },
                )
            )

    quality_reasons = [
        label
        for field, label in (
            ("未开摄像头", "未开摄像头"),
            ("cpu占用过高", "CPU 占用过高"),
            ("网络延迟过高", "网络延迟过高"),
        )
        if is_true_flag(row.get(field))
    ]
    if quality_reasons:
        decisions.append(
            TriggerDecision(
                rule_code="TR-QUALITY-IN-CLASS",
                domain="CLASS_QUALITY",
                output_type="NOTIFICATION",
                title="课中质量问题",
                priority="P1",
                why=f"该课程检测到：{'、'.join(quality_reasons)}。请检查并改善上课环境。",
                evidence={"lesson_id": lesson_id, "anomalies": quality_reasons},
            )
        )

    return decisions
