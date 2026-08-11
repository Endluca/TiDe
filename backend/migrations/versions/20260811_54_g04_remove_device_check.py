"""remove the device check from G04 first-lesson preparation

Revision ID: 20260811_54_g04_remove_device_check
Revises: 20260811_51_g01_tesol_only
Create Date: 2026-08-11

G04 keeps its code, score, published status and assignment lifecycle. This
controlled catalog update renames the task and limits its teacher-facing copy
to the two retained sections: courseware preparation and teaching-environment
photo AI review. The stable physical row remains ``G02:v1``.

This revision is forward-only. Teacher revision 0037 removes the active device
step without restoring it on downgrade, so restoring the public three-part copy
would split the shared contract. ``downgrade()`` therefore fails explicitly;
operators must correct the issue with a reviewed forward migration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_54_g04_remove_device_check"
down_revision: Union[str, None] = "20260811_51_g01_tesol_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COPY: dict[str, str] = {
    "ops_name_zh": "首课备课与设备网络检测",
    "title": "Lesson Preparation&Device Network Check",
    "why_template": (
        "Complete lesson preparation and confirm that your teaching setup is "
        "ready before class."
    ),
    "how_summary": (
        "Complete three independent sections in any order: review the "
        "lesson-preparation guidance; run the camera, microphone and network "
        "check; and submit one teaching-environment photo for AI review. Each "
        "section keeps its own progress."
    ),
    "completion_standard": (
        "G04 is completed only after all three independent sections pass: the "
        "lesson-preparation guidance is confirmed; the camera, microphone and "
        "network check passes; and all four teaching-environment photo "
        "criteria—camera angle, lighting, background and dressing—pass AI review. "
        "The sections may be completed in any order."
    ),
    "benefit": (
        "Your lesson-preparation knowledge, device and network readiness, and "
        "teaching environment are independently verified for your first lesson."
    ),
}

NEW_COPY: dict[str, str] = {
    "ops_name_zh": "首课准备",
    "title": "Lesson Preparation",
    "why_template": (
        "Complete the teaching-environment photo review and prepare the "
        "courseware before your first lesson."
    ),
    "how_summary": (
        "Complete two sections in any order: submit one teaching-environment "
        "photo for AI review and prepare the courseware for your first lesson. "
        "Each section keeps its own progress."
    ),
    "completion_standard": (
        "G04 is completed only after both sections pass: all four "
        "teaching-environment photo criteria—camera angle, lighting, background "
        "and dressing—pass AI review, and the courseware preparation is "
        "confirmed. The sections may be completed in any order."
    ),
    "benefit": (
        "Your teaching environment and courseware are ready for your first lesson."
    ),
}


_templates = sa.table(
    "task_templates",
    sa.column("row_id", sa.String()),
    sa.column("template_id", sa.String()),
    sa.column("template_version", sa.Integer()),
    sa.column("status", sa.String()),
    sa.column("revision", sa.Integer()),
    sa.column("payload", sa.JSON()),
    sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


G04_STABLE_ROW_ID = "G02:v1"
FORWARD_ONLY_DOWNGRADE_ERROR = (
    "revision 20260811_54_g04_remove_device_check is forward-only; restoring "
    "the removed G04 device check would violate the shared public/teacher contract"
)


def _g04_select() -> sa.sql.Select[Any]:
    return (
        sa.select(
            _templates.c.row_id,
            _templates.c.template_id,
            _templates.c.template_version,
            _templates.c.status,
            _templates.c.revision,
            _templates.c.payload,
        )
        .where(_templates.c.row_id == G04_STABLE_ROW_ID)
        .with_for_update()
    )


def _g04_row(bind: sa.engine.Connection) -> Mapping[str, Any]:
    rows = list(bind.execute(_g04_select()).mappings())
    if len(rows) != 1:
        raise RuntimeError(
            "G04 device-check removal requires exactly one stable G02:v1 template row"
        )
    row = rows[0]
    payload = row["payload"]
    if (
        row["template_id"] != "G04"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(payload, Mapping)
        or payload.get("template_id") != "G04"
        or payload.get("score_value") != 3
    ):
        raise RuntimeError(
            "G04 device-check removal requires stable G02:v1 to be the published "
            "three-point G04 version"
        )
    return row


def _apply_copy(
    copy: Mapping[str, str],
    *,
    expected_copy: Mapping[str, str],
    actor: str,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    row = _g04_row(bind)
    payload = dict(row["payload"] or {})
    current_copy = {field: payload.get(field) for field in expected_copy}
    if current_copy != dict(expected_copy):
        raise RuntimeError(
            "G04 device-check removal found unreviewed teacher-facing copy drift"
        )

    payload.update(copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == G04_STABLE_ROW_ID,
            _templates.c.template_id == "G04",
            _templates.c.template_version == 1,
            _templates.c.status == "PUBLISHED",
            _templates.c.revision == row["revision"],
        )
        .values(
            payload=payload,
            revision=_templates.c.revision + revision_delta,
            updated_by=actor,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        raise RuntimeError(
            "G04 device-check removal did not update exactly one template row"
        )


def upgrade() -> None:
    _apply_copy(
        NEW_COPY,
        expected_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260811_54_G04_REMOVE_DEVICE_CHECK",
        revision_delta=1,
    )


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_DOWNGRADE_ERROR)
