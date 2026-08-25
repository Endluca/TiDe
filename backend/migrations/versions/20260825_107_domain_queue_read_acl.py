"""allow the Domain runtime to read claimed queue evidence.

Revision ID: 20260825_107_domain_queue_read_acl
Revises: 20260825_106_dts_hot_indexes
Create Date: 2026-08-25

The claim/lease commands remain SECURITY DEFINER, but every Domain projector
must read the immutable causal inputs for the claim it just received.  Some
projectors also join the leased queue row to those inputs.  Rev80 intentionally
removed all direct queue access and only granted the command functions, so the
first production claim failed with SQLSTATE 42501.  Restore only the two
required SELECT capabilities; all queue mutation stays behind the protected
commands.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_107_domain_queue_read_acl"
down_revision: Union[str, None] = "20260825_106_dts_hot_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $domain_queue_read_acl$
        BEGIN
          IF to_regrole('tit_growth_app') IS NULL
             OR to_regclass('public.dts_dirty_keys') IS NULL
             OR to_regclass('public.dts_dirty_key_inputs') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_DOMAIN_QUEUE_READ_PREREQUISITE_MISSING';
          END IF;

          GRANT SELECT ON TABLE
            public.dts_dirty_keys,
            public.dts_dirty_key_inputs
          TO tit_growth_app;

          REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE
            public.dts_dirty_keys,
            public.dts_dirty_key_inputs
          FROM tit_growth_app;

          IF NOT has_table_privilege(
                   'tit_growth_app','public.dts_dirty_keys','SELECT'
                 )
             OR NOT has_table_privilege(
                  'tit_growth_app','public.dts_dirty_key_inputs','SELECT'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_keys','INSERT'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_keys','UPDATE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_keys','DELETE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_key_inputs','INSERT'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_key_inputs','UPDATE'
                )
             OR has_table_privilege(
                  'tit_growth_app','public.dts_dirty_key_inputs','DELETE'
                )
             OR has_schema_privilege('tit_growth_app','public','CREATE') THEN
            RAISE EXCEPTION 'DTS_V2_DOMAIN_QUEUE_READ_ACL_INVALID';
          END IF;
        END
        $domain_queue_read_acl$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260825_107_domain_queue_read_acl is forward-only"
    )
