"""backfill source-wide teacher state and contract graduation score locking.

Revision ID: 20260822_72_teacher_online_lock
Revises: 20260822_71_dom_teacher_area
Create Date: 2026-08-22

Only teachers materialized from ``SOURCE_WIDE_CURRENT`` participate in the
online-state backfill.  The business date is evaluated explicitly in
Asia/Shanghai so the result does not depend on the database session timezone.
The same source snapshot is recorded in the teacher payload so the daily
``NEW -> EXISTING`` refresh can advance pre-existing teachers without waiting
for another source event.

Downgrading this revision removes only the added index and reverse lock
constraint.  It deliberately retains every backfilled ``online_status`` and
``graduation_score_locked`` fact; the revision-70 downgrade guard will refuse
to drop their owning columns while any such fact remains.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_72_teacher_online_lock"
down_revision: Union[str, None] = "20260822_71_dom_teacher_area"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ONLINE_REFRESH_INDEX = "ix_teachers_source_snapshot_online_status_teacher"
GRADUATION_REQUIRES_LOCK_CONSTRAINT = (
    "ck_teacher_qualification_graduation_requires_score_locked_v2"
)


def _lock_projection_tables() -> None:
    # Block concurrent source/qualification writers for the whole backfill and
    # constraint-installation transaction.  Without this lock an old writer
    # could insert a qualified row with a NULL lock between UPDATE and CHECK.
    op.execute(
        """
        LOCK TABLE
            public.teachers,
            public.teacher_source_wide,
            public.teacher_qualifications
        IN SHARE ROW EXCLUSIVE MODE
        """
    )


def _backfill_source_wide_online_status() -> None:
    op.execute(
        """
        WITH business_clock AS (
            SELECT
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date
                    AS business_date
        ),
        classified AS (
            SELECT
                teacher.teacher_id,
                teacher.payload,
                source.status AS source_status,
                source.status_on_date,
                clock.business_date,
                CASE
                    WHEN lower(btrim(source.status)) = 'off' THEN 'LEFT'
                    WHEN lower(btrim(source.status)) = 'hei' THEN 'BLOCKED'
                    WHEN lower(btrim(source.status)) = 'on'
                         AND source.status_on_date IS NOT NULL
                         AND source.status_on_date <= clock.business_date
                         AND clock.business_date - source.status_on_date
                             BETWEEN 0 AND 29
                        THEN 'NEW'
                    WHEN lower(btrim(source.status)) = 'on'
                         AND source.status_on_date IS NOT NULL
                         AND source.status_on_date <= clock.business_date
                         AND clock.business_date - source.status_on_date >= 30
                        THEN 'EXISTING'
                    ELSE NULL
                END AS online_status
            FROM public.teachers AS teacher
            CROSS JOIN business_clock AS clock
            LEFT JOIN public.teacher_source_wide AS source
              ON source.tchr_id = teacher.teacher_id
            WHERE teacher.source_snapshot_label = 'SOURCE_WIDE_CURRENT'
        ),
        projected AS (
            SELECT
                classified.teacher_id,
                classified.online_status,
                jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                COALESCE(
                                    classified.payload,
                                    '{}'::jsonb
                                ),
                                '{employment_status}',
                                COALESCE(
                                    to_jsonb(classified.source_status),
                                    'null'::jsonb
                                ),
                                true
                            ),
                            '{online_status_onboard_date}',
                            COALESCE(
                                to_jsonb(
                                    classified.status_on_date::text
                                ),
                                'null'::jsonb
                            ),
                            true
                        ),
                        '{online_status_evidence_status}',
                        to_jsonb(
                            CASE
                                WHEN classified.online_status IS NULL
                                    THEN 'SOURCE_MISSING'::text
                                ELSE 'CONFIRMED'::text
                            END
                        ),
                        true
                    ),
                    '{online_status_business_date}',
                    to_jsonb(classified.business_date::text),
                    true
                ) AS payload
            FROM classified
        )
        UPDATE public.teachers AS teacher
        SET online_status = projected.online_status,
            payload = projected.payload
        FROM projected
        WHERE teacher.teacher_id = projected.teacher_id
          AND (
              teacher.online_status IS DISTINCT FROM projected.online_status
              OR teacher.payload IS DISTINCT FROM projected.payload
          )
        """
    )


def _complete_graduation_locks() -> None:
    # Revision 70 already rejects locks on unqualified rows and values other
    # than 100.  This backfill closes the inverse historical gap before the
    # new qualified=>locked constraint is installed.
    op.execute(
        """
        UPDATE public.teacher_qualifications
        SET graduation_score_locked = 100.0,
            revision = revision + 1
        WHERE graduation_qualified IS TRUE
          AND graduation_score_locked IS NULL
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _lock_projection_tables()
    _backfill_source_wide_online_status()
    _complete_graduation_locks()
    op.create_index(
        ONLINE_REFRESH_INDEX,
        "teachers",
        ["source_snapshot_label", "online_status", "teacher_id"],
        unique=False,
        schema="public",
    )
    op.create_check_constraint(
        GRADUATION_REQUIRES_LOCK_CONSTRAINT,
        "teacher_qualifications",
        "graduation_qualified IS FALSE "
        "OR (graduation_score_locked IS NOT NULL "
        "AND graduation_score_locked = 100.0)",
        schema="public",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # No fact column is cleared or dropped here.  Keeping the data makes a
    # revision rollback reversible, and revision 70 owns the final destructive
    # guard if a caller subsequently attempts to remove the v2 columns.
    op.drop_constraint(
        GRADUATION_REQUIRES_LOCK_CONSTRAINT,
        "teacher_qualifications",
        schema="public",
        type_="check",
    )
    op.drop_index(
        ONLINE_REFRESH_INDEX,
        table_name="teachers",
        schema="public",
    )
