"""retire the cancelled false-early-leave source fact.

Revision ID: 20260822_73_retire_false_early
Revises: 20260822_72_teacher_online_lock
Create Date: 2026-08-22

The business cancelled this fact completely.  The migration first rewrites
the only current dependent view without the JSON key, verifies that the
column has no remaining view dependency, and then drops the physical column
without ``CASCADE``.

The removed values cannot be reconstructed.  Downgrade is therefore allowed
only while ``lesson_source_wide`` is empty; it restores the nullable column
and the revision-72 view shape solely for a verified code rollback.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_73_retire_false_early"
down_revision: Union[str, None] = "20260822_72_teacher_online_lock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RETIRED_COLUMN = "假早退"
DEPENDENT_VIEW = "public.teacher_lesson_score_current"


def _rewrite_lesson_view_without_retired_fact() -> None:
    op.execute(
        r"""
        DO $retire_false_early_view$
        DECLARE
            dependent_views text[];
            original_definition text;
            migrated_definition text;
        BEGIN
            SELECT array_agg(dependent_view ORDER BY dependent_view)
            INTO dependent_views
            FROM (
                SELECT DISTINCT
                    quote_ident(view_namespace.nspname)
                    || '.' || quote_ident(dependent_view.relname)
                        AS dependent_view
                FROM pg_catalog.pg_attribute AS source_column
                JOIN pg_catalog.pg_depend AS dependency
                  ON dependency.refobjid = source_column.attrelid
                 AND dependency.refobjsubid = source_column.attnum
                JOIN pg_catalog.pg_rewrite AS rewrite
                  ON rewrite.oid = dependency.objid
                JOIN pg_catalog.pg_class AS dependent_view
                  ON dependent_view.oid = rewrite.ev_class
                JOIN pg_catalog.pg_namespace AS view_namespace
                  ON view_namespace.oid = dependent_view.relnamespace
                WHERE source_column.attrelid =
                        'public.lesson_source_wide'::regclass
                  AND source_column.attname = '假早退'
                  AND NOT source_column.attisdropped
                  AND dependent_view.relkind IN ('v', 'm')
            ) AS dependencies;

            IF dependent_views IS DISTINCT FROM
                ARRAY['public.teacher_lesson_score_current']::text[] THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_DEPENDENT_VIEWS_UNEXPECTED:%',
                    dependent_views;
            END IF;

            SELECT pg_catalog.pg_get_viewdef(
                'public.teacher_lesson_score_current'::regclass,
                true
            )
            INTO original_definition;

            IF original_definition NOT LIKE '%is_false_early_leave%'
               OR original_definition NOT LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_VIEW_SOURCE_SHAPE_UNEXPECTED';
            END IF;

            migrated_definition := pg_catalog.regexp_replace(
                original_definition,
                E',[[:space:]]*''is_false_early_leave'',[[:space:]]*source\\.\"假早退\"',
                '',
                'g'
            );

            IF migrated_definition = original_definition
               OR migrated_definition LIKE '%is_false_early_leave%'
               OR migrated_definition LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_VIEW_REWRITE_INCOMPLETE';
            END IF;

            EXECUTE
                'CREATE OR REPLACE VIEW '
                || 'public.teacher_lesson_score_current AS '
                || migrated_definition;
        END
        $retire_false_early_view$;
        """
    )


def _assert_no_retired_column_dependency() -> None:
    op.execute(
        """
        DO $retire_false_early_dependency_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS source_column
                JOIN pg_catalog.pg_depend AS dependency
                  ON dependency.refobjid = source_column.attrelid
                 AND dependency.refobjsubid = source_column.attnum
                JOIN pg_catalog.pg_rewrite AS rewrite
                  ON rewrite.oid = dependency.objid
                JOIN pg_catalog.pg_class AS dependent_view
                  ON dependent_view.oid = rewrite.ev_class
                WHERE source_column.attrelid =
                        'public.lesson_source_wide'::regclass
                  AND source_column.attname = '假早退'
                  AND NOT source_column.attisdropped
                  AND dependent_view.relkind IN ('v', 'm')
            ) THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_VIEW_DEPENDENCY_REMAINS';
            END IF;
        END
        $retire_false_early_dependency_guard$;
        """
    )


def _assert_upgrade_shape() -> None:
    op.execute(
        """
        DO $retire_false_early_upgrade_shape$
        DECLARE
            lesson_column_count integer;
            lesson_view_definition text;
        BEGIN
            SELECT count(*)
            INTO lesson_column_count
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'lesson_source_wide';

            SELECT pg_catalog.pg_get_viewdef(
                'public.teacher_lesson_score_current'::regclass,
                true
            )
            INTO lesson_view_definition;

            IF lesson_column_count <> 23
               OR EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
               )
               OR lesson_view_definition LIKE '%is_false_early_leave%'
               OR lesson_view_definition LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_UPGRADE_SHAPE_INVALID';
            END IF;
        END
        $retire_false_early_upgrade_shape$;
        """
    )


def _guard_downgrade() -> None:
    op.execute(
        """
        LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE;

        DO $retire_false_early_downgrade_guard$
        DECLARE
            lesson_column_count integer;
            lesson_view_definition text;
        BEGIN
            SELECT count(*)
            INTO lesson_column_count
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'lesson_source_wide';

            SELECT pg_catalog.pg_get_viewdef(
                'public.teacher_lesson_score_current'::regclass,
                true
            )
            INTO lesson_view_definition;

            IF lesson_column_count <> 23
               OR EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
               )
               OR lesson_view_definition LIKE '%is_false_early_leave%'
               OR lesson_view_definition LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_DOWNGRADE_SOURCE_SHAPE_INVALID';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'refusing false-early-leave downgrade: retired values cannot be restored for existing lessons';
            END IF;
        END
        $retire_false_early_downgrade_guard$;
        """
    )


def _restore_lesson_view_with_retired_fact() -> None:
    op.execute(
        r"""
        DO $restore_false_early_view$
        DECLARE
            original_definition text;
            restored_definition text;
        BEGIN
            SELECT pg_catalog.pg_get_viewdef(
                'public.teacher_lesson_score_current'::regclass,
                true
            )
            INTO original_definition;

            IF original_definition LIKE '%is_false_early_leave%'
               OR original_definition LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_DOWNGRADE_VIEW_ALREADY_RESTORED';
            END IF;

            restored_definition := pg_catalog.regexp_replace(
                original_definition,
                E'''is_early'',[[:space:]]*source\\.\"早退\",[[:space:]]*''absence_reason_detail''',
                E'''is_early'', source.\"早退\", ''is_false_early_leave'', source.\"假早退\", ''absence_reason_detail''',
                'g'
            );

            IF restored_definition = original_definition
               OR restored_definition NOT LIKE '%is_false_early_leave%'
               OR restored_definition NOT LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_DOWNGRADE_VIEW_REWRITE_INCOMPLETE';
            END IF;

            EXECUTE
                'CREATE OR REPLACE VIEW '
                || 'public.teacher_lesson_score_current AS '
                || restored_definition;
        END
        $restore_false_early_view$;
        """
    )


def _assert_downgrade_shape() -> None:
    op.execute(
        """
        DO $restore_false_early_shape$
        DECLARE
            lesson_column_count integer;
            lesson_view_definition text;
        BEGIN
            SELECT count(*)
            INTO lesson_column_count
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'lesson_source_wide';

            SELECT pg_catalog.pg_get_viewdef(
                'public.teacher_lesson_score_current'::regclass,
                true
            )
            INTO lesson_view_definition;

            IF lesson_column_count <> 24
               OR NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
                      AND data_type = 'boolean'
                      AND is_nullable = 'YES'
               )
               OR lesson_view_definition NOT LIKE '%is_false_early_leave%'
               OR lesson_view_definition NOT LIKE '%假早退%' THEN
                RAISE EXCEPTION
                    'FALSE_EARLY_LEAVE_DOWNGRADE_SHAPE_INVALID';
            END IF;
        END
        $restore_false_early_shape$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        "LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE"
    )
    _rewrite_lesson_view_without_retired_fact()
    _assert_no_retired_column_dependency()
    op.drop_column(
        "lesson_source_wide",
        RETIRED_COLUMN,
        schema="public",
    )
    _assert_upgrade_shape()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_downgrade()
    op.add_column(
        "lesson_source_wide",
        sa.Column(RETIRED_COLUMN, sa.Boolean(), nullable=True),
        schema="public",
    )
    _restore_lesson_view_with_retired_fact()
    _assert_downgrade_shape()
