"""allow the application runtime to read pipeline control.

Revision ID: 20260825_105_pipeline_read_acl
Revises: 20260825_104_runtime_table_acl
Create Date: 2026-08-25

The Outbox runtime compares the database qualification gate with its exact
environment value.  Its startup identity check previously required the same
role not to have SELECT on ``dts_pipeline_control``, making the first runtime
snapshot deterministically fail with SQLSTATE 42501.  Keep control mutation
protected while granting the table-level read needed by every snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_105_pipeline_read_acl"
down_revision: Union[str, None] = "20260825_104_runtime_table_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $runtime_pipeline_read_acl$
        BEGIN
          IF to_regrole('tit_growth_app') IS NULL
             OR to_regclass('public.dts_pipeline_control') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_RUNTIME_PIPELINE_READ_PREREQUISITE_MISSING';
          END IF;

          GRANT SELECT ON TABLE public.dts_pipeline_control
          TO tit_growth_app;
          REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER
          ON TABLE public.dts_pipeline_control FROM tit_growth_app;

          IF NOT has_table_privilege(
                   'tit_growth_app','public.dts_pipeline_control','SELECT'
                 )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_pipeline_control','INSERT'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_pipeline_control','UPDATE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_pipeline_control','DELETE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_pipeline_control','TRUNCATE'
                )
             OR has_schema_privilege('tit_growth_app','public','CREATE') THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_PIPELINE_READ_ACL_INVALID';
          END IF;
        END
        $runtime_pipeline_read_acl$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260825_105_pipeline_read_acl is forward-only"
    )
