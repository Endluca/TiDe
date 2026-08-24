"""contract teacher camp state to IN_CAMP and GRADUATED.

Revision ID: 20260822_75_camp_state_contract
Revises: 20260822_74_favorite_schema
Create Date: 2026-08-22

The business has only two camp states.  Legacy ``IN_PROGRESS`` and
``NOT_IN_CAMP`` rows are normalized to ``IN_CAMP`` while an earned
``GRADUATED`` state is retained.  The compatibility payload is updated in the
same locked transaction, and an unknown source value fails the migration
before any row is rewritten.

Downgrade removes only the two-value CHECK.  It deliberately does not map
``IN_CAMP`` back to an ambiguous legacy value and never changes an earned
qualification or its payload.  The pre-existing irreversible qualification
trigger remains installed in both directions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_75_camp_state_contract"
down_revision: Union[str, None] = "20260822_74_favorite_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CAMP_STATE_CONSTRAINT = "ck_teachers_graduation_state_v2"
IRREVERSIBLE_TRIGGER = "trg_guard_teacher_qualification_reversal"


def _lock_and_validate_legacy_state() -> None:
    op.execute(
        """
        LOCK TABLE public.teachers IN ACCESS EXCLUSIVE MODE;

        DO $camp_state_preflight$
        DECLARE
            unknown_states text[];
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_constraint
                WHERE conrelid = 'public.teachers'::regclass
                  AND conname = 'ck_teachers_graduation_state_v2'
            ) THEN
                RAISE EXCEPTION 'CAMP_STATE_CONSTRAINT_ALREADY_PRESENT';
            END IF;

            SELECT array_agg(DISTINCT graduation_state ORDER BY graduation_state)
            INTO unknown_states
            FROM public.teachers
            WHERE graduation_state NOT IN (
                'IN_PROGRESS', 'NOT_IN_CAMP', 'IN_CAMP', 'GRADUATED'
            );

            IF unknown_states IS NOT NULL THEN
                RAISE EXCEPTION 'CAMP_STATE_UNKNOWN:%', unknown_states;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.teachers
                WHERE payload IS NULL
                   OR jsonb_typeof(payload) IS DISTINCT FROM 'object'
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'CAMP_STATE_PAYLOAD_INVALID';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'public.teachers'::regclass
                  AND tgname = 'trg_guard_teacher_qualification_reversal'
                  AND NOT tgisinternal
                  AND tgenabled <> 'D'
            ) THEN
                RAISE EXCEPTION
                    'CAMP_STATE_IRREVERSIBLE_TRIGGER_MISSING';
            END IF;
        END
        $camp_state_preflight$;
        """
    )


def _normalize_camp_state() -> None:
    op.execute(
        """
        WITH normalized AS (
            SELECT
                teacher_id,
                CASE
                    WHEN graduation_state = 'GRADUATED' THEN 'GRADUATED'
                    ELSE 'IN_CAMP'
                END AS graduation_state
            FROM public.teachers
        )
        UPDATE public.teachers AS teacher
        SET graduation_state = normalized.graduation_state,
            payload = jsonb_set(
                teacher.payload,
                '{graduation_state}',
                to_jsonb(normalized.graduation_state),
                true
            )
        FROM normalized
        WHERE teacher.teacher_id = normalized.teacher_id
          AND (
              teacher.graduation_state
                  IS DISTINCT FROM normalized.graduation_state
              OR teacher.payload ->> 'graduation_state'
                  IS DISTINCT FROM normalized.graduation_state
          )
        """
    )


def _normalize_scorecard_view_state() -> None:
    """Keep the teacher-facing read contract on the same two camp states.

    Revision 44 derives this value from the qualification row and therefore
    does not automatically see the normalized ``teachers.graduation_state``.
    Replacing only the legacy literal preserves the view columns, dependencies,
    owner and grants installed by the source-read migration.
    """

    op.execute(
        """
        DO $camp_state_scorecard_view$
        DECLARE
            current_definition text;
            normalized_definition text;
            legacy_occurrences integer;
        BEGIN
            IF to_regclass('public.teacher_scorecard_current') IS NULL THEN
                RAISE EXCEPTION 'CAMP_STATE_SCORECARD_VIEW_MISSING';
            END IF;

            SELECT pg_get_viewdef(
                'public.teacher_scorecard_current'::regclass,
                true
            )
            INTO current_definition;

            legacy_occurrences := (
                length(current_definition)
                - length(replace(current_definition, 'IN_PROGRESS', ''))
            ) / length('IN_PROGRESS');

            IF legacy_occurrences = 0 THEN
                IF position('IN_CAMP' IN current_definition) = 0 THEN
                    RAISE EXCEPTION
                        'CAMP_STATE_SCORECARD_VIEW_CONTRACT_UNKNOWN';
                END IF;
                RETURN;
            END IF;
            IF legacy_occurrences <> 1 THEN
                RAISE EXCEPTION
                    'CAMP_STATE_SCORECARD_VIEW_LEGACY_AMBIGUOUS:%',
                    legacy_occurrences;
            END IF;

            normalized_definition := replace(
                current_definition,
                'IN_PROGRESS',
                'IN_CAMP'
            );
            EXECUTE
                'CREATE OR REPLACE VIEW public.teacher_scorecard_current AS '
                || normalized_definition;

            SELECT pg_get_viewdef(
                'public.teacher_scorecard_current'::regclass,
                true
            )
            INTO current_definition;
            IF position('IN_PROGRESS' IN current_definition) > 0
               OR position('IN_CAMP' IN current_definition) = 0 THEN
                RAISE EXCEPTION
                    'CAMP_STATE_SCORECARD_VIEW_BACKFILL_INCOMPLETE';
            END IF;
        END
        $camp_state_scorecard_view$;
        """
    )


def _assert_current_contract() -> None:
    op.execute(
        """
        DO $camp_state_contract_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.teachers
                WHERE graduation_state NOT IN ('IN_CAMP', 'GRADUATED')
                   OR payload ->> 'graduation_state'
                        IS DISTINCT FROM graduation_state
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'CAMP_STATE_CONTRACT_BACKFILL_INCOMPLETE';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'public.teachers'::regclass
                  AND tgname = 'trg_guard_teacher_qualification_reversal'
                  AND NOT tgisinternal
                  AND tgenabled <> 'D'
            ) THEN
                RAISE EXCEPTION
                    'CAMP_STATE_IRREVERSIBLE_TRIGGER_MISSING';
            END IF;
        END
        $camp_state_contract_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _lock_and_validate_legacy_state()
    _normalize_camp_state()
    _normalize_scorecard_view_state()
    op.create_check_constraint(
        CAMP_STATE_CONSTRAINT,
        "teachers",
        "graduation_state IN ('IN_CAMP', 'GRADUATED') "
        "AND jsonb_typeof(payload) = 'object' "
        "AND payload ? 'graduation_state' "
        "AND payload ->> 'graduation_state' = graduation_state",
        schema="public",
    )
    _assert_current_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE public.teachers IN ACCESS EXCLUSIVE MODE;

        DO $camp_state_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.teachers
                WHERE graduation_state NOT IN ('IN_CAMP', 'GRADUATED')
                   OR payload ->> 'graduation_state'
                        IS DISTINCT FROM graduation_state
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'CAMP_STATE_DOWNGRADE_SOURCE_INVALID';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'public.teachers'::regclass
                  AND tgname = 'trg_guard_teacher_qualification_reversal'
                  AND NOT tgisinternal
                  AND tgenabled <> 'D'
            ) THEN
                RAISE EXCEPTION
                    'CAMP_STATE_IRREVERSIBLE_TRIGGER_MISSING';
            END IF;
        END
        $camp_state_downgrade_guard$;
        """
    )
    op.drop_constraint(
        CAMP_STATE_CONSTRAINT,
        "teachers",
        schema="public",
        type_="check",
    )
