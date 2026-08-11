"""generalize P-FB-NEGATIVE improvement activity copy

Revision ID: 20260811_56_p_fb_negative_copy
Revises: 20260811_55_source_wide_v12
Create Date: 2026-08-11

P-FB-NEGATIVE keeps its stable template identity, zero-point contract and
assignment lifecycle. This controlled catalog update changes only the
teacher-facing How and completion copy on ``P-FB-NEGATIVE:v1`` so that the
same task template can describe either an environment-photo review or another
configured improvement activity. Existing task assignments and their status
facts are not updated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_56_p_fb_negative_copy"
down_revision: Union[str, None] = "20260811_55_source_wide_v12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COPY: dict[str, str] = {
    "how_summary": (
        "Complete the learning activity assigned for the feedback issue shown "
        "in the task reason."
    ),
    "completion_standard": (
        "The teacher app marks the matching learning activity as completed."
    ),
}

NEW_COPY: dict[str, str] = {
    "how_summary": (
        "Complete the configured improvement activity for the feedback issue "
        "shown in the task reason. Depending on the assigned activity, you may "
        "need to submit a teaching-environment photo for review or complete "
        "another guided action."
    ),
    "completion_standard": (
        "The teacher app marks the task as completed after every requirement "
        "for the assigned improvement activity, including any required photo "
        "review, is satisfied."
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


STABLE_ROW_ID = "P-FB-NEGATIVE:v1"


def _stable_template_row(bind: sa.engine.Connection) -> Mapping[str, Any]:
    rows = list(
        bind.execute(
            sa.select(
                _templates.c.row_id,
                _templates.c.template_id,
                _templates.c.template_version,
                _templates.c.status,
                _templates.c.revision,
                _templates.c.payload,
            ).where(_templates.c.row_id == STABLE_ROW_ID)
        ).mappings()
    )
    if len(rows) != 1:
        raise RuntimeError(
            "P-FB-NEGATIVE copy migration requires exactly one stable "
            "P-FB-NEGATIVE:v1 template row"
        )
    row = rows[0]
    payload = row["payload"]
    if (
        row["template_id"] != "P-FB-NEGATIVE"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(payload, Mapping)
        or payload.get("template_id") != "P-FB-NEGATIVE"
        or payload.get("category") != "PERSONALIZED_IMPROVEMENT"
        or payload.get("title") != "Feedback Improvement"
        or payload.get("score_type") != "ZERO"
        or payload.get("score_value") != 0
    ):
        raise RuntimeError(
            "P-FB-NEGATIVE copy migration requires the stable published "
            "zero-point personalized template"
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
    row = _stable_template_row(bind)
    payload = dict(row["payload"] or {})
    current_copy = {field: payload.get(field) for field in expected_copy}
    if current_copy != dict(expected_copy):
        raise RuntimeError(
            "P-FB-NEGATIVE copy migration found unreviewed teacher-facing "
            "copy drift"
        )

    payload.update(copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == STABLE_ROW_ID,
            _templates.c.template_id == "P-FB-NEGATIVE",
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
        raise RuntimeError(
            "P-FB-NEGATIVE copy migration did not update exactly one row"
        )


def upgrade() -> None:
    _apply_copy(
        NEW_COPY,
        expected_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260811_56_P_FB_NEGATIVE_COPY",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        OLD_COPY,
        expected_copy=NEW_COPY,
        actor="SYSTEM_MIGRATION_20260811_56_P_FB_NEGATIVE_COPY_DOWN",
        revision_delta=-1,
    )
