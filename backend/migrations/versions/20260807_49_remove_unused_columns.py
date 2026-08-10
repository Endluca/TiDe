"""remove unused complaint and operator session columns

Revision ID: 20260807_49_unused_columns
Revises: 20260807_48_schema_cleanup
Create Date: 2026-08-07

The complaint workbook remains the lossless source for learning-resource
columns.  Before removing their typed copies, this revision proves that every
rule maps to exactly one raw workbook row and that the normalized values agree.
``operator_sessions.last_seen_at`` was never updated after login, so it is
removed only when every persisted value is identical to ``created_at``.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_49_unused_columns"
down_revision: str | None = "20260807_48_schema_cleanup"
branch_labels: str | None = None
depends_on: str | None = None


TITLE_KEY = "Course Title in the Learning Hub"
URL_KEY = "link"


def _guard_relation_shape_and_lock() -> None:
    """Require the exact source/target shape and close the check/drop race."""

    op.execute(
        """
        DO $unused_column_presence_guard$
        DECLARE
            missing_columns text;
        BEGIN
            SELECT string_agg(
                expected.table_name || '.' || expected.column_name,
                ', ' ORDER BY expected.table_name, expected.column_name
            )
            INTO missing_columns
            FROM (VALUES
                ('complaint_category_rules', 'learning_title'),
                ('complaint_category_rules', 'learning_url'),
                ('complaint_category_rules', 'source_sha256'),
                ('complaint_category_rules', 'source_row_number'),
                ('complaint_rule_imports', 'source_sha256'),
                ('complaint_rule_imports', 'raw_rows'),
                ('operator_sessions', 'created_at'),
                ('operator_sessions', 'last_seen_at')
            ) AS expected(table_name, column_name)
            WHERE NOT EXISTS (
                SELECT 1
                FROM information_schema.columns AS existing
                WHERE existing.table_schema = 'public'
                  AND existing.table_name = expected.table_name
                  AND existing.column_name = expected.column_name
            );

            IF missing_columns IS NOT NULL THEN
                RAISE EXCEPTION
                    'unused-column cleanup expected missing columns: %',
                    missing_columns;
            END IF;
        END
        $unused_column_presence_guard$;

        LOCK TABLE
            public.complaint_rule_imports,
            public.complaint_category_rules,
            public.operator_sessions
        IN ACCESS EXCLUSIVE MODE;
        """
    )


def _normalized_raw_text(key: str) -> str:
    """PostgreSQL equivalent of the importer's NFKC/whitespace normalization."""

    # Both keys are migration constants, never runtime input.  ``->>`` keeps
    # JSON null as SQL NULL.  Python spells booleans with an initial capital,
    # so handle that scalar explicitly before applying the same normalization.
    return (
        "NULLIF(btrim(regexp_replace("
        "normalize(CASE "
        f"WHEN jsonb_typeof(source.raw_row -> '{key}') = 'boolean' "
        f"THEN CASE WHEN source.raw_row -> '{key}' = 'true'::jsonb "
        "THEN 'True' ELSE 'False' END "
        f"ELSE source.raw_row ->> '{key}' END, NFKC), "
        "E'\\\\s+', ' ', 'g')), '')"
    )


def _raw_recovery_guard(*, compare_typed_columns: bool) -> None:
    """Refuse missing, duplicate, malformed, or inconsistent raw evidence."""

    title_value = _normalized_raw_text(TITLE_KEY)
    url_value = _normalized_raw_text(URL_KEY)
    typed_comparison = ""
    if compare_typed_columns:
        typed_comparison = f"""
                  OR rule.learning_title IS DISTINCT FROM {title_value}
                  OR rule.learning_url IS DISTINCT FROM {url_value}
        """

    op.execute(
        f"""
        DO $unused_column_raw_recovery_guard$
        DECLARE
            malformed_import text;
            invalid_rule text;
        BEGIN
            SELECT imported.source_sha256
            INTO malformed_import
            FROM public.complaint_rule_imports AS imported
            WHERE jsonb_typeof(imported.raw_rows) IS DISTINCT FROM 'array'
            LIMIT 1;

            IF malformed_import IS NOT NULL THEN
                RAISE EXCEPTION
                    'complaint raw_rows is not an array for source %',
                    malformed_import;
            END IF;

            SELECT rule.rule_id
            INTO invalid_rule
            FROM public.complaint_category_rules AS rule
            LEFT JOIN public.complaint_rule_imports AS imported
              ON imported.source_sha256 = rule.source_sha256
            LEFT JOIN LATERAL (
                SELECT
                    count(*) AS match_count,
                    jsonb_agg(candidate.raw_row) -> 0 AS raw_row
                FROM jsonb_array_elements(imported.raw_rows)
                    AS candidate(raw_row)
                WHERE candidate.raw_row @> jsonb_build_object(
                    'source_row_number', rule.source_row_number
                )
            ) AS source ON true
            WHERE source.match_count <> 1
                OR jsonb_typeof(source.raw_row) IS DISTINCT FROM 'object'
                OR NOT source.raw_row ? '{TITLE_KEY}'
                OR NOT source.raw_row ? '{URL_KEY}'
                OR jsonb_typeof(source.raw_row -> '{TITLE_KEY}') NOT IN (
                    'string', 'number', 'boolean', 'null'
                )
                OR jsonb_typeof(source.raw_row -> '{URL_KEY}') NOT IN (
                    'string', 'number', 'boolean', 'null'
                )
                OR char_length({title_value}) > 500
                {typed_comparison}
            LIMIT 1;

            IF invalid_rule IS NOT NULL THEN
                RAISE EXCEPTION
                    'complaint rule cannot be losslessly restored from raw_rows: %',
                    invalid_rule;
            END IF;
        END
        $unused_column_raw_recovery_guard$;
        """
    )


def _guard_upgrade() -> None:
    _guard_relation_shape_and_lock()
    _raw_recovery_guard(compare_typed_columns=True)
    op.execute(
        """
        DO $unused_column_operator_guard$
        DECLARE
            invalid_session text;
        BEGIN
            SELECT session_id
            INTO invalid_session
            FROM public.operator_sessions
            WHERE last_seen_at IS DISTINCT FROM created_at
            LIMIT 1;

            IF invalid_session IS NOT NULL THEN
                RAISE EXCEPTION
                    'operator session last_seen_at is not redundant: %',
                    invalid_session;
            END IF;
        END
        $unused_column_operator_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_upgrade()
    op.drop_column(
        "complaint_category_rules",
        "learning_title",
        schema="public",
    )
    op.drop_column(
        "complaint_category_rules",
        "learning_url",
        schema="public",
    )
    op.drop_column("operator_sessions", "last_seen_at", schema="public")


def _guard_downgrade() -> None:
    op.execute(
        """
        LOCK TABLE
            public.complaint_rule_imports,
            public.complaint_category_rules,
            public.operator_sessions
        IN ACCESS EXCLUSIVE MODE;
        """
    )
    _raw_recovery_guard(compare_typed_columns=False)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_downgrade()
    op.add_column(
        "complaint_category_rules",
        sa.Column("learning_title", sa.String(length=500), nullable=True),
        schema="public",
    )
    op.add_column(
        "complaint_category_rules",
        sa.Column("learning_url", sa.Text(), nullable=True),
        schema="public",
    )
    op.execute(
        f"""
        UPDATE public.complaint_category_rules AS rule
        SET
            learning_title = {_normalized_raw_text(TITLE_KEY)},
            learning_url = {_normalized_raw_text(URL_KEY)}
        FROM public.complaint_rule_imports AS imported
        CROSS JOIN LATERAL jsonb_array_elements(imported.raw_rows)
            AS source(raw_row)
        WHERE imported.source_sha256 = rule.source_sha256
          AND source.raw_row @> jsonb_build_object(
                'source_row_number', rule.source_row_number
          )
        """
    )

    op.add_column(
        "operator_sessions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.operator_sessions
        SET last_seen_at = created_at
        """
    )
    op.alter_column(
        "operator_sessions",
        "last_seen_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema="public",
    )
