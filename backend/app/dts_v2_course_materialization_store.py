"""Protected PostgreSQL COURSE materialization client for DTS v2.

The database command receives only an authoritative aggregate/state-version
request.  It derives the compatibility row and lesson component settlements
from the typed source facts inside PostgreSQL; the Outbox role never receives
table-level DML or a raw pipeline-control read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_course_source_wide_plan import CourseSourceWidePlanV2
from .dts_v2_course_trigger_plan import build_course_trigger_plan_v2
from .dts_v2_runtime_guard import (
    DtsV2RuntimeTransactionGuard,
    PostgresDtsV2PrimaryTransactionGuard,
)


COURSE_MATERIALIZE_REGPROCEDURE = (
    "public.materialize_course_source_wide_v2("
    "jsonb,bigint,bigint,text,text)"
)
_REQUEST_PROTOCOL = "course-materialization-request-v1"
_RESULT_PROTOCOL = "course-materialization-result-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DtsV2ProtectedCourseMaterializerError(RuntimeError):
    """The protected COURSE command or its proof result is invalid."""


class DtsV2CourseScoreProjectionRefresher(Protocol):
    def refresh_course_and_teachers(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        teacher_ids: Sequence[str],
        projection_generation: int,
    ) -> Mapping[str, int]: ...


class DtsV2ProtectedCourseTriggerMatchStore(Protocol):
    def reconcile_course_matches(
        self,
        connection: Connection,
        plan: Any,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class DtsV2ProtectedCourseNonTaskOutputStore(Protocol):
    def reconcile_course_outputs(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class PostgresDtsV2CourseMaterializationCommandStore:
    """Invoke and prove the owner-only COURSE compatibility/component command."""

    def apply(
        self,
        connection: Connection,
        plan: CourseSourceWidePlanV2,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        _request_arguments(
            plan=plan,
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        aggregate_state_sha256 = _aggregate_state_sha256(
            connection,
            source_region=plan.source_region,
            source_appoint_id=plan.source_appoint_id,
            aggregate_revision=aggregate_revision,
        )
        request = _request_payload(
            plan,
            aggregate_state_sha256=aggregate_state_sha256,
        )
        canonical = _canonical_json(request)
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        value = connection.execute(
            text(
                """
                SELECT public.materialize_course_source_wide_v2(
                    CAST(:request AS jsonb),:aggregate_revision,
                    :projection_generation,:triggering_event_id,
                    :expected_request_sha256
                )
                """
            ),
            {
                "request": canonical,
                "aggregate_revision": aggregate_revision,
                "projection_generation": projection_generation,
                "triggering_event_id": triggering_event_id,
                "expected_request_sha256": expected_hash,
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != {
            "protocol_version",
            "request_sha256",
            "aggregate_state_sha256",
            "projection_generation",
            "affected_teacher_ids",
            "counts",
        }:
            _fail("DTS_V2_COURSE_COMMAND_RESULT_INVALID")
        if value.get("protocol_version") != _RESULT_PROTOCOL:
            _fail("DTS_V2_COURSE_COMMAND_PROTOCOL_INVALID")
        if value.get("request_sha256") != expected_hash:
            _fail("DTS_V2_COURSE_COMMAND_REQUEST_HASH_MISMATCH")
        if value.get("aggregate_state_sha256") != aggregate_state_sha256:
            _fail("DTS_V2_COURSE_COMMAND_AGGREGATE_HASH_MISMATCH")
        if value.get("projection_generation") != projection_generation:
            _fail("DTS_V2_COURSE_COMMAND_GENERATION_MISMATCH")
        affected = value.get("affected_teacher_ids")
        if not isinstance(affected, list) or tuple(affected) != tuple(
            plan.affected_teacher_ids
        ):
            _fail("DTS_V2_COURSE_COMMAND_TEACHERS_MISMATCH")
        return _counts(value.get("counts"))


class PostgresDtsV2ProtectedCourseMaterializer:
    """Compose protected COURSE, trigger and score commands in one caller tx."""

    def __init__(
        self,
        *,
        score_refresher: DtsV2CourseScoreProjectionRefresher,
        trigger_match_store: DtsV2ProtectedCourseTriggerMatchStore,
        non_task_output_store: DtsV2ProtectedCourseNonTaskOutputStore,
        command_store: PostgresDtsV2CourseMaterializationCommandStore | None = None,
        primary_guard: DtsV2RuntimeTransactionGuard | None = None,
    ) -> None:
        if not callable(
            getattr(score_refresher, "refresh_course_and_teachers", None)
        ):
            _fail("DTS_V2_COURSE_SCORE_REFRESHER_REQUIRED")
        if not callable(
            getattr(trigger_match_store, "reconcile_course_matches", None)
        ):
            _fail("DTS_V2_COURSE_TRIGGER_STORE_REQUIRED")
        if not callable(
            getattr(non_task_output_store, "reconcile_course_outputs", None)
        ):
            _fail("DTS_V2_COURSE_NON_TASK_OUTPUT_STORE_REQUIRED")
        self.score_refresher = score_refresher
        self.trigger_matches = trigger_match_store
        self.non_task_outputs = non_task_output_store
        self.command = command_store or (
            PostgresDtsV2CourseMaterializationCommandStore()
        )
        self.primary_guard = primary_guard or (
            PostgresDtsV2PrimaryTransactionGuard()
        )

    def apply_course_plan(
        self,
        connection: Connection,
        plan: CourseSourceWidePlanV2,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        _request_arguments(
            plan=plan,
            aggregate_revision=aggregate_revision,
            projection_generation=1,
            triggering_event_id=triggering_event_id,
            validate_generation=False,
        )
        projection_generation = self.primary_guard.acquire(
            connection, component="COURSE"
        )
        if type(projection_generation) is not int or projection_generation < 1:
            _fail("DTS_V2_COURSE_PRIMARY_MODE_REQUIRED")
        materialized = self.command.apply(
            connection,
            plan,
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        trigger_counts = self.trigger_matches.reconcile_course_matches(
            connection,
            build_course_trigger_plan_v2(plan),
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        non_task_output_counts = self.non_task_outputs.reconcile_course_outputs(
            connection,
            source_region=plan.source_region,
            source_appoint_id=plan.source_appoint_id,
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        refreshed = self.score_refresher.refresh_course_and_teachers(
            connection,
            source_region=plan.source_region,
            source_appoint_id=plan.source_appoint_id,
            teacher_ids=plan.affected_teacher_ids,
            projection_generation=projection_generation,
        )
        return _merge_counts(
            materialized,
            trigger_counts,
            non_task_output_counts,
            refreshed,
        )


def _aggregate_state_sha256(
    connection: Connection,
    *,
    source_region: str,
    source_appoint_id: str,
    aggregate_revision: int,
) -> str:
    rows = list(
        connection.execute(
            text(
                """
                SELECT aggregate_state_sha256
                FROM public.domain_aggregate_revisions
                WHERE aggregate_type='COURSE'
                  AND canonical_key=CAST(:canonical_key AS jsonb)
                  AND revision=:aggregate_revision
                """
            ),
            {
                "canonical_key": _canonical_json(
                    {
                        "source_region": source_region,
                        "source_appoint_id": source_appoint_id,
                    }
                ),
                "aggregate_revision": aggregate_revision,
            },
        ).mappings()
    )
    if len(rows) != 1:
        _fail("DTS_V2_COURSE_AGGREGATE_REVISION_STALE")
    value = rows[0].get("aggregate_state_sha256")
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("DTS_V2_COURSE_AGGREGATE_HASH_INVALID")
    return value


def _request_payload(
    plan: CourseSourceWidePlanV2,
    *,
    aggregate_state_sha256: str,
) -> Mapping[str, Any]:
    return {
        "protocol_version": _REQUEST_PROTOCOL,
        "source_region": plan.source_region,
        "source_appoint_id": plan.source_appoint_id,
        "aggregate_state_sha256": aggregate_state_sha256,
        "course_row_version": plan.course_row_version,
        "course_fact_row_version": plan.course_fact_row_version,
        "participation_versions": [
            {
                "participation_seq": row.participation_seq,
                "participation_row_version": row.participation_row_version,
                "participation_fact_row_version": (
                    row.participation_fact_row_version
                ),
            }
            for row in plan.participation_rows
        ],
        "affected_teacher_ids": list(plan.affected_teacher_ids),
    }


def _request_arguments(
    *,
    plan: CourseSourceWidePlanV2,
    aggregate_revision: int,
    projection_generation: int,
    triggering_event_id: str,
    validate_generation: bool = True,
) -> None:
    if not isinstance(plan, CourseSourceWidePlanV2):
        _fail("DTS_V2_COURSE_PLAN_REQUIRED")
    if type(aggregate_revision) is not int or aggregate_revision < 1:
        _fail("DTS_V2_COURSE_AGGREGATE_REVISION_INVALID")
    if validate_generation and (
        type(projection_generation) is not int or projection_generation < 1
    ):
        _fail("DTS_V2_COURSE_PROJECTION_GENERATION_INVALID")
    if (
        not isinstance(triggering_event_id, str)
        or not triggering_event_id
        or triggering_event_id.strip() != triggering_event_id
        or len(triggering_event_id) > 512
    ):
        _fail("DTS_V2_COURSE_EVENT_ID_INVALID")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _counts(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        _fail("DTS_V2_COURSE_COMMAND_COUNTS_INVALID")
    result: dict[str, int] = {}
    for name, count in value.items():
        if (
            not isinstance(name, str)
            or not name
            or type(count) is not int
            or count < 0
        ):
            _fail("DTS_V2_COURSE_COMMAND_COUNTS_INVALID")
        result[name] = count
    return result


def _merge_counts(*values: Mapping[str, Any]) -> Mapping[str, int]:
    result: dict[str, int] = {}
    for value in values:
        for name, count in _counts(value).items():
            result[name] = result.get(name, 0) + count
    return result


def _fail(code: str) -> None:
    raise DtsV2ProtectedCourseMaterializerError(code)


__all__ = [
    "COURSE_MATERIALIZE_REGPROCEDURE",
    "DtsV2ProtectedCourseMaterializerError",
    "PostgresDtsV2CourseMaterializationCommandStore",
    "PostgresDtsV2ProtectedCourseMaterializer",
]
