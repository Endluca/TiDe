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
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, exists, func, select, tuple_, update
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
_NO_NOTICE_DETAIL = "No Notification"
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
_COURSE_TARGET_SUFFIXES = frozenset(
    {
        "complaint",
        "grading_label_log",
        "qa_task_close_camera_record",
        "teacher_absent_reason",
        "teacher_penalty",
        "user_teacher_grading",
    }
)
_TEACHER_TARGET_SUFFIXES = frozenset(
    {
        "teacher_certification",
        "teacher_class_schedule",
        "teacher_favorite",
        "teacher_blacklist",
    }
)
_TARGET_PREFETCH_CHUNK_SIZE = 1_000


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
    batch_prefiltered: int = 0
    batch_target_queries: int = 0
    relationship_cache_hits: int = 0
    relationship_cache_misses: int = 0
    relationship_fallback_queries: int = 0
    events_by_suffix: dict[str, int] = field(default_factory=dict)
    ignored_by_suffix: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "ignored": self.ignored,
            "lesson_upserts": self.lesson_upserts,
            "lesson_deletes": self.lesson_deletes,
            "teacher_upserts": self.teacher_upserts,
            "teacher_deletes": self.teacher_deletes,
            "teacher_delta_updates": self.teacher_delta_updates,
            "slot_activations": self.slot_activations,
            "batch_prefiltered": self.batch_prefiltered,
            "batch_target_queries": self.batch_target_queries,
            "relationship_cache_hits": self.relationship_cache_hits,
            "relationship_cache_misses": self.relationship_cache_misses,
            "relationship_fallback_queries": self.relationship_fallback_queries,
            "events_by_suffix": dict(sorted(self.events_by_suffix.items())),
            "ignored_by_suffix": dict(sorted(self.ignored_by_suffix.items())),
        }


@dataclass(frozen=True)
class _RelationshipDependency:
    source_region: str
    teacher_id: str
    student_id: str
    added: datetime


@dataclass
class _DirectBatchTargets:
    teacher_ids: set[str]
    lesson_teachers: dict[tuple[str, str], str | None]
    lesson_statuses: dict[tuple[str, str], str | None]
    relationship_courses: dict[_RelationshipDependency, str | None]
    volatile_relationship_pairs: set[tuple[str, str, str]]
    volatile_relationship_teachers: set[str]


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
    return "ovs" if "global_cn" in rendered or "global_pool" in rendered else "dom"


def _center_type_description(value: Any) -> str:
    code = _int(value)
    if code == 1:
        return "CBT"
    if code == 5:
        return "TBT"
    return "HBT"


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
        source_field_types=event.source_field_types,
        source_field_type_numbers=event.source_field_type_numbers,
        source_images_complete=event.source_images_complete,
        source_image_profile_id=event.source_image_profile_id,
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
        self._active_batch_targets: _DirectBatchTargets | None = None

    def drain_counts(self) -> dict[str, Any]:
        result = self._counts.as_dict()
        self._counts = _DirectCounts()
        return result

    def apply(self, connection: Any, event: DtsChangeEvent) -> None:
        event = direct_projection_event(event)
        self._apply_normalized(connection, event)

    def _apply_normalized(self, connection: Any, event: DtsChangeEvent) -> None:
        self._counts.events += 1
        suffix = source_table_suffix(event)
        suffix_name = suffix or "<unmapped>"
        self._increment_suffix(self._counts.events_by_suffix, suffix_name)
        if suffix is None:
            self._counts.ignored += 1
            self._increment_suffix(self._counts.ignored_by_suffix, suffix_name)
            return
        handler = getattr(self, f"_apply_{suffix}", None)
        if handler is None:
            self._counts.ignored += 1
            self._increment_suffix(self._counts.ignored_by_suffix, suffix_name)
            return
        targets = self._active_batch_targets
        if targets is not None and self._missing_batch_target(
            event,
            suffix=suffix,
            targets=targets,
        ):
            self._counts.ignored += 1
            self._counts.batch_prefiltered += 1
            self._increment_suffix(self._counts.ignored_by_suffix, suffix_name)
            return
        before_teacher_upserts = self._counts.teacher_upserts
        before_teacher_deletes = self._counts.teacher_deletes
        before_lesson_upserts = self._counts.lesson_upserts
        before_lesson_deletes = self._counts.lesson_deletes
        before_ignored = self._counts.ignored
        handler(connection, event)
        ignored_delta = self._counts.ignored - before_ignored
        if ignored_delta:
            self._increment_suffix(
                self._counts.ignored_by_suffix,
                suffix_name,
                ignored_delta,
            )
        if targets is not None:
            self._refresh_batch_targets(
                event,
                suffix=suffix,
                targets=targets,
                teacher_upserted=(
                    self._counts.teacher_upserts > before_teacher_upserts
                ),
                teacher_deleted=(
                    self._counts.teacher_deletes > before_teacher_deletes
                ),
                lesson_upserted=(
                    self._counts.lesson_upserts > before_lesson_upserts
                ),
                lesson_deleted=(
                    self._counts.lesson_deletes > before_lesson_deletes
                ),
            )

    @staticmethod
    def _increment_suffix(
        counts: dict[str, int],
        suffix: str,
        increment: int = 1,
    ) -> None:
        counts[suffix] = counts.get(suffix, 0) + increment

    def apply_batch(
        self,
        connection: Any,
        events: Sequence[DtsChangeEvent],
    ) -> None:
        """Apply an ordered batch after one bounded target-dependency lookup.

        Most events seen after a mid-stream reset are updates/deletes whose
        teacher or lesson target does not exist. Looking up those targets one
        event at a time amplifies a no-op batch into hundreds of SQL round
        trips. The snapshot below is only a skip index: events that can create
        rows still run in offset order, and successful creates/deletes update
        the index before the next event is evaluated.
        """

        normalized = tuple(direct_projection_event(event) for event in events)
        targets = self._load_batch_targets(connection, normalized)
        if self._active_batch_targets is not None:
            raise RuntimeError("DTS_DIRECT_BATCH_REENTRY_NOT_ALLOWED")
        self._active_batch_targets = targets
        try:
            for event in normalized:
                self._apply_normalized(connection, event)
        finally:
            self._active_batch_targets = None

    def _load_batch_targets(
        self,
        connection: Any,
        events: Sequence[DtsChangeEvent],
    ) -> _DirectBatchTargets:
        teacher_ids: set[str] = set()
        course_identities: set[tuple[str, str]] = set()
        relationship_dependencies: set[_RelationshipDependency] = set()
        volatile_relationship_pairs: set[tuple[str, str, str]] = set()
        volatile_relationship_teachers: set[str] = set()
        for event in events:
            suffix = source_table_suffix(event)
            if suffix is None:
                continue
            teacher_ids.update(self._teacher_dependencies(event, suffix))
            course_identities.update(
                (event.source_region, course_id)
                for course_id in self._course_dependencies(event, suffix)
            )
            relationship_dependencies.update(
                self._relationship_dependencies(event, suffix)
            )
            if suffix == "teacher":
                volatile_relationship_teachers.update(
                    self._teacher_dependencies(event, suffix)
                )
            elif suffix == "appoint":
                volatile_relationship_pairs.update(
                    (event.source_region, *pair)
                    for row in self._event_rows(event)
                    if (pair := self._appoint_relationship_pair(row)) is not None
                )

        existing_teachers: set[str] = set()
        teacher_target = TeacherSourceWideRecord.__table__
        for chunk in self._chunks(teacher_ids):
            existing_teachers.update(
                str(value)
                for value in connection.execute(
                    select(teacher_target.c.tchr_id).where(
                        teacher_target.c.tchr_id.in_(chunk)
                    )
                ).scalars()
            )
            self._counts.batch_target_queries += 1

        lesson_teachers: dict[tuple[str, str], str | None] = {}
        lesson_statuses: dict[tuple[str, str], str | None] = {}
        lesson_target = LessonSourceWideRecord.__table__
        for chunk in self._identity_chunks(course_identities):
            rows = list(
                connection.execute(
                    select(
                        lesson_target.c.source_region,
                        lesson_target.c["课程id"],
                        lesson_target.c["老师id"],
                        lesson_target.c["课程状态"],
                    ).where(
                        tuple_(
                            lesson_target.c.source_region,
                            lesson_target.c["课程id"],
                        ).in_(chunk)
                    )
                ).mappings()
            )
            lesson_teachers.update(
                {
                    (str(row["source_region"]), str(row["课程id"])):
                        _string(row["老师id"])
                    for row in rows
                }
            )
            lesson_statuses.update(
                {
                    (str(row["source_region"]), str(row["课程id"])):
                        _string(row["课程状态"])
                    for row in rows
                }
            )
            self._counts.batch_target_queries += 1

        # Relationship events need the latest completed lesson at their own
        # event time. Load stable teacher-student histories once per batch.
        # Any teacher/course mutation in the same batch is deliberately marked
        # volatile and keeps the original sequential query path below.
        relationship_courses = {
            dependency: None for dependency in relationship_dependencies
        }
        stable_dependencies = {
            dependency
            for dependency in relationship_dependencies
            if self._relationship_dependency_is_stable(
                dependency,
                volatile_relationship_pairs=volatile_relationship_pairs,
                volatile_relationship_teachers=volatile_relationship_teachers,
            )
        }
        candidate_rows: dict[
            tuple[str, str, str],
            list[tuple[datetime, str]],
        ] = {}
        relationship_pairs = {
            (
                dependency.source_region,
                dependency.teacher_id,
                dependency.student_id,
            )
            for dependency in stable_dependencies
        }
        for chunk in self._triple_chunks(relationship_pairs):
            rows = connection.execute(
                select(
                    lesson_target.c.source_region,
                    lesson_target.c["课程id"],
                    lesson_target.c["老师id"],
                    lesson_target.c["学员id"],
                    lesson_target.c["上课日期"],
                    lesson_target.c["上课时间"],
                ).where(
                    tuple_(
                        lesson_target.c.source_region,
                        lesson_target.c["老师id"],
                        lesson_target.c["学员id"],
                    ).in_(chunk),
                    lesson_target.c["课程状态"] == _COMPLETED_STATUS,
                    lesson_target.c["上课日期"].is_not(None),
                    lesson_target.c["上课时间"].is_not(None),
                )
            ).mappings()
            for row in rows:
                lesson_date = row["上课日期"]
                lesson_time = row["上课时间"]
                if not isinstance(lesson_date, date) or not isinstance(
                    lesson_time,
                    time,
                ):
                    continue
                pair = (
                    str(row["source_region"]),
                    str(row["老师id"]),
                    str(row["学员id"]),
                )
                candidate_rows.setdefault(pair, []).append(
                    (
                        datetime.combine(lesson_date, lesson_time),
                        str(row["课程id"]),
                    )
                )
            self._counts.batch_target_queries += 1
        for dependency in stable_dependencies:
            eligible = (
                candidate
                for candidate in candidate_rows.get(
                    (
                        dependency.source_region,
                        dependency.teacher_id,
                        dependency.student_id,
                    ),
                    (),
                )
                if candidate[0] <= dependency.added
            )
            selected = max(eligible, default=None)
            relationship_courses[dependency] = (
                selected[1] if selected is not None else None
            )
        return _DirectBatchTargets(
            teacher_ids=existing_teachers,
            lesson_teachers=lesson_teachers,
            lesson_statuses=lesson_statuses,
            relationship_courses=relationship_courses,
            volatile_relationship_pairs=volatile_relationship_pairs,
            volatile_relationship_teachers=volatile_relationship_teachers,
        )

    @staticmethod
    def _chunks(values: set[str]) -> tuple[tuple[str, ...], ...]:
        ordered = sorted(values)
        return tuple(
            tuple(ordered[start : start + _TARGET_PREFETCH_CHUNK_SIZE])
            for start in range(0, len(ordered), _TARGET_PREFETCH_CHUNK_SIZE)
        )

    @staticmethod
    def _pair_chunks(
        values: set[tuple[str, str]],
    ) -> tuple[tuple[tuple[str, str], ...], ...]:
        ordered = sorted(values)
        return tuple(
            tuple(ordered[start : start + _TARGET_PREFETCH_CHUNK_SIZE])
            for start in range(0, len(ordered), _TARGET_PREFETCH_CHUNK_SIZE)
        )

    @staticmethod
    def _identity_chunks(
        values: set[tuple[str, str]],
    ) -> tuple[tuple[tuple[str, str], ...], ...]:
        ordered = sorted(values)
        return tuple(
            tuple(ordered[start : start + _TARGET_PREFETCH_CHUNK_SIZE])
            for start in range(0, len(ordered), _TARGET_PREFETCH_CHUNK_SIZE)
        )

    @staticmethod
    def _triple_chunks(
        values: set[tuple[str, str, str]],
    ) -> tuple[tuple[tuple[str, str, str], ...], ...]:
        ordered = sorted(values)
        return tuple(
            tuple(ordered[start : start + _TARGET_PREFETCH_CHUNK_SIZE])
            for start in range(0, len(ordered), _TARGET_PREFETCH_CHUNK_SIZE)
        )

    @staticmethod
    def _event_rows(event: DtsChangeEvent) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            row
            for row in (event.before, event.after)
            if row is not None
        )

    @classmethod
    def _teacher_dependencies(
        cls,
        event: DtsChangeEvent,
        suffix: str,
    ) -> set[str]:
        rows = cls._event_rows(event)
        field: str | None = None
        if suffix == "teacher":
            field = "id"
        elif suffix == "appoint":
            field = "t_id"
        elif suffix in {"teacher_certification", "teacher_class_schedule"}:
            field = "teacher_id"
        elif suffix == "teacher_favorite":
            field = "tea_id"
        elif suffix == "teacher_blacklist":
            field = "teacher_id"
        if field is None:
            return set()
        return {
            value
            for row in rows
            if (value := _string(row.get(field))) is not None
        }

    @classmethod
    def _course_dependencies(
        cls,
        event: DtsChangeEvent,
        suffix: str,
    ) -> set[str]:
        if suffix == "appoint":
            field = "id"
        elif suffix in _COURSE_TARGET_SUFFIXES:
            field = "appoint_id"
        else:
            field = None
        values = {
            value
            for row in cls._event_rows(event)
            if field is not None
            if (value := _string(row.get(field))) is not None
        }
        return values

    @staticmethod
    def _blacklist_active(row: Mapping[str, Any]) -> bool:
        valid_end = _datetime(row.get("valid_end_time"))
        return _truthy(row.get("is_valid_forever")) or bool(
            valid_end is not None and valid_end.year >= 2999
        )

    @staticmethod
    def _relationship_dependency(
        row: Mapping[str, Any],
        *,
        source_region: str,
        blacklist: bool,
    ) -> _RelationshipDependency | None:
        teacher_id = _string(row.get("teacher_id" if blacklist else "tea_id"))
        student_id = student_subject(row)
        added = _datetime(row.get("add_time") or row.get("valid_start_time"))
        if teacher_id is None or student_id is None or added is None:
            return None
        return _RelationshipDependency(
            source_region=source_region,
            teacher_id=teacher_id,
            student_id=student_id,
            added=added,
        )

    @classmethod
    def _relationship_dependencies(
        cls,
        event: DtsChangeEvent,
        suffix: str,
    ) -> set[_RelationshipDependency]:
        if suffix not in {"teacher_favorite", "teacher_blacklist"}:
            return set()
        blacklist = suffix == "teacher_blacklist"
        dependencies: set[_RelationshipDependency] = set()
        if event.before is not None:
            dependency = cls._relationship_dependency(
                event.before,
                source_region=event.source_region,
                blacklist=blacklist,
            )
            if dependency is not None:
                dependencies.add(dependency)
        after = event.after if event.operation != "DELETE" else None
        if after is not None and (
            not blacklist or cls._blacklist_active(after)
        ):
            dependency = cls._relationship_dependency(
                after,
                source_region=event.source_region,
                blacklist=blacklist,
            )
            if dependency is not None:
                dependencies.add(dependency)
        return dependencies

    @staticmethod
    def _appoint_relationship_pair(
        row: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        teacher_id = _string(row.get("t_id"))
        student_id = student_subject(row)
        if teacher_id is None or student_id is None:
            return None
        return teacher_id, student_id

    @staticmethod
    def _relationship_dependency_is_stable(
        dependency: _RelationshipDependency,
        *,
        volatile_relationship_pairs: set[tuple[str, str, str]],
        volatile_relationship_teachers: set[str],
    ) -> bool:
        return bool(
            dependency.teacher_id not in volatile_relationship_teachers
            and (
                dependency.source_region,
                dependency.teacher_id,
                dependency.student_id,
            )
            not in volatile_relationship_pairs
        )

    def _missing_batch_target(
        self,
        event: DtsChangeEvent,
        *,
        suffix: str,
        targets: _DirectBatchTargets,
    ) -> bool:
        teacher_ids = self._teacher_dependencies(event, suffix)
        course_ids = self._course_dependencies(event, suffix)
        course_identities = {
            (event.source_region, course_id) for course_id in course_ids
        }
        if suffix == "teacher":
            target_missing = bool(
                teacher_ids
                and teacher_ids.isdisjoint(targets.teacher_ids)
            )
            if not target_missing:
                return False
            # Every non-delete teacher row is an admissible source fact.
            # Cohort settings fence subscription bootstrap only; they must not
            # make an older or undated teacher look like a permanent no-op.
            return event.operation == "DELETE"
        if suffix == "appoint":
            course_missing = bool(
                course_identities
                and course_identities.isdisjoint(targets.lesson_teachers)
            )
            row = event.after if event.operation != "DELETE" else event.before
            in_scope = self._appoint_in_scope(event, row)
            if course_missing and (event.operation == "DELETE" or not in_scope):
                return True
            return bool(
                in_scope
                and teacher_ids
                and teacher_ids.isdisjoint(targets.teacher_ids)
            )
        if suffix in _COURSE_TARGET_SUFFIXES:
            return bool(
                course_identities
                and course_identities.isdisjoint(targets.lesson_teachers)
            )
        if suffix in _TEACHER_TARGET_SUFFIXES:
            if (
                teacher_ids
                and teacher_ids.isdisjoint(targets.teacher_ids)
            ):
                return True
            if suffix not in {"teacher_favorite", "teacher_blacklist"}:
                return False
            dependencies = self._relationship_dependencies(event, suffix)
            if not dependencies:
                return False
            for dependency in dependencies:
                if not self._relationship_dependency_is_stable(
                    dependency,
                    volatile_relationship_pairs=(
                        targets.volatile_relationship_pairs
                    ),
                    volatile_relationship_teachers=(
                        targets.volatile_relationship_teachers
                    ),
                ):
                    return False
                if dependency not in targets.relationship_courses:
                    return False
                if targets.relationship_courses[dependency] is not None:
                    return False
            return True
        return False

    def _refresh_batch_targets(
        self,
        event: DtsChangeEvent,
        *,
        suffix: str,
        targets: _DirectBatchTargets,
        teacher_upserted: bool,
        teacher_deleted: bool,
        lesson_upserted: bool,
        lesson_deleted: bool,
    ) -> None:
        if suffix == "teacher":
            row = event.after if event.operation != "DELETE" else event.before
            teacher_id = _string((row or {}).get("id"))
            if teacher_id is None:
                return
            if teacher_deleted:
                targets.teacher_ids.discard(teacher_id)
                removed_course_ids = {
                    course_id
                    for course_id, owner in targets.lesson_teachers.items()
                    if owner == teacher_id
                }
                targets.lesson_teachers = {
                    course_id: owner
                    for course_id, owner in targets.lesson_teachers.items()
                    if owner != teacher_id
                }
                for course_id in removed_course_ids:
                    targets.lesson_statuses.pop(course_id, None)
            elif teacher_upserted:
                targets.teacher_ids.add(teacher_id)
            return
        if suffix != "appoint":
            return
        row = event.after if event.operation != "DELETE" else event.before
        course_id = _string((row or {}).get("id"))
        if course_id is None:
            return
        course_identity = (event.source_region, course_id)
        if lesson_deleted:
            targets.lesson_teachers.pop(course_identity, None)
            targets.lesson_statuses.pop(course_identity, None)
        if lesson_upserted:
            previous_status = targets.lesson_statuses.get(course_identity)
            if previous_status != _COMPLETED_STATUS:
                targets.lesson_teachers[course_identity] = _string(
                    (row or {}).get("t_id")
                )
            targets.lesson_statuses[course_identity] = (
                _COMPLETED_STATUS
                if previous_status == _COMPLETED_STATUS
                else _string((row or {}).get("status"))
            )

    def _apply_teacher(self, connection: Any, event: DtsChangeEvent) -> None:
        row = event.after if event.operation != "DELETE" else event.before
        teacher_id = _string((row or {}).get("id"))
        if teacher_id is None:
            raise DtsWideProjectionError("DTS_DIRECT_TEACHER_ID_REQUIRED")
        target = TeacherSourceWideRecord.__table__
        existing = connection.execute(
            select(target)
            .where(target.c.tchr_id == teacher_id)
            .with_for_update()
        ).mappings().one_or_none()
        if event.operation == "DELETE" and existing is None:
            self._counts.ignored += 1
            return
        onboard = _date((row or {}).get("status_on_time"))
        if event.operation == "DELETE":
            lesson = LessonSourceWideRecord.__table__
            deleted_lessons = connection.execute(
                delete(lesson).where(
                    lesson.c.source_region == event.source_region,
                    lesson.c["老师id"] == teacher_id,
                )
            ).rowcount or 0
            deleted_teacher = connection.execute(
                delete(target).where(target.c.tchr_id == teacher_id)
            ).rowcount or 0
            self._counts.lesson_deletes += int(deleted_lessons)
            self._counts.teacher_deletes += int(deleted_teacher)
            return
        assert row is not None
        status_off = _date(row.get("status_off_time"))
        snapshot_date = datetime.now(_UTC_PLUS_8).date()
        job_days = (
            ((status_off or snapshot_date) - onboard).days
            if onboard is not None
            else None
        )
        values = self._empty_teacher_values(teacher_id)
        values.update(
            {
                "real_name": _string(row.get("real_name")),
                "center_type_id": _string(row.get("center_type")),
                "center_type_desc": _center_type_description(
                    row.get("center_type")
                ),
                "bu": _teacher_bu(row.get("is_full_time")),
                "status": _string(row.get("status")),
                "status_on_date": onboard,
                "status_off_date": status_off,
                "last_on_date": _date(row.get("last_on_time")),
                "job_days": job_days,
                "job_month": (
                    math.floor(job_days / 30) + 1
                    if job_days is not None and job_days >= 0
                    else None
                ),
                "teach_area_type": _teacher_area(row.get("course")),
                "onboard_date": onboard,
                "onboard_30d_end_date": (
                    onboard + timedelta(days=29)
                    if onboard is not None
                    else None
                ),
            }
        )
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
            select(target)
            .where(
                target.c.source_region == event.source_region,
                target.c["课程id"] == course_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if event.operation == "DELETE" and existing is None:
            self._counts.ignored += 1
            return
        in_scope = self._appoint_in_scope(event, row)
        if not in_scope:
            if existing is None or not self._write_lesson_state(
                connection,
                event.source_region,
                course_id,
                values=None,
                existing=existing,
            ):
                self._counts.ignored += 1
            return

        assert row is not None
        source_teacher_id = _string(row.get("t_id"))
        teacher_id = source_teacher_id
        if (
            existing is not None
            and existing.get("课程状态") == _COMPLETED_STATUS
        ):
            # The first teacher observed when the course enters ``end`` owns
            # the completion permanently.  A later appoint.t_id change is a
            # correction conflict, not a score transfer.  The legacy table
            # cannot represent that conflict, so keep the frozen owner here;
            # the v2 participation model records the pending correction.
            teacher_id = _string(existing["老师id"]) or source_teacher_id
        lesson_date = _date(row.get("date")) or _date(row.get("start_time"))
        lesson_time = _time(row.get("time")) or _time(row.get("start_time"))
        if teacher_id is None or lesson_date is None:
            raise DtsWideProjectionError("DTS_DIRECT_APPOINT_FULL_IMAGE_REQUIRED")
        teacher = TeacherSourceWideRecord.__table__
        teacher_row = connection.execute(
            select(teacher).where(teacher.c.tchr_id == teacher_id)
        ).mappings().one_or_none()
        if teacher_row is None:
            self._counts.ignored += 1
            return
        expected_area = event.source_region
        stored_area = _string(teacher_row["teach_area_type"])
        if event.source_region == "dom" and stored_area == "dmo":
            # Revision 71 must run before this code.  Failing the transaction
            # preserves the old lesson; treating the legacy value as a region
            # mismatch would silently delete it.
            raise DtsWideProjectionError(
                "DTS_DIRECT_DOM_AREA_MIGRATION_REQUIRED"
            )
        if stored_area != expected_area:
            if existing is None or not self._write_lesson_state(
                connection,
                event.source_region,
                course_id,
                values=None,
                existing=existing,
            ):
                self._counts.ignored += 1
            return

        lesson_status = (
            _COMPLETED_STATUS
            if existing is not None
            and existing.get("课程状态") == _COMPLETED_STATUS
            else _string(row.get("status"))
        )
        values = {
            "source_region": event.source_region,
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
            "课程状态": lesson_status,
            "缺席原因明细": None,
            "迟到": None,
            "早退": None,
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
            "cpu占用过高": None,
            "网络延迟过高": None,
        }
        if existing is not None:
            teacher_changed = _string(existing["老师id"]) != teacher_id
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
            ):
                if teacher_changed and name in {
                    "缺席原因明细",
                    "迟到",
                    "早退",
                    "是否拉黑",
                    "收藏",
                }:
                    # The v1 table has no participation/relationship identity.
                    # Keeping the old teacher's scoped facts would assign them
                    # to the substitute teacher, so fail closed until v2 can
                    # rebuild those facts against the new typed identity.
                    continue
                values[name] = existing[name]
        if not self._write_lesson_state(
            connection,
            event.source_region,
            course_id,
            values=values,
            existing=existing,
        ):
            self._counts.ignored += 1

    @staticmethod
    def _appoint_in_scope(
        event: DtsChangeEvent,
        row: Mapping[str, Any] | None,
    ) -> bool:
        return bool(
            event.operation != "DELETE"
            and row is not None
        )

    def _apply_teacher_class_schedule(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        row = slot_activation(event)
        if row is None:
            self._counts.ignored += 1
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
            self._counts.ignored += 1
            return
        if (
            teacher["onboard_date"] is None
            or teacher["onboard_30d_end_date"] is None
            or not (
                teacher["onboard_date"]
                <= schedule_date
                <= teacher["onboard_30d_end_date"]
            )
        ):
            self._counts.ignored += 1
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
        changes: dict[str, bool | None] = {}
        before = event.before
        if (
            before is not None
            and _string(before.get("certification_code")) == "16"
        ):
            before_teacher = _string(before.get("teacher_id"))
            if before_teacher is not None:
                # Direct mode has no complete certificate current set.  One
                # code-16 row disappearing cannot prove the teacher has no
                # other active TESOL certificate.
                changes[before_teacher] = None
        after = event.after if event.operation != "DELETE" else None
        if after is not None:
            after_teacher = _string(after.get("teacher_id"))
            if (
                after_teacher is not None
                and _string(after.get("certification_code")) == "16"
            ):
                changes[after_teacher] = (
                    True
                    if _int(after.get("certification_status")) == 1
                    else None
                )
        if not changes:
            self._counts.ignored += 1
            return
        target = TeacherSourceWideRecord.__table__
        changed_any = False
        for teacher_id, completed in sorted(changes.items()):
            targets = self._active_batch_targets
            if targets is not None and teacher_id not in targets.teacher_ids:
                continue
            changed = connection.execute(
                update(target)
                .where(target.c.tchr_id == teacher_id)
                .values(is_cpl_tesol=completed)
            ).rowcount or 0
            if not changed:
                continue
            changed_any = True
            self._counts.teacher_upserts += 1
        if not changed_any:
            self._counts.ignored += 1

    def _apply_teacher_absent_reason(self, connection: Any, event: DtsChangeEvent) -> None:
        self._apply_course_change(
            connection,
            event,
            cleared={"缺席原因明细": None},
            build_after=lambda row: {
                "缺席原因明细": _string(row.get("reason_type"))
            },
            required_teacher_field="t_id",
        )

    def _apply_teacher_penalty(self, connection: Any, event: DtsChangeEvent) -> None:
        def values(row: Mapping[str, Any]) -> Mapping[str, Any]:
            appeal_status = _int(row.get("appeal_status"))
            if appeal_status is None:
                return {"迟到": None, "早退": None}
            if appeal_status == 2:
                return {"迟到": False, "早退": False}
            start = _datetime(row.get("lesson_start_time"))
            in_time = _datetime(row.get("in_time"))
            out_time = _datetime(row.get("out_time"))
            end = start + timedelta(minutes=30) if start is not None else None
            return {
                "迟到": (
                    None
                    if start is None or in_time is None
                    else (in_time - start).total_seconds() > 30
                ),
                "早退": (
                    None
                    if (
                        end is None
                        or out_time is None
                        or out_time <= datetime(1970, 1, 1, 8)
                    )
                    else (end - out_time).total_seconds() > 30
                ),
            }

        self._apply_course_change(
            connection,
            event,
            cleared={"迟到": None, "早退": None},
            build_after=values,
            required_teacher_field="t_id",
        )

    def _apply_user_teacher_grading(self, connection: Any, event: DtsChangeEvent) -> None:
        def values(row: Mapping[str, Any]) -> Mapping[str, Any]:
            if event.source_region == "dom":
                if _int(row.get("is_del")) not in {None, 0}:
                    return {
                        "差评分": None,
                        "差评标签": None,
                        "好评标签": None,
                    }
                use_point = str(row.get("use_point") or "").strip().lower()
                score = _float(row.get("score"))
                grading_type = str(row.get("type") or "").strip().lower()
                if use_point == "buy":
                    negative = score in {1.0, 2.0}
                    return {
                        "差评分": score if negative else None,
                        "差评标签": negative,
                        "好评标签": score in {4.0, 5.0},
                    }
                if use_point == "free":
                    return {
                        "差评分": None,
                        "差评标签": grading_type == "unsatisfactory",
                        "好评标签": grading_type == "satisfactory",
                    }
                return {
                    "差评分": None,
                    "差评标签": None,
                    "好评标签": None,
                }

            # OVS evaluation semantics are not source-profile confirmed.  The
            # event remains in the DTS ledger/current source state, but the
            # compatibility lesson projection must stay unknown and cannot
            # create score or personalized-task evidence.
            return {"差评分": None, "差评标签": None, "好评标签": None}

        self._apply_course_change(
            connection,
            event,
            cleared={"差评分": None, "差评标签": None, "好评标签": None},
            build_after=values,
        )

    def _apply_grading_label_log(self, connection: Any, event: DtsChangeEvent) -> None:
        if event.source_region != "dom":
            # OVS label semantics are not confirmed.  Its source event may be
            # retained by a source-current pipeline, but it must not become
            # DOM-style task or score evidence in the compatibility row.
            self._counts.ignored += 1
            return
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
        changed_any = False
        for course_id in sorted(course_ids):
            targets = self._active_batch_targets
            if (
                targets is not None
                and (event.source_region, course_id)
                not in targets.lesson_teachers
            ):
                continue
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
                after_name = _string(after.get("label_name"))
                active = event.source_region == "dom" or (
                    _int(after.get("type")) == 1
                    and str(after.get("status") or "").strip().lower()
                    == "normal"
                )
                if after_name is not None and active:
                    add_names.add(after_name)
            changed_any = self._update_lesson_label_set(
                connection,
                event.source_region,
                course_id,
                remove_names=remove_names,
                add_names=add_names,
            ) or changed_any
        if not changed_any:
            self._counts.ignored += 1

    def _apply_grading_label(self, connection: Any, event: DtsChangeEvent) -> None:
        del connection, event
        # Course label identity and display name both come from the surviving
        # grading_label_log current set.  Dictionary renames/deletes must not
        # rewrite historical log names or task titles.
        self._counts.ignored += 1

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
        # Compatibility-only v1 projection.  The lesson boolean is neither
        # the authoritative current relationship nor an end+24h favorite
        # attribution and must not be consumed as score evidence.
        before = event.before
        after = event.after if event.operation != "DELETE" else None
        changed_any = False
        if before is not None:
            selected = self._relationship_course(
                connection,
                before,
                source_region=event.source_region,
                blacklist=blacklist,
            )
            if selected is not None:
                changed_any = self._update_lesson(
                    connection,
                    event.source_region,
                    selected,
                    {column: False},
                ) or changed_any
        if after is not None:
            active = not blacklist or self._blacklist_active(after)
            if active:
                selected = self._relationship_course(
                    connection,
                    after,
                    source_region=event.source_region,
                    blacklist=blacklist,
                )
                if selected is not None:
                    changed_any = self._update_lesson(
                        connection,
                        event.source_region,
                        selected,
                        {column: True},
                    ) or changed_any
        if not changed_any:
            self._counts.ignored += 1

    def _relationship_course(
        self,
        connection: Any,
        row: Mapping[str, Any],
        *,
        source_region: str,
        blacklist: bool,
    ) -> str | None:
        dependency = self._relationship_dependency(
            row,
            source_region=source_region,
            blacklist=blacklist,
        )
        if dependency is None:
            return None
        targets = self._active_batch_targets
        if targets is not None and self._relationship_dependency_is_stable(
            dependency,
            volatile_relationship_pairs=targets.volatile_relationship_pairs,
            volatile_relationship_teachers=targets.volatile_relationship_teachers,
        ):
            if dependency in targets.relationship_courses:
                selected = targets.relationship_courses[dependency]
                if selected is None:
                    self._counts.relationship_cache_misses += 1
                else:
                    self._counts.relationship_cache_hits += 1
                return selected
        self._counts.relationship_fallback_queries += 1
        target = LessonSourceWideRecord.__table__
        lesson_at = target.c["上课日期"] + target.c["上课时间"]
        # Legacy fallback retained for v1 readers only.  It intentionally is
        # not promoted to the v2 observation/attribution contract.
        return connection.execute(
            select(target.c["课程id"])
            .where(
                target.c.source_region == dependency.source_region,
                target.c["老师id"] == dependency.teacher_id,
                target.c["学员id"] == dependency.student_id,
                target.c["课程状态"] == _COMPLETED_STATUS,
                lesson_at <= dependency.added,
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
        def values(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
            raw_grandson = row.get("complaint_type_grandson")
            grandson = _int(raw_grandson)
            valid = bool(
                _int(row.get("complaint_type")) == 13
                and (
                    raw_grandson is None
                    or (
                        not isinstance(raw_grandson, bool)
                        and grandson is not None
                    )
                )
                and grandson != 82
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
                if names is None:
                    return None
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
            course_identities = tuple(
                (str(row["source_region"]), str(row["课程id"]))
                for row in connection.execute(
                    select(target.c.source_region, target.c["课程id"]).where(
                        target.c.source_region == event.source_region,
                        target.c[name] == before_name,
                    )
                ).mappings()
            )
            for source_region, course_id in course_identities:
                self._update_lesson(
                    connection,
                    source_region,
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
    ) -> tuple[str | None, str | None, str | None] | None:
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
            return None
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

    def _apply_course_change(
        self,
        connection: Any,
        event: DtsChangeEvent,
        *,
        cleared: Mapping[str, Any],
        build_after: Callable[
            [Mapping[str, Any]],
            Mapping[str, Any] | None,
        ],
        required_teacher_field: str | None = None,
    ) -> None:
        before_course = _string((event.before or {}).get("appoint_id"))
        after = event.after if event.operation != "DELETE" else None
        after_course = _string((after or {}).get("appoint_id"))
        course_ids = {
            course_id
            for course_id in (before_course, after_course)
            if course_id is not None
        }
        if not course_ids:
            raise DtsWideProjectionError("DTS_DIRECT_COURSE_DEPENDENCY_REQUIRED")
        target = LessonSourceWideRecord.__table__
        targets = self._active_batch_targets
        if targets is None:
            existing_rows = connection.execute(
                select(
                    target.c["课程id"],
                    target.c["老师id"],
                ).where(
                    target.c.source_region == event.source_region,
                    target.c["课程id"].in_(sorted(course_ids)),
                )
            ).mappings()
            lesson_owners = {
                str(row["课程id"]): _string(row["老师id"])
                for row in existing_rows
            }
            existing_ids = set(lesson_owners)
        else:
            existing_ids = {
                course_id
                for course_id in course_ids
                if (event.source_region, course_id) in targets.lesson_teachers
            }
            lesson_owners = {
                course_id: targets.lesson_teachers.get(
                    (event.source_region, course_id)
                )
                for course_id in existing_ids
            }

        def row_matches_current_owner(
            row: Mapping[str, Any] | None,
            course_id: str | None,
        ) -> bool:
            if required_teacher_field is None:
                return True
            if row is None or course_id is None:
                return False
            source_teacher_id = _string(row.get(required_teacher_field))
            return bool(
                source_teacher_id is not None
                and source_teacher_id == lesson_owners.get(course_id)
            )
        if not existing_ids:
            self._counts.ignored += 1
            return
        changes: dict[str, dict[str, Any]] = {}
        if (
            before_course in existing_ids
            and row_matches_current_owner(event.before, before_course)
        ):
            changes[before_course] = dict(cleared)
        if (
            after_course in existing_ids
            and after is not None
            and row_matches_current_owner(after, after_course)
        ):
            after_values = build_after(after)
            if after_values is None:
                self._counts.ignored += 1
                return
            changes[after_course] = dict(after_values)
        changed_any = False
        for course_id, values in sorted(changes.items()):
            changed_any = self._update_lesson(
                connection,
                event.source_region,
                course_id,
                values,
            ) or changed_any
        if not changed_any:
            self._counts.ignored += 1

    def _update_lesson_label_set(
        self,
        connection: Any,
        source_region: str,
        course_id: str,
        *,
        remove_names: set[str],
        add_names: set[str],
    ) -> bool:
        target = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(
                target.c["老师id"],
                target.c["评价详情"],
            )
            .where(
                target.c.source_region == source_region,
                target.c["课程id"] == course_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if existing is None:
            return False
        names = {
            value.strip()
            for value in str(existing["评价详情"] or "").split(",")
            if value.strip()
        }
        names.difference_update(remove_names)
        names.update(add_names)
        connection.execute(
            update(target)
            .where(
                target.c.source_region == source_region,
                target.c["课程id"] == course_id,
            )
            .values(**{"评价详情": ",".join(sorted(names)) or None})
        )
        self._counts.lesson_upserts += 1
        return True

    def _update_lesson(
        self,
        connection: Any,
        source_region: str,
        course_id: str,
        values: Mapping[str, Any],
    ) -> bool:
        target = LessonSourceWideRecord.__table__
        existing = connection.execute(
            select(target)
            .where(
                target.c.source_region == source_region,
                target.c["课程id"] == course_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if existing is None:
            return False
        updated = dict(existing)
        updated.update(values)
        return self._write_lesson_state(
            connection,
            source_region,
            course_id,
            values=updated,
            existing=existing,
        )

    def _write_lesson_state(
        self,
        connection: Any,
        source_region: str,
        course_id: str,
        *,
        values: Mapping[str, Any] | None,
        existing: Mapping[str, Any] | None,
    ) -> bool:
        old_row = dict(existing) if existing is not None else None
        new_row = dict(values) if values is not None else None
        if old_row is None and new_row is None:
            return False
        if new_row is not None:
            new_row["source_region"] = source_region
            new_row["课程id"] = course_id

        affected_teachers = {
            teacher_id
            for teacher_id in (
                _string((old_row or {}).get("老师id")),
                _string((new_row or {}).get("老师id")),
            )
            if teacher_id is not None
        }
        if not self._lock_teacher_rows(connection, affected_teachers):
            return False
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
                    delete(target).where(
                        target.c.source_region == source_region,
                        target.c["课程id"] == course_id,
                    )
                )
                self._counts.lesson_deletes += 1
        elif old_row is None:
            connection.execute(insert(target).values(new_row))
            self._counts.lesson_upserts += 1
        else:
            connection.execute(
                update(target)
                .where(
                    target.c.source_region == source_region,
                    target.c["课程id"] == course_id,
                )
                .values(
                    {
                        name: value
                        for name, value in new_row.items()
                        if name not in {"source_region", "课程id"}
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
        return True

    @staticmethod
    def _lock_teacher_rows(
        connection: Any,
        teacher_ids: set[str],
    ) -> bool:
        if not teacher_ids:
            return True
        teacher = TeacherSourceWideRecord.__table__
        locked = set(
            connection.execute(
                select(teacher.c.tchr_id)
                .where(teacher.c.tchr_id.in_(sorted(teacher_ids)))
                .order_by(teacher.c.tchr_id)
                .with_for_update()
            ).scalars()
        )
        return locked == teacher_ids

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
        # Child facts are retained as soon as they arrive, but facts without
        # an explicit participant must not affect a teacher aggregate before
        # the first ``end`` freezes the completion owner.  Otherwise an
        # evaluation/complaint received while the lesson is ``on`` can grant
        # an irreversible qualification to a teacher who is substituted
        # before completion.
        evaluated = completed and (
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
                and row.get("迟到") is False
                and row.get("早退") is False
            ),
            "no_notice_cnt": int(
                absent and row.get("缺席原因明细") == _NO_NOTICE_DETAIL
            ),
            "feedback_total_eval_cnt": int(evaluated),
            "feedback_praise_cnt": int(
                completed and row.get("好评标签") is True
            ),
            "feedback_negative_cnt": int(
                completed and row.get("差评标签") is True
            ),
            "feedback_complaint_cnt": int(completed and complaint),
            "feedback_valid_complaint_cnt": int(completed and complaint),
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
        onboard = current["onboard_date"]
        onboard_end = current["onboard_30d_end_date"]
        completed_in_observation_window = 0
        if onboard is not None and onboard_end is not None:
            completed_in_observation_window = int(
                connection.execute(
                    select(func.count())
                    .select_from(lesson)
                    .where(
                        lesson.c["老师id"] == teacher_id,
                        lesson.c["课程状态"] == _COMPLETED_STATUS,
                        lesson.c["上课日期"] >= onboard,
                        lesson.c["上课日期"] <= onboard_end,
                    )
                ).scalar_one()
                or 0
            )
        late_or_early_completed = int(
            connection.execute(
                select(func.count())
                .select_from(lesson)
                .where(
                    lesson.c["老师id"] == teacher_id,
                    lesson.c["课程状态"] == _COMPLETED_STATUS,
                    (
                        lesson.c["迟到"].is_(True)
                        | lesson.c["早退"].is_(True)
                    ),
                )
            ).scalar_one()
            or 0
        )
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
                late_or_early_completed,
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
            # Favorite/block are teacher-student relationship counts.  There is
            # no confirmed denominator with the same scope, so the legacy
            # completed-student rate must not be presented as a business fact.
            "feedback_favorite_rate": None,
            "feedback_block_rate": None,
            "feedback_eval_rate": _safe_rate(total_evaluated, total_completed),
            "capacity_avg_completed_per_day": (
                completed_in_observation_window / 30
            ),
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
