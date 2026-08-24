"""Explicit deterministic test seed for the two upstream source-wide tables.

The seed never runs at application startup and never writes TiDe projections,
tasks, scores, notifications, or any legacy import table.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
import os
from typing import Any, TypeVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .db_models import LessonSourceWideRecord, TeacherSourceWideRecord


SCENARIO = "SOURCE-WIDE-TEST-V1"
ALLOWED_ENVIRONMENTS = frozenset({"local", "dev", "development", "test"})

_TEACHER_ROWS: tuple[dict[str, Any], ...] = (
    {
        "tchr_id": "TEST-SOURCE-TEACHER-001",
        "real_name": "Fictional Teacher Alpha [TEST ONLY]",
        "center_type_id": "TEST-CENTER",
        "center_type_desc": "Synthetic test center",
        "bu": "TEST-BU",
        "status": "TEST-ACTIVE",
        "status_on_date": date(2099, 1, 1),
        "job_days": 10,
        "job_month": 0.33,
        "teach_area_type": "TEST-AREA",
        "onboard_date": date(2099, 1, 1),
        "onboard_30d_end_date": date(2099, 1, 31),
        "first_open_slot_dt": date(2099, 1, 1),
        "first_booked_dt": date(2099, 1, 2),
        "first_completed_dt": date(2099, 1, 3),
        "total_booked_cnt": 2,
        "peak_booked_cnt": 1,
        "total_completed_cnt": 2,
        "peak_completed_cnt": 1,
        "absent_cnt": 0,
        "late_cnt": 1,
        "early_cnt": 1,
        "anomaly_cnt": 1,
        "perfect_cnt": 1,
        "no_notice_cnt": 0,
        "first_completed_student_cnt": 2,
        "feedback_total_eval_cnt": 1,
        "feedback_praise_cnt": 1,
        "feedback_negative_cnt": 0,
        "feedback_complaint_cnt": 0,
        "feedback_valid_complaint_cnt": 0,
        "feedback_favorite_cnt": 1,
        "feedback_block_cnt": 0,
        "total_slot_cnt": 50,
        "reg_slot_cnt": 20,
        "peak_slot_cnt": 40,
        "slot_days": 10,
        "peak_slot_days": 8,
    },
    {
        "tchr_id": "TEST-SOURCE-TEACHER-002",
        "real_name": "Fictional Teacher Beta [TEST ONLY]",
        "center_type_id": "TEST-CENTER",
        "center_type_desc": "Synthetic test center",
        "bu": "TEST-BU",
        "status": "TEST-ACTIVE",
        "status_on_date": date(2099, 2, 1),
        "job_days": 5,
        "job_month": 0.17,
        "teach_area_type": "TEST-AREA",
        "onboard_date": date(2099, 2, 1),
        "onboard_30d_end_date": date(2099, 3, 3),
        "first_open_slot_dt": date(2099, 2, 1),
        "first_booked_dt": date(2099, 2, 2),
        "first_completed_dt": date(2099, 2, 3),
        "total_booked_cnt": 2,
        "peak_booked_cnt": 0,
        "total_completed_cnt": 2,
        "peak_completed_cnt": 0,
        "absent_cnt": 0,
        "late_cnt": 0,
        "early_cnt": 0,
        "anomaly_cnt": 2,
        "perfect_cnt": 2,
        "no_notice_cnt": 0,
        "first_completed_student_cnt": 2,
        "feedback_total_eval_cnt": 1,
        "feedback_praise_cnt": 0,
        "feedback_negative_cnt": 1,
        "feedback_complaint_cnt": 1,
        "feedback_valid_complaint_cnt": 1,
        "feedback_favorite_cnt": 0,
        "feedback_block_cnt": 0,
        "total_slot_cnt": 20,
        "reg_slot_cnt": 12,
        "peak_slot_cnt": 8,
        "slot_days": 5,
        "peak_slot_days": 2,
    },
)

_LESSON_ROWS: tuple[dict[str, Any], ...] = (
    {
        "source_region": "ovs",
        "course_id": "TEST-SOURCE-LESSON-001",
        "lesson_date": date(2099, 1, 3),
        "lesson_time": time(10, 0),
        "is_peak": True,
        "teacher_id": "TEST-SOURCE-TEACHER-001",
        "student_id": "TEST-SOURCE-STUDENT-001",
        "lesson_status": "end",
        "is_late": False,
        "is_early": False,
        "negative_score": 0.0,
        "has_negative_feedback_tag": False,
        "is_blocked": False,
        "is_favorited": True,
        "has_positive_feedback_tag": True,
        "feedback_detail": "TEST ONLY: synthetic positive lesson",
        "is_camera_off": False,
        "is_cpu_usage_high": None,
        "is_network_delay_high": None,
    },
    {
        "source_region": "ovs",
        "course_id": "TEST-SOURCE-LESSON-002",
        "lesson_date": date(2099, 1, 4),
        "lesson_time": time(11, 0),
        "is_peak": False,
        "teacher_id": "TEST-SOURCE-TEACHER-001",
        "student_id": "TEST-SOURCE-STUDENT-002",
        "lesson_status": "end",
        "is_late": True,
        "is_early": True,
        "negative_score": 0.0,
        "has_negative_feedback_tag": False,
        "is_blocked": False,
        "is_favorited": False,
        "has_positive_feedback_tag": False,
        "feedback_detail": "TEST ONLY: synthetic late and early lesson",
        "is_camera_off": False,
        "is_cpu_usage_high": None,
        "is_network_delay_high": None,
    },
    {
        "source_region": "ovs",
        "course_id": "TEST-SOURCE-LESSON-003",
        "lesson_date": date(2099, 2, 3),
        "lesson_time": time(12, 0),
        "is_peak": False,
        "teacher_id": "TEST-SOURCE-TEACHER-002",
        "student_id": "TEST-SOURCE-STUDENT-003",
        "lesson_status": "end",
        "is_late": False,
        "is_early": False,
        "negative_score": 1.0,
        "has_negative_feedback_tag": True,
        "complaint_category_l1": "TEST-COMPLAINT-L1",
        "complaint_category_l2": "TEST-COMPLAINT-L2",
        "complaint_category_l3": "TEST-COMPLAINT-L3",
        "is_blocked": False,
        "is_favorited": False,
        "has_positive_feedback_tag": False,
        "feedback_detail": "TEST ONLY: synthetic complaint evidence",
        "is_camera_off": False,
        "is_cpu_usage_high": None,
        "is_network_delay_high": None,
    },
    {
        "source_region": "ovs",
        "course_id": "TEST-SOURCE-LESSON-004",
        "lesson_date": date(2099, 2, 4),
        "lesson_time": time(13, 0),
        "is_peak": False,
        "teacher_id": "TEST-SOURCE-TEACHER-002",
        "student_id": "TEST-SOURCE-STUDENT-004",
        "lesson_status": "end",
        "is_late": False,
        "is_early": False,
        "negative_score": 0.0,
        "has_negative_feedback_tag": False,
        "is_blocked": False,
        "is_favorited": False,
        "has_positive_feedback_tag": False,
        "feedback_detail": "TEST ONLY: synthetic device and network anomaly",
        "is_camera_off": True,
        "is_cpu_usage_high": None,
        "is_network_delay_high": None,
    },
)

_RecordT = TypeVar(
    "_RecordT",
    TeacherSourceWideRecord,
    LessonSourceWideRecord,
)


class SourceTestSeedCollisionError(RuntimeError):
    """A reserved synthetic ID already contains different data."""


def _same_record(actual: _RecordT, expected: _RecordT) -> bool:
    return all(
        getattr(actual, attribute.key) == getattr(expected, attribute.key)
        for attribute in expected.__mapper__.column_attrs
    )


def _add_or_validate(
    session: Session,
    model: type[_RecordT],
    primary_key: Any,
    values: Mapping[str, Any],
) -> None:
    expected = model(**dict(values))
    actual = session.get(model, primary_key)
    if actual is None:
        session.add(expected)
        return
    if not _same_record(actual, expected):
        raise SourceTestSeedCollisionError(
            f"reserved synthetic source ID has different data: {primary_key}"
        )


def seed_source_test_data(
    bind: Engine,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Validate or apply the deterministic two-table synthetic source seed."""

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env not in ALLOWED_ENVIRONMENTS:
        raise RuntimeError(
            "source test seed requires APP_ENV local/dev/development/test"
        )

    with Session(bind) as session:
        try:
            for values in _TEACHER_ROWS:
                _add_or_validate(
                    session,
                    TeacherSourceWideRecord,
                    str(values["tchr_id"]),
                    values,
                )
            for values in _LESSON_ROWS:
                _add_or_validate(
                    session,
                    LessonSourceWideRecord,
                    (
                        str(values["source_region"]),
                        str(values["course_id"]),
                    ),
                    values,
                )
            session.flush()
            if apply:
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise

    return {
        "scenario": SCENARIO,
        "applied": apply,
        "teacher_count": len(_TEACHER_ROWS),
        "lesson_count": len(_LESSON_ROWS),
    }


__all__ = [
    "ALLOWED_ENVIRONMENTS",
    "SCENARIO",
    "SourceTestSeedCollisionError",
    "seed_source_test_data",
]
