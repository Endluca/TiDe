"""make personalized assignment titles English database facts

Revision ID: 20260729_35_task_title_en
Revises: 20260729_34_body_evidence
Create Date: 2026-07-29

Teacher-facing personalized task titles are migrated in place. Raw upstream
labels remain unchanged in ``evidence_snapshot``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260729_35_task_title_en"
down_revision: Union[str, None] = "20260729_34_body_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CONSTRAINT_NAME = "ck_task_assignment_personalized_title_english"

_NEGATIVE_LABELS_EN = {
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

_assignments = sa.table(
    "task_assignments",
    sa.column("assignment_id", sa.String()),
    sa.column("task_code", sa.String()),
    sa.column("task_kind", sa.String()),
    sa.column("display_title", sa.String()),
    sa.column("row_version", sa.Integer()),
    sa.column("updated_by", sa.String()),
)


def _english_title(task_code: str, value: object) -> str:
    title = str(value or "").strip()
    if title and not _HAN_CHARACTER.search(title):
        return title
    if task_code == "P-REL-MEMO":
        return "Missing Lesson Memo"
    if task_code == "P-REL-ATTENDANCE":
        return "Attendance Improvement"
    if task_code == "P-FB-BLACKLIST":
        return "Blacklist Prevention"
    if task_code == "P-FB-NEGATIVE":
        label = title.removeprefix("差评-").removesuffix("问题")
        translated = _NEGATIVE_LABELS_EN.get(label)
        if translated:
            return f"Negative Feedback - {translated}"
    if task_code == "P-FB-COMPLAINT":
        label = title.removeprefix("一般投诉-").removesuffix("问题")
        translated = _COMPLAINT_LABELS_EN.get(label)
        if translated:
            return f"General Complaint - {translated}"
    raise RuntimeError(
        f"no English display-title mapping for {task_code}: {title}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            _assignments.c.assignment_id,
            _assignments.c.task_code,
            _assignments.c.display_title,
        ).where(
            _assignments.c.task_kind == "PERSONALIZED_IMPROVEMENT"
        )
    ).mappings()
    updates = [
        {
            "assignment_id": row["assignment_id"],
            "display_title": _english_title(
                str(row["task_code"]),
                row["display_title"],
            ),
        }
        for row in rows
    ]

    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER trg_task_assignment_write "
            "ON public.task_assignments"
        )

    for item in updates:
        bind.execute(
            sa.update(_assignments)
            .where(
                _assignments.c.assignment_id == item["assignment_id"]
            )
            .values(
                display_title=item["display_title"],
                row_version=_assignments.c.row_version + 1,
                updated_by="SYSTEM_MIGRATION_20260729_35",
            )
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TRIGGER trg_task_assignment_write
            BEFORE INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write()
            """
        )
        op.create_check_constraint(
            _CONSTRAINT_NAME,
            "task_assignments",
            (
                "task_kind <> 'PERSONALIZED_IMPROVEMENT' "
                "OR (display_title IS NOT NULL "
                "AND btrim(display_title) <> '' "
                "AND display_title !~ U&'[\\4E00-\\9FFF]')"
            ),
            schema="public",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            _CONSTRAINT_NAME,
            "task_assignments",
            type_="check",
            schema="public",
        )
    # The original Chinese display copy cannot be reconstructed safely.
