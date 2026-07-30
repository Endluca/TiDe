"""persist source absent count for the current Gold gate

Revision ID: 20260728_28_teacher_absent
Revises: 20260728_27_lesson_perfect
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_28_teacher_absent"
down_revision: Union[str, None] = "20260728_27_lesson_perfect"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("absent_cnt", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE teacher_metric_snapshots
        SET absent_cnt = CASE
            WHEN btrim(COALESCE(raw_payload ->> 'absent_cnt', ''))
                ~ '^[0-9]+([.]0+)?$'
            THEN (raw_payload ->> 'absent_cnt')::numeric::integer
            ELSE 0
        END
        """
    )
    op.execute(
        """
        UPDATE teacher_metric_snapshots
        SET
            metric_inputs = COALESCE(metric_inputs, '{}'::jsonb)
                || jsonb_build_object('absent_cnt', absent_cnt),
            metric_provenance = COALESCE(metric_provenance, '{}'::jsonb)
                || jsonb_build_object(
                    'absent_cnt',
                    jsonb_build_object(
                        'source_mode', 'REAL',
                        'source_field', 'absent_cnt',
                        'source_fields', jsonb_build_array('absent_cnt'),
                        'batch_id', batch_id,
                        'note',
                        'Direct absence count from the validated teacher snapshot.'
                    )
                )
        """
    )
    op.alter_column(
        "teacher_metric_snapshots",
        "absent_cnt",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE teacher_metric_snapshots
        SET
            metric_inputs = metric_inputs - 'absent_cnt',
            metric_provenance = metric_provenance - 'absent_cnt'
        """
    )
    op.drop_column("teacher_metric_snapshots", "absent_cnt")
