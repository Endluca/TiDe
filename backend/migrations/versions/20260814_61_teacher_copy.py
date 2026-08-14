"""align the remaining reviewed teacher-facing English copy

Revision ID: 20260814_61_teacher_copy
Revises: 20260813_60_dom_privacy
Create Date: 2026-08-14

This controlled catalog update keeps stable template identities, scores and
assignment lifecycle facts unchanged. It updates only the reviewed copy for
G01, G08, Lesson Memo and Attendance. Existing task assignments are not
rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260814_61_teacher_copy"
down_revision: Union[str, None] = "20260813_60_dom_privacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TARGETS: dict[str, dict[str, Any]] = {
    "G01:v1": {
        "template_id": "G01",
        "category": "MANDATORY_GROWTH",
        "score_type": "FIXED",
        "score_value": 3,
        "old_copy": {
            "benefit": (
                "Your profile and required TESOL learning evidence are complete."
            ),
        },
        "new_copy": {
            "benefit": (
                "Your profile and required TESOL learning evidence are now "
                "complete."
            ),
        },
    },
    "G09:v1": {
        "template_id": "G08",
        "category": "MANDATORY_GROWTH",
        "score_type": "FIXED",
        "score_value": 5,
        "old_copy": {
            "ops_name_zh": "Cocos 课程培训",
            "title": "Cocos Course Training",
            "why_template": "Learn the core Cocos teaching flow.",
            "benefit": "You can prepare for a Cocos class.",
        },
        "new_copy": {
            "ops_name_zh": "Global Communicator 培训",
            "title": "Global Communicator Training",
            "why_template": "Learn the core Global Communicator teaching flow.",
            "benefit": (
                "You can now confidently prepare for a Global Communicator "
                "lesson."
            ),
        },
    },
    "P-REL-MEMO:v1": {
        "template_id": "P-REL-MEMO",
        "category": "PERSONALIZED_IMPROVEMENT",
        "score_type": "ZERO",
        "score_value": 0,
        "old_copy": {
            "why_template": (
                "A completed lesson was recorded with an unfilled Lesson Memo."
            ),
            "benefit": (
                "This task carries no points. It closes the identified Lesson "
                "Memo reliability gap."
            ),
        },
        "new_copy": {
            "why_template": (
                "A completed lesson was recorded with a blank Lesson Memo."
            ),
            "benefit": (
                "This task carries no points. It helps strengthen your Lesson "
                "Memo reliability."
            ),
        },
    },
    "P-REL-ATTENDANCE:v1": {
        "template_id": "P-REL-ATTENDANCE",
        "category": "PERSONALIZED_IMPROVEMENT",
        "score_type": "ZERO",
        "score_value": 0,
        "old_copy": {
            "why_template": (
                "A lesson record contains a reliability issue such as absence, "
                "late arrival or early leave."
            ),
        },
        "new_copy": {
            "why_template": (
                "A lesson record shows a reliability issue, such as an absence, "
                "late arrival, or early leave."
            ),
        },
    },
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


def _validated_rows(
    bind: sa.engine.Connection,
    *,
    expected_copy_key: str,
) -> dict[str, Mapping[str, Any]]:
    rows = list(
        bind.execute(
            sa.select(
                _templates.c.row_id,
                _templates.c.template_id,
                _templates.c.template_version,
                _templates.c.status,
                _templates.c.revision,
                _templates.c.payload,
            ).where(_templates.c.row_id.in_(tuple(TARGETS)))
        ).mappings()
    )
    rows_by_id = {str(row["row_id"]): row for row in rows}
    if set(rows_by_id) != set(TARGETS):
        raise RuntimeError(
            "teacher copy migration requires exactly the four stable target rows"
        )

    for row_id, target in TARGETS.items():
        row = rows_by_id[row_id]
        payload = row["payload"]
        if (
            row["template_id"] != target["template_id"]
            or row["template_version"] != 1
            or row["status"] != "PUBLISHED"
            or not isinstance(payload, Mapping)
            or payload.get("template_id") != target["template_id"]
            or payload.get("category") != target["category"]
            or payload.get("score_type") != target["score_type"]
            or payload.get("score_value") != target["score_value"]
        ):
            raise RuntimeError(
                "teacher copy migration requires stable published template "
                f"identity for {row_id}"
            )

        expected_copy = target[expected_copy_key]
        current_copy = {field: payload.get(field) for field in expected_copy}
        if current_copy != expected_copy:
            raise RuntimeError(
                "teacher copy migration found unreviewed copy drift for "
                f"{row_id}"
            )
    return rows_by_id


def _apply_copy(
    *,
    source_copy_key: str,
    target_copy_key: str,
    actor: str,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    rows_by_id = _validated_rows(bind, expected_copy_key=source_copy_key)
    updated_at = datetime.now(timezone.utc)

    for row_id, target in TARGETS.items():
        row = rows_by_id[row_id]
        payload = dict(row["payload"] or {})
        payload.update(target[target_copy_key])
        result = bind.execute(
            sa.update(_templates)
            .where(
                _templates.c.row_id == row_id,
                _templates.c.template_id == target["template_id"],
                _templates.c.template_version == 1,
                _templates.c.status == "PUBLISHED",
                _templates.c.revision == row["revision"],
            )
            .values(
                payload=payload,
                revision=_templates.c.revision + revision_delta,
                updated_by=actor,
                updated_at=updated_at,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError(
                "teacher copy migration did not update exactly one row for "
                f"{row_id}"
            )


def upgrade() -> None:
    _apply_copy(
        source_copy_key="old_copy",
        target_copy_key="new_copy",
        actor="SYSTEM_MIGRATION_20260814_61_TEACHER_COPY",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        source_copy_key="new_copy",
        target_copy_key="old_copy",
        actor="SYSTEM_MIGRATION_20260814_61_TEACHER_COPY_DOWN",
        revision_delta=-1,
    )
