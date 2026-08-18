"""Project durable DTS current state into the two source-wide tables.

The projector is deliberately downstream of :mod:`dts_ingest_store`.  One
Kafka event first commits its receipt, whitelisted source image and dirty key;
this worker then recomputes the complete affected lesson or teacher row.  A
failed projection never removes the dirty key and therefore never needs a
Kafka replay to recover.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, cast, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert

from .db_models import (
    DtsDirtyKeyRecord,
    DtsSourceRowRecord,
    LessonSourceWideRecord,
    TeacherSourceWideRecord,
)
from .dts_source_consumer import (
    derive_penalty_flags,
    is_peak_lesson,
    reduce_latest_complaints,
    student_subject,
    teacher_matches_region,
)


_UTC_PLUS_8 = timezone(timedelta(hours=8))
_REGIONS = ("ovs", "dom")
_COMPLETED_STATUS = "end"
_ABSENT_STATUS = "t_absent"
_HBT_CODES = frozenset({5, 6, 7, 11, 19, 20, 21, 22, 503})
_OBT_CODES = frozenset({8, 10, 201})
_CENTER_DESCRIPTIONS = {0: "HBT", 1: "CBT", 5: "TBT", 6: "HBT"}
_COURSE_DATE_FIELDS_BY_SUFFIX: dict[str, tuple[str, ...]] = {
    "complaint": ("course_date",),
    "qa_task_close_camera_record": ("start_time",),
    "qa_task_fake_early_leave_record": ("start_time",),
    "teacher_penalty": ("lesson_start_time",),
    "user_teacher_grading": ("start_time",),
}


class DtsWideProjectionError(RuntimeError):
    """Stable, non-sensitive projection failure."""


@dataclass(frozen=True)
class DtsWideProjectionSettings:
    cohort_start: date
    cohort_end_exclusive: date | None = None
    retry_base_seconds: int = 10
    retry_max_seconds: int = 300
    retry_max_attempts: int = 8

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DtsWideProjectionSettings:
        values = os.environ if environ is None else environ

        def parsed_date(name: str, default: str) -> date:
            raw = values.get(name, default).strip()
            try:
                return date.fromisoformat(raw)
            except ValueError as exc:
                raise DtsWideProjectionError(f"{name}_INVALID") from exc

        cohort_start = parsed_date("TIT_DTS_COHORT_START", "2026-08-13")
        raw_end = values.get("TIT_DTS_COHORT_END_EXCLUSIVE", "").strip()
        cohort_end = (
            parsed_date("TIT_DTS_COHORT_END_EXCLUSIVE", raw_end)
            if raw_end
            else None
        )
        if cohort_end is not None and cohort_start >= cohort_end:
            raise DtsWideProjectionError("DTS_COHORT_WINDOW_INVALID")
        raw_max_attempts = values.get(
            "TIT_DTS_PROJECTION_MAX_ATTEMPTS",
            "8",
        ).strip()
        try:
            retry_max_attempts = int(raw_max_attempts)
        except ValueError as exc:
            raise DtsWideProjectionError(
                "TIT_DTS_PROJECTION_MAX_ATTEMPTS_INVALID"
            ) from exc
        if not 1 <= retry_max_attempts <= 100:
            raise DtsWideProjectionError(
                "TIT_DTS_PROJECTION_MAX_ATTEMPTS_INVALID"
            )
        return cls(
            cohort_start=cohort_start,
            cohort_end_exclusive=cohort_end,
            retry_max_attempts=retry_max_attempts,
        )

    def require_subscription_boundary(
        self,
        start_timestamp_seconds: int | None,
    ) -> None:
        """Prove change capture begins no later than the first cohort day."""

        if start_timestamp_seconds is None:
            raise DtsWideProjectionError("DTS_START_AT_REQUIRED_FOR_COHORT")
        cohort_start_at = datetime.combine(
            self.cohort_start,
            time.min,
            tzinfo=_UTC_PLUS_8,
        )
        if start_timestamp_seconds > int(cohort_start_at.timestamp()):
            raise DtsWideProjectionError("DTS_START_AT_AFTER_COHORT_START")

@dataclass(frozen=True)
class _SourceRow:
    region: str
    table: str
    row: Mapping[str, Any]
    source_timestamp: int
    last_record_id: int


@dataclass
class _ProjectionCounts:
    dirty_keys: int = 0
    lesson_upserts: int = 0
    lesson_deletes: int = 0
    teacher_upserts: int = 0
    teacher_deletes: int = 0
    unchanged: int = 0
    retries: int = 0
    quarantined: int = 0

    def add(self, other: _ProjectionCounts) -> None:
        self.lesson_upserts += other.lesson_upserts
        self.lesson_deletes += other.lesson_deletes
        self.teacher_upserts += other.teacher_upserts
        self.teacher_deletes += other.teacher_deletes
        self.unchanged += other.unchanged

    def as_dict(self) -> dict[str, int]:
        return {
            "dirty_keys": self.dirty_keys,
            "lesson_upserts": self.lesson_upserts,
            "lesson_deletes": self.lesson_deletes,
            "teacher_upserts": self.teacher_upserts,
            "teacher_deletes": self.teacher_deletes,
            "unchanged": self.unchanged,
            "retries": self.retries,
            "quarantined": self.quarantined,
        }


def _string_id(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _complaint_category_id(value: Any) -> str | None:
    rendered = _string_id(value)
    return None if rendered in {"-1", "0"} else rendered


def _int_value(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        return date.fromisoformat(rendered[:10])
    except ValueError:
        return None


def _datetime_value(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).strip().lstrip("-").isdigit():
        numeric = float(value)
        if abs(numeric) >= 10_000_000_000:
            numeric /= 1000
        parsed = datetime.fromtimestamp(numeric, tz=_UTC_PLUS_8)
    else:
        rendered = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(rendered.replace(" ", "T", 1))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_UTC_PLUS_8).replace(tzinfo=None)
    return parsed


def _time_value(value: Any) -> time | None:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    parsed_datetime = _datetime_value(value)
    if parsed_datetime is not None and (
        isinstance(value, datetime)
        or " " in str(value)
        or "T" in str(value)
    ):
        return parsed_datetime.time()
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        return time.fromisoformat(rendered).replace(tzinfo=None)
    except ValueError:
        pass
    hour = _int_value(rendered)
    return time(hour, 0) if hour is not None and 0 <= hour <= 23 else None


def _non_empty(*values: Any) -> str | None:
    for value in values:
        rendered = str(value or "").strip()
        if rendered:
            return rendered
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _safe_rate(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _sortable_id(value: Any) -> tuple[int, int | str]:
    parsed = _int_value(value)
    return (1, parsed) if parsed is not None else (0, str(value or ""))


def _latest(
    rows: Sequence[Mapping[str, Any]],
    *,
    time_fields: tuple[str, ...] = (),
) -> Mapping[str, Any] | None:
    if not rows:
        return None

    def key(row: Mapping[str, Any]) -> tuple[datetime, datetime, tuple[int, int | str]]:
        parsed = next(
            (
                value
                for field in time_fields
                if (value := _datetime_value(row.get(field))) is not None
            ),
            datetime.min,
        )
        created = _datetime_value(row.get("create_time")) or datetime.min
        return parsed, created, _sortable_id(row.get("id"))

    return max(rows, key=key)


class DtsWideProjector:
    """Consume coalesced dirty keys and mutate only the two source-wide tables."""

    def __init__(
        self,
        engine: Engine,
        *,
        worker_id: str,
        settings: DtsWideProjectionSettings | None = None,
    ) -> None:
        if not worker_id.strip():
            raise DtsWideProjectionError("DTS_PROJECTOR_WORKER_ID_REQUIRED")
        self.engine = engine
        self.worker_id = worker_id.strip()[:128]
        self.settings = settings or DtsWideProjectionSettings.from_env()

    def run_batch(self, *, max_keys: int = 100) -> dict[str, int]:
        if max_keys < 1:
            raise DtsWideProjectionError("DTS_PROJECTOR_MAX_KEYS_INVALID")
        result = _ProjectionCounts()
        for _ in range(max_keys):
            current_key: tuple[str, str, str] | None = None
            try:
                with self.engine.begin() as connection:
                    dirty = self._lock_next_dirty_key(connection)
                    if dirty is None:
                        break
                    current_key = (
                        str(dirty["key_type"]),
                        str(dirty["key_part_1"]),
                        str(dirty["key_part_2"]),
                    )
                    self._mark_processing(connection, current_key)
                    projected = self._dispatch(connection, dirty)
                    self._mark_completed(connection, current_key)
                    result.dirty_keys += 1
                    result.add(projected)
            except Exception as exc:
                if current_key is None:
                    raise
                attempt = self._mark_retry(current_key, exc)
                result.retries += 1
                if attempt >= self.settings.retry_max_attempts:
                    result.quarantined += 1
        return result.as_dict()

    def _lock_next_dirty_key(self, connection: Any) -> Mapping[str, Any] | None:
        table = DtsDirtyKeyRecord.__table__
        return connection.execute(
            select(table)
            .where(
                table.c.status.in_(("PENDING", "RETRY")),
                or_(
                    table.c.next_attempt_at.is_(None),
                    table.c.next_attempt_at <= text("clock_timestamp()"),
                ),
            )
            .order_by(table.c.last_seen_at, table.c.key_type, table.c.key_part_1)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).mappings().first()

    def _mark_processing(
        self,
        connection: Any,
        key: tuple[str, str, str],
    ) -> None:
        table = DtsDirtyKeyRecord.__table__
        connection.execute(
            update(table)
            .where(
                table.c.key_type == key[0],
                table.c.key_part_1 == key[1],
                table.c.key_part_2 == key[2],
            )
            .values(
                status="PROCESSING",
                claimed_at=text("clock_timestamp()"),
                claimed_by=self.worker_id,
                row_version=table.c.row_version + 1,
            )
        )

    def _mark_completed(
        self,
        connection: Any,
        key: tuple[str, str, str],
    ) -> None:
        table = DtsDirtyKeyRecord.__table__
        connection.execute(
            update(table)
            .where(
                table.c.key_type == key[0],
                table.c.key_part_1 == key[1],
                table.c.key_part_2 == key[2],
            )
            .values(
                status="COMPLETED",
                attempt_count=table.c.attempt_count + 1,
                last_error_code=None,
                next_attempt_at=None,
                claimed_at=None,
                claimed_by=None,
                row_version=table.c.row_version + 1,
            )
        )

    def _mark_retry(
        self,
        key: tuple[str, str, str],
        exc: Exception,
    ) -> int:
        table = DtsDirtyKeyRecord.__table__
        error_code = (
            str(exc)
            if isinstance(exc, DtsWideProjectionError) and str(exc)
            else "DTS_WIDE_PROJECTION_FAILED"
        )[:128]
        with self.engine.begin() as connection:
            dirty = connection.execute(
                select(table.c.attempt_count)
                .where(
                    table.c.key_type == key[0],
                    table.c.key_part_1 == key[1],
                    table.c.key_part_2 == key[2],
                )
                .with_for_update()
            ).first()
            if dirty is None:
                raise DtsWideProjectionError("DTS_DIRTY_KEY_LOST_DURING_RETRY")
            attempt = int(dirty[0]) + 1
            exhausted = attempt >= self.settings.retry_max_attempts
            if exhausted:
                # Keep the failure visible while preventing one poison key
                # from terminating or hot-looping the whole projector.  A
                # later real CDC event resets the key to PENDING in the ingest
                # store, so quarantine does not suppress new source evidence.
                next_attempt_at = text("'infinity'::timestamptz")
            else:
                delay = min(
                    self.settings.retry_max_seconds,
                    self.settings.retry_base_seconds * (2 ** min(attempt - 1, 8)),
                )
                next_attempt_at = datetime.now(timezone.utc) + timedelta(
                    seconds=delay
                )
            connection.execute(
                update(table)
                .where(
                    table.c.key_type == key[0],
                    table.c.key_part_1 == key[1],
                    table.c.key_part_2 == key[2],
                )
                .values(
                    status="RETRY",
                    attempt_count=attempt,
                    last_error_code=error_code,
                    next_attempt_at=next_attempt_at,
                    claimed_at=None,
                    claimed_by=None,
                    row_version=table.c.row_version + 1,
                )
            )
        return attempt

    def _dispatch(
        self,
        connection: Any,
        dirty: Mapping[str, Any],
    ) -> _ProjectionCounts:
        key_type = str(dirty["key_type"])
        key_1 = str(dirty["key_part_1"])
        key_2 = str(dirty["key_part_2"])
        if key_type == "COURSE":
            return self._project_course(connection, key_1, dirty)
        if key_type == "TEACHER":
            return self._project_teacher(connection, key_1)
        if key_type == "TEACHER_STUDENT":
            return self._project_course_set(
                connection,
                self._course_ids_for_pair(connection, key_1, key_2),
                dirty,
            )
        if key_type == "LABEL":
            return self._project_course_set(
                connection,
                self._course_ids_for_dependency(
                    connection,
                    "label_ids",
                    key_1,
                    suffixes=("grading_label_log",),
                ),
                dirty,
            )
        if key_type == "COMPLAINT_CATEGORY":
            return self._project_course_set(
                connection,
                self._course_ids_for_dependency(
                    connection,
                    "category_ids",
                    key_1,
                    suffixes=("complaint", "user_complaint"),
                ),
                dirty,
            )
        raise DtsWideProjectionError("DTS_DIRTY_KEY_TYPE_UNSUPPORTED")

    def _project_course_set(
        self,
        connection: Any,
        course_ids: Sequence[str],
        dirty: Mapping[str, Any],
    ) -> _ProjectionCounts:
        result = _ProjectionCounts()
        for course_id in sorted(set(course_ids)):
            result.add(self._project_course(connection, course_id, dirty))
        if not course_ids:
            result.unchanged += 1
        return result

    def _active_source_rows(
        self,
        connection: Any,
        *,
        suffixes: Sequence[str],
        dependency_name: str | None = None,
        dependency_value: str | None = None,
        regions: Sequence[str] = _REGIONS,
    ) -> list[_SourceRow]:
        table = DtsSourceRowRecord.__table__
        source_tables = [
            f"{region}_{suffix}"
            for region in regions
            for suffix in suffixes
        ]
        clauses = [
            table.c.source_table.in_(source_tables),
            table.c.is_deleted.is_(False),
        ]
        if dependency_name is not None and dependency_value is not None:
            dependency = {dependency_name: [str(dependency_value)]}
            clauses.append(
                table.c.dependency_keys.op("@>")(cast(dependency, JSONB))
            )
        rows = connection.execute(
            select(
                table.c.source_region,
                table.c.source_table,
                table.c.source_row,
                table.c.source_timestamp,
                table.c.last_record_id,
            ).where(*clauses)
        ).mappings()
        return [
            _SourceRow(
                region=str(item["source_region"]),
                table=str(item["source_table"]),
                row=item["source_row"],
                source_timestamp=int(item["source_timestamp"]),
                last_record_id=int(item["last_record_id"]),
            )
            for item in rows
        ]

    def _course_ids_for_dependency(
        self,
        connection: Any,
        dependency_name: str,
        dependency_value: str,
        *,
        suffixes: Sequence[str],
    ) -> list[str]:
        rows = self._active_source_rows(
            connection,
            suffixes=suffixes,
            dependency_name=dependency_name,
            dependency_value=dependency_value,
        )
        return [
            course_id
            for source in rows
            if (course_id := _string_id(source.row.get("appoint_id"))) is not None
        ]

    def _course_ids_for_pair(
        self,
        connection: Any,
        teacher_id: str,
        student_id: str,
    ) -> list[str]:
        rows = self._active_source_rows(
            connection,
            suffixes=("appoint",),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
        )
        return [
            course_id
            for source in rows
            if student_subject(source.row) == student_id
            and (course_id := _string_id(source.row.get("id"))) is not None
        ]

    def _appoint_source(
        self,
        connection: Any,
        course_id: str,
    ) -> _SourceRow | None:
        rows = self._active_source_rows(
            connection,
            suffixes=("appoint",),
            dependency_name="course_ids",
            dependency_value=course_id,
        )
        rows = [item for item in rows if _string_id(item.row.get("id")) == course_id]
        if len(rows) > 1:
            raise DtsWideProjectionError("DTS_GLOBAL_APPOINT_ID_COLLISION")
        return rows[0] if rows else None

    def _teacher_source(
        self,
        connection: Any,
        teacher_id: str,
    ) -> _SourceRow | None:
        rows = self._active_source_rows(
            connection,
            suffixes=("teacher",),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
            regions=("dom",),
        )
        rows = [item for item in rows if _string_id(item.row.get("id")) == teacher_id]
        if len(rows) > 1:
            raise DtsWideProjectionError("DTS_TEACHER_ID_COLLISION")
        return rows[0] if rows else None

    @staticmethod
    def _teacher_tombstoned(connection: Any, teacher_id: str) -> bool:
        table = DtsSourceRowRecord.__table__
        dependency = {"teacher_ids": [teacher_id]}
        return connection.execute(
            select(table.c.source_key)
            .where(
                table.c.source_region == "dom",
                table.c.source_table == "dom_teacher",
                table.c.is_deleted.is_(True),
                table.c.dependency_keys.op("@>")(cast(dependency, JSONB)),
            )
            .limit(1)
        ).first() is not None

    def _project_course(
        self,
        connection: Any,
        course_id: str,
        dirty: Mapping[str, Any],
    ) -> _ProjectionCounts:
        result = _ProjectionCounts()
        lesson_table = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(lesson_table).where(lesson_table.c["课程id"] == course_id)
        ).mappings().first()
        appoint_source = self._appoint_source(connection, course_id)
        if appoint_source is None:
            # A relation/QA event can arrive before the appoint event on the
            # same regional stream.  Absence from the change-only mirror is
            # not a delete signal; only a persisted appoint tombstone is.
            if self._appoint_tombstoned(connection, course_id):
                deleted = connection.execute(
                    delete(lesson_table).where(lesson_table.c["课程id"] == course_id)
                ).rowcount
                if existing is not None:
                    self._enqueue_teacher(
                        connection,
                        _string_id(existing["老师id"]),
                        dirty,
                    )
                if deleted:
                    result.lesson_deletes += 1
                else:
                    result.unchanged += 1
                return result
            # The incremental mirror can legitimately see a historical QA or
            # relation event whose appoint predates the subscription.  Ignore
            # it only when a whitelisted lesson-time field proves that the
            # course is before the configured cohort; unknown dates remain a
            # retryable dependency failure.
            if self._course_dependency_is_definitively_before_cohort(
                connection,
                course_id,
            ):
                deleted = connection.execute(
                    delete(lesson_table).where(
                        lesson_table.c["课程id"] == course_id
                    )
                ).rowcount
                if existing is not None:
                    self._enqueue_teacher(
                        connection,
                        _string_id(existing["老师id"]),
                        dirty,
                    )
                if deleted:
                    result.lesson_deletes += 1
                else:
                    result.unchanged += 1
                return result
            raise DtsWideProjectionError("DTS_APPOINT_DEPENDENCY_PENDING")
        if not self._appoint_in_scope(appoint_source.row):
            deleted = connection.execute(
                delete(lesson_table).where(lesson_table.c["课程id"] == course_id)
            ).rowcount
            if existing is not None:
                self._enqueue_teacher(
                    connection,
                    _string_id(existing["老师id"]),
                    dirty,
                )
            if deleted:
                result.lesson_deletes += 1
            else:
                result.unchanged += 1
            return result

        appoint = appoint_source.row
        teacher_id = _string_id(appoint.get("t_id"))
        student_id = student_subject(appoint)
        if teacher_id is None:
            raise DtsWideProjectionError("DTS_APPOINT_TEACHER_REQUIRED")
        lesson_date = _date_value(appoint.get("date")) or _date_value(
            appoint.get("start_time")
        )
        lesson_time = _time_value(appoint.get("start_time"))
        teacher_source = self._teacher_source(connection, teacher_id)
        # The monitored population is defined by the domestic teacher's
        # onboarding date.  Never materialize an overseas lesson first and
        # hope the teacher arrives later: the two DTS topics progress
        # independently and that transient row would already emit an Outbox
        # event.  A later dom_teacher event requeues every already-seen appoint
        # for this teacher from the durable source mirror.
        if teacher_source is None:
            if self._teacher_tombstoned(connection, teacher_id):
                deleted = connection.execute(
                    delete(lesson_table).where(
                        lesson_table.c["课程id"] == course_id
                    )
                ).rowcount
                if existing is not None:
                    previous_teacher_id = _string_id(existing["老师id"])
                    if (
                        previous_teacher_id is not None
                        and previous_teacher_id != teacher_id
                    ):
                        self._enqueue_teacher(
                            connection,
                            previous_teacher_id,
                            dirty,
                        )
                if deleted:
                    result.lesson_deletes += 1
                else:
                    result.unchanged += 1
                return result
            raise DtsWideProjectionError("DTS_TEACHER_DEPENDENCY_PENDING")
        if (
            not self._teacher_in_cohort(teacher_source.row)
            or not self._lesson_matches_teacher_window(
                appoint_source.region,
                lesson_date,
                teacher_source.row,
            )
        ):
            deleted = connection.execute(
                delete(lesson_table).where(lesson_table.c["课程id"] == course_id)
            ).rowcount
            self._enqueue_teacher(connection, teacher_id, dirty)
            if existing is not None:
                self._enqueue_teacher(
                    connection,
                    _string_id(existing["老师id"]),
                    dirty,
                )
            if deleted:
                result.lesson_deletes += 1
            else:
                result.unchanged += 1
            return result

        # SourceWideWorker requires the teacher projection before it can
        # consume a lesson Outbox event.  When two independent topics arrive
        # out of order, materialize the identity/zero-state teacher first;
        # this course then requeues the teacher so aggregates are recomputed
        # with the new lesson in the next dirty-key transaction.
        result.add(self._ensure_teacher_wide(connection, teacher_id))

        absence = self._absence_reason(connection, course_id, teacher_id, appoint)
        penalty_rows = [
            {
                **item.row,
                "in_time": _datetime_value(item.row.get("in_time")),
                "out_time": _datetime_value(item.row.get("out_time")),
            }
            for item in self._active_source_rows(
                connection,
                suffixes=("teacher_penalty",),
                dependency_name="course_ids",
                dependency_value=course_id,
                regions=("dom",),
            )
            if _string_id(item.row.get("t_id")) == teacher_id
        ]
        is_late, is_early = derive_penalty_flags(
            penalty_rows,
            lesson_start=appoint.get("start_time"),
            lesson_end=appoint.get("end_time"),
        )
        grading = self._latest_grading(connection, course_id)
        score = _float_value(grading.get("score")) if grading else None
        grading_type = str(grading.get("type") or "").strip().lower() if grading else ""
        feedback_detail = self._feedback_detail(connection, course_id)
        complaint_names = self._complaint_names(connection, course_id)
        is_blocked = self._relationship_assigned_to_course(
            connection,
            relation_suffix="teacher_blacklist",
            region=appoint_source.region,
            teacher_id=teacher_id,
            student_id=student_id,
            course_id=course_id,
        )
        is_favorited = self._relationship_assigned_to_course(
            connection,
            relation_suffix="teacher_favorite",
            region=appoint_source.region,
            teacher_id=teacher_id,
            student_id=student_id,
            course_id=course_id,
        )
        camera = self._has_course_record(
            connection,
            "qa_task_close_camera_record",
            course_id,
            appoint_source.region,
        )
        fake_early = self._has_course_record(
            connection,
            "qa_task_fake_early_leave_record",
            course_id,
            appoint_source.region,
        )
        cpu = self._qa_json_flag(
            connection,
            course_id=course_id,
            region=appoint_source.region,
            source_type="CPU",
            json_key="cpu",
        )
        network = self._qa_json_flag(
            connection,
            course_id=course_id,
            region=appoint_source.region,
            source_type="NETWORK_DELAY",
            json_key="network_delay",
        )
        values: dict[str, Any] = {
            "课程id": course_id,
            "上课日期": lesson_date,
            "上课时间": lesson_time,
            "是否高峰": is_peak_lesson(
                appoint_source.region,
                appoint.get("week")
                if appoint.get("week") is not None
                else (lesson_date.isoweekday() if lesson_date else None),
                lesson_time,
            ),
            "老师id": teacher_id,
            "学员id": student_id,
            "课程状态": _non_empty(appoint.get("status")),
            "缺席原因明细": absence,
            "迟到": is_late,
            "早退": is_early,
            "差评分": score if score in {1.0, 2.0} else None,
            "差评标签": (
                score in {1.0, 2.0} or grading_type == "unsatisfactory"
                if grading is not None
                else None
            ),
            "投诉一级分类": complaint_names[0],
            "投诉二级分类": complaint_names[1],
            "投诉三级分类": complaint_names[2],
            "是否拉黑": is_blocked,
            "收藏": is_favorited,
            "好评标签": (
                score in {4.0, 5.0} or grading_type == "satisfactory"
                if grading is not None
                else None
            ),
            "评价详情": feedback_detail,
            "未开摄像头": camera,
            "cpu占用过高": cpu,
            "网络延迟过高": network,
            "假早退": fake_early,
        }
        changed = self._upsert(
            connection,
            lesson_table,
            values,
            primary_keys=("课程id",),
        )
        old_teacher = _string_id(existing["老师id"]) if existing is not None else None
        self._enqueue_teacher(connection, teacher_id, dirty)
        if old_teacher != teacher_id:
            self._enqueue_teacher(connection, old_teacher, dirty)
        if changed:
            result.lesson_upserts += 1
        else:
            result.unchanged += 1
        return result

    def _ensure_teacher_wide(
        self,
        connection: Any,
        teacher_id: str,
    ) -> _ProjectionCounts:
        target = TeacherSourceWideRecord.__table__
        existing = connection.execute(
            select(target.c.tchr_id).where(target.c.tchr_id == teacher_id).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return _ProjectionCounts()
        projected = self._project_teacher(connection, teacher_id)
        if projected.teacher_upserts != 1:
            raise DtsWideProjectionError("DTS_TEACHER_WIDE_DEPENDENCY_PENDING")
        return projected

    @staticmethod
    def _appoint_tombstoned(connection: Any, course_id: str) -> bool:
        table = DtsSourceRowRecord.__table__
        dependency = {"course_ids": [course_id]}
        return connection.execute(
            select(table.c.source_key)
            .where(
                table.c.source_table.in_(("dom_appoint", "ovs_appoint")),
                table.c.is_deleted.is_(True),
                table.c.dependency_keys.op("@>")(cast(dependency, JSONB)),
            )
            .limit(1)
        ).first() is not None

    def _course_dependency_is_definitively_before_cohort(
        self,
        connection: Any,
        course_id: str,
    ) -> bool:
        """Return true only when mirrored lesson-time evidence is pre-cohort."""

        known_dates: list[date] = []
        rows = self._active_source_rows(
            connection,
            suffixes=tuple(_COURSE_DATE_FIELDS_BY_SUFFIX),
            dependency_name="course_ids",
            dependency_value=course_id,
        )
        for item in rows:
            _region, _separator, suffix = item.table.partition("_")
            for field_name in _COURSE_DATE_FIELDS_BY_SUFFIX.get(suffix, ()):
                lesson_date = _date_value(item.row.get(field_name))
                if lesson_date is not None:
                    known_dates.append(lesson_date)
                    break
        return bool(known_dates) and all(
            lesson_date < self.settings.cohort_start
            for lesson_date in known_dates
        )

    @staticmethod
    def _appoint_in_scope(row: Mapping[str, Any]) -> bool:
        return (
            str(row.get("use_point") or "") == "buy"
            and str(row.get("status") or "") not in {"cancel", "on"}
            and student_subject(row) is not None
        )

    def _lesson_matches_teacher_window(
        self,
        region: str,
        lesson_date: date | None,
        teacher: Mapping[str, Any],
    ) -> bool:
        region_match = teacher_matches_region(region, teacher.get("course"))
        if region_match is not True:
            return False
        onboard = _date_value(teacher.get("status_on_time"))
        if onboard is None or lesson_date is None:
            return False
        return onboard <= lesson_date <= onboard + timedelta(days=29)

    def _teacher_in_cohort(self, teacher: Mapping[str, Any]) -> bool:
        onboard = _date_value(teacher.get("status_on_time"))
        if onboard is None or onboard < self.settings.cohort_start:
            return False
        return (
            self.settings.cohort_end_exclusive is None
            or onboard < self.settings.cohort_end_exclusive
        )

    def _absence_reason(
        self,
        connection: Any,
        course_id: str,
        teacher_id: str,
        appoint: Mapping[str, Any],
    ) -> str | None:
        rows = [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("teacher_absent_reason",),
                dependency_name="course_ids",
                dependency_value=course_id,
                regions=("dom",),
            )
            if _string_id(item.row.get("t_id")) == teacher_id
        ]
        latest = _latest(rows, time_fields=("add_time",))
        return _non_empty(
            latest.get("reason_desc") if latest else None,
            latest.get("reason_type") if latest else None,
            appoint.get("cancel_reason"),
        )

    def _latest_grading(
        self,
        connection: Any,
        course_id: str,
    ) -> Mapping[str, Any] | None:
        rows = [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("user_teacher_grading",),
                dependency_name="course_ids",
                dependency_value=course_id,
            )
            if _int_value(item.row.get("is_del")) in (None, 0)
            and _int_value(item.row.get("status")) in (None, 0)
        ]
        return _latest(rows, time_fields=("update_time", "create_time"))

    def _feedback_detail(self, connection: Any, course_id: str) -> str | None:
        rows = [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("grading_label_log",),
                dependency_name="course_ids",
                dependency_value=course_id,
            )
            if _int_value(item.row.get("type")) == 1
            and str(item.row.get("status") or "").lower() == "normal"
        ]
        labels: dict[tuple[int, int | str], str] = {}
        for row in rows:
            label_id = _sortable_id(row.get("label_id"))
            label_name = _non_empty(row.get("label_name"))
            if label_name is not None:
                labels[label_id] = label_name
        return ",".join(labels[key] for key in sorted(labels)) or None

    def _complaint_names(
        self,
        connection: Any,
        course_id: str,
    ) -> tuple[str | None, str | None, str | None]:
        user_rows = [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("user_complaint",),
                dependency_name="course_ids",
                dependency_value=course_id,
            )
        ]
        complaint_rows = [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("complaint",),
                dependency_name="course_ids",
                dependency_value=course_id,
            )
        ]
        complaints = reduce_latest_complaints(user_rows, complaint_rows)
        latest = _latest(complaints, time_fields=("course_date",))
        if latest is None:
            return None, None, None
        categories = {
            _string_id(item.row.get("id")): item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("complaint_cate",),
                regions=("dom",),
            )
            if _string_id(item.row.get("id")) is not None
        }
        ids = (
            _complaint_category_id(latest.get("complaint_type")),
            _complaint_category_id(latest.get("complaint_type_child")),
            _complaint_category_id(latest.get("complaint_type_grandson")),
        )
        if any(
            category_id is not None and category_id not in categories
            for category_id in ids
        ):
            # Complaint categories are long-lived shared reference data and
            # may predate the new-teacher cohort.  Do not emit a lesson with a
            # silently incomplete category path; the activation runbook must
            # seed or lazily hydrate the small dictionary first.
            raise DtsWideProjectionError(
                "DTS_COMPLAINT_CATEGORY_DEPENDENCY_PENDING"
            )
        names = tuple(
            _non_empty(categories.get(category_id, {}).get("cate_cn_name"))
            if category_id is not None
            else None
            for category_id in ids
        )
        return names[0], names[1], names[2]

    def _relationship_assigned_to_course(
        self,
        connection: Any,
        *,
        relation_suffix: str,
        region: str,
        teacher_id: str,
        student_id: str | None,
        course_id: str,
    ) -> bool:
        if student_id is None:
            return False
        relationships = self._active_source_rows(
            connection,
            suffixes=(relation_suffix,),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
            regions=(region,),
        )
        relationships = [
            item
            for item in relationships
            if student_subject(item.row) == student_id
            and (
                relation_suffix != "teacher_blacklist"
                or self._active_blacklist(item.row)
            )
        ]
        candidates = [
            item
            for item in self._active_source_rows(
                connection,
                suffixes=("appoint",),
                dependency_name="teacher_ids",
                dependency_value=teacher_id,
                regions=(region,),
            )
            if student_subject(item.row) == student_id
            and self._appoint_in_scope(item.row)
            and _datetime_value(item.row.get("end_time")) is not None
        ]
        for relationship in relationships:
            added_at = _datetime_value(relationship.row.get("add_time"))
            if added_at is None or not candidates:
                continue

            def nearest_key(candidate: _SourceRow) -> tuple[float, int, float, tuple[int, int | str]]:
                ended_at = _datetime_value(candidate.row.get("end_time"))
                if ended_at is None:
                    raise DtsWideProjectionError("DTS_APPOINT_END_TIME_REQUIRED")
                distance = abs((added_at - ended_at).total_seconds())
                historical_rank = 0 if ended_at <= added_at else 1
                return (
                    distance,
                    historical_rank,
                    -ended_at.timestamp(),
                    _sortable_id(candidate.row.get("id")),
                )

            selected = min(candidates, key=nearest_key)
            if _string_id(selected.row.get("id")) == course_id:
                return True
        return False

    @staticmethod
    def _active_blacklist(row: Mapping[str, Any]) -> bool:
        valid_end = _datetime_value(row.get("valid_end_time"))
        return (
            _truthy(row.get("is_valid_forever"))
            or valid_end is None
            or valid_end.year >= 2999
        )

    def _has_course_record(
        self,
        connection: Any,
        suffix: str,
        course_id: str,
        region: str,
    ) -> bool:
        return bool(
            self._active_source_rows(
                connection,
                suffixes=(suffix,),
                dependency_name="course_ids",
                dependency_value=course_id,
                regions=(region,),
            )
        )

    def _qa_json_flag(
        self,
        connection: Any,
        *,
        course_id: str,
        region: str,
        source_type: str,
        json_key: str,
    ) -> bool | None:
        rows = self._active_source_rows(
            connection,
            suffixes=("qa_ac_classroom_record",),
            dependency_name="course_ids",
            dependency_value=course_id,
            regions=(region,),
        )
        malformed = False
        for source in rows:
            if str(source.row.get("type") or "").upper() != source_type:
                continue
            info = source.row.get("info")
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except json.JSONDecodeError:
                    malformed = True
                    continue
            if not isinstance(info, Mapping):
                malformed = True
                continue
            items = info.get(json_key)
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
                malformed = True
                continue
            if any(
                isinstance(item, Mapping)
                and _string_id(item.get("appoint_id")) == course_id
                for item in items
            ):
                return True
        return None if malformed else False

    def _enqueue_teacher(
        self,
        connection: Any,
        teacher_id: str | None,
        origin: Mapping[str, Any],
    ) -> None:
        if teacher_id is None:
            return
        table = DtsDirtyKeyRecord.__table__
        statement = insert(table).values(
            key_type="TEACHER",
            key_part_1=teacher_id,
            key_part_2="",
            status="PENDING",
            pending_event_count=1,
            attempt_count=0,
            last_source_region=str(origin["last_source_region"]),
            last_source_table=origin.get("last_source_table"),
            last_topic=str(origin["last_topic"]),
            last_partition=int(origin["last_partition"]),
            last_offset=int(origin["last_offset"]),
            issue_codes=[],
            row_version=1,
        )
        excluded = statement.excluded
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    table.c.key_type,
                    table.c.key_part_1,
                    table.c.key_part_2,
                ],
                set_={
                    "status": "PENDING",
                    "pending_event_count": table.c.pending_event_count + 1,
                    "attempt_count": 0,
                    "last_source_region": excluded.last_source_region,
                    "last_source_table": excluded.last_source_table,
                    "last_topic": excluded.last_topic,
                    "last_partition": excluded.last_partition,
                    "last_offset": excluded.last_offset,
                    "last_error_code": None,
                    "next_attempt_at": None,
                    "claimed_at": None,
                    "claimed_by": None,
                    "row_version": table.c.row_version + 1,
                    "last_seen_at": text("clock_timestamp()"),
                },
            )
        )

    @staticmethod
    def _delete_teacher_projection(
        connection: Any,
        teacher_id: str,
    ) -> _ProjectionCounts:
        """Delete dependent lessons before the teacher in one transaction."""

        lesson_target = LessonSourceWideRecord.__table__
        teacher_target = TeacherSourceWideRecord.__table__
        lesson_deletes = int(
            connection.execute(
                delete(lesson_target).where(
                    lesson_target.c["老师id"] == teacher_id
                )
            ).rowcount
            or 0
        )
        teacher_deletes = int(
            connection.execute(
                delete(teacher_target).where(
                    teacher_target.c.tchr_id == teacher_id
                )
            ).rowcount
            or 0
        )
        result = _ProjectionCounts(
            lesson_deletes=lesson_deletes,
            teacher_deletes=teacher_deletes,
        )
        if lesson_deletes == 0 and teacher_deletes == 0:
            result.unchanged += 1
        return result

    def _project_teacher(
        self,
        connection: Any,
        teacher_id: str,
    ) -> _ProjectionCounts:
        result = _ProjectionCounts()
        target = TeacherSourceWideRecord.__table__
        teacher_source = self._teacher_source(connection, teacher_id)
        if teacher_source is None:
            # A teacher dirty key can originate from an overseas lesson before
            # the domestic shared teacher subscription exists.  That is not a
            # delete signal; the eventual dom_teacher event will requeue it.
            if self._teacher_tombstoned(connection, teacher_id):
                return self._delete_teacher_projection(connection, teacher_id)
            result.unchanged += 1
            return result
        teacher = teacher_source.row
        onboard = _date_value(teacher.get("status_on_time"))
        if not self._teacher_in_cohort(teacher):
            return self._delete_teacher_projection(connection, teacher_id)
        onboard_end = onboard + timedelta(days=29)
        lessons = self._teacher_lessons(connection, teacher_id, onboard, onboard_end)
        completed = [row for row in lessons if row["课程状态"] == _COMPLETED_STATUS]
        absent = [row for row in lessons if row["课程状态"] == _ABSENT_STATUS]
        during_absence_ids = self._during_lesson_absence_course_ids(
            connection,
            teacher_id,
        )
        late = [
            row
            for row in lessons
            if (
                (row["课程状态"] == _COMPLETED_STATUS and row["迟到"] is True)
                or str(row["课程id"]) in during_absence_ids
            )
        ]
        early = [
            row
            for row in lessons
            if row["课程状态"] == _COMPLETED_STATUS and row["早退"] is True
        ]
        anomalous_ids = {
            str(row["课程id"])
            for row in (*absent, *late, *early)
        }
        perfect = [
            row
            for row in completed
            if row["迟到"] is not True
            and row["早退"] is not True
            and str(row["课程id"]) not in during_absence_ids
        ]
        course_ids = {str(row["课程id"]) for row in lessons}
        no_notice = self._no_notice_course_ids(connection, teacher_id) & course_ids
        evaluated_ids = {
            course_id
            for course_id in course_ids
            if self._latest_grading(connection, course_id) is not None
        }
        complaint_total, valid_complaint_total = self._teacher_complaint_counts(
            connection,
            course_ids,
        )
        completed_students = {
            str(row["学员id"])
            for row in completed
            if row["学员id"] is not None
        }
        favorite_students = {
            str(row["学员id"])
            for row in lessons
            if row["收藏"] is True and row["学员id"] is not None
        }
        blocked_students = {
            str(row["学员id"])
            for row in lessons
            if row["是否拉黑"] is True and row["学员id"] is not None
        }
        schedules = self._teacher_schedules(connection, teacher_id, onboard, onboard_end)
        peak_schedule = [
            row
            for row in schedules
            if self._schedule_is_peak(self._teacher_area(teacher), row) is True
        ]
        status_on = onboard
        status_off = _date_value(teacher.get("status_off_time"))
        snapshot_date = connection.execute(select(text("current_date"))).scalar_one()
        job_days = ((status_off or snapshot_date) - status_on).days
        total_booked = len(lessons)
        total_completed = len(completed)
        total_evaluated = len(evaluated_ids)
        first_completed_students = len(completed_students)
        total_slots = len({_slot_identity(row) for row in schedules})
        peak_slots = len({_slot_identity(row) for row in peak_schedule})
        slot_days = len({_date_value(row.get("date")) for row in schedules})
        peak_slot_days = len({_date_value(row.get("date")) for row in peak_schedule})
        certifications = self._active_source_rows(
            connection,
            suffixes=("teacher_certification",),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
            regions=("dom",),
        )
        values: dict[str, Any] = {
            "tchr_id": teacher_id,
            "real_name": _non_empty(teacher.get("real_name")),
            "center_type_id": _string_id(teacher.get("center_type")),
            "center_type_desc": _CENTER_DESCRIPTIONS.get(
                _int_value(teacher.get("center_type"))
            ),
            "bu": self._teacher_bu(teacher),
            "status": _non_empty(teacher.get("status")),
            "status_on_date": status_on,
            "status_off_date": status_off,
            "last_on_date": _date_value(teacher.get("last_on_time")),
            "job_days": job_days,
            "job_month": math.floor(job_days / 30) + 1 if job_days >= 0 else None,
            "teach_area_type": self._teacher_area(teacher),
            "onboard_date": onboard,
            "onboard_30d_end_date": onboard_end,
            "first_open_slot_dt": min(
                (_date_value(row.get("date")) for row in schedules),
                default=None,
            ),
            "first_booked_dt": min(
                (row["上课日期"] for row in lessons if row["上课日期"] is not None),
                default=None,
            ),
            "first_completed_dt": min(
                (row["上课日期"] for row in completed if row["上课日期"] is not None),
                default=None,
            ),
            "total_booked_cnt": total_booked,
            "peak_booked_cnt": sum(row["是否高峰"] is True for row in lessons),
            "total_completed_cnt": total_completed,
            "peak_completed_cnt": sum(row["是否高峰"] is True for row in completed),
            "absent_cnt": len(absent),
            "late_cnt": len({str(row["课程id"]) for row in late}),
            "early_cnt": len({str(row["课程id"]) for row in early}),
            "anomaly_cnt": len(anomalous_ids),
            "perfect_cnt": len(perfect),
            "no_notice_cnt": len(no_notice),
            "first_completed_student_cnt": first_completed_students,
            "feedback_total_eval_cnt": total_evaluated,
            "feedback_praise_cnt": sum(row["好评标签"] is True for row in lessons),
            "feedback_negative_cnt": sum(row["差评标签"] is True for row in lessons),
            "feedback_complaint_cnt": complaint_total,
            "feedback_valid_complaint_cnt": valid_complaint_total,
            "feedback_favorite_cnt": len(favorite_students),
            "feedback_block_cnt": len(blocked_students),
            "total_slot_cnt": total_slots,
            "reg_slot_cnt": len(
                {
                    _slot_identity(row)
                    for row in schedules
                    if str(row.get("project_code") or "") == "1v1"
                }
            ),
            "peak_slot_cnt": peak_slots,
            "slot_days": slot_days,
            "peak_slot_days": peak_slot_days,
            "reliability_absent_rate": _safe_rate(len(absent), total_booked),
            "reliability_late_rate": _safe_rate(len(late), total_completed),
            "reliability_early_leave_rate": _safe_rate(len(early), total_completed),
            "reliability_late_early_rate": _safe_rate(
                len(late) + len(early),
                total_completed,
            ),
            "feedback_praise_rate": _safe_rate(
                sum(row["好评标签"] is True for row in lessons),
                total_evaluated,
            ),
            "feedback_negative_rate": _safe_rate(
                sum(row["差评标签"] is True for row in lessons),
                total_evaluated,
            ),
            "feedback_complaint_rate": _safe_rate(
                complaint_total,
                total_completed,
            ),
            "feedback_favorite_rate": _safe_rate(
                len(favorite_students),
                first_completed_students,
            ),
            "feedback_block_rate": _safe_rate(
                len(blocked_students),
                first_completed_students,
            ),
            "feedback_eval_rate": _safe_rate(total_evaluated, total_completed),
            "capacity_avg_completed_per_day": total_completed / 30,
            "capacity_peak_slot_rate": _safe_rate(peak_slots, total_slots),
            "capacity_key_slot_day_rate": _safe_rate(peak_slot_days, slot_days),
            "is_cpl_tesol": self._tesol_state(
                connection,
                teacher_id,
                certifications,
            ),
            "is_self_introduce": None,
        }
        changed = self._upsert(
            connection,
            target,
            values,
            primary_keys=("tchr_id",),
        )
        if changed:
            result.teacher_upserts += 1
        else:
            result.unchanged += 1
        return result

    def _teacher_lessons(
        self,
        connection: Any,
        teacher_id: str,
        start: date,
        end: date,
    ) -> list[Mapping[str, Any]]:
        table = LessonSourceWideRecord.__table__
        return list(
            connection.execute(
                select(table).where(
                    table.c["老师id"] == teacher_id,
                    table.c["上课日期"] >= start,
                    table.c["上课日期"] <= end,
                )
            ).mappings()
        )

    def _teacher_schedules(
        self,
        connection: Any,
        teacher_id: str,
        start: date,
        end: date,
    ) -> list[Mapping[str, Any]]:
        return [
            item.row
            for item in self._active_source_rows(
                connection,
                suffixes=("teacher_class_schedule",),
                dependency_name="teacher_ids",
                dependency_value=teacher_id,
                regions=("dom",),
            )
            if str(item.row.get("status") or "").lower() == "on"
            and (schedule_date := _date_value(item.row.get("date"))) is not None
            and start <= schedule_date <= end
        ]

    def _no_notice_course_ids(self, connection: Any, teacher_id: str) -> set[str]:
        rows = self._active_source_rows(
            connection,
            suffixes=("teacher_absent_reason",),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
            regions=("dom",),
        )
        return {
            course_id
            for item in rows
            if str(item.row.get("reason_type") or "").strip().lower()
            == "no notification"
            and (course_id := _string_id(item.row.get("appoint_id"))) is not None
        }

    def _during_lesson_absence_course_ids(
        self,
        connection: Any,
        teacher_id: str,
    ) -> set[str]:
        rows = self._active_source_rows(
            connection,
            suffixes=("teacher_absent_reason",),
            dependency_name="teacher_ids",
            dependency_value=teacher_id,
            regions=("dom",),
        )
        result: set[str] = set()
        for item in rows:
            course_id = _string_id(item.row.get("appoint_id"))
            added_at = _datetime_value(item.row.get("add_time"))
            if course_id is None or added_at is None:
                continue
            appoint = self._appoint_source(connection, course_id)
            started_at = (
                _datetime_value(appoint.row.get("start_time"))
                if appoint is not None
                else None
            )
            if started_at is not None and added_at >= started_at:
                result.add(course_id)
        return result

    def _teacher_complaint_counts(
        self,
        connection: Any,
        course_ids: set[str],
    ) -> tuple[int, int]:
        all_keys: set[tuple[str, str]] = set()
        valid_courses: set[str] = set()
        for course_id in course_ids:
            user_rows = [
                item.row
                for item in self._active_source_rows(
                    connection,
                    suffixes=("user_complaint",),
                    dependency_name="course_ids",
                    dependency_value=course_id,
                )
            ]
            complaint_rows = [
                item.row
                for item in self._active_source_rows(
                    connection,
                    suffixes=("complaint",),
                    dependency_name="course_ids",
                    dependency_value=course_id,
                )
            ]
            for row in (*user_rows, *complaint_rows):
                student_id = student_subject(row) or ""
                all_keys.add((student_id, course_id))
            if reduce_latest_complaints(user_rows, complaint_rows):
                valid_courses.add(course_id)
        return len({key[1] for key in all_keys}), len(valid_courses)

    @staticmethod
    def _tesol_state(
        connection: Any,
        teacher_id: str,
        active_certifications: Sequence[_SourceRow],
    ) -> bool | None:
        seen_tesol = [
            item
            for item in active_certifications
            if str(item.row.get("certification_type") or "").strip().lower()
            == "tesol"
        ]
        if any(
            _int_value(item.row.get("certification_status")) == 1
            for item in seen_tesol
        ):
            return True
        if seen_tesol:
            return False
        table = DtsSourceRowRecord.__table__
        dependency = {"teacher_ids": [teacher_id]}
        deleted_rows = connection.execute(
            select(table.c.source_row).where(
                table.c.source_region == "dom",
                table.c.source_table == "dom_teacher_certification",
                table.c.is_deleted.is_(True),
                table.c.dependency_keys.op("@>")(cast(dependency, JSONB)),
            )
        ).scalars()
        if any(
            str(row.get("certification_type") or "").strip().lower() == "tesol"
            for row in deleted_rows
        ):
            return False
        # Change-only consumption cannot prove that an untouched teacher has
        # no TESOL certificate, so absence remains unknown rather than false.
        return None

    @staticmethod
    def _teacher_bu(teacher: Mapping[str, Any]) -> str | None:
        code = _int_value(teacher.get("is_full_time"))
        if code in _HBT_CODES:
            return "HBT"
        if code in _OBT_CODES:
            return "OBT"
        return None

    @staticmethod
    def _teacher_area(teacher: Mapping[str, Any]) -> str | None:
        course = teacher.get("course")
        if course is None:
            return None
        rendered = str(course)
        return "ovs" if "global_cn" in rendered or "global_pool" in rendered else "dmo"

    @staticmethod
    def _schedule_is_peak(
        teacher_area: str | None,
        row: Mapping[str, Any],
    ) -> bool | None:
        schedule_date = _date_value(row.get("date"))
        raw_time = row.get("time")
        rendered = str(raw_time or "").strip()
        if "_" in rendered:
            rendered = rendered.rsplit("_", 1)[-1]
        schedule_time = _time_value(rendered) or _time_value(row.get("time_slot"))
        if schedule_date is None or schedule_time is None or teacher_area is None:
            return None
        region = "ovs" if teacher_area == "ovs" else "dom"
        week = schedule_date.isoweekday()
        return is_peak_lesson(region, week, schedule_time)

    @staticmethod
    def _upsert(
        connection: Any,
        table: Any,
        values: Mapping[str, Any],
        *,
        primary_keys: tuple[str, ...],
    ) -> bool:
        statement = insert(table).values(dict(values))
        excluded = statement.excluded
        mutable = tuple(name for name in values if name not in primary_keys)
        changed = or_(
            *(table.c[name].is_distinct_from(excluded[name]) for name in mutable)
        )
        result = connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c[name] for name in primary_keys],
                set_={name: excluded[name] for name in mutable},
                where=changed,
            ).returning(table.c[primary_keys[0]])
        )
        # psycopg 3 may expose rowcount=-1 after SQLAlchemy closes an INSERT
        # cursor.  A returned non-null primary key is the unambiguous
        # PostgreSQL signal that this INSERT/UPDATE changed the target row.
        return result.scalar_one_or_none() is not None


def _slot_identity(row: Mapping[str, Any]) -> str:
    return (
        _string_id(row.get("date_slot"))
        or _string_id(row.get("id"))
        or json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    )


__all__ = [
    "DtsWideProjectionError",
    "DtsWideProjectionSettings",
    "DtsWideProjector",
]
