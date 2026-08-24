"""add the confirmed v2 teacher and qualification state schema.

Revision ID: 20260822_70_qualification_schema
Revises: 20260822_69_dts_v2_source_guard
Create Date: 2026-08-22

This is an expand-only schema slice.  It does not project ``online_status``,
rename the legacy ``IN_PROGRESS`` camp value, or activate the v2 qualification
writer.  Existing earned timestamps are kept as they are: a missing historical
timestamp remains unknown instead of being fabricated by the migration.

The final ``IN_CAMP/GRADUATED`` and "every newly earned graduation has a locked
score" constraints belong to the later writer-cutover contraction.  Enforcing
them here would break the still-active compatibility writer immediately after
this migration, which is not a valid expand/contract sequence.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_70_qualification_schema"
down_revision: Union[str, None] = "20260822_69_dts_v2_source_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EXPAND_ONLY = True
GRADUATION_SCORE_LOCK_VALUE = 100.0


def _expand_columns() -> None:
    op.add_column(
        "teachers",
        sa.Column("online_status", sa.String(length=32), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_qualifications",
        sa.Column(
            "graduation_score_locked",
            sa.Float(),
            nullable=True,
        ),
        schema="public",
    )


def _backfill_existing_locked_scores() -> None:
    op.execute(
        """
        UPDATE public.teacher_qualifications
        SET graduation_score_locked = 100.0,
            revision = revision + 1
        WHERE graduation_qualified IS TRUE
          AND graduation_score_locked IS NULL
        """
    )


def _install_constraints_and_guard() -> None:
    op.create_check_constraint(
        "ck_teachers_online_status_v2",
        "teachers",
        "online_status IS NULL OR online_status IN "
        "('NEW', 'EXISTING', 'LEFT', 'BLOCKED')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_teacher_qualification_graduation_score_locked_v2",
        "teacher_qualifications",
        "graduation_score_locked IS NULL OR "
        "(graduation_qualified IS TRUE AND graduation_score_locked = 100.0)",
        schema="public",
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_graduation_score_locked_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF OLD.graduation_score_locked IS NOT NULL
               AND NEW.graduation_score_locked
                   IS DISTINCT FROM OLD.graduation_score_locked THEN
                RAISE EXCEPTION 'GRADUATION_SCORE_LOCKED_IMMUTABLE';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER trg_guard_graduation_score_locked_v2
        BEFORE UPDATE OF graduation_score_locked
        ON public.teacher_qualifications
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_graduation_score_locked_v2();

        REVOKE ALL ON FUNCTION public.guard_graduation_score_locked_v2()
        FROM PUBLIC;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _expand_columns()
    _backfill_existing_locked_scores()
    _install_constraints_and_guard()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE public.teachers, public.teacher_qualifications
        IN ACCESS EXCLUSIVE MODE;

        DO $qualification_schema_downgrade_guard$
        BEGIN
            IF EXISTS (
                    SELECT 1
                    FROM public.teachers
                    WHERE online_status IS NOT NULL
                    LIMIT 1
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.teacher_qualifications
                    WHERE graduation_score_locked IS NOT NULL
                    LIMIT 1
               ) THEN
                RAISE EXCEPTION
                    'refusing qualification schema downgrade: v2 qualification facts exist';
            END IF;
        END
        $qualification_schema_downgrade_guard$;
        """
    )
    op.execute(
        """
        DROP TRIGGER trg_guard_graduation_score_locked_v2
        ON public.teacher_qualifications;
        DROP FUNCTION public.guard_graduation_score_locked_v2();
        """
    )
    op.drop_constraint(
        "ck_teacher_qualification_graduation_score_locked_v2",
        "teacher_qualifications",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_teachers_online_status_v2",
        "teachers",
        schema="public",
        type_="check",
    )
    op.drop_column(
        "teacher_qualifications",
        "graduation_score_locked",
        schema="public",
    )
    op.drop_column("teachers", "online_status", schema="public")
