"""grant runtime only the source-derived writes used by atomic recalculation

Revision ID: 20260806_45_source_runtime_acl
Revises: 20260806_44_source_reads
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op


revision: str = "20260806_45_source_runtime_acl"
down_revision: str | None = "20260806_44_source_reads"
branch_labels: str | None = None
depends_on: str | None = None


LESSON_RESULT_UPDATE_COLUMNS: tuple[str, ...] = (
    "user_feedback_score",
    "reliability_score",
    "class_quality_score",
    "lesson_total_score",
    "dimensions",
    "score_rule_version",
    "projection_revision",
    "calculated_at",
)

QUALIFICATION_UPDATE_COLUMNS: tuple[str, ...] = (
    "graduation_criteria_met",
    "graduation_qualified",
    "graduation_qualified_at",
    "gold_criteria_met",
    "gold_qualified",
    "gold_qualified_at",
    "score_rule_version",
    "gate_results",
    "revision",
    "calculated_at",
)


def _columns(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    lesson_columns = _columns(LESSON_RESULT_UPDATE_COLUMNS)
    qualification_columns = _columns(QUALIFICATION_UPDATE_COLUMNS)
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            public.lesson_score_results,
            public.teacher_qualifications
        FROM tit_growth_app;

        GRANT SELECT, INSERT ON TABLE
            public.lesson_score_results,
            public.teacher_qualifications
        TO tit_growth_app;

        GRANT UPDATE ({lesson_columns})
        ON TABLE public.lesson_score_results
        TO tit_growth_app;

        GRANT UPDATE ({qualification_columns})
        ON TABLE public.teacher_qualifications
        TO tit_growth_app;
        """
    )
    op.execute(
        f"""
        DO $runtime_source_result_acl_assertions$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'public.lesson_score_results',
                    'public.teacher_qualifications'
                ]::text[]) AS relation(name)
                WHERE NOT has_table_privilege(
                    'tit_growth_app', relation.name, 'SELECT'
                ) OR NOT has_table_privilege(
                    'tit_growth_app', relation.name, 'INSERT'
                )
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app is missing source-result read/insert access';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'public.lesson_score_results',
                    'public.teacher_qualifications'
                ]::text[]) AS relation(name),
                unnest(ARRAY[
                    'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
                ]::text[]) AS privilege(name)
                WHERE has_table_privilege(
                    'tit_growth_app', relation.name, privilege.name
                )
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app received broad source-result mutation access';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    {", ".join(repr(item) for item in LESSON_RESULT_UPDATE_COLUMNS)}
                ]::text[]) AS allowed(column_name)
                WHERE NOT has_column_privilege(
                    'tit_growth_app',
                    'public.lesson_score_results',
                    allowed.column_name,
                    'UPDATE'
                )
            ) OR has_column_privilege(
                'tit_growth_app',
                'public.lesson_score_results',
                'lesson_id',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app lesson-result UPDATE columns are invalid';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    {", ".join(repr(item) for item in QUALIFICATION_UPDATE_COLUMNS)}
                ]::text[]) AS allowed(column_name)
                WHERE NOT has_column_privilege(
                    'tit_growth_app',
                    'public.teacher_qualifications',
                    allowed.column_name,
                    'UPDATE'
                )
            ) OR has_column_privilege(
                'tit_growth_app',
                'public.teacher_qualifications',
                'teacher_id',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app qualification UPDATE columns are invalid';
            END IF;

            IF has_table_privilege(
                'tit_growth_app', 'public.teacher_source_wide', 'INSERT'
            ) OR has_table_privilege(
                'tit_growth_app', 'public.teacher_source_wide', 'UPDATE'
            ) OR has_table_privilege(
                'tit_growth_app', 'public.teacher_source_wide', 'DELETE'
            ) OR has_table_privilege(
                'tit_growth_app', 'public.lesson_source_wide', 'INSERT'
            ) OR has_table_privilege(
                'tit_growth_app', 'public.lesson_source_wide', 'UPDATE'
            ) OR has_table_privilege(
                'tit_growth_app', 'public.lesson_source_wide', 'DELETE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app source tables must remain read-only';
            END IF;
        END
        $runtime_source_result_acl_assertions$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    lesson_columns = _columns(LESSON_RESULT_UPDATE_COLUMNS)
    qualification_columns = _columns(QUALIFICATION_UPDATE_COLUMNS)
    op.execute(
        f"""
        REVOKE UPDATE ({lesson_columns})
        ON TABLE public.lesson_score_results
        FROM tit_growth_app;
        REVOKE UPDATE ({qualification_columns})
        ON TABLE public.teacher_qualifications
        FROM tit_growth_app;
        REVOKE SELECT, INSERT ON TABLE
            public.lesson_score_results,
            public.teacher_qualifications
        FROM tit_growth_app;
        """
    )
