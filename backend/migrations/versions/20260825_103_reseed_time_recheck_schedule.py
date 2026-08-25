"""restore the teacher time recheck schedule singleton after the history reset.

Revision ID: 20260825_103_reseed_time_recheck_schedule
Revises: 20260825_102_dts_ingest_batch_throughput
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_103_reseed_time_recheck_schedule"
down_revision: Union[str, None] = (
    "20260825_102_dts_ingest_batch_throughput"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        INSERT INTO public.dts_teacher_time_recheck_schedule(
          schedule_id,last_enqueued_business_date,last_enqueued_generation,
          last_enqueued_at,row_version
        ) VALUES ('PRIMARY',NULL,NULL,NULL,1)
        ON CONFLICT (schedule_id) DO NOTHING
        """
    )
    op.execute(
        """
        DO $verify_time_recheck_schedule_reseed$
        BEGIN
          IF (SELECT count(*)
              FROM public.dts_teacher_time_recheck_schedule)<>1
             OR NOT EXISTS (
               SELECT 1
               FROM public.dts_teacher_time_recheck_schedule
               WHERE schedule_id='PRIMARY' AND row_version>=1
             ) THEN
            RAISE EXCEPTION
              'DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED'
              USING ERRCODE='55000';
          END IF;
        END
        $verify_time_recheck_schedule_reseed$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260825_103_reseed_time_recheck_schedule is forward-only"
    )
