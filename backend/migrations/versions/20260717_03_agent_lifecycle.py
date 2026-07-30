"""allow scheduled assignments to remain unactivated

Revision ID: 20260717_03_agent_lifecycle
Revises: 20260717_02_config
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_03_agent_lifecycle"
down_revision: Union[str, None] = "20260717_02_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "task_assignments",
        "assigned_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "task_assignments",
        "original_due_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "task_assignments",
        "due_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    # A downgrade is intentionally refused when scheduled rows still have null
    # activation timestamps. Operators must activate/withdraw them first.
    op.alter_column(
        "task_assignments",
        "due_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "task_assignments",
        "original_due_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "task_assignments",
        "assigned_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
