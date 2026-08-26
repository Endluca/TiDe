"""allow the Domain runtime to execute the typed-id CHECK validator.

Revision ID: 20260826_108_domain_validator_acl
Revises: 20260825_107_domain_queue_read_acl
Create Date: 2026-08-26

Relationship tables use ``dts_v2_typed_id_valid`` from CHECK constraints.
Rev74 revoked the function from every runtime role, so otherwise-authorized
Domain writes fail with SQLSTATE 42501 while PostgreSQL evaluates the
constraint.  Grant only this immutable validator to the existing application
role; no table mutation or schema-creation capability is added.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260826_108_domain_validator_acl"
down_revision: Union[str, None] = "20260825_107_domain_queue_read_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $domain_validator_acl$
        DECLARE
          validator_oid regprocedure;
          validator_volatility "char";
          public_can_execute boolean;
        BEGIN
          validator_oid := to_regprocedure(
            'public.dts_v2_typed_id_valid(text,text)'
          );
          IF to_regrole('tit_growth_app') IS NULL
             OR validator_oid IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_DOMAIN_VALIDATOR_ACL_PREREQUISITE_MISSING';
          END IF;

          SELECT provolatile
          INTO validator_volatility
          FROM pg_catalog.pg_proc
          WHERE oid = validator_oid;
          IF validator_volatility IS DISTINCT FROM 'i' THEN
            RAISE EXCEPTION
              'DTS_V2_DOMAIN_VALIDATOR_MUST_BE_IMMUTABLE';
          END IF;

          REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_typed_id_valid(text, text)
          FROM PUBLIC;
          GRANT EXECUTE ON FUNCTION
            public.dts_v2_typed_id_valid(text, text)
          TO tit_growth_app;

          SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                function_row.proacl,
                pg_catalog.acldefault('f', function_row.proowner)
              )
            ) AS privilege_row
            WHERE function_row.oid = validator_oid
              AND privilege_row.grantee = 0
              AND privilege_row.privilege_type = 'EXECUTE'
          )
          INTO public_can_execute;

          IF NOT has_function_privilege(
                   'tit_growth_app',
                   'public.dts_v2_typed_id_valid(text,text)',
                   'EXECUTE'
                 )
             OR public_can_execute
             OR has_schema_privilege(
                  'tit_growth_app','public','CREATE'
                ) THEN
            RAISE EXCEPTION 'DTS_V2_DOMAIN_VALIDATOR_ACL_INVALID';
          END IF;
        END
        $domain_validator_acl$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260826_108_domain_validator_acl is forward-only"
    )
