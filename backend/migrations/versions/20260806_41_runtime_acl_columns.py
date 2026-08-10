"""allow the runtime login rehash to update its timestamp

Revision ID: 20260806_41_runtime_acl_columns
Revises: 20260806_40_runtime_acl
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op


revision: str = "20260806_41_runtime_acl_columns"
down_revision: str | None = "20260806_40_runtime_acl"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        GRANT UPDATE (updated_at)
            ON TABLE public.operator_accounts TO tit_growth_app;

        DO $runtime_operator_acl_assertions$
        BEGIN
            IF has_table_privilege(
                'tit_growth_app', 'public.operator_accounts', 'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app received table-wide operator account UPDATE';
            END IF;
            IF NOT has_column_privilege(
                'tit_growth_app',
                'public.operator_accounts',
                'password_hash',
                'UPDATE'
            ) OR NOT has_column_privilege(
                'tit_growth_app',
                'public.operator_accounts',
                'updated_at',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app is missing login rehash column privileges';
            END IF;
            IF has_column_privilege(
                'tit_growth_app',
                'public.operator_accounts',
                'username',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app may not update operator identity fields';
            END IF;
        END
        $runtime_operator_acl_assertions$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        REVOKE UPDATE (updated_at)
            ON TABLE public.operator_accounts FROM tit_growth_app;
        """
    )
