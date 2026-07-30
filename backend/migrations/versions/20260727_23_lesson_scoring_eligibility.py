"""mark completed real lessons eligible for score attribution

Revision ID: 20260727_23_lesson_score
Revises: 20260727_22_score_read_acl
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260727_23_lesson_score"
down_revision: Union[str, None] = "20260727_22_score_read_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE lesson_facts
        SET valid_for_scoring = CASE
            WHEN lower(trim(lesson_lifecycle_status)) IN
                ('end', 'ended', 'complete', 'completed', 'finished', '已完课', '完课')
                THEN TRUE
            ELSE FALSE
        END
        WHERE data_mode = 'REAL'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE lesson_facts
        SET valid_for_scoring = FALSE
        WHERE data_mode = 'REAL'
        """
    )
