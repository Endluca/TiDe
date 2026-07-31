"""persist personalized task evidence in task_assignments.why

Revision ID: 20260728_31_task_why_evidence
Revises: 20260728_30_renumber_g01_g09
Create Date: 2026-07-28

The teacher and operations services share task_assignments directly.  This
revision makes the stored why value self-contained so consumers do not need to
reproduce an API-only projection from evidence_snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_31_task_why_evidence"
down_revision: Union[str, None] = "20260728_30_renumber_g01_g09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    "语速过快": "Speaking Too Fast",
}

_QUALITY_ANOMALIES_EN = {
    "未开摄像头": "camera off",
    "CPU 占用过高": "high CPU usage",
    "cpu占用过高": "high CPU usage",
    "网络延迟过高": "high network delay",
}


def _contains_han(value: object) -> bool:
    return bool(_HAN_CHARACTER.search(str(value or "")))


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
    return fallback if _contains_han(normalized) else normalized


def _teacher_evidence_summary(snapshot: Mapping[str, Any] | None) -> str:
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
    if evidence.get("is_fake_early") is True:
        parts.append("possible false early-departure signal recorded")

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
            summary = _teacher_evidence_summary(sample_evidence)
            for item in summary.split("; "):
                if item.startswith("Lesson IDs:"):
                    continue
                if item and item not in parts:
                    parts.append(item)

    return "; ".join(parts[:6])


def _with_teacher_evidence(
    reason: str,
    snapshot: Mapping[str, Any] | None,
) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ValueError("task_assignments.why is required")
    if _contains_han(normalized):
        raise ValueError("task_assignments.why must remain English")
    if "Evidence:" in normalized:
        return normalized
    evidence = _teacher_evidence_summary(snapshot)
    if not evidence:
        raise ValueError(
            "personalized task evidence_snapshot cannot produce an English summary"
        )
    if "Lesson IDs:" in normalized:
        evidence = "; ".join(
            item
            for item in evidence.split("; ")
            if not item.startswith("Lesson IDs:")
        )
    if not evidence:
        raise ValueError(
            "personalized task why must contain a separate evidence summary"
        )
    result = f"{normalized.rstrip()} Evidence: {evidence}."
    if _contains_han(result):
        raise ValueError("persisted task_assignments.why must remain English")
    return result


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT assignment_id, why, evidence_snapshot
            FROM task_assignments
            WHERE task_kind = 'PERSONALIZED_IMPROVEMENT'
              AND why NOT LIKE '%Evidence:%'
            ORDER BY assignment_id
            """
        )
    ).mappings()
    updates = [
        {
            "assignment_id": row["assignment_id"],
            "why": _with_teacher_evidence(
                str(row["why"]),
                row["evidence_snapshot"],
            ),
        }
        for row in rows
    ]

    is_postgresql = bind.dialect.name == "postgresql"
    if is_postgresql:
        op.execute(
            "DROP TRIGGER trg_task_assignment_write ON public.task_assignments"
        )

    now = datetime.now(timezone.utc)
    for item in updates:
        bind.execute(
            sa.text(
                """
                UPDATE task_assignments
                SET why = :why,
                    updated_by = 'MIGRATION:TASK_WHY_EVIDENCE',
                    row_version = row_version + 1,
                    updated_at = :updated_at
                WHERE assignment_id = :assignment_id
                """
            ),
            {**item, "updated_at": now},
        )

    if is_postgresql:
        op.execute(
            """
            CREATE TRIGGER trg_task_assignment_write
            BEFORE INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write()
            """
        )

    op.create_check_constraint(
        "ck_task_assignment_personalized_why_evidence",
        "task_assignments",
        "task_kind <> 'PERSONALIZED_IMPROVEMENT' "
        "OR why LIKE '%Evidence:%'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_assignment_personalized_why_evidence",
        "task_assignments",
        type_="check",
    )
    # The previous generic text cannot be reconstructed without discarding
    # useful teacher-facing evidence, so the enriched why values remain.
