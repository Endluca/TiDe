"""publish the reviewed G09 Kuozhi SET course copy

Revision ID: 20260819_65_g09_set_course
Revises: 20260819_64_g05_g08_courses
Create Date: 2026-08-19

The stable G10:v1 physical row remains business task G09.  This migration
validates and writes its complete code-canonical teacher-facing copy for
Kuozhi course 658. Scores, assignments, task status and completion facts
remain unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260819_65_g09_set_course"
down_revision: Union[str, None] = "20260819_64_g05_g08_courses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROW_ID = "G10:v1"
OLD_COPY = {
    "ops_name_zh": "SET 教学基础",
    "title": "SET Teaching Fundamentals",
    "why_template": "Learn the fundamentals of SET teaching.",
    "how_summary": (
        "Watch the in-platform Mock video slot and complete the five-question "
        "Mock check."
    ),
    "completion_standard": (
        "The Mock video is watched in full and the five-question check reaches "
        "80%."
    ),
    "benefit": "You understand the SET teaching foundation.",
}
NEW_COPY = {
    "ops_name_zh": "SET 教学基础",
    "title": "SET Teaching Fundamentals",
    "why_template": "Learn the fundamentals of SET teaching.",
    "how_summary": (
        "Complete the three SET videos and their three paired quizzes in "
        "Kuozhi."
    ),
    "completion_standard": (
        "All three required videos and all three paired quizzes reach 100% "
        "progress in Kuozhi."
    ),
    "benefit": "You understand the SET teaching foundation.",
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


def _validated_row(
    bind: sa.engine.Connection,
    *,
    expected_copy: Mapping[str, str],
) -> Mapping[str, Any]:
    row = bind.execute(
        sa.select(
            _templates.c.row_id,
            _templates.c.template_id,
            _templates.c.template_version,
            _templates.c.status,
            _templates.c.revision,
            _templates.c.payload,
        ).where(_templates.c.row_id == ROW_ID)
    ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("G09 SET migration requires stable row G10:v1")

    payload = row["payload"]
    if (
        row["template_id"] != "G09"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(payload, Mapping)
        or payload.get("template_id") != "G09"
        or payload.get("category") != "MANDATORY_GROWTH"
        or payload.get("score_type") != "FIXED"
        or payload.get("score_value") != 5
        or payload.get("content_status") != "READY"
    ):
        raise RuntimeError(
            "G09 SET migration requires the stable published five-point G09 "
            "template identity"
        )

    current_copy = {field: payload.get(field) for field in expected_copy}
    if current_copy != expected_copy:
        raise RuntimeError("G09 SET migration found unreviewed copy drift")
    return row


def _apply_copy(
    *,
    source_copy: Mapping[str, str],
    target_copy: Mapping[str, str],
    actor: str,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    row = _validated_row(bind, expected_copy=source_copy)
    payload = dict(row["payload"] or {})
    payload.update(target_copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == ROW_ID,
            _templates.c.template_id == "G09",
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
        raise RuntimeError("G09 SET migration did not update exactly one row")


def upgrade() -> None:
    _apply_copy(
        source_copy=OLD_COPY,
        target_copy=NEW_COPY,
        actor="SYSTEM_MIGRATION_20260819_65_G09_SET_COURSE",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        source_copy=NEW_COPY,
        target_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260819_65_G09_SET_COURSE_DOWN",
        revision_delta=-1,
    )
