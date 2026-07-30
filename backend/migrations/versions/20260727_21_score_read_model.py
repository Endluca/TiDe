"""persist score components and lesson score read models

Revision ID: 20260727_21_score_read
Revises: 20260724_20_task_why_en
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260727_21_score_read"
down_revision: Union[str, None] = "20260724_20_task_why_en"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _postgres_read_views() -> None:
    op.execute(
        """
        CREATE VIEW public.teacher_scorecard_current AS
        SELECT
            t.teacher_id,
            s.raw_total_score,
            s.public_total_score,
            s.score_rule_version,
            s.score_policy_sha256,
            s.updated_at AS calculated_at
        FROM public.teachers AS t
        JOIN public.teacher_metric_snapshots AS s
          ON s.teacher_id = t.teacher_id
         AND s.batch_id = t.source_batch_id
        """
    )
    op.execute(
        """
        CREATE VIEW public.teacher_score_dimension_current AS
        SELECT
            teacher_id,
            dimension,
            current_score,
            score_rule_version,
            version AS projection_revision,
            updated_at AS calculated_at,
            payload
        FROM public.score_accounts
        """
    )
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
        CREATE VIEW public.teacher_lesson_score_current AS
        SELECT
            s.teacher_id,
            s.lesson_id,
            f.lesson_local_date,
            f.lesson_local_time,
            f.lesson_lifecycle_status,
            s.dimension,
            s.current_score,
            s.evidence_status,
            s.evidence_coverage,
            s.score_rule_version,
            s.current_revision,
            s.score_as_of,
            s.updated_at,
            s.payload
        FROM public.lesson_dimension_scores AS s
        JOIN public.lesson_facts AS f ON f.lesson_id = s.lesson_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
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


def upgrade() -> None:
    op.add_column(
        "lesson_dimension_scores",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_table(
        "score_component_accounts",
        sa.Column("component_account_id", sa.String(length=192), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("camp_enrollment_id", sa.String(length=96), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("component_code", sa.String(length=64), nullable=False),
        sa.Column("source_scope", sa.String(length=16), nullable=False),
        sa.Column("source_metric", sa.String(length=128), nullable=True),
        sa.Column("unit_count", sa.Float(), nullable=False),
        sa.Column("points_per_unit", sa.Float(), nullable=True),
        sa.Column("current_score", sa.Float(), nullable=False),
        sa.Column("lesson_attributed_count", sa.Integer(), nullable=False),
        sa.Column("lesson_attributed_score", sa.Float(), nullable=False),
        sa.Column("unattributed_score", sa.Float(), nullable=False),
        sa.Column("reconciliation_status", sa.String(length=24), nullable=False),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column("source_teacher_batch_id", sa.String(length=160), nullable=True),
        sa.Column("source_lesson_batch_id", sa.String(length=160), nullable=True),
        sa.Column("projection_revision", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "source_scope IN ('LESSON', 'TEACHER', 'TASK')",
            name="ck_score_component_account_source_scope",
        ),
        sa.CheckConstraint(
            "reconciliation_status IN "
            "('MATCHED', 'MATCHED_ZERO', 'PARTIAL', 'MISMATCH', "
            "'SOURCE_MISSING', 'NOT_APPLICABLE')",
            name="ck_score_component_account_reconciliation",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.teacher_id"],
            name="fk_score_component_account_teacher",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("component_account_id"),
        sa.UniqueConstraint(
            "teacher_id",
            "component_code",
            name="uq_score_component_account_teacher_component",
        ),
    )
    op.create_index(
        "ix_score_component_account_teacher_id",
        "score_component_accounts",
        ["teacher_id"],
    )
    op.create_index(
        "ix_score_component_account_camp_enrollment_id",
        "score_component_accounts",
        ["camp_enrollment_id"],
    )
    op.create_index(
        "ix_score_component_account_teacher_dimension",
        "score_component_accounts",
        ["teacher_id", "dimension"],
    )
    op.create_index(
        "ix_score_component_account_source_teacher_batch_id",
        "score_component_accounts",
        ["source_teacher_batch_id"],
    )
    op.create_index(
        "ix_score_component_account_source_lesson_batch_id",
        "score_component_accounts",
        ["source_lesson_batch_id"],
    )
    op.create_index(
        "ix_score_component_account_calculated_at",
        "score_component_accounts",
        ["calculated_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        _postgres_read_views()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name in (
            "teacher_lesson_score_current",
            "teacher_score_component_current",
            "teacher_score_dimension_current",
            "teacher_scorecard_current",
        ):
            op.execute(f"DROP VIEW IF EXISTS public.{name}")

    op.drop_index(
        "ix_score_component_account_calculated_at",
        table_name="score_component_accounts",
    )
    op.drop_index(
        "ix_score_component_account_source_lesson_batch_id",
        table_name="score_component_accounts",
    )
    op.drop_index(
        "ix_score_component_account_source_teacher_batch_id",
        table_name="score_component_accounts",
    )
    op.drop_index(
        "ix_score_component_account_teacher_dimension",
        table_name="score_component_accounts",
    )
    op.drop_index(
        "ix_score_component_account_camp_enrollment_id",
        table_name="score_component_accounts",
    )
    op.drop_index(
        "ix_score_component_account_teacher_id",
        table_name="score_component_accounts",
    )
    op.drop_table("score_component_accounts")
    op.drop_column("lesson_dimension_scores", "updated_at")
