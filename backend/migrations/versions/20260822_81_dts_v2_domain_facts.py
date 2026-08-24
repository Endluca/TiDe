"""add typed DTS v2 domain facts and aggregate revisions.

Revision ID: 20260822_81_dts_v2_domain_facts
Revises: 20260822_80_dts_v2_dirty_queue
Create Date: 2026-08-22

This is an additive schema slice only.  It gives the later Domain Projector a
typed, region-qualified write target, but it does not start a worker, emit an
Outbox event, or change a serving/read route.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_81_dts_v2_domain_facts"
down_revision: Union[str, None] = "20260822_80_dts_v2_dirty_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVIDENCE_STATUSES: tuple[str, ...] = (
    "CONFIRMED",
    "PENDING_DATA",
    "SOURCE_MISSING",
)

AGGREGATE_TYPES: tuple[str, ...] = (
    "COURSE",
    "PARTICIPATION",
    "TEACHER",
    "TEACHER_STUDENT",
    "LABEL",
    "COMPLAINT_CATEGORY",
    "COMPLETION_CONFLICT",
    "SOURCE_SCOPE",
    "TASK_PLAN",
)

EMPTY_OBJECT_SHA256 = (
    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)

DOMAIN_FACT_TABLES: tuple[str, ...] = (
    "source_course_labels",
    "source_course_complaints",
    "source_course_fact_current",
    "source_participation_fact_current",
)

ALL_NEW_TABLES: tuple[str, ...] = (
    *DOMAIN_FACT_TABLES,
    "domain_aggregate_revisions",
)


def _typed_id_columns(
    name: str,
    *,
    nullable: bool,
    length: int = 512,
) -> list[sa.Column[object]]:
    return [
        sa.Column(name, sa.String(length=length), nullable=nullable),
        sa.Column(f"{name}_type", sa.String(length=16), nullable=nullable),
        sa.Column(f"{name}_numeric", sa.Numeric(), nullable=True),
        sa.Column(f"{name}_text", sa.Text(), nullable=True),
    ]


def _typed_id_check(name: str, *, nullable: bool) -> str:
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


def _evidence_check(status: str, error: str) -> str:
    return (
        f"{status} IN ('CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
        f"AND (({status} = 'CONFIRMED' AND {error} IS NULL) "
        f"OR ({status} <> 'CONFIRMED' AND {error} IS NOT NULL "
        f"AND btrim({error}) <> ''))"
    )


def _source_position_check(column: str, *, nullable: bool = False) -> str:
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


def _install_aggregate_key_validator() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_domain_aggregate_key_valid_v2(
            p_aggregate_type text,
            p_key jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT CASE
                WHEN jsonb_typeof(p_key) <> 'object' THEN false
                WHEN p_aggregate_type IN ('COURSE','COMPLETION_CONFLICT') THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'source_appoint_id',p_key->'source_appoint_id'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'source_appoint_id') = 'string'
                    AND p_key->>'source_appoint_id' <> ''
                    AND btrim(p_key->>'source_appoint_id') =
                        p_key->>'source_appoint_id'
                WHEN p_aggregate_type = 'PARTICIPATION' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'source_appoint_id',p_key->'source_appoint_id',
                        'participation_seq',p_key->'participation_seq'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'source_appoint_id') = 'string'
                    AND p_key->>'source_appoint_id' <> ''
                    AND btrim(p_key->>'source_appoint_id') =
                        p_key->>'source_appoint_id'
                    AND jsonb_typeof(p_key->'participation_seq') = 'number'
                    AND (p_key->>'participation_seq') ~ '^[1-9][0-9]*$'
                WHEN p_aggregate_type = 'TEACHER' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'teacher_id',p_key->'teacher_id'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'teacher_id') = 'string'
                    AND p_key->>'teacher_id' <> ''
                    AND btrim(p_key->>'teacher_id') = p_key->>'teacher_id'
                WHEN p_aggregate_type = 'TEACHER_STUDENT' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'teacher_id',p_key->'teacher_id',
                        'student_token',p_key->'student_token'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'teacher_id') = 'string'
                    AND p_key->>'teacher_id' <> ''
                    AND btrim(p_key->>'teacher_id') = p_key->>'teacher_id'
                    AND jsonb_typeof(p_key->'student_token') = 'string'
                    AND p_key->>'student_token' <> ''
                    AND btrim(p_key->>'student_token') = p_key->>'student_token'
                    AND (
                        p_key->>'source_region' = 'ovs'
                        OR p_key->>'student_token' ~ '^dom:v1:[0-9a-f]{64}$'
                    )
                WHEN p_aggregate_type = 'LABEL' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'label_id',p_key->'label_id'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'label_id') = 'string'
                    AND p_key->>'label_id' <> ''
                    AND btrim(p_key->>'label_id') = p_key->>'label_id'
                WHEN p_aggregate_type = 'COMPLAINT_CATEGORY' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'category_id',p_key->'category_id'
                    )
                    AND p_key->>'source_region' = 'dom'
                    AND jsonb_typeof(p_key->'category_id') = 'string'
                    AND p_key->>'category_id' <> ''
                    AND btrim(p_key->>'category_id') = p_key->>'category_id'
                WHEN p_aggregate_type = 'SOURCE_SCOPE' THEN
                    p_key = jsonb_build_object(
                        'source_region',p_key->'source_region',
                        'source_table',p_key->'source_table',
                        'scope_kind',p_key->'scope_kind',
                        'scope_level',p_key->'scope_level',
                        'scope_key',p_key->'scope_key'
                    )
                    AND p_key->>'source_region' IN ('dom','ovs')
                    AND jsonb_typeof(p_key->'source_table') = 'string'
                    AND p_key->>'source_table' <> ''
                    AND btrim(p_key->>'source_table') = p_key->>'source_table'
                    AND strpos(
                        p_key->>'source_table',
                        p_key->>'source_region' || '_'
                    ) = 1
                    AND p_key->>'scope_kind' IN ('CURRENT','HISTORY')
                    AND p_key->>'scope_level' IN ('GLOBAL','TEACHER')
                    AND jsonb_typeof(p_key->'scope_key') = 'string'
                    AND (
                        (p_key->>'scope_level' = 'GLOBAL'
                         AND p_key->>'scope_key' = '*')
                        OR (p_key->>'scope_level' = 'TEACHER'
                            AND p_key->>'scope_key' <> ''
                            AND btrim(p_key->>'scope_key') =
                                p_key->>'scope_key')
                    )
                WHEN p_aggregate_type = 'TASK_PLAN' THEN
                    p_key = jsonb_build_object(
                        'assignment_dedupe_key',
                        p_key->'assignment_dedupe_key'
                    )
                    AND jsonb_typeof(p_key->'assignment_dedupe_key') = 'string'
                    AND p_key->>'assignment_dedupe_key' <> ''
                    AND btrim(p_key->>'assignment_dedupe_key') =
                        p_key->>'assignment_dedupe_key'
                ELSE false
            END
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_domain_aggregate_key_valid_v2(text,jsonb)
        FROM PUBLIC;
        """
    )


def _create_source_course_labels() -> None:
    op.create_table(
        "source_course_labels",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        *_typed_id_columns("source_log_id", nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        *_typed_id_columns("label_id", nullable=False),
        sa.Column("label_name_snapshot", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("source_row_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "evidence_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'CONFIRMED'"),
        ),
        sa.Column("evidence_error_code", sa.String(length=160), nullable=True),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source_version", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_log_id",
            name="pk_source_course_labels",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            [
                "public.source_courses.source_region",
                "public.source_courses.source_appoint_id",
            ],
            name="fk_source_course_label_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_label_region",
        ),
        sa.CheckConstraint(
            _typed_id_check("source_log_id", nullable=False),
            name="ck_source_course_label_log_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("label_id", nullable=False),
            name="ck_source_course_label_id_typed",
        ),
        sa.CheckConstraint(
            "source_row_revision >= 1",
            name="ck_source_course_label_revision",
        ),
        sa.CheckConstraint(
            _source_position_check("source_position"),
            name="ck_source_course_label_position",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_version) = 'object'",
            name="ck_source_course_label_source_version",
        ),
        sa.CheckConstraint(
            _evidence_check("evidence_status", "evidence_error_code"),
            name="ck_source_course_label_evidence",
        ),
        schema="public",
    )
    op.create_index(
        "ix_source_course_labels_course_label",
        "source_course_labels",
        [
            "source_region",
            "source_appoint_id",
            "label_id_type",
            "label_id",
            "is_deleted",
        ],
        schema="public",
    )
    op.create_index(
        "ix_source_course_labels_label_reverse",
        "source_course_labels",
        ["source_region", "label_id_type", "label_id"],
        schema="public",
    )


def _create_source_course_complaints() -> None:
    op.create_table(
        "source_course_complaints",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        *_typed_id_columns("source_complaint_id", nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        *_typed_id_columns("source_teacher_id", nullable=True, length=64),
        *_typed_id_columns("complaint_type", nullable=True),
        *_typed_id_columns("complaint_type_child", nullable=True),
        *_typed_id_columns("complaint_type_grandson", nullable=True),
        sa.Column("approve", sa.Text(), nullable=True),
        sa.Column("validity", sa.Integer(), nullable=True),
        sa.Column("add_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("course_date", sa.Date(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column("complaint_rule_id", sa.String(length=160), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("severity_rank", sa.Integer(), nullable=True),
        sa.Column("category_l1_snapshot", sa.Text(), nullable=True),
        sa.Column("category_l2_snapshot", sa.Text(), nullable=True),
        sa.Column("category_l3_snapshot", sa.Text(), nullable=True),
        sa.Column("category_l3_normalized", sa.Text(), nullable=True),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column("evidence_error_code", sa.String(length=160), nullable=True),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source_version", postgresql.JSONB(), nullable=False),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("source_row_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_complaint_id",
            name="pk_source_course_complaints",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            [
                "public.source_courses.source_region",
                "public.source_courses.source_appoint_id",
            ],
            name="fk_source_course_complaint_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["complaint_rule_id", "source_sha256"],
            [
                "public.complaint_category_rules.rule_id",
                "public.complaint_category_rules.source_sha256",
            ],
            name="fk_source_course_complaint_rule_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_complaint_region",
        ),
        sa.CheckConstraint(
            _typed_id_check("source_complaint_id", nullable=False),
            name="ck_source_course_complaint_id_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("source_teacher_id", nullable=True),
            name="ck_source_course_complaint_teacher_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("complaint_type", nullable=True),
            name="ck_source_course_complaint_l1_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("complaint_type_child", nullable=True),
            name="ck_source_course_complaint_l2_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("complaint_type_grandson", nullable=True),
            name="ck_source_course_complaint_l3_typed",
        ),
        sa.CheckConstraint(
            "((complaint_rule_id IS NULL AND source_sha256 IS NULL "
            "AND severity_rank IS NULL) OR "
            "(complaint_rule_id IS NOT NULL AND source_sha256 IS NOT NULL "
            "AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND severity_rank BETWEEN 0 AND 4 "
            "AND category_l3_normalized IS NOT NULL "
            "AND btrim(category_l3_normalized) <> ''))",
            name="ck_source_course_complaint_rule_group",
        ),
        sa.CheckConstraint(
            _evidence_check("evidence_status", "evidence_error_code"),
            name="ck_source_course_complaint_evidence",
        ),
        sa.CheckConstraint(
            "source_row_revision >= 1",
            name="ck_source_course_complaint_revision",
        ),
        sa.CheckConstraint(
            _source_position_check("source_position"),
            name="ck_source_course_complaint_position",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_version) = 'object'",
            name="ck_source_course_complaint_source_version",
        ),
        schema="public",
    )
    op.create_index(
        "ix_source_course_complaints_course_latest",
        "source_course_complaints",
        [
            "source_region",
            "source_appoint_id",
            "is_deleted",
            "is_valid",
            "add_time",
            "course_date",
            "source_complaint_id",
            "source_row_revision",
        ],
        schema="public",
    )
    for suffix, column in (
        ("l1", "complaint_type"),
        ("l2", "complaint_type_child"),
        ("l3", "complaint_type_grandson"),
    ):
        op.create_index(
            f"ix_source_course_complaints_category_{suffix}",
            "source_course_complaints",
            [f"{column}_type", column, "source_region", "source_appoint_id"],
            schema="public",
        )


def _create_source_course_fact_current() -> None:
    op.create_table(
        "source_course_fact_current",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        *_typed_id_columns("current_grading_source_id", nullable=True),
        sa.Column(
            "grading_classification",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("negative_score", sa.Numeric(), nullable=True),
        sa.Column("grading_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("grading_error_code", sa.String(length=160), nullable=True),
        *_typed_id_columns("latest_valid_complaint_id", nullable=True),
        sa.Column("has_complaint", sa.Boolean(), nullable=True),
        sa.Column("has_valid_complaint", sa.Boolean(), nullable=True),
        sa.Column("latest_category_l1_snapshot", sa.Text(), nullable=True),
        sa.Column("latest_category_l2_snapshot", sa.Text(), nullable=True),
        sa.Column("latest_category_l3_snapshot", sa.Text(), nullable=True),
        sa.Column("complaint_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("complaint_error_code", sa.String(length=160), nullable=True),
        sa.Column("is_camera_off", sa.Boolean(), nullable=True),
        sa.Column("camera_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("is_cpu_usage_high", sa.Boolean(), nullable=True),
        sa.Column("cpu_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("is_network_delay_high", sa.Boolean(), nullable=True),
        sa.Column("network_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("source_version_vector", postgresql.JSONB(), nullable=False),
        sa.Column("source_version_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            name="pk_source_course_fact_current",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id"],
            [
                "public.source_courses.source_region",
                "public.source_courses.source_appoint_id",
            ],
            name="fk_source_course_fact_course",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "latest_valid_complaint_id"],
            [
                "public.source_course_complaints.source_region",
                "public.source_course_complaints.source_complaint_id",
            ],
            name="fk_source_course_fact_latest_complaint",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_source_course_fact_region",
        ),
        sa.CheckConstraint(
            _typed_id_check("current_grading_source_id", nullable=True),
            name="ck_source_course_fact_grading_typed",
        ),
        sa.CheckConstraint(
            _typed_id_check("latest_valid_complaint_id", nullable=True),
            name="ck_source_course_fact_complaint_typed",
        ),
        sa.CheckConstraint(
            "grading_classification IN ("
            "'POSITIVE','NEGATIVE','UNCLASSIFIED','SOURCE_MISSING') "
            "AND (grading_classification = 'NEGATIVE' "
            "OR negative_score IS NULL) "
            "AND (grading_classification <> 'SOURCE_MISSING' "
            "OR grading_evidence_status = 'SOURCE_MISSING')",
            name="ck_source_course_fact_grading_result",
        ),
        sa.CheckConstraint(
            _evidence_check("grading_evidence_status", "grading_error_code"),
            name="ck_source_course_fact_grading_evidence",
        ),
        sa.CheckConstraint(
            "(latest_valid_complaint_id IS NULL "
            "OR has_valid_complaint IS TRUE)",
            name="ck_source_course_fact_latest_valid",
        ),
        sa.CheckConstraint(
            _evidence_check("complaint_evidence_status", "complaint_error_code"),
            name="ck_source_course_fact_complaint_evidence",
        ),
        sa.CheckConstraint(
            "camera_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((camera_evidence_status = 'CONFIRMED' "
            "AND is_camera_off IS NOT NULL) "
            "OR (camera_evidence_status <> 'CONFIRMED' "
            "AND is_camera_off IS NULL))",
            name="ck_source_course_fact_camera_evidence",
        ),
        sa.CheckConstraint(
            "is_cpu_usage_high IS NULL "
            "AND cpu_evidence_status = 'SOURCE_MISSING' "
            "AND is_network_delay_high IS NULL "
            "AND network_evidence_status = 'SOURCE_MISSING'",
            name="ck_source_course_fact_retired_qa_sources",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_source_course_fact_row_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_version_vector) = 'object' "
            "AND source_version_hash ~ '^[0-9a-f]{64}$' "
            "AND source_version_hash = "
            "public.dts_canonical_json_sha256_v1(source_version_vector)",
            name="ck_source_course_fact_source_vector",
        ),
        schema="public",
    )


def _create_source_participation_fact_current() -> None:
    op.create_table(
        "source_participation_fact_current",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("participation_seq", sa.Integer(), nullable=False),
        sa.Column("is_late", sa.Boolean(), nullable=True),
        sa.Column("late_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("is_early", sa.Boolean(), nullable=True),
        sa.Column("early_evidence_status", sa.String(length=32), nullable=False),
        sa.Column("penalty_source_keys", postgresql.JSONB(), nullable=False),
        sa.Column("penalty_source_keys_hash", sa.String(length=64), nullable=False),
        sa.Column("source_version_vector", postgresql.JSONB(), nullable=False),
        sa.Column("source_version_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            "participation_seq",
            name="pk_source_participation_fact_current",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_appoint_id", "participation_seq"],
            [
                "public.source_course_participations.source_region",
                "public.source_course_participations.source_appoint_id",
                "public.source_course_participations.participation_seq",
            ],
            name="fk_source_participation_fact_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs') AND participation_seq >= 1",
            name="ck_source_participation_fact_identity",
        ),
        sa.CheckConstraint(
            "late_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((late_evidence_status = 'CONFIRMED' AND is_late IS NOT NULL) "
            "OR (late_evidence_status <> 'CONFIRMED' AND is_late IS NULL))",
            name="ck_source_participation_fact_late",
        ),
        sa.CheckConstraint(
            "early_evidence_status IN ("
            "'CONFIRMED','PENDING_DATA','SOURCE_MISSING') "
            "AND ((early_evidence_status = 'CONFIRMED' AND is_early IS NOT NULL) "
            "OR (early_evidence_status <> 'CONFIRMED' AND is_early IS NULL))",
            name="ck_source_participation_fact_early",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(penalty_source_keys) = 'array' "
            "AND penalty_source_keys_hash ~ '^[0-9a-f]{64}$' "
            "AND penalty_source_keys_hash = "
            "public.dts_canonical_json_sha256_v1(penalty_source_keys)",
            name="ck_source_participation_fact_penalty_keys",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_version_vector) = 'object' "
            "AND source_version_hash ~ '^[0-9a-f]{64}$' "
            "AND source_version_hash = "
            "public.dts_canonical_json_sha256_v1(source_version_vector)",
            name="ck_source_participation_fact_source_vector",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_source_participation_fact_row_version",
        ),
        schema="public",
    )


def _create_domain_aggregate_revisions() -> None:
    op.create_table(
        "domain_aggregate_revisions",
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.String(length=160), nullable=False),
        sa.Column("canonical_key", postgresql.JSONB(), nullable=False),
        sa.Column("canonical_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("last_source_row_revision", sa.BigInteger(), nullable=True),
        sa.Column("last_source_position", postgresql.JSONB(), nullable=True),
        sa.Column(
            "aggregate_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "aggregate_state_sha256",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text(f"'{EMPTY_OBJECT_SHA256}'"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "aggregate_type",
            "aggregate_id",
            name="pk_domain_aggregate_revisions",
        ),
        sa.CheckConstraint(
            "aggregate_type IN ("
            "'COURSE','PARTICIPATION','TEACHER','TEACHER_STUDENT','LABEL',"
            "'COMPLAINT_CATEGORY','COMPLETION_CONFLICT','SOURCE_SCOPE',"
            "'TASK_PLAN')",
            name="ck_domain_aggregate_type",
        ),
        sa.CheckConstraint(
            "public.dts_domain_aggregate_key_valid_v2("
            "aggregate_type,canonical_key) IS TRUE",
            name="ck_domain_aggregate_canonical_key",
        ),
        sa.CheckConstraint(
            "canonical_key_sha256 ~ '^[0-9a-f]{64}$' "
            "AND canonical_key_sha256 = "
            "public.dts_canonical_json_sha256_v1(canonical_key) "
            "AND aggregate_id = 'v2:' || aggregate_type || ':' || "
            "canonical_key_sha256",
            name="ck_domain_aggregate_id",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND (last_source_row_revision IS NULL "
            "OR last_source_row_revision >= 1)",
            name="ck_domain_aggregate_revisions",
        ),
        sa.CheckConstraint(
            _source_position_check("last_source_position", nullable=True),
            name="ck_domain_aggregate_source_position",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(aggregate_state) = 'object' "
            "AND aggregate_state_sha256 ~ '^[0-9a-f]{64}$' "
            "AND aggregate_state_sha256 = "
            "public.dts_canonical_json_sha256_v1(aggregate_state)",
            name="ck_domain_aggregate_state_hash",
        ),
        sa.CheckConstraint(
            "aggregate_type <> 'TEACHER_STUDENT' "
            "OR canonical_key->>'source_region' = 'ovs' "
            "OR canonical_key->>'student_token' "
            "~ '^dom:v1:[0-9a-f]{64}$'",
            name="ck_domain_aggregate_dom_student_token",
        ),
        schema="public",
    )


def _apply_acl() -> None:
    table_list = ",\n            ".join(
        f"public.{table_name}" for table_name in ALL_NEW_TABLES
    )
    fact_table_list = ",".join(
        f"public.{table_name}" for table_name in DOMAIN_FACT_TABLES
    )
    op.execute(
        sa.text(
            f"""
            REVOKE ALL PRIVILEGES ON TABLE
                {table_list}
            FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

            DO $domain_fact_optional_acl$
            BEGIN
                IF to_regrole('tit_dts_domain_projector_runtime') IS NOT NULL THEN
                    IF EXISTS (
                        SELECT 1
                        FROM pg_roles
                        WHERE rolname = 'tit_dts_domain_projector_runtime'
                          AND (
                              NOT rolcanlogin OR rolinherit OR rolsuper
                              OR rolcreatedb OR rolcreaterole OR rolreplication
                              OR rolbypassrls
                          )
                    ) THEN
                        RAISE EXCEPTION
                            'tit_dts_domain_projector_runtime must be a restricted NOINHERIT LOGIN role';
                    END IF;
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                        '{fact_table_list},public.domain_aggregate_revisions '
                        'FROM tit_dts_domain_projector_runtime';
                    EXECUTE 'GRANT SELECT,INSERT,UPDATE ON TABLE '
                        '{fact_table_list} '
                        'TO tit_dts_domain_projector_runtime';
                    EXECUTE 'GRANT SELECT ON TABLE '
                        'public.domain_aggregate_revisions '
                        'TO tit_dts_domain_projector_runtime';
                    EXECUTE 'GRANT EXECUTE ON FUNCTION '
                        'public.dts_canonical_json_v1(jsonb),'
                        'public.dts_canonical_json_sha256_v1(jsonb) '
                        'TO tit_dts_domain_projector_runtime';
                END IF;
            END
            $domain_fact_optional_acl$;
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("DTS v2 domain facts require PostgreSQL")

    _install_aggregate_key_validator()
    # The PK already makes rule_id unique, but PostgreSQL requires a matching
    # composite unique key for the frozen (rule_id, source_sha256) FK.
    op.create_unique_constraint(
        "uq_complaint_rule_identity_version",
        "complaint_category_rules",
        ["rule_id", "source_sha256"],
        schema="public",
    )
    _create_source_course_labels()
    _create_source_course_complaints()
    _create_source_course_fact_current()
    _create_source_participation_fact_current()
    _create_domain_aggregate_revisions()
    _apply_acl()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("DTS v2 domain facts require PostgreSQL")

    op.drop_table("domain_aggregate_revisions", schema="public")
    op.drop_table("source_participation_fact_current", schema="public")
    op.drop_table("source_course_fact_current", schema="public")
    op.drop_table("source_course_complaints", schema="public")
    op.drop_table("source_course_labels", schema="public")
    op.drop_constraint(
        "uq_complaint_rule_identity_version",
        "complaint_category_rules",
        type_="unique",
        schema="public",
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "public.dts_domain_aggregate_key_valid_v2(text,jsonb)"
    )
