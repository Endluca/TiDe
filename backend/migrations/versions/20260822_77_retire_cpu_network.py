"""retire the unapproved lesson CPU and network sources.

Revision ID: 20260822_77_retire_cpu_network
Revises: 20260822_76_lesson_score_components
Create Date: 2026-08-22

The two columns remain as nullable placeholders in the 22-column lesson
contract.  Existing values are cleared through the normal source-wide outbox
trigger so the worker can reconcile legacy hardware awards.  A temporary
database check keeps both placeholders NULL until a future versioned source
migration names and validates their replacement sources.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_77_retire_cpu_network"
down_revision: Union[str, None] = "20260822_76_lesson_score_components"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "ck_lesson_source_cpu_network_retired_v1"
OUTBOX_TRIGGER = "trg_lesson_source_wide_outbox_v1"
CPU_COLUMN = "cpu占用过高"
NETWORK_COLUMN = "网络延迟过高"


def _lock_and_assert_upgrade_preconditions() -> None:
    op.execute(
        f"""
        DO $cpu_network_table_preflight$
        BEGIN
            IF to_regclass('public.lesson_source_wide') IS NULL THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_LESSON_SOURCE_MISSING';
            END IF;
            IF to_regclass('public.teacher_source_wide') IS NULL THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_TEACHER_SOURCE_MISSING';
            END IF;
            IF to_regclass('public.outbox_events') IS NULL THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_OUTBOX_MISSING';
            END IF;
        END
        $cpu_network_table_preflight$;

        LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.teacher_source_wide IN SHARE MODE;

        DO $cpu_network_contract_preflight$
        DECLARE
            expected_column text;
        BEGIN
            FOREACH expected_column IN ARRAY ARRAY[
                '{CPU_COLUMN}', '{NETWORK_COLUMN}'
            ] LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid =
                            'public.lesson_source_wide'::regclass
                      AND attribute.attname = expected_column
                      AND NOT attribute.attisdropped
                      AND attribute.atttypid = 'pg_catalog.bool'::regtype
                      AND NOT attribute.attnotnull
                ) THEN
                    RAISE EXCEPTION
                        'CPU_NETWORK_RETIREMENT_COLUMN_INVALID:%',
                        expected_column;
                END IF;
            END LOOP;

            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_constraint
                WHERE conrelid = 'public.lesson_source_wide'::regclass
                  AND conname = '{CONSTRAINT_NAME}'
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_CONSTRAINT_ALREADY_PRESENT';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'public.lesson_source_wide'::regclass
                  AND tgname = '{OUTBOX_TRIGGER}'
                  AND NOT tgisinternal
                  AND tgenabled <> 'D'
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_OUTBOX_TRIGGER_MISSING';
            END IF;
        END
        $cpu_network_contract_preflight$;
        """
    )


def _clear_retired_values() -> None:
    # Do not disable the established row trigger: each changed course must
    # emit source_wide.changed.v1 so existing derived hardware points can be
    # reconciled by the worker after this separately executed migration.
    op.execute(
        f"""
        UPDATE public.lesson_source_wide
        SET "{CPU_COLUMN}" = NULL,
            "{NETWORK_COLUMN}" = NULL
        WHERE "{CPU_COLUMN}" IS NOT NULL
           OR "{NETWORK_COLUMN}" IS NOT NULL;
        """
    )


def _enqueue_teacher_reconciliation() -> None:
    # The active v1 projection also needs to reverse already-materialized
    # favorite and hardware awards.  One deterministic teacher-wide event is
    # enough to rebuild every lesson/component for that teacher.  The exact
    # payload is deliberately limited to the established routing envelope.
    op.execute(
        """
        INSERT INTO public.outbox_events (
            outbox_id,
            event_id,
            aggregate_type,
            aggregate_id,
            event_type,
            payload,
            status,
            available_at,
            attempt_count,
            last_error,
            created_at,
            published_at
        )
        SELECT
            'OUT-SW-RECON-77-' || md5(source.tchr_id),
            'EVT-SW-RECON-77-' || md5(source.tchr_id),
            'TEACHER_SOURCE_WIDE',
            source.tchr_id,
            'source_wide.changed.v1',
            jsonb_build_object(
                'source_table', 'teacher_source_wide',
                'source_id', source.tchr_id,
                'operation', 'UPDATE',
                'changed_fields', jsonb_build_array('feedback_favorite_cnt'),
                'old_teacher_id', source.tchr_id,
                'new_teacher_id', source.tchr_id
            ),
            'PENDING',
            statement_timestamp(),
            0,
            NULL,
            statement_timestamp(),
            NULL
        FROM public.teacher_source_wide AS source
        ORDER BY source.tchr_id
        ON CONFLICT DO NOTHING;
        """
    )


def _assert_installed_contract() -> None:
    op.execute(
        f"""
        DO $cpu_network_installed_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide
                WHERE "{CPU_COLUMN}" IS NOT NULL
                   OR "{NETWORK_COLUMN}" IS NOT NULL
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_NON_NULL_VALUE_REMAINS';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_constraint
                WHERE conrelid = 'public.lesson_source_wide'::regclass
                  AND conname = '{CONSTRAINT_NAME}'
                  AND contype = 'c'
                  AND convalidated
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_CONSTRAINT_MISSING';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'public.lesson_source_wide'::regclass
                  AND tgname = '{OUTBOX_TRIGGER}'
                  AND NOT tgisinternal
                  AND tgenabled <> 'D'
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_OUTBOX_TRIGGER_DISABLED';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.teacher_source_wide AS source
                LEFT JOIN public.outbox_events AS event
                  ON event.outbox_id =
                        'OUT-SW-RECON-77-' || md5(source.tchr_id)
                WHERE event.outbox_id IS NULL
                   OR event.event_id IS DISTINCT FROM
                        'EVT-SW-RECON-77-' || md5(source.tchr_id)
                   OR event.aggregate_type IS DISTINCT FROM
                        'TEACHER_SOURCE_WIDE'
                   OR event.aggregate_id IS DISTINCT FROM source.tchr_id
                   OR event.event_type IS DISTINCT FROM
                        'source_wide.changed.v1'
                   OR event.status IS DISTINCT FROM 'PENDING'
                   OR event.attempt_count IS DISTINCT FROM 0
                   OR event.last_error IS NOT NULL
                   OR event.published_at IS NOT NULL
                   OR event.payload IS DISTINCT FROM jsonb_build_object(
                        'source_table', 'teacher_source_wide',
                        'source_id', source.tchr_id,
                        'operation', 'UPDATE',
                        'changed_fields',
                            jsonb_build_array('feedback_favorite_cnt'),
                        'old_teacher_id', source.tchr_id,
                        'new_teacher_id', source.tchr_id
                   )
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_RECONCILIATION_OUTBOX_CONFLICT';
            END IF;
        END
        $cpu_network_installed_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _lock_and_assert_upgrade_preconditions()
    _clear_retired_values()
    _enqueue_teacher_reconciliation()
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "lesson_source_wide",
        f'"{CPU_COLUMN}" IS NULL AND "{NETWORK_COLUMN}" IS NULL',
        schema="public",
    )
    _assert_installed_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Dropping the temporary lock is reversible; the retired values cleared
    # on upgrade are intentionally not fabricated or restored.
    op.execute(
        f"""
        LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE;

        DO $cpu_network_downgrade_guard$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_constraint
                WHERE conrelid = 'public.lesson_source_wide'::regclass
                  AND conname = '{CONSTRAINT_NAME}'
                  AND contype = 'c'
                  AND convalidated
            ) THEN
                RAISE EXCEPTION
                    'CPU_NETWORK_RETIREMENT_DOWNGRADE_CONSTRAINT_MISSING';
            END IF;
        END
        $cpu_network_downgrade_guard$;
        """
    )
    op.drop_constraint(
        CONSTRAINT_NAME,
        "lesson_source_wide",
        schema="public",
        type_="check",
    )
