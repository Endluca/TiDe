from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    BigInteger,
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
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ComplaintRuleImportRecord(Base):
    """One immutable reviewed complaint-rule workbook."""

    __tablename__ = "complaint_rule_imports"

    source_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_rows: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeacherSourceWideRecord(Base):
    """Current upstream teacher-wide source row; no derived TiDe columns."""

    __tablename__ = "teacher_source_wide"

    tchr_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    real_name: Mapped[Optional[str]] = mapped_column(Text)
    center_type_id: Mapped[Optional[str]] = mapped_column(Text)
    center_type_desc: Mapped[Optional[str]] = mapped_column(Text)
    bu: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    status_on_date: Mapped[Optional[date]] = mapped_column(Date)
    status_off_date: Mapped[Optional[date]] = mapped_column(Date)
    last_on_date: Mapped[Optional[date]] = mapped_column(Date)
    job_days: Mapped[Optional[int]] = mapped_column(Integer)
    job_month: Mapped[Optional[float]] = mapped_column(Float)
    teach_area_type: Mapped[Optional[str]] = mapped_column(Text)
    onboard_date: Mapped[Optional[date]] = mapped_column(Date)
    onboard_30d_end_date: Mapped[Optional[date]] = mapped_column(Date)
    first_open_slot_dt: Mapped[Optional[date]] = mapped_column(Date)
    first_booked_dt: Mapped[Optional[date]] = mapped_column(Date)
    first_completed_dt: Mapped[Optional[date]] = mapped_column(Date)
    total_booked_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    peak_booked_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    total_completed_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    peak_completed_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    absent_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    late_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    early_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    anomaly_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    perfect_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    no_notice_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    first_completed_student_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_total_eval_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_praise_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_negative_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_complaint_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_valid_complaint_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_favorite_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_block_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    total_slot_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    reg_slot_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    peak_slot_cnt: Mapped[Optional[int]] = mapped_column(Integer)
    slot_days: Mapped[Optional[int]] = mapped_column(Integer)
    peak_slot_days: Mapped[Optional[int]] = mapped_column(Integer)
    reliability_absent_rate: Mapped[Optional[float]] = mapped_column(Float)
    reliability_late_rate: Mapped[Optional[float]] = mapped_column(Float)
    reliability_early_leave_rate: Mapped[Optional[float]] = mapped_column(Float)
    reliability_late_early_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_praise_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_negative_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_complaint_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_favorite_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_block_rate: Mapped[Optional[float]] = mapped_column(Float)
    feedback_eval_rate: Mapped[Optional[float]] = mapped_column(Float)
    capacity_avg_completed_per_day: Mapped[Optional[float]] = mapped_column(Float)
    capacity_peak_slot_rate: Mapped[Optional[float]] = mapped_column(Float)
    capacity_key_slot_day_rate: Mapped[Optional[float]] = mapped_column(Float)
    is_cpl_tesol: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_self_introduce: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)


class LessonSourceWideRecord(Base):
    """Current upstream lesson-wide source row; no derived TiDe columns."""

    __tablename__ = "lesson_source_wide"
    # Teacher-time reads drive lesson scoring; teacher-student-time reads drive
    # first-favorite attribution and distinct-student blacklist evaluation.
    __table_args__ = (
        Index(
            "ix_lesson_source_wide_teacher_time",
            "老师id",
            "上课日期",
            "上课时间",
        ),
        Index(
            "ix_lesson_source_wide_teacher_student_time",
            "老师id",
            "学员id",
            "上课日期",
            "上课时间",
        ),
    )

    course_id: Mapped[str] = mapped_column("课程id", String(128), primary_key=True)
    lesson_date: Mapped[Optional[date]] = mapped_column("上课日期", Date)
    lesson_time: Mapped[Optional[time]] = mapped_column("上课时间", Time)
    is_peak: Mapped[Optional[bool]] = mapped_column("是否高峰", Boolean)
    teacher_id: Mapped[str] = mapped_column("老师id", String(64), nullable=False)
    student_id: Mapped[Optional[str]] = mapped_column("学员id", String(128))
    lesson_status: Mapped[Optional[str]] = mapped_column("课程状态", Text)
    absence_reason_detail: Mapped[Optional[str]] = mapped_column(
        "缺席原因明细",
        Text,
    )
    is_late: Mapped[Optional[bool]] = mapped_column("迟到", Boolean)
    is_early: Mapped[Optional[bool]] = mapped_column("早退", Boolean)
    negative_score: Mapped[Optional[float]] = mapped_column("差评分", Float)
    has_negative_feedback_tag: Mapped[Optional[bool]] = mapped_column(
        "差评标签",
        Boolean,
    )
    complaint_category_l1: Mapped[Optional[str]] = mapped_column(
        "投诉一级分类",
        Text,
    )
    complaint_category_l2: Mapped[Optional[str]] = mapped_column(
        "投诉二级分类",
        Text,
    )
    complaint_category_l3: Mapped[Optional[str]] = mapped_column(
        "投诉三级分类",
        Text,
    )
    is_blocked: Mapped[Optional[bool]] = mapped_column("是否拉黑", Boolean)
    is_favorited: Mapped[Optional[bool]] = mapped_column("收藏", Boolean)
    has_positive_feedback_tag: Mapped[Optional[bool]] = mapped_column(
        "好评标签",
        Boolean,
    )
    feedback_detail: Mapped[Optional[str]] = mapped_column("评价详情", Text)
    is_camera_off: Mapped[Optional[bool]] = mapped_column("未开摄像头", Boolean)
    is_cpu_usage_high: Mapped[Optional[bool]] = mapped_column(
        "cpu占用过高",
        Boolean,
    )
    is_network_delay_high: Mapped[Optional[bool]] = mapped_column(
        "网络延迟过高",
        Boolean,
    )
    is_false_early_leave: Mapped[Optional[bool]] = mapped_column("假早退", Boolean)


class DtsIngestCheckpointRecord(Base):
    """Durable replay floor after one DTS event transaction commits."""

    __tablename__ = "dts_ingest_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_checkpoint_region",
        ),
        CheckConstraint(
            "partition_id >= 0 AND next_offset >= 0",
            name="ck_dts_checkpoint_offsets",
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_position: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class DtsIngestEventRecord(Base):
    """One safe, immutable processing receipt per Kafka offset."""

    __tablename__ = "dts_ingest_events"
    __table_args__ = (
        CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_event_region",
        ),
        CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0 AND dirty_key_count >= 0",
            name="ck_dts_event_offsets",
        ),
        CheckConstraint(
            "route_status IN ('PROCESSED', 'IGNORED')",
            name="ck_dts_event_route_status",
        ),
        Index(
            "ix_dts_ingest_events_source_table_processed",
            "source_region",
            "source_table",
            "processed_at",
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_value: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_txid: Mapped[str] = mapped_column(Text, nullable=False)
    source_position: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    source_database: Mapped[Optional[str]] = mapped_column(Text)
    source_schema: Mapped[Optional[str]] = mapped_column(Text)
    source_table: Mapped[Optional[str]] = mapped_column(Text)
    route_status: Mapped[str] = mapped_column(String(16), nullable=False)
    dirty_key_count: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_codes: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class DtsSourceRowRecord(Base):
    """Latest whitelisted source image seen after the subscription start."""

    __tablename__ = "dts_source_rows"
    __table_args__ = (
        CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_source_row_region",
        ),
        CheckConstraint(
            "last_partition >= 0 AND last_offset >= 0 AND row_version >= 1",
            name="ck_dts_source_row_version",
        ),
        Index(
            "ix_dts_source_rows_table_active",
            "source_region",
            "source_table",
            "is_deleted",
        ),
        Index(
            "ix_dts_source_rows_dependency_keys",
            "dependency_keys",
            postgresql_using="gin",
            postgresql_ops={"dependency_keys": "jsonb_path_ops"},
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_key_data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    dependency_keys: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    source_row: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_position: Mapped[str] = mapped_column(Text, nullable=False)
    last_topic: Mapped[str] = mapped_column(String(512), nullable=False)
    last_partition: Mapped[int] = mapped_column(Integer, nullable=False)
    last_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class DtsDirtyKeyRecord(Base):
    """Coalesced key waiting for deterministic source-wide recomputation."""

    __tablename__ = "dts_dirty_keys"
    __table_args__ = (
        CheckConstraint(
            "key_type IN "
            "('COURSE', 'TEACHER', 'TEACHER_STUDENT', 'LABEL', 'COMPLAINT_CATEGORY')",
            name="ck_dts_dirty_key_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY', 'COMPLETED')",
            name="ck_dts_dirty_key_status",
        ),
        CheckConstraint(
            "last_source_region IN ('ovs', 'dom')",
            name="ck_dts_dirty_key_region",
        ),
        CheckConstraint(
            "pending_event_count >= 1 AND attempt_count >= 0 "
            "AND last_partition >= 0 AND last_offset >= 0 AND row_version >= 1",
            name="ck_dts_dirty_key_counters",
        ),
        Index(
            "ix_dts_dirty_keys_ready",
            "status",
            "next_attempt_at",
            "last_seen_at",
        ),
        Index(
            "ix_dts_dirty_keys_pending_fifo",
            "last_seen_at",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_dts_dirty_keys_retry_due",
            "next_attempt_at",
            "last_seen_at",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text(
                "status = 'RETRY' "
                "AND next_attempt_at < 'infinity'::timestamptz"
            ),
        ),
    )

    key_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_part_1: Mapped[str] = mapped_column(String(256), primary_key=True)
    key_part_2: Mapped[str] = mapped_column(String(256), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    pending_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    last_source_table: Mapped[Optional[str]] = mapped_column(String(128))
    last_topic: Mapped[str] = mapped_column(String(512), nullable=False)
    last_partition: Mapped[int] = mapped_column(Integer, nullable=False)
    last_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issue_codes: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class TeacherRecord(Base):
    __tablename__ = "teachers"

    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    camp_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graduation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    gold_qualified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    graduation_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    source_snapshot_label: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TeacherQualificationRecord(Base):
    """Current criteria and irreversible earned qualifications for one teacher."""

    __tablename__ = "teacher_qualifications"
    __table_args__ = (
        CheckConstraint(
            "gold_qualified = FALSE OR graduation_qualified = TRUE",
            name="ck_teacher_qualification_gold_requires_graduation",
        ),
        CheckConstraint(
            "graduation_qualified = TRUE OR graduation_qualified_at IS NULL",
            name="ck_teacher_qualification_graduation_time",
        ),
        CheckConstraint(
            "gold_qualified = TRUE OR gold_qualified_at IS NULL",
            name="ck_teacher_qualification_gold_time",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_teacher_qualification_revision",
        ),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    graduation_criteria_met: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    graduation_qualified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    graduation_qualified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    gold_criteria_met: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    gold_qualified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    gold_qualified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    gate_results: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )


class ComplaintCategoryRuleRecord(Base):
    """One exact level-3 complaint mapping from the approved source workbook."""

    __tablename__ = "complaint_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "source_sha256",
            "category_l3_normalized",
            name="uq_complaint_rule_source_l3",
        ),
        CheckConstraint(
            "severity_rank BETWEEN 0 AND 4",
            name="ck_complaint_rule_rank",
        ),
        Index(
            "ix_complaint_rule_l3_current",
            "category_l3_normalized",
            "source_sha256",
        ),
    )

    rule_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_sha256: Mapped[str] = mapped_column(
        ForeignKey("complaint_rule_imports.source_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    category_l1: Mapped[Optional[str]] = mapped_column(String(255))
    category_l2: Mapped[Optional[str]] = mapped_column(String(255))
    category_l3: Mapped[str] = mapped_column(String(500), nullable=False)
    category_l3_normalized: Mapped[str] = mapped_column(String(500), nullable=False)
    source_level: Mapped[str] = mapped_column(String(32), nullable=False)
    severity_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    default_route: Mapped[str] = mapped_column(String(32), nullable=False)
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
        Index(
            "ix_personalized_trigger_match_active_output",
            "output_type",
            "output_id",
            "matched_at",
            postgresql_where=text("match_status <> 'SUPPRESSED'"),
            sqlite_where=text("match_status <> 'SUPPRESSED'"),
        ),
        Index("ix_personalized_trigger_match_teacher", "teacher_id", "matched_at"),
    )

    trigger_match_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trigger_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="RESTRICT"), nullable=False
    )
    lesson_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("lesson_source_wide.课程id", ondelete="SET NULL"), index=True
    )
    complaint_rule_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("complaint_category_rules.rule_id", ondelete="RESTRICT"), index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_title: Mapped[str] = mapped_column(String(500), nullable=False)
    output_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    match_status: Mapped[str] = mapped_column(String(24), nullable=False, default="MATCHED")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    materialized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LessonScoreResultRecord(Base):
    """Current one-row score result derived from one lesson source row."""

    __tablename__ = "lesson_score_results"
    __table_args__ = (
        CheckConstraint(
            "reliability_score >= 0 AND user_feedback_score >= 0 "
            "AND class_quality_score >= 0 AND lesson_total_score >= 0",
            name="ck_lesson_score_result_nonnegative",
        ),
        CheckConstraint(
            "abs(lesson_total_score - (reliability_score + "
            "user_feedback_score + class_quality_score)) <= 0.000001",
            name="ck_lesson_score_result_total",
        ),
        CheckConstraint(
            "projection_revision >= 1",
            name="ck_lesson_score_result_projection_revision",
        ),
    )

    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lesson_source_wide.课程id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_feedback_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )
    reliability_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )
    class_quality_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )
    lesson_total_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )
    dimensions: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )


class ScoreAccountRecord(Base):
    __tablename__ = "score_accounts"

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id"),
        primary_key=True,
    )
    dimension: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="mock_score_v1")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class ScoreComponentAccountRecord(Base):
    """Current teacher-level score for one explainable scoring component."""

    __tablename__ = "score_component_accounts"
    __table_args__ = (
        CheckConstraint(
            "source_scope IN ('LESSON', 'TEACHER', 'TASK')",
            name="ck_score_component_account_source_scope",
        ),
        CheckConstraint(
            "reconciliation_status IN "
            "('MATCHED', 'MATCHED_ZERO', 'PARTIAL', 'MISMATCH', "
            "'SOURCE_MISSING', 'NOT_APPLICABLE')",
            name="ck_score_component_account_reconciliation",
        ),
        Index(
            "ix_score_component_account_teacher_dimension",
            "teacher_id",
            "dimension",
        ),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="CASCADE"),
        primary_key=True,
    )
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    component_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    source_metric: Mapped[Optional[str]] = mapped_column(String(128))
    unit_count: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    points_per_unit: Mapped[Optional[float]] = mapped_column(Float)
    current_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    lesson_attributed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lesson_attributed_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    unattributed_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reconciliation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NOT_APPLICABLE"
    )
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
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
            "((task_code IN ('G00', 'G01', 'G02', 'G03', 'G04', 'G05', 'G06', "
            "'G07', 'G08', 'G09') AND task_kind = 'FIXED_GROWTH' "
            "AND creator_system = 'TRIGGER_CENTER') OR "
            "(task_code NOT IN ('G00', 'G01', 'G02', 'G03', 'G04', 'G05', 'G06', "
            "'G07', 'G08', 'G09') AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
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
        CheckConstraint(
            "why !~ U&'[\\4E00-\\9FFF]'",
            name="ck_task_assignment_why_teacher_english",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "task_kind <> 'PERSONALIZED_IMPROVEMENT' "
            "OR why LIKE '%Evidence:%'",
            name="ck_task_assignment_personalized_why_evidence",
        ),
        CheckConstraint(
            "task_kind <> 'PERSONALIZED_IMPROVEMENT' "
            "OR (display_title IS NOT NULL "
            "AND btrim(display_title) <> '' "
            "AND display_title !~ U&'[\\4E00-\\9FFF]')",
            name="ck_task_assignment_personalized_title_english",
        ).ddl_if(dialect="postgresql"),
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
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND length(btrim(payload->>'title')) > 0 "
            "AND length(btrim(payload->>'body')) > 0 "
            "AND position('Evidence:' in payload->>'body') > 0 "
            "AND (payload->>'title') !~ "
            "'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]' "
            "AND (payload->>'body') !~ "
            "'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]'",
            name="ck_notifications_teacher_copy_english",
        ).ddl_if(dialect="postgresql"),
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


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_pending_settlement_claim",
            "event_type",
            "aggregate_type",
            "available_at",
            "created_at",
            "outbox_id",
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
    )

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
    __table_args__ = (
        Index(
            "ix_audit_events_teacher_sequence",
            "teacher_id",
            "sequence",
        ),
        Index(
            "ix_audit_events_structured_search_trgm",
            literal_column(
                "((((((((((event_id::text || ' '::text) || "
                "event_type::text) || ' '::text) || "
                "COALESCE(teacher_id, ''::character varying)::text) || "
                "' '::text) || "
                "COALESCE(task_id, ''::character varying)::text) || "
                "' '::text) || "
                "COALESCE(case_id, ''::character varying)::text) || "
                "' '::text) || actor_type::text)"
            ).label("structured_search"),
            postgresql_using="gin",
            postgresql_ops={
                "structured_search": "gin_trgm_ops",
            },
        ).ddl_if(dialect="postgresql"),
    )

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


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    scope: Mapped[str] = mapped_column(String(48), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128))
    response_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
