"""link fixed-growth awards to the shared task ledger

Revision ID: 20260722_12_task_score
Revises: 20260722_11_teacher_profile
Create Date: 2026-07-22

The nullable foreign key leaves every historical score row untouched.  Only
new ``FIXED_TASK_AWARD`` rows use the assignment reference, and the partial
unique index makes one assignment awardable at most once.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_12_task_score"
down_revision: Union[str, None] = "20260722_11_teacher_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "score_entries",
        sa.Column("task_assignment_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_score_entries_task_assignment_id",
        "score_entries",
        "task_assignments",
        ["task_assignment_id"],
        ["assignment_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_score_entries_task_assignment_id",
        "score_entries",
        ["task_assignment_id"],
        unique=False,
    )
    op.create_index(
        "uq_score_entries_fixed_task_assignment",
        "score_entries",
        ["task_assignment_id"],
        unique=True,
        postgresql_where=sa.text(
            "entry_type = 'FIXED_TASK_AWARD' AND task_assignment_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_score_entries_fixed_task_assignment",
        table_name="score_entries",
    )
    op.drop_index("ix_score_entries_task_assignment_id", table_name="score_entries")
    op.drop_constraint(
        "fk_score_entries_task_assignment_id",
        "score_entries",
        type_="foreignkey",
    )
    op.drop_column("score_entries", "task_assignment_id")
