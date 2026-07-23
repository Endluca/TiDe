"""add governed teacher metric imports and mixed-data provenance

Revision ID: 20260717_04_teacher_metrics
Revises: 20260717_03_agent_lifecycle
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717_04_teacher_metrics"
down_revision: Union[str, None] = "20260717_03_agent_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_value = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    op.create_table(
        "data_import_batches",
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_sheet", sa.String(length=128), nullable=False),
        sa.Column("snapshot_label", sa.String(length=128), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("header", json_value, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", json_value, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("data_mode = 'MIXED'", name="ck_data_import_batch_mixed"),
        sa.CheckConstraint(
            "status IN ('VALIDATED', 'COMPLETED', 'FAILED')",
            name="ck_data_import_batch_status",
        ),
        sa.PrimaryKeyConstraint("batch_id"),
        sa.UniqueConstraint("source_sha256", "source_sheet", name="uq_teacher_import_content_sheet"),
    )
    op.create_index(
        "ix_data_import_batches_snapshot_label",
        "data_import_batches",
        ["snapshot_label"],
    )
    op.create_index("ix_data_import_batches_status", "data_import_batches", ["status"])
    op.create_index(
        "ix_data_import_source_time",
        "data_import_batches",
        ["source_system", "imported_at"],
    )

    op.add_column(
        "teachers",
        sa.Column("data_mode", sa.String(length=16), server_default="MOCK", nullable=False),
    )
    op.add_column(
        "teachers",
        sa.Column("source_batch_id", sa.String(length=96), nullable=True),
    )
    op.add_column(
        "teachers",
        sa.Column("source_snapshot_label", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_teachers_source_batch_id",
        "teachers",
        "data_import_batches",
        ["source_batch_id"],
        ["batch_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_teachers_source_batch_id", "teachers", ["source_batch_id"])
    op.create_index(
        "ix_teachers_source_snapshot_label",
        "teachers",
        ["source_snapshot_label"],
    )

    op.create_table(
        "teacher_metric_snapshots",
        sa.Column("snapshot_id", sa.String(length=192), nullable=False),
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_label", sa.String(length=128), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("real_name", sa.String(length=255), nullable=False),
        sa.Column("employment_status", sa.String(length=32), nullable=True),
        sa.Column("bu", sa.String(length=64), nullable=True),
        sa.Column("based_type", sa.String(length=64), nullable=True),
        sa.Column("teach_area_type", sa.String(length=64), nullable=True),
        sa.Column("onboard_date", sa.Date(), nullable=True),
        sa.Column("onboard_30d_end_date", sa.Date(), nullable=True),
        sa.Column("lessons_completed", sa.Integer(), nullable=False),
        sa.Column("total_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("peak_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("perfect_cnt", sa.Integer(), nullable=False),
        sa.Column("on_time_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("feedback_praise_cnt", sa.Integer(), nullable=False),
        sa.Column("feedback_favorite_cnt", sa.Integer(), nullable=False),
        sa.Column("completed_again_student_15d_cnt", sa.Integer(), nullable=False),
        sa.Column("late_cnt", sa.Integer(), nullable=False),
        sa.Column("early_cnt", sa.Integer(), nullable=False),
        sa.Column("real_absent_cnt", sa.Integer(), nullable=False),
        sa.Column("severe_redline_event", sa.Boolean(), nullable=False),
        sa.Column("capacity_score", sa.Float(), nullable=False),
        sa.Column("new_teacher_task_score", sa.Float(), nullable=False),
        sa.Column("class_quality_no_issue_rate", sa.Float(), nullable=False),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("user_feedback_score", sa.Float(), nullable=False),
        sa.Column("class_quality_score", sa.Float(), nullable=False),
        sa.Column("raw_total_score", sa.Float(), nullable=False),
        sa.Column("public_total_score", sa.Float(), nullable=False),
        sa.Column("metric_inputs", json_value, nullable=False),
        sa.Column("metric_provenance", json_value, nullable=False),
        sa.Column("raw_payload", json_value, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("data_mode = 'MIXED'", name="ck_teacher_metric_snapshot_mixed"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["data_import_batches.batch_id"],
            name="fk_teacher_metric_snapshot_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "batch_id",
            "teacher_id",
            name="uq_teacher_metric_snapshot_batch_teacher",
        ),
    )
    for column in (
        "batch_id",
        "teacher_id",
        "snapshot_label",
        "employment_status",
        "bu",
        "based_type",
        "teach_area_type",
    ):
        op.create_index(
            f"ix_teacher_metric_snapshots_{column}",
            "teacher_metric_snapshots",
            [column],
        )
    op.create_index(
        "ix_teacher_metric_snapshot_ops_filter",
        "teacher_metric_snapshots",
        ["snapshot_label", "employment_status", "bu", "based_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_teacher_metric_snapshot_ops_filter", table_name="teacher_metric_snapshots")
    for column in reversed(
        (
            "batch_id",
            "teacher_id",
            "snapshot_label",
            "employment_status",
            "bu",
            "based_type",
            "teach_area_type",
        )
    ):
        op.drop_index(
            f"ix_teacher_metric_snapshots_{column}",
            table_name="teacher_metric_snapshots",
        )
    op.drop_table("teacher_metric_snapshots")

    op.drop_index("ix_teachers_source_snapshot_label", table_name="teachers")
    op.drop_index("ix_teachers_source_batch_id", table_name="teachers")
    op.drop_constraint("fk_teachers_source_batch_id", "teachers", type_="foreignkey")
    op.drop_column("teachers", "source_snapshot_label")
    op.drop_column("teachers", "source_batch_id")
    op.drop_column("teachers", "data_mode")

    op.drop_index("ix_data_import_source_time", table_name="data_import_batches")
    op.drop_index("ix_data_import_batches_status", table_name="data_import_batches")
    op.drop_index("ix_data_import_batches_snapshot_label", table_name="data_import_batches")
    op.drop_table("data_import_batches")
