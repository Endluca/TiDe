"""restore simple table-level Outbox runtime permissions.

Revision ID: 20260825_104_runtime_table_acl
Revises: 20260825_103_reseed_time_recheck_schedule
Create Date: 2026-08-25

The application role historically owns the Outbox DML contract.  Later v2
migrations narrowed UPDATE to individual technical columns.  That made the
runtime ACL harder to operate and left ``SELECT ... FOR UPDATE`` dependent on
database-specific column-privilege behaviour.  Keep the stable table-level DML
contract here; the existing Outbox trigger remains the authority for immutable
identity/payload fields, legal status transitions, recovery and deletion.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_104_runtime_table_acl"
down_revision: Union[str, None] = (
    "20260825_103_reseed_time_recheck_schedule"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $runtime_table_acl$
        DECLARE column_name text;
        BEGIN
          IF to_regrole('tit_growth_app') IS NULL
             OR to_regclass('public.outbox_events') IS NULL
             OR to_regprocedure(
                  'public.guard_outbox_event_update()'
                ) IS NULL
             OR NOT EXISTS (
               SELECT 1
               FROM pg_catalog.pg_trigger trigger_row
               WHERE trigger_row.tgrelid='public.outbox_events'::regclass
                 AND trigger_row.tgname='guard_outbox_event_update'
                 AND trigger_row.tgenabled IN ('O','A')
             ) THEN
            RAISE EXCEPTION 'DTS_V2_OUTBOX_TABLE_ACL_PREREQUISITE_MISSING';
          END IF;

          -- Remove historical per-column ACL entries.  Effective DML is now
          -- intentionally visible and manageable at the table level only.
          FOR column_name IN
            SELECT attribute.attname::text
            FROM pg_catalog.pg_attribute attribute
            WHERE attribute.attrelid='public.outbox_events'::regclass
              AND attribute.attnum>0
              AND NOT attribute.attisdropped
          LOOP
            EXECUTE format(
              'REVOKE ALL PRIVILEGES (%I) ON TABLE '
              'public.outbox_events FROM tit_growth_app',
              column_name
            );
          END LOOP;

          GRANT SELECT,INSERT,UPDATE,DELETE
          ON TABLE public.outbox_events TO tit_growth_app;
          REVOKE TRUNCATE,REFERENCES,TRIGGER
          ON TABLE public.outbox_events FROM tit_growth_app;

          IF NOT has_table_privilege(
                   'tit_growth_app','public.outbox_events','SELECT'
                 )
             OR NOT has_table_privilege(
                   'tit_growth_app','public.outbox_events','INSERT'
                 )
             OR NOT has_table_privilege(
                   'tit_growth_app','public.outbox_events','UPDATE'
                 )
             OR NOT has_table_privilege(
                   'tit_growth_app','public.outbox_events','DELETE'
                 )
             OR has_table_privilege(
                  'tit_growth_app','public.outbox_events','TRUNCATE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.outbox_events','TRIGGER'
                )
             OR has_schema_privilege(
                  'tit_growth_app','public','CREATE'
                )
             OR EXISTS (
               SELECT 1
               FROM pg_catalog.pg_attribute attribute
               CROSS JOIN LATERAL unnest(attribute.attacl)
                 privilege_row(acl)
               WHERE attribute.attrelid='public.outbox_events'::regclass
                 AND split_part(privilege_row.acl::text,'=',1)
                       ='tit_growth_app'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_OUTBOX_TABLE_ACL_INVALID';
          END IF;
        END
        $runtime_table_acl$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError("20260825_104_runtime_table_acl is forward-only")
