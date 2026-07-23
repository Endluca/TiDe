"""operator authentication and RBAC

Revision ID: 20260717_01_auth
Revises: 27db1e431977
Create Date: 2026-07-17
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_01_auth"
down_revision: Union[str, None] = "27db1e431977"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operator_accounts",
        sa.Column("operator_id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operator_id"),
    )
    op.create_index("ix_operator_accounts_username", "operator_accounts", ["username"], unique=True)

    op.create_table(
        "operator_role_grants",
        sa.Column("grant_id", sa.String(length=36), nullable=False),
        sa.Column("operator_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=48), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('VIEWER', 'CASE_OPERATOR', 'SENIOR_REVIEWER', 'CONFIG_PUBLISHER', "
            "'EXTERNAL_ACTION_APPROVER', 'AUDITOR')",
            name="ck_operator_role_valid",
        ),
        sa.ForeignKeyConstraint(["operator_id"], ["operator_accounts.operator_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("grant_id"),
        sa.UniqueConstraint("operator_id", "role", name="uq_operator_role_grant"),
    )
    op.create_index("ix_operator_role_grants_operator_id", "operator_role_grants", ["operator_id"])
    op.create_index("ix_operator_role_grants_role", "operator_role_grants", ["role"])

    op.create_table(
        "operator_sessions",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("operator_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["operator_accounts.operator_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_operator_sessions_operator_id", "operator_sessions", ["operator_id"])
    op.create_index("ix_operator_sessions_token_hash", "operator_sessions", ["token_hash"], unique=True)
    op.create_index("ix_operator_sessions_expires_at", "operator_sessions", ["expires_at"])
    op.create_index(
        "ix_operator_sessions_operator_active",
        "operator_sessions",
        ["operator_id", "revoked_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operator_sessions_operator_active", table_name="operator_sessions")
    op.drop_index("ix_operator_sessions_expires_at", table_name="operator_sessions")
    op.drop_index("ix_operator_sessions_token_hash", table_name="operator_sessions")
    op.drop_index("ix_operator_sessions_operator_id", table_name="operator_sessions")
    op.drop_table("operator_sessions")

    op.drop_index("ix_operator_role_grants_role", table_name="operator_role_grants")
    op.drop_index("ix_operator_role_grants_operator_id", table_name="operator_role_grants")
    op.drop_table("operator_role_grants")

    op.drop_index("ix_operator_accounts_username", table_name="operator_accounts")
    op.drop_table("operator_accounts")
