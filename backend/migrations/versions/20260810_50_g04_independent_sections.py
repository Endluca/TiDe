"""clarify G04 as three independent first-lesson readiness sections

Revision ID: 20260810_50_g04_sections
Revises: 20260807_49_unused_columns
Create Date: 2026-08-10

G04 keeps its code, score, published status and assignment lifecycle.  This
controlled catalog update changes only the teacher-facing How, completion and
benefit copy on the stable ``G02:v1`` row that was remapped to current G04 by
revision 30. The three sections can be completed in any order while retaining
independent progress.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260810_50_g04_sections"
down_revision: Union[str, None] = "20260807_49_unused_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COPY: dict[str, str] = {
    "how_summary": (
        "Confirm lesson preparation, check the camera, microphone and network, "
        "then take one teaching-environment photo."
    ),
    "completion_standard": (
        "Lesson preparation is confirmed, camera, microphone and network pass, "
        "and the teaching-environment photo passes AI review."
    ),
    "benefit": (
        "Your lesson preparation and pre-class setup are recorded as ready."
    ),
}

NEW_COPY: dict[str, str] = {
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


def _g04_row(bind: sa.engine.Connection) -> Mapping[str, Any]:
    rows = list(
        bind.execute(
            sa.select(
                _templates.c.row_id,
                _templates.c.template_id,
                _templates.c.template_version,
                _templates.c.status,
                _templates.c.revision,
                _templates.c.payload,
            ).where(_templates.c.row_id == G04_STABLE_ROW_ID)
        ).mappings()
    )
    if len(rows) != 1:
        raise RuntimeError(
            "G04 copy migration requires exactly one stable G02:v1 template row"
        )
    row = rows[0]
    if (
        row["template_id"] != "G04"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(row["payload"], Mapping)
        or row["payload"].get("template_id") != "G04"
    ):
        raise RuntimeError(
            "G04 copy migration requires stable G02:v1 to be the published G04 version"
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
            "G04 copy migration found unreviewed teacher-facing copy drift"
        )

    payload.update(copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == G04_STABLE_ROW_ID,
            _templates.c.template_id == "G04",
            _templates.c.template_version == 1,
            _templates.c.status == "PUBLISHED",
        )
        .values(
            payload=payload,
            revision=_templates.c.revision + revision_delta,
            updated_by=actor,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        raise RuntimeError("G04 copy migration did not update exactly one row")


def upgrade() -> None:
    _apply_copy(
        NEW_COPY,
        expected_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260810_50_G04_SECTIONS",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        OLD_COPY,
        expected_copy=NEW_COPY,
        actor="SYSTEM_MIGRATION_20260810_50_G04_SECTIONS_DOWN",
        revision_delta=-1,
    )
