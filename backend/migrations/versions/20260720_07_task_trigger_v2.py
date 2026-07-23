"""add simplified task publication and versioned trigger policies

Revision ID: 20260720_07_task_trigger_v2
Revises: 20260717_06_policy_lineage
Create Date: 2026-07-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260720_07_task_trigger_v2"
down_revision: Union[str, None] = "20260717_06_policy_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "task_output_templates_v2",
        sa.Column("row_id", sa.String(length=160), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("execution_owner", sa.String(length=32), nullable=False),
        sa.Column("external_task_template_code", sa.String(length=128), nullable=False),
        sa.Column("source_mode", sa.String(length=24), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'RETIRED')",
            name="ck_task_output_template_v2_status",
        ),
        sa.CheckConstraint(
            "execution_owner = 'TEACHER_APP'",
            name="ck_task_output_template_v2_execution_owner",
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "template_id",
            "template_version",
            name="uq_task_output_template_v2_version",
        ),
    )
    op.create_index(
        "ix_task_output_template_v2_status",
        "task_output_templates_v2",
        ["status", "template_id"],
    )
    op.create_index(
        op.f("ix_task_output_templates_v2_template_id"),
        "task_output_templates_v2",
        ["template_id"],
    )
    op.create_index(
        op.f("ix_task_output_templates_v2_external_task_template_code"),
        "task_output_templates_v2",
        ["external_task_template_code"],
    )

    op.create_table(
        "trigger_policies_v2",
        sa.Column("row_id", sa.String(length=192), nullable=False),
        sa.Column("trigger_rule_id", sa.String(length=96), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("signal_code", sa.String(length=128), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("source_mode", sa.String(length=24), nullable=False),
        sa.Column("manual_gate", sa.Boolean(), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT_PENDING_CONFIRMATION', 'PUBLISHED', 'RETIRED')",
            name="ck_trigger_policy_v2_status",
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "trigger_rule_id",
            "policy_version",
            name="uq_trigger_policy_v2_version",
        ),
    )
    op.create_index(
        "ix_trigger_policy_v2_match",
        "trigger_policies_v2",
        ["status", "signal_code"],
    )
    op.create_index(
        op.f("ix_trigger_policies_v2_trigger_rule_id"),
        "trigger_policies_v2",
        ["trigger_rule_id"],
    )
    op.create_index(
        op.f("ix_trigger_policies_v2_signal_code"),
        "trigger_policies_v2",
        ["signal_code"],
    )
    op.create_index(
        op.f("ix_trigger_policies_v2_template_id"),
        "trigger_policies_v2",
        ["template_id"],
    )

    op.create_table(
        "task_publications_v2",
        sa.Column("publication_id", sa.String(length=128), nullable=False),
        sa.Column("assignment_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("trigger_rule_id", sa.String(length=96), nullable=False),
        sa.Column("trigger_rule_version", sa.Integer(), nullable=False),
        sa.Column("publication_status", sa.String(length=32), nullable=False),
        sa.Column("is_preview", sa.Boolean(), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=True),
        sa.Column("latest_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("trigger_reason_snapshot", JSON_VALUE, nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_status IN ('PREVIEW_ONLY', 'PENDING_DISPATCH', 'RECEIVED', 'VIEWED', "
            "'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', "
            "'EXPIRED', 'WAIVED', 'CANCELLED')",
            name="ck_task_publication_v2_status",
        ),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.teacher_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("publication_id"),
        sa.UniqueConstraint("assignment_id", name="uq_task_publication_v2_assignment"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_publication_v2_idempotency"),
    )
    op.create_index(
        "ix_task_publication_v2_output",
        "task_publications_v2",
        ["publication_status", "teacher_id", "is_preview"],
    )
    for column in ("teacher_id", "template_id", "trigger_rule_id", "publication_status", "external_task_id"):
        op.create_index(
            op.f(f"ix_task_publications_v2_{column}"),
            "task_publications_v2",
            [column],
        )

    op.create_table(
        "task_status_callbacks_v2",
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("publication_id", sa.String(length=128), nullable=False),
        sa.Column("assignment_id", sa.String(length=128), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["task_publications_v2.publication_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider_event_id"),
        sa.UniqueConstraint(
            "publication_id",
            "sequence",
            name="uq_task_status_callback_v2_sequence",
        ),
    )
    op.create_index(
        "ix_task_status_callback_v2_publication_time",
        "task_status_callbacks_v2",
        ["publication_id", "occurred_at"],
    )
    for column in ("assignment_id", "external_task_id"):
        op.create_index(
            op.f(f"ix_task_status_callbacks_v2_{column}"),
            "task_status_callbacks_v2",
            [column],
        )


def downgrade() -> None:
    op.drop_table("task_status_callbacks_v2")
    op.drop_table("task_publications_v2")
    op.drop_table("trigger_policies_v2")
    op.drop_table("task_output_templates_v2")
