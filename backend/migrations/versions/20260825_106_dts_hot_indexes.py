"""reduce DTS ledger and dirty-queue index write amplification.

Revision ID: 20260825_106_dts_hot_indexes
Revises: 20260825_105_pipeline_read_acl
Create Date: 2026-08-25

Production statistics showed that the 950 MB ledger B-tree had only 192
scans after more than 34 million inserts, while ``processed_at`` is physically
ordered.  A BRIN keeps the operational time-range access path without paying
one large B-tree mutation per ledger receipt.

The dirty claim function has one predicate and one ordering for both PENDING
and RETRY.  Replace the full ready index plus two state-specific indexes with
one partial index that matches that exact predicate and ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_106_dts_hot_indexes"
down_revision: Union[str, None] = "20260825_105_pipeline_read_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $dts_hot_index_prerequisite$
        BEGIN
          IF to_regclass(
               'public.ix_dts_ingest_events_source_table_processed'
             ) IS NULL
             OR to_regclass('public.ix_dts_dirty_keys_ready_v2') IS NULL
             OR to_regclass(
                  'public.ix_dts_dirty_keys_pending_fifo_v2'
                ) IS NULL
             OR to_regclass(
                  'public.ix_dts_dirty_keys_retry_due_v2'
                ) IS NULL THEN
            RAISE EXCEPTION 'DTS_HOT_INDEX_PREREQUISITE_MISSING';
          END IF;
        END
        $dts_hot_index_prerequisite$;

        LOCK TABLE public.dts_ingest_events,public.dts_dirty_keys
        IN SHARE ROW EXCLUSIVE MODE;

        CREATE INDEX ix_dts_ingest_events_processed_at_brin
        ON public.dts_ingest_events USING brin (processed_at)
        WITH (pages_per_range=64);

        CREATE INDEX ix_dts_dirty_keys_ready_v3
        ON public.dts_dirty_keys (
          next_attempt_at,updated_at,source_region,key_type,
          key_part_1,key_part_2
        )
        WHERE status IN ('PENDING','RETRY');

        DROP INDEX public.ix_dts_ingest_events_source_table_processed;
        DROP INDEX public.ix_dts_dirty_keys_pending_fifo_v2;
        DROP INDEX public.ix_dts_dirty_keys_retry_due_v2;
        DROP INDEX public.ix_dts_dirty_keys_ready_v2;

        DO $dts_hot_index_postflight$
        BEGIN
          IF to_regclass(
               'public.ix_dts_ingest_events_source_table_processed'
             ) IS NOT NULL
             OR to_regclass('public.ix_dts_dirty_keys_ready_v2') IS NOT NULL
             OR to_regclass(
                  'public.ix_dts_dirty_keys_pending_fifo_v2'
                ) IS NOT NULL
             OR to_regclass(
                  'public.ix_dts_dirty_keys_retry_due_v2'
                ) IS NOT NULL
             OR NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_index AS index_state
                  JOIN pg_catalog.pg_class AS index_class
                    ON index_class.oid=index_state.indexrelid
                  JOIN pg_catalog.pg_am AS access_method
                    ON access_method.oid=index_class.relam
                  WHERE index_state.indexrelid=
                          'public.ix_dts_ingest_events_processed_at_brin'::regclass
                    AND index_state.indisvalid
                    AND index_state.indisready
                    AND access_method.amname='brin'
                )
             OR NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_index AS index_state
                  JOIN pg_catalog.pg_class AS index_class
                    ON index_class.oid=index_state.indexrelid
                  JOIN pg_catalog.pg_am AS access_method
                    ON access_method.oid=index_class.relam
                  WHERE index_state.indexrelid=
                          'public.ix_dts_dirty_keys_ready_v3'::regclass
                    AND index_state.indisvalid
                    AND index_state.indisready
                    AND index_state.indpred IS NOT NULL
                    AND access_method.amname='btree'
                ) THEN
            RAISE EXCEPTION 'DTS_HOT_INDEX_POSTFLIGHT_INVALID';
          END IF;
        END
        $dts_hot_index_postflight$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError("20260825_106_dts_hot_indexes is forward-only")
