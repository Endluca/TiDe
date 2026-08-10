"""add nullable G01 status facts to the teacher source-wide table

Revision ID: 20260807_46_teacher_g01_source
Revises: 20260806_45_source_runtime_acl
Create Date: 2026-08-07

The original teacher CSV remains a 61-column contract.  These two nullable
source facts are appended for teacher-side G01 validation.  No timestamp,
version, hash, or sync-batch column is introduced.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "20260807_46_teacher_g01_source"
down_revision: str | None = "20260806_45_source_runtime_acl"
branch_labels: str | None = None
depends_on: str | None = None


G01_SOURCE_COLUMNS: tuple[str, ...] = (
    "is_cpl_tesol",
    "is_self_introduce",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for column_name in G01_SOURCE_COLUMNS:
        op.add_column(
            "teacher_source_wide",
            sa.Column(column_name, sa.Boolean(), nullable=True),
            schema="public",
        )

    # Teacher-side G01 needs only the stable teacher key and the two status
    # facts.  It must not gain access to the rest of the wide row, and the old
    # snapshot read is removed at the same boundary.
    op.execute(
        """
        DO $teacher_g01_source_acl$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.teacher_source_wide,
                    public.teacher_metric_snapshots
                FROM tit_teacher_crud;

                GRANT SELECT (
                    tchr_id,
                    is_cpl_tesol,
                    is_self_introduce
                ) ON TABLE public.teacher_source_wide
                TO tit_teacher_crud;

                IF has_table_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'SELECT'
                ) OR has_column_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'real_name',
                    'SELECT'
                ) OR NOT has_column_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'tchr_id',
                    'SELECT'
                ) OR NOT has_column_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'is_cpl_tesol',
                    'SELECT'
                ) OR NOT has_column_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'is_self_introduce',
                    'SELECT'
                ) OR has_table_privilege(
                    'tit_teacher_crud',
                    'public.teacher_metric_snapshots',
                    'SELECT'
                ) THEN
                    RAISE EXCEPTION
                        'tit_teacher_crud G01 source privileges are invalid';
                END IF;
            END IF;
        END
        $teacher_g01_source_acl$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $teacher_g01_source_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.teacher_source_wide
                WHERE is_cpl_tesol IS NOT NULL
                   OR is_self_introduce IS NOT NULL
                LIMIT 1
            ) OR EXISTS (
                SELECT 1
                FROM public.outbox_events
                WHERE event_type = 'source_wide.changed.v1'
                  AND status IN ('PENDING', 'FAILED')
                  AND payload ->> 'source_table' = 'teacher_source_wide'
                  AND payload -> 'changed_fields' ?| ARRAY[
                        'is_cpl_tesol',
                        'is_self_introduce'
                  ]
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'refusing to drop active G01 source facts or pending events';
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                REVOKE SELECT (
                    tchr_id,
                    is_cpl_tesol,
                    is_self_introduce
                ) ON TABLE public.teacher_source_wide
                FROM tit_teacher_crud;
                GRANT SELECT ON TABLE public.teacher_metric_snapshots
                TO tit_teacher_crud;
            END IF;
        END
        $teacher_g01_source_downgrade_guard$;
        """
    )

    for column_name in reversed(G01_SOURCE_COLUMNS):
        op.drop_column(
            "teacher_source_wide",
            column_name,
            schema="public",
        )
