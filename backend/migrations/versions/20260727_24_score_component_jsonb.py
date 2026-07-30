"""align score component payload with the shared PostgreSQL JSONB convention

Revision ID: 20260727_24_component_jsonb
Revises: 20260727_23_lesson_score
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_24_component_jsonb"
down_revision: Union[str, None] = "20260727_23_lesson_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_component_view() -> None:
    op.execute(
        """
        CREATE VIEW public.teacher_score_component_current AS
        SELECT
            teacher_id,
            dimension,
            component_code,
            source_scope,
            source_metric,
            unit_count,
            points_per_unit,
            current_score,
            lesson_attributed_count,
            lesson_attributed_score,
            unattributed_score,
            reconciliation_status,
            score_rule_version,
            source_teacher_batch_id,
            source_lesson_batch_id,
            projection_revision,
            calculated_at,
            payload
        FROM public.score_component_accounts
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                GRANT SELECT ON TABLE public.teacher_score_component_current
                TO tit_growth_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud') THEN
                GRANT SELECT ON TABLE public.teacher_score_component_current
                TO tit_teacher_crud;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP VIEW public.teacher_score_component_current")
    op.alter_column(
        "score_component_accounts",
        "payload",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="payload::jsonb",
    )
    _create_component_view()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP VIEW public.teacher_score_component_current")
    op.alter_column(
        "score_component_accounts",
        "payload",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        existing_nullable=False,
        postgresql_using="payload::json",
    )
    _create_component_view()
