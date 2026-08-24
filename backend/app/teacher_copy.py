from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_NEGATIVE_FEEDBACK_LABELS_EN = {
    "上课死板": "Rigid Teaching Style",
    "不够耐心": "Insufficient Patience",
    "发音不准": "Inaccurate Pronunciation",
    "只是读课件": "Only Reading the Courseware",
    "很少鼓励孩子": "Insufficient Student Encouragement",
    "教的太难": "Content Too Difficult",
    "有口音听不懂": "Accent Difficult to Understand",
    "有噪音/老师声音小": "Background Noise or Low Teacher Volume",
    "未讲完教材": "Courseware Not Completed",
    "灯光过暗/亮": "Lighting Too Dark or Too Bright",
    "环境乱/灯光差": "Distracting Environment or Poor Lighting",
    "缺乏热情": "Lack of Enthusiasm",
    "缺乏耐心": "Lack of Patience",
    "缺少互动": "Insufficient Interaction",
    "网络设备差": "Poor Network or Equipment",
    "老师上课不专注": "Teacher Not Focused",
    "语速太快": "Speaking Too Fast",
    "语速过快": "Speaking Too Fast",
}

_COMPLAINT_LABELS_EN = {
    "未及时回应学员问题": "Did Not Respond to the Student Promptly",
    "过早上完教材,等待下课": (
        "Finished Courseware Too Early and Waited for Class to End"
    ),
    "外教向学员借钱": "Teacher Asked Student for Money",
    "迟到": "Late Arrival",
    "网络卡顿": "Unstable Network",
    "麦克风没有声音/卡顿": "Microphone Audio Missing or Unstable",
    "语速过快": "Speaking Too Fast",
}

_QUALITY_ANOMALIES_EN = {
    "未开摄像头": "camera off",
    "CPU 占用过高": "high CPU usage",
    "cpu占用过高": "high CPU usage",
    "网络延迟过高": "high network delay",
}


def contains_han(value: object) -> bool:
    """Return whether teacher-facing copy contains a Han character."""

    return bool(_HAN_CHARACTER.search(str(value or "")))


def require_english_teacher_copy(value: str, *, field_name: str) -> str:
    """Fail closed when teacher-facing English copy contains Chinese text."""

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if contains_han(normalized):
        raise ValueError(f"{field_name} must be English teacher-facing copy")
    return normalized


def personalized_task_title(task_code: str, detail: str | None = None) -> str:
    """Return concise teacher-facing English for personalized assignments."""

    normalized_detail = str(detail or "").strip()
    if task_code == "P-REL-MEMO":
        return "Missing Lesson Memo"
    if task_code == "P-REL-ATTENDANCE":
        return "Attendance Improvement"
    if task_code == "P-FB-BLACKLIST":
        return "Blacklist Prevention"
    if task_code == "P-FB-NEGATIVE":
        translated = _NEGATIVE_FEEDBACK_LABELS_EN.get(normalized_detail)
        if translated is None:
            translated = (
                normalized_detail
                if normalized_detail and not contains_han(normalized_detail)
                else "Feedback Pattern"
            )
        return f"Negative Feedback - {translated}"
    if task_code == "P-FB-COMPLAINT":
        translated = _COMPLAINT_LABELS_EN.get(normalized_detail)
        if translated is None:
            translated = (
                normalized_detail
                if normalized_detail and not contains_han(normalized_detail)
                else "Complaint Category"
            )
        return f"General Complaint - {translated}"
    return require_english_teacher_copy(
        normalized_detail or task_code,
        field_name="task_assignments.display_title",
    )


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _translated_label(
    value: object,
    translations: Mapping[str, str],
    *,
    fallback: str,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    translated = translations.get(normalized)
    if translated:
        return translated
    return fallback if contains_han(normalized) else normalized


def teacher_evidence_summary(snapshot: Mapping[str, Any] | None) -> str:
    """Build concise English evidence from a task or notification snapshot."""

    if not isinstance(snapshot, Mapping):
        return ""
    nested = snapshot.get("evidence")
    evidence = nested if isinstance(nested, Mapping) else snapshot
    parts: list[str] = []

    lesson_ids = _as_string_list(evidence.get("lesson_ids"))
    lesson_id = str(evidence.get("lesson_id") or "").strip()
    if not lesson_ids and lesson_id:
        lesson_ids = [lesson_id]
    if lesson_ids:
        sample = ", ".join(lesson_ids[:5])
        suffix = ", ..." if len(lesson_ids) > 5 else ""
        parts.append(f"Lesson IDs: {sample}{suffix}")

    absence_reason = str(evidence.get("absence_reason_detail") or "").strip()
    if absence_reason == "Unfilled Lesson Memo":
        parts.append("absence reason: Unfilled Lesson Memo")
    elif absence_reason:
        parts.append("an attendance-related absence reason was recorded")

    if evidence.get("is_late") is True:
        parts.append("late arrival recorded")
    if evidence.get("is_early") is True:
        parts.append("early departure recorded")

    complaint = evidence.get("complaint_level3") or evidence.get(
        "complaint_category_l3"
    )
    complaint_label = _translated_label(
        complaint,
        _COMPLAINT_LABELS_EN,
        fallback="complaint category recorded",
    )
    complaint_level = str(
        evidence.get("source_level_code")
        or evidence.get("source_level")
        or ""
    ).strip()
    if complaint_label:
        suffix = f" ({complaint_level})" if complaint_level else ""
        parts.append(f"complaint: {complaint_label}{suffix}")

    negative_label = _translated_label(
        evidence.get("negative_feedback_label"),
        _NEGATIVE_FEEDBACK_LABELS_EN,
        fallback="repeated negative-feedback tag",
    )
    if negative_label:
        hit_count = (
            evidence.get("aggregate_hit_count")
            or evidence.get("negative_review_lesson_count")
            or evidence.get("hit_count")
        )
        suffix = f" across {hit_count} lessons" if hit_count else ""
        parts.append(f"negative feedback: {negative_label}{suffix}")

    distinct_student_count = evidence.get("distinct_student_count")
    if distinct_student_count:
        parts.append(
            f"blacklisted by {distinct_student_count} different students"
        )

    anomalies = [
        _translated_label(
            item,
            _QUALITY_ANOMALIES_EN,
            fallback="in-class quality anomaly",
        )
        for item in _as_string_list(evidence.get("anomalies"))
    ]
    anomalies = list(dict.fromkeys(item for item in anomalies if item))
    if anomalies:
        parts.append("quality anomalies: " + ", ".join(anomalies))

    signal_samples = evidence.get("signal_samples")
    if isinstance(signal_samples, Sequence) and not isinstance(
        signal_samples, (str, bytes)
    ):
        for sample in signal_samples:
            if not isinstance(sample, Mapping):
                continue
            sample_evidence = sample.get("evidence")
            if not isinstance(sample_evidence, Mapping):
                continue
            summary = teacher_evidence_summary(sample_evidence)
            for item in summary.split("; "):
                if item.startswith("Lesson IDs:"):
                    continue
                if item and item not in parts:
                    parts.append(item)

    return "; ".join(parts[:6])


def with_teacher_evidence(
    reason: str,
    snapshot: Mapping[str, Any] | None,
) -> str:
    """Append a compact evidence clause once and enforce English output."""

    normalized = require_english_teacher_copy(
        reason,
        field_name="teacher_facing_reason",
    )
    if "Evidence:" in normalized:
        return normalized
    evidence = teacher_evidence_summary(snapshot)
    if not evidence:
        return normalized
    if "Lesson IDs:" in normalized:
        evidence = "; ".join(
            item
            for item in evidence.split("; ")
            if not item.startswith("Lesson IDs:")
        )
    if not evidence:
        return normalized
    return require_english_teacher_copy(
        f"{normalized.rstrip()} Evidence: {evidence}.",
        field_name="teacher_facing_reason",
    )


__all__ = [
    "contains_han",
    "personalized_task_title",
    "require_english_teacher_copy",
    "teacher_evidence_summary",
    "with_teacher_evidence",
]
