"""grant minimum score read-model permissions

Revision ID: 20260727_22_score_read_acl
Revises: 20260727_21_score_read
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260727_22_score_read_acl"
down_revision: Union[str, None] = "20260727_21_score_read"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON TABLE public.score_component_accounts
                    TO tit_growth_app;
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_score_dimension_current,
                    public.teacher_score_component_current,
                    public.teacher_lesson_score_current
                    TO tit_growth_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                GRANT SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_score_dimension_current,
                    public.teacher_score_component_current,
                    public.teacher_lesson_score_current
                    TO tit_teacher_crud;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                REVOKE ALL PRIVILEGES
                    ON TABLE public.score_component_accounts
                    FROM tit_growth_app;
                REVOKE SELECT ON TABLE
                    public.teacher_scorecard_current,
                    public.teacher_score_dimension_current,
                    public.teacher_score_component_current,
                    public.teacher_lesson_score_current
                    FROM tit_growth_app;
            END IF;
        END
        $$;
        """
    )
