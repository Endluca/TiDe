"""Disabled-by-default appoint-only v2 course shadow projector.

This module is intentionally not imported by the active DTS consumer.  An
explicit caller must enable it and own one PostgreSQL transaction.  One call
either verifies a replay or advances exactly one contiguous appoint source-row
revision into ``source_courses`` and ``source_course_participations``.  It
never writes the v1 wide tables, tasks, scores, dirty queue, or outbox.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_course_participation import (
    AppointSnapshot,
    CourseParticipation,
    CourseParticipationState,
    SourceEventReference,
    reduce_course_participations,
)
from .dts_source_contract_v2 import (
    V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
    V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
    V2SourceRouteDecision,
)
from .dts_v2_appoint_adapter import adapt_v2_appoint_route


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_POSITION_KEYS = frozenset(
    {
        "v",
        "source_timestamp",
        "record_id_type",
        "record_id",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
        "offset_value",
    }
)
_COURSE_OWNED_COLUMNS = (
    "student_token",
    "lesson_local_date",
    "lesson_local_time",
    "scheduled_start_at",
    "raw_end_time",
    "end_time",
    "source_status",
    "current_teacher_id",
    "current_teacher_id_type",
    "current_participation_seq",
    "is_peak",
    "completion_participation_seq",
    "completion_teacher_id",
    "completion_teacher_id_type",
    "completion_frozen_at",
    "completion_end_time",
    "completion_student_token",
    "completion_is_peak",
    "completion_lesson_local_date",
    "completion_lesson_local_time",
    "completion_source_position",
    "completion_source_revision",
    "initial_completion_snapshot",
    "completion_voided_at",
    "completion_conflict_status",
    "conflict_resolved_against_position",
    "conflict_resolved_against_revision",
    "conflict_fingerprint",
    "source_is_deleted",
    "appoint_evidence_status",
    "last_applied_event_position",
    "last_applied_source_revision",
)
_COURSE_JSON_COLUMNS = frozenset(
    {
        "completion_source_position",
        "initial_completion_snapshot",
        "conflict_resolved_against_position",
        "last_applied_event_position",
    }
)
_PARTICIPATION_IDENTITY_COLUMNS = (
    "teacher_id",
    "teacher_id_type",
    "assigned_at",
    "assigned_at_evidence_status",
    "assignment_source_partition_epoch_id",
    "assignment_event_topic",
    "assignment_event_partition",
    "assignment_event_offset",
    "assignment_source_row_revision",
    "assignment_event_phase",
)
_PARTICIPATION_MUTABLE_COLUMNS = (
    "participation_status",
    "participation_role",
    "is_current",
    "ended_at",
    "source_deleted",
)


class DtsV2CourseProjectorError(RuntimeError):
    """The shadow course cannot be projected without guessing or drifting."""


@dataclass(frozen=True)
class DtsV2CourseProjectionResult:
    status: str
    source_region: str
    source_appoint_id: str
    source_row_revision: int
    participation_count: int


@dataclass(frozen=True)
class DtsV2CourseProjectionBatchResult:
    """One locked course brought to the latest durable source revision."""

    source_region: str
    source_appoint_id: str
    source_row_revision: int
    applied_revision_count: int
    participation_count: int


class DtsV2CourseProjector:
    """Project one persisted appoint revision into the inert v2 shadow."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def project_until_current(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        max_revisions: int = 10_000,
    ) -> DtsV2CourseProjectionBatchResult:
        """Replay every unprojected appoint revision in one transaction.

        A dirty key is intentionally coalesced, while appoint source versions
        are not.  The domain worker must therefore keep invoking the exact
        one-revision projector until it reports ``REPLAYED``.  The first call
        locks the source current/course identity for the surrounding
        transaction, so a concurrent ingest cannot move the target underneath
        this loop; its later revision becomes a new dirty input instead.
        """

        if self.enabled is not True:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_PROJECTOR_DISABLED"
            )
        if not connection.in_transaction():
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_TRANSACTION_REQUIRED"
            )
        if (
            isinstance(max_revisions, bool)
            or not isinstance(max_revisions, int)
            or max_revisions < 1
        ):
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_MAX_REVISIONS_INVALID"
            )

        applied_revision_count = 0
        while True:
            result = self.project_next(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
            )
            if result.status == "REPLAYED":
                return DtsV2CourseProjectionBatchResult(
                    source_region=result.source_region,
                    source_appoint_id=result.source_appoint_id,
                    source_row_revision=result.source_row_revision,
                    applied_revision_count=applied_revision_count,
                    participation_count=result.participation_count,
                )
            if result.status != "APPLIED":
                raise DtsV2CourseProjectorError(
                    "DTS_V2_COURSE_PROJECTION_STATUS_INVALID"
                )
            applied_revision_count += 1
            if applied_revision_count > max_revisions:
                # The loop may perform one final read after exactly N writes
                # to obtain the REPLAYED proof.  A (N+1)th write exceeds the
                # bound and rolls the surrounding transaction back.
                raise DtsV2CourseProjectorError(
                    "DTS_V2_COURSE_PROJECTION_LIMIT_EXCEEDED"
                )

    def project_next(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
    ) -> DtsV2CourseProjectionResult:
        if self.enabled is not True:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_PROJECTOR_DISABLED"
            )
        if not connection.in_transaction():
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_TRANSACTION_REQUIRED"
            )
        if source_region not in {"dom", "ovs"}:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_REGION_INVALID"
            )
        if not isinstance(source_appoint_id, str) or not source_appoint_id:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_APPOINT_ID_INVALID"
            )
        source_table = f"{source_region}_appoint"
        if _canonical_numeric_id(source_appoint_id) != source_appoint_id:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_APPOINT_ID_NOT_CANONICAL"
            )

        _lock_identity(
            connection,
            "source-current",
            source_region,
            source_table,
            source_appoint_id,
        )
        current_source = _read_source_current_for_update(
            connection,
            source_region=source_region,
            source_table=source_table,
            source_appoint_id=source_appoint_id,
        )
        if current_source is None:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_SOURCE_CURRENT_REQUIRED"
            )
        version_rows = _read_source_versions(
            connection,
            source_region=source_region,
            source_table=source_table,
            source_appoint_id=source_appoint_id,
        )
        if not version_rows:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_SOURCE_HISTORY_REQUIRED"
            )
        adapted_versions = _adapt_contiguous_versions(
            version_rows,
            source_region=source_region,
            source_table=source_table,
            source_appoint_id=source_appoint_id,
        )
        _require_current_matches_history(current_source, version_rows[-1])

        course = _read_course_for_update(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
        )
        participations = _read_participations_for_update(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
        )
        latest_revision = len(adapted_versions)
        if course is None:
            if participations:
                raise DtsV2CourseProjectorError(
                    "DTS_V2_COURSE_ORPHAN_PARTICIPATIONS"
                )
            applied_revision = 0
        else:
            applied_revision = course.get("last_applied_source_revision")
            if (
                isinstance(applied_revision, bool)
                or not isinstance(applied_revision, int)
                or not 1 <= applied_revision <= latest_revision
            ):
                raise DtsV2CourseProjectorError(
                    "DTS_V2_COURSE_LAST_REVISION_INVALID"
                )
            prior_reduction = _reduce_exact(
                adapted_versions[:applied_revision],
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                target_revision=applied_revision,
            )
            prior_course = _course_projection(
                prior_reduction.state,
                version_rows[:applied_revision],
            )
            _require_projection_matches_database(
                course,
                participations,
                expected_course=prior_course,
                expected_participations=prior_reduction.state.participations,
            )

        if applied_revision == latest_revision:
            assert course is not None
            return DtsV2CourseProjectionResult(
                status="REPLAYED",
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                source_row_revision=latest_revision,
                participation_count=len(participations),
            )

        target_revision = applied_revision + 1
        reduction = _reduce_exact(
            adapted_versions[:target_revision],
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            target_revision=target_revision,
        )
        expected_course = _course_projection(
            reduction.state,
            version_rows[:target_revision],
        )
        if course is None:
            _insert_course(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                values=expected_course,
            )
        _persist_participations(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            existing=participations,
            expected=reduction.state.participations,
        )
        if course is not None:
            _update_course(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                values=expected_course,
                expected_row_version=int(course["row_version"]),
            )
        return DtsV2CourseProjectionResult(
            status="APPLIED",
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            source_row_revision=target_revision,
            participation_count=len(reduction.state.participations),
        )


def _canonical_numeric_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_APPOINT_ID_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_APPOINT_ID_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_APPOINT_ID_INVALID"
        )
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _lock_identity(connection: Connection, *parts: Any) -> None:
    payload = json.dumps(
        list(parts),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    lock_id = int.from_bytes(
        hashlib.sha256(payload).digest()[:8],
        byteorder="big",
        signed=True,
    )
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def _read_source_current_for_update(
    connection: Connection,
    *,
    source_region: str,
    source_table: str,
    source_appoint_id: str,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT source_region, source_table, source_key,
                   source_key_type, source_key_numeric, source_key_text,
                   source_schema_profile_id, source_field_types, source_row,
                   is_deleted, source_row_revision, source_position_v2,
                   source_payload_hash, provenance_state
            FROM public.dts_source_rows
            WHERE source_region = :source_region
              AND source_table = :source_table
              AND source_key = :source_key
            FOR UPDATE
            """
        ),
        {
            "source_region": source_region,
            "source_table": source_table,
            "source_key": source_appoint_id,
        },
    ).mappings().one_or_none()


def _read_source_versions(
    connection: Connection,
    *,
    source_region: str,
    source_table: str,
    source_appoint_id: str,
) -> list[Mapping[str, Any]]:
    return list(
        connection.execute(
            text(
                """
                SELECT source_region, source_partition_epoch_id, topic,
                       partition_id, offset_value, version_kind, source_table,
                       source_schema_profile_id, source_field_types, source_key,
                       source_key_data, source_key_type, source_key_numeric,
                       source_key_text, operation, before_row, after_row,
                       source_timestamp, record_id_type, record_id_numeric,
                       record_id_text, source_position, source_row_revision,
                       protected_source_row_hash
                FROM public.dts_source_row_versions
                WHERE source_region = :source_region
                  AND source_table = :source_table
                  AND source_key = :source_key
                ORDER BY source_row_revision, source_partition_epoch_id,
                         topic, partition_id, offset_value
                """
            ),
            {
                "source_region": source_region,
                "source_table": source_table,
                "source_key": source_appoint_id,
            },
        ).mappings()
    )


def _read_course_for_update(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
) -> Mapping[str, Any] | None:
    columns = ", ".join((*_COURSE_OWNED_COLUMNS, "row_version"))
    return connection.execute(
        text(
            f"""
            SELECT {columns}
            FROM public.source_courses
            WHERE source_region = :source_region
              AND source_appoint_id = :source_appoint_id
            FOR UPDATE
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
        },
    ).mappings().one_or_none()


def _read_participations_for_update(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
) -> list[Mapping[str, Any]]:
    columns = ", ".join(
        (
            "participation_seq",
            *_PARTICIPATION_IDENTITY_COLUMNS,
            *_PARTICIPATION_MUTABLE_COLUMNS,
            "absence_reason_detail",
            "no_notice",
            "row_version",
        )
    )
    return list(
        connection.execute(
            text(
                f"""
                SELECT {columns}
                FROM public.source_course_participations
                WHERE source_region = :source_region
                  AND source_appoint_id = :source_appoint_id
                ORDER BY participation_seq
                FOR UPDATE
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
            },
        ).mappings()
    )


def _adapt_contiguous_versions(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_region: str,
    source_table: str,
    source_appoint_id: str,
) -> list[Any]:
    expected_profile = V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE.get(source_table)
    expected_key_type = V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE.get(source_table)
    if expected_profile is None or expected_key_type is None:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_SOURCE_PROFILE_MISSING"
        )
    adapted = []
    for expected_revision, row in enumerate(rows, start=1):
        revision = row.get("source_row_revision")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision != expected_revision
        ):
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_SOURCE_REVISION_GAP"
            )
        _require_persisted_version(
            row,
            source_region=source_region,
            source_table=source_table,
            source_appoint_id=source_appoint_id,
            expected_profile=expected_profile,
            expected_key_type=expected_key_type,
        )
        source_ref = SourceEventReference(
            source_partition_epoch_id=row["source_partition_epoch_id"],
            topic=row["topic"],
            partition=row["partition_id"],
            offset=row["offset_value"],
            source_timestamp=row["source_timestamp"],
        )
        route = V2SourceRouteDecision(
            route_status="VERSIONED",
            source_table=row["source_table"],
            operation=row["operation"],
            source_key=row["source_key"],
            source_key_type=row["source_key_type"],
            source_key_data_json=_canonical_json(row["source_key_data"]),
            source_key_numeric=row["source_key_numeric"],
            source_key_text=row["source_key_text"],
            source_schema_profile_id=row["source_schema_profile_id"],
            source_field_types=row["source_field_types"],
            before_row=row["before_row"],
            after_row=row["after_row"],
            protected_payload_hash=row["protected_source_row_hash"],
        )
        try:
            version = adapt_v2_appoint_route(
                route,
                source_region=source_region,
                source_row_revision=revision,
                source_ref=source_ref,
            )
        except (TypeError, ValueError) as exc:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_SOURCE_VERSION_INVALID"
            ) from exc
        if version.source_appoint_id != source_appoint_id:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_SOURCE_IDENTITY_MISMATCH"
            )
        adapted.append(version.source_version)
    return adapted


def _require_persisted_version(
    row: Mapping[str, Any],
    *,
    source_region: str,
    source_table: str,
    source_appoint_id: str,
    expected_profile: str,
    expected_key_type: str,
) -> None:
    position = row.get("source_position")
    timestamp = row.get("source_timestamp")
    source_field_types = row.get("source_field_types")
    source_key_data = row.get("source_key_data")
    payload_hash = row.get("protected_source_row_hash")
    if (
        row.get("source_region") != source_region
        or row.get("source_table") != source_table
        or row.get("source_key") != source_appoint_id
        or row.get("version_kind") != "CDC"
        or row.get("operation") not in {"INSERT", "UPDATE", "DELETE"}
        or row.get("source_schema_profile_id") != expected_profile
        or row.get("source_key_type") != expected_key_type
        or row.get("source_key_numeric") != Decimal(source_appoint_id)
        or row.get("source_key_text") is not None
        or not isinstance(source_key_data, Mapping)
        or set(source_key_data) != {"id"}
        or _canonical_numeric_id(source_key_data.get("id"))
        != source_appoint_id
        or not isinstance(source_field_types, Mapping)
        or source_field_types.get("id") != expected_key_type
        or not isinstance(payload_hash, str)
        or _SHA256_PATTERN.fullmatch(payload_hash) is None
        or not isinstance(timestamp, datetime)
        or timestamp.utcoffset() is None
        or not isinstance(position, Mapping)
        or set(position) != _SOURCE_POSITION_KEYS
        or row.get("record_id_type") != "numeric"
        or row.get("record_id_numeric") is None
        or row.get("record_id_text") is not None
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_SOURCE_VERSION_INVALID"
        )
    expected_payload_hash = hashlib.sha256(
        json.dumps(
            {
                "source_region": source_region,
                "source_table": source_table,
                "source_key_type": expected_key_type,
                "source_key": source_appoint_id,
                "source_schema_profile_id": expected_profile,
                "source_field_types": source_field_types,
                "operation": row.get("operation"),
                "before_row": row.get("before_row"),
                "after_row": row.get("after_row"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if payload_hash != expected_payload_hash:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_SOURCE_PAYLOAD_HASH_INVALID"
        )
    expected_timestamp = timestamp.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    if (
        position.get("v") != 1
        or position.get("source_timestamp") != expected_timestamp
        or position.get("record_id_type") != "numeric"
        or position.get("record_id") != _canonical_numeric_id(
            row["record_id_numeric"]
        )
        or position.get("source_partition_epoch_id")
        != row.get("source_partition_epoch_id")
        or position.get("topic") != row.get("topic")
        or position.get("partition_id") != row.get("partition_id")
        or position.get("offset_value") != row.get("offset_value")
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_SOURCE_POSITION_INVALID"
        )


def _require_current_matches_history(
    current: Mapping[str, Any],
    latest: Mapping[str, Any],
) -> None:
    expected_row = (
        latest.get("before_row")
        if latest.get("operation") == "DELETE"
        else latest.get("after_row")
    )
    expected_deleted = latest.get("operation") == "DELETE"
    if (
        current.get("source_region") != latest.get("source_region")
        or current.get("source_table") != latest.get("source_table")
        or current.get("source_key") != latest.get("source_key")
        or current.get("source_key_type") != latest.get("source_key_type")
        or current.get("source_key_numeric") != latest.get("source_key_numeric")
        or current.get("source_key_text") != latest.get("source_key_text")
        or current.get("source_schema_profile_id")
        != latest.get("source_schema_profile_id")
        or current.get("provenance_state") != "V2_CONFIRMED"
        or current.get("source_row_revision")
        != latest.get("source_row_revision")
        or current.get("is_deleted") is not expected_deleted
        or not _same_json(current.get("source_row"), expected_row)
        or not _same_json(
            current.get("source_field_types"), latest.get("source_field_types")
        )
        or not _same_json(
            current.get("source_position_v2"), latest.get("source_position")
        )
        or current.get("source_payload_hash")
        != latest.get("protected_source_row_hash")
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_SOURCE_CURRENT_MISMATCH"
        )


def _reduce_exact(
    versions: Sequence[Any],
    *,
    source_region: str,
    source_appoint_id: str,
    target_revision: int,
) -> Any:
    try:
        reduction = reduce_course_participations(
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            versions=versions,
            scope_complete=True,
        )
    except (AssertionError, TypeError, ValueError) as exc:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_REDUCTION_FAILED"
        ) from exc
    state = reduction.state
    if (
        state.last_applied_source_revision != target_revision
        or state.blocked_source_revision is not None
        or len(reduction.versions) != target_revision
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_REDUCTION_NOT_CONTIGUOUS"
        )
    return reduction


def _course_projection(
    state: CourseParticipationState,
    version_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    last_revision = state.last_applied_source_revision
    if last_revision is None or last_revision != len(version_rows):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_REDUCTION_REVISION_INVALID"
        )
    by_revision = {
        int(row["source_row_revision"]): row for row in version_rows
    }
    current = state.current_snapshot
    completion = state.completion_snapshot
    initial_completion = state.initial_completion_snapshot
    completion_projection = completion
    if (
        completion_projection is None
        and initial_completion is not None
        and initial_completion.teacher_id is None
        and state.completion_conflict_status.value == "PENDING"
    ):
        completion_projection = initial_completion

    completion_source = (
        None
        if state.completion_source_revision is None
        else by_revision.get(state.completion_source_revision)
    )
    initial_completion_source = (
        None
        if state.initial_completion_source_revision is None
        else by_revision.get(state.initial_completion_source_revision)
    )
    if (
        state.completion_participation_seq is not None
        and (completion_source is None or initial_completion_source is None)
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_COMPLETION_SOURCE_MISSING"
        )
    last_source = by_revision[last_revision]
    values: dict[str, Any] = {
        "student_token": None if current is None else current.student_token,
        "lesson_local_date": (
            None
            if current is None
            else _parse_date(current.lesson_local_date, "CURRENT_DATE")
        ),
        "lesson_local_time": (
            None
            if current is None
            else _parse_time(current.lesson_local_time, "CURRENT_TIME")
        ),
        # Appoint carries a local date/time but no verified timezone profile.
        "scheduled_start_at": None,
        "raw_end_time": None if current is None else current.end_time,
        "end_time": (
            None
            if current is None
            else _parse_datetime(current.end_time)
        ),
        "source_status": None if current is None else current.status,
        "current_teacher_id": state.current_teacher_id,
        "current_teacher_id_type": state.current_teacher_id_type,
        "current_participation_seq": state.current_participation_seq,
        "is_peak": None if current is None else current.is_peak,
        "completion_participation_seq": state.completion_participation_seq,
        "completion_teacher_id": state.completion_teacher_id,
        "completion_teacher_id_type": state.completion_teacher_id_type,
        "completion_frozen_at": (
            None
            if state.completion_participation_seq is None
            else initial_completion_source["source_timestamp"]
        ),
        "completion_end_time": (
            None
            if completion_projection is None
            else _parse_datetime(completion_projection.end_time)
        ),
        "completion_student_token": (
            None
            if completion_projection is None
            else completion_projection.student_token
        ),
        "completion_is_peak": (
            None
            if completion_projection is None
            else completion_projection.is_peak
        ),
        "completion_lesson_local_date": (
            None
            if completion_projection is None
            else _parse_date(
                completion_projection.lesson_local_date,
                "COMPLETION_DATE",
            )
        ),
        "completion_lesson_local_time": (
            None
            if completion_projection is None
            else _parse_time(
                completion_projection.lesson_local_time,
                "COMPLETION_TIME",
            )
        ),
        "completion_source_position": (
            None
            if completion_source is None
            else completion_source["source_position"]
        ),
        "completion_source_revision": state.completion_source_revision,
        "initial_completion_snapshot": _snapshot_json(initial_completion),
        "completion_voided_at": None,
        "completion_conflict_status": state.completion_conflict_status.value,
        "conflict_resolved_against_position": None,
        "conflict_resolved_against_revision": None,
        "conflict_fingerprint": _conflict_fingerprint(state),
        "source_is_deleted": state.source_is_deleted,
        "appoint_evidence_status": state.evidence_status.value,
        "last_applied_event_position": last_source["source_position"],
        "last_applied_source_revision": last_revision,
    }
    return values


def _snapshot_json(snapshot: AppointSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "teacher_id": snapshot.teacher_id,
        "teacher_id_type": snapshot.teacher_id_type,
        "status": snapshot.status,
        "use_point": snapshot.use_point,
        "end_time": snapshot.end_time,
        "student_token": snapshot.student_token,
        "lesson_local_date": snapshot.lesson_local_date,
        "lesson_local_time": snapshot.lesson_local_time,
        "is_peak": snapshot.is_peak,
    }


def _conflict_fingerprint(state: CourseParticipationState) -> str | None:
    if state.completion_conflict_status.value != "PENDING":
        return None
    payload = {
        "v": 1,
        "source_region": state.source_region,
        "source_appoint_id": state.source_appoint_id,
        "last_applied_source_revision": state.last_applied_source_revision,
        "current_participation_seq": state.current_participation_seq,
        "completion_participation_seq": state.completion_participation_seq,
        "current_snapshot": _snapshot_json(state.current_snapshot),
        "completion_snapshot": _snapshot_json(state.completion_snapshot),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_date(value: str | None, field: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DtsV2CourseProjectorError(
            f"DTS_V2_COURSE_{field}_INVALID"
        ) from exc


def _parse_time(value: str | None, field: str) -> time | None:
    if value is None:
        return None
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DtsV2CourseProjectorError(
            f"DTS_V2_COURSE_{field}_INVALID"
        ) from exc
    if parsed.tzinfo is not None:
        raise DtsV2CourseProjectorError(
            f"DTS_V2_COURSE_{field}_INVALID"
        )
    return parsed


def _parse_datetime(value: str | None) -> datetime | None:
    """Return only a source-proved instant; preserve all other text as raw.

    Appoint has no verified source timezone profile.  A naive or malformed
    value must therefore not inherit the PostgreSQL/Python session timezone.
    Its raw string remains in ``raw_end_time`` and the completion snapshot so
    timestamp-dependent consumers can wait for authoritative evidence.
    """

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


def _participation_projection(row: CourseParticipation) -> dict[str, Any]:
    return {
        "teacher_id": row.teacher_id,
        "teacher_id_type": row.teacher_id_type,
        "participation_status": row.participation_status,
        "participation_role": row.participation_role.value,
        "is_current": row.is_current,
        "assigned_at": row.assigned_at,
        "assigned_at_evidence_status": row.assigned_at_evidence_status.value,
        "ended_at": row.ended_at,
        "source_deleted": row.source_deleted,
        "assignment_source_partition_epoch_id": (
            row.assignment_source_partition_epoch_id
        ),
        "assignment_event_topic": row.assignment_event_topic,
        "assignment_event_partition": row.assignment_event_partition,
        "assignment_event_offset": row.assignment_event_offset,
        "assignment_source_row_revision": row.assignment_source_row_revision,
        "assignment_event_phase": row.assignment_event_phase.value,
    }


def _require_projection_matches_database(
    course: Mapping[str, Any],
    participations: Sequence[Mapping[str, Any]],
    *,
    expected_course: Mapping[str, Any],
    expected_participations: Sequence[CourseParticipation],
) -> None:
    if any(
        not _same_json(course.get(column), expected_course[column])
        for column in _COURSE_OWNED_COLUMNS
    ):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_REPLAY_STATE_MISMATCH"
        )
    if len(participations) != len(expected_participations):
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_REPLAY_STATE_MISMATCH"
        )
    for stored, expected in zip(
        participations,
        expected_participations,
    ):
        projected = _participation_projection(expected)
        if stored.get("participation_seq") != expected.participation_seq or any(
            not _same_json(stored.get(column), value)
            for column, value in projected.items()
        ):
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_REPLAY_STATE_MISMATCH"
            )


def _insert_course(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
    values: Mapping[str, Any],
) -> None:
    columns = ", ".join(_COURSE_OWNED_COLUMNS)
    binds = ", ".join(
        _course_bind_expression(column) for column in _COURSE_OWNED_COLUMNS
    )
    connection.execute(
        text(
            f"""
            INSERT INTO public.source_courses (
                source_region, source_appoint_id, {columns},
                teacher_region_evidence_status,evidence_status,row_version
            ) VALUES (
                :source_region, :source_appoint_id, {binds},
                'SOURCE_MISSING',
                CASE WHEN CAST(:appoint_evidence_status AS varchar(32))=
                          'SOURCE_CONFLICT'
                     THEN 'SOURCE_CONFLICT' ELSE 'SOURCE_MISSING' END,
                1
            )
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
            **_course_bind_values(values),
        },
    )


def _update_course(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
    values: Mapping[str, Any],
    expected_row_version: int,
) -> None:
    assignments = ", ".join(
        f"{column} = {_course_bind_expression(column)}"
        for column in _COURSE_OWNED_COLUMNS
    )
    result = connection.execute(
        text(
            f"""
            UPDATE public.source_courses
            SET {assignments},
                evidence_status=CASE
                  WHEN CAST(:appoint_evidence_status AS varchar(32))=
                         'SOURCE_CONFLICT'
                    OR teacher_region_evidence_status='SOURCE_CONFLICT'
                    THEN 'SOURCE_CONFLICT'
                  WHEN CAST(:appoint_evidence_status AS varchar(32))=
                         'SOURCE_MISSING'
                    OR teacher_region_evidence_status='SOURCE_MISSING'
                    THEN 'SOURCE_MISSING'
                  ELSE 'CONFIRMED'
                END,
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = :source_region
              AND source_appoint_id = :source_appoint_id
              AND row_version = :expected_row_version
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
            "expected_row_version": expected_row_version,
            **_course_bind_values(values),
        },
    )
    if result.rowcount != 1:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_CONCURRENT_UPDATE"
        )


def _course_bind_expression(column: str) -> str:
    return (
        f"CAST(:{column} AS jsonb)"
        if column in _COURSE_JSON_COLUMNS
        else f":{column}"
    )


def _course_bind_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        column: (
            None
            if values[column] is None
            else _canonical_json(values[column])
            if column in _COURSE_JSON_COLUMNS
            else values[column]
        )
        for column in _COURSE_OWNED_COLUMNS
    }


def _persist_participations(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
    existing: Sequence[Mapping[str, Any]],
    expected: Sequence[CourseParticipation],
) -> None:
    by_seq = {int(row["participation_seq"]): row for row in existing}
    expected_seqs = {row.participation_seq for row in expected}
    if set(by_seq) - expected_seqs:
        raise DtsV2CourseProjectorError(
            "DTS_V2_COURSE_EXTRA_PARTICIPATION"
        )

    # Clear/change existing current/role rows before inserts so PostgreSQL's
    # immediate partial unique indexes never observe two current/completion
    # rows inside the transaction.
    for participation in expected:
        stored = by_seq.get(participation.participation_seq)
        if stored is None:
            continue
        projected = _participation_projection(participation)
        if any(
            not _same_json(stored.get(column), projected[column])
            for column in _PARTICIPATION_IDENTITY_COLUMNS
        ):
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_PARTICIPATION_IDENTITY_MISMATCH"
            )
        mutable_changed = any(
            not _same_json(stored.get(column), projected[column])
            for column in _PARTICIPATION_MUTABLE_COLUMNS
        )
        if not mutable_changed:
            continue
        assignments = ", ".join(
            f"{column} = :{column}"
            for column in _PARTICIPATION_MUTABLE_COLUMNS
        )
        result = connection.execute(
            text(
                f"""
                UPDATE public.source_course_participations
                SET {assignments}, row_version = row_version + 1
                WHERE source_region = :source_region
                  AND source_appoint_id = :source_appoint_id
                  AND participation_seq = :participation_seq
                  AND row_version = :expected_row_version
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "participation_seq": participation.participation_seq,
                "expected_row_version": int(stored["row_version"]),
                **{
                    column: projected[column]
                    for column in _PARTICIPATION_MUTABLE_COLUMNS
                },
            },
        )
        if result.rowcount != 1:
            raise DtsV2CourseProjectorError(
                "DTS_V2_COURSE_PARTICIPATION_CONCURRENT_UPDATE"
            )

    insert_columns = (
        "teacher_id",
        "teacher_id_type",
        "participation_status",
        "participation_role",
        "is_current",
        "assigned_at",
        "assigned_at_evidence_status",
        "ended_at",
        "source_deleted",
        "assignment_source_partition_epoch_id",
        "assignment_event_topic",
        "assignment_event_partition",
        "assignment_event_offset",
        "assignment_source_row_revision",
        "assignment_event_phase",
    )
    columns = ", ".join(insert_columns)
    binds = ", ".join(f":{column}" for column in insert_columns)
    for participation in expected:
        if participation.participation_seq in by_seq:
            continue
        projected = _participation_projection(participation)
        connection.execute(
            text(
                f"""
                INSERT INTO public.source_course_participations (
                    source_region, source_appoint_id, participation_seq,
                    {columns}, absence_reason_detail, no_notice, row_version
                ) VALUES (
                    :source_region, :source_appoint_id, :participation_seq,
                    {binds}, NULL, NULL, 1
                )
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "participation_seq": participation.participation_seq,
                **{column: projected[column] for column in insert_columns},
            },
        )


def _same_json(left: Any, right: Any) -> bool:
    return _canonical_json(left) == _canonical_json(right)


def _canonical_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): normalize(child)
                for key, child in sorted(
                    item.items(), key=lambda pair: str(pair[0])
                )
            }
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if isinstance(item, Decimal):
            rendered = format(item, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return rendered or "0"
        if isinstance(item, datetime):
            if item.utcoffset() is not None:
                return item.astimezone(timezone.utc).isoformat()
            return item.isoformat()
        if isinstance(item, (date, time)):
            return item.isoformat()
        return item

    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
