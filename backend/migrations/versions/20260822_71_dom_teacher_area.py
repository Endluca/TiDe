"""normalize the domestic teacher-area compatibility value.

Revision ID: 20260822_71_dom_teacher_area
Revises: 20260822_70_qualification_schema
Create Date: 2026-08-22

The migration must be applied before deploying the matching direct/queued
projectors.  Those projectors write and compare the confirmed ``dom`` value;
the direct writer fails closed if it still observes a legacy ``dmo`` row so a
deployment-order mistake cannot silently delete a lesson as a region mismatch.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_71_dom_teacher_area"
down_revision: Union[str, None] = "20260822_70_qualification_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE public.teacher_source_wide IN SHARE ROW EXCLUSIVE MODE;

        UPDATE public.teacher_source_wide
        SET teach_area_type = 'dom'
        WHERE teach_area_type = 'dmo';

        DO $dom_teacher_area_upgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.teacher_source_wide
                WHERE teach_area_type = 'dmo'
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'DOM_TEACHER_AREA_UPGRADE_INCOMPLETE';
            END IF;
        END
        $dom_teacher_area_upgrade_guard$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # A code rollback expects the legacy value for every domestic row,
    # including rows first created after upgrade.  Translating all ``dom``
    # rows is therefore the reversible semantic inverse; OVS/NULL are intact.
    op.execute(
        """
        LOCK TABLE public.teacher_source_wide IN SHARE ROW EXCLUSIVE MODE;

        UPDATE public.teacher_source_wide
        SET teach_area_type = 'dmo'
        WHERE teach_area_type = 'dom';

        DO $dom_teacher_area_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.teacher_source_wide
                WHERE teach_area_type = 'dom'
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'DOM_TEACHER_AREA_DOWNGRADE_INCOMPLETE';
            END IF;
        END
        $dom_teacher_area_downgrade_guard$;
        """
    )
