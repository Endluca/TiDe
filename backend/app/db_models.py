from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DataImportBatchRecord(Base):
    """One immutable batch received from a file baseline or a daily source API."""

    __tablename__ = "data_import_batches"
    __table_args__ = (
        UniqueConstraint("source_sha256", "source_sheet", name="uq_data_import_content_sheet"),
        CheckConstraint(
            "sync_mode IN ('MANUAL_BASELINE', 'API_DAILY')",
            name="ck_data_import_batch_sync_mode",
        ),
        CheckConstraint(
            "data_mode IN ('REAL', 'MIXED')",
            name="ck_data_import_batch_data_mode",
        ),
        CheckConstraint(
            "status IN ('VALIDATED', 'COMPLETED', 'FAILED')",
            name="ck_data_import_batch_status",
        ),
        Index("ix_data_import_source_time", "source_system", "imported_at"),
    )

    batch_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="TEACHER_SNAPSHOT"
    )
    sync_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="MANUAL_BASELINE"
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_label: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="MIXED")
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    header: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="COMPLETED", index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SourceRecord(Base):
    """Lossless source row; typed domain tables are projections of this evidence."""

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "source_sheet",
            "source_row_number",
            name="uq_source_record_batch_sheet_row",
        ),
        Index("ix_source_record_business_key", "batch_id", "business_key"),
        Index("ix_source_record_teacher_time", "teacher_id", "occurred_at"),
    )

    source_record_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("data_import_batches.batch_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_sheet: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    business_key: Mapped[str] = mapped_column(String(256), nullable=False)
    teacher_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    lesson_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    row_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TeacherRecord(Base):
    __tablename__ = "teachers"

    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    camp_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graduation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    graduation_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    source_batch_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("data_import_batches.batch_id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_snapshot_label: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TeacherMetricSnapshotRecord(Base):
    """Queryable projection plus lossless source row for one teacher and batch."""

    __tablename__ = "teacher_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("batch_id", "teacher_id", name="uq_teacher_metric_snapshot_batch_teacher"),
        CheckConstraint("data_mode = 'MIXED'", name="ck_teacher_metric_snapshot_mixed"),
        Index(
            "ix_teacher_metric_snapshot_ops_filter",
            "snapshot_label",
            "employment_status",
            "bu",
            "based_type",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("data_import_batches.batch_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_label: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="MIXED")
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    score_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    real_name: Mapped[str] = mapped_column(String(255), nullable=False)
    employment_status: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    bu: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    based_type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    teach_area_type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    onboard_date: Mapped[Optional[date]] = mapped_column(Date)
    onboard_30d_end_date: Mapped[Optional[date]] = mapped_column(Date)
    first_booked_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    is_cpl_tesol: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_self_introduce: Mapped[Optional[bool]] = mapped_column(Boolean)
    lessons_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    total_completed_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peak_completed_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peak_slot_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    perfect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_time_completed_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feedback_praise_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feedback_favorite_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_again_student_15d_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    late_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    early_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    real_absent_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severe_redline_event: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capacity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    new_teacher_task_score: Mapped[float] = mapped_column(Float, nullable=False, default=30)
    class_quality_no_issue_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)

    reliability_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    user_feedback_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    class_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    public_total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    metric_inputs: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    metric_provenance: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LessonFactRecord(Base):
    __tablename__ = "lesson_facts"
    __table_args__ = (
        UniqueConstraint("source_record_id", name="uq_lesson_fact_source_record"),
        CheckConstraint(
            "complaint_level_rank IS NULL OR complaint_level_rank BETWEEN 0 AND 4",
            name="ck_lesson_fact_complaint_rank",
        ),
        Index("ix_lesson_fact_teacher_local_date", "teacher_id", "lesson_local_date"),
        Index("ix_lesson_fact_complaint_l3", "complaint_category_l3"),
    )

    lesson_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    scheduled_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lesson_lifecycle_status: Mapped[str] = mapped_column(String(48), nullable=False)
    lesson_local_date: Mapped[Optional[date]] = mapped_column(Date)
    lesson_local_time: Mapped[Optional[time]] = mapped_column(Time)
    student_id_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    is_late: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_early: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_false_early_leave: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_peak: Mapped[Optional[bool]] = mapped_column(Boolean)
    negative_score: Mapped[Optional[float]] = mapped_column(Float)
    has_negative_feedback_tag: Mapped[Optional[bool]] = mapped_column(Boolean)
    feedback_detail: Mapped[Optional[str]] = mapped_column(Text)
    negative_tag_values: Mapped[list[Any]] = mapped_column(
        JSON_VALUE, nullable=False, default=list, server_default=text("'[]'")
    )
    absence_reason_detail: Mapped[Optional[str]] = mapped_column(String(512))
    complaint_category_l1: Mapped[Optional[str]] = mapped_column(String(255))
    complaint_category_l2: Mapped[Optional[str]] = mapped_column(String(255))
    complaint_category_l3: Mapped[Optional[str]] = mapped_column(String(500))
    complaint_source_level: Mapped[Optional[str]] = mapped_column(String(32))
    complaint_level_rank: Mapped[Optional[int]] = mapped_column(Integer)
    complaint_route: Mapped[Optional[str]] = mapped_column(String(32))
    complaint_rule_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("complaint_category_rules.rule_id", ondelete="RESTRICT"),
        index=True,
    )
    is_blocked: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_favorited: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_positive_feedback_tag: Mapped[Optional[bool]] = mapped_column(Boolean)
    positive_tag_value: Mapped[Optional[str]] = mapped_column(String(255))
    is_rebooked: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_camera_off: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_cpu_usage_high: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_network_delay_high: Mapped[Optional[bool]] = mapped_column(Boolean)
    source_batch_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("data_import_batches.batch_id", ondelete="RESTRICT"),
        index=True,
    )
    source_record_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("source_records.source_record_id", ondelete="RESTRICT"),
    )
    valid_for_scoring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ComplaintCategoryRuleRecord(Base):
    """One exact level-3 complaint mapping from the approved source workbook."""

    __tablename__ = "complaint_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "category_l3_normalized",
            name="uq_complaint_rule_batch_l3",
        ),
        CheckConstraint(
            "normalized_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
            name="ck_complaint_rule_level",
        ),
        CheckConstraint(
            "severity_rank BETWEEN 0 AND 4",
            name="ck_complaint_rule_rank",
        ),
        Index("ix_complaint_rule_l3_current", "category_l3_normalized", "batch_id"),
    )

    rule_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("data_import_batches.batch_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_sheet: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    category_l1: Mapped[Optional[str]] = mapped_column(String(255))
    category_l2: Mapped[Optional[str]] = mapped_column(String(255))
    category_l3: Mapped[str] = mapped_column(String(500), nullable=False)
    category_l3_normalized: Mapped[str] = mapped_column(String(500), nullable=False)
    source_level: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_level: Mapped[str] = mapped_column(String(2), nullable=False)
    severity_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    default_route: Mapped[str] = mapped_column(String(32), nullable=False)
    learning_title: Mapped[Optional[str]] = mapped_column(String(500))
    learning_url: Mapped[Optional[str]] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PersonalizedTriggerMatchRecord(Base):
    """Immutable evidence that a deterministic personalized rule matched once."""

    __tablename__ = "personalized_trigger_matches"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_personalized_trigger_match_dedupe"),
        CheckConstraint(
            "output_type IN ('TEACHER_TASK', 'OPS_CASE', 'NOTIFICATION', 'PENDING_DATA')",
            name="ck_personalized_trigger_match_output_type",
        ),
        CheckConstraint(
            "match_status IN ('MATCHED', 'MATERIALIZED', 'SUPPRESSED', 'FAILED', "
            "'PENDING_DATA')",
            name="ck_personalized_trigger_match_status",
        ),
        Index(
            "ix_personalized_trigger_match_ops",
            "match_status",
            "output_type",
            "matched_at",
        ),
        Index("ix_personalized_trigger_match_teacher", "teacher_id", "matched_at"),
    )

    trigger_match_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trigger_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    lesson_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("lesson_facts.lesson_id", ondelete="RESTRICT"), index=True
    )
    source_record_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("source_records.source_record_id", ondelete="RESTRICT"), index=True
    )
    complaint_rule_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("complaint_category_rules.rule_id", ondelete="RESTRICT"), index=True
    )
    scope_key: Mapped[str] = mapped_column(String(256), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_title: Mapped[str] = mapped_column(String(500), nullable=False)
    output_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    match_status: Mapped[str] = mapped_column(String(24), nullable=False, default="MATCHED")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    materialized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LessonDimensionScoreRecord(Base):
    __tablename__ = "lesson_dimension_scores"
    __table_args__ = (
        UniqueConstraint("camp_enrollment_id", "lesson_id", "dimension", name="uq_lesson_dimension_state"),
    )

    score_state_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lesson_facts.lesson_id"), nullable=False, index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    current_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_coverage: Mapped[Optional[str]] = mapped_column(String(32))
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    score_as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_score_entry_id: Mapped[Optional[str]] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class ScoreAccountRecord(Base):
    __tablename__ = "score_accounts"
    __table_args__ = (UniqueConstraint("teacher_id", "dimension", name="uq_score_account_teacher_dimension"),)

    account_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    current_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    minimum_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="mock_score_v1")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class ScoreEntryRecord(Base):
    __tablename__ = "score_entries"
    __table_args__ = (
        Index(
            "uq_score_entries_fixed_task_assignment",
            "task_assignment_id",
            unique=True,
            postgresql_where=text(
                "entry_type = 'FIXED_TASK_AWARD' AND task_assignment_id IS NOT NULL"
            ),
            sqlite_where=text(
                "entry_type = 'FIXED_TASK_AWARD' AND task_assignment_id IS NOT NULL"
            ),
        ),
    )

    score_entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    lesson_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INITIAL")
    delta_score: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CONFIRMED")
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reversal_of_score_entry_id: Mapped[Optional[str]] = mapped_column(String(128))
    task_assignment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("task_assignments.assignment_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class TaskTemplateRecord(Base):
    """The single current task-template catalog used by both services."""

    __tablename__ = "task_templates"
    __table_args__ = (
        UniqueConstraint("template_id", "template_version", name="uq_task_template_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'RETIRED')",
            name="ck_task_template_status",
        ),
        CheckConstraint(
            "execution_owner = 'TEACHER_APP'",
            name="ck_task_template_execution_owner",
        ),
        CheckConstraint(
            "integration_mode IN ('OUTBOUND_MANAGED', 'INBOUND_STATUS_ONLY')",
            name="ck_task_template_integration_mode",
        ),
        Index("ix_task_template_status", "status", "template_id"),
    )

    row_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False, default="TEACHER_TASK")
    execution_owner: Mapped[str] = mapped_column(String(32), nullable=False, default="TEACHER_APP")
    integration_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OUTBOUND_MANAGED"
    )
    external_task_template_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TaskAssignmentRecord(Base):
    __tablename__ = "task_assignments"
    __table_args__ = (
        CheckConstraint(
            "task_kind IN ('FIXED_GROWTH', 'PERSONALIZED_IMPROVEMENT')",
            name="ck_task_assignment_kind",
        ),
        CheckConstraint(
            "creator_system IN ('TEACHER_APP', 'TRIGGER_CENTER')",
            name="ck_task_assignment_creator",
        ),
        CheckConstraint(
            "status IN ('ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'SUBMITTED', "
            "'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')",
            name="ck_task_assignment_status",
        ),
        CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_task_assignment_priority",
        ),
        CheckConstraint(
            "source_mode IN ('REAL', 'DERIVED_REAL', 'MOCK', 'MOCK_SIMULATION', 'MOCK_PROXY')",
            name="ck_task_assignment_source_mode",
        ),
        CheckConstraint("row_version >= 1", name="ck_task_assignment_row_version"),
        CheckConstraint(
            "((task_code IN ('G01', 'G02', 'G03', 'G04', 'G05', 'G06', 'G07', "
            "'G08', 'G09', 'G10') AND task_kind = 'FIXED_GROWTH' "
            "AND creator_system = 'TRIGGER_CENTER') OR "
            "(task_code NOT IN ('G01', 'G02', 'G03', 'G04', 'G05', 'G06', 'G07', "
            "'G08', 'G09', 'G10') AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
            "AND creator_system = 'TRIGGER_CENTER'))",
            name="ck_task_assignment_owner_consistency",
        ),
        CheckConstraint(
            "task_kind <> 'FIXED_GROWTH' OR "
            "dedupe_key = 'fixed:' || teacher_id || ':' || task_code",
            name="ck_task_assignment_fixed_dedupe",
        ),
        CheckConstraint(
            "(due_at IS NULL AND timezone_used IS NULL AND timezone_source IS NULL "
            "AND timezone_verified_at IS NULL) OR "
            "(due_at IS NOT NULL AND timezone_used IS NOT NULL AND timezone_source IS NOT NULL "
            "AND timezone_verified_at IS NOT NULL)",
            name="ck_task_assignment_due_timezone",
        ),
        CheckConstraint(
            "status NOT IN ('FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED') "
            "OR status_reason_code IS NOT NULL",
            name="ck_task_assignment_required_reason",
        ),
        CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="ck_task_assignment_completed_at",
        ),
        CheckConstraint(
            "btrim(why) <> '' AND btrim(dedupe_key) <> '' "
            "AND btrim(created_by) <> '' AND btrim(updated_by) <> ''",
            name="ck_task_assignment_required_text",
        ),
        UniqueConstraint("dedupe_key", name="uq_task_assignment_dedupe"),
        Index(
            "uq_task_assignment_fixed_teacher_task",
            "teacher_id",
            "task_code",
            unique=True,
            postgresql_where=text("task_kind = 'FIXED_GROWTH'"),
            sqlite_where=text("task_kind = 'FIXED_GROWTH'"),
        ),
        Index(
            "ix_task_assignment_teacher_status_priority",
            "teacher_id",
            "status",
            "priority",
        ),
        Index("ix_task_assignment_due_at", "due_at"),
        Index("ix_task_assignment_template_version", "template_version_id"),
    )

    assignment_id: Mapped[str] = mapped_column(
        String(128), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="RESTRICT"), nullable=False
    )
    task_code: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_templates.row_id", ondelete="RESTRICT"), nullable=False
    )
    task_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    creator_system: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'ASSIGNED'")
    )
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    display_title: Mapped[Optional[str]] = mapped_column(String(500))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False, default=dict, server_default=text("'{}'")
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    timezone_used: Mapped[Optional[str]] = mapped_column(String(64))
    timezone_source: Mapped[Optional[str]] = mapped_column(String(32))
    timezone_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status_reason_code: Mapped[Optional[str]] = mapped_column(String(128))
    source_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("CURRENT_USER")
    )
    updated_by: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("CURRENT_USER")
    )
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class NotificationRecord(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "task_id IS NOT NULL OR source_ref IS NOT NULL",
            name="ck_notification_source",
        ),
        UniqueConstraint("source_ref", name="uq_notification_source_ref"),
    )

    notification_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("task_assignments.assignment_id"), nullable=True, unique=True
    )
    source_ref: Mapped[Optional[str]] = mapped_column(String(256))
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    clicked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    response_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class NotificationEventRecord(Base):
    __tablename__ = "notification_events"

    notification_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    notification_id: Mapped[str] = mapped_column(ForeignKey("notifications.notification_id"), nullable=False, index=True)
    delivery_status: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


Index(
    "ix_notifications_teacher_requested_desc",
    NotificationRecord.teacher_id,
    NotificationRecord.requested_at.desc(),
    NotificationRecord.notification_id.desc(),
)
Index(
    "uq_notification_events_notification_request_hash",
    NotificationEventRecord.notification_id,
    NotificationEventRecord.request_hash,
    unique=True,
)


class OpsCaseRecord(Base):
    __tablename__ = "ops_cases"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_reason: Mapped[Optional[str]] = mapped_column(String(64))
    external_action_status: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class OpsDecisionRecord(Base):
    __tablename__ = "ops_decisions"

    decision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("ops_cases.case_id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False, default="OPS_USER")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class OutboundOutputRecord(Base):
    __tablename__ = "outbound_outputs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbound_output_idempotency"),
        Index("ix_outputs_type_status_teacher", "output_type", "status", "teacher_id"),
    )

    output_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    display_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    delivery_kind: Mapped[Optional[str]] = mapped_column(String(40))
    audience_type: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_id: Mapped[Optional[str]] = mapped_column(String(128))
    recipient_name: Mapped[Optional[str]] = mapped_column(String(255))
    channel: Mapped[Optional[str]] = mapped_column(String(40))
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    teacher_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"

    outbox_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    teacher_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SYSTEM")
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class AgentDecisionRecord(Base):
    __tablename__ = "agent_decisions"

    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    route: Mapped[str] = mapped_column(String(24), nullable=False)
    planner: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    constraints: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    selected_template_ids: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    scope: Mapped[str] = mapped_column(String(48), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128))
    response_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProviderCallRecord(Base):
    __tablename__ = "provider_calls"

    provider_call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    call_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    result_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
