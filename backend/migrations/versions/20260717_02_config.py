"""add governed runtime configuration center

Revision ID: 20260717_02_config
Revises: 20260717_01_auth
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717_02_config"
down_revision: Union[str, None] = "20260717_01_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_value = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.create_table(
        "config_versions",
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("config_key", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("high_impact", sa.Boolean(), nullable=False),
        sa.Column("payload", json_value, nullable=False),
        sa.Column("validation_errors", json_value, nullable=False),
        sa.Column("source_version_id", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("validated_by", sa.String(length=128), nullable=True),
        sa.Column("published_by", sa.String(length=128), nullable=True),
        sa.Column("retired_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "config_key IN ('SCORE_GRADUATION', 'AGENT_POLICY', 'DELIVERY_POLICY')",
            name="ck_config_versions_key",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'RETIRED')",
            name="ck_config_versions_status",
        ),
        sa.ForeignKeyConstraint(["source_version_id"], ["config_versions.version_id"]),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint("config_key", "version_number", name="uq_config_version_number"),
    )
    op.create_index("ix_config_versions_config_key", "config_versions", ["config_key"], unique=False)
    op.create_index("ix_config_versions_status", "config_versions", ["status"], unique=False)
    op.create_index("ix_config_key_status", "config_versions", ["config_key", "status"], unique=False)
    op.create_index(
        "uq_one_published_config_per_key",
        "config_versions",
        ["config_key"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
        sqlite_where=sa.text("status = 'PUBLISHED'"),
    )

    op.create_table(
        "config_publication_audits",
        sa.Column("audit_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("config_key", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["config_versions.version_id"]),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_config_publication_audits_version_id",
        "config_publication_audits",
        ["version_id"],
        unique=False,
    )
    op.create_index(
        "ix_config_publication_audits_config_key",
        "config_publication_audits",
        ["config_key"],
        unique=False,
    )
    op.create_index(
        "ix_config_audit_version_time",
        "config_publication_audits",
        ["version_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_config_audit_version_time", table_name="config_publication_audits")
    op.drop_index("ix_config_publication_audits_config_key", table_name="config_publication_audits")
    op.drop_index("ix_config_publication_audits_version_id", table_name="config_publication_audits")
    op.drop_table("config_publication_audits")
    op.drop_index("uq_one_published_config_per_key", table_name="config_versions")
    op.drop_index("ix_config_key_status", table_name="config_versions")
    op.drop_index("ix_config_versions_status", table_name="config_versions")
    op.drop_index("ix_config_versions_config_key", table_name="config_versions")
    op.drop_table("config_versions")
