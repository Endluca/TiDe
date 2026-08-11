"""align source-wide tables with the confirmed v1.2 mapping contract

Revision ID: 20260811_55_source_wide_v12
Revises: 20260811_54_g04_remove_device_check
Create Date: 2026-08-11

The lesson contract remains exactly 23 columns.  The teacher contract now has
53 mapped business fields plus the two nullable G01 status facts.  Eight
retired teacher columns are removed without CASCADE.  The downgrade is allowed
only on an empty source table because the removed values cannot be reconstructed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_55_source_wide_v12"
down_revision: Union[str, None] = "20260811_54_g04_remove_device_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RETIRED_TEACHER_COLUMNS: tuple[str, ...] = (
    "tchr_group",
    "tchr_group_desc",
    "based_type",
    "is_ft_hbt",
    "is_fte",
    "tchr_score",
    "completed_again_student_15d_cnt",
    "feedback_rebook_rate",
)


def _assert_contract_shape(*, teacher_count: int, phase: str) -> None:
    retired_values = ", ".join(
        "'{}'".format(column.replace("'", "''"))
        for column in RETIRED_TEACHER_COLUMNS
    )
    op.execute(
        sa.text(
            f"""
            DO $source_wide_v12_{phase}$
            DECLARE
                actual_teacher_count integer;
                actual_lesson_count integer;
            BEGIN
                SELECT count(*)
                INTO actual_teacher_count
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'teacher_source_wide';

                SELECT count(*)
                INTO actual_lesson_count
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'lesson_source_wide';

                IF actual_teacher_count <> {teacher_count} THEN
                    RAISE EXCEPTION
                        'teacher_source_wide column count mismatch during {phase}: expected %, actual %',
                        {teacher_count}, actual_teacher_count;
                END IF;
                IF actual_lesson_count <> 23 THEN
                    RAISE EXCEPTION
                        'lesson_source_wide must remain the confirmed 23-column contract';
                END IF;

                IF '{phase}' = 'before_upgrade' AND EXISTS (
                    SELECT 1
                    FROM unnest(ARRAY[{retired_values}]::text[]) retired(column_name)
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns source_column
                        WHERE source_column.table_schema = 'public'
                          AND source_column.table_name = 'teacher_source_wide'
                          AND source_column.column_name = retired.column_name
                    )
                ) THEN
                    RAISE EXCEPTION
                        'teacher_source_wide is missing a retired v1.1 column before upgrade';
                END IF;

                IF '{phase}' = 'after_upgrade' AND EXISTS (
                    SELECT 1
                    FROM information_schema.columns source_column
                    WHERE source_column.table_schema = 'public'
                      AND source_column.table_name = 'teacher_source_wide'
                      AND source_column.column_name = ANY(
                          ARRAY[{retired_values}]::text[]
                      )
                ) THEN
                    RAISE EXCEPTION
                        'teacher_source_wide still contains a retired v1.1 column after upgrade';
                END IF;
            END
            $source_wide_v12_{phase}$;
            """
        )
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _assert_contract_shape(teacher_count=63, phase="before_upgrade")
    for column_name in RETIRED_TEACHER_COLUMNS:
        op.drop_column(
            "teacher_source_wide",
            column_name,
            schema="public",
        )
    _assert_contract_shape(teacher_count=55, phase="after_upgrade")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            DO $source_wide_v12_downgrade_guard$
            BEGIN
                IF EXISTS (SELECT 1 FROM public.teacher_source_wide LIMIT 1) THEN
                    RAISE EXCEPTION
                        'refusing source-wide v1.2 downgrade: removed column values cannot be restored for existing rows';
                END IF;
            END
            $source_wide_v12_downgrade_guard$;
            """
        )
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("tchr_group", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("tchr_group_desc", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("based_type", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("is_ft_hbt", sa.Boolean(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("is_fte", sa.Boolean(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("tchr_score", sa.Float(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("completed_again_student_15d_cnt", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column("feedback_rebook_rate", sa.Float(), nullable=True),
        schema="public",
    )
