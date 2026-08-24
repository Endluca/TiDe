from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
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


def _outbox_payload_sha256(context: Any) -> str:
    """Match ``dts_canonical_json_sha256_v1`` for ORM-originated JSON."""

    payload = context.get_current_parameters()["payload"]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dts_typed_id_check(name: str, *, nullable: bool) -> str:
    typed = (
        f"(({name}_type = 'NUMERIC' "
        f"AND {name}_numeric IS NOT NULL AND {name}_text IS NULL "
        f"AND {name} = trim_scale({name}_numeric)::text) "
        f"OR ({name}_type = 'TEXT' "
        f"AND {name}_numeric IS NULL AND {name}_text IS NOT NULL "
        f"AND {name}_text <> '' AND {name} = {name}_text))"
    )
    if not nullable:
        return f"{name} IS NOT NULL AND {typed}"
    return (
        f"(({name} IS NULL AND {name}_type IS NULL "
        f"AND {name}_numeric IS NULL AND {name}_text IS NULL) OR {typed})"
    )


def _dts_evidence_check(status: str, error: str) -> str:
    return (
        f"{status} IN ('CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
        f"AND (({status} = 'CONFIRMED' AND {error} IS NULL) "
        f"OR ({status} <> 'CONFIRMED' AND {error} IS NOT NULL "
        f"AND btrim({error}) <> ''))"
    )


def _dts_source_position_check(column: str, *, nullable: bool = False) -> str:
    valid = (
        f"jsonb_typeof({column}) = 'object' "
        f"AND {column} = jsonb_build_object("
        f"'v',{column}->'v',"
        f"'source_timestamp',{column}->'source_timestamp',"
        f"'record_id_type',{column}->'record_id_type',"
        f"'record_id',{column}->'record_id',"
        f"'source_partition_epoch_id',{column}->'source_partition_epoch_id',"
        f"'topic',{column}->'topic',"
        f"'partition_id',{column}->'partition_id',"
        f"'offset_value',{column}->'offset_value') "
        f"AND {column}->'v' = '1'::jsonb "
        f"AND jsonb_typeof({column}->'source_timestamp') IN ('null','string') "
        f"AND jsonb_typeof({column}->'record_id_type') = 'string' "
        f"AND {column}->>'record_id_type' IN ('none','numeric','text') "
        f"AND (({column}->>'record_id_type' = 'none' "
        f"AND jsonb_typeof({column}->'record_id') = 'null') "
        f"OR ({column}->>'record_id_type' IN ('numeric','text') "
        f"AND jsonb_typeof({column}->'record_id') = 'string' "
        f"AND {column}->>'record_id' <> '')) "
        f"AND jsonb_typeof({column}->'source_partition_epoch_id') = 'string' "
        f"AND {column}->>'source_partition_epoch_id' <> '' "
        f"AND jsonb_typeof({column}->'topic') = 'string' "
        f"AND {column}->>'topic' <> '' "
        f"AND jsonb_typeof({column}->'partition_id') = 'number' "
        f"AND ({column}->>'partition_id') ~ '^(0|[1-9][0-9]*)$' "
        f"AND jsonb_typeof({column}->'offset_value') = 'number' "
        f"AND ({column}->>'offset_value') ~ '^(0|[1-9][0-9]*)$'"
    )
    return f"{column} IS NULL OR ({valid})" if nullable else valid


class ComplaintRuleImportRecord(Base):
    """One immutable reviewed complaint-rule workbook."""

    __tablename__ = "complaint_rule_imports"
    __table_args__ = (
        Index(
            "uq_complaint_rule_import_published_v2",
            "status",
            unique=True,
            postgresql_where=text("status='PUBLISHED'"),
            sqlite_where=text("status='PUBLISHED'"),
        ),
        Index(
            "uq_complaint_rule_activation_generation_v2",
            "activation_generation",
            unique=True,
            postgresql_where=text("activation_generation IS NOT NULL"),
            sqlite_where=text("activation_generation IS NOT NULL"),
        ),
    )

    source_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_rows: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    publication_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1
    )
    activation_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: "0" * 64
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class TeacherSourceWideRecord(Base):
    """Current upstream teacher-wide source row; no derived TiDe columns."""

    __tablename__ = "teacher_source_wide"
    __table_args__ = (
        CheckConstraint(
            "(v2_dom_aggregate_revision IS NULL "
            "AND v2_ovs_aggregate_revision IS NULL "
            "AND v2_projection_generation IS NULL "
            "AND v2_materialized_event_id IS NULL "
            "AND v2_materialized_at IS NULL AND v2_row_version IS NULL "
            "AND first_open_slot_evidence_status IS NULL "
            "AND first_booked_evidence_status IS NULL "
            "AND first_completed_evidence_status IS NULL) OR "
            "(v2_dom_aggregate_revision >= 1 "
            "AND v2_ovs_aggregate_revision >= 1 "
            "AND v2_projection_generation >= 1 "
            "AND v2_materialized_event_id IS NOT NULL "
            "AND btrim(v2_materialized_event_id) <> '' "
            "AND v2_materialized_at IS NOT NULL AND v2_row_version >= 1 "
            "AND first_open_slot_evidence_status IN "
            "('CONFIRMED','CONFIRMED_EMPTY','LEGACY_FROZEN','SOURCE_MISSING') "
            "AND first_booked_evidence_status IN "
            "('CONFIRMED','CONFIRMED_EMPTY','LEGACY_FROZEN','SOURCE_MISSING') "
            "AND first_completed_evidence_status IN "
            "('CONFIRMED','CONFIRMED_EMPTY','LEGACY_FROZEN','SOURCE_MISSING'))",
            name="ck_teacher_source_wide_v2_materialization_shape",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_teacher_source_wide_v2_generation",
            "v2_projection_generation",
            "tchr_id",
        ),
    )

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
    first_open_slot_evidence_status: Mapped[Optional[str]] = mapped_column(
        String(32),
        comment=(
            "Lifetime first-open evidence: confirmed, proven empty, frozen "
            "legacy, or missing"
        ),
    )
    first_booked_evidence_status: Mapped[Optional[str]] = mapped_column(
        String(32),
        comment=(
            "Lifetime first-booked evidence: confirmed, proven empty, "
            "frozen legacy, or missing"
        ),
    )
    first_completed_evidence_status: Mapped[Optional[str]] = mapped_column(
        String(32),
        comment=(
            "Lifetime first-completion evidence: confirmed, proven empty, "
            "frozen legacy, or missing"
        ),
    )
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
    v2_dom_aggregate_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    v2_ovs_aggregate_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    v2_projection_generation: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment=(
            "Global serving generation; independent of both regional "
            "aggregate revisions"
        ),
    )
    v2_materialized_event_id: Mapped[Optional[str]] = mapped_column(String(512))
    v2_materialized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    v2_row_version: Mapped[Optional[int]] = mapped_column(BigInteger)


class LessonSourceWideRecord(Base):
    """Current upstream lesson-wide source row; no derived TiDe columns."""

    __tablename__ = "lesson_source_wide"
    # Teacher-time reads drive lesson scoring; teacher-student-time reads drive
    # first-favorite attribution and distinct-student blacklist evaluation.
    __table_args__ = (
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_lesson_source_wide_region",
        ),
        CheckConstraint(
            '"cpu占用过高" IS NULL AND "网络延迟过高" IS NULL',
            name="ck_lesson_source_cpu_network_retired_v1",
        ),
        Index(
            "ix_lesson_source_wide_region_teacher_time",
            "source_region",
            "老师id",
            "上课日期",
            "上课时间",
            "课程id",
        ),
        Index(
            "ix_lesson_source_wide_region_teacher_student_time",
            "source_region",
            "老师id",
            "学员id",
            "上课日期",
            "上课时间",
            "课程id",
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    course_id: Mapped[str] = mapped_column("课程id", String(128), primary_key=True)
    lesson_date: Mapped[Optional[date]] = mapped_column("上课日期", Date)
    lesson_time: Mapped[Optional[time]] = mapped_column("上课时间", Time)
    is_peak: Mapped[Optional[bool]] = mapped_column("是否高峰", Boolean)
    teacher_id: Mapped[Optional[str]] = mapped_column(
        "老师id", String(64), nullable=True
    )
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


class LessonSourceRegionBackfillManifestRecord(Base):
    """Operator-approved evidence for one legacy lesson region backfill."""

    __tablename__ = "lesson_source_region_backfill_manifest"
    __table_args__ = (
        CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_lesson_region_manifest_region",
        ),
        CheckConstraint(
            "evidence_kind IN ("
            "'SOURCE_APPOINT_SNAPSHOT','DTS_LEDGER','MANUAL_VERIFIED')",
            name="ck_lesson_region_manifest_evidence_kind",
        ),
        CheckConstraint(
            "btrim(evidence_ref) <> '' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "AND btrim(approved_by) <> ''",
            name="ck_lesson_region_manifest_evidence",
        ).ddl_if(dialect="postgresql"),
    )

    course_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class LessonSourceRegionMigrationControlRecord(Base):
    """Singleton expand/contract gate for the lesson region migration."""

    __tablename__ = "lesson_source_region_migration_control"
    __table_args__ = (
        CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_lesson_region_control_singleton",
        ),
        CheckConstraint(
            "phase IN ('EXPANDED','WRITER_READY','CONTRACTED')",
            name="ck_lesson_region_control_phase",
        ),
        CheckConstraint(
            "(phase = 'EXPANDED' AND writer_contract_version IS NULL "
            "AND writer_smoke_sha256 IS NULL AND approved_by IS NULL "
            "AND writer_ready_at IS NULL AND contracted_at IS NULL) OR "
            "(phase = 'WRITER_READY' AND writer_contract_version IS NOT NULL "
            "AND writer_smoke_sha256 ~ '^[0-9a-f]{64}$' "
            "AND approved_by IS NOT NULL AND btrim(approved_by) <> '' "
            "AND writer_ready_at IS NOT NULL AND contracted_at IS NULL) OR "
            "(phase = 'CONTRACTED' AND writer_contract_version IS NOT NULL "
            "AND writer_smoke_sha256 ~ '^[0-9a-f]{64}$' "
            "AND approved_by IS NOT NULL AND writer_ready_at IS NOT NULL "
            "AND contracted_at IS NOT NULL)",
            name="ck_lesson_region_control_shape",
        ).ddl_if(dialect="postgresql"),
    )

    control_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    writer_contract_version: Mapped[Optional[str]] = mapped_column(String(64))
    writer_smoke_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    approved_by: Mapped[Optional[str]] = mapped_column(String(128))
    writer_ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    contracted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


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
    source_partition_epoch_id: Mapped[Optional[str]] = mapped_column(String(160))
    consumer_group: Mapped[Optional[str]] = mapped_column(String(256))
    checkpoint_row_version: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_current_epoch: Mapped[Optional[bool]] = mapped_column(Boolean)


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
    identity_version: Mapped[Optional[str]] = mapped_column(String(32))
    source_partition_epoch_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_position_v2: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    event_payload_hash: Mapped[Optional[str]] = mapped_column(String(64))


class DtsSourceRowRecord(Base):
    """Latest whitelisted source image seen after the subscription start."""

    __tablename__ = "dts_source_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "source_region",
                "last_source_partition_epoch_id",
                "last_topic",
                "last_partition",
                "last_offset",
            ],
            [
                "dts_source_row_versions.source_region",
                "dts_source_row_versions.source_partition_epoch_id",
                "dts_source_row_versions.topic",
                "dts_source_row_versions.partition_id",
                "dts_source_row_versions.offset_value",
            ],
            name="fk_dts_source_row_current_version_event",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('ovs', 'dom')",
            name="ck_dts_source_row_region",
        ),
        CheckConstraint(
            "last_partition >= 0 AND last_offset >= 0 AND row_version >= 1",
            name="ck_dts_source_row_version",
        ),
        CheckConstraint(
            "source_table NOT IN ('dom_appoint', 'ovs_appoint') "
            "OR source_key_type IS NULL OR source_key_type = 'NUMERIC'",
            name="ck_dts_source_row_profiled_key_type",
        ),
        CheckConstraint(
            "public.dts_v2_source_row_transition_valid("
            "source_key, source_key_data, source_row_revision, "
            "last_source_partition_epoch_id, last_version_kind, "
            "source_position_v2, record_id_type, record_id_numeric, "
            "record_id_text, source_timestamp_v2, source_payload_hash, "
            "provenance_state, source_key_type, source_key_numeric, "
            "source_key_text, source_schema_profile_id, source_field_types"
            ") IS TRUE",
            name="ck_dts_source_row_v2_transition_shape",
        ).ddl_if(dialect="postgresql"),
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
    source_row_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_source_partition_epoch_id: Mapped[Optional[str]] = mapped_column(
        String(160)
    )
    last_version_kind: Mapped[Optional[str]] = mapped_column(String(32))
    source_position_v2: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    record_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    record_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    record_id_text: Mapped[Optional[str]] = mapped_column(Text)
    source_timestamp_v2: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    source_payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    provenance_state: Mapped[Optional[str]] = mapped_column(String(32))
    source_key_type: Mapped[Optional[str]] = mapped_column(String(16))
    source_key_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_key_text: Mapped[Optional[str]] = mapped_column(Text)
    source_schema_profile_id: Mapped[Optional[str]] = mapped_column(
        String(160)
    )
    source_field_types: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )


class DtsDirtyKeyRecord(Base):
    """Region-qualified v2 deterministic recomputation state machine."""

    __tablename__ = "dts_dirty_keys"
    __table_args__ = (
        CheckConstraint(
            "public.dts_dirty_key_identity_valid_v2("
            "source_region,key_type,key_part_1,key_part_2) IS TRUE",
            name="ck_dts_dirty_key_identity_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','WAITING_DEPENDENCY',"
            "'RETRY','DEAD','COMPLETED')",
            name="ck_dts_dirty_key_status_v2",
        ),
        CheckConstraint(
            "required_work_revision >= 1 "
            "AND completed_work_revision >= 0 "
            "AND completed_work_revision <= required_work_revision "
            "AND (claimed_through_work_revision IS NULL OR "
            "(claimed_through_work_revision >= 1 "
            "AND claimed_through_work_revision <= required_work_revision)) "
            "AND work_generation >= 1 AND dead_generation >= 0 "
            "AND last_input_revision >= 1 "
            "AND last_input_identity_hash ~ '^[0-9a-f]{64}$' "
            "AND row_version >= 1",
            name="ck_dts_dirty_key_revisions_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(status = 'PROCESSING' "
            "AND claimed_through_work_revision IS NOT NULL "
            "AND lease_owner_kind IS NOT NULL AND lease_owner IS NOT NULL "
            "AND btrim(lease_owner) <> '' AND lease_token IS NOT NULL "
            "AND btrim(lease_token) <> '' AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at > claimed_at AND next_attempt_at IS NULL "
            "AND ((lease_owner_kind = 'DOMAIN_PROJECTOR' AND key_type IN "
            "('COURSE','TEACHER','TEACHER_STUDENT','LABEL',"
            "'COMPLAINT_CATEGORY')) OR (lease_owner_kind = "
            "'SOURCEWIDE_TIME_RECHECK' AND key_type = "
            "'TEACHER_TIME_RECHECK'))) OR (status <> 'PROCESSING' "
            "AND claimed_through_work_revision IS NULL "
            "AND lease_owner_kind IS NULL AND lease_owner IS NULL "
            "AND lease_token IS NULL AND claimed_at IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_dts_dirty_key_lease_shape_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(status = 'PENDING' AND attempt_count = 0 "
            "AND next_attempt_at IS NOT NULL AND blocked_by IS NULL "
            "AND last_error_code IS NULL) OR (status = 'PROCESSING' "
            "AND attempt_count BETWEEN 0 AND 7 AND blocked_by IS NULL) "
            "OR (status = 'WAITING_DEPENDENCY' "
            "AND attempt_count BETWEEN 0 AND 7 "
            "AND next_attempt_at IS NULL AND blocked_by IS NOT NULL "
            "AND jsonb_typeof(blocked_by) = 'array' "
            "AND jsonb_array_length(blocked_by) > 0 "
            "AND last_error_code IS NULL) OR (status = 'RETRY' "
            "AND attempt_count BETWEEN 1 AND 7 "
            "AND next_attempt_at IS NOT NULL "
            "AND last_error_code IS NOT NULL AND blocked_by IS NULL) "
            "OR (status = 'DEAD' AND attempt_count = 8 "
            "AND next_attempt_at IS NULL AND last_error_code IS NOT NULL "
            "AND blocked_by IS NULL) OR (status = 'COMPLETED' "
            "AND attempt_count = 0 AND next_attempt_at IS NULL "
            "AND completed_work_revision >= required_work_revision "
            "AND last_error_code IS NULL AND blocked_by IS NULL)",
            name="ck_dts_dirty_key_state_shape_v2",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_dirty_keys_ready_v2",
            "status",
            "next_attempt_at",
            "updated_at",
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
        ),
        Index(
            "ix_dts_dirty_keys_pending_fifo_v2",
            "next_attempt_at",
            "updated_at",
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_dts_dirty_keys_retry_due_v2",
            "next_attempt_at",
            "updated_at",
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text("status = 'RETRY'"),
        ),
        CheckConstraint(
            "compat_status IN ('STANDBY','PENDING','PROCESSING','RETRY',"
            "'DEAD','COMPLETED') "
            "AND compat_required_work_revision >= 0 "
            "AND compat_completed_work_revision >= 0 "
            "AND compat_completed_work_revision <= "
            "compat_required_work_revision "
            "AND (compat_claimed_work_revision IS NULL OR "
            "(compat_claimed_work_revision >= 1 AND "
            "compat_claimed_work_revision <= "
            "compat_required_work_revision)) "
            "AND compat_attempt_count BETWEEN 0 AND 100 "
            "AND compat_row_version >= 1 "
            "AND ((compat_status = 'STANDBY' "
            "AND compat_attempt_count = 0 "
            "AND compat_claimed_work_revision IS NULL "
            "AND compat_next_attempt_at IS NULL "
            "AND compat_claimed_at IS NULL "
            "AND compat_claimed_by IS NULL "
            "AND compat_last_error_code IS NULL) OR "
            "(compat_status = 'PENDING' AND compat_attempt_count = 0 "
            "AND compat_claimed_work_revision IS NULL "
            "AND compat_next_attempt_at IS NOT NULL "
            "AND compat_claimed_at IS NULL "
            "AND compat_claimed_by IS NULL "
            "AND compat_last_error_code IS NULL) OR "
            "(compat_status = 'PROCESSING' "
            "AND compat_claimed_work_revision IS NOT NULL "
            "AND compat_next_attempt_at IS NULL "
            "AND compat_claimed_at IS NOT NULL "
            "AND nullif(btrim(compat_claimed_by), '') IS NOT NULL "
            "AND compat_last_error_code IS NULL) OR "
            "(compat_status = 'RETRY' AND compat_attempt_count >= 1 "
            "AND compat_claimed_work_revision IS NULL "
            "AND compat_next_attempt_at IS NOT NULL "
            "AND compat_claimed_at IS NULL "
            "AND compat_claimed_by IS NULL "
            "AND compat_last_error_code IS NOT NULL) OR "
            "(compat_status = 'DEAD' AND compat_attempt_count >= 1 "
            "AND compat_claimed_work_revision IS NULL "
            "AND compat_next_attempt_at IS NULL "
            "AND compat_claimed_at IS NULL "
            "AND compat_claimed_by IS NULL "
            "AND compat_last_error_code IS NOT NULL) OR "
            "(compat_status = 'COMPLETED' "
            "AND compat_attempt_count = 0 "
            "AND compat_completed_work_revision >= "
            "compat_required_work_revision "
            "AND compat_claimed_work_revision IS NULL "
            "AND compat_next_attempt_at IS NULL "
            "AND compat_claimed_at IS NULL "
            "AND compat_claimed_by IS NULL "
            "AND compat_last_error_code IS NULL))",
            name="ck_dts_dirty_key_compat_state_v1",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_dirty_keys_compat_pending_v1",
            "updated_at",
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text("compat_status = 'PENDING'"),
        ),
        Index(
            "ix_dts_dirty_keys_compat_retry_v1",
            "compat_next_attempt_at",
            "updated_at",
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            postgresql_where=text("compat_status = 'RETRY'"),
        ),
        {
            "comment": "Region-qualified v2 recomputation work; state "
            "changes only through SECURITY DEFINER commands."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_part_1: Mapped[str] = mapped_column(String(256), primary_key=True)
    key_part_2: Mapped[str] = mapped_column(String(256), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
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
    required_work_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_through_work_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    completed_work_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_input_identity_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    last_input_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dead_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_by: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON_VALUE)
    lease_owner_kind: Mapped[Optional[str]] = mapped_column(String(32))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128))
    lease_token: Mapped[Optional[str]] = mapped_column(String(160))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    compat_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'STANDBY'")
    )
    compat_required_work_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    compat_claimed_work_revision: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )
    compat_completed_work_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    compat_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    compat_last_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    compat_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    compat_claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    compat_claimed_by: Mapped[Optional[str]] = mapped_column(String(128))
    compat_row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )


class DtsDirtyKeyInputRecord(Base):
    """Append-only authoritative input assigned a dirty-local revision."""

    __tablename__ = "dts_dirty_key_inputs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_region", "key_type", "key_part_1", "key_part_2"],
            [
                "dts_dirty_keys.source_region",
                "dts_dirty_keys.key_type",
                "dts_dirty_keys.key_part_1",
                "dts_dirty_keys.key_part_2",
            ],
            name="fk_dts_dirty_key_input_key",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            "dirty_work_revision",
            name="uq_dts_dirty_key_input_work_revision",
        ),
        CheckConstraint(
            "input_kind IN ('SOURCE_REVISION','SCOPE_REVISION',"
            "'CATALOG_REVISION','DEPENDENCY_WAKE','OPERATOR_RECOVERY',"
            "'TIME_RECHECK')",
            name="ck_dts_dirty_key_input_kind",
        ),
        CheckConstraint(
            "input_identity_hash ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_revision >= 1 AND dirty_work_revision >= 1 "
            "AND jsonb_typeof(input_identity) = 'object'",
            name="ck_dts_dirty_key_input_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": "Append-only authoritative dirty inputs with "
            "dirty-local work revisions."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_part_1: Mapped[str] = mapped_column(String(256), primary_key=True)
    key_part_2: Mapped[str] = mapped_column(String(256), primary_key=True)
    input_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    input_identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    input_identity: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dirty_work_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsDirtyKeyLegacyArchiveV80Record(Base):
    """Immutable verbatim archive of drained pre-v2 queue rows."""

    __tablename__ = "dts_dirty_keys_legacy_archive_v80"
    __table_args__ = (
        CheckConstraint(
            "migration_id = '20260822_80_dts_v2_dirty_queue' "
            "AND jsonb_typeof(legacy_row) = 'object' "
            "AND legacy_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_dirty_keys_legacy_archive_v80_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": "Immutable verbatim archive of drained legacy dirty "
            "rows; never used as v2 business identity."
        },
    )

    legacy_key_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    legacy_key_part_1: Mapped[str] = mapped_column(String(256), primary_key=True)
    legacy_key_part_2: Mapped[str] = mapped_column(String(256), primary_key=True)
    migration_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legacy_row: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    legacy_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsDirtyKeyDependencyRecord(Base):
    """Typed reverse-indexed dependency for a waiting dirty key."""

    __tablename__ = "dts_dirty_key_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_region", "key_type", "key_part_1", "key_part_2"],
            [
                "dts_dirty_keys.source_region",
                "dts_dirty_keys.key_type",
                "dts_dirty_keys.key_part_1",
                "dts_dirty_keys.key_part_2",
            ],
            name="fk_dts_dirty_key_dependency_key",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_dts_dirty_key_dependencies_reverse",
            "dependency_type",
            "dependency_region",
            "dependency_key",
        ),
        CheckConstraint(
            "dependency_type ~ '^[A-Z][A-Z0-9_]{0,63}$' "
            "AND dependency_region IN ('dom','ovs') "
            "AND btrim(dependency_key) <> '' "
            "AND observed_source_revision >= 1 "
            "AND observed_dependency_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_dirty_key_dependency_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": "Typed WAITING_DEPENDENCY reverse index; never scan "
            "blocked_by JSON for wakeups."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_part_1: Mapped[str] = mapped_column(String(256), primary_key=True)
    key_part_2: Mapped[str] = mapped_column(String(256), primary_key=True)
    dependency_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    dependency_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    dependency_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    observed_source_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    observed_dependency_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsDirtyKeyStateAuditRecord(Base):
    """Append-only DEAD/reopen/recovery state transition audit."""

    __tablename__ = "dts_dirty_key_state_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_region", "key_type", "key_part_1", "key_part_2"],
            [
                "dts_dirty_keys.source_region",
                "dts_dirty_keys.key_type",
                "dts_dirty_keys.key_part_1",
                "dts_dirty_keys.key_part_2",
            ],
            name="fk_dts_dirty_key_state_audit_key",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "event_type IN ('DEAD','DEAD_REOPENED_BY_INPUT',"
            "'OPERATOR_RECOVERY') AND work_generation >= 1 "
            "AND dead_generation >= 0 "
            "AND jsonb_typeof(detail) = 'object'",
            name="ck_dts_dirty_key_state_audit_shape",
        ).ddl_if(dialect="postgresql"),
    )

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_part_1: Mapped[str] = mapped_column(String(256), nullable=False)
    key_part_2: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    work_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dead_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsTeacherTimeRecheckScheduleRecord(Base):
    """Singleton proof of the latest Beijing-date fan-out scan."""

    __tablename__ = "dts_teacher_time_recheck_schedule"
    __table_args__ = (
        CheckConstraint(
            "schedule_id='PRIMARY' AND row_version>=1 AND "
            "(last_enqueued_business_date IS NULL OR "
            "last_enqueued_generation>=1)",
            name="ck_dts_teacher_time_recheck_schedule_shape",
        ),
    )

    schedule_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_enqueued_business_date: Mapped[Optional[date]] = mapped_column(Date)
    last_enqueued_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_enqueued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DtsTeacherTimeRecheckResultRecord(Base):
    """Immutable per-teacher/per-Beijing-date materialization evidence."""

    __tablename__ = "dts_teacher_time_recheck_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["teacher_id"],
            ["teacher_source_wide.tchr_id"],
            name="fk_dts_teacher_time_recheck_result_teacher",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "teacher_id_type IN ('NUMERIC','TEXT') "
            "AND dom_aggregate_revision>=1 AND ovs_aggregate_revision>=1 "
            "AND projection_generation>=1 AND claimed_work_revision>=1 "
            "AND regional_state_sha256 ?& ARRAY['dom','ovs'] "
            "AND regional_state_sha256=jsonb_build_object("
            "'dom',regional_state_sha256->'dom',"
            "'ovs',regional_state_sha256->'ovs') "
            "AND regional_state_sha256->>'dom' ~ '^[0-9a-f]{64}$' "
            "AND regional_state_sha256->>'ovs' ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(time_values)='object' "
            "AND plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND btrim(triggering_event_id)<>''",
            name="ck_dts_teacher_time_recheck_result_shape",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_teacher_time_recheck_result_date",
            "business_date_beijing",
            "teacher_id",
        ),
    )

    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_date_beijing: Mapped[date] = mapped_column(Date, primary_key=True)
    teacher_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    dom_aggregate_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ovs_aggregate_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    regional_state_sha256: Mapped[dict[str, str]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    projection_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    triggering_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    claimed_work_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_values: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    materialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsTeacherTimeRecheckAuditRecord(Base):
    """Append-only evidence for obsolete clock work superseded by a new day."""

    __tablename__ = "dts_teacher_time_recheck_audits"
    __table_args__ = (
        CheckConstraint(
            "event_type='SUPERSEDED_BY_CURRENT_DATE' "
            "AND jsonb_typeof(detail)='object' "
            "AND detail_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dts_teacher_time_recheck_audit_shape",
        ).ddl_if(dialect="postgresql"),
    )

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date_beijing: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    detail_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DtsPipelineControlRecord(Base):
    """Protected singleton mode and immutable initial DTS v2 H0 vector."""

    __tablename__ = "dts_pipeline_control"
    __table_args__ = (
        CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_dts_pipeline_control_singleton",
        ),
        CheckConstraint(
            "mode IN ('V1_COMPAT_DUAL_CAPTURE', 'V2_PRIMARY', 'ROLLED_BACK')",
            name="ck_dts_pipeline_control_mode",
        ),
        CheckConstraint(
            "row_version >= 1 AND projection_generation >= 0",
            name="ck_dts_pipeline_control_versions",
        ),
        CheckConstraint(
            "jsonb_typeof(initial_h0_vector) = 'array' "
            "AND jsonb_array_length(initial_h0_vector) > 0",
            name="ck_dts_pipeline_control_initial_vector",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "initial_h0_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_pipeline_control_initial_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(last_handoff_vector IS NULL "
            "AND last_handoff_vector_hash IS NULL "
            "AND last_handoff_run_id IS NULL) "
            "OR (last_handoff_vector IS NOT NULL "
            "AND last_handoff_vector_hash IS NOT NULL "
            "AND last_handoff_run_id IS NOT NULL "
            "AND jsonb_typeof(last_handoff_vector) = 'array' "
            "AND last_handoff_vector_hash ~ '^[0-9a-f]{64}$' "
            "AND btrim(last_handoff_run_id) <> '')",
            name="ck_dts_pipeline_control_handoff_shape",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "time_catchup_status IN ('NOT_REQUIRED', 'PENDING', 'COMPLETE')",
            name="ck_dts_pipeline_control_catchup_status",
        ),
        {
            "comment": (
                "Protected singleton DTS mode and immutable initial H0 vector; "
                "created only by whole-vector bootstrap."
            )
        },
    )

    control_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    qualification_grants_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment=(
            "Database-authoritative gate for first irreversible graduation "
            "and gold grants"
        ),
    )
    consumer_group: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment=(
            "Bootstrap fleet identity; per-subscription consumer groups are "
            "stored in initial_h0_vector routes"
        ),
    )
    initial_h0_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    initial_h0_vector_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    initial_h0_bootstrap_run_id: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    last_handoff_vector: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSON_VALUE
    )
    last_handoff_vector_hash: Mapped[Optional[str]] = mapped_column(String(64))
    last_handoff_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    time_catchup_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'NOT_REQUIRED'")
    )
    time_catchup_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    time_catchup_from: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    time_catchup_through: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    time_catchup_expected_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    time_catchup_expected_hash: Mapped[Optional[str]] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsProjectionReadRouteRecord(Base):
    """Protected singleton selecting one stable teacher read projection."""

    __tablename__ = "dts_projection_read_routes"
    __table_args__ = (
        CheckConstraint(
            "route_id='PRIMARY'",
            name="ck_dts_projection_read_route_singleton",
        ),
        CheckConstraint(
            "active_projection IN ('V1_COMPAT','V2') AND row_version>=1",
            name="ck_dts_projection_read_route_state",
        ),
        CheckConstraint(
            "route_contract_version='teacher-read-route-v1'",
            name="ck_dts_projection_read_route_contract",
        ),
        CheckConstraint(
            "(row_version=1 AND active_projection='V1_COMPAT' "
            "AND switched_by_run_id IS NULL AND switched_at IS NULL) OR "
            "(row_version>1 AND switched_by_run_id IS NOT NULL "
            "AND switched_at IS NOT NULL)",
            name="ck_dts_projection_read_route_switch_shape",
        ),
        {
            "comment": (
                "Singleton stable read-route fact. Only protected CAS "
                "switch/rollback may mutate it."
            )
        },
    )

    route_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    active_projection: Mapped[str] = mapped_column(String(16), nullable=False)
    route_contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    switched_by_run_id: Mapped[Optional[str]] = mapped_column(String(128))
    switched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    switched_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsSourceProfileApprovalV2Record(Base):
    """Immutable reviewed source profile vector accepted for cutover."""

    __tablename__ = "dts_source_profile_approvals_v2"
    __table_args__ = (
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND profile_vector_hash=manifest_sha256 "
            "AND manifest_version=1 AND status='APPROVED' "
            "AND jsonb_typeof(profile_vector)='array' "
            "AND btrim(approved_by_change_id)<>'' "
            "AND btrim(approved_by)<>''",
            name="ck_dts_source_profile_approval_shape_v2",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable production source-profile approvals. This "
                "revision intentionally seeds no default approval."
            )
        },
    )

    manifest_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    profile_vector_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by_change_id: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsPipelineResetAuditRecord(Base):
    """One-shot destructive reset evidence for the only DTS pipeline."""

    __tablename__ = "dts_pipeline_reset_audits"
    __table_args__ = (
        CheckConstraint(
            "source_profile_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND history_policy='CLEAR_ALL_CONSUMED_HISTORY' "
            "AND event_scope='POST_CONFIGURED_START' "
            "AND btrim(reset_by)<>''",
            name="ck_dts_pipeline_reset_audit_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "One-shot destructive DTS reset evidence. Catalog/configuration "
                "and operator identity survive; consumed and dependent facts do not."
            )
        },
    )

    reset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_profile_manifest_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    history_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    event_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    reset_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsV2ReconciliationRunRecord(Base):
    """Immutable database-verified fourteen-result PASS evidence."""

    __tablename__ = "dts_v2_reconciliation_runs"
    __table_args__ = (
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND source_profile_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_profile_vector_hash ~ '^[0-9a-f]{64}$' "
            "AND source_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND full_reconciliation_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND v1_result_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND v2_result_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND legacy_output_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND technical_gate_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND base_control_version>=1 AND base_route_version>=1 "
            "AND target_projection_generation>=1 "
            "AND source_profile_manifest_version=1 "
            "AND base_mode IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK') "
            "AND result_status='PASS'",
            name="ck_dts_v2_reconciliation_run_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable database-verified fourteen-result "
                "reconciliation PASS evidence; operator-supplied hashes "
                "alone are never trusted."
            )
        },
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    base_control_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_route_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    source_profile_manifest_version: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    source_profile_manifest_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_profile_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_profile_vector_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_fence_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_fence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    full_reconciliation_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    full_reconciliation_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    v1_result_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    v1_result_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    v2_result_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    v2_result_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    legacy_output_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    legacy_output_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    technical_gate_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    technical_gate_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    evaluation_as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluation_business_date_beijing: Mapped[date] = mapped_column(
        Date, nullable=False
    )
    result_status: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsProjectionSwitchAuditRecord(Base):
    """Immutable idempotent cutover or rollback command result."""

    __tablename__ = "dts_projection_switch_audits"
    __table_args__ = (
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND source_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND response_hash ~ '^[0-9a-f]{64}$' "
            "AND projection_generation>=0 "
            "AND after_control_version=before_control_version+1 "
            "AND after_route_version=before_route_version+1 "
            "AND result_status='APPLIED' "
            "AND ((to_mode='V2_PRIMARY' AND to_projection='V2' "
            "AND reconciliation_run_id=switch_run_id) OR "
            "(to_mode='ROLLED_BACK' AND to_projection='V1_COMPAT' "
            "AND reconciliation_run_id IS NULL))",
            name="ck_dts_projection_switch_audit_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable idempotent cutover and rollback command audit."
            )
        },
    )

    switch_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciliation_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("dts_v2_reconciliation_runs.run_id", ondelete="RESTRICT")
    )
    from_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    to_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    from_projection: Mapped[str] = mapped_column(String(16), nullable=False)
    to_projection: Mapped[str] = mapped_column(String(16), nullable=False)
    projection_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_control_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    after_control_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_route_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    after_route_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_fence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    occurred_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsQualificationGateCommandRecord(Base):
    """Immutable CAS audit for the irreversible qualification grant gate."""

    __tablename__ = "dts_qualification_gate_commands"
    __table_args__ = (
        CheckConstraint(
            "expected_control_row_version >= 1 "
            "AND resulting_control_row_version >= "
            "expected_control_row_version "
            "AND nullif(btrim(reason), '') IS NOT NULL "
            "AND command_sha256 ~ '^[0-9a-f]{64}$' "
            "AND fanout_count >= 0 AND result_status = 'APPLIED'",
            name="ck_dts_qualification_gate_command_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable CAS commands for the database-authoritative "
                "irreversible qualification grant gate"
            )
        },
    )

    command_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expected_control_row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    resulting_control_row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    command_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fanout_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result_status: Mapped[str] = mapped_column(String(16), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    executed_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsPipelineBootstrapAuditRecord(Base):
    """Append-only proof of the sole successful initial H0 command."""

    __tablename__ = "dts_pipeline_bootstrap_audits"
    __table_args__ = (
        UniqueConstraint(
            "control_id",
            name="uq_dts_pipeline_bootstrap_audit_control",
        ),
        CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_dts_pipeline_bootstrap_audit_control",
        ),
        CheckConstraint(
            "jsonb_typeof(h0_vector) = 'array' "
            "AND jsonb_array_length(h0_vector) = route_count "
            "AND route_count > 0",
            name="ck_dts_pipeline_bootstrap_audit_vector",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "h0_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_pipeline_bootstrap_audit_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "result_status = 'APPLIED'",
            name="ck_dts_pipeline_bootstrap_audit_result",
        ),
        {
            "comment": (
                "Append-only initial broker epoch bootstrap audit; one "
                "successful PRIMARY H0 command."
            )
        },
    )

    bootstrap_run_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    control_id: Mapped[str] = mapped_column(
        ForeignKey("dts_pipeline_control.control_id", ondelete="RESTRICT"),
        nullable=False,
    )
    consumer_group: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Bootstrap fleet identity, not a route consumer group",
    )
    h0_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    h0_vector_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    route_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_status: Mapped[str] = mapped_column(String(16), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    executed_by: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsIngestIssueRecord(Base):
    """Sanitized fail-closed work item for an invalid broker delivery."""

    __tablename__ = "dts_ingest_issues"
    __table_args__ = (
        UniqueConstraint(
            "connector_delivery_identity_hash",
            "payload_hmac",
            "hmac_key_version",
            name="uq_dts_ingest_issue_delivery_payload",
        ),
        CheckConstraint(
            "ingest_issue_id ~ '^[0-9a-f]{64}$' "
            "AND connector_delivery_identity_hash ~ '^[0-9a-f]{64}$' "
            "AND payload_hmac ~ '^[0-9a-f]{64}$' "
            "AND btrim(hmac_key_version) <> ''",
            name="ck_dts_ingest_issue_identity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "ingest_issue_id = public.dts_ingest_issue_id_v2("
            "connector_delivery_identity_hash, payload_hmac, "
            "hmac_key_version)",
            name="ck_dts_ingest_issue_derived_id",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_region IS NULL OR source_region IN ('dom', 'ovs')",
            name="ck_dts_ingest_issue_region",
        ),
        CheckConstraint(
            "partition_id IS NULL OR partition_id >= 0",
            name="ck_dts_ingest_issue_partition",
        ),
        CheckConstraint(
            "offset_value IS NULL OR offset_value >= 0",
            name="ck_dts_ingest_issue_offset",
        ),
        CheckConstraint(
            "jsonb_typeof(current_error_codes) = 'array' "
            "AND jsonb_array_length(current_error_codes) > 0",
            name="ck_dts_ingest_issue_errors",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(diagnostic_summary) = 'object'",
            name="ck_dts_ingest_issue_diagnostic",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "issue_revision >= 1 AND attempt_count >= 1",
            name="ck_dts_ingest_issue_versions",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL) "
            "OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)",
            name="ck_dts_ingest_issue_status",
        ),
        Index(
            "ix_dts_ingest_issues_open_seen",
            "last_seen_at",
            "ingest_issue_id",
            postgresql_where=text("status = 'OPEN'"),
            sqlite_where=text("status = 'OPEN'"),
        ),
        {
            "comment": (
                "Sanitized invalid-delivery work items; raw payload, raw "
                "student identity, and HMAC keys are prohibited."
            )
        },
    )

    ingest_issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_delivery_identity_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    payload_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    hmac_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_region: Mapped[Optional[str]] = mapped_column(String(8))
    source_partition_epoch_id: Mapped[Optional[str]] = mapped_column(String(160))
    topic: Mapped[Optional[str]] = mapped_column(String(512))
    partition_id: Mapped[Optional[int]] = mapped_column(Integer)
    offset_value: Mapped[Optional[int]] = mapped_column(BigInteger)
    current_error_codes: Mapped[list[str]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    issue_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    case_id: Mapped[Optional[str]] = mapped_column(String(128))
    diagnostic_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )


class DtsSourcePartitionEpochRecord(Base):
    """v2 epoch registry with controlled whole-vector initial bootstrap."""

    __tablename__ = "dts_source_partition_epochs"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_dts_source_partition_epochs"),
        UniqueConstraint(
            "source_region",
            "source_partition_epoch_id",
            name="uq_dts_source_partition_epoch_identity",
        ),
        UniqueConstraint(
            "source_region",
            "topic",
            "partition_id",
            "epoch_sequence",
            name="uq_dts_source_partition_epoch_sequence",
        ),
        UniqueConstraint(
            "source_region",
            "epoch_kind",
            "snapshot_id",
            "source_table",
            name="uq_dts_source_partition_epoch_snapshot",
        ),
        ForeignKeyConstraint(
            ["source_region", "predecessor_epoch_id"],
            [
                "dts_source_partition_epochs.source_region",
                "dts_source_partition_epochs.source_partition_epoch_id",
            ],
            name="fk_dts_source_partition_epoch_predecessor",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_dts_source_partition_epoch_region",
        ),
        CheckConstraint(
            "partition_id >= 0",
            name="ck_dts_source_partition_epoch_partition",
        ),
        CheckConstraint(
            "epoch_kind IN ('BROKER', 'SNAPSHOT_BASELINE', 'SNAPSHOT_DIFF')",
            name="ck_dts_source_partition_epoch_kind",
        ),
        CheckConstraint(
            "status IN ('BARRIER_PENDING', 'ACTIVE', 'SUPERSEDED', 'SEALED')",
            name="ck_dts_source_partition_epoch_status",
        ),
        CheckConstraint(
            "(epoch_kind = 'BROKER' "
            "AND stream_generation_id IS NOT NULL "
            "AND epoch_opening_id IS NOT NULL "
            "AND epoch_sequence IS NOT NULL "
            "AND epoch_sequence >= 1 "
            "AND start_offset IS NOT NULL "
            "AND start_offset >= 0 "
            "AND v2_epoch_bootstrap_floor IS NOT NULL "
            "AND v2_epoch_bootstrap_floor >= 0 "
            "AND snapshot_id IS NULL "
            "AND source_table IS NULL "
            "AND status IN ('BARRIER_PENDING', 'ACTIVE', 'SUPERSEDED')) "
            "OR (epoch_kind IN ('SNAPSHOT_BASELINE', 'SNAPSHOT_DIFF') "
            "AND stream_generation_id IS NULL "
            "AND epoch_opening_id IS NULL "
            "AND epoch_sequence IS NULL "
            "AND predecessor_epoch_id IS NULL "
            "AND start_offset IS NULL "
            "AND v2_epoch_bootstrap_floor IS NULL "
            "AND activation_mode IS NULL "
            "AND activation_manifest_hash IS NULL "
            "AND snapshot_id IS NOT NULL "
            "AND source_table IS NOT NULL "
            "AND status = 'SEALED')",
            name="ck_dts_source_partition_epoch_shape",
        ),
        CheckConstraint(
            "activation_mode IS NULL "
            "OR activation_mode IN ('H0_BOOTSTRAP', 'SNAPSHOT_MANIFEST')",
            name="ck_dts_source_partition_epoch_activation_mode",
        ),
        CheckConstraint(
            "activation_manifest_hash IS NULL "
            "OR activation_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_partition_epoch_manifest_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "row_version >= 1",
            name="ck_dts_source_partition_epoch_row_version",
        ),
        Index(
            "uq_dts_source_partition_epoch_active_broker",
            "source_region",
            "topic",
            "partition_id",
            unique=True,
            postgresql_where=text("epoch_kind = 'BROKER' AND status = 'ACTIVE'"),
            sqlite_where=text("epoch_kind = 'BROKER' AND status = 'ACTIVE'"),
        ),
        {
            "comment": (
                "DTS v2 epoch registry; initial ACTIVE BROKER rows are created "
                "only by the whole-vector H0 bootstrap function."
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_partition_epoch_id: Mapped[str] = mapped_column(
        String(160), primary_key=True
    )
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epoch_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stream_generation_id: Mapped[Optional[str]] = mapped_column(Text)
    epoch_opening_id: Mapped[Optional[str]] = mapped_column(Text)
    epoch_sequence: Mapped[Optional[int]] = mapped_column(BigInteger)
    predecessor_epoch_id: Mapped[Optional[str]] = mapped_column(String(160))
    start_offset: Mapped[Optional[int]] = mapped_column(BigInteger)
    v2_epoch_bootstrap_floor: Mapped[Optional[int]] = mapped_column(BigInteger)
    activation_mode: Mapped[Optional[str]] = mapped_column(String(32))
    activation_manifest_hash: Mapped[Optional[str]] = mapped_column(String(64))
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_table: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )


class DtsSourceTablePublishGenerationRecord(Base):
    """Serial publication head shared by all scopes of one source table."""

    __tablename__ = "dts_source_table_publish_generations"
    __table_args__ = (
        PrimaryKeyConstraint(
            "source_region",
            "source_table",
            name="pk_dts_source_table_publish_generations",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_table",
                "current_generation",
                "current_snapshot_id",
            ],
            [
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.published_generation",
                "dts_source_scope_snapshots.snapshot_id",
            ],
            name="fk_dts_source_publish_head_current_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_region", "source_table", "active_candidate_snapshot_id"],
            [
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.snapshot_id",
            ],
            name="fk_dts_source_publish_head_candidate_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_table_allowed_v2(source_region,source_table)",
            name="ck_dts_source_publish_head_table",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "current_generation >= 0 AND row_version >= 1 "
            "AND ((current_generation = 0 AND current_snapshot_id IS NULL) "
            "OR (current_generation > 0 AND current_snapshot_id IS NOT NULL))",
            name="ck_dts_source_publish_head_generation",
        ),
        CheckConstraint(
            "(active_candidate_snapshot_id IS NULL "
            "AND candidate_owner IS NULL "
            "AND candidate_lease_token IS NULL "
            "AND candidate_lease_expires_at IS NULL) OR "
            "(active_candidate_snapshot_id IS NOT NULL "
            "AND candidate_owner IS NOT NULL "
            "AND btrim(candidate_owner) <> '' "
            "AND candidate_lease_token IS NOT NULL "
            "AND btrim(candidate_lease_token) <> '' "
            "AND candidate_lease_expires_at IS NOT NULL)",
            name="ck_dts_source_publish_head_candidate",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_dts_source_publish_head_lease_token",
            "candidate_lease_token",
            unique=True,
            postgresql_where=text("candidate_lease_token IS NOT NULL"),
            sqlite_where=text("candidate_lease_token IS NOT NULL"),
        ),
        {
            "comment": (
                "Per-table serial publication head for all GLOBAL and TEACHER "
                "source-scope candidates."
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    current_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    current_snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    active_candidate_snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    candidate_owner: Mapped[Optional[str]] = mapped_column(String(128))
    candidate_lease_token: Mapped[Optional[str]] = mapped_column(String(160))
    candidate_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DtsSourceScopeSnapshotRecord(Base):
    """Immutable source-scope snapshot and publication evidence."""

    __tablename__ = "dts_source_scope_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("snapshot_id", name="pk_dts_source_scope_snapshots"),
        UniqueConstraint(
            "snapshot_id",
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
            name="uq_dts_source_scope_snapshot_identity",
        ),
        UniqueConstraint(
            "source_region",
            "source_table",
            "snapshot_id",
            name="uq_dts_source_scope_snapshot_table_identity",
        ),
        UniqueConstraint(
            "source_region",
            "source_table",
            "published_generation",
            "snapshot_id",
            name="uq_dts_source_scope_snapshot_generation_identity",
        ),
        CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_snapshot_identity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "epoch_state IN ('LOADING','VERIFYING','COMPLETE','FAILED',"
            "'STALE','SUPERSEDED')",
            name="ck_dts_source_scope_snapshot_state",
        ),
        CheckConstraint(
            "snapshot_consistency_token_hash ~ '^[0-9a-f]{64}$' "
            "AND snapshot_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND begin_request_hash ~ '^[0-9a-f]{64}$' "
            "AND (verify_request_hash IS NULL OR verify_request_hash ~ "
            "'^[0-9a-f]{64}$') "
            "AND (publish_request_hash IS NULL OR publish_request_hash ~ "
            "'^[0-9a-f]{64}$') "
            "AND (terminal_request_hash IS NULL OR terminal_request_hash ~ "
            "'^[0-9a-f]{64}$')",
            name="ck_dts_source_scope_snapshot_hashes",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(snapshot_fence_vector) = 'array' "
            "AND jsonb_array_length(snapshot_fence_vector) > 0 "
            "AND partition_offsets = snapshot_fence_vector",
            name="ck_dts_source_scope_snapshot_fence",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(scope_kind = 'CURRENT' AND history_from IS NULL "
            "AND history_through IS NULL) OR "
            "(scope_kind = 'HISTORY' AND history_from IS NOT NULL "
            "AND history_through IS NOT NULL "
            "AND history_from <= history_through)",
            name="ck_dts_source_scope_snapshot_history",
        ),
        CheckConstraint(
            "base_publish_generation >= 0 AND row_version >= 1 "
            "AND (published_generation IS NULL OR published_generation >= 1) "
            "AND (generation_diff_count IS NULL OR generation_diff_count >= 0)",
            name="ck_dts_source_scope_snapshot_versions",
        ),
        CheckConstraint(
            "(epoch_state = 'LOADING' AND row_count IS NULL "
            "AND content_hash IS NULL AND verified_at IS NULL "
            "AND published_generation IS NULL AND completed_at IS NULL "
            "AND failed_at IS NULL AND error_code IS NULL) OR "
            "(epoch_state = 'VERIFYING' AND row_count >= 0 "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND verified_at IS NOT NULL "
            "AND published_generation IS NULL AND completed_at IS NULL "
            "AND failed_at IS NULL AND error_code IS NULL) OR "
            "(epoch_state = 'COMPLETE' AND row_count >= 0 "
            "AND content_hash ~ '^[0-9a-f]{64}$' "
            "AND verified_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND published_generation >= 1 "
            "AND generation_diff_count >= 0 "
            "AND generation_diff_hash ~ '^[0-9a-f]{64}$' "
            "AND error_code IS NULL) OR "
            "(epoch_state = 'FAILED' AND failed_at IS NOT NULL "
            "AND error_code IS NOT NULL AND btrim(error_code) <> '' "
            "AND published_generation IS NULL) OR "
            "(epoch_state = 'STALE' AND invalidated_at IS NOT NULL "
            "AND published_generation IS NOT NULL) OR "
            "(epoch_state = 'SUPERSEDED' AND invalidated_at IS NOT NULL "
            "AND published_generation IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_dts_source_scope_snapshot_lifecycle",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(source_schema_profile_id IS NULL "
            "AND source_field_types IS NULL AND profile_bound_at IS NULL) OR "
            "(source_schema_profile_id IS NOT NULL "
            "AND btrim(source_schema_profile_id) <> '' "
            "AND public.dts_v2_source_field_types_valid(source_field_types) "
            "IS TRUE AND source_field_types ? 'id' "
            "AND profile_bound_at IS NOT NULL)",
            name="ck_dts_source_scope_snapshot_profile_v3",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(published_through_offsets IS NULL "
            "AND published_through_hash IS NULL) OR "
            "(jsonb_typeof(published_through_offsets) = 'array' "
            "AND jsonb_array_length(published_through_offsets) > 0 "
            "AND published_through_hash ~ '^[0-9a-f]{64}$' "
            "AND published_through_hash = "
            "public.dts_canonical_json_sha256_v1("
            "published_through_offsets))",
            name="ck_dts_source_scope_snapshot_published_through_v3",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_dts_source_scope_snapshot_published_generation",
            "source_region",
            "source_table",
            "published_generation",
            unique=True,
            postgresql_where=text("published_generation IS NOT NULL"),
            sqlite_where=text("published_generation IS NOT NULL"),
        ),
        {
            "comment": (
                "Immutable source-scope snapshot epochs with relationally "
                "verified fence and publication evidence."
            )
        },
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    epoch_state: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_consistency_token: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_consistency_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_fence_vector: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    partition_offsets: Mapped[list[Any]] = mapped_column(JSON_VALUE, nullable=False)
    snapshot_fence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))
    history_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    history_through: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    base_publish_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    published_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    generation_diff_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    generation_diff_hash: Mapped[Optional[str]] = mapped_column(String(64))
    source_schema_profile_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_field_types: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    profile_bound_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    published_through_offsets: Mapped[Optional[list[Any]]] = mapped_column(
        JSON_VALUE
    )
    published_through_hash: Mapped[Optional[str]] = mapped_column(String(64))
    begin_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verify_request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    publish_request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    terminal_request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )


class DtsSourceSnapshotFenceRecord(Base):
    """Relational epoch-aware half-open fence for a source snapshot."""

    __tablename__ = "dts_source_snapshot_fences"
    __table_args__ = (
        PrimaryKeyConstraint(
            "snapshot_id",
            "source_partition_epoch_id",
            "topic",
            "partition_id",
            name="pk_dts_source_snapshot_fences",
        ),
        ForeignKeyConstraint(
            [
                "snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_snapshot_fence_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_partition_epoch_id",
                "topic",
                "partition_id",
            ],
            [
                "dts_source_partition_epochs.source_region",
                "dts_source_partition_epochs.source_partition_epoch_id",
                "dts_source_partition_epochs.topic",
                "dts_source_partition_epochs.partition_id",
            ],
            name="fk_dts_source_snapshot_fence_epoch",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "partition_id >= 0 AND start_next_offset >= 0 "
            "AND end_next_offset >= start_next_offset",
            name="ck_dts_source_snapshot_fence_offsets",
        ),
        {
            "comment": (
                "Typed per-epoch half-open broker next-offset fences for one "
                "source-scope snapshot."
            )
        },
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_partition_epoch_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_next_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_next_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DtsSourceSnapshotRowRecord(Base):
    """Candidate-only protected snapshot row with a frozen live base."""

    __tablename__ = "dts_source_snapshot_rows"
    __table_args__ = (
        PrimaryKeyConstraint(
            "snapshot_id",
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
            "source_key",
            name="pk_dts_source_snapshot_rows",
        ),
        ForeignKeyConstraint(
            [
                "snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_snapshot_row_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_snapshot_row_key",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,dependency_keys)",
            name="ck_dts_source_snapshot_row_dependencies",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(protected_source_row) = 'object' "
            "AND snapshot_row_hash ~ '^[0-9a-f]{64}$' "
            "AND snapshot_row_hash = "
            "public.dts_canonical_json_sha256_v1(protected_source_row) "
            "AND (source_region <> 'dom' OR "
            "public.dom_student_json_is_safe_v1(protected_source_row))",
            name="ck_dts_source_snapshot_row_payload",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(base_source_row_revision IS NULL OR "
            "base_source_row_revision >= 1) AND "
            "(base_row_hash IS NULL OR base_row_hash ~ '^[0-9a-f]{64}$')",
            name="ck_dts_source_snapshot_row_base",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Candidate-only protected staging rows with typed keys and "
                "frozen live-current base evidence."
            )
        },
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_key_data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    source_key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_key_text: Mapped[Optional[str]] = mapped_column(Text)
    dependency_keys: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    protected_source_row: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    snapshot_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_source_row_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    base_row_hash: Mapped[Optional[str]] = mapped_column(String(64))
    snapshot_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DtsSourceSnapshotDesiredRowRecord(Base):
    """Immutable final desired row after the snapshot CDC catch-up."""

    __tablename__ = "dts_source_snapshot_desired_rows"
    __table_args__ = (
        PrimaryKeyConstraint(
            "snapshot_id",
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
            "source_key",
            name="pk_dts_source_snapshot_desired_rows",
        ),
        ForeignKeyConstraint(
            [
                "snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_snapshot_desired_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_snapshot_desired_key",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,"
            "dependency_keys)",
            name="ck_dts_source_snapshot_desired_dependencies",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(protected_source_row) = 'object' "
            "AND desired_row_hash ~ '^[0-9a-f]{64}$' "
            "AND desired_row_hash = "
            "public.dts_canonical_json_sha256_v1(protected_source_row) "
            "AND (source_region <> 'dom' OR "
            "public.dom_student_json_is_safe_v1(protected_source_row))",
            name="ck_dts_source_snapshot_desired_payload",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable final desired set after candidate CDC catch-up; "
                "raw source-export rows remain separately preserved in "
                "dts_source_snapshot_rows."
            )
        },
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_key_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_key_text: Mapped[Optional[str]] = mapped_column(Text)
    dependency_keys: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    protected_source_row: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    desired_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class DtsSourceScopeStateRecord(Base):
    """Authoritative scope state; non-COMPLETE never proves absence."""

    __tablename__ = "dts_source_scope_states"
    __table_args__ = (
        PrimaryKeyConstraint(
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
            name="pk_dts_source_scope_states",
        ),
        ForeignKeyConstraint(
            [
                "active_snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_scope_state_active_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "candidate_snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_scope_state_candidate_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_state_identity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "state IN ('INCOMPLETE','LOADING','VERIFYING','COMPLETE',"
            "'FAILED','STALE') AND row_version >= 1",
            name="ck_dts_source_scope_state_value",
        ),
        CheckConstraint(
            "(state = 'INCOMPLETE' AND active_snapshot_id IS NULL "
            "AND candidate_snapshot_id IS NULL AND completed_at IS NULL) OR "
            "(state IN ('LOADING','VERIFYING') "
            "AND candidate_snapshot_id IS NOT NULL) OR "
            "(state = 'COMPLETE' AND active_snapshot_id IS NOT NULL "
            "AND candidate_snapshot_id IS NULL AND completed_at IS NOT NULL "
            "AND invalidated_at IS NULL) OR "
            "(state = 'FAILED' AND candidate_snapshot_id IS NOT NULL) OR "
            "(state = 'STALE' AND active_snapshot_id IS NOT NULL "
            "AND candidate_snapshot_id IS NULL "
            "AND invalidated_at IS NOT NULL)",
            name="ck_dts_source_scope_state_shape",
        ),
        CheckConstraint(
            "last_invalidation_hash IS NULL OR "
            "last_invalidation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_scope_state_invalidation_hash",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Authoritative scope completeness state; only COMPLETE plus a "
                "COMPLETE active snapshot proves empty/false/zero."
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    active_snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    candidate_snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_invalidation_hash: Mapped[Optional[str]] = mapped_column(String(64))
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DtsSourceScopeMembershipRecord(Base):
    """Published snapshot membership baseline plus nullable CDC overlay."""

    __tablename__ = "dts_source_scope_memberships"
    __table_args__ = (
        PrimaryKeyConstraint(
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
            "source_key",
            name="pk_dts_source_scope_memberships",
        ),
        ForeignKeyConstraint(
            [
                "active_snapshot_id",
                "source_region",
                "source_table",
                "scope_kind",
                "scope_level",
                "scope_key",
            ],
            [
                "dts_source_scope_snapshots.snapshot_id",
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.scope_kind",
                "dts_source_scope_snapshots.scope_level",
                "dts_source_scope_snapshots.scope_key",
            ],
            name="fk_dts_source_scope_membership_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_membership_identity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_scope_membership_key",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,dependency_keys)",
            name="ck_dts_source_scope_membership_dependencies",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "((snapshot_is_present AND snapshot_row_hash ~ '^[0-9a-f]{64}$') "
            "OR (NOT snapshot_is_present AND snapshot_row_hash IS NULL)) "
            "AND ((cdc_overlay_is_present IS NULL "
            "AND cdc_overlay_row_hash IS NULL "
            "AND last_cdc_source_revision IS NULL) OR "
            "(cdc_overlay_is_present IS TRUE "
            "AND cdc_overlay_row_hash ~ '^[0-9a-f]{64}$' "
            "AND last_cdc_source_revision >= 1) OR "
            "(cdc_overlay_is_present IS FALSE "
            "AND cdc_overlay_row_hash IS NULL "
            "AND last_cdc_source_revision >= 1)) "
            "AND membership_revision >= 1",
            name="ck_dts_source_scope_membership_evidence",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_source_scope_memberships_source_key",
            "source_region",
            "source_table",
            "source_key",
        ),
        {
            "comment": (
                "Published snapshot baseline plus nullable CDC overlay for one "
                "typed source key in a source scope."
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_key_data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    source_key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_key_text: Mapped[Optional[str]] = mapped_column(Text)
    active_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_is_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    snapshot_row_hash: Mapped[Optional[str]] = mapped_column(String(64))
    dependency_keys: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    cdc_overlay_is_present: Mapped[Optional[bool]] = mapped_column(Boolean)
    cdc_overlay_row_hash: Mapped[Optional[str]] = mapped_column(String(64))
    last_cdc_source_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    effective_is_present: Mapped[bool] = mapped_column(
        Boolean,
        Computed("coalesce(cdc_overlay_is_present,snapshot_is_present)", persisted=True),
        nullable=False,
    )
    membership_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DtsSourceScopeCommandRecord(Base):
    """Append-only response-lost-safe source-scope command ledger."""

    __tablename__ = "dts_source_scope_commands"
    __table_args__ = (
        PrimaryKeyConstraint("command_id", name="pk_dts_source_scope_commands"),
        CheckConstraint(
            "command_type IN ('BEGIN','HEARTBEAT','VERIFY','ABORT',"
            "'TAKEOVER','PUBLISH','INVALIDATE') "
            "AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(scope_identity) = 'object' "
            "AND jsonb_typeof(response_payload) = 'object'",
            name="ck_dts_source_scope_command_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Append-only command idempotency ledger for response-lost-safe "
                "source-scope state transitions."
            )
        },
    )

    command_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    command_type: Mapped[str] = mapped_column(String(24), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    scope_identity: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    executed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DtsSourceScopeTransitionAuditRecord(Base):
    """Append-only transition audit tied to one idempotent command."""

    __tablename__ = "dts_source_scope_transition_audits"
    __table_args__ = (
        PrimaryKeyConstraint(
            "audit_id", name="pk_dts_source_scope_transition_audits"
        ),
        UniqueConstraint(
            "command_id", name="uq_dts_source_scope_transition_command"
        ),
        ForeignKeyConstraint(
            ["command_id"],
            ["dts_source_scope_commands.command_id"],
            name="fk_dts_source_scope_transition_command",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key) "
            "AND to_state IN ('LOADING','VERIFYING','COMPLETE','FAILED','STALE') "
            "AND (from_state IS NULL OR from_state IN "
            "('INCOMPLETE','LOADING','VERIFYING','COMPLETE','FAILED','STALE')) "
            "AND scope_row_version >= 1 "
            "AND jsonb_typeof(detail) = 'object'",
            name="ck_dts_source_scope_transition_shape",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Append-only source-scope state transition audit tied to one "
                "idempotent command."
            )
        },
    )

    audit_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    command_id: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_region: Mapped[str] = mapped_column(String(8), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    from_state: Mapped[Optional[str]] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_row_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)


class DtsSourceRowVersionRecord(Base):
    """Append-only v2 source version shadow; no runtime writer is active."""

    __tablename__ = "dts_source_row_versions"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_dts_source_row_versions"),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_partition_epoch_id",
                "topic",
                "partition_id",
            ],
            [
                "dts_source_partition_epochs.source_region",
                "dts_source_partition_epochs.source_partition_epoch_id",
                "dts_source_partition_epochs.topic",
                "dts_source_partition_epochs.partition_id",
            ],
            name="fk_dts_source_row_version_epoch",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_table",
                "source_table_publish_generation",
                "snapshot_id",
            ],
            [
                "dts_source_scope_snapshots.source_region",
                "dts_source_scope_snapshots.source_table",
                "dts_source_scope_snapshots.published_generation",
                "dts_source_scope_snapshots.snapshot_id",
            ],
            name="fk_dts_source_row_version_snapshot_generation_v3",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_dts_source_row_version_region",
        ),
        CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0",
            name="ck_dts_source_row_version_offset",
        ),
        CheckConstraint(
            "version_kind IN ('CDC', 'BASELINE', 'SNAPSHOT_DIFF')",
            name="ck_dts_source_row_version_kind",
        ),
        CheckConstraint(
            "btrim(source_schema_profile_id) <> ''",
            name="ck_dts_source_row_version_schema_profile",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(source_field_types) = 'object'",
            name="ck_dts_source_row_version_field_types",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_v2_source_field_types_valid(source_field_types) "
            "IS TRUE AND (source_row_revision IS NULL OR ("
            "source_field_types ? 'id' AND source_field_types ->> 'id' "
            "IS NOT DISTINCT FROM source_key_type))",
            name="ck_dts_source_row_version_field_type_values",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_table NOT IN ('dom_appoint', 'ovs_appoint') "
            "OR source_key_type = 'NUMERIC'",
            name="ck_dts_source_row_version_profiled_key_type",
        ),
        CheckConstraint(
            "CASE "
            "WHEN jsonb_typeof(source_key_data) <> 'object' THEN false "
            "WHEN source_key_data <> "
            "jsonb_build_object('id', source_key_data -> 'id') THEN false "
            "WHEN source_key_type = 'NUMERIC' THEN "
            "CASE WHEN jsonb_typeof(source_key_data -> 'id') = 'number' THEN "
            "source_key_numeric IS NOT NULL "
            "AND source_key_text IS NULL "
            "AND source_key_numeric = (source_key_data ->> 'id')::numeric "
            "AND source_key = trim_scale(source_key_numeric)::text "
            "ELSE false END "
            "WHEN source_key_type = 'TEXT' THEN "
            "jsonb_typeof(source_key_data -> 'id') = 'string' "
            "AND source_key_numeric IS NULL "
            "AND source_key_text IS NOT NULL "
            "AND source_key_text <> '' "
            "AND source_key_text = source_key_data ->> 'id' "
            "AND source_key = source_key_text "
            "ELSE false END",
            name="ck_dts_source_row_version_source_key",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "record_id_type IN ('none', 'numeric', 'text') "
            "AND ((record_id_type = 'none' "
            "AND record_id_numeric IS NULL AND record_id_text IS NULL) "
            "OR (record_id_type = 'numeric' "
            "AND record_id_numeric IS NOT NULL AND record_id_text IS NULL) "
            "OR (record_id_type = 'text' "
            "AND record_id_numeric IS NULL AND record_id_text IS NOT NULL))",
            name="ck_dts_source_row_version_record_id",
        ),
        CheckConstraint(
            "jsonb_typeof(source_position) = 'object'",
            name="ck_dts_source_row_version_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "protected_source_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_row_version_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(version_kind = 'CDC' "
            "AND operation IN ('INSERT', 'UPDATE', 'DELETE', 'EPOCH_REPLAY') "
            "AND snapshot_id IS NULL "
            "AND ((operation = 'EPOCH_REPLAY' AND source_row_revision IS NULL) "
            "OR (operation <> 'EPOCH_REPLAY' "
            "AND source_row_revision IS NOT NULL "
            "AND source_row_revision >= 1))) "
            "OR (version_kind = 'BASELINE' "
            "AND operation = 'BASELINE' "
            "AND source_row_revision IS NULL "
            "AND snapshot_id IS NOT NULL) "
            "OR (version_kind = 'SNAPSHOT_DIFF' "
            "AND operation IN ('SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE', "
            "'SNAPSHOT_DELETE', 'SNAPSHOT_BOOTSTRAP_PRESENT', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE') "
            "AND source_row_revision IS NOT NULL "
            "AND source_row_revision >= 1 "
            "AND snapshot_id IS NOT NULL)",
            name="ck_dts_source_row_version_lifecycle",
        ),
        CheckConstraint(
            "(version_kind <> 'SNAPSHOT_DIFF' "
            "AND diff_step IS NULL "
            "AND source_table_publish_generation IS NULL) "
            "OR (version_kind = 'SNAPSHOT_DIFF' "
            "AND diff_step IS NOT NULL "
            "AND diff_step IN (1, 2) "
            "AND source_table_publish_generation IS NOT NULL "
            "AND source_table_publish_generation >= 1 "
            "AND ((diff_step = 1 AND mod(offset_value, 2) = 1) "
            "OR (diff_step = 2 "
            "AND mod(offset_value, 2) = 0 "
            "AND operation IN ('SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE', "
            "'SNAPSHOT_DELETE'))))",
            name="ck_dts_source_row_version_diff_step",
        ),
        Index(
            "uq_dts_source_row_version_snapshot_key",
            "source_region",
            "snapshot_id",
            "source_table",
            "source_key",
            unique=True,
            postgresql_where=text("version_kind = 'BASELINE'"),
            sqlite_where=text("version_kind = 'BASELINE'"),
        ),
        Index(
            "uq_dts_source_row_version_snapshot_diff_key_step",
            "source_region",
            "snapshot_id",
            "source_table",
            "source_key",
            "diff_step",
            unique=True,
            postgresql_where=text("version_kind = 'SNAPSHOT_DIFF'"),
            sqlite_where=text("version_kind = 'SNAPSHOT_DIFF'"),
        ),
        Index(
            "uq_dts_source_row_version_source_revision",
            "source_region",
            "source_table",
            "source_key",
            "source_row_revision",
            unique=True,
            postgresql_where=text("source_row_revision IS NOT NULL"),
            sqlite_where=text("source_row_revision IS NOT NULL"),
        ),
        Index(
            "ix_dts_source_row_versions_source_order",
            "source_region",
            "source_table",
            "source_key",
            "source_row_revision",
        ),
        Index(
            "ix_dts_source_row_versions_appoint_teacher_after_v2",
            "source_region",
            "source_table",
            text("(after_row->>'t_id')"),
            "source_row_revision",
            postgresql_where=text(
                "source_table IN ('dom_appoint','ovs_appoint') "
                "AND after_row ? 't_id'"
            ),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_source_row_versions_appoint_teacher_before_v2",
            "source_region",
            "source_table",
            text("(before_row->>'t_id')"),
            "source_row_revision",
            postgresql_where=text(
                "source_table IN ('dom_appoint','ovs_appoint') "
                "AND before_row ? 't_id'"
            ),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_source_row_versions_schedule_teacher_after_v2",
            text("(after_row->>'teacher_id')"),
            "source_row_revision",
            postgresql_where=text(
                "source_region='dom' "
                "AND source_table='dom_teacher_class_schedule' "
                "AND after_row ? 'teacher_id'"
            ),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_dts_source_row_versions_schedule_teacher_before_v2",
            text("(before_row->>'teacher_id')"),
            "source_row_revision",
            postgresql_where=text(
                "source_region='dom' "
                "AND source_table='dom_teacher_class_schedule' "
                "AND before_row ? 'teacher_id'"
            ),
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "DTS v2 shadow only; source schema profile and field types "
                "preserve type evidence; typed source key is distinct from "
                "record_id; no runtime writer granted"
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_partition_epoch_id: Mapped[str] = mapped_column(
        String(160), primary_key=True
    )
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_value: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    source_schema_profile_id: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    source_field_types: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_key_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_key_text: Mapped[Optional[str]] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    before_row: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    after_row: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_VALUE)
    source_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    record_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    record_id_text: Mapped[Optional[str]] = mapped_column(Text)
    source_position: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_row_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(160))
    snapshot_as_of: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    covered_through_offsets: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    diff_step: Mapped[Optional[int]] = mapped_column(Integer)
    source_table_publish_generation: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )
    protected_source_row_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class CompletionCorrectionPointerUpgradeArchiveRecord(Base):
    """Owner-only rollback evidence for rev90 compatibility pointers."""

    __tablename__ = "completion_correction_pointer_upgrade_archive"
    __table_args__ = (
        PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            name="pk_completion_correction_pointer_upgrade_archive",
        ),
        CheckConstraint(
            "source_region IN ('dom','ovs') AND prior_course_row_version>=1",
            name="ck_completion_correction_pointer_archive_shape",
        ),
        {
            "comment": (
                "Owner-only rollback evidence for compatibility pointers "
                "cleared when rev90 establishes mode-aware Case ownership."
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(
        String(512), primary_key=True
    )
    prior_case_id: Mapped[str] = mapped_column(String(768), nullable=False)
    prior_course_row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SourceCourseRecord(Base):
    """One source course in the inert v2 course/participation shadow."""

    __tablename__ = "source_courses"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_courses"),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "current_participation_seq",
                "current_teacher_id",
                "current_teacher_id_type",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
                "source_course_participations.teacher_id",
                "source_course_participations.teacher_id_type",
            ],
            name="fk_source_course_current_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["completion_conflict_case_id"],
            ["ops_cases.case_id"],
            name="fk_source_course_completion_conflict_case_v2",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
                "completion_teacher_id",
                "completion_teacher_id_type",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
                "source_course_participations.teacher_id",
                "source_course_participations.teacher_id_type",
            ],
            name="fk_source_course_completion_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_source_course_region",
        ),
        CheckConstraint(
            "current_participation_seq IS NULL OR current_participation_seq >= 1",
            name="ck_source_course_current_seq",
        ),
        CheckConstraint(
            "completion_participation_seq IS NULL "
            "OR completion_participation_seq >= 1",
            name="ck_source_course_completion_seq",
        ),
        CheckConstraint(
            "(current_teacher_id IS NULL "
            "AND current_teacher_id_type IS NULL "
            "AND current_participation_seq IS NULL) "
            "OR (current_teacher_id IS NOT NULL "
            "AND current_teacher_id_type IN ('NUMERIC', 'TEXT') "
            "AND current_participation_seq IS NOT NULL)",
            name="ck_source_course_current_pointer_pair",
        ),
        CheckConstraint(
            "(completion_teacher_id IS NULL "
            "AND completion_teacher_id_type IS NULL "
            "AND completion_participation_seq IS NULL "
            "AND completion_frozen_at IS NULL "
            "AND completion_source_position IS NULL "
            "AND completion_source_revision IS NULL) "
            "OR (completion_teacher_id IS NOT NULL "
            "AND completion_teacher_id_type IN ('NUMERIC', 'TEXT') "
            "AND completion_participation_seq IS NOT NULL "
            "AND completion_frozen_at IS NOT NULL "
            "AND completion_source_position IS NOT NULL "
            "AND completion_source_revision IS NOT NULL "
            "AND completion_source_revision >= 1)",
            name="ck_source_course_completion_pointer_group",
        ),
        CheckConstraint(
            "completion_conflict_status IN ("
            "'NONE', 'PENDING', 'RESOLVED_KEEP', 'RESOLVED_UPDATE', "
            "'RESOLVED_TRANSFER', 'RESOLVED_VOID')",
            name="ck_source_course_conflict_status",
        ),
        CheckConstraint(
            "evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING', 'SOURCE_CONFLICT')",
            name="ck_source_course_evidence_status",
        ),
        CheckConstraint(
            "appoint_evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING', 'SOURCE_CONFLICT') "
            "AND teacher_region_evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING', 'SOURCE_CONFLICT')",
            name="ck_source_course_region_evidence_status_v2",
        ),
        CheckConstraint(
            "evidence_status = CASE "
            "WHEN appoint_evidence_status='SOURCE_CONFLICT' "
            "OR teacher_region_evidence_status='SOURCE_CONFLICT' "
            "THEN 'SOURCE_CONFLICT' "
            "WHEN appoint_evidence_status='SOURCE_MISSING' "
            "OR teacher_region_evidence_status='SOURCE_MISSING' "
            "THEN 'SOURCE_MISSING' ELSE 'CONFIRMED' END",
            name="ck_source_course_combined_evidence_v2",
        ),
        CheckConstraint(
            "row_version >= 1 "
            "AND (completion_source_revision IS NULL "
            "OR completion_source_revision >= 1) "
            "AND (conflict_resolved_against_revision IS NULL "
            "OR conflict_resolved_against_revision >= 1) "
            "AND (last_applied_source_revision IS NULL "
            "OR last_applied_source_revision >= 1)",
            name="ck_source_course_revisions",
        ),
        CheckConstraint(
            "student_token IS NULL "
            "OR source_region = 'ovs' "
            "OR student_token ~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_source_course_dom_student_token",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "completion_student_token IS NULL "
            "OR source_region = 'ovs' "
            "OR completion_student_token ~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_source_course_dom_completion_student_token",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "completion_source_position IS NULL "
            "OR jsonb_typeof(completion_source_position) = 'object'",
            name="ck_source_course_completion_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "last_applied_event_position IS NULL "
            "OR jsonb_typeof(last_applied_event_position) = 'object'",
            name="ck_source_course_last_applied_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "conflict_resolved_against_position IS NULL "
            "OR jsonb_typeof(conflict_resolved_against_position) = 'object'",
            name="ck_source_course_resolved_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "initial_completion_snapshot IS NULL "
            "OR jsonb_typeof(initial_completion_snapshot) = 'object'",
            name="ck_source_course_initial_completion_snapshot",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "conflict_fingerprint IS NULL "
            "OR conflict_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_source_course_conflict_fingerprint",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_source_courses_favorite_completion_pair_v2",
            "source_region",
            "completion_teacher_id",
            "completion_student_token",
            "completion_end_time",
            "source_appoint_id",
            postgresql_where=text(
                "completion_participation_seq IS NOT NULL "
                "AND completion_end_time IS NOT NULL "
                "AND completion_voided_at IS NULL"
            ),
        ),
        {
            "comment": (
                "DTS v2 shadow only; rev68 enforces frozen initial snapshot, "
                "source history, and bidirectional participation pointers; "
                "no runtime writer or score-ownership route is active"
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    student_token: Mapped[Optional[str]] = mapped_column(String(128))
    lesson_local_date: Mapped[Optional[date]] = mapped_column(Date)
    lesson_local_time: Mapped[Optional[time]] = mapped_column(Time)
    scheduled_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    raw_end_time: Mapped[Optional[str]] = mapped_column(Text)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    source_status: Mapped[Optional[str]] = mapped_column(Text)
    current_teacher_id: Mapped[Optional[str]] = mapped_column(String(64))
    current_teacher_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    current_participation_seq: Mapped[Optional[int]] = mapped_column(Integer)
    is_peak: Mapped[Optional[bool]] = mapped_column(Boolean)
    completion_participation_seq: Mapped[Optional[int]] = mapped_column(Integer)
    completion_teacher_id: Mapped[Optional[str]] = mapped_column(String(64))
    completion_teacher_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    completion_frozen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    completion_end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    completion_student_token: Mapped[Optional[str]] = mapped_column(String(128))
    completion_is_peak: Mapped[Optional[bool]] = mapped_column(Boolean)
    completion_lesson_local_date: Mapped[Optional[date]] = mapped_column(Date)
    completion_lesson_local_time: Mapped[Optional[time]] = mapped_column(Time)
    completion_source_position: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    completion_source_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    initial_completion_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    completion_voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    completion_conflict_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'NONE'")
    )
    completion_conflict_case_id: Mapped[Optional[str]] = mapped_column(
        String(768),
        comment=(
            "Canonical visible completion-correction Case pointer; NULL "
            "during first compatibility shadow capture, preserved but never "
            "newly filled in ROLLED_BACK, command-owned in V2_PRIMARY."
        ),
    )
    conflict_resolved_against_position: Mapped[
        Optional[dict[str, Any]]
    ] = mapped_column(JSON_VALUE)
    conflict_resolved_against_revision: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )
    conflict_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    source_is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'SOURCE_MISSING'")
    )
    appoint_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'SOURCE_MISSING'")
    )
    teacher_region_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'SOURCE_MISSING'")
    )
    last_applied_event_position: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    last_applied_source_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class SourceCourseParticipationRecord(Base):
    """One teacher assignment phase in the inert v2 course shadow."""

    __tablename__ = "source_course_participations"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_course_participations"),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_source_course_participation_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "assignment_source_partition_epoch_id",
                "assignment_event_topic",
                "assignment_event_partition",
                "assignment_event_offset",
            ],
            [
                "dts_source_row_versions.source_region",
                "dts_source_row_versions.source_partition_epoch_id",
                "dts_source_row_versions.topic",
                "dts_source_row_versions.partition_id",
                "dts_source_row_versions.offset_value",
            ],
            name="fk_source_course_participation_source_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
            "teacher_id_type",
            name="uq_source_course_participation_teacher_identity",
        ),
        UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
            name="uq_source_course_participation_score_owner_v2",
        ),
        UniqueConstraint(
            "source_region",
            "assignment_source_partition_epoch_id",
            "assignment_event_topic",
            "assignment_event_partition",
            "assignment_event_offset",
            "assignment_event_phase",
            name="uq_source_course_participation_source_phase",
        ),
        UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "assignment_source_row_revision",
            "assignment_event_phase",
            name="uq_source_course_participation_revision_phase",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_source_course_participation_region",
        ),
        CheckConstraint(
            "teacher_id_type IN ('NUMERIC', 'TEXT')",
            name="ck_source_course_participation_teacher_type",
        ),
        CheckConstraint(
            "participation_seq >= 1 "
            "AND assignment_event_partition >= 0 "
            "AND assignment_event_offset >= 0 "
            "AND assignment_source_row_revision >= 1 "
            "AND row_version >= 1",
            name="ck_source_course_participation_numbers",
        ),
        CheckConstraint(
            "participation_role IN ("
            "'NORMAL', 'COMPLETION', 'PENDING_CORRECTION', "
            "'REJECTED_CORRECTION', 'SUPERSEDED_COMPLETION', "
            "'VOIDED_COMPLETION')",
            name="ck_source_course_participation_role",
        ),
        CheckConstraint(
            "assigned_at_evidence_status IN ('CONFIRMED', 'SOURCE_MISSING')",
            name="ck_source_course_participation_assigned_evidence",
        ),
        CheckConstraint(
            "assignment_event_phase IN ('SNAPSHOT_DIFF', 'BEFORE', 'AFTER')",
            name="ck_source_course_participation_event_phase",
        ),
        CheckConstraint(
            "NOT source_deleted OR NOT is_current",
            name="ck_source_course_participation_deleted_not_current",
        ),
        CheckConstraint(
            "(absence_source_id IS NULL "
            "AND absence_source_id_type IS NULL "
            "AND absence_source_row_revision IS NULL "
            "AND absence_selected_reason_type IS NULL "
            "AND absence_reason_detail IS NULL) OR "
            "(source_region='dom' "
            "AND absence_source_id IS NOT NULL "
            "AND btrim(absence_source_id)<>'' "
            "AND absence_source_id_type IN ('NUMERIC','TEXT') "
            "AND absence_source_row_revision>=1 "
            "AND absence_reason_detail IS NOT DISTINCT FROM "
            "absence_selected_reason_type)",
            name="ck_source_course_participation_absence_provenance_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "teacher_region_evidence_status IN ("
            "'CONFIRMED','SOURCE_MISSING','SOURCE_CONFLICT') AND ("
            "(teacher_region_evidence_status='SOURCE_MISSING' "
            "AND teacher_expected_source_region IS NULL "
            "AND ((teacher_profile_source_row_revision IS NULL "
            "AND teacher_profile_source_payload_hash IS NULL) OR "
            "(teacher_profile_source_row_revision>=1 "
            "AND teacher_profile_source_payload_hash "
            "~ '^[0-9a-f]{64}$'))) OR "
            "(teacher_region_evidence_status IN ("
            "'CONFIRMED','SOURCE_CONFLICT') "
            "AND teacher_expected_source_region IN ('dom','ovs') "
            "AND teacher_profile_source_row_revision>=1 "
            "AND teacher_profile_source_payload_hash "
            "~ '^[0-9a-f]{64}$'))",
            name="ck_source_course_participation_teacher_region_v2",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_source_course_participation_current",
            "source_region",
            "source_appoint_id",
            unique=True,
            postgresql_where=text("is_current IS TRUE"),
            sqlite_where=text("is_current IS TRUE"),
        ),
        Index(
            "uq_source_course_participation_completion",
            "source_region",
            "source_appoint_id",
            unique=True,
            postgresql_where=text("participation_role = 'COMPLETION'"),
            sqlite_where=text("participation_role = 'COMPLETION'"),
        ),
        Index(
            "ix_source_course_participation_teacher_time",
            "source_region",
            "teacher_id_type",
            "teacher_id",
            "source_appoint_id",
            "participation_seq",
        ),
        Index(
            "ix_source_course_participation_absence_source_v2",
            "absence_source_id_type",
            "absence_source_id",
            "absence_source_row_revision",
            postgresql_where=text("absence_source_id IS NOT NULL"),
        ),
        {
            "comment": (
                "DTS v2 shadow only; rev68 enforces appoint provenance, "
                "completion status, and reverse pointers; no runtime writer "
                "or correction command is active"
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    participation_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    participation_status: Mapped[Optional[str]] = mapped_column(Text)
    participation_role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    assigned_at_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    absence_reason_detail: Mapped[Optional[str]] = mapped_column(Text)
    no_notice: Mapped[Optional[bool]] = mapped_column(Boolean)
    absence_source_id: Mapped[Optional[str]] = mapped_column(String(512))
    absence_source_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    absence_source_row_revision: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )
    absence_selected_reason_type: Mapped[Optional[str]] = mapped_column(Text)
    teacher_expected_source_region: Mapped[Optional[str]] = mapped_column(
        String(8)
    )
    teacher_region_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'SOURCE_MISSING'")
    )
    teacher_profile_source_row_revision: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )
    teacher_profile_source_payload_hash: Mapped[Optional[str]] = mapped_column(
        String(64)
    )
    source_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assignment_source_partition_epoch_id: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    assignment_event_topic: Mapped[str] = mapped_column(String(512), nullable=False)
    assignment_event_partition: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_event_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignment_source_row_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    assignment_event_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )


class SourceCourseLabelRecord(Base):
    """Current/tombstone state of one typed grading-label log row."""

    __tablename__ = "source_course_labels"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_course_labels"),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_source_course_label_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_label_region",
        ),
        CheckConstraint(
            _dts_typed_id_check("source_log_id", nullable=False),
            name="ck_source_course_label_log_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("label_id", nullable=False),
            name="ck_source_course_label_id_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_row_revision >= 1",
            name="ck_source_course_label_revision",
        ),
        CheckConstraint(
            _dts_source_position_check("source_position"),
            name="ck_source_course_label_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(source_version) = 'object'",
            name="ck_source_course_label_source_version",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_evidence_check("evidence_status", "evidence_error_code"),
            name="ck_source_course_label_evidence",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_source_course_labels_course_label",
            "source_region",
            "source_appoint_id",
            "label_id_type",
            "label_id",
            "is_deleted",
        ),
        Index(
            "ix_source_course_labels_label_reverse",
            "source_region",
            "label_id_type",
            "label_id",
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_log_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_log_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_log_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_log_id_text: Mapped[Optional[str]] = mapped_column(Text)
    source_appoint_id: Mapped[str] = mapped_column(String(512), nullable=False)
    label_id: Mapped[str] = mapped_column(String(512), nullable=False)
    label_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    label_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    label_id_text: Mapped[Optional[str]] = mapped_column(Text)
    label_name_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    create_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    dt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    source_position: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_row_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'CONFIRMED'")
    )
    evidence_error_code: Mapped[Optional[str]] = mapped_column(String(160))
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source_version: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceCourseComplaintRecord(Base):
    """Typed current/tombstone for every source complaint row."""

    __tablename__ = "source_course_complaints"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_course_complaints"),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_source_course_complaint_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["complaint_rule_id", "source_sha256"],
            [
                "complaint_category_rules.rule_id",
                "complaint_category_rules.source_sha256",
            ],
            name="fk_source_course_complaint_rule_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_complaint_region",
        ),
        CheckConstraint(
            _dts_typed_id_check("source_complaint_id", nullable=False),
            name="ck_source_course_complaint_id_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("source_teacher_id", nullable=True),
            name="ck_source_course_complaint_teacher_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("complaint_type", nullable=True),
            name="ck_source_course_complaint_l1_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("complaint_type_child", nullable=True),
            name="ck_source_course_complaint_l2_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("complaint_type_grandson", nullable=True),
            name="ck_source_course_complaint_l3_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "((complaint_rule_id IS NULL AND source_sha256 IS NULL "
            "AND severity_rank IS NULL) OR "
            "(complaint_rule_id IS NOT NULL AND source_sha256 IS NOT NULL "
            "AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND severity_rank BETWEEN 0 AND 4 "
            "AND category_l3_normalized IS NOT NULL "
            "AND btrim(category_l3_normalized) <> ''))",
            name="ck_source_course_complaint_rule_group",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_evidence_check("evidence_status", "evidence_error_code"),
            name="ck_source_course_complaint_evidence",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_row_revision >= 1",
            name="ck_source_course_complaint_revision",
        ),
        CheckConstraint(
            _dts_source_position_check("source_position"),
            name="ck_source_course_complaint_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(source_version) = 'object'",
            name="ck_source_course_complaint_source_version",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_source_course_complaints_course_latest",
            "source_region",
            "source_appoint_id",
            "is_deleted",
            "is_valid",
            "add_time",
            "course_date",
            "source_complaint_id",
            "source_row_revision",
        ),
        Index(
            "ix_source_course_complaints_category_l1",
            "complaint_type_type",
            "complaint_type",
            "source_region",
            "source_appoint_id",
        ),
        Index(
            "ix_source_course_complaints_category_l2",
            "complaint_type_child_type",
            "complaint_type_child",
            "source_region",
            "source_appoint_id",
        ),
        Index(
            "ix_source_course_complaints_category_l3",
            "complaint_type_grandson_type",
            "complaint_type_grandson",
            "source_region",
            "source_appoint_id",
        ),
        Index(
            "ix_source_course_complaints_rule_category_reverse_v2",
            "category_l3_normalized",
            "source_region",
            "source_appoint_id",
            postgresql_where=text("NOT is_deleted"),
            sqlite_where=text("NOT is_deleted"),
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_complaint_id: Mapped[str] = mapped_column(
        String(512), primary_key=True
    )
    source_complaint_id_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )
    source_complaint_id_numeric: Mapped[Optional[Decimal]] = mapped_column(
        Numeric
    )
    source_complaint_id_text: Mapped[Optional[str]] = mapped_column(Text)
    source_appoint_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_teacher_id: Mapped[Optional[str]] = mapped_column(String(64))
    source_teacher_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    source_teacher_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_teacher_id_text: Mapped[Optional[str]] = mapped_column(Text)
    complaint_type: Mapped[Optional[str]] = mapped_column(String(512))
    complaint_type_type: Mapped[Optional[str]] = mapped_column(String(16))
    complaint_type_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    complaint_type_text: Mapped[Optional[str]] = mapped_column(Text)
    complaint_type_child: Mapped[Optional[str]] = mapped_column(String(512))
    complaint_type_child_type: Mapped[Optional[str]] = mapped_column(String(16))
    complaint_type_child_numeric: Mapped[Optional[Decimal]] = mapped_column(
        Numeric
    )
    complaint_type_child_text: Mapped[Optional[str]] = mapped_column(Text)
    complaint_type_grandson: Mapped[Optional[str]] = mapped_column(String(512))
    complaint_type_grandson_type: Mapped[Optional[str]] = mapped_column(
        String(16)
    )
    complaint_type_grandson_numeric: Mapped[Optional[Decimal]] = mapped_column(
        Numeric
    )
    complaint_type_grandson_text: Mapped[Optional[str]] = mapped_column(Text)
    approve: Mapped[Optional[str]] = mapped_column(Text)
    validity: Mapped[Optional[int]] = mapped_column(Integer)
    add_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    course_date: Mapped[Optional[date]] = mapped_column(Date)
    is_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    complaint_rule_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    severity_rank: Mapped[Optional[int]] = mapped_column(Integer)
    category_l1_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    category_l2_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    category_l3_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    category_l3_normalized: Mapped[Optional[str]] = mapped_column(Text)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_error_code: Mapped[Optional[str]] = mapped_column(String(160))
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source_version: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_position: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_row_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceCourseFactCurrentRecord(Base):
    """Typed course-level selector output; child collections stay separate."""

    __tablename__ = "source_course_fact_current"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_course_fact_current"),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_source_course_fact_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["source_region", "latest_valid_complaint_id"],
            [
                "source_course_complaints.source_region",
                "source_course_complaints.source_complaint_id",
            ],
            name="fk_source_course_fact_latest_complaint",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_fact_region",
        ),
        CheckConstraint(
            _dts_typed_id_check("current_grading_source_id", nullable=True),
            name="ck_source_course_fact_grading_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _dts_typed_id_check("latest_valid_complaint_id", nullable=True),
            name="ck_source_course_fact_complaint_typed",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "grading_classification IN ("
            "'POSITIVE','NEGATIVE','UNCLASSIFIED','SOURCE_MISSING') "
            "AND (grading_classification = 'NEGATIVE' "
            "OR negative_score IS NULL) "
            "AND (grading_classification <> 'SOURCE_MISSING' "
            "OR grading_evidence_status = 'SOURCE_MISSING')",
            name="ck_source_course_fact_grading_result",
        ),
        CheckConstraint(
            _dts_evidence_check("grading_evidence_status", "grading_error_code"),
            name="ck_source_course_fact_grading_evidence",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "latest_valid_complaint_id IS NULL "
            "OR has_valid_complaint IS TRUE",
            name="ck_source_course_fact_latest_valid",
        ),
        CheckConstraint(
            _dts_evidence_check("complaint_evidence_status", "complaint_error_code"),
            name="ck_source_course_fact_complaint_evidence",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "camera_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((camera_evidence_status = 'CONFIRMED' "
            "AND is_camera_off IS NOT NULL) "
            "OR (camera_evidence_status <> 'CONFIRMED' "
            "AND is_camera_off IS NULL))",
            name="ck_source_course_fact_camera_evidence",
        ),
        CheckConstraint(
            "is_cpu_usage_high IS NULL "
            "AND cpu_evidence_status = 'SOURCE_MISSING' "
            "AND is_network_delay_high IS NULL "
            "AND network_evidence_status = 'SOURCE_MISSING'",
            name="ck_source_course_fact_retired_qa_sources",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="ck_source_course_fact_row_version",
        ),
        CheckConstraint(
            "jsonb_typeof(source_version_vector) = 'object' "
            "AND source_version_hash ~ '^[0-9a-f]{64}$' "
            "AND source_version_hash = "
            "public.dts_canonical_json_sha256_v1(source_version_vector)",
            name="ck_source_course_fact_source_vector",
        ).ddl_if(dialect="postgresql"),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    current_grading_source_id: Mapped[Optional[str]] = mapped_column(String(512))
    current_grading_source_id_type: Mapped[Optional[str]] = mapped_column(
        String(16)
    )
    current_grading_source_id_numeric: Mapped[Optional[Decimal]] = mapped_column(
        Numeric
    )
    current_grading_source_id_text: Mapped[Optional[str]] = mapped_column(Text)
    grading_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    negative_score: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    grading_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    grading_error_code: Mapped[Optional[str]] = mapped_column(String(160))
    latest_valid_complaint_id: Mapped[Optional[str]] = mapped_column(String(512))
    latest_valid_complaint_id_type: Mapped[Optional[str]] = mapped_column(
        String(16)
    )
    latest_valid_complaint_id_numeric: Mapped[Optional[Decimal]] = mapped_column(
        Numeric
    )
    latest_valid_complaint_id_text: Mapped[Optional[str]] = mapped_column(Text)
    has_complaint: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_valid_complaint: Mapped[Optional[bool]] = mapped_column(Boolean)
    latest_category_l1_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    latest_category_l2_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    latest_category_l3_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    complaint_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    complaint_error_code: Mapped[Optional[str]] = mapped_column(String(160))
    is_camera_off: Mapped[Optional[bool]] = mapped_column(Boolean)
    camera_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    is_cpu_usage_high: Mapped[Optional[bool]] = mapped_column(Boolean)
    cpu_evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_network_delay_high: Mapped[Optional[bool]] = mapped_column(Boolean)
    network_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    source_version_vector: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceParticipationFactCurrentRecord(Base):
    """Typed late/early evidence for one teacher participation."""

    __tablename__ = "source_participation_fact_current"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_source_participation_fact_current"),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id", "participation_seq"],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
            ],
            name="fk_source_participation_fact_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom','ovs') AND participation_seq >= 1",
            name="ck_source_participation_fact_identity",
        ),
        CheckConstraint(
            "late_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((late_evidence_status = 'CONFIRMED' AND is_late IS NOT NULL) "
            "OR (late_evidence_status <> 'CONFIRMED' AND is_late IS NULL))",
            name="ck_source_participation_fact_late",
        ),
        CheckConstraint(
            "early_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((early_evidence_status = 'CONFIRMED' AND is_early IS NOT NULL) "
            "OR (early_evidence_status <> 'CONFIRMED' AND is_early IS NULL))",
            name="ck_source_participation_fact_early",
        ),
        CheckConstraint(
            "jsonb_typeof(penalty_source_keys) = 'array' "
            "AND penalty_source_keys_hash ~ '^[0-9a-f]{64}$' "
            "AND penalty_source_keys_hash = "
            "public.dts_canonical_json_sha256_v1(penalty_source_keys)",
            name="ck_source_participation_fact_penalty_keys",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(source_version_vector) = 'object' "
            "AND source_version_hash ~ '^[0-9a-f]{64}$' "
            "AND source_version_hash = "
            "public.dts_canonical_json_sha256_v1(source_version_vector)",
            name="ck_source_participation_fact_source_vector",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "row_version >= 1",
            name="ck_source_participation_fact_row_version",
        ),
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    participation_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_late: Mapped[Optional[bool]] = mapped_column(Boolean)
    late_evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_early: Mapped[Optional[bool]] = mapped_column(Boolean)
    early_evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    penalty_source_keys: Mapped[list[Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    penalty_source_keys_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_version_vector: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    source_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DomainAggregateRevisionRecord(Base):
    """Current semantic revision and canonical hash for one v2 aggregate."""

    __tablename__ = "domain_aggregate_revisions"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_domain_aggregate_revisions"),
        CheckConstraint(
            "aggregate_type IN ("
            "'COURSE','PARTICIPATION','TEACHER','TEACHER_STUDENT','LABEL',"
            "'COMPLAINT_CATEGORY','COMPLETION_CONFLICT','SOURCE_SCOPE',"
            "'TASK_PLAN')",
            name="ck_domain_aggregate_type",
        ),
        CheckConstraint(
            "public.dts_domain_aggregate_key_valid_v2("
            "aggregate_type,canonical_key) IS TRUE",
            name="ck_domain_aggregate_canonical_key",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "canonical_key_sha256 ~ '^[0-9a-f]{64}$' "
            "AND canonical_key_sha256 = "
            "public.dts_canonical_json_sha256_v1(canonical_key) "
            "AND aggregate_id = 'v2:' || aggregate_type || ':' || "
            "canonical_key_sha256",
            name="ck_domain_aggregate_id",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "revision >= 1 AND (last_source_row_revision IS NULL "
            "OR last_source_row_revision >= 1)",
            name="ck_domain_aggregate_revisions",
        ),
        CheckConstraint(
            _dts_source_position_check("last_source_position", nullable=True),
            name="ck_domain_aggregate_source_position",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(aggregate_state) = 'object' "
            "AND aggregate_state_sha256 ~ '^[0-9a-f]{64}$' "
            "AND aggregate_state_sha256 = "
            "public.dts_canonical_json_sha256_v1(aggregate_state)",
            name="ck_domain_aggregate_state_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "aggregate_type <> 'TEACHER_STUDENT' "
            "OR canonical_key->>'source_region' = 'ovs' "
            "OR canonical_key->>'student_token' "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_domain_aggregate_dom_student_token",
        ).ddl_if(dialect="postgresql"),
    )

    aggregate_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    canonical_key: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    canonical_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_source_row_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_source_position: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    aggregate_state: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    aggregate_state_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text(
            "'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'"
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TeacherStudentRelationshipEventRecord(Base):
    """Append-only typed relationship history; no runtime writer is active."""

    __tablename__ = "teacher_student_relationship_events"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_teacher_student_relationship_events"),
        UniqueConstraint(
            "event_sequence",
            name="uq_teacher_student_relationship_event_sequence",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_partition_epoch_id",
                "topic",
                "partition_id",
                "offset_value",
            ],
            [
                "dts_source_row_versions.source_region",
                "dts_source_row_versions.source_partition_epoch_id",
                "dts_source_row_versions.topic",
                "dts_source_row_versions.partition_id",
                "dts_source_row_versions.offset_value",
            ],
            name="fk_relationship_event_source_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_relationship_event_region",
        ),
        CheckConstraint(
            "partition_id >= 0 AND offset_value >= 0 "
            "AND source_row_revision >= 1",
            name="ck_relationship_event_source_numbers",
        ),
        CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "source_record_id_type, source_record_id) IS TRUE "
            "AND CASE source_record_id_type "
            "WHEN 'NUMERIC' THEN source_record_id_numeric IS NOT NULL "
            "AND source_record_id_text IS NULL "
            "AND source_record_id_numeric = source_record_id::numeric "
            "WHEN 'TEXT' THEN source_record_id_numeric IS NULL "
            "AND source_record_id_text = source_record_id "
            "ELSE false END",
            name="ck_relationship_event_typed_source_id",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "relationship_type IN ('FAVORITE', 'BLOCK') "
            "AND ((source_region = 'dom' AND source_table IN ("
            "'dom_teacher_favorite', 'dom_teacher_blacklist')) "
            "OR (source_region = 'ovs' AND source_table IN ("
            "'ovs_teacher_favorite', 'ovs_teacher_blacklist'))) "
            "AND ((relationship_type = 'FAVORITE' "
            "AND source_table LIKE '%_teacher_favorite') "
            "OR (relationship_type = 'BLOCK' "
            "AND source_table LIKE '%_teacher_blacklist'))",
            name="ck_relationship_event_route",
        ),
        CheckConstraint(
            "operation IN ("
            "'INSERT', 'UPDATE', 'DELETE', 'SNAPSHOT_INSERT', "
            "'SNAPSHOT_UPDATE', 'SNAPSHOT_DELETE', "
            "'SNAPSHOT_BOOTSTRAP_PRESENT', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE')",
            name="ck_relationship_event_operation",
        ),
        CheckConstraint(
            "((old_teacher_id IS NULL AND old_teacher_id_type IS NULL "
            "AND old_student_token IS NULL) "
            "OR (old_teacher_id IS NOT NULL "
            "AND public.dts_v2_typed_id_valid("
            "old_teacher_id_type, old_teacher_id) IS TRUE "
            "AND old_student_token IS NOT NULL "
            "AND old_student_token <> '')) "
            "AND ((new_teacher_id IS NULL AND new_teacher_id_type IS NULL "
            "AND new_student_token IS NULL) "
            "OR (new_teacher_id IS NOT NULL "
            "AND public.dts_v2_typed_id_valid("
            "new_teacher_id_type, new_teacher_id) IS TRUE "
            "AND new_student_token IS NOT NULL "
            "AND new_student_token <> ''))",
            name="ck_relationship_event_pair_shapes",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(operation IN ("
            "'INSERT', 'SNAPSHOT_INSERT', 'SNAPSHOT_BOOTSTRAP_PRESENT') "
            "AND old_teacher_id IS NULL AND new_teacher_id IS NOT NULL) "
            "OR (operation IN ('UPDATE', 'SNAPSHOT_UPDATE') "
            "AND old_teacher_id IS NOT NULL "
            "AND new_teacher_id IS NOT NULL) "
            "OR (operation IN ("
            "'DELETE', 'SNAPSHOT_DELETE', "
            "'SNAPSHOT_BOOTSTRAP_TOMBSTONE') "
            "AND old_teacher_id IS NOT NULL AND new_teacher_id IS NULL)",
            name="ck_relationship_event_crud_images",
        ),
        CheckConstraint(
            "source_region = 'ovs' OR ("
            "(old_student_token IS NULL OR old_student_token "
            "~ '^dom:v1:[0-9a-f]{64}$') "
            "AND (new_student_token IS NULL OR new_student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'))",
            name="ck_relationship_event_dom_student_tokens",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(old_valid_start_at IS NULL OR old_valid_end_at IS NULL "
            "OR old_valid_end_at >= old_valid_start_at) "
            "AND (new_valid_start_at IS NULL OR new_valid_end_at IS NULL "
            "OR new_valid_end_at >= new_valid_start_at)",
            name="ck_relationship_event_valid_intervals",
        ),
        CheckConstraint(
            "(effective_time_evidence_status = 'CONFIRMED' "
            "AND effective_at IS NOT NULL) "
            "OR (effective_time_evidence_status = 'SOURCE_MISSING' "
            "AND effective_at IS NULL)",
            name="ck_relationship_event_effective_evidence",
        ),
        Index(
            "ix_relationship_events_pair_sequence",
            "source_region",
            "event_sequence",
        ),
        Index(
            "ix_relationship_events_business_time",
            "source_region",
            "relationship_type",
            "effective_at",
        ),
        Index(
            "ix_relationship_events_favorite_old_pair_v2",
            "source_region",
            "old_teacher_id",
            "old_student_token",
            "source_table",
            "source_record_id_type",
            "source_record_id",
            "source_row_revision",
            "event_sequence",
            postgresql_where=text(
                "relationship_type='FAVORITE' AND old_teacher_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_relationship_events_favorite_new_pair_v2",
            "source_region",
            "new_teacher_id",
            "new_student_token",
            "source_table",
            "source_record_id_type",
            "source_record_id",
            "source_row_revision",
            "event_sequence",
            postgresql_where=text(
                "relationship_type='FAVORITE' AND new_teacher_id IS NOT NULL"
            ),
        ),
        {
            "comment": "DTS v2 append-only typed relationship history; "
            "domain-projector command path owns writes."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_partition_epoch_id: Mapped[str] = mapped_column(
        String(160), primary_key=True
    )
    topic: Mapped[str] = mapped_column(String(512), primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_value: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(start=1), nullable=False
    )
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_record_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_record_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_record_id_text: Mapped[Optional[str]] = mapped_column(Text)
    source_row_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    old_teacher_id: Mapped[Optional[str]] = mapped_column(String(64))
    old_teacher_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    new_teacher_id: Mapped[Optional[str]] = mapped_column(String(64))
    new_teacher_id_type: Mapped[Optional[str]] = mapped_column(String(16))
    old_student_token: Mapped[Optional[str]] = mapped_column(String(128))
    new_student_token: Mapped[Optional[str]] = mapped_column(String(128))
    old_valid_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    old_valid_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    new_valid_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    new_valid_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    old_is_valid_forever: Mapped[Optional[bool]] = mapped_column(Boolean)
    new_is_valid_forever: Mapped[Optional[bool]] = mapped_column(Boolean)
    effective_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    effective_time_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    source_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class TeacherStudentRelationshipCurrentRecord(Base):
    """Course-independent current relationship projection."""

    __tablename__ = "teacher_student_relationship_current"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_teacher_student_relationship_current"),
        ForeignKeyConstraint(
            [
                "source_region",
                "last_source_partition_epoch_id",
                "last_topic",
                "last_partition_id",
                "last_offset_value",
            ],
            [
                "teacher_student_relationship_events.source_region",
                "teacher_student_relationship_events.source_partition_epoch_id",
                "teacher_student_relationship_events.topic",
                "teacher_student_relationship_events.partition_id",
                "teacher_student_relationship_events.offset_value",
            ],
            name="fk_relationship_current_latest_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_relationship_current_region",
        ),
        CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_relationship_current_typed_teacher",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_relationship_current_dom_student_token",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "student_token <> '' AND last_event_sequence >= 1 "
            "AND last_partition_id >= 0 AND last_offset_value >= 0 "
            "AND last_source_row_revision >= 1 AND row_version >= 1",
            name="ck_relationship_current_numbers_and_identity",
        ),
        CheckConstraint(
            "effective_time_evidence_status IN ("
            "'CONFIRMED', 'SOURCE_MISSING')",
            name="ck_relationship_current_evidence",
        ),
        Index(
            "ix_relationship_current_teacher_favorite",
            "source_region",
            "teacher_id",
            "student_token",
            postgresql_where=text("is_favorited IS TRUE"),
            sqlite_where=text("is_favorited IS TRUE"),
        ),
        Index(
            "ix_relationship_current_teacher_block",
            "source_region",
            "teacher_id",
            "student_token",
            postgresql_where=text("is_blocked IS TRUE"),
            sqlite_where=text("is_blocked IS TRUE"),
        ),
        {
            "comment": "DTS v2 course-independent relationship current "
            "state; no course-end prerequisite."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    teacher_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    student_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_favorited: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_blocked: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_business_effective_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    effective_time_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    last_event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_source_partition_epoch_id: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    last_topic: Mapped[str] = mapped_column(String(512), nullable=False)
    last_partition_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_offset_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_source_row_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CourseFavoriteObservationRecord(Base):
    """One end+24h observation revision for a frozen completion."""

    __tablename__ = "course_favorite_observations"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_course_favorite_observations"),
        UniqueConstraint(
            "source_region",
            "source_appoint_id",
            "observation_revision",
            "teacher_id",
            "teacher_id_type",
            "student_token",
            "completion_participation_seq",
            name="uq_favorite_observation_attribution_identity",
        ),
        UniqueConstraint(
            "lease_token",
            name="uq_favorite_observation_lease_token",
        ),
        ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_favorite_observation_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
                "teacher_id",
                "teacher_id_type",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
                "source_course_participations.teacher_id",
                "source_course_participations.teacher_id_type",
            ],
            name="fk_favorite_observation_completion_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_favorite_observation_region",
        ),
        CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_favorite_observation_typed_teacher",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_favorite_observation_dom_student_token",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "student_token <> '' AND observation_revision >= 1 "
            "AND completion_participation_seq >= 1 "
            "AND required_evidence_revision >= 1 "
            "AND completed_evidence_revision >= 0 "
            "AND completed_evidence_revision <= required_evidence_revision "
            "AND (claimed_evidence_revision IS NULL "
            "OR (claimed_evidence_revision >= 1 "
            "AND claimed_evidence_revision <= required_evidence_revision)) "
            "AND attempt_count BETWEEN 0 AND 8 "
            "AND dead_generation >= 0 AND row_version >= 1 "
            "AND created_projection_generation >= 0 "
            "AND serving_projection_generation >= 0",
            name="ck_favorite_observation_numbers",
        ),
        CheckConstraint(
            "required_evidence_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND trim(rule_version) <> ''",
            name="ck_favorite_observation_version_evidence",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "appoint_id_type, source_appoint_id) IS TRUE "
            "AND CASE appoint_id_type "
            "WHEN 'NUMERIC' THEN appoint_id_numeric IS NOT NULL "
            "AND appoint_id_numeric = source_appoint_id::numeric "
            "AND appoint_id_text_sort IS NULL "
            "WHEN 'TEXT' THEN appoint_id_numeric IS NULL "
            "AND appoint_id_text_sort = convert_to("
            "source_appoint_id, 'UTF8') "
            "ELSE false END",
            name="ck_favorite_observation_typed_appoint",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "status IN ('PENDING', 'EVALUATING', 'CONFIRMED_TRUE', "
            "'CONFIRMED_FALSE', 'WAITING_HISTORY', 'WAITING_EVIDENCE', "
            "'RETRY', 'DEAD', 'INVALIDATED', 'VOIDED')",
            name="ck_favorite_observation_status",
        ),
        CheckConstraint(
            "relation_evidence_status IN ("
            "'PENDING', 'CONFIRMED', 'HISTORY_INCOMPLETE', "
            "'SOURCE_MISSING')",
            name="ck_favorite_observation_evidence_status",
        ),
        CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL) "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_favorite_observation_origin",
        ),
        CheckConstraint(
            "((status = 'PENDING' AND attempt_count = 0 "
            "AND next_attempt_at IS NOT NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NULL) "
            "OR (status = 'RETRY' AND attempt_count BETWEEN 1 AND 7 "
            "AND next_attempt_at IS NOT NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NOT NULL) "
            "OR (status = 'EVALUATING' AND attempt_count BETWEEN 1 AND 8 "
            "AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NOT NULL "
            "AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) "
            "OR (status = 'DEAD' AND attempt_count = 8 "
            "AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL "
            "AND last_error IS NOT NULL AND dead_generation >= 1 "
            "AND technical_case_id IS NOT NULL) "
            "OR (status IN ('CONFIRMED_TRUE', 'CONFIRMED_FALSE', "
            "'WAITING_HISTORY', 'WAITING_EVIDENCE', "
            "'INVALIDATED', 'VOIDED') AND next_attempt_at IS NULL "
            "AND claimed_evidence_revision IS NULL "
            "AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL))",
            name="ck_favorite_observation_worker_shape",
        ),
        CheckConstraint(
            "(status = 'CONFIRMED_TRUE' "
            "AND relation_state IS TRUE "
            "AND relation_evidence_status = 'CONFIRMED' "
            "AND relation_error_code IS NULL "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'CONFIRMED_FALSE' "
            "AND relation_state IS FALSE "
            "AND relation_evidence_status = 'CONFIRMED' "
            "AND relation_error_code IS NULL "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'WAITING_HISTORY' "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'HISTORY_INCOMPLETE' "
            "AND relation_error_code = "
            "'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE' "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status = 'WAITING_EVIDENCE' "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'SOURCE_MISSING' "
            "AND relation_error_code IN ("
            "'SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING',"
            "'SOURCE_CONFLICT:COURSE_COMPLETION_PENDING') "
            "AND completed_evidence_revision = required_evidence_revision) "
            "OR (status IN ('PENDING', 'EVALUATING', 'RETRY', 'DEAD') "
            "AND relation_state IS NULL "
            "AND relation_evidence_status = 'PENDING' "
            "AND relation_error_code IS NULL) "
            "OR status IN ('INVALIDATED', 'VOIDED')",
            name="ck_favorite_observation_evidence_shape",
        ),
        CheckConstraint(
            "((status IN ('INVALIDATED', 'VOIDED') "
            "AND terminal_reason IS NOT NULL AND terminal_at IS NOT NULL "
            "AND is_serving IS FALSE) "
            "OR (status NOT IN ('INVALIDATED', 'VOIDED') "
            "AND terminal_reason IS NULL AND terminal_at IS NULL))",
            name="ck_favorite_observation_terminal_shape",
        ),
        Index(
            "uq_favorite_observation_current",
            "source_region",
            "source_appoint_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('INVALIDATED', 'VOIDED')"
            ),
            sqlite_where=text("status NOT IN ('INVALIDATED', 'VOIDED')"),
        ),
        Index(
            "ix_favorite_observation_due",
            "next_attempt_at",
            "observed_at",
            "source_region",
            postgresql_where=text("status IN ('PENDING', 'RETRY')"),
            sqlite_where=text("status IN ('PENDING', 'RETRY')"),
        ),
        Index(
            "ix_favorite_observation_expired_lease",
            "lease_expires_at",
            "source_region",
            postgresql_where=text("status = 'EVALUATING'"),
            sqlite_where=text("status = 'EVALUATING'"),
        ),
        Index(
            "ix_favorite_observation_candidate_numeric",
            "source_region",
            "teacher_id",
            "student_token",
            "observed_at",
            "appoint_id_numeric",
            "observation_revision",
            postgresql_where=text(
                "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'NUMERIC'"
            ),
            sqlite_where=text(
                "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'NUMERIC'"
            ),
        ),
        Index(
            "ix_favorite_observation_candidate_text",
            "source_region",
            "teacher_id",
            "student_token",
            "observed_at",
            "appoint_id_text_sort",
            "observation_revision",
            postgresql_where=text(
                "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'TEXT'"
            ),
            sqlite_where=text(
                "status = 'CONFIRMED_TRUE' AND appoint_id_type = 'TEXT'"
            ),
        ),
        Index(
            "ix_favorite_observation_pair_state_v2",
            "source_region",
            "teacher_id",
            "student_token",
            "status",
            "observed_at",
            "source_appoint_id",
            "observation_revision",
            postgresql_where=text(
                "status NOT IN ('INVALIDATED','VOIDED')"
            ),
        ),
        {
            "comment": "DTS v2 durable end_time plus 24-hour favorite "
            "observation work; database time owns due and lease decisions."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    observation_revision: Mapped[int] = mapped_column(
        BigInteger, primary_key=True
    )
    appoint_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    appoint_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    appoint_id_text_sort: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    student_token: Mapped[str] = mapped_column(String(128), nullable=False)
    completion_participation_seq: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    relation_state: Mapped[Optional[bool]] = mapped_column(Boolean)
    relation_evidence_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    relation_error_code: Mapped[Optional[str]] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    required_evidence_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    claimed_evidence_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    completed_evidence_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    required_evidence_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    lease_owner: Mapped[Optional[str]] = mapped_column(String(160))
    lease_token: Mapped[Optional[str]] = mapped_column(String(160))
    lease_acquired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    dead_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    technical_case_id: Mapped[Optional[str]] = mapped_column(String(160))
    terminal_reason: Mapped[Optional[str]] = mapped_column(Text)
    terminal_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    rule_version: Mapped[str] = mapped_column(String(128), nullable=False)
    materialization_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    materialized_by_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    created_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    serving_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    is_serving: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CourseFavoriteAttributionRecord(Base):
    """One retained lifetime award identity for a regional teacher/student."""

    __tablename__ = "course_favorite_attributions"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_course_favorite_attributions"),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "observation_revision",
                "teacher_id",
                "teacher_id_type",
                "student_token",
                "completion_participation_seq",
            ],
            [
                "course_favorite_observations.source_region",
                "course_favorite_observations.source_appoint_id",
                "course_favorite_observations.observation_revision",
                "course_favorite_observations.teacher_id",
                "course_favorite_observations.teacher_id_type",
                "course_favorite_observations.student_token",
                "course_favorite_observations.completion_participation_seq",
            ],
            name="fk_favorite_attribution_observation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
                "teacher_id",
                "teacher_id_type",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
                "source_course_participations.teacher_id",
                "source_course_participations.teacher_id_type",
            ],
            name="fk_favorite_attribution_completion_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["current_score_entry_id"],
            ["score_entries.score_entry_id"],
            name="fk_favorite_attribution_current_score_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_reversal_score_entry_id"],
            ["score_entries.score_entry_id"],
            name="fk_favorite_attribution_reversal_score_entry",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_favorite_attribution_region",
        ),
        CheckConstraint(
            "public.dts_v2_typed_id_valid("
            "teacher_id_type, teacher_id) IS TRUE",
            name="ck_favorite_attribution_typed_teacher",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "source_region = 'ovs' OR student_token "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_favorite_attribution_dom_student_token",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "student_token <> '' AND observation_revision >= 1 "
            "AND completion_participation_seq >= 1 "
            "AND award_generation >= 1 AND points > 0 "
            "AND award_projection_generation >= 0 AND row_version >= 1 "
            "AND trim(rule_version) <> '' "
            "AND trim(recompute_reason) <> '' "
            "AND (last_reversal_score_entry_id IS NULL "
            "OR last_reversal_score_entry_id <> current_score_entry_id)",
            name="ck_favorite_attribution_values",
        ),
        CheckConstraint(
            "(status = 'AWARDED' AND hold_reason IS NULL "
            "AND reversed_at IS NULL) "
            "OR (status = 'AWARDED_PENDING_EVIDENCE' "
            "AND hold_reason IN ("
            "'REVALIDATION_PENDING', 'WAITING_HISTORY', "
            "'WAITING_EVIDENCE') AND reversed_at IS NULL) "
            "OR (status = 'REVERSED' AND hold_reason IS NULL "
            "AND last_reversal_score_entry_id IS NOT NULL "
            "AND reversed_at IS NOT NULL)",
            name="ck_favorite_attribution_status_shape",
        ),
        CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL) "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_favorite_attribution_origin",
        ),
        Index(
            "uq_favorite_attribution_current_course",
            "source_region",
            "source_appoint_id",
            unique=True,
            postgresql_where=text(
                "status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')"
            ),
            sqlite_where=text(
                "status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')"
            ),
        ),
        {
            "comment": "DTS v2 retained one-course favorite attribution per "
            "regional teacher/student pair; reversals are append-only score "
            "entries."
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    teacher_id_type: Mapped[str] = mapped_column(String(16), nullable=False)
    student_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), nullable=False)
    observation_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completion_participation_seq: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    hold_reason: Mapped[Optional[str]] = mapped_column(String(40))
    points: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(128), nullable=False)
    award_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_score_entry_id: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    last_reversal_score_entry_id: Mapped[Optional[str]] = mapped_column(
        String(128)
    )
    recompute_reason: Mapped[str] = mapped_column(Text, nullable=False)
    materialization_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    materialized_by_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    award_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reversed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class LessonScoreComponentSettlementRecord(Base):
    """One inert v2 ledger owner frozen during pending completion review."""

    __tablename__ = "lesson_score_component_settlements"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_lesson_score_component_settlements"),
        ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
            ],
            name="fk_lesson_component_settlement_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["current_award_score_entry_id"],
            ["score_entries.score_entry_id"],
            name="fk_lesson_component_current_award_entry",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["last_reversal_score_entry_id"],
            ["score_entries.score_entry_id"],
            name="fk_lesson_component_last_reversal_entry",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_lesson_component_region",
        ),
        CheckConstraint(
            "component_code IN ("
            "'FEEDBACK_PRAISE', 'PERFECT_COMPLETED', "
            "'PEAK_COMPLETED', 'CLASS_QUALITY_HARDWARE')",
            name="ck_lesson_component_code",
        ),
        CheckConstraint(
            "completion_participation_seq >= 1 "
            "AND award_generation >= 1 "
            "AND component_score > 0 "
            "AND award_projection_generation >= 0 "
            "AND row_version >= 1 "
            "AND btrim(teacher_id) <> '' "
            "AND btrim(score_rule_version) <> '' "
            "AND evidence_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND (current_award_score_entry_id IS NULL "
            "OR last_reversal_score_entry_id IS NULL "
            "OR current_award_score_entry_id <> "
            "last_reversal_score_entry_id)",
            name="ck_lesson_component_values",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(status = 'AWARDED' "
            "AND current_award_score_entry_id IS NOT NULL "
            "AND reversed_at IS NULL "
            "AND ((award_generation = 1 "
            "AND last_reversal_score_entry_id IS NULL) "
            "OR (award_generation > 1 "
            "AND last_reversal_score_entry_id IS NOT NULL))) "
            "OR (status = 'REVERSED' "
            "AND current_award_score_entry_id IS NULL "
            "AND last_reversal_score_entry_id IS NOT NULL "
            "AND reversed_at IS NOT NULL)",
            name="ck_lesson_component_status_shape",
        ),
        CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL "
            "AND btrim(materialized_by_run_id) <> '') "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_lesson_component_origin",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_lesson_component_one_current_award",
            "source_region",
            "source_appoint_id",
            "component_code",
            unique=True,
            postgresql_where=text("status = 'AWARDED'"),
            sqlite_where=text("status = 'AWARDED'"),
        ),
        Index(
            "ix_lesson_component_teacher",
            "source_region",
            "teacher_id",
            "source_appoint_id",
        ),
        {
            "comment": (
                "DTS v2 shadow: PENDING retains frozen awards but blocks "
                "new/replacement/re-award writes; no production writer is active"
            )
        },
    )

    source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    source_appoint_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    completion_participation_seq: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    component_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    award_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    component_score: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    score_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    current_award_score_entry_id: Mapped[Optional[str]] = mapped_column(
        String(128)
    )
    last_reversal_score_entry_id: Mapped[Optional[str]] = mapped_column(
        String(128)
    )
    materialization_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    materialized_by_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    award_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reversed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class TeacherRecord(Base):
    __tablename__ = "teachers"
    __table_args__ = (
        CheckConstraint(
            "graduation_state IN ('IN_CAMP', 'GRADUATED') "
            "AND jsonb_typeof(payload) = 'object' "
            "AND payload ? 'graduation_state' "
            "AND payload ->> 'graduation_state' = graduation_state",
            name="ck_teachers_graduation_state_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "online_status IS NULL OR online_status IN "
            "('NEW', 'EXISTING', 'LEFT', 'BLOCKED')",
            name="ck_teachers_online_status_v2",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_teachers_source_snapshot_online_status_teacher",
            "source_snapshot_label",
            "online_status",
            "teacher_id",
        ),
    )

    teacher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    camp_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    online_status: Mapped[Optional[str]] = mapped_column(String(32))
    graduation_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="IN_CAMP"
    )
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
            "graduation_score_locked IS NULL OR "
            "(graduation_qualified IS TRUE "
            "AND graduation_score_locked = 100.0)",
            name=(
                "ck_teacher_qualification_graduation_score_locked_v2"
            ),
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "graduation_qualified IS FALSE "
            "OR (graduation_score_locked IS NOT NULL "
            "AND graduation_score_locked = 100.0)",
            name=(
                "ck_teacher_qualification_"
                "graduation_requires_score_locked_v2"
            ),
        ).ddl_if(dialect="postgresql"),
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
    graduation_score_locked: Mapped[Optional[float]] = mapped_column(Float)
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
            "rule_id",
            "source_sha256",
            name="uq_complaint_rule_identity_version",
        ),
        UniqueConstraint(
            "source_sha256",
            "category_l3_normalized",
            name="uq_complaint_rule_source_l3",
        ),
        UniqueConstraint(
            "source_sha256",
            "source_row_number",
            name="uq_complaint_rule_source_row_v2",
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


class ComplaintRulePublicationAuditRecord(Base):
    """Immutable receipt for one complaint-catalog activation command."""

    __tablename__ = "complaint_rule_publication_audits"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_complaint_rule_publication_idempotency",
        ),
        UniqueConstraint(
            "activation_generation",
            name="uq_complaint_rule_publication_generation",
        ),
    )

    publication_audit_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(
        ForeignKey("complaint_rule_imports.source_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    previous_source_sha256: Mapped[Optional[str]] = mapped_column(
        ForeignKey("complaint_rule_imports.source_sha256", ondelete="RESTRICT")
    )
    expected_catalog_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    activation_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_categories: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False)
    affected_category_count: Mapped[int] = mapped_column(Integer, nullable=False)
    course_count: Mapped[int] = mapped_column(Integer, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PersonalizedTriggerMatchRecord(Base):
    """Immutable evidence that a deterministic personalized rule matched once."""

    __tablename__ = "personalized_trigger_matches"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_personalized_trigger_match_dedupe"),
        ForeignKeyConstraint(
            ["task_assignment_id"],
            ["task_assignments.assignment_id"],
            name="fk_trigger_match_task_assignment_v2",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["ops_case_id"],
            ["ops_cases.case_id"],
            name="fk_trigger_match_ops_case_v2",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.notification_id"],
            name="fk_trigger_match_notification_v2",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["lesson_source_region", "lesson_id"],
            ["lesson_source_wide.source_region", "lesson_source_wide.课程id"],
            name="fk_personalized_trigger_match_lesson_region",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["complaint_rule_id", "complaint_rule_source_sha256"],
            [
                "complaint_category_rules.rule_id",
                "complaint_category_rules.source_sha256",
            ],
            name="fk_trigger_match_complaint_rule_version_v2",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "(lesson_id IS NULL AND lesson_source_region IS NULL) OR "
            "(lesson_id IS NOT NULL "
            "AND lesson_source_region IN ('dom','ovs'))",
            name="ck_trigger_match_compat_lesson_pair",
        ),
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
        Index(
            "ix_trigger_match_compat_region_lesson",
            "lesson_source_region",
            "lesson_id",
        ),
        Index(
            "ix_trigger_match_complaint_rule_version_v2",
            "complaint_rule_id",
            "complaint_rule_source_sha256",
        ),
        Index(
            "ix_trigger_match_v2_assignment_active_seed",
            "assignment_dedupe_key_sort_bytes",
            "is_serving",
            "match_status",
            "seed_rule_rank",
            "source_region_rank",
            "source_appoint_id_type_rank",
            "source_appoint_id_numeric",
            "source_appoint_id_sort_bytes",
            "participation_seq",
            "evidence_discriminator_type_rank",
            "evidence_discriminator_numeric",
            "evidence_discriminator_sort_bytes",
            "dedupe_key_sort_bytes",
        ),
        Index(
            "ix_trigger_match_v2_course_current",
            "source_region",
            "source_appoint_id",
            "source_candidate_active",
        ),
    )

    trigger_match_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trigger_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.teacher_id", ondelete="RESTRICT"), nullable=False
    )
    lesson_source_region: Mapped[Optional[str]] = mapped_column(String(8))
    lesson_id: Mapped[Optional[str]] = mapped_column(index=True)
    complaint_rule_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    complaint_rule_source_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_title: Mapped[str] = mapped_column(String(500), nullable=False)
    output_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    match_status: Mapped[str] = mapped_column(String(24), nullable=False, default="MATCHED")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    source_region: Mapped[Optional[str]] = mapped_column(String(8))
    source_appoint_id: Mapped[Optional[str]] = mapped_column(String(512))
    participation_seq: Mapped[Optional[int]] = mapped_column(Integer)
    match_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'LEGACY_REUSED'")
    )
    match_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    last_transition_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    target_task_code: Mapped[Optional[str]] = mapped_column(String(64))
    assignment_dedupe_key: Mapped[Optional[str]] = mapped_column(String(256))
    seed_rule_rank: Mapped[Optional[int]] = mapped_column(Integer)
    threshold_required: Mapped[Optional[int]] = mapped_column(Integer)
    source_candidate_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    plan_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False, default=dict, server_default=text("'{}'")
    )
    plan_evidence_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'")
    )
    teacher_execution_variant: Mapped[Optional[str]] = mapped_column(String(48))
    output_key: Mapped[Optional[str]] = mapped_column(String(768))
    source_ref: Mapped[str] = mapped_column(String(768), nullable=False, server_default=text("''"))
    assignment_dedupe_key_sort_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    source_region_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    source_appoint_id_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NONE'")
    )
    source_appoint_id_type_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    source_appoint_id_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_appoint_id_sort_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    evidence_discriminator: Mapped[Optional[str]] = mapped_column(String(512))
    evidence_discriminator_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NONE'")
    )
    evidence_discriminator_type_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    evidence_discriminator_numeric: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    evidence_discriminator_sort_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    dedupe_key_sort_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, server_default=text("''"))
    materialization_origin: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'LEGACY_REUSED'")
    )
    created_projection_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    serving_projection_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_serving: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    task_assignment_id: Mapped[Optional[str]] = mapped_column(String(128))
    ops_case_id: Mapped[Optional[str]] = mapped_column(String(768))
    notification_id: Mapped[Optional[str]] = mapped_column(String(128))
    source_aggregate_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    projection_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    triggering_event_id: Mapped[Optional[str]] = mapped_column(String(512))
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    materialized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LessonScoreResultRecord(Base):
    """Current one-row score result derived from one lesson source row."""

    __tablename__ = "lesson_score_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["lesson_source_region", "lesson_id"],
            ["lesson_source_wide.source_region", "lesson_source_wide.课程id"],
            name="fk_lesson_score_result_source_lesson_region",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "lesson_source_region IN ('dom','ovs')",
            name="ck_lesson_score_result_compat_region",
        ),
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
        CheckConstraint(
            "(v2_source_region IS NULL "
            "AND v2_source_appoint_id IS NULL "
            "AND v2_completion_participation_seq IS NULL "
            "AND v2_teacher_id IS NULL "
            "AND v2_projection_generation IS NULL) "
            "OR (v2_source_region IN ('dom', 'ovs') "
            "AND v2_source_appoint_id IS NOT NULL "
            "AND v2_completion_participation_seq >= 1 "
            "AND v2_teacher_id IS NOT NULL "
            "AND btrim(v2_teacher_id) <> '' "
            "AND v2_projection_generation >= 1)",
            name="ck_lesson_score_result_v2_ownership",
        ).ddl_if(dialect="postgresql"),
        ForeignKeyConstraint(
            ["v2_source_region", "v2_source_appoint_id"],
            ["source_courses.source_region", "source_courses.source_appoint_id"],
            name="fk_lesson_score_result_v2_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "v2_source_region",
                "v2_source_appoint_id",
                "v2_completion_participation_seq",
                "v2_teacher_id",
            ],
            [
                "source_course_participations.source_region",
                "source_course_participations.source_appoint_id",
                "source_course_participations.participation_seq",
                "source_course_participations.teacher_id",
            ],
            name="fk_lesson_score_result_v2_score_owner",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_lesson_score_result_v2_course",
            "v2_source_region",
            "v2_source_appoint_id",
            unique=True,
            postgresql_where=text("v2_source_region IS NOT NULL"),
            sqlite_where=text("v2_source_region IS NOT NULL"),
        ),
        Index(
            "ix_lesson_score_result_compat_region_lesson",
            "lesson_source_region",
            "lesson_id",
        ),
    )

    lesson_source_region: Mapped[str] = mapped_column(String(8), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String(128), primary_key=True)
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
    v2_source_region: Mapped[Optional[str]] = mapped_column(
        String(8),
        comment=(
            "Nullable v2 ownership; legacy rows remain NULL and cannot be "
            "retrofitted by UPDATE"
        ),
    )
    v2_source_appoint_id: Mapped[Optional[str]] = mapped_column(String(512))
    v2_completion_participation_seq: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment=(
            "Frozen v2 completion ownership; PENDING retains existing matching "
            "results but forbids result writes"
        ),
    )
    v2_teacher_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        comment="Frozen completion teacher for the v2 lesson score result",
    )
    v2_projection_generation: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment="Serving generation owned by the v2 score projection",
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
        CheckConstraint(
            "(source_region IS NULL AND source_appoint_id IS NULL) OR "
            "(source_region IN ('dom','ovs') "
            "AND source_appoint_id IS NOT NULL)",
            name="ck_score_entry_source_identity_expand",
        ),
        CheckConstraint(
            "projection_origin IS NULL OR projection_origin IN ("
            "'LEGACY_EXISTING','V1_COMPAT_LIVE','FIXED_TASK_LIVE',"
            "'CUTOVER_CREATED','V2_LIVE','ROLLBACK_CREATED')",
            name="ck_score_entry_projection_origin_expand",
        ),
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
        Index(
            "ix_score_entry_source_course_expand",
            "source_region",
            "source_appoint_id",
            "participation_seq",
        ),
        Index(
            "uq_score_entries_reversal_once_v2",
            "reversal_of_score_entry_id",
            unique=True,
            postgresql_where=text("reversal_of_score_entry_id IS NOT NULL"),
            sqlite_where=text("reversal_of_score_entry_id IS NOT NULL"),
        ),
    )

    score_entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    camp_enrollment_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    lesson_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    source_region: Mapped[Optional[str]] = mapped_column(String(8))
    source_appoint_id: Mapped[Optional[str]] = mapped_column(String(512))
    participation_seq: Mapped[Optional[int]] = mapped_column(Integer)
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
    projection_origin: Mapped[Optional[str]] = mapped_column(String(32))
    materialized_by_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    projection_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)


class ScoreEntryIdempotencyAliasRecord(Base):
    """Cutover-only canonical alias for one immutable legacy score entry."""

    __tablename__ = "score_entry_idempotency_aliases"
    __table_args__ = (
        UniqueConstraint(
            "score_entry_id", name="uq_score_entry_idempotency_alias_entry"
        ),
        CheckConstraint(
            "alias_type = 'LEGACY_CAPACITY_MILESTONE'",
            name="ck_score_entry_idempotency_alias_type",
        ),
        CheckConstraint(
            "btrim(alias_key) <> '' AND btrim(migration_run_id) <> '' "
            "AND btrim(reason) <> ''",
            name="ck_score_entry_idempotency_alias_text",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Cutover-only canonical aliases for immutable legacy "
                "capacity milestone score entries"
            )
        },
    )

    alias_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    score_entry_id: Mapped[str] = mapped_column(
        ForeignKey(
            "score_entries.score_entry_id",
            name="fk_score_entry_idempotency_alias_entry",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    alias_type: Mapped[str] = mapped_column(String(64), nullable=False)
    migration_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    audit_event_id: Mapped[str] = mapped_column(
        ForeignKey(
            "audit_events.event_id",
            name="fk_score_entry_idempotency_alias_audit",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class TaskTemplateRecord(Base):
    """The single current task-template catalog used by both services."""

    __tablename__ = "task_templates"
    __table_args__ = (
        UniqueConstraint("template_id", "template_version", name="uq_task_template_version"),
        UniqueConstraint(
            "row_id",
            "template_id",
            name="uq_task_template_row_identity",
        ),
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
        ForeignKeyConstraint(
            ["template_version_id", "task_code"],
            ["task_templates.row_id", "task_templates.template_id"],
            name="fk_task_assignment_template_code_v2",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["teacher_copy_version_id", "teacher_copy_config_key"],
            ["config_versions.version_id", "config_versions.config_key"],
            name="fk_task_assignment_copy_version_v2",
            ondelete="RESTRICT",
        ),
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
        Index(
            "ix_task_assignment_materialization_seed_v2",
            "materialization_seed_kind",
            "materialization_seed_key",
        ),
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
    materialization_seed_kind: Mapped[Optional[str]] = mapped_column(String(32))
    materialization_seed_key: Mapped[Optional[str]] = mapped_column(String(768))
    materialization_seed_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    materialization_seed_payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    materialization_template_revision: Mapped[Optional[int]] = mapped_column(Integer)
    materialization_plan_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    eligibility_generation: Mapped[Optional[int]] = mapped_column(BigInteger)
    eligible_since_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    teacher_copy_version_id: Mapped[Optional[str]] = mapped_column(String(128))
    teacher_copy_config_key: Mapped[Optional[str]] = mapped_column(String(64))
    teacher_execution_variant: Mapped[Optional[str]] = mapped_column(String(48))
    plan_evidence_hash: Mapped[Optional[str]] = mapped_column(String(64))
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
    source_ref: Mapped[Optional[str]] = mapped_column(String(768))
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
    __table_args__ = (
        CheckConstraint(
            "case_revision>=1 AND row_version>=1 "
            "AND recovery_evidence_count>=0 "
            "AND ((recovery_evidence_count=0 "
            "AND last_recovery_event_id IS NULL "
            "AND last_recovery_count IS NULL "
            "AND last_recovered_at IS NULL) "
            "OR (recovery_evidence_count>0 "
            "AND last_recovery_event_id IS NOT NULL "
            "AND last_recovery_count>=1 "
            "AND last_recovered_at IS NOT NULL))",
            name="ck_ops_case_v2_versions",
        ),
        CheckConstraint(
            "(source_region IS NULL OR source_region IN ('dom','ovs')) "
            "AND (source_appoint_id IS NULL OR source_region IS NOT NULL)",
            name="ck_ops_case_v2_region",
        ),
        CheckConstraint(
            "teacher_id IS NOT NULL OR case_type IN ("
            "'COURSE_COMPLETION_CORRECTION','DTS_DIRTY_KEY_DEAD',"
            "'DTS_SOURCE_CONFLICT','FAVORITE_OBSERVATION_DEAD',"
            "'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD')",
            name="ck_ops_case_v2_subject",
        ),
        CheckConstraint(
            "case_type NOT IN ("
            "'COURSE_COMPLETION_CORRECTION','DTS_DIRTY_KEY_DEAD',"
            "'DTS_SOURCE_CONFLICT','FAVORITE_OBSERVATION_DEAD',"
            "'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD') "
            "OR source_ref IS NOT NULL",
            name="ck_ops_case_v2_protected_source_ref",
        ),
        CheckConstraint(
            "case_type<>'COURSE_COMPLETION_CORRECTION' OR ("
            "source_region IN ('dom','ovs') "
            "AND source_appoint_id IS NOT NULL "
            "AND btrim(source_appoint_id)<>'' "
            "AND source_ref='course-completion-correction:' || "
            "source_region || ':' || source_appoint_id "
            "AND case_id=source_ref "
            "AND evidence_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_ops_case_v2_completion_identity",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "case_type NOT IN ("
            "'DTS_DIRTY_KEY_DEAD','DTS_SOURCE_CONFLICT',"
            "'FAVORITE_OBSERVATION_DEAD','TASK_MATERIALIZATION_DEAD',"
            "'DOWNSTREAM_PROJECTION_DEAD') OR ("
            "case_id='v2case:' || encode("
            "sha256(convert_to(source_ref,'UTF8')),'hex') "
            "AND priority='P1' "
            "AND status IN ('OPEN','IN_REVIEW','RESOLVED') "
            "AND external_action_status='NOT_REQUESTED' "
            "AND evidence_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(payload)='object')",
            name="ck_ops_case_v2_technical_shape",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "NOT public.dts_ops_case_is_technical_v2(case_type) "
            "OR public.dts_technical_source_ref_shape_valid_v2("
            "case_type,source_ref)",
            name="ck_ops_case_v2_technical_source_ref",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_ops_cases_source_ref_v2",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
            sqlite_where=text("source_ref IS NOT NULL"),
        ),
        Index(
            "uq_ops_cases_completion_course_v2",
            "case_type",
            "source_region",
            "source_appoint_id",
            unique=True,
            postgresql_where=text(
                "case_type='COURSE_COMPLETION_CORRECTION'"
            ),
            sqlite_where=text(
                "case_type='COURSE_COMPLETION_CORRECTION'"
            ),
        ),
        Index(
            "ix_ops_cases_source_course_v2",
            "source_region",
            "source_appoint_id",
            "case_type",
            "status",
            postgresql_where=text("source_region IS NOT NULL"),
            sqlite_where=text("source_region IS NOT NULL"),
        ),
        {
            "comment": (
                "Current operational Case state. DTS v2 protected Case "
                "identities and evidence are command-owned and "
                "delete-forbidden."
            )
        },
    )

    case_id: Mapped[str] = mapped_column(String(768), primary_key=True)
    case_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    teacher_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("teachers.teacher_id"), nullable=True, index=True
    )
    task_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_reason: Mapped[Optional[str]] = mapped_column(String(128))
    external_action_status: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(
        String(768),
        comment=(
            "Canonical unique business or technical source identity; "
            "mandatory for protected v2 Case types."
        ),
    )
    source_region: Mapped[Optional[str]] = mapped_column(String(8))
    source_appoint_id: Mapped[Optional[str]] = mapped_column(String(512))
    case_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
        comment=(
            "Semantic evidence revision used for correction decisions and "
            "technical recovery concurrency."
        ),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
        comment=(
            "Local optimistic-lock version; increments on every protected "
            "Case update."
        ),
    )
    evidence_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    recovery_evidence_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Count of append-only proofs that the original work truly "
            "completed."
        ),
    )
    last_recovery_event_id: Mapped[Optional[str]] = mapped_column(String(512))
    last_recovery_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_recovered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )


class OpsDecisionRecord(Base):
    __tablename__ = "ops_decisions"
    __table_args__ = (
        CheckConstraint(
            "row_version>=1",
            name="ck_ops_decision_v2_row_version",
        ),
        CheckConstraint(
            "downstream_projection_status IS NULL OR "
            "downstream_projection_status IN ("
            "'PENDING','PUBLISHED','DEAD_LETTER')",
            name="ck_ops_decision_projection_status_v2",
        ),
        CheckConstraint(
            "projection_event_ids IS NULL OR "
            "public.dts_projection_event_ids_valid_v2(projection_event_ids)",
            name="ck_ops_decision_projection_events_v2",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Immutable operator decision facts plus separately mutable "
                "downstream projection status."
            )
        },
    )

    decision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(768), ForeignKey("ops_cases.case_id"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False, default="OPS_USER")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    expected_case_revision: Mapped[Optional[int]] = mapped_column(Integer)
    expected_conflict_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64)
    )
    expected_source_revision: Mapped[Optional[int]] = mapped_column(BigInteger)
    expected_source_position: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON_VALUE
    )
    downstream_projection_status: Mapped[Optional[str]] = mapped_column(
        String(24),
        comment=(
            "PENDING, PUBLISHED, or DEAD_LETTER for the immutable "
            "projection_event_ids set; not the decision result."
        ),
    )
    projection_event_ids: Mapped[Optional[list[str]]] = mapped_column(JSON_VALUE)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )


class OpsCaseRecoveryEventRecord(Base):
    __tablename__ = "ops_case_recovery_events"
    __table_args__ = (
        PrimaryKeyConstraint(
            "recovery_event_id", name="pk_ops_case_recovery_events"
        ),
        UniqueConstraint(
            "case_id",
            "event_id",
            "recovery_count",
            name="uq_ops_case_recovery_work_generation",
        ),
        ForeignKeyConstraint(
            ["case_id"],
            ["ops_cases.case_id"],
            name="fk_ops_case_recovery_case",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "recovery_count >= 1 AND outbox_row_version >= 1 "
            "AND case_revision_after >= 1",
            name="ck_ops_case_recovery_versions",
        ),
        CheckConstraint(
            "work_status='PUBLISHED'",
            name="ck_ops_case_recovery_work_status",
        ),
        CheckConstraint(
            "outbox_payload_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256="
            "public.dts_canonical_json_sha256_v1(evidence)",
            name="ck_ops_case_recovery_hashes",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_ops_case_recovery_case_recorded_v2",
            "case_id",
            "recorded_at",
            "recovery_event_id",
        ),
        {
            "comment": (
                "Append-only evidence that the original DTS v2 work reached "
                "PUBLISHED; DEAD to PENDING alone never creates this row."
            )
        },
    )

    recovery_event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(768),
        nullable=False,
    )
    source_ref: Mapped[str] = mapped_column(String(768), nullable=False)
    event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    recovery_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_status: Mapped[str] = mapped_column(String(24), nullable=False)
    outbox_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outbox_row_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    case_status_before: Mapped[str] = mapped_column(String(32), nullable=False)
    case_status_after: Mapped[str] = mapped_column(String(32), nullable=False)
    case_revision_after: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$' "
            "AND payload_sha256 = "
            "public.dts_canonical_json_sha256_v1(payload)",
            name="ck_outbox_payload_sha256_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "recovery_count >= 0 AND row_version >= 1 "
            "AND ((recovery_count = 0 AND recovered_at IS NULL) "
            "OR (recovery_count > 0 AND recovered_at IS NOT NULL))",
            name="ck_outbox_recovery_v2",
        ),
        CheckConstraint(
            "status IN ('PENDING','PUBLISHED','DEAD_LETTER')",
            name="ck_outbox_status_v2",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "attempt_count >= 0 AND recovery_count >= 0 "
            "AND row_version >= 1 AND ("
            "(status='PENDING' AND attempt_count BETWEEN 0 AND 7 "
            "AND published_at IS NULL AND settled_by_run_id IS NULL "
            "AND ((attempt_count=0 AND last_error IS NULL) "
            "OR (attempt_count>0 AND last_error IS NOT NULL))) OR "
            "(status='PUBLISHED' AND attempt_count BETWEEN 0 AND 7 "
            "AND published_at IS NOT NULL) OR "
            "(status='DEAD_LETTER' AND attempt_count=8 "
            "AND last_error IS NOT NULL AND published_at IS NULL "
            "AND settled_by_run_id IS NULL))",
            name="ck_outbox_state_shape_v2",
        ).ddl_if(dialect="postgresql"),
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
        Index(
            "ix_outbox_dead_letter_v2",
            "event_type",
            "created_at",
            "outbox_id",
            postgresql_where=text("status = 'DEAD_LETTER'"),
            sqlite_where=text("status = 'DEAD_LETTER'"),
        ),
        {
            "comment": (
                "Active v2 Outbox work. Only PENDING, PUBLISHED, and "
                "DEAD_LETTER are valid; DEAD_LETTER recovery is an "
                "audited command."
            )
        },
    )

    outbox_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default=_outbox_payload_sha256
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    recovered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    settled_by_run_id: Mapped[Optional[str]] = mapped_column(
        String(160),
        comment=(
            "Optional cutover run identity; ordinary Worker publication "
            "leaves it NULL."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OutboxEventLegacyArchiveRecord(Base):
    __tablename__ = "outbox_events_legacy_archive"
    __table_args__ = (
        UniqueConstraint(
            "outbox_id", name="uq_outbox_events_legacy_archive_outbox_id"
        ),
        CheckConstraint(
            "status IN ('CANCELLED','PARKED')",
            name="ck_outbox_legacy_archive_status",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' "
            "AND archive_source_row_hash ~ '^[0-9a-f]{64}$' "
            "AND proof_hash ~ '^[0-9a-f]{64}$' "
            "AND payload_hash = "
            "public.dts_canonical_json_sha256_v1(payload)",
            name="ck_outbox_legacy_archive_hashes",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(proof_type='MOCK_SEED_CANCELLED' "
            "AND archive_reason='PROVEN_NON_REAL_MOCK_CANCELLED' "
            "AND status='CANCELLED') OR "
            "(proof_type='MIGRATION_20260729_37_RETRY_PARKED' "
            "AND archive_reason='PROVEN_RETIRED_CONTROL_PARKED' "
            "AND status='PARKED')",
            name="ck_outbox_legacy_archive_proof_reason",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "attempt_count >= 0 AND recovery_count = 0 "
            "AND recovered_at IS NULL AND row_version >= 1 "
            "AND published_at IS NULL AND settled_by_run_id IS NULL "
            "AND btrim(migration_run_id) <> '' "
            "AND btrim(archived_by) <> ''",
            name="ck_outbox_legacy_archive_state",
        ).ddl_if(dialect="postgresql"),
        {
            "comment": (
                "Append-only audit archive for legacy Outbox rows proven "
                "to be non-real mock work or retired control work; never "
                "active work."
            )
        },
    )

    event_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    outbox_id: Mapped[str] = mapped_column(String(160), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(48), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recovery_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recovered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    settled_by_run_id: Mapped[Optional[str]] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    archive_source_row_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    archive_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    proof_type: Mapped[str] = mapped_column(String(64), nullable=False)
    proof_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    migration_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    archive_audit_event_id: Mapped[str] = mapped_column(
        ForeignKey(
            "audit_events.event_id",
            name="fk_outbox_legacy_archive_audit_event",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    archived_by: Mapped[str] = mapped_column(String(128), nullable=False)


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
    case_id: Mapped[Optional[str]] = mapped_column(String(768), index=True)
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
