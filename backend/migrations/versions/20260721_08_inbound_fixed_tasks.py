"""add inbound-only fixed mandatory task status mirror

Revision ID: 20260721_08_inbound_fixed_tasks
Revises: 20260720_07_task_trigger_v2
Create Date: 2026-07-21
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260721_08_inbound_fixed_tasks"
down_revision: Union[str, None] = "20260720_07_task_trigger_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

FIXED_TASK_CODES_SQL = "'G01', 'G02', 'G03', 'G04', 'G05', 'G06', 'G07', 'G08', 'G09', 'G10'"
FIXED_TRIGGER_CODES_SQL = (
    "'TR-G01', 'TR-G02', 'TR-G03', 'TR-G04', 'TR-G05', "
    "'TR-G06', 'TR-G07', 'TR-G08', 'TR-G09', 'TR-G10'"
)


def upgrade() -> None:
    bind = op.get_bind()
    migration_time = datetime.now(timezone.utc)
    migration_time_iso = migration_time.isoformat().replace("+00:00", "Z")
    op.add_column(
        "task_output_templates_v2",
        sa.Column(
            "integration_mode",
            sa.String(length=32),
            nullable=False,
            server_default="OUTBOUND_MANAGED",
        ),
    )
    op.create_check_constraint(
        "ck_task_output_template_v2_integration_mode",
        "task_output_templates_v2",
        "integration_mode IN ('OUTBOUND_MANAGED', 'INBOUND_STATUS_ONLY')",
    )
    template_table = sa.table(
        "task_output_templates_v2",
        sa.column("row_id", sa.String()),
        sa.column("template_id", sa.String()),
        sa.column("integration_mode", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("payload", JSON_VALUE),
    )
    fixed_templates = bind.execute(
        sa.select(
            template_table.c.row_id,
            template_table.c.revision,
            template_table.c.payload,
        ).where(
            template_table.c.template_id.in_([f"G{index:02d}" for index in range(1, 11)])
        )
    ).mappings()
    for row in fixed_templates:
        next_revision = int(row["revision"]) + 1
        payload = dict(row["payload"] or {})
        payload.update(
            integration_mode="INBOUND_STATUS_ONLY",
            accepted_callback_statuses=["COMPLETED"],
            revision=next_revision,
            updated_by="SYSTEM_MIGRATION_20260721",
            updated_at=migration_time_iso,
        )
        bind.execute(
            template_table.update()
            .where(template_table.c.row_id == row["row_id"])
            .values(
                integration_mode="INBOUND_STATUS_ONLY",
                revision=next_revision,
                updated_by="SYSTEM_MIGRATION_20260721",
                updated_at=migration_time,
                payload=payload,
            )
        )

    # Preserve the old TR-G rows as audit history, but remove them from the
    # current confirmation/runtime catalog.
    policy_table = sa.table(
        "trigger_policies_v2",
        sa.column("row_id", sa.String()),
        sa.column("trigger_rule_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("payload", JSON_VALUE),
    )
    fixed_policies = bind.execute(
        sa.select(
            policy_table.c.row_id,
            policy_table.c.revision,
            policy_table.c.payload,
        ).where(
            policy_table.c.trigger_rule_id.in_(
                [f"TR-G{index:02d}" for index in range(1, 11)]
            ),
            policy_table.c.status != "RETIRED",
        )
    ).mappings()
    for row in fixed_policies:
        next_revision = int(row["revision"]) + 1
        payload = dict(row["payload"] or {})
        payload.update(
            status="RETIRED",
            revision=next_revision,
            updated_by="SYSTEM_MIGRATION_20260721",
            updated_at=migration_time_iso,
        )
        bind.execute(
            policy_table.update()
            .where(policy_table.c.row_id == row["row_id"])
            .values(
                status="RETIRED",
                revision=next_revision,
                updated_by="SYSTEM_MIGRATION_20260721",
                updated_at=migration_time,
                payload=payload,
            )
        )

    op.create_table(
        "fixed_task_instances_v2",
        sa.Column("fixed_task_instance_id", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("task_code", sa.String(length=64), nullable=False),
        sa.Column("template_row_id", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("latest_status", sa.String(length=32), nullable=False),
        sa.Column("latest_sequence", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_entry_id", sa.String(length=128), nullable=True),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"task_code IN ({FIXED_TASK_CODES_SQL})",
            name="ck_fixed_task_instance_v2_task_code",
        ),
        sa.CheckConstraint(
            "latest_status = 'COMPLETED'",
            name="ck_fixed_task_instance_v2_status",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.teacher_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_row_id"],
            ["task_output_templates_v2.row_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fixed_task_instance_id"),
        sa.UniqueConstraint(
            "source_system",
            "external_task_id",
            name="uq_fixed_task_instance_v2_external",
        ),
    )
    op.create_index(
        "ix_fixed_task_instance_v2_teacher_task",
        "fixed_task_instances_v2",
        ["teacher_id", "task_code", "updated_at"],
    )
    op.create_index(
        op.f("ix_fixed_task_instances_v2_teacher_id"),
        "fixed_task_instances_v2",
        ["teacher_id"],
    )
    op.create_index(
        op.f("ix_fixed_task_instances_v2_task_code"),
        "fixed_task_instances_v2",
        ["task_code"],
    )

    op.create_table(
        "fixed_task_status_events_v2",
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("fixed_task_instance_id", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("task_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("result_code", sa.String(length=128), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'COMPLETED'",
            name="ck_fixed_task_status_event_v2_status",
        ),
        sa.CheckConstraint(
            "result_code = 'VERIFIED_COMPLETED'",
            name="ck_fixed_task_status_event_v2_result_code",
        ),
        sa.ForeignKeyConstraint(
            ["fixed_task_instance_id"],
            ["fixed_task_instances_v2.fixed_task_instance_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider_event_id"),
        sa.UniqueConstraint(
            "fixed_task_instance_id",
            "sequence",
            name="uq_fixed_task_status_event_v2_sequence",
        ),
    )
    op.create_index(
        "ix_fixed_task_status_event_v2_instance_time",
        "fixed_task_status_events_v2",
        ["fixed_task_instance_id", "occurred_at"],
    )
    op.create_index(
        op.f("ix_fixed_task_status_events_v2_teacher_id"),
        "fixed_task_status_events_v2",
        ["teacher_id"],
    )
    op.create_index(
        op.f("ix_fixed_task_status_events_v2_task_code"),
        "fixed_task_status_events_v2",
        ["task_code"],
    )


def downgrade() -> None:
    op.drop_table("fixed_task_status_events_v2")
    op.drop_table("fixed_task_instances_v2")
    op.execute(
        sa.text(
            f"UPDATE trigger_policies_v2 "
            f"SET status = 'DRAFT_PENDING_CONFIRMATION', revision = revision + 1, "
            f"updated_by = 'SYSTEM_MIGRATION_DOWNGRADE', updated_at = CURRENT_TIMESTAMP "
            f"WHERE trigger_rule_id IN ({FIXED_TRIGGER_CODES_SQL}) "
            f"AND status = 'RETIRED'"
        )
    )
    op.drop_constraint(
        "ck_task_output_template_v2_integration_mode",
        "task_output_templates_v2",
        type_="check",
    )
    op.drop_column("task_output_templates_v2", "integration_mode")
