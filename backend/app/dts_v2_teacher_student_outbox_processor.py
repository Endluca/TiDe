"""TEACHER_STUDENT Outbox validation and favorite materialization plan.

The processor validates the latest aggregate snapshot and delegates database
state transitions to a materializer.  It never evaluates a future favorite
observation from the Outbox arrival time and never writes score rows itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Protocol

from sqlalchemy.engine import Connection

from .dts_v2_aggregate_state_reader import DtsV2AggregateStateReader
from .dts_v2_outbox_worker import DtsV2OutboxEvent


_REGIONS = frozenset({"dom", "ovs"})
_ID_TYPES = frozenset({"NUMERIC", "TEXT"})
_OBSERVATION_STATUSES = frozenset(
    {
        "PENDING",
        "CONFIRMED_TRUE",
        "CONFIRMED_FALSE",
        "WAITING_HISTORY",
        "WAITING_EVIDENCE",
    }
)
_EVIDENCE_STATUSES = frozenset(
    {"PENDING", "CONFIRMED", "HISTORY_INCOMPLETE", "SOURCE_MISSING"}
)
_ATTRIBUTION_ACTIONS = frozenset(
    {"NONE", "AWARD", "REVERSE", "RESELECT", "HOLD"}
)
_DOM_STUDENT_TOKEN = re.compile(r"^dom:v1:[0-9a-f]{64}$")


class DtsV2TeacherStudentOutboxProcessorError(RuntimeError):
    """A TEACHER_STUDENT event or aggregate state is unsafe to consume."""


@dataclass(frozen=True)
class FavoriteObservationMaterializationV2:
    source_region: str
    source_appoint_id: str
    appoint_id_type: str | None
    teacher_id: str
    teacher_id_type: str
    student_token: str
    completion_participation_seq: int
    completion_source_revision: int | None
    completion_end_time: datetime | None
    observed_at: datetime | None
    projection_status: str
    projection_relation_state: bool | None
    projection_evidence_status: str
    projection_error_code: str | None
    requires_materialization: bool

    @property
    def has_persistable_identity(self) -> bool:
        return (
            self.appoint_id_type in _ID_TYPES
            and self.completion_end_time is not None
            and self.observed_at is not None
        )


@dataclass(frozen=True)
class BlacklistThresholdMaterializationV2:
    threshold_state: str
    evidence_status: str
    source_collection_complete: bool
    distinct_active_student_count: int
    source_missing_student_count: int
    active_student_token_set_hash: str
    source_missing_student_token_set_hash: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class TeacherStudentMaterializationPlanV2:
    source_region: str
    teacher_id: str
    teacher_id_type: str
    student_token: str
    is_favorited: bool | None
    favorite_evidence_status: str
    is_blocked: bool | None
    block_evidence_status: str
    blacklist_threshold: BlacklistThresholdMaterializationV2
    observations: tuple[FavoriteObservationMaterializationV2, ...]
    projected_attribution_action: str
    projected_attribution_course_id: str | None


class DtsV2TeacherStudentMaterializer(Protocol):
    def apply_teacher_student_plan(
        self,
        connection: Connection,
        plan: TeacherStudentMaterializationPlanV2,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class DtsV2TeacherStudentOutboxProcessor:
    """Validate latest TEACHER_STUDENT state and apply one atomic plan."""

    def __init__(
        self,
        *,
        materializer: DtsV2TeacherStudentMaterializer,
        aggregate_reader: DtsV2AggregateStateReader | None = None,
    ) -> None:
        self.materializer = materializer
        self.aggregate_reader = aggregate_reader or DtsV2AggregateStateReader()

    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]:
        if (
            event.aggregate_type != "TEACHER_STUDENT"
            or event.event_type != "source_wide.changed.v2"
        ):
            raise DtsV2TeacherStudentOutboxProcessorError(
                "DTS_V2_TEACHER_STUDENT_OUTBOX_EVENT_REQUIRED"
            )
        snapshot = self.aggregate_reader.read_current(connection, event)
        plan = build_teacher_student_materialization_plan_v2(
            aggregate_key=snapshot.aggregate_key,
            aggregate_state=snapshot.aggregate_state,
        )
        result = self.materializer.apply_teacher_student_plan(
            connection,
            plan,
            aggregate_revision=snapshot.current_revision,
            triggering_event_id=event.event_id,
        )
        if not isinstance(result, Mapping):
            raise DtsV2TeacherStudentOutboxProcessorError(
                "DTS_V2_TEACHER_STUDENT_MATERIALIZER_RESULT_INVALID"
            )
        counts: dict[str, int] = {
            "superseded_events": int(snapshot.is_superseded_event)
        }
        for name, count in result.items():
            if (
                not isinstance(name, str)
                or not name
                or type(count) is not int
                or count < 0
            ):
                raise DtsV2TeacherStudentOutboxProcessorError(
                    "DTS_V2_TEACHER_STUDENT_MATERIALIZER_RESULT_INVALID"
                )
            counts[name] = count
        return counts


def build_teacher_student_materialization_plan_v2(
    *,
    aggregate_key: Mapping[str, Any],
    aggregate_state: Mapping[str, Any],
) -> TeacherStudentMaterializationPlanV2:
    region = aggregate_key.get("source_region")
    teacher_id = aggregate_key.get("teacher_id")
    student_token = aggregate_key.get("student_token")
    if (
        region not in _REGIONS
        or not isinstance(teacher_id, str)
        or not teacher_id
        or not isinstance(student_token, str)
        or not student_token
    ):
        _fail("IDENTITY_INVALID")
    assert isinstance(region, str)
    assert isinstance(teacher_id, str)
    assert isinstance(student_token, str)
    if region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(student_token) is None:
        _fail("DOM_TOKEN_INVALID")
    if aggregate_state.get("protocol_version") != "teacher-student-domain-v1":
        _fail("STATE_PROTOCOL_INVALID")

    current = _mapping(aggregate_state.get("relationship_current"), "CURRENT")
    teacher_id_type = current.get("teacher_id_type")
    if teacher_id_type not in _ID_TYPES:
        _fail("TEACHER_ID_TYPE_INVALID")
    assert isinstance(teacher_id_type, str)
    if _canonical_id(teacher_id, teacher_id_type) != teacher_id:
        _fail("TEACHER_ID_INVALID")
    is_favorited = _three_state(current.get("is_favorited"), "FAVORITE")
    is_blocked = _three_state(current.get("is_blocked"), "BLOCK")
    favorite_evidence = _current_evidence(
        current.get("favorite_evidence_status"),
        is_favorited,
        "FAVORITE",
    )
    block_evidence = _current_evidence(
        current.get("block_evidence_status"),
        is_blocked,
        "BLOCK",
    )
    _validate_relationship_evidence(
        aggregate_state.get("relationship_evidence")
    )
    scopes = _validate_scope_evidence(aggregate_state.get("scope_evidence"))
    blacklist_threshold = _blacklist_threshold(
        aggregate_state.get("blacklist_threshold"),
        source_region=region,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        scopes=scopes,
    )

    raw_observations = aggregate_state.get("favorite_observation_requirements")
    if not isinstance(raw_observations, Sequence) or isinstance(
        raw_observations, (str, bytes, bytearray)
    ):
        _fail("OBSERVATIONS_INVALID")
    observations = tuple(
        _observation(
            value,
            source_region=region,
            teacher_id=teacher_id,
            teacher_id_type=teacher_id_type,
            student_token=student_token,
        )
        for value in raw_observations
    )
    identities = {
        (row.source_appoint_id, row.completion_participation_seq)
        for row in observations
    }
    if len(identities) != len(observations):
        _fail("OBSERVATION_DUPLICATE")

    attribution = _mapping(
        aggregate_state.get("favorite_attribution_intent"),
        "ATTRIBUTION",
    )
    action = attribution.get("action")
    selected = attribution.get("selected_source_appoint_id")
    if action not in _ATTRIBUTION_ACTIONS:
        _fail("ATTRIBUTION_ACTION_INVALID")
    if selected is not None and (
        not isinstance(selected, str) or not selected
    ):
        _fail("ATTRIBUTION_COURSE_INVALID")
    if action in {"AWARD", "RESELECT"} and selected not in {
        row.source_appoint_id for row in observations
    }:
        _fail("ATTRIBUTION_COURSE_INVALID")

    return TeacherStudentMaterializationPlanV2(
        source_region=region,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        student_token=student_token,
        is_favorited=is_favorited,
        favorite_evidence_status=favorite_evidence,
        is_blocked=is_blocked,
        block_evidence_status=block_evidence,
        blacklist_threshold=blacklist_threshold,
        observations=observations,
        projected_attribution_action=str(action),
        projected_attribution_course_id=(
            str(selected) if selected is not None else None
        ),
    )


def _observation(
    value: Any,
    *,
    source_region: str,
    teacher_id: str,
    teacher_id_type: str,
    student_token: str,
) -> FavoriteObservationMaterializationV2:
    row = _mapping(value, "OBSERVATION")
    appoint_id = row.get("source_appoint_id")
    appoint_type = row.get("appoint_id_type")
    seq = row.get("completion_participation_seq")
    revision = row.get("completion_source_revision")
    if not isinstance(appoint_id, str) or not appoint_id:
        _fail("OBSERVATION_APPOINT_ID_INVALID")
    if appoint_type is not None and appoint_type not in _ID_TYPES:
        _fail("OBSERVATION_APPOINT_TYPE_INVALID")
    if appoint_type is not None and _canonical_id(
        appoint_id, str(appoint_type)
    ) != appoint_id:
        _fail("OBSERVATION_APPOINT_ID_INVALID")
    if type(seq) is not int or seq < 1:
        _fail("OBSERVATION_PARTICIPATION_INVALID")
    if revision is not None and (type(revision) is not int or revision < 1):
        _fail("OBSERVATION_SOURCE_REVISION_INVALID")

    completion_end = _datetime_or_none(row.get("completion_end_time"))
    observed_at = _datetime_or_none(row.get("observed_at"))
    if (completion_end is None) is not (observed_at is None):
        _fail("OBSERVATION_TIME_SHAPE_INVALID")
    if completion_end is not None and observed_at != completion_end + timedelta(
        hours=24
    ):
        _fail("OBSERVATION_TIME_MISMATCH")
    due = row.get("observation_due")
    if due is not None and type(due) is not bool:
        _fail("OBSERVATION_DUE_INVALID")

    status = row.get("status")
    relation_state = row.get("relation_state")
    evidence = row.get("relation_evidence_status")
    error = row.get("relation_error_code")
    if status not in _OBSERVATION_STATUSES or evidence not in _EVIDENCE_STATUSES:
        _fail("OBSERVATION_EVIDENCE_INVALID")
    if relation_state is not None and type(relation_state) is not bool:
        _fail("OBSERVATION_EVIDENCE_INVALID")
    if error is not None and (not isinstance(error, str) or not error):
        _fail("OBSERVATION_EVIDENCE_INVALID")
    _validate_projected_observation_evidence(
        str(status), relation_state, str(evidence), error
    )
    requires = row.get("requires_materialization")
    if type(requires) is not bool:
        _fail("OBSERVATION_MATERIALIZATION_FLAG_INVALID")

    return FavoriteObservationMaterializationV2(
        source_region=source_region,
        source_appoint_id=appoint_id,
        appoint_id_type=(str(appoint_type) if appoint_type is not None else None),
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        student_token=student_token,
        completion_participation_seq=seq,
        completion_source_revision=revision,
        completion_end_time=completion_end,
        observed_at=observed_at,
        projection_status=str(status),
        projection_relation_state=relation_state,
        projection_evidence_status=str(evidence),
        projection_error_code=error,
        requires_materialization=requires,
    )


def _validate_projected_observation_evidence(
    status: str,
    relation_state: bool | None,
    evidence: str,
    error: str | None,
) -> None:
    valid = (
        status == "CONFIRMED_TRUE"
        and relation_state is True
        and evidence == "CONFIRMED"
        and error is None
    ) or (
        status == "CONFIRMED_FALSE"
        and relation_state is False
        and evidence == "CONFIRMED"
        and error is None
    ) or (
        status == "WAITING_HISTORY"
        and relation_state is None
        and evidence == "HISTORY_INCOMPLETE"
        and error == "PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE"
    ) or (
        status == "WAITING_EVIDENCE"
        and relation_state is None
        and evidence == "SOURCE_MISSING"
        and isinstance(error, str)
        and (
            error.startswith("SOURCE_MISSING:")
            or error.startswith("SOURCE_CONFLICT:")
        )
    ) or (
        status == "PENDING"
        and relation_state is None
        and evidence == "PENDING"
        and error is None
    )
    if not valid:
        _fail("OBSERVATION_EVIDENCE_INVALID")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name}_INVALID")
    return value


def _validate_relationship_evidence(value: Any) -> None:
    evidence = _mapping(value, "RELATIONSHIP_EVIDENCE")
    for name in (
        "current_row_version",
        "last_event_sequence",
        "last_partition_id",
        "last_offset_value",
        "last_source_row_revision",
    ):
        item = evidence.get(name)
        minimum = 0 if name in {"last_partition_id", "last_offset_value"} else 1
        if type(item) is not int or item < minimum:
            _fail("RELATIONSHIP_EVIDENCE_INVALID")
    for name in ("last_source_partition_epoch_id", "last_topic"):
        item = evidence.get(name)
        if not isinstance(item, str) or not item:
            _fail("RELATIONSHIP_EVIDENCE_INVALID")


def _validate_scope_evidence(
    value: Any,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        _fail("SCOPE_EVIDENCE_INVALID")
    scopes: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in value:
        row = _mapping(item, "SCOPE_EVIDENCE")
        source_table = row.get("source_table")
        scope_kind = row.get("scope_kind")
        state = row.get("state")
        if (
            not isinstance(source_table, str)
            or not source_table
            or scope_kind not in {"CURRENT", "HISTORY"}
            or state not in {
                "UNKNOWN",
                "INCOMPLETE",
                "LOADING",
                "VERIFYING",
                "COMPLETE",
                "FAILED",
                "STALE",
            }
        ):
            _fail("SCOPE_EVIDENCE_INVALID")
        identity = (source_table, str(scope_kind))
        if identity in scopes:
            _fail("SCOPE_EVIDENCE_INVALID")
        scopes[identity] = row
        row_version = row.get("row_version")
        if row_version is not None and (
            type(row_version) is not int or row_version < 1
        ):
            _fail("SCOPE_EVIDENCE_INVALID")
        snapshot_id = row.get("active_snapshot_id")
        fence_hash = row.get("active_fence_hash")
        if (snapshot_id is None) is not (fence_hash is None):
            _fail("SCOPE_EVIDENCE_INVALID")
        if snapshot_id is not None and (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(fence_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
        ):
            _fail("SCOPE_EVIDENCE_INVALID")
    return scopes


def _blacklist_threshold(
    value: Any,
    *,
    source_region: str,
    teacher_id: str,
    teacher_id_type: str,
    scopes: Mapping[tuple[str, str], Mapping[str, Any]],
) -> BlacklistThresholdMaterializationV2:
    row = _mapping(value, "BLACKLIST_THRESHOLD")
    expected_fields = {
        "protocol_version",
        "source_region",
        "teacher_id",
        "teacher_id_type",
        "threshold",
        "threshold_state",
        "evidence_status",
        "source_collection_complete",
        "distinct_active_student_count",
        "source_missing_student_count",
        "active_student_token_set_hash",
        "source_missing_student_token_set_hash",
        "scope",
    }
    if set(row) != expected_fields:
        _fail("BLACKLIST_THRESHOLD_SHAPE_INVALID")
    if (
        row.get("protocol_version") != "blacklist-threshold-evidence-v1"
        or row.get("source_region") != source_region
        or row.get("teacher_id") != teacher_id
        or row.get("teacher_id_type") != teacher_id_type
        or row.get("threshold") != 2
    ):
        _fail("BLACKLIST_THRESHOLD_IDENTITY_INVALID")

    threshold_state = row.get("threshold_state")
    evidence_status = row.get("evidence_status")
    collection_complete = row.get("source_collection_complete")
    active_count = row.get("distinct_active_student_count")
    missing_count = row.get("source_missing_student_count")
    if (
        threshold_state not in {"ACTIVE", "SUPPRESSED", "SOURCE_MISSING"}
        or evidence_status not in {"CONFIRMED", "SOURCE_MISSING"}
        or type(collection_complete) is not bool
        or type(active_count) is not int
        or active_count < 0
        or type(missing_count) is not int
        or missing_count < 0
    ):
        _fail("BLACKLIST_THRESHOLD_STATE_INVALID")
    for name in (
        "active_student_token_set_hash",
        "source_missing_student_token_set_hash",
    ):
        item = row.get(name)
        if not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None:
            _fail("BLACKLIST_THRESHOLD_HASH_INVALID")

    valid_state = (
        threshold_state == "ACTIVE"
        and evidence_status == "CONFIRMED"
        and active_count >= 2
    ) or (
        threshold_state == "SUPPRESSED"
        and evidence_status == "CONFIRMED"
        and active_count < 2
        and collection_complete
        and missing_count == 0
    ) or (
        threshold_state == "SOURCE_MISSING"
        and evidence_status == "SOURCE_MISSING"
        and active_count < 2
        and (not collection_complete or missing_count > 0)
    )
    if not valid_state:
        _fail("BLACKLIST_THRESHOLD_BOUNDARY_INVALID")

    scope = _mapping(row.get("scope"), "BLACKLIST_THRESHOLD_SCOPE")
    scope_identity = (f"{source_region}_teacher_blacklist", "CURRENT")
    aggregate_scope = scopes.get(scope_identity)
    if aggregate_scope is None or dict(scope) != dict(aggregate_scope):
        _fail("BLACKLIST_THRESHOLD_SCOPE_MISMATCH")
    if (
        scope.get("source_table") != scope_identity[0]
        or scope.get("scope_kind") != "CURRENT"
        or collection_complete != (scope.get("state") == "COMPLETE")
    ):
        _fail("BLACKLIST_THRESHOLD_SCOPE_INVALID")

    evidence = dict(row)
    evidence["scope"] = dict(scope)
    return BlacklistThresholdMaterializationV2(
        threshold_state=str(threshold_state),
        evidence_status=str(evidence_status),
        source_collection_complete=collection_complete,
        distinct_active_student_count=active_count,
        source_missing_student_count=missing_count,
        active_student_token_set_hash=str(
            row["active_student_token_set_hash"]
        ),
        source_missing_student_token_set_hash=str(
            row["source_missing_student_token_set_hash"]
        ),
        evidence=evidence,
    )


def _three_state(value: Any, name: str) -> bool | None:
    if value is not None and type(value) is not bool:
        _fail(f"{name}_STATE_INVALID")
    return value


def _current_evidence(value: Any, state: bool | None, name: str) -> str:
    if value not in {"CONFIRMED", "SOURCE_MISSING"}:
        _fail(f"{name}_EVIDENCE_INVALID")
    if state is None and value != "SOURCE_MISSING":
        _fail(f"{name}_EVIDENCE_INVALID")
    return str(value)


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            _fail("OBSERVATION_DATETIME_INVALID")
    else:
        _fail("OBSERVATION_DATETIME_INVALID")
    if parsed.utcoffset() is None:
        _fail("OBSERVATION_DATETIME_INVALID")
    return parsed


def _canonical_id(value: Any, value_type: str) -> str:
    if value_type == "TEXT":
        if not isinstance(value, str) or not value:
            _fail("TEXT_ID_INVALID")
        return value
    if value_type != "NUMERIC" or isinstance(value, bool) or value is None:
        _fail("NUMERIC_ID_INVALID")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _fail("NUMERIC_ID_INVALID")
    if not number.is_finite():
        _fail("NUMERIC_ID_INVALID")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _fail(code: str) -> None:
    raise DtsV2TeacherStudentOutboxProcessorError(
        f"DTS_V2_TEACHER_STUDENT_{code}"
    )


__all__ = [
    "DtsV2TeacherStudentMaterializer",
    "DtsV2TeacherStudentOutboxProcessor",
    "DtsV2TeacherStudentOutboxProcessorError",
    "BlacklistThresholdMaterializationV2",
    "FavoriteObservationMaterializationV2",
    "TeacherStudentMaterializationPlanV2",
    "build_teacher_student_materialization_plan_v2",
]
