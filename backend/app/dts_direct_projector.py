"""Direct DTS-event projection into the two source-wide tables.

This mode intentionally does not persist a general source-row mirror, event
ledger or dirty-key queue.  The only reference rows retained in
``dts_source_rows`` are the small shared complaint-category dictionary and the
domestic HMAC fingerprint contract.

The direct mode is deliberately simpler than :mod:`dts_wide_projector`:

* course and teacher facts are last-event-wins;
* deleting a child fact clears its current target value and does not resurrect
  an older child row;
* schedule capacity is monotonic inside the reset window.  A slot contributes
  exactly once when it transitions from a non-open image to ``status='on'``;
  later closing/deletion does not decrement the counters.

These semantics are suitable only for a clean DTS reset/baseline boundary.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from .db_models import (
    DtsSourceRowRecord,
    LessonSourceWideRecord,
    TeacherSourceWideRecord,
)
from .dts_source_consumer import (
    SOURCE_FIELD_WHITELIST,
    DtsChangeEvent,
    DtsRecordError,
    is_peak_lesson,
    source_table_suffix,
    student_subject,
)
from .dts_wide_projector import (
    DtsWideProjectionError,
    DtsWideProjectionSettings,
)


_UTC_PLUS_8 = timezone(timedelta(hours=8))
_COMPLETED_STATUS = "end"
_ABSENT_STATUS = "t_absent"
_HBT_CODES = frozenset({5, 6, 7, 11, 19, 20, 21, 22, 503})
_OBT_CODES = frozenset({8, 10, 201})
_CENTER_DESCRIPTIONS = {0: "HBT", 1: "CBT", 5: "TBT", 6: "HBT"}
_NO_NOTICE_DETAIL = "Unfilled Lesson Memo"
_LESSON_COUNTER_FIELDS = (
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
    "feedback_total_eval_cnt",
    "feedback_praise_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
)
_DISTINCT_STUDENT_FIELDS = (
    "first_completed_student_cnt",
    "feedback_favorite_cnt",
    "feedback_block_cnt",
)


@dataclass
class _DirectCounts:
    events: int = 0
    ignored: int = 0
    lesson_upserts: int = 0
    lesson_deletes: int = 0
    teacher_upserts: int = 0
    teacher_deletes: int = 0
    teacher_delta_updates: int = 0
    slot_activations: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "events": self.events,
            "ignored": self.ignored,
            "lesson_upserts": self.lesson_upserts,
            "lesson_deletes": self.lesson_deletes,
            "teacher_upserts": self.teacher_upserts,
            "teacher_deletes": self.teacher_deletes,
            "teacher_delta_updates": self.teacher_delta_updates,
            "slot_activations": self.slot_activations,
        }


def _string(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        rendered = str(value).strip().replace("Z", "+00:00")
        if not rendered:
            return None
        try:
            parsed = datetime.fromisoformat(rendered.replace(" ", "T", 1))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(_UTC_PLUS_8).replace(tzinfo=None)
    return parsed


def _date(value: Any) -> date | None:
    parsed = _datetime(value)
    if parsed is not None:
        return parsed.date()
    if isinstance(value, date):
        return value
    return None


def _time(value: Any) -> time | None:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    parsed = _datetime(value)
    if parsed is not None:
        return parsed.time()
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        return time.fromisoformat(rendered).replace(tzinfo=None)
    except ValueError:
        return None


def _safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _teacher_area(course_value: Any) -> str | None:
    if course_value is None:
        return None
    rendered = str(course_value)
    return "ovs" if "global_cn" in rendered or "global_pool" in rendered else "dmo"


def _teacher_bu(value: Any) -> str | None:
    code = _int(value)
    if code in _HBT_CODES:
        return "HBT"
    if code in _OBT_CODES:
        return "OBT"
    return None


def _slot_is_open(row: Mapping[str, Any] | None) -> bool:
    return bool(row is not None and str(row.get("status") or "").strip().lower() == "on")


def slot_activation(event: DtsChangeEvent) -> Mapping[str, Any] | None:
    """Return the newly-opened slot image, never a close/delete image."""

    if event.operation == "DELETE" or not _slot_is_open(event.after):
        return None
    if _slot_is_open(event.before):
        return None
    return event.after


def schedule_slot_is_peak(
    teacher_area: str | None,
    schedule_date: date | None,
    time_slot: int | None,
) -> bool | None:
    """Apply the confirmed half-hour slot ranges without parsing slot as time."""

    if teacher_area is None or schedule_date is None or time_slot is None:
        return None
    weekend = schedule_date.isoweekday() in {6, 7}
    if teacher_area == "ovs":
        return (
            1 <= time_slot <= 12
            or 37 <= time_slot <= 48
            or (weekend and 19 <= time_slot <= 24)
        )
    return 37 <= time_slot <= 44 or (weekend and 19 <= time_slot <= 24)


def direct_projection_event(event: DtsChangeEvent) -> DtsChangeEvent:
    """Return the minimum whitelisted before/after images used by direct mode.

    DTS UPDATE images may be sparse.  Combining the two sides in both
    directions reconstructs the unchanged whitelisted fields while retaining
    the old value in ``before`` and the new value in ``after`` for every
    changed field.  No non-contract field is allowed to reach projection SQL.
    """

    suffix = source_table_suffix(event)
    if suffix is None:
        return event
    allowed = SOURCE_FIELD_WHITELIST[suffix]

    def filtered(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {name: value for name, value in row.items() if name in allowed}

    before = filtered(event.before)
    after = filtered(event.after)
    if event.operation == "UPDATE":
        before_id = (before or {}).get("id")
        after_id = (after or {}).get("id")
        if (
            before_id is not None
            and after_id is not None
            and str(before_id).strip() != str(after_id).strip()
        ):
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED")
        old_image = dict(after or {})
        old_image.update(before or {})
        new_image = dict(before or {})
        new_image.update(after or {})
        before = old_image
        after = new_image

    return DtsChangeEvent(
        source_region=event.source_region,
        topic=event.topic,
        partition=event.partition,
        offset=event.offset,
        record_id=event.record_id,
        source_timestamp=event.source_timestamp,
        source_txid=event.source_txid,
        source_position=event.source_position,
        operation=event.operation,
        database_name=event.database_name,
        schema_name=event.schema_name,
        table_name=event.table_name,
        before=before,
        after=after,
    )


class DtsDirectWideProjector:
    """Apply one normalized DTS event directly inside its checkpoint transaction."""

    def __init__(
        self,
        *,
        settings: DtsWideProjectionSettings | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings or DtsWideProjectionSettings.from_env(environ)
        self._counts = _DirectCounts()

    def drain_counts(self) -> dict[str, int]:
        result = self._counts.as_dict()
        self._counts = _DirectCounts()
        return result

    def apply(self, connection: Any, event: DtsChangeEvent) -> None:
        event = direct_projection_event(event)
        self._counts.events += 1
        suffix = source_table_suffix(event)
        if suffix is None:
            self._counts.ignored += 1
            return
        handler = getattr(self, f"_apply_{suffix}", None)
        if handler is None:
            self._counts.ignored += 1
            return
        handler(connection, event)

    def apply_batch(
        self,
        connection: Any,
        events: Sequence[DtsChangeEvent],
    ) -> None:
        """Apply each event directly while sharing the checkpoint transaction."""

        for event in events:
            self.apply(connection, event)

    def _teacher_in_cohort(self, onboard: date | None) -> bool:
        return bool(
            onboard is not None
            and onboard >= self.settings.cohort_start
            and (
                self.settings.cohort_end_exclusive is None
                or onboard < self.settings.cohort_end_exclusive
            )
        )

    def _apply_teacher(self, connection: Any, event: DtsChangeEvent) -> None:
        row = event.after if event.operation != "DELETE" else event.before
        teacher_id = _string((row or {}).get("id"))
        if teacher_id is None:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_ID_REQUIRED")
        onboard = _date((row or {}).get("status_on_time"))
        if event.operation == "DELETE" or not self._teacher_in_cohort(onboard):
            lesson = LessonSourceWideRecord.__table__
            teacher = TeacherSourceWideRecord.__table__
            deleted_lessons = connection.execute(
                delete(lesson).where(lesson.c["老师id"] == teacher_id)
            ).rowcount or 0
            deleted_teacher = connection.execute(
                delete(teacher).where(teacher.c.tchr_id == teacher_id)
            ).rowcount or 0
            self._counts.lesson_deletes += int(deleted_lessons)
            self._counts.teacher_deletes += int(deleted_teacher)
            return

        assert row is not None
        status_off = _date(row.get("status_off_time"))
        snapshot_date = datetime.now(_UTC_PLUS_8).date()
        job_days = ((status_off or snapshot_date) - onboard).days
        values = self._empty_teacher_values(teacher_id)
        values.update(
            {
                "real_name": _string(row.get("real_name")),
                "center_type_id": _string(row.get("center_type")),
                "center_type_desc": _CENTER_DESCRIPTIONS.get(_int(row.get("center_type"))),
                "bu": _teacher_bu(row.get("is_full_time")),
                "status": _string(row.get("status")),
                "status_on_date": onboard,
                "status_off_date": status_off,
                "last_on_date": _date(row.get("last_on_time")),
                "job_days": job_days,
                "job_month": math.floor(job_days / 30) + 1 if job_days >= 0 else None,
                "teach_area_type": _teacher_area(row.get("course")),
                "onboard_date": onboard,
                "onboard_30d_end_date": onboard + timedelta(days=29),
            }
        )
        target = TeacherSourceWideRecord.__table__
        existing = connection.execute(
            select(target).where(target.c.tchr_id == teacher_id).with_for_update()
        ).mappings().one_or_none()
        if existing is not None:
            for name in (
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
                "is_cpl_tesol",
                "is_self_introduce",
            ):
                values[name] = existing[name]
        self._upsert(connection, target, values, primary_key="tchr_id")
        self._counts.teacher_upserts += 1

    @staticmethod
    def _empty_teacher_values(teacher_id: str) -> dict[str, Any]:
        return {
            "tchr_id": teacher_id,
            "real_name": None,
            "center_type_id": None,
            "center_type_desc": None,
            "bu": None,
            "status": None,
            "status_on_date": None,
            "status_off_date": None,
            "last_on_date": None,
            "job_days": None,
            "job_month": None,
            "teach_area_type": None,
            "onboard_date": None,
            "onboard_30d_end_date": None,
            "first_open_slot_dt": None,
            "first_booked_dt": None,
            "first_completed_dt": None,
            "total_booked_cnt": 0,
            "peak_booked_cnt": 0,
            "total_completed_cnt": 0,
            "peak_completed_cnt": 0,
            "absent_cnt": 0,
            "late_cnt": 0,
            "early_cnt": 0,
            "anomaly_cnt": 0,
            "perfect_cnt": 0,
            "no_notice_cnt": 0,
            "first_completed_student_cnt": 0,
            "feedback_total_eval_cnt": 0,
            "feedback_praise_cnt": 0,
            "feedback_negative_cnt": 0,
            "feedback_complaint_cnt": 0,
            "feedback_valid_complaint_cnt": 0,
            "feedback_favorite_cnt": 0,
            "feedback_block_cnt": 0,
            "total_slot_cnt": 0,
            "reg_slot_cnt": 0,
            "peak_slot_cnt": 0,
            "slot_days": 0,
            "peak_slot_days": 0,
            "reliability_absent_rate": None,
            "reliability_late_rate": None,
            "reliability_early_leave_rate": None,
            "reliability_late_early_rate": None,
            "feedback_praise_rate": None,
            "feedback_negative_rate": None,
            "feedback_complaint_rate": None,
            "feedback_favorite_rate": None,
            "feedback_block_rate": None,
            "feedback_eval_rate": None,
            "capacity_avg_completed_per_day": 0.0,
            "capacity_peak_slot_rate": None,
            "capacity_key_slot_day_rate": None,
            "is_cpl_tesol": None,
            "is_self_introduce": None,
        }

    def _apply_appoint(self, connection: Any, event: DtsChangeEvent) -> None:
        row = event.after if event.operation != "DELETE" else event.before
        course_id = _string((row or {}).get("id"))
        if course_id is None:
            raise DtsWideProjectionError("DTS_DIRECT_COURSE_ID_REQUIRED")
        target = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(target).where(target.c["课程id"] == course_id).with_for_update()
        ).mappings().one_or_none()
        in_scope = bool(
            event.operation != "DELETE"
            and row is not None
            and str(row.get("use_point") or "") == "buy"
            and str(row.get("status") or "") not in {"cancel", "on"}
            and student_subject(row) is not None
        )
        if not in_scope:
            self._write_lesson_state(
                connection,
                course_id,
                values=None,
                existing=existing,
            )
            return

        assert row is not None
        teacher_id = _string(row.get("t_id"))
        lesson_date = _date(row.get("date")) or _date(row.get("start_time"))
        lesson_time = _time(row.get("time")) or _time(row.get("start_time"))
        if teacher_id is None or lesson_date is None:
            raise DtsWideProjectionError("DTS_DIRECT_APPOINT_FULL_IMAGE_REQUIRED")
        teacher = TeacherSourceWideRecord.__table__
        teacher_row = connection.execute(
            select(teacher).where(teacher.c.tchr_id == teacher_id)
        ).mappings().one_or_none()
        if teacher_row is None:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_DEPENDENCY_PENDING")
        expected_area = "ovs" if event.source_region == "ovs" else "dmo"
        if (
            teacher_row["teach_area_type"] != expected_area
            or teacher_row["onboard_date"] is None
            or teacher_row["onboard_30d_end_date"] is None
            or not (
                teacher_row["onboard_date"]
                <= lesson_date
                <= teacher_row["onboard_30d_end_date"]
            )
        ):
            self._write_lesson_state(
                connection,
                course_id,
                values=None,
                existing=existing,
            )
            return

        values = {
            "课程id": course_id,
            "上课日期": lesson_date,
            "上课时间": lesson_time,
            "是否高峰": is_peak_lesson(
                event.source_region,
                row.get("week") if row.get("week") is not None else lesson_date.isoweekday(),
                lesson_time,
            ),
            "老师id": teacher_id,
            "学员id": student_subject(row),
            "课程状态": _string(row.get("status")),
            "缺席原因明细": _string(row.get("cancel_reason")),
            "迟到": False,
            "早退": False,
            "差评分": None,
            "差评标签": None,
            "投诉一级分类": None,
            "投诉二级分类": None,
            "投诉三级分类": None,
            "是否拉黑": False,
            "收藏": False,
            "好评标签": None,
            "评价详情": None,
            "未开摄像头": False,
            "cpu占用过高": False,
            "网络延迟过高": False,
            "假早退": False,
        }
        if existing is not None:
            for name in (
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
                "假早退",
            ):
                values[name] = existing[name]
        self._write_lesson_state(
            connection,
            course_id,
            values=values,
            existing=existing,
        )

    def _apply_teacher_class_schedule(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        row = slot_activation(event)
        if row is None:
            return
        teacher_id = _string(row.get("teacher_id"))
        schedule_date = _date(row.get("date"))
        if teacher_id is None or schedule_date is None:
            raise DtsWideProjectionError("DTS_DIRECT_SCHEDULE_FULL_IMAGE_REQUIRED")
        target = TeacherSourceWideRecord.__table__
        teacher = connection.execute(
            select(target).where(target.c.tchr_id == teacher_id).with_for_update()
        ).mappings().one_or_none()
        if teacher is None:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_DEPENDENCY_PENDING")
        if (
            teacher["onboard_date"] is None
            or teacher["onboard_30d_end_date"] is None
            or not (
                teacher["onboard_date"]
                <= schedule_date
                <= teacher["onboard_30d_end_date"]
            )
        ):
            return
        peak = schedule_slot_is_peak(
            _string(teacher["teach_area_type"]),
            schedule_date,
            _int(row.get("time_slot")),
        ) is True
        total = int(teacher["total_slot_cnt"] or 0) + 1
        regular = int(teacher["reg_slot_cnt"] or 0) + (
            1 if str(row.get("project_code") or "") == "1v1" else 0
        )
        peak_total = int(teacher["peak_slot_cnt"] or 0) + (1 if peak else 0)
        slot_days = int(teacher["slot_days"] or 0) + 1
        peak_slot_days = int(teacher["peak_slot_days"] or 0) + (1 if peak else 0)
        first_open = teacher["first_open_slot_dt"]
        if first_open is None or schedule_date < first_open:
            first_open = schedule_date
        connection.execute(
            update(target)
            .where(target.c.tchr_id == teacher_id)
            .values(
                first_open_slot_dt=first_open,
                total_slot_cnt=total,
                reg_slot_cnt=regular,
                peak_slot_cnt=peak_total,
                slot_days=slot_days,
                peak_slot_days=peak_slot_days,
                capacity_peak_slot_rate=_safe_rate(peak_total, total),
                capacity_key_slot_day_rate=_safe_rate(peak_slot_days, slot_days),
            )
        )
        self._counts.slot_activations += 1
        self._counts.teacher_upserts += 1

    def _apply_teacher_certification(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        changes: dict[str, bool] = {}
        before = event.before
        if (
            before is not None
            and str(before.get("certification_type") or "").strip().lower()
            == "tesol"
        ):
            before_teacher = _string(before.get("teacher_id"))
            if before_teacher is not None:
                changes[before_teacher] = False
        after = event.after if event.operation != "DELETE" else None
        if after is not None:
            after_teacher = _string(after.get("teacher_id"))
            if (
                after_teacher is not None
                and str(after.get("certification_type") or "").strip().lower()
                == "tesol"
            ):
                changes[after_teacher] = (
                    _int(after.get("certification_status")) == 1
                )
        if not changes:
            return
        target = TeacherSourceWideRecord.__table__
        for teacher_id, completed in sorted(changes.items()):
            changed = connection.execute(
                update(target)
                .where(target.c.tchr_id == teacher_id)
                .values(is_cpl_tesol=completed)
            ).rowcount or 0
            if not changed:
                raise DtsWideProjectionError(
                    "DTS_DIRECT_TEACHER_DEPENDENCY_PENDING"
                )
            self._counts.teacher_upserts += 1

    def _apply_teacher_absent_reason(self, connection: Any, event: DtsChangeEvent) -> None:
        self._apply_course_change(
            connection,
            event,
            cleared={"缺席原因明细": None},
            build_after=lambda row: {
                "缺席原因明细": _string(row.get("reason_desc"))
                or _string(row.get("reason_type"))
            },
        )

    def _apply_teacher_penalty(self, connection: Any, event: DtsChangeEvent) -> None:
        def values(row: Mapping[str, Any]) -> Mapping[str, Any]:
            if _int(row.get("appeal_status")) in {None, 2}:
                return {"迟到": False, "早退": False}
            start = _datetime(row.get("lesson_start_time"))
            in_time = _datetime(row.get("in_time"))
            out_time = _datetime(row.get("out_time"))
            end = start + timedelta(minutes=30) if start is not None else None
            return {
                "迟到": bool(start and in_time and (in_time - start).total_seconds() > 30),
                "早退": bool(
                    end
                    and out_time
                    and out_time > datetime(1970, 1, 1, 8)
                    and (end - out_time).total_seconds() > 30
                ),
            }

        self._apply_course_change(
            connection,
            event,
            cleared={"迟到": False, "早退": False},
            build_after=values,
        )

    def _apply_user_teacher_grading(self, connection: Any, event: DtsChangeEvent) -> None:
        def values(row: Mapping[str, Any]) -> Mapping[str, Any]:
            active = (
                _int(row.get("is_del")) in {None, 0}
                and _int(row.get("status")) in {None, 0}
            )
            if not active:
                return {"差评分": None, "差评标签": None, "好评标签": None}
            score = _float(row.get("score"))
            grading_type = str(row.get("type") or "").strip().lower()
            return {
                "差评分": score if score in {1.0, 2.0} else None,
                "差评标签": score in {1.0, 2.0} or grading_type == "unsatisfactory",
                "好评标签": score in {4.0, 5.0} or grading_type == "satisfactory",
            }

        self._apply_course_change(
            connection,
            event,
            cleared={"差评分": None, "差评标签": None, "好评标签": None},
            build_after=values,
        )

    def _apply_grading_label_log(self, connection: Any, event: DtsChangeEvent) -> None:
        rows = tuple(
            row
            for row in (event.before, event.after)
            if row is not None
        )
        course_ids = {
            course_id
            for course_id in (_string(row.get("appoint_id")) for row in rows)
            if course_id is not None
        }
        if not course_ids:
            raise DtsWideProjectionError("DTS_DIRECT_COURSE_DEPENDENCY_REQUIRED")
        for course_id in sorted(course_ids):
            remove_names: set[str] = set()
            add_names: set[str] = set()
            if (
                event.before is not None
                and _string(event.before.get("appoint_id")) == course_id
            ):
                before_name = _string(event.before.get("label_name"))
                if before_name is not None:
                    remove_names.add(before_name)
            after = event.after if event.operation != "DELETE" else None
            if (
                after is not None
                and _string(after.get("appoint_id")) == course_id
            ):
                active = (
                    _int(after.get("type")) == 1
                    and str(after.get("status") or "").strip().lower() == "normal"
                )
                after_name = _string(after.get("label_name"))
                if active and after_name is not None:
                    add_names.add(after_name)
            self._update_lesson_label_set(
                connection,
                course_id,
                remove_names=remove_names,
                add_names=add_names,
            )

    def _apply_grading_label(self, connection: Any, event: DtsChangeEvent) -> None:
        before_name = _string((event.before or {}).get("label_name"))
        after_name = (
            _string((event.after or {}).get("label_name"))
            if event.operation != "DELETE"
            else None
        )
        if before_name is None or before_name == after_name:
            self._counts.ignored += 1
            return
        target = LessonSourceWideRecord.__table__
        lessons = connection.execute(
            select(
                target.c["课程id"],
                target.c["评价详情"],
            ).where(target.c["评价详情"].is_not(None))
        ).mappings()
        for lesson in lessons:
            names = {
                value.strip()
                for value in str(lesson["评价详情"] or "").split(",")
                if value.strip()
            }
            if before_name not in names:
                continue
            names.remove(before_name)
            if after_name is not None:
                names.add(after_name)
            connection.execute(
                update(target)
                .where(target.c["课程id"] == lesson["课程id"])
                .values(**{"评价详情": ",".join(sorted(names)) or None})
            )
            self._counts.lesson_upserts += 1

    def _apply_teacher_favorite(self, connection: Any, event: DtsChangeEvent) -> None:
        self._apply_relationship(connection, event, column="收藏", blacklist=False)

    def _apply_teacher_blacklist(self, connection: Any, event: DtsChangeEvent) -> None:
        self._apply_relationship(connection, event, column="是否拉黑", blacklist=True)

    def _apply_relationship(
        self,
        connection: Any,
        event: DtsChangeEvent,
        *,
        column: str,
        blacklist: bool,
    ) -> None:
        before = event.before
        after = event.after if event.operation != "DELETE" else None
        if before is not None:
            selected = self._relationship_course(connection, before, blacklist=blacklist)
            if selected is not None:
                self._update_lesson(connection, selected, {column: False})
        if after is None:
            return
        active = True
        if blacklist:
            valid_end = _datetime(after.get("valid_end_time"))
            active = _truthy(after.get("is_valid_forever")) or bool(
                valid_end is not None and valid_end.year >= 2999
            )
        if active:
            selected = self._relationship_course(connection, after, blacklist=blacklist)
            if selected is not None:
                self._update_lesson(connection, selected, {column: True})

    @staticmethod
    def _relationship_course(
        connection: Any,
        row: Mapping[str, Any],
        *,
        blacklist: bool,
    ) -> str | None:
        teacher_id = _string(row.get("teacher_id" if blacklist else "tea_id"))
        student_id = student_subject(row)
        added = _datetime(row.get("add_time") or row.get("valid_start_time"))
        if teacher_id is None or student_id is None or added is None:
            return None
        target = LessonSourceWideRecord.__table__
        lesson_at = target.c["上课日期"] + target.c["上课时间"]
        return connection.execute(
            select(target.c["课程id"])
            .where(
                target.c["老师id"] == teacher_id,
                target.c["学员id"] == student_id,
                target.c["课程状态"] == _COMPLETED_STATUS,
                lesson_at <= added,
            )
            .order_by(
                target.c["上课日期"].desc(),
                target.c["上课时间"].desc(),
                target.c["课程id"].desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _apply_complaint(self, connection: Any, event: DtsChangeEvent) -> None:
        self._apply_complaint_row(connection, event)

    def _apply_user_complaint(self, connection: Any, event: DtsChangeEvent) -> None:
        # The user-side row does not contain approve/validity.  Direct mode
        # therefore waits for the authoritative complaint row instead of
        # inventing a cross-table current-state join.
        del connection, event
        self._counts.ignored += 1

    def _apply_complaint_row(self, connection: Any, event: DtsChangeEvent) -> None:
        def values(row: Mapping[str, Any]) -> Mapping[str, Any]:
            valid = bool(
                _int(row.get("complaint_type")) == 13
                and _int(row.get("complaint_type_grandson")) != 82
                and str(row.get("approve") or "").strip().lower() == "y"
                and _int(row.get("validity")) == 1
            )
            if not valid:
                names: tuple[str | None, str | None, str | None] = (
                    None,
                    None,
                    None,
                )
            else:
                category_ids = (
                    _string(row.get("complaint_type")),
                    _string(row.get("complaint_type_child")),
                    _string(row.get("complaint_type_grandson")),
                )
                names = self._complaint_category_names(
                    connection,
                    category_ids,
                )
            return {
                "投诉一级分类": names[0],
                "投诉二级分类": names[1],
                "投诉三级分类": names[2],
            }

        self._apply_course_change(
            connection,
            event,
            cleared={
                "投诉一级分类": None,
                "投诉二级分类": None,
                "投诉三级分类": None,
            },
            build_after=values,
        )

    def _apply_complaint_cate(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        row = event.after if event.operation != "DELETE" else event.before
        category_id = _string((row or {}).get("id"))
        if category_id is None:
            raise DtsWideProjectionError(
                "DTS_DIRECT_COMPLAINT_CATEGORY_ID_REQUIRED"
            )
        self._upsert_complaint_category_reference(connection, event, category_id)
        before_name = _string((event.before or {}).get("cate_cn_name"))
        after_name = (
            _string((event.after or {}).get("cate_cn_name"))
            if event.operation != "DELETE"
            else None
        )
        if before_name is None or before_name == after_name:
            self._counts.ignored += 1
            return
        target = LessonSourceWideRecord.__table__
        category_columns = (
            "投诉一级分类",
            "投诉二级分类",
            "投诉三级分类",
        )
        for name in category_columns:
            course_ids = tuple(
                str(value)
                for value in connection.execute(
                    select(target.c["课程id"]).where(
                        target.c[name] == before_name
                    )
                ).scalars()
            )
            for course_id in course_ids:
                self._update_lesson(
                    connection,
                    course_id,
                    {name: after_name},
                )

    @staticmethod
    def _complaint_category_source_key(category_id: str) -> str:
        return json.dumps(
            {"id": category_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _complaint_category_names(
        self,
        connection: Any,
        category_ids: tuple[str | None, str | None, str | None],
    ) -> tuple[str | None, str | None, str | None]:
        required_ids = {
            value
            for value in category_ids
            if value not in {None, "-1", "0"}
        }
        if not required_ids:
            return (None, None, None)
        target = DtsSourceRowRecord.__table__
        rows = connection.execute(
            select(target.c.source_row).where(
                target.c.source_region == "dom",
                target.c.source_table == "dom_complaint_cate",
                target.c.source_key.in_(
                    sorted(
                        self._complaint_category_source_key(value)
                        for value in required_ids
                    )
                ),
                target.c.is_deleted.is_(False),
            )
        ).scalars()
        names = {
            category_id: category_name
            for row in rows
            if isinstance(row, Mapping)
            if (category_id := _string(row.get("id"))) is not None
            if (category_name := _string(row.get("cate_cn_name"))) is not None
        }
        if required_ids != set(names):
            raise DtsWideProjectionError(
                "DTS_DIRECT_COMPLAINT_CATEGORY_DEPENDENCY_PENDING"
            )
        return tuple(
            names.get(value) if value not in {None, "-1", "0"} else None
            for value in category_ids
        )

    def _upsert_complaint_category_reference(
        self,
        connection: Any,
        event: DtsChangeEvent,
        category_id: str,
    ) -> None:
        row = event.before if event.operation == "DELETE" else event.after
        if row is None:
            raise DtsWideProjectionError(
                "DTS_DIRECT_COMPLAINT_CATEGORY_IMAGE_REQUIRED"
            )
        parent_id = _string(row.get("cate_parent"))
        category_ids = sorted(
            value
            for value in {category_id, parent_id}
            if value not in {None, "-1", "0"}
        )
        target = DtsSourceRowRecord.__table__
        values = {
            "source_region": "dom",
            "source_table": "dom_complaint_cate",
            "source_key": self._complaint_category_source_key(category_id),
            "source_key_data": {"id": category_id},
            "dependency_keys": {"category_ids": category_ids},
            "source_row": dict(row),
            "is_deleted": event.operation == "DELETE",
            "source_timestamp": event.source_timestamp,
            "last_record_id": event.record_id,
            "source_position": event.source_position,
            "last_topic": event.topic,
            "last_partition": event.partition,
            "last_offset": event.offset,
            "row_version": 1,
        }
        statement = insert(target).values(values)
        excluded = statement.excluded
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    target.c.source_region,
                    target.c.source_table,
                    target.c.source_key,
                ],
                set_={
                    "source_key_data": excluded.source_key_data,
                    "dependency_keys": excluded.dependency_keys,
                    "source_row": excluded.source_row,
                    "is_deleted": excluded.is_deleted,
                    "source_timestamp": excluded.source_timestamp,
                    "last_record_id": excluded.last_record_id,
                    "source_position": excluded.source_position,
                    "last_topic": excluded.last_topic,
                    "last_partition": excluded.last_partition,
                    "last_offset": excluded.last_offset,
                    "row_version": target.c.row_version + 1,
                    "updated_at": func.now(),
                },
            )
        )

    def _apply_qa_task_close_camera_record(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        self._apply_course_boolean(connection, event, column="未开摄像头")

    def _apply_qa_task_fake_early_leave_record(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        self._apply_course_boolean(connection, event, column="假早退")

    def _apply_course_boolean(
        self,
        connection: Any,
        event: DtsChangeEvent,
        *,
        column: str,
    ) -> None:
        self._apply_course_change(
            connection,
            event,
            cleared={column: False},
            build_after=lambda _row: {column: True},
        )

    def _apply_qa_ac_classroom_record(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        changes: dict[str, dict[str, Any]] = {}

        def collect(row: Mapping[str, Any] | None, value: bool) -> None:
            if row is None:
                return
            info = row.get("info")
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except json.JSONDecodeError as exc:
                    raise DtsWideProjectionError("DTS_DIRECT_QA_INFO_INVALID") from exc
            if not isinstance(info, Mapping):
                raise DtsWideProjectionError("DTS_DIRECT_QA_INFO_INVALID")
            for key, column in (
                ("cpu", "cpu占用过高"),
                ("network_delay", "网络延迟过高"),
            ):
                records = info.get(key, ())
                if not isinstance(records, Sequence) or isinstance(
                    records,
                    (str, bytes, bytearray),
                ):
                    continue
                for record in records:
                    if not isinstance(record, Mapping):
                        continue
                    course_id = _string(record.get("appoint_id"))
                    if course_id is not None:
                        changes.setdefault(course_id, {})[column] = value

        collect(event.before, False)
        if event.operation != "DELETE":
            collect(event.after, True)
        for course_id, values in sorted(changes.items()):
            self._update_lesson(connection, course_id, values)

    def _apply_course_change(
        self,
        connection: Any,
        event: DtsChangeEvent,
        *,
        cleared: Mapping[str, Any],
        build_after: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        changes: dict[str, dict[str, Any]] = {}
        if event.before is not None:
            before_course = _string(event.before.get("appoint_id"))
            if before_course is not None:
                changes[before_course] = dict(cleared)
        if event.operation != "DELETE" and event.after is not None:
            after_course = _string(event.after.get("appoint_id"))
            if after_course is not None:
                changes[after_course] = dict(build_after(event.after))
        if not changes:
            raise DtsWideProjectionError("DTS_DIRECT_COURSE_DEPENDENCY_REQUIRED")
        for course_id, values in sorted(changes.items()):
            self._update_lesson(connection, course_id, values)

    def _update_lesson_label_set(
        self,
        connection: Any,
        course_id: str,
        *,
        remove_names: set[str],
        add_names: set[str],
    ) -> None:
        target = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(
                target.c["老师id"],
                target.c["评价详情"],
            )
            .where(target.c["课程id"] == course_id)
            .with_for_update()
        ).mappings().one_or_none()
        if existing is None:
            raise DtsWideProjectionError("DTS_DIRECT_LESSON_DEPENDENCY_PENDING")
        names = {
            value.strip()
            for value in str(existing["评价详情"] or "").split(",")
            if value.strip()
        }
        names.difference_update(remove_names)
        names.update(add_names)
        connection.execute(
            update(target)
            .where(target.c["课程id"] == course_id)
            .values(**{"评价详情": ",".join(sorted(names)) or None})
        )
        self._counts.lesson_upserts += 1

    def _update_lesson(
        self,
        connection: Any,
        course_id: str,
        values: Mapping[str, Any],
    ) -> None:
        target = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(target)
            .where(target.c["课程id"] == course_id)
            .with_for_update()
        ).mappings().one_or_none()
        if existing is None:
            raise DtsWideProjectionError("DTS_DIRECT_LESSON_DEPENDENCY_PENDING")
        updated = dict(existing)
        updated.update(values)
        self._write_lesson_state(
            connection,
            course_id,
            values=updated,
            existing=existing,
        )

    def _write_lesson_state(
        self,
        connection: Any,
        course_id: str,
        *,
        values: Mapping[str, Any] | None,
        existing: Mapping[str, Any] | None,
    ) -> None:
        old_row = dict(existing) if existing is not None else None
        new_row = dict(values) if values is not None else None
        if new_row is not None:
            new_row["课程id"] = course_id

        affected_teachers = {
            teacher_id
            for teacher_id in (
                _string((old_row or {}).get("老师id")),
                _string((new_row or {}).get("老师id")),
            )
            if teacher_id is not None
        }
        self._lock_teacher_rows(connection, affected_teachers)
        affected_pairs = {
            (teacher_id, student_id)
            for teacher_id, student_id in (
                (
                    _string((old_row or {}).get("老师id")),
                    _string((old_row or {}).get("学员id")),
                ),
                (
                    _string((new_row or {}).get("老师id")),
                    _string((new_row or {}).get("学员id")),
                ),
            )
            if teacher_id is not None and student_id is not None
        }
        before_memberships = {
            pair: self._student_memberships(connection, *pair)
            for pair in affected_pairs
        }

        target = LessonSourceWideRecord.__table__
        if new_row is None:
            if old_row is not None:
                connection.execute(
                    delete(target).where(target.c["课程id"] == course_id)
                )
                self._counts.lesson_deletes += 1
        elif old_row is None:
            connection.execute(insert(target).values(new_row))
            self._counts.lesson_upserts += 1
        else:
            connection.execute(
                update(target)
                .where(target.c["课程id"] == course_id)
                .values(
                    {
                        name: value
                        for name, value in new_row.items()
                        if name != "课程id"
                    }
                )
            )
            self._counts.lesson_upserts += 1

        after_memberships = {
            pair: self._student_memberships(connection, *pair)
            for pair in affected_pairs
        }
        deltas: dict[str, dict[str, int]] = {
            teacher_id: {
                name: 0
                for name in (*_LESSON_COUNTER_FIELDS, *_DISTINCT_STUDENT_FIELDS)
            }
            for teacher_id in affected_teachers
        }
        old_teacher = _string((old_row or {}).get("老师id"))
        if old_teacher is not None:
            for name, amount in self._lesson_contribution(old_row).items():
                deltas[old_teacher][name] -= amount
        new_teacher = _string((new_row or {}).get("老师id"))
        if new_teacher is not None:
            for name, amount in self._lesson_contribution(new_row).items():
                deltas[new_teacher][name] += amount
        for pair in affected_pairs:
            teacher_id, _student_id = pair
            before = before_memberships[pair]
            after = after_memberships[pair]
            for name in _DISTINCT_STUDENT_FIELDS:
                deltas[teacher_id][name] += int(after[name]) - int(before[name])
        for teacher_id in sorted(affected_teachers):
            old_date_effect = (
                (
                    (old_row or {}).get("上课日期"),
                    (old_row or {}).get("课程状态") == _COMPLETED_STATUS,
                )
                if old_teacher == teacher_id
                else None
            )
            new_date_effect = (
                (
                    (new_row or {}).get("上课日期"),
                    (new_row or {}).get("课程状态") == _COMPLETED_STATUS,
                )
                if new_teacher == teacher_id
                else None
            )
            refresh_dates = old_date_effect != new_date_effect
            if not refresh_dates and not any(deltas[teacher_id].values()):
                continue
            self._apply_teacher_delta(
                connection,
                teacher_id,
                deltas[teacher_id],
                refresh_dates=refresh_dates,
            )

    @staticmethod
    def _lock_teacher_rows(
        connection: Any,
        teacher_ids: set[str],
    ) -> None:
        if not teacher_ids:
            return
        teacher = TeacherSourceWideRecord.__table__
        locked = set(
            connection.execute(
                select(teacher.c.tchr_id)
                .where(teacher.c.tchr_id.in_(sorted(teacher_ids)))
                .order_by(teacher.c.tchr_id)
                .with_for_update()
            ).scalars()
        )
        if locked != teacher_ids:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_DEPENDENCY_PENDING")

    @staticmethod
    def _lesson_contribution(
        row: Mapping[str, Any] | None,
    ) -> dict[str, int]:
        if row is None:
            return {name: 0 for name in _LESSON_COUNTER_FIELDS}
        completed = row.get("课程状态") == _COMPLETED_STATUS
        absent = row.get("课程状态") == _ABSENT_STATUS
        late = completed and row.get("迟到") is True
        early = completed and row.get("早退") is True
        complaint = any(
            row.get(name) is not None
            for name in ("投诉一级分类", "投诉二级分类", "投诉三级分类")
        )
        evaluated = (
            row.get("好评标签") is not None
            or row.get("差评标签") is not None
        )
        return {
            "total_booked_cnt": 1,
            "peak_booked_cnt": int(row.get("是否高峰") is True),
            "total_completed_cnt": int(completed),
            "peak_completed_cnt": int(
                completed and row.get("是否高峰") is True
            ),
            "absent_cnt": int(absent),
            "late_cnt": int(late),
            "early_cnt": int(early),
            "anomaly_cnt": int(absent or late or early),
            "perfect_cnt": int(
                completed
                and row.get("迟到") is not True
                and row.get("早退") is not True
            ),
            "no_notice_cnt": int(
                absent and row.get("缺席原因明细") == _NO_NOTICE_DETAIL
            ),
            "feedback_total_eval_cnt": int(evaluated),
            "feedback_praise_cnt": int(row.get("好评标签") is True),
            "feedback_negative_cnt": int(row.get("差评标签") is True),
            "feedback_complaint_cnt": int(complaint),
            "feedback_valid_complaint_cnt": int(complaint),
        }

    @staticmethod
    def _student_memberships(
        connection: Any,
        teacher_id: str,
        student_id: str,
    ) -> dict[str, bool]:
        lesson = LessonSourceWideRecord.__table__
        base = (
            lesson.c["老师id"] == teacher_id,
            lesson.c["学员id"] == student_id,
        )
        row = connection.execute(
            select(
                exists(
                    select(1).where(*base, lesson.c["课程状态"] == _COMPLETED_STATUS)
                ).label("first_completed_student_cnt"),
                exists(
                    select(1).where(*base, lesson.c["收藏"].is_(True))
                ).label("feedback_favorite_cnt"),
                exists(
                    select(1).where(*base, lesson.c["是否拉黑"].is_(True))
                ).label("feedback_block_cnt"),
            )
        ).mappings().one()
        return {name: bool(row[name]) for name in _DISTINCT_STUDENT_FIELDS}

    def _apply_teacher_delta(
        self,
        connection: Any,
        teacher_id: str,
        delta: Mapping[str, int],
        *,
        refresh_dates: bool,
    ) -> None:
        teacher = TeacherSourceWideRecord.__table__
        lesson = LessonSourceWideRecord.__table__
        current = connection.execute(
            select(teacher)
            .where(teacher.c.tchr_id == teacher_id)
            .with_for_update()
        ).mappings().one_or_none()
        if current is None:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_DEPENDENCY_PENDING")
        counters = {
            name: int(current[name] or 0) + int(delta.get(name, 0))
            for name in (*_LESSON_COUNTER_FIELDS, *_DISTINCT_STUDENT_FIELDS)
        }
        if any(value < 0 for value in counters.values()):
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_COUNTER_UNDERFLOW")
        if refresh_dates:
            first_booked, first_completed = connection.execute(
                select(
                    func.min(lesson.c["上课日期"]),
                    func.min(
                        case(
                            (
                                lesson.c["课程状态"] == _COMPLETED_STATUS,
                                lesson.c["上课日期"],
                            ),
                            else_=None,
                        )
                    ),
                ).where(lesson.c["老师id"] == teacher_id)
            ).one()
        else:
            first_booked = current["first_booked_dt"]
            first_completed = current["first_completed_dt"]
        total_booked = counters["total_booked_cnt"]
        total_completed = counters["total_completed_cnt"]
        total_evaluated = counters["feedback_total_eval_cnt"]
        completed_students = counters["first_completed_student_cnt"]
        values = {
            **counters,
            "first_booked_dt": first_booked,
            "first_completed_dt": first_completed,
            "reliability_absent_rate": _safe_rate(
                counters["absent_cnt"], total_booked
            ),
            "reliability_late_rate": _safe_rate(
                counters["late_cnt"], total_completed
            ),
            "reliability_early_leave_rate": _safe_rate(
                counters["early_cnt"], total_completed
            ),
            "reliability_late_early_rate": _safe_rate(
                counters["late_cnt"] + counters["early_cnt"],
                total_completed,
            ),
            "feedback_praise_rate": _safe_rate(
                counters["feedback_praise_cnt"], total_evaluated
            ),
            "feedback_negative_rate": _safe_rate(
                counters["feedback_negative_cnt"], total_evaluated
            ),
            "feedback_complaint_rate": _safe_rate(
                counters["feedback_valid_complaint_cnt"],
                total_completed,
            ),
            "feedback_favorite_rate": _safe_rate(
                counters["feedback_favorite_cnt"],
                completed_students,
            ),
            "feedback_block_rate": _safe_rate(
                counters["feedback_block_cnt"],
                completed_students,
            ),
            "feedback_eval_rate": _safe_rate(total_evaluated, total_completed),
            "capacity_avg_completed_per_day": total_completed / 30,
        }
        connection.execute(
            update(teacher).where(teacher.c.tchr_id == teacher_id).values(values)
        )
        self._counts.teacher_delta_updates += 1
        self._counts.teacher_upserts += 1

    @staticmethod
    def _upsert(
        connection: Any,
        table: Any,
        values: Mapping[str, Any],
        *,
        primary_key: str,
    ) -> None:
        statement = insert(table).values(dict(values))
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c[primary_key]],
                set_={
                    name: statement.excluded[name]
                    for name in values
                    if name != primary_key
                },
            )
        )


__all__ = [
    "DtsDirectWideProjector",
    "direct_projection_event",
    "schedule_slot_is_peak",
    "slot_activation",
]
