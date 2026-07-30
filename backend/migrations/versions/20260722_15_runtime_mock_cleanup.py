"""mark the current task catalog as real business configuration

Revision ID: 20260722_15_runtime_mock_cleanup
Revises: 20260722_14_lesson_evidence
Create Date: 2026-07-22

G01-G10 are approved mandatory-growth definitions, not test fixtures.  Earlier
local migrations labeled them as MOCK while the teacher-side integration was
still simulated.  Keep the definitions and correct only their provenance.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_15_runtime_mock_cleanup"
down_revision: Union[str, None] = "20260722_14_lesson_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MANDATORY_TASK_CODES = tuple(f"G{number:02d}" for number in range(1, 11))


def _set_mandatory_source_mode(source_mode: str) -> None:
    task_templates = sa.table(
        "task_templates",
        sa.column("row_id", sa.String()),
        sa.column("template_id", sa.String()),
        sa.column("source_mode", sa.String()),
        sa.column("payload", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(task_templates.c.row_id, task_templates.c.payload).where(
            task_templates.c.template_id.in_(MANDATORY_TASK_CODES)
        )
    ).mappings()
    for row in rows:
        payload = deepcopy(row["payload"] or {})
        payload["source_mode"] = source_mode
        connection.execute(
            task_templates.update()
            .where(task_templates.c.row_id == row["row_id"])
            .values(source_mode=source_mode, payload=payload)
        )


def upgrade() -> None:
    _set_mandatory_source_mode("REAL")


def downgrade() -> None:
    _set_mandatory_source_mode("MOCK")
