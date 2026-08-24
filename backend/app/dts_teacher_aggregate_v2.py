"""Deterministic teacher-wide aggregation for confirmed DTS v2 facts.

This module contains no database access.  The SourceWide v2 worker supplies a
complete, typed snapshot plus explicit scope coverage.  Missing coverage is
kept as ``None`` instead of being silently converted to zero or false.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import floor
from typing import Any, Literal, Mapping, Sequence

from .dts_business_rules_v2 import (
    center_type_description,
    classify_teacher_online_state,
)


EvidenceStatusV2 = Literal[
    "CONFIRMED",
    "CONFIRMED_EMPTY",
    "LEGACY_FROZEN",
    "SOURCE_MISSING",
]

_ORDINARY_PARTICIPATION_ROLES = frozenset({"NORMAL", "COMPLETION"})
_PROFILE_BU_HBT = frozenset({5, 6, 7, 11, 19, 20, 21, 22, 503})
_PROFILE_BU_OBT = frozenset({8, 10, 201})


class DtsTeacherAggregateV2Error(ValueError):
    """A typed aggregate input is internally inconsistent."""


@dataclass(frozen=True)
class TeacherProfileFactV2:
    teacher_id: str
    real_name: str | None
    center_type: object
    is_full_time: object
    course: object
    employment_status: object
    status_on_time: object
    status_off_time: object = None
    last_on_time: object = None
    source_deleted: bool = False


@dataclass(frozen=True)
class TeacherParticipationMetricV2:
    source_region: str
    source_appoint_id: str
    participation_seq: int
    teacher_id: str
    participation_role: str
    participation_status: str | None
    source_deleted: bool
    is_peak: bool | None
    lesson_local_date: date | None
    completion_student_token: str | None = None
    is_late: bool | None = None
    late_evidence_status: str = "SOURCE_MISSING"
    is_early: bool | None = None
    early_evidence_status: str = "SOURCE_MISSING"
    is_no_notice: bool | None = None
    absence_evidence_status: str = "SOURCE_MISSING"
    grading_classification: str = "SOURCE_MISSING"
    grading_evidence_status: str = "SOURCE_MISSING"
    has_complaint: bool | None = None
    has_valid_complaint: bool | None = None
    complaint_evidence_status: str = "SOURCE_MISSING"

    def __post_init__(self) -> None:
        if self.source_region not in {"dom", "ovs"}:
            raise DtsTeacherAggregateV2Error(
                "DTS_V2_TEACHER_AGGREGATE_REGION_INVALID"
            )
        if not self.source_appoint_id or self.participation_seq < 1:
            raise DtsTeacherAggregateV2Error(
                "DTS_V2_TEACHER_AGGREGATE_PARTICIPATION_INVALID"
            )
        if self.participation_role not in {
            "NORMAL",
            "COMPLETION",
            "PENDING_CORRECTION",
            "REJECTED_CORRECTION",
            "SUPERSEDED_COMPLETION",
            "VOIDED_COMPLETION",
        }:
            raise DtsTeacherAggregateV2Error(
                "DTS_V2_TEACHER_AGGREGATE_PARTICIPATION_ROLE_INVALID"
            )


@dataclass(frozen=True)
class TeacherRelationshipMetricV2:
    source_region: str
    teacher_id: str
    student_token: str
    is_favorited: bool | None
    favorite_evidence_status: str
    is_blocked: bool | None
    block_evidence_status: str


@dataclass(frozen=True)
class TeacherScheduleMetricV2:
    source_region: str
    source_schedule_id: str
    teacher_id: str
    schedule_date: date | None
    is_current_open: bool
    is_regular: bool | None
    is_peak: bool | None


@dataclass(frozen=True)
class TeacherAggregateCoverageV2:
    course_current_complete: bool
    grading_current_complete: bool
    complaint_current_complete: bool
    penalty_current_complete: bool
    absence_current_complete: bool
    relationship_current_complete: bool
    schedule_current_complete: bool
    booked_history_complete: bool
    completion_history_complete: bool
    schedule_history_complete: bool


@dataclass(frozen=True)
class TeacherFirstDateEvidenceV2:
    booked_dates: tuple[date, ...] = ()
    completed_dates: tuple[date, ...] = ()
    opened_slot_dates: tuple[date, ...] = ()
    legacy_first_booked_date: date | None = None
    legacy_first_completed_date: date | None = None
    legacy_first_open_slot_date: date | None = None


@dataclass(frozen=True)
class TeacherSourceWideProjectionV2:
    values: Mapping[str, Any]
    first_date_evidence: Mapping[str, EvidenceStatusV2]
    metric_evidence: Mapping[str, EvidenceStatusV2]


def rebuild_teacher_source_wide_v2(
    *,
    profile: TeacherProfileFactV2,
    participations: Sequence[TeacherParticipationMetricV2],
    relationships: Sequence[TeacherRelationshipMetricV2],
    schedules: Sequence[TeacherScheduleMetricV2],
    coverage: TeacherAggregateCoverageV2,
    first_dates: TeacherFirstDateEvidenceV2,
    business_date_beijing: date,
    is_cpl_tesol: bool | None,
) -> TeacherSourceWideProjectionV2:
    """Rebuild one teacher row without event deltas or inferred emptiness."""

    teacher_id = _teacher_id(profile.teacher_id)
    _require_teacher_scope(
        teacher_id,
        participations=participations,
        relationships=relationships,
        schedules=schedules,
    )
    onboard_date = _date_value(profile.status_on_time)
    status_off_date = _date_value(profile.status_off_time)
    last_on_date = _date_value(profile.last_on_time)
    online = classify_teacher_online_state(
        status=profile.employment_status,
        status_on_time=profile.status_on_time,
        business_date=business_date_beijing,
    )

    ordinary = tuple(
        row
        for row in participations
        if not row.source_deleted
        and row.participation_role in _ORDINARY_PARTICIPATION_ROLES
    )
    completed = tuple(
        row for row in ordinary if row.participation_role == "COMPLETION"
    )

    metric_evidence: dict[str, EvidenceStatusV2] = {}
    course_complete = coverage.course_current_complete
    total_booked = _known_count(len(ordinary), course_complete)
    total_completed = _known_count(len(completed), course_complete)
    absent = _known_count(
        sum(row.participation_status == "t_absent" for row in ordinary),
        course_complete,
    )
    peak_booked = _optional_boolean_count(
        ordinary,
        value=lambda row: row.is_peak,
        complete=course_complete,
    )
    peak_completed = _optional_boolean_count(
        completed,
        value=lambda row: row.is_peak,
        complete=course_complete,
    )

    penalty_complete = course_complete and coverage.penalty_current_complete
    late = _confirmed_boolean_count(
        completed,
        value=lambda row: row.is_late,
        evidence=lambda row: row.late_evidence_status,
        complete=penalty_complete,
    )
    early = _confirmed_boolean_count(
        completed,
        value=lambda row: row.is_early,
        evidence=lambda row: row.early_evidence_status,
        complete=penalty_complete,
    )
    late_early = _late_early_count(completed, complete=penalty_complete)
    anomaly = _anomaly_count(ordinary, complete=penalty_complete)
    perfect = _perfect_count(completed, complete=penalty_complete)

    absence_complete = course_complete and coverage.absence_current_complete
    no_notice = _no_notice_count(ordinary, complete=absence_complete)
    first_completed_students = _distinct_completed_students(
        completed,
        complete=course_complete,
    )

    grading_complete = course_complete and coverage.grading_current_complete
    total_eval, praise, negative = _grading_counts(
        completed,
        complete=grading_complete,
    )
    complaint_complete = course_complete and coverage.complaint_current_complete
    complaint, valid_complaint = _complaint_counts(
        completed,
        complete=complaint_complete,
    )
    favorite, blocked = _relationship_counts(
        relationships,
        complete=coverage.relationship_current_complete,
    )

    schedule_values = _schedule_counts(
        schedules,
        onboard_date=onboard_date,
        complete=coverage.schedule_current_complete,
    )

    first_booked, first_booked_evidence = _first_date(
        first_dates.booked_dates,
        legacy_value=first_dates.legacy_first_booked_date,
        history_complete=coverage.booked_history_complete,
    )
    first_completed, first_completed_evidence = _first_date(
        first_dates.completed_dates,
        legacy_value=first_dates.legacy_first_completed_date,
        history_complete=coverage.completion_history_complete,
    )
    first_open_slot, first_open_slot_evidence = _first_date(
        first_dates.opened_slot_dates,
        legacy_value=first_dates.legacy_first_open_slot_date,
        history_complete=coverage.schedule_history_complete,
    )

    job_days = _job_days(
        onboard_date=onboard_date,
        status_off_date=status_off_date,
        business_date=business_date_beijing,
    )
    completed_in_first_30_days = _completed_in_first_30_days(
        completed,
        onboard_date=onboard_date,
        complete=course_complete,
    )

    values: dict[str, Any] = {
        "tchr_id": teacher_id,
        "real_name": profile.real_name,
        "center_type_id": _optional_source_text(profile.center_type),
        "center_type_desc": center_type_description(profile.center_type),
        "bu": _teacher_bu(profile.is_full_time),
        "status": _optional_source_text(profile.employment_status),
        "status_on_date": onboard_date,
        "status_off_date": status_off_date,
        "last_on_date": last_on_date,
        "job_days": job_days,
        "job_month": None if job_days is None else floor(job_days / 30) + 1,
        "teach_area_type": _teacher_area(profile.course),
        "onboard_date": onboard_date,
        "onboard_30d_end_date": (
            None if onboard_date is None else onboard_date + timedelta(days=29)
        ),
        "first_open_slot_dt": first_open_slot,
        "first_booked_dt": first_booked,
        "first_completed_dt": first_completed,
        "total_booked_cnt": total_booked,
        "peak_booked_cnt": peak_booked,
        "total_completed_cnt": total_completed,
        "peak_completed_cnt": peak_completed,
        "absent_cnt": absent,
        "late_cnt": late,
        "early_cnt": early,
        "anomaly_cnt": anomaly,
        "perfect_cnt": perfect,
        "no_notice_cnt": no_notice,
        "first_completed_student_cnt": first_completed_students,
        "feedback_total_eval_cnt": total_eval,
        "feedback_praise_cnt": praise,
        "feedback_negative_cnt": negative,
        "feedback_complaint_cnt": complaint,
        "feedback_valid_complaint_cnt": valid_complaint,
        "feedback_favorite_cnt": favorite,
        "feedback_block_cnt": blocked,
        **schedule_values,
        "reliability_absent_rate": _rate(absent, total_booked),
        "reliability_late_rate": _rate(late, total_completed),
        "reliability_early_leave_rate": _rate(early, total_completed),
        "reliability_late_early_rate": _rate(late_early, total_completed),
        "feedback_praise_rate": _rate(praise, total_eval),
        "feedback_negative_rate": _rate(negative, total_eval),
        "feedback_complaint_rate": _rate(valid_complaint, total_completed),
        "feedback_favorite_rate": None,
        "feedback_block_rate": None,
        "feedback_eval_rate": _rate(total_eval, total_completed),
        "capacity_avg_completed_per_day": (
            None
            if completed_in_first_30_days is None
            else completed_in_first_30_days / 30
        ),
        "capacity_peak_slot_rate": _rate(
            schedule_values["peak_slot_cnt"],
            schedule_values["total_slot_cnt"],
        ),
        "capacity_key_slot_day_rate": _rate(
            schedule_values["peak_slot_days"],
            schedule_values["slot_days"],
        ),
        "is_cpl_tesol": is_cpl_tesol,
        "is_self_introduce": None,
        "online_status": online.state,
        "online_status_evidence_status": online.evidence_status,
    }

    for field in (
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
    ):
        metric_evidence[field] = (
            "CONFIRMED" if values[field] is not None else "SOURCE_MISSING"
        )

    return TeacherSourceWideProjectionV2(
        values=values,
        first_date_evidence={
            "first_booked_dt_evidence_status": first_booked_evidence,
            "first_completed_dt_evidence_status": first_completed_evidence,
            "first_open_slot_dt_evidence_status": first_open_slot_evidence,
        },
        metric_evidence=metric_evidence,
    )


def _teacher_id(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_TEACHER_ID_INVALID"
        )
    return value


def _require_teacher_scope(
    teacher_id: str,
    *,
    participations: Sequence[TeacherParticipationMetricV2],
    relationships: Sequence[TeacherRelationshipMetricV2],
    schedules: Sequence[TeacherScheduleMetricV2],
) -> None:
    if any(row.teacher_id != teacher_id for row in participations):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_PARTICIPATION_TEACHER_MISMATCH"
        )
    if any(row.teacher_id != teacher_id for row in relationships):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_RELATION_TEACHER_MISMATCH"
        )
    if any(row.teacher_id != teacher_id for row in schedules):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_SCHEDULE_TEACHER_MISMATCH"
        )
    participation_keys = {
        (row.source_region, row.source_appoint_id, row.participation_seq)
        for row in participations
    }
    if len(participation_keys) != len(participations):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_PARTICIPATION_DUPLICATE"
        )
    relationship_keys = {
        (row.source_region, row.student_token) for row in relationships
    }
    if len(relationship_keys) != len(relationships):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_RELATION_DUPLICATE"
        )
    schedule_keys = {
        (row.source_region, row.source_schedule_id) for row in schedules
    }
    if len(schedule_keys) != len(schedules):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_SCHEDULE_DUPLICATE"
        )


def _known_count(value: int, complete: bool) -> int | None:
    return value if complete else None


def _optional_boolean_count(
    rows: Sequence[Any],
    *,
    value: Any,
    complete: bool,
) -> int | None:
    if not complete or any(value(row) is None for row in rows):
        return None
    return sum(value(row) is True for row in rows)


def _confirmed_boolean_count(
    rows: Sequence[Any],
    *,
    value: Any,
    evidence: Any,
    complete: bool,
) -> int | None:
    if not complete or any(
        evidence(row) != "CONFIRMED" or value(row) is None for row in rows
    ):
        return None
    return sum(value(row) is True for row in rows)


def _late_early_count(
    completed: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> int | None:
    if not complete or any(
        row.late_evidence_status != "CONFIRMED"
        or row.early_evidence_status != "CONFIRMED"
        or row.is_late is None
        or row.is_early is None
        for row in completed
    ):
        return None
    return sum(row.is_late is True or row.is_early is True for row in completed)


def _anomaly_count(
    ordinary: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> int | None:
    if not complete:
        return None
    total = 0
    for row in ordinary:
        if row.participation_status == "t_absent":
            total += 1
            continue
        if row.participation_role != "COMPLETION":
            continue
        if (
            row.late_evidence_status != "CONFIRMED"
            or row.early_evidence_status != "CONFIRMED"
            or row.is_late is None
            or row.is_early is None
        ):
            return None
        total += int(row.is_late or row.is_early)
    return total


def _perfect_count(
    completed: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> int | None:
    if not complete or any(
        row.late_evidence_status != "CONFIRMED"
        or row.early_evidence_status != "CONFIRMED"
        or row.is_late is None
        or row.is_early is None
        for row in completed
    ):
        return None
    return sum(row.is_late is False and row.is_early is False for row in completed)


def _no_notice_count(
    ordinary: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> int | None:
    absent = tuple(
        row for row in ordinary if row.participation_status == "t_absent"
    )
    if not complete or any(
        row.absence_evidence_status != "CONFIRMED"
        or row.is_no_notice is None
        for row in absent
    ):
        return None
    return sum(row.is_no_notice is True for row in absent)


def _distinct_completed_students(
    completed: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> int | None:
    if not complete or any(row.completion_student_token is None for row in completed):
        return None
    # DOM tokens and OVS identifiers live in different identity namespaces.
    # Keeping region in the key prevents a coincidentally equal source value
    # from collapsing two distinct students during the global merge.
    return len(
        {
            (row.source_region, row.completion_student_token)
            for row in completed
        }
    )


def _grading_counts(
    completed: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> tuple[int | None, int | None, int | None]:
    # The frozen grading definition is DOM-only.  OVS completions still count
    # as lessons, but they neither require DOM grading coverage nor contribute
    # to the grading numerator/denominator.
    domestic = tuple(row for row in completed if row.source_region == "dom")
    if not complete or any(
        row.grading_evidence_status != "CONFIRMED" for row in domestic
    ):
        return None, None, None
    classified = tuple(
        row.grading_classification
        for row in domestic
        if row.grading_classification in {"POSITIVE", "NEGATIVE"}
    )
    return (
        len(classified),
        sum(value == "POSITIVE" for value in classified),
        sum(value == "NEGATIVE" for value in classified),
    )


def _complaint_counts(
    completed: Sequence[TeacherParticipationMetricV2], *, complete: bool
) -> tuple[int | None, int | None]:
    if not complete or any(
        row.complaint_evidence_status != "CONFIRMED"
        or row.has_complaint is None
        or row.has_valid_complaint is None
        for row in completed
    ):
        return None, None
    return (
        sum(row.has_complaint is True for row in completed),
        sum(row.has_valid_complaint is True for row in completed),
    )


def _relationship_counts(
    rows: Sequence[TeacherRelationshipMetricV2], *, complete: bool
) -> tuple[int | None, int | None]:
    return (
        _relationship_count(
            rows,
            complete=complete,
            value=lambda row: row.is_favorited,
            evidence=lambda row: row.favorite_evidence_status,
        ),
        _relationship_count(
            rows,
            complete=complete,
            value=lambda row: row.is_blocked,
            evidence=lambda row: row.block_evidence_status,
        ),
    )


def _relationship_count(
    rows: Sequence[TeacherRelationshipMetricV2],
    *,
    complete: bool,
    value: Any,
    evidence: Any,
) -> int | None:
    if not complete or any(
        evidence(row) != "CONFIRMED" or value(row) is None for row in rows
    ):
        return None
    return len(
        {
            (row.source_region, row.student_token)
            for row in rows
            if value(row) is True
        }
    )


def _schedule_counts(
    rows: Sequence[TeacherScheduleMetricV2],
    *,
    onboard_date: date | None,
    complete: bool,
) -> dict[str, int | None]:
    empty = {
        "total_slot_cnt": None,
        "reg_slot_cnt": None,
        "peak_slot_cnt": None,
        "slot_days": None,
        "peak_slot_days": None,
    }
    if not complete or onboard_date is None:
        return empty
    end_date = onboard_date + timedelta(days=29)
    active = tuple(
        row
        for row in rows
        if row.is_current_open
        and row.schedule_date is not None
        and onboard_date <= row.schedule_date <= end_date
    )
    if any(
        row.is_current_open and row.schedule_date is None for row in rows
    ):
        return empty
    total_slot = len({(row.source_region, row.source_schedule_id) for row in active})
    regular = (
        None
        if any(row.is_regular is None for row in active)
        else sum(row.is_regular is True for row in active)
    )
    peak = (
        None
        if any(row.is_peak is None for row in active)
        else sum(row.is_peak is True for row in active)
    )
    days = {row.schedule_date for row in active}
    peak_days = (
        None
        if any(row.is_peak is None for row in active)
        else len({row.schedule_date for row in active if row.is_peak})
    )
    return {
        "total_slot_cnt": total_slot,
        "reg_slot_cnt": regular,
        "peak_slot_cnt": peak,
        "slot_days": len(days),
        "peak_slot_days": peak_days,
    }


def _first_date(
    current_candidates: Sequence[date],
    *,
    legacy_value: date | None,
    history_complete: bool,
) -> tuple[date | None, EvidenceStatusV2]:
    candidates = tuple(current_candidates)
    if any(not isinstance(value, date) for value in candidates):
        raise DtsTeacherAggregateV2Error(
            "DTS_V2_TEACHER_AGGREGATE_FIRST_DATE_INVALID"
        )
    current = min(candidates) if candidates else None
    if legacy_value is not None and (current is None or legacy_value < current):
        return legacy_value, "LEGACY_FROZEN"
    if current is not None:
        return current, "CONFIRMED"
    if legacy_value is not None:
        return legacy_value, "LEGACY_FROZEN"
    return (None, "CONFIRMED_EMPTY" if history_complete else "SOURCE_MISSING")


def _completed_in_first_30_days(
    completed: Sequence[TeacherParticipationMetricV2],
    *,
    onboard_date: date | None,
    complete: bool,
) -> int | None:
    if not complete or onboard_date is None:
        return None
    if any(row.lesson_local_date is None for row in completed):
        return None
    end_date = onboard_date + timedelta(days=29)
    return sum(
        onboard_date <= row.lesson_local_date <= end_date
        for row in completed
        if row.lesson_local_date is not None
    )


def _rate(
    numerator: int | float | None,
    denominator: int | float | None,
) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _job_days(
    *,
    onboard_date: date | None,
    status_off_date: date | None,
    business_date: date,
) -> int | None:
    if onboard_date is None:
        return None
    value = ((status_off_date or business_date) - onboard_date).days
    return value if value >= 0 else None


def _teacher_bu(value: object) -> str | None:
    integer = _strict_integer(value)
    if integer in _PROFILE_BU_HBT:
        return "HBT"
    if integer in _PROFILE_BU_OBT:
        return "OBT"
    return None


def _teacher_area(value: object) -> str | None:
    return teacher_expected_source_region_v2(value)


def teacher_expected_source_region_v2(value: object) -> str | None:
    """Map the authoritative DOM teacher ``course`` value to its DTS region.

    Blank or non-text values carry no regional evidence.  Any non-blank value
    outside the two explicitly overseas pools is domestic by contract.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.casefold()
    if "global_cn" in normalized or "global_pool" in normalized:
        return "ovs"
    return "dom"


def _strict_integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value and value.strip() == value:
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_source_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        ).date()
    except ValueError:
        return None


__all__ = [
    "DtsTeacherAggregateV2Error",
    "TeacherAggregateCoverageV2",
    "TeacherFirstDateEvidenceV2",
    "TeacherParticipationMetricV2",
    "TeacherProfileFactV2",
    "TeacherRelationshipMetricV2",
    "TeacherScheduleMetricV2",
    "TeacherSourceWideProjectionV2",
    "rebuild_teacher_source_wide_v2",
]
