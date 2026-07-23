"""store peak flags and split negative-feedback labels

Revision ID: 20260724_19_lesson_feedback
Revises: 20260723_18_fixed_baseline
Create Date: 2026-07-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260724_19_lesson_feedback"
down_revision: Union[str, None] = "20260723_18_fixed_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lesson_facts", sa.Column("is_peak", sa.Boolean(), nullable=True))
    op.add_column("lesson_facts", sa.Column("feedback_detail", sa.Text(), nullable=True))
    op.add_column(
        "lesson_facts",
        sa.Column(
            "negative_tag_values",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.drop_index("ix_lesson_fact_negative_tag", table_name="lesson_facts")
    op.drop_column("lesson_facts", "negative_tag_value")


def downgrade() -> None:
    op.add_column(
        "lesson_facts",
        sa.Column("negative_tag_value", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_lesson_fact_negative_tag",
        "lesson_facts",
        ["teacher_id", "negative_tag_value"],
        unique=False,
    )
    op.drop_column("lesson_facts", "negative_tag_values")
    op.drop_column("lesson_facts", "feedback_detail")
    op.drop_column("lesson_facts", "is_peak")
