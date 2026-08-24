"""Canonical identities for DTS v2 domain revisions and Outbox events.

The Domain Projector, Scope Coordinator and SourceWide Worker share this
protocol.  Keeping the identity construction in one value-only module avoids
each runtime inventing a subtly different JSON encoding, event id, or student
privacy rule.  Database mutation remains in the owning runtime transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from sqlalchemy import text


SOURCE_WIDE_CHANGED_EVENT_TYPE = "source_wide.changed.v2"
TASK_PLAN_EVENT_TYPE = "task.materialization.requested.v2"

DOMAIN_OUTBOX_AGGREGATE_TYPES = frozenset(
    {
        "COURSE",
        "PARTICIPATION",
        "TEACHER",
        "TEACHER_STUDENT",
        "LABEL",
        "COMPLAINT_CATEGORY",
        "COMPLETION_CONFLICT",
        "SOURCE_SCOPE",
    }
)
DOMAIN_AGGREGATE_TYPES = DOMAIN_OUTBOX_AGGREGATE_TYPES | {"TASK_PLAN"}
_DOMAIN_REVISION_PUBLISHER_AGGREGATE_TYPES = (
    DOMAIN_OUTBOX_AGGREGATE_TYPES - {"SOURCE_SCOPE"}
)

_REGIONS = frozenset({"dom", "ovs"})
_SCOPE_KINDS = frozenset({"CURRENT", "HISTORY"})
_SCOPE_LEVELS = frozenset({"GLOBAL", "TEACHER"})
_SCOPE_STATES = frozenset(
    {"INCOMPLETE", "LOADING", "VERIFYING", "COMPLETE", "FAILED", "STALE"}
)
_DOM_STUDENT_TOKEN = re.compile(r"^dom:v1:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "student_id",
        "studentid",
        "s_id",
        "sid",
        "stu_id",
        "stuid",
        "raw_student_id",
        "rawstudentid",
        "raw_student",
        "rawstudent",
        "user_id",
        "userid",
        "uid",
        "u_id",
        "mobile",
        "mobile_number",
        "mobilenumber",
        "phone",
        "phone_number",
        "phonenumber",
    }
)

_KEY_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "COURSE": ("source_region", "source_appoint_id"),
        "PARTICIPATION": (
            "source_region",
            "source_appoint_id",
            "participation_seq",
        ),
        "TEACHER": ("source_region", "teacher_id"),
        "TEACHER_STUDENT": (
            "source_region",
            "teacher_id",
            "student_token",
        ),
        "LABEL": ("source_region", "label_id"),
        "COMPLAINT_CATEGORY": ("source_region", "category_id"),
        "COMPLETION_CONFLICT": (
            "source_region",
            "source_appoint_id",
        ),
        "SOURCE_SCOPE": (
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
        ),
        "TASK_PLAN": ("assignment_dedupe_key",),
    }
)


class DtsV2DomainAggregateError(ValueError):
    """The proposed aggregate identity or event is not canonical."""


@dataclass(frozen=True)
class DtsV2DomainAggregateIdentity:
    aggregate_type: str
    aggregate_key: Mapping[str, Any]
    canonical_key_json: str
    aggregate_id: str


@dataclass(frozen=True)
class DtsV2DomainOutboxEvent:
    identity: DtsV2DomainAggregateIdentity
    aggregate_revision: int
    event_id: str
    outbox_id: str
    event_type: str
    payload: Mapping[str, Any]
    payload_sha256: str


@dataclass(frozen=True)
class DtsV2DomainRevisionPublishResult:
    status: str
    identity: DtsV2DomainAggregateIdentity
    aggregate_revision: int
    event: DtsV2DomainOutboxEvent | None


def build_domain_aggregate_identity_v2(
    aggregate_type: str,
    aggregate_key: Mapping[str, Any],
) -> DtsV2DomainAggregateIdentity:
    """Validate one typed aggregate key and derive its stable identity."""

    if aggregate_type not in DOMAIN_AGGREGATE_TYPES:
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_TYPE_INVALID")
    if not isinstance(aggregate_key, Mapping):
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_KEY_INVALID")
    expected_fields = _KEY_FIELDS[aggregate_type]
    if frozenset(aggregate_key) != frozenset(expected_fields):
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_KEY_SHAPE_INVALID")

    key = {field: aggregate_key[field] for field in expected_fields}
    _validate_aggregate_key(aggregate_type, key)
    canonical_key_json = _canonical_json(key)
    key_sha256 = hashlib.sha256(canonical_key_json.encode("utf-8")).hexdigest()
    aggregate_id = f"v2:{aggregate_type}:{key_sha256}"
    return DtsV2DomainAggregateIdentity(
        aggregate_type=aggregate_type,
        aggregate_key=MappingProxyType(key),
        canonical_key_json=canonical_key_json,
        aggregate_id=aggregate_id,
    )


def build_domain_outbox_event_v2(
    *,
    identity: DtsV2DomainAggregateIdentity,
    aggregate_revision: int,
    changed_fields: Sequence[str],
    source_row_revision: int | None,
    source_position: Mapping[str, Any] | None,
    rule_version: str | None,
    cutover_coverage_identity: Mapping[str, Any],
) -> DtsV2DomainOutboxEvent:
    """Build the immutable Outbox identity and privacy-safe payload."""

    if identity.aggregate_type not in DOMAIN_AGGREGATE_TYPES:
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_TYPE_INVALID")
    if (
        isinstance(aggregate_revision, bool)
        or not isinstance(aggregate_revision, int)
        or aggregate_revision < 1
    ):
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_REVISION_INVALID")
    normalized_changed_fields = _changed_fields(changed_fields)
    if rule_version is not None and (
        not isinstance(rule_version, str) or not rule_version.strip()
    ):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_RULE_VERSION_INVALID"
        )
    if not isinstance(cutover_coverage_identity, Mapping) or not (
        cutover_coverage_identity
    ):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_COVERAGE_IDENTITY_REQUIRED"
        )
    _reject_sensitive_payload_keys(cutover_coverage_identity)
    _reject_non_integer_json_numbers(cutover_coverage_identity)
    _validate_domain_trigger_coverage_v1(
        cutover_coverage_identity,
        source_row_revision=source_row_revision,
        source_position=source_position,
    )

    event_type = (
        TASK_PLAN_EVENT_TYPE
        if identity.aggregate_type == "TASK_PLAN"
        else SOURCE_WIDE_CHANGED_EVENT_TYPE
    )
    event_id = (
        f"{event_type}:{identity.aggregate_type}:"
        f"{identity.aggregate_id}:{aggregate_revision}"
    )
    outbox_id = f"outbox:v2:{hashlib.sha256(event_id.encode('utf-8')).hexdigest()}"
    payload: dict[str, Any] = {
        "protocol_version": "domain-aggregate-outbox-v2",
        "aggregate_key": dict(identity.aggregate_key),
        "changed_fields": normalized_changed_fields,
        "aggregate_revision": aggregate_revision,
        "source_row_revision": source_row_revision,
        "source_position": (
            None if source_position is None else dict(source_position)
        ),
        "rule_version": rule_version,
        "cutover_coverage_identity": dict(cutover_coverage_identity),
    }
    _reject_sensitive_payload_keys(payload)
    canonical_payload = _canonical_json(payload)
    return DtsV2DomainOutboxEvent(
        identity=identity,
        aggregate_revision=aggregate_revision,
        event_id=event_id,
        outbox_id=outbox_id,
        event_type=event_type,
        payload=MappingProxyType(payload),
        payload_sha256=hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest(),
    )


def canonical_domain_state_v2(state: Mapping[str, Any]) -> tuple[str, str]:
    """Return canonical JSON and SHA-256 for semantic change detection."""

    if not isinstance(state, Mapping):
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_STATE_INVALID")
    _reject_sensitive_payload_keys(state)
    canonical_state = _canonical_json(dict(state))
    return (
        canonical_state,
        hashlib.sha256(canonical_state.encode("utf-8")).hexdigest(),
    )


class DtsV2DomainRevisionStore:
    """Call the owner-checked PostgreSQL revision/Outbox command.

    The runtime deliberately has no direct INSERT/UPDATE privilege on
    ``domain_aggregate_revisions`` or immutable Outbox identity columns.
    """

    def publish_change(
        self,
        connection: Any,
        *,
        aggregate_type: str,
        aggregate_key: Mapping[str, Any],
        aggregate_state: Mapping[str, Any],
        changed_fields: Sequence[str],
        source_row_revision: int | None,
        source_position: Mapping[str, Any] | None,
        rule_version: str | None,
        cutover_coverage_identity: Mapping[str, Any],
    ) -> DtsV2DomainRevisionPublishResult:
        if aggregate_type not in _DOMAIN_REVISION_PUBLISHER_AGGREGATE_TYPES:
            raise DtsV2DomainAggregateError(
                "DTS_V2_DOMAIN_PUBLISHER_AGGREGATE_TYPE_INVALID"
            )
        identity = build_domain_aggregate_identity_v2(
            aggregate_type,
            aggregate_key,
        )
        canonical_state, state_sha256 = canonical_domain_state_v2(
            aggregate_state
        )
        normalized_changed_fields = _changed_fields(changed_fields)
        # Validate every immutable event field before reaching PostgreSQL.  The
        # revision is database-owned, so use one only for shape validation.
        build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=1,
            changed_fields=normalized_changed_fields,
            source_row_revision=source_row_revision,
            source_position=source_position,
            rule_version=rule_version,
            cutover_coverage_identity=cutover_coverage_identity,
        )
        value = connection.execute(
            text(
                """
                SELECT public.publish_domain_aggregate_revision_v2(
                    :aggregate_type,
                    CAST(:canonical_key AS jsonb),
                    CAST(:aggregate_state AS jsonb),
                    :aggregate_state_sha256,
                    CAST(:changed_fields AS jsonb),
                    :source_row_revision,
                    CAST(:source_position AS jsonb),
                    :rule_version,
                    CAST(:cutover_coverage_identity AS jsonb)
                )
                """
            ),
            {
                "aggregate_type": aggregate_type,
                "canonical_key": identity.canonical_key_json,
                "aggregate_state": canonical_state,
                "aggregate_state_sha256": state_sha256,
                "changed_fields": _canonical_json(normalized_changed_fields),
                "source_row_revision": source_row_revision,
                "source_position": (
                    None
                    if source_position is None
                    else _canonical_json(dict(source_position))
                ),
                "rule_version": rule_version,
                "cutover_coverage_identity": _canonical_json(
                    dict(cutover_coverage_identity)
                ),
            },
        ).scalar_one()
        result = _database_json_object(value)
        if result.get("aggregate_id") != identity.aggregate_id:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DATABASE_ID_MISMATCH"
            )
        revision = result.get("aggregate_revision")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DATABASE_REVISION_INVALID"
            )
        status = result.get("status")
        if status == "UNCHANGED":
            if any(
                result.get(field) is not None
                for field in ("event_id", "outbox_id", "payload_sha256")
            ):
                raise DtsV2DomainAggregateError(
                    "DTS_V2_AGGREGATE_DATABASE_NOOP_EVENT_INVALID"
                )
            return DtsV2DomainRevisionPublishResult(
                status=status,
                identity=identity,
                aggregate_revision=revision,
                event=None,
            )
        if status != "CHANGED":
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DATABASE_STATUS_INVALID"
            )
        event = build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=revision,
            changed_fields=normalized_changed_fields,
            source_row_revision=source_row_revision,
            source_position=source_position,
            rule_version=rule_version,
            cutover_coverage_identity=cutover_coverage_identity,
        )
        if (
            result.get("event_id") != event.event_id
            or result.get("outbox_id") != event.outbox_id
            or result.get("payload_sha256") != event.payload_sha256
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DATABASE_EVENT_MISMATCH"
            )
        return DtsV2DomainRevisionPublishResult(
            status=status,
            identity=identity,
            aggregate_revision=revision,
            event=event,
        )


def _validate_aggregate_key(aggregate_type: str, key: Mapping[str, Any]) -> None:
    if aggregate_type == "TASK_PLAN":
        _nonempty_string(key["assignment_dedupe_key"])
        return

    source_region = key["source_region"]
    if source_region not in _REGIONS:
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_REGION_INVALID")
    if aggregate_type in {"COURSE", "COMPLETION_CONFLICT"}:
        _nonempty_string(key["source_appoint_id"])
    elif aggregate_type == "PARTICIPATION":
        _nonempty_string(key["source_appoint_id"])
        participation_seq = key["participation_seq"]
        if (
            isinstance(participation_seq, bool)
            or not isinstance(participation_seq, int)
            or participation_seq < 1
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_PARTICIPATION_SEQ_INVALID"
            )
    elif aggregate_type == "TEACHER":
        _nonempty_string(key["teacher_id"])
    elif aggregate_type == "TEACHER_STUDENT":
        _nonempty_string(key["teacher_id"])
        student_token = _nonempty_string(key["student_token"])
        if source_region == "dom" and not _DOM_STUDENT_TOKEN.fullmatch(
            student_token
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DOM_STUDENT_TOKEN_INVALID"
            )
    elif aggregate_type == "LABEL":
        _nonempty_string(key["label_id"])
    elif aggregate_type == "COMPLAINT_CATEGORY":
        if source_region != "dom":
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COMPLAINT_REGION_INVALID"
            )
        _nonempty_string(key["category_id"])
    elif aggregate_type == "SOURCE_SCOPE":
        _nonempty_string(key["source_table"])
        if not str(key["source_table"]).startswith(f"{source_region}_"):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SCOPE_TABLE_REGION_MISMATCH"
            )
        if key["scope_kind"] not in _SCOPE_KINDS:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SCOPE_KIND_INVALID"
            )
        if key["scope_level"] not in _SCOPE_LEVELS:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SCOPE_LEVEL_INVALID"
            )
        scope_key = _nonempty_string(key["scope_key"])
        if (key["scope_level"] == "GLOBAL") != (scope_key == "*"):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SCOPE_KEY_INVALID"
            )


def _validate_source_position_v1(value: Any) -> None:
    """Match ``public.dts_v2_source_position_valid`` exactly.

    SOURCE-triggered domain revisions carry one concrete confirmed source
    revision.  Scope-triggered revisions use a typed coverage trigger and a
    NULL provenance pair because no single source row represents a scope.
    """

    fields = {
        "v",
        "source_timestamp",
        "record_id_type",
        "record_id",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
        "offset_value",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
        )
    if isinstance(value["v"], bool) or value["v"] != 1:
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
        )
    source_timestamp = value["source_timestamp"]
    if source_timestamp is not None and not isinstance(source_timestamp, str):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
        )
    record_id_type = value["record_id_type"]
    record_id = value["record_id"]
    if record_id_type not in {"none", "numeric", "text"} or (
        (record_id_type == "none" and record_id is not None)
        or (
            record_id_type in {"numeric", "text"}
            and (not isinstance(record_id, str) or not record_id)
        )
    ):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
        )
    for field in ("source_partition_epoch_id", "topic"):
        if not isinstance(value[field], str) or not value[field]:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
            )
    for field in ("partition_id", "offset_value"):
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_SOURCE_POSITION_INVALID"
            )


def _validate_domain_trigger_coverage_v1(
    coverage: Mapping[str, Any],
    *,
    source_row_revision: int | None,
    source_position: Mapping[str, Any] | None,
) -> None:
    trigger = coverage.get("trigger")
    if not isinstance(trigger, Mapping):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
        )
    input_kind = trigger.get("input_kind")
    identity = trigger.get("input_identity")
    if input_kind == "SOURCE_REVISION":
        expected_fields = {
            "input_kind",
            "input_identity",
            "input_revision",
            "input_fingerprint",
            "source_payload_hash",
        }
        if set(trigger) != expected_fields or not isinstance(identity, Mapping):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        if set(identity) != {"source_region", "source_table", "source_key"}:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        source_region = identity.get("source_region")
        source_table = identity.get("source_table")
        source_key = identity.get("source_key")
        if (
            source_region not in _REGIONS
            or not isinstance(source_table, str)
            or not source_table.startswith(f"{source_region}_")
            or not isinstance(source_key, str)
            or not source_key
            or not _positive_int(trigger.get("input_revision"))
            or not _sha256_value(trigger.get("input_fingerprint"))
            or not _sha256_value(trigger.get("source_payload_hash"))
            or not _positive_int(source_row_revision)
            or source_row_revision != trigger.get("input_revision")
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        _validate_source_position_v1(source_position)
        return
    if input_kind == "SCOPE_REVISION":
        expected_fields = {
            "input_kind",
            "input_identity",
            "input_revision",
            "input_fingerprint",
            "scope_state",
            "active_snapshot_id",
            "active_fence_hash",
        }
        if set(trigger) != expected_fields or not isinstance(identity, Mapping):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        if set(identity) != {
            "source_region",
            "source_table",
            "scope_kind",
            "scope_level",
            "scope_key",
        }:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        source_region = identity.get("source_region")
        source_table = identity.get("source_table")
        scope_level = identity.get("scope_level")
        scope_key = identity.get("scope_key")
        active_snapshot_id = trigger.get("active_snapshot_id")
        active_fence_hash = trigger.get("active_fence_hash")
        if (
            source_row_revision is not None
            or source_position is not None
            or source_region not in _REGIONS
            or not isinstance(source_table, str)
            or not source_table.startswith(f"{source_region}_")
            or identity.get("scope_kind") not in _SCOPE_KINDS
            or scope_level not in _SCOPE_LEVELS
            or not isinstance(scope_key, str)
            or (scope_level == "GLOBAL") != (scope_key == "*")
            or not _positive_int(trigger.get("input_revision"))
            or not _sha256_value(trigger.get("input_fingerprint"))
            or trigger.get("scope_state") not in _SCOPE_STATES
            or (
                active_snapshot_id is not None
                and (
                    not isinstance(active_snapshot_id, str)
                    or not active_snapshot_id
                )
            )
            or (
                active_fence_hash is not None
                and not _sha256_value(active_fence_hash)
            )
        ):
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
            )
        return
    raise DtsV2DomainAggregateError(
        "DTS_V2_AGGREGATE_COVERAGE_TRIGGER_INVALID"
    )


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _sha256_value(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _changed_fields(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_CHANGED_FIELDS_INVALID"
        )
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or _FIELD_NAME.fullmatch(value) is None:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_CHANGED_FIELDS_INVALID"
            )
        normalized.add(value)
    if not normalized:
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_CHANGED_FIELDS_REQUIRED"
        )
    return sorted(normalized)


def _nonempty_string(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or "\x00" in value
    ):
        raise DtsV2DomainAggregateError("DTS_V2_AGGREGATE_KEY_VALUE_INVALID")
    return value


def _reject_sensitive_payload_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if not isinstance(raw_key, str):
                raise DtsV2DomainAggregateError(
                    "DTS_V2_AGGREGATE_JSON_KEY_INVALID"
                )
            if raw_key.lower() in _FORBIDDEN_PAYLOAD_KEYS:
                raise DtsV2DomainAggregateError(
                    "DTS_V2_AGGREGATE_RAW_STUDENT_ID_FORBIDDEN"
                )
            _reject_sensitive_payload_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_sensitive_payload_keys(nested)


def _canonical_json(value: Any) -> str:
    _reject_non_integer_json_numbers(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DtsV2DomainAggregateError(
            "DTS_V2_AGGREGATE_JSON_INVALID"
        ) from exc


def _reject_non_integer_json_numbers(value: Any) -> None:
    """Keep v2 hashes identical across Python and PostgreSQL jsonb.

    PostgreSQL normalizes decimal/exponent spellings while ``json.dumps``
    preserves a different float representation.  v2 therefore only admits
    integer JSON numbers; decimal business values use canonical strings until
    a later protocol explicitly versions a decimal canonicalization rule.
    """

    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise DtsV2DomainAggregateError(
            "DTS_V2_DOMAIN_JSON_NUMBER_INVALID"
        )
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_non_integer_json_numbers(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_non_integer_json_numbers(nested)


def _database_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DtsV2DomainAggregateError(
                "DTS_V2_AGGREGATE_DATABASE_RESULT_INVALID"
            ) from exc
        if isinstance(decoded, dict):
            return decoded
    raise DtsV2DomainAggregateError(
        "DTS_V2_AGGREGATE_DATABASE_RESULT_INVALID"
    )


def is_sha256_v2(value: str) -> bool:
    """Small shared predicate for persistence adapters and tests."""

    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
