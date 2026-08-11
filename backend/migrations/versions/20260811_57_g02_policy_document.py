"""switch G02 to the versioned Overseas NT Policies document

Revision ID: 20260811_57_g02_document
Revises: 20260811_56_p_fb_negative_copy
Create Date: 2026-08-11

G02 keeps its code, score, published status and assignment lifecycle. This
controlled catalog update changes only the teacher-facing How and completion
copy on the stable ``G03:v1`` row that was remapped to current G02 by revision
30. Existing completed assignments remain terminal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_57_g02_document"
down_revision: Union[str, None] = "20260811_56_p_fb_negative_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COPY: dict[str, str] = {
    "how_summary": "Read the in-platform policy guide and complete its quiz.",
    "completion_standard": (
        "The policy guide is confirmed and the quiz requirements pass."
    ),
}

NEW_COPY: dict[str, str] = {
    "how_summary": (
        "Read the current Overseas NT Policies document in TIDE. Your reading "
        "progress is saved automatically."
    ),
    "completion_standard": (
        "G02 is completed automatically after you reach the end of the current "
        "published document."
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


G02_STABLE_ROW_ID = "G03:v1"


def _g02_row(bind: sa.engine.Connection) -> Mapping[str, Any]:
    rows = list(
        bind.execute(
            sa.select(
                _templates.c.row_id,
                _templates.c.template_id,
                _templates.c.template_version,
                _templates.c.status,
                _templates.c.revision,
                _templates.c.payload,
            ).where(_templates.c.row_id == G02_STABLE_ROW_ID)
        ).mappings()
    )
    if len(rows) != 1:
        raise RuntimeError(
            "G02 document migration requires exactly one stable G03:v1 template row"
        )
    row = rows[0]
    if (
        row["template_id"] != "G02"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(row["payload"], Mapping)
        or row["payload"].get("template_id") != "G02"
        or row["payload"].get("title") != "Platform Policies"
        or row["payload"].get("score_value") != 2
    ):
        raise RuntimeError(
            "G02 document migration requires stable G03:v1 to be the published two-point G02 version"
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
    row = _g02_row(bind)
    payload = dict(row["payload"] or {})
    current_copy = {field: payload.get(field) for field in expected_copy}
    if current_copy != dict(expected_copy):
        raise RuntimeError(
            "G02 document migration found unreviewed teacher-facing copy drift"
        )

    payload.update(copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == G02_STABLE_ROW_ID,
            _templates.c.template_id == "G02",
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
        raise RuntimeError("G02 document migration did not update exactly one row")


def upgrade() -> None:
    _apply_copy(
        NEW_COPY,
        expected_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260811_51_G02_DOCUMENT",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        OLD_COPY,
        expected_copy=NEW_COPY,
        actor="SYSTEM_MIGRATION_20260811_51_G02_DOCUMENT_DOWN",
        revision_delta=-1,
    )
