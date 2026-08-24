"""Source-wide-table field contracts and incremental dependency routing.

The two contracts in this module describe the source tables owned by the
upstream monitoring service.  They deliberately do not describe derived TiDe
tables.  A source field either names its smallest downstream recomputation or
states why the current v1 business rules do not consume it.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


TEACHER_SOURCE_TABLE = "teacher_source_wide"
LESSON_SOURCE_TABLE = "lesson_source_wide"


@dataclass(frozen=True)
class SourceFieldDependency:
    """Routing metadata for one source field."""

    authority: str
    handlers: tuple[str, ...]
    recompute_scope: str
    no_downstream_reason: str | None


TEACHER_CSV_FIELDS: tuple[str, ...] = (
    "tchr_id",
    "real_name",
    "center_type_id",
    "center_type_desc",
    "bu",
    "status",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_days",
    "job_month",
    "teach_area_type",
    "onboard_date",
    "onboard_30d_end_date",
    "first_open_slot_dt",
    "first_booked_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "total_completed_cnt",
    "peak_completed_cnt",
    "absent_cnt",
    "late_cnt",
    "early_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "feedback_total_eval_cnt",
    "feedback_praise_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_favorite_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "peak_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
)

# The confirmed v1.5 contract contains the 53 metric/profile fields above plus
# the two nullable G01 source facts below, for 55 columns in total.  G01
# consumes only ``is_cpl_tesol``; ``is_self_introduce`` intentionally remains
# NULL until the business enables a source.  The established constant name is
# retained for compatibility with existing schema checks.
TEACHER_G01_STATUS_FIELDS: tuple[str, ...] = (
    "is_cpl_tesol",
    "is_self_introduce",
)

TEACHER_SOURCE_FIELDS: tuple[str, ...] = (
    TEACHER_CSV_FIELDS + TEACHER_G01_STATUS_FIELDS
)


# The confirmed lesson source contract has exactly 22 columns.  The legacy
# ``是否复约`` column is not part of this contract.
LESSON_SOURCE_FIELDS: tuple[str, ...] = (
    "课程id",
    "上课日期",
    "上课时间",
    "是否高峰",
    "老师id",
    "学员id",
    "课程状态",
    "缺席原因明细",
    "迟到",
    "早退",
    "差评分",
    "差评标签",
    "投诉一级分类",
    "投诉二级分类",
    "投诉三级分类",
    "是否拉黑",
    "收藏",
    "好评标签",
    "评价详情",
    "未开摄像头",
    "cpu占用过高",
    "网络延迟过高",
)


def _dependency(
    authority: str,
    handlers: tuple[str, ...],
    recompute_scope: str,
) -> SourceFieldDependency:
    return SourceFieldDependency(
        authority=authority,
        handlers=handlers,
        recompute_scope=recompute_scope,
        no_downstream_reason=None,
    )


def _no_downstream(authority: str, reason: str) -> SourceFieldDependency:
    return SourceFieldDependency(
        authority=authority,
        handlers=(),
        recompute_scope="NONE",
        no_downstream_reason=reason,
    )


_TEACHER_PROFILE = _dependency(
    "TEACHER_PROFILE_SOURCE",
    ("TEACHER_PROFILE",),
    "SINGLE_TEACHER",
)

_TEACHER_NO_DOWNSTREAM_DEFAULT = (
    "Retained only to mirror the 53-column upstream teacher mapping; "
    "current v1 business rules do not consume it."
)

TEACHER_NO_DOWNSTREAM_FIELDS: tuple[str, ...] = (
    "center_type_id",
    "center_type_desc",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_month",
    "first_open_slot_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "feedback_total_eval_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
)

_teacher_no_downstream_reasons = {
    field: _TEACHER_NO_DOWNSTREAM_DEFAULT for field in TEACHER_NO_DOWNSTREAM_FIELDS
}
_teacher_no_downstream_reasons.update(
    {
        "perfect_cnt": (
            "Retained only for reconciliation. Current perfect-completion scoring "
            "is derived from lesson status, late, and early fields."
        ),
        "feedback_valid_complaint_cnt": (
            "Current L0 qualification evidence is derived from exact lesson-level "
            "complaint severity, not this teacher aggregate."
        ),
    }
)

_teacher_dependencies: dict[str, SourceFieldDependency] = {
    "tchr_id": _dependency(
        "TEACHER_IDENTITY_SOURCE",
        ("TEACHER_IDENTITY", "FIXED_TASK_BASELINE"),
        "SINGLE_TEACHER_IDENTITY",
    ),
    "real_name": _TEACHER_PROFILE,
    "bu": _TEACHER_PROFILE,
    "status": _TEACHER_PROFILE,
    "job_days": _TEACHER_PROFILE,
    "teach_area_type": _TEACHER_PROFILE,
    "onboard_date": _TEACHER_PROFILE,
    "onboard_30d_end_date": _TEACHER_PROFILE,
    "first_booked_dt": _TEACHER_PROFILE,
    "total_completed_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_PROFILE", "TEACHER_SOURCE_STATE"),
        "SINGLE_TEACHER",
    ),
    "peak_completed_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_RELIABILITY", "TEACHER_TOTAL", "TEACHER_QUALIFICATION"),
        "SINGLE_TEACHER_RELIABILITY",
    ),
    "feedback_praise_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_USER_FEEDBACK", "TEACHER_TOTAL", "TEACHER_QUALIFICATION"),
        "SINGLE_TEACHER_USER_FEEDBACK",
    ),
    "feedback_favorite_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_USER_FEEDBACK", "TEACHER_TOTAL", "TEACHER_QUALIFICATION"),
        "SINGLE_TEACHER_USER_FEEDBACK",
    ),
    "peak_slot_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_CAPACITY", "TEACHER_TOTAL", "TEACHER_QUALIFICATION"),
        "SINGLE_TEACHER_CAPACITY",
    ),
    "late_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_QUALIFICATION",),
        "SINGLE_TEACHER_QUALIFICATION",
    ),
    "early_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_QUALIFICATION",),
        "SINGLE_TEACHER_QUALIFICATION",
    ),
    "absent_cnt": _dependency(
        "TEACHER_AGGREGATE_SOURCE",
        ("TEACHER_QUALIFICATION",),
        "SINGLE_TEACHER_QUALIFICATION",
    ),
    "is_cpl_tesol": _TEACHER_PROFILE,
    "is_self_introduce": _TEACHER_PROFILE,
    **{
        field: _no_downstream("TEACHER_UPSTREAM_SOURCE_ONLY", reason)
        for field, reason in _teacher_no_downstream_reasons.items()
    },
}

TEACHER_FIELD_DEPENDENCIES: Mapping[str, SourceFieldDependency] = MappingProxyType(
    {field: _teacher_dependencies[field] for field in TEACHER_SOURCE_FIELDS}
)


_lesson_dependencies: dict[str, SourceFieldDependency] = {
    "课程id": _dependency(
        "LESSON_IDENTITY_SOURCE",
        (
            "LESSON_SCORE",
            "LESSON_TRIGGER",
            "TEACHER_RELIABILITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_COMPONENTS",
    ),
    "老师id": _dependency(
        "LESSON_IDENTITY_SOURCE",
        (
            "LESSON_SCORE",
            "LESSON_TRIGGER",
            "FAVORITE_PAIR",
            "NEGATIVE_LABEL",
            "BLACKLIST_TEACHER",
            "COMPLAINT_TEACHER",
            "TEACHER_RELIABILITY",
            "TEACHER_CLASS_QUALITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_OLD_NEW_TEACHER_COMPONENTS",
    ),
    "学员id": _dependency(
        "LESSON_FACT_SOURCE",
        ("FAVORITE_PAIR", "BLACKLIST_TEACHER"),
        "SINGLE_TEACHER_FEEDBACK_SETS",
    ),
    "上课日期": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_SCORE", "FAVORITE_PAIR", "NEGATIVE_LABEL", "BLACKLIST_TEACHER"),
        "SINGLE_LESSON_AND_TEACHER_FEEDBACK_SETS",
    ),
    "上课时间": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_SCORE", "FAVORITE_PAIR", "NEGATIVE_LABEL", "BLACKLIST_TEACHER"),
        "SINGLE_LESSON_AND_TEACHER_FEEDBACK_SETS",
    ),
    "课程状态": _dependency(
        "LESSON_FACT_SOURCE",
        (
            "LESSON_SCORE",
            "FAVORITE_PAIR",
            "TEACHER_RELIABILITY",
            "TEACHER_CLASS_QUALITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_COMPONENTS",
    ),
    "是否高峰": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_SCORE",),
        "SINGLE_LESSON",
    ),
    "缺席原因明细": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER",),
        "SINGLE_LESSON",
    ),
    "迟到": _dependency(
        "LESSON_FACT_SOURCE",
        (
            "LESSON_SCORE",
            "LESSON_TRIGGER",
            "TEACHER_RELIABILITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_RELIABILITY",
    ),
    "早退": _dependency(
        "LESSON_FACT_SOURCE",
        (
            "LESSON_SCORE",
            "LESSON_TRIGGER",
            "TEACHER_RELIABILITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_RELIABILITY",
    ),
    "差评标签": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER", "NEGATIVE_LABEL"),
        "SINGLE_TEACHER_NEGATIVE_LABEL_SET",
    ),
    "评价详情": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER", "NEGATIVE_LABEL"),
        "SINGLE_TEACHER_NEGATIVE_LABEL_SET",
    ),
    "投诉一级分类": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER", "COMPLAINT_TEACHER", "TEACHER_QUALIFICATION"),
        "SINGLE_LESSON_AND_TEACHER_COMPLAINTS",
    ),
    "投诉二级分类": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER", "COMPLAINT_TEACHER", "TEACHER_QUALIFICATION"),
        "SINGLE_LESSON_AND_TEACHER_COMPLAINTS",
    ),
    "投诉三级分类": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_TRIGGER", "COMPLAINT_TEACHER", "TEACHER_QUALIFICATION"),
        "SINGLE_LESSON_AND_TEACHER_COMPLAINTS",
    ),
    "是否拉黑": _dependency(
        "LESSON_FACT_SOURCE",
        ("BLACKLIST_TEACHER",),
        "SINGLE_TEACHER_BLACKLIST_SET",
    ),
    "收藏": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_SCORE", "FAVORITE_PAIR"),
        "SINGLE_TEACHER_FAVORITE_SET",
    ),
    "好评标签": _dependency(
        "LESSON_FACT_SOURCE",
        ("LESSON_SCORE",),
        "SINGLE_LESSON",
    ),
    "未开摄像头": _dependency(
        "LESSON_FACT_SOURCE",
        (
            "LESSON_SCORE",
            "LESSON_TRIGGER",
            "TEACHER_CLASS_QUALITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_CLASS_QUALITY",
    ),
    # These are reserved nullable columns, not currently valid facts.  Their
    # legacy recompute routes stay active so the retirement migration's NULL
    # updates can reverse stale score/qualification projections.  A future
    # versioned source migration must replace both the storage lock and this
    # authority before either column may carry evidence again.
    "cpu占用过高": _dependency(
        "LESSON_RESERVED_NULL_SOURCE",
        (
            "LESSON_SCORE",
            "TEACHER_CLASS_QUALITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_CLASS_QUALITY",
    ),
    "网络延迟过高": _dependency(
        "LESSON_RESERVED_NULL_SOURCE",
        (
            "LESSON_SCORE",
            "TEACHER_CLASS_QUALITY",
            "TEACHER_TOTAL",
            "TEACHER_QUALIFICATION",
        ),
        "SINGLE_LESSON_AND_TEACHER_CLASS_QUALITY",
    ),
    "差评分": _no_downstream(
        "LESSON_UPSTREAM_SOURCE_ONLY",
        "Retained as an upstream lesson fact; current v1 rules neither score nor trigger from it.",
    ),
}

LESSON_FIELD_DEPENDENCIES: Mapping[str, SourceFieldDependency] = MappingProxyType(
    {field: _lesson_dependencies[field] for field in LESSON_SOURCE_FIELDS}
)

SOURCE_FIELD_DEPENDENCIES: Mapping[
    str, Mapping[str, SourceFieldDependency]
] = MappingProxyType(
    {
        TEACHER_SOURCE_TABLE: TEACHER_FIELD_DEPENDENCIES,
        LESSON_SOURCE_TABLE: LESSON_FIELD_DEPENDENCIES,
    }
)


def _validate_contract(
    source_name: str,
    fields: tuple[str, ...],
    dependencies: Mapping[str, SourceFieldDependency],
) -> None:
    if len(fields) != len(set(fields)):
        raise RuntimeError(f"{source_name} source contract contains duplicate fields")

    expected = set(fields)
    actual = set(dependencies)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise RuntimeError(
            f"{source_name} dependency coverage mismatch: missing={missing}, extra={extra}"
        )

    for field in fields:
        dependency = dependencies[field]
        if not dependency.authority:
            raise RuntimeError(f"{source_name}.{field} has no authority")
        if len(dependency.handlers) != len(set(dependency.handlers)):
            raise RuntimeError(f"{source_name}.{field} repeats a handler")
        if dependency.handlers:
            if dependency.recompute_scope == "NONE":
                raise RuntimeError(
                    f"{source_name}.{field} has handlers but no recompute scope"
                )
            if dependency.no_downstream_reason is not None:
                raise RuntimeError(
                    f"{source_name}.{field} has handlers and a no-downstream reason"
                )
        else:
            if dependency.recompute_scope != "NONE":
                raise RuntimeError(
                    f"{source_name}.{field} has no handlers but a recompute scope"
                )
            if not dependency.no_downstream_reason:
                raise RuntimeError(
                    f"{source_name}.{field} has no handlers and no reason"
                )


def validate_source_contracts() -> None:
    """Fail fast when a source field is unclassified or over-classified."""

    if len(TEACHER_CSV_FIELDS) != 53:
        raise RuntimeError("teacher mapping contract must contain exactly 53 fields")
    if len(TEACHER_G01_STATUS_FIELDS) != 2:
        raise RuntimeError("teacher G01 source contract must contain exactly 2 fields")
    if len(TEACHER_SOURCE_FIELDS) != 55:
        raise RuntimeError("teacher source table contract must contain exactly 55 fields")
    if len(LESSON_SOURCE_FIELDS) != 22:
        raise RuntimeError("lesson source contract must contain exactly 22 fields")
    if "是否复约" in LESSON_SOURCE_FIELDS:
        raise RuntimeError("the 22-column lesson source must not contain 是否复约")

    # Validate the raw registries as well as the ordered public mappings.  The
    # latter intentionally follows CSV order, so checking only it could hide a
    # mistakenly added registry key.
    _validate_contract(
        "teacher registry",
        TEACHER_SOURCE_FIELDS,
        _teacher_dependencies,
    )
    _validate_contract(
        "lesson registry",
        LESSON_SOURCE_FIELDS,
        _lesson_dependencies,
    )
    _validate_contract(
        "teacher",
        TEACHER_SOURCE_FIELDS,
        TEACHER_FIELD_DEPENDENCIES,
    )
    _validate_contract(
        "lesson",
        LESSON_SOURCE_FIELDS,
        LESSON_FIELD_DEPENDENCIES,
    )


validate_source_contracts()
