"""align G05 and G08 with their published Kuozhi courses

Revision ID: 20260819_64_g05_g08_courses
Revises: 20260819_63_dts_direct_privacy
Create Date: 2026-08-19

This controlled content migration validates the complete code-canonical copy
and changes the published How and completion copy for G05 and G08. Existing
G08 assignments created with the retired Cocos reason/title are corrected in
place; their status, score and completion facts remain unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260819_64_g05_g08_courses"
down_revision: Union[str, None] = "20260819_63_dts_direct_privacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TARGETS: dict[str, dict[str, Any]] = {
    "G06:v1": {
        "template_id": "G05",
        "category": "MANDATORY_GROWTH",
        "score_type": "FIXED",
        "score_value": 3,
        "old_copy": {
            "ops_name_zh": "TTP 入门",
            "title": "TTP Orientation",
            "why_template": "Understand TTP and its key business scenarios.",
            "how_summary": (
                "Watch the in-platform TTP video and confirm every item in "
                "the learning checklist."
            ),
            "completion_standard": (
                "The TTP video is watched in full and every published "
                "checklist item is confirmed."
            ),
            "benefit": "You understand the key TTP workflow and commitments.",
        },
        "new_copy": {
            "ops_name_zh": "TTP 入门",
            "title": "TTP Orientation",
            "why_template": "Understand TTP and its key business scenarios.",
            "how_summary": "Complete the TTP video and Quiz in Kuozhi.",
            "completion_standard": (
                "The TTP video reaches 100% progress and the Quiz is "
                "completed in Kuozhi."
            ),
            "benefit": "You understand the key TTP workflow and commitments.",
        },
    },
    "G09:v1": {
        "template_id": "G08",
        "category": "MANDATORY_GROWTH",
        "score_type": "FIXED",
        "score_value": 5,
        "old_copy": {
            "ops_name_zh": "Global Communicator 培训",
            "title": "Global Communicator Training",
            "why_template": (
                "Learn the core Global Communicator teaching flow."
            ),
            "how_summary": "Complete the configured in-platform videos and quiz.",
            "completion_standard": (
                "All configured videos and quiz requirements pass."
            ),
            "benefit": (
                "You can now confidently prepare for a Global Communicator "
                "lesson."
            ),
        },
        "new_copy": {
            "ops_name_zh": "Global Communicator 培训",
            "title": "Global Communicator Training",
            "why_template": (
                "Learn the core Global Communicator teaching flow."
            ),
            "how_summary": (
                "Complete all six Global Communicator Sample Lessons videos "
                "in Kuozhi."
            ),
            "completion_standard": (
                "All six required videos reach 100% progress in Kuozhi."
            ),
            "benefit": (
                "You can now confidently prepare for a Global Communicator "
                "lesson."
            ),
        },
    },
}

OLD_G08_WHY = "Learn the core Cocos teaching flow."
NEW_G08_WHY = "Learn the core Global Communicator teaching flow."
OLD_G08_TITLE = "Cocos Course Training"
NEW_G08_TITLE = "Global Communicator Training"


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

_assignments = sa.table(
    "task_assignments",
    sa.column("assignment_id", sa.String()),
    sa.column("task_code", sa.String()),
    sa.column("task_kind", sa.String()),
    sa.column("why", sa.String()),
    sa.column("display_title", sa.String()),
    sa.column("row_version", sa.Integer()),
    sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _validated_templates(
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
            "G05/G08 course migration requires exactly the two stable target rows"
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
            or payload.get("content_status") != "READY"
        ):
            raise RuntimeError(
                "G05/G08 course migration requires stable published template "
                f"identity for {row_id}"
            )

        expected_copy = target[expected_copy_key]
        current_copy = {field: payload.get(field) for field in expected_copy}
        if current_copy != expected_copy:
            raise RuntimeError(
                "G05/G08 course migration found unreviewed copy drift for "
                f"{row_id}"
            )
    return rows_by_id


def _apply_template_copy(
    *,
    source_copy_key: str,
    target_copy_key: str,
    actor: str,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    rows_by_id = _validated_templates(
        bind,
        expected_copy_key=source_copy_key,
    )
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
                "G05/G08 course migration did not update exactly one row for "
                f"{row_id}"
            )


def _update_existing_g08_assignments() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                _assignments.c.assignment_id,
                _assignments.c.why,
                _assignments.c.display_title,
            ).where(
                _assignments.c.task_code == "G08",
                _assignments.c.task_kind == "FIXED_GROWTH",
            )
        ).mappings()
    )
    allowed_whys = {OLD_G08_WHY, NEW_G08_WHY}
    allowed_titles = {None, "", OLD_G08_TITLE, NEW_G08_TITLE}
    if any(
        row["why"] not in allowed_whys
        or row["display_title"] not in allowed_titles
        for row in rows
    ):
        raise RuntimeError(
            "G08 assignment copy drift must be reviewed before migration"
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER trg_task_assignment_write ON public.task_assignments"
        )

    bind.execute(
        sa.update(_assignments)
        .where(
            _assignments.c.task_code == "G08",
            _assignments.c.task_kind == "FIXED_GROWTH",
            sa.or_(
                _assignments.c.why == OLD_G08_WHY,
                _assignments.c.display_title == OLD_G08_TITLE,
            ),
        )
        .values(
            why=sa.case(
                (_assignments.c.why == OLD_G08_WHY, NEW_G08_WHY),
                else_=_assignments.c.why,
            ),
            display_title=sa.case(
                (
                    _assignments.c.display_title == OLD_G08_TITLE,
                    NEW_G08_TITLE,
                ),
                else_=_assignments.c.display_title,
            ),
            row_version=_assignments.c.row_version + 1,
            updated_by="SYSTEM_MIGRATION_20260819_64_G05_G08",
            updated_at=datetime.now(timezone.utc),
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


def upgrade() -> None:
    _apply_template_copy(
        source_copy_key="old_copy",
        target_copy_key="new_copy",
        actor="SYSTEM_MIGRATION_20260819_64_G05_G08",
        revision_delta=1,
    )
    _update_existing_g08_assignments()


def downgrade() -> None:
    _apply_template_copy(
        source_copy_key="new_copy",
        target_copy_key="old_copy",
        actor="SYSTEM_MIGRATION_20260819_64_G05_G08_DOWN",
        revision_delta=-1,
    )
    # Corrected assignment copy remains teacher-safe when the catalog revision
    # is downgraded; task lifecycle facts are never rewritten backwards.
