"""Pure current-state and threshold rules for teacher blacklist sources.

The reducer consumes the complete current set for one typed teacher in one
region.  It has no course dependency and never creates or mutates an
assignment.  A source row is an effective blacklist relation only when its
permanent flag is true or its end-year is the approved 2999+ sentinel.  An
ordinary finite validity interval is not a business blacklist relation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence


BLACKLIST_THRESHOLD = 2

RELATION_BLOCKED = "BLOCKED"
RELATION_NOT_BLOCKED = "NOT_BLOCKED"
RELATION_SOURCE_MISSING = "SOURCE_MISSING"

THRESHOLD_ACTIVE = "ACTIVE"
THRESHOLD_SUPPRESSED = "SUPPRESSED"
THRESHOLD_SOURCE_MISSING = "SOURCE_MISSING"

EVIDENCE_CONFIRMED = "CONFIRMED"
EVIDENCE_SOURCE_MISSING = "SOURCE_MISSING"

_SUPPORTED_REGIONS = frozenset({"dom", "ovs"})
_SUPPORTED_ID_TYPES = frozenset({"NUMERIC", "TEXT"})
_DOM_STUDENT_TOKEN = re.compile(r"^dom:v1:[0-9a-f]{64}$")


class DtsBlacklistRuleError(ValueError):
    """The supplied current set is structurally unsafe to evaluate."""


@dataclass(frozen=True)
class TypedBlacklistIdV2:
    """One canonical source identity that cannot collide across type tags."""

    id_type: str
    value: Any
    canonical_value: str = field(init=False)

    def __post_init__(self) -> None:
        if self.id_type not in _SUPPORTED_ID_TYPES:
            raise DtsBlacklistRuleError("DTS_BLACKLIST_ID_TYPE_INVALID")
        if self.id_type == "TEXT":
            if not isinstance(self.value, str) or not self.value:
                raise DtsBlacklistRuleError("DTS_BLACKLIST_TEXT_ID_INVALID")
            object.__setattr__(self, "canonical_value", self.value)
            return
        if isinstance(self.value, bool) or self.value is None:
            raise DtsBlacklistRuleError("DTS_BLACKLIST_NUMERIC_ID_INVALID")
        try:
            number = Decimal(str(self.value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_NUMERIC_ID_INVALID"
            ) from exc
        if not number.is_finite():
            raise DtsBlacklistRuleError("DTS_BLACKLIST_NUMERIC_ID_INVALID")
        if number == 0:
            number = Decimal("0")
        object.__setattr__(self, "value", number)
        object.__setattr__(self, "canonical_value", _decimal_text(number))


@dataclass(frozen=True)
class BlacklistSourceRecordV2:
    """Protected typed current row for one blacklist source record."""

    source_region: str
    source_id: TypedBlacklistIdV2
    teacher_id: TypedBlacklistIdV2
    student_token: str
    is_deleted: bool = False
    valid_start_time: datetime | None = None
    add_time: datetime | None = None
    source_timestamp: datetime | None = None
    valid_end_time: datetime | None = None
    is_valid_forever: bool | int | Decimal | None = None


@dataclass(frozen=True)
class BlacklistStudentRelationV2:
    """Three-valued relation rebuilt from all remaining source records."""

    student_token: str
    relation_state: str
    active_source_ids: tuple[TypedBlacklistIdV2, ...]
    inactive_source_ids: tuple[TypedBlacklistIdV2, ...]
    source_missing_ids: tuple[TypedBlacklistIdV2, ...]


@dataclass(frozen=True)
class BlacklistThresholdProjectionV2:
    """Region-local teacher threshold; assignment lifecycle is out of scope."""

    source_region: str
    teacher_id: TypedBlacklistIdV2
    business_as_of: datetime
    source_collection_complete: bool
    student_relations: tuple[BlacklistStudentRelationV2, ...]
    active_student_tokens: tuple[str, ...]
    source_missing_student_tokens: tuple[str, ...]
    distinct_active_student_count: int
    threshold_state: str
    evidence_status: str


def rebuild_blacklist_threshold_v2(
    *,
    source_region: str,
    teacher_id: TypedBlacklistIdV2,
    source_records: Sequence[BlacklistSourceRecordV2],
    business_as_of: datetime,
    source_collection_complete: bool,
) -> BlacklistThresholdProjectionV2:
    """Rebuild one region/teacher from the full current source collection.

    Deleted records never contribute.  Removing one source row therefore
    cannot clear a relation that another active row still proves.  A known
    active record wins over overlapping unknown records; without such proof,
    missing boundaries or incomplete collection coverage remain
    ``SOURCE_MISSING`` rather than being guessed false.
    """

    if source_region not in _SUPPORTED_REGIONS:
        raise DtsBlacklistRuleError("DTS_BLACKLIST_REGION_INVALID")
    if not isinstance(teacher_id, TypedBlacklistIdV2):
        raise DtsBlacklistRuleError("DTS_BLACKLIST_TEACHER_ID_INVALID")
    _require_aware_datetime(
        business_as_of,
        "DTS_BLACKLIST_BUSINESS_AS_OF_INVALID",
    )
    if type(source_collection_complete) is not bool:
        raise DtsBlacklistRuleError(
            "DTS_BLACKLIST_COLLECTION_COMPLETENESS_INVALID"
        )
    if not isinstance(source_records, Sequence) or isinstance(
        source_records,
        (str, bytes, bytearray),
    ):
        raise DtsBlacklistRuleError("DTS_BLACKLIST_SOURCE_RECORDS_INVALID")

    seen_source_ids: set[TypedBlacklistIdV2] = set()
    grouped: dict[
        str,
        list[tuple[TypedBlacklistIdV2, str]],
    ] = {}
    for record in source_records:
        if not isinstance(record, BlacklistSourceRecordV2):
            raise DtsBlacklistRuleError("DTS_BLACKLIST_SOURCE_RECORD_INVALID")
        if record.source_region != source_region:
            raise DtsBlacklistRuleError("DTS_BLACKLIST_RECORD_REGION_MISMATCH")
        if not isinstance(record.source_id, TypedBlacklistIdV2):
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_RECORD_SOURCE_ID_INVALID"
            )
        if not isinstance(record.teacher_id, TypedBlacklistIdV2):
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_RECORD_TEACHER_ID_INVALID"
            )
        if record.teacher_id != teacher_id:
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_RECORD_TEACHER_MISMATCH"
            )
        if type(record.is_deleted) is not bool:
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_DELETED_FLAG_INVALID"
            )
        if record.source_id in seen_source_ids:
            raise DtsBlacklistRuleError(
                "DTS_BLACKLIST_SOURCE_ID_DUPLICATE"
            )
        seen_source_ids.add(record.source_id)
        _validate_student_token(source_region, record.student_token)
        _validate_record_times(record)
        _normalize_permanent_flag(record.is_valid_forever)
        if record.is_deleted:
            continue

        record_state = _evaluate_source_record(
            record,
            business_as_of=business_as_of,
        )
        grouped.setdefault(record.student_token, []).append(
            (record.source_id, record_state)
        )

    relations = tuple(
        _reduce_student_relation(student_token, grouped[student_token])
        for student_token in sorted(grouped, key=_utf8_sort_key)
    )
    active_student_tokens = tuple(
        relation.student_token
        for relation in relations
        if relation.relation_state == RELATION_BLOCKED
    )
    source_missing_student_tokens = tuple(
        relation.student_token
        for relation in relations
        if relation.relation_state == RELATION_SOURCE_MISSING
    )
    distinct_active_student_count = len(active_student_tokens)
    if distinct_active_student_count >= BLACKLIST_THRESHOLD:
        threshold_state = THRESHOLD_ACTIVE
    elif (
        source_collection_complete
        and not source_missing_student_tokens
    ):
        threshold_state = THRESHOLD_SUPPRESSED
    else:
        threshold_state = THRESHOLD_SOURCE_MISSING

    return BlacklistThresholdProjectionV2(
        source_region=source_region,
        teacher_id=teacher_id,
        business_as_of=business_as_of,
        source_collection_complete=source_collection_complete,
        student_relations=relations,
        active_student_tokens=active_student_tokens,
        source_missing_student_tokens=source_missing_student_tokens,
        distinct_active_student_count=distinct_active_student_count,
        threshold_state=threshold_state,
        evidence_status=(
            EVIDENCE_SOURCE_MISSING
            if threshold_state == THRESHOLD_SOURCE_MISSING
            else EVIDENCE_CONFIRMED
        ),
    )


def _evaluate_source_record(
    record: BlacklistSourceRecordV2,
    *,
    business_as_of: datetime,
) -> str:
    permanent = _normalize_permanent_flag(record.is_valid_forever)
    end = record.valid_end_time
    is_approved_permanent_relation = permanent is True or (
        end is not None and end.year >= 2999
    )
    if not is_approved_permanent_relation:
        return RELATION_NOT_BLOCKED

    start = (
        record.valid_start_time
        if record.valid_start_time is not None
        else record.add_time
        if record.add_time is not None
        else record.source_timestamp
    )
    if start is None:
        return RELATION_SOURCE_MISSING
    if permanent is not True and end is not None and end < start:
        return RELATION_SOURCE_MISSING
    return (
        RELATION_BLOCKED
        if business_as_of >= start
        else RELATION_NOT_BLOCKED
    )


def _validate_record_times(record: BlacklistSourceRecordV2) -> None:
    for value, code in (
        (record.valid_start_time, "DTS_BLACKLIST_VALID_START_TIME_INVALID"),
        (record.add_time, "DTS_BLACKLIST_ADD_TIME_INVALID"),
        (record.source_timestamp, "DTS_BLACKLIST_SOURCE_TIMESTAMP_INVALID"),
        (record.valid_end_time, "DTS_BLACKLIST_VALID_END_TIME_INVALID"),
    ):
        if value is not None:
            _require_aware_datetime(value, code)


def _reduce_student_relation(
    student_token: str,
    source_states: Sequence[tuple[TypedBlacklistIdV2, str]],
) -> BlacklistStudentRelationV2:
    active = _sorted_ids(
        source_id
        for source_id, state in source_states
        if state == RELATION_BLOCKED
    )
    inactive = _sorted_ids(
        source_id
        for source_id, state in source_states
        if state == RELATION_NOT_BLOCKED
    )
    missing = _sorted_ids(
        source_id
        for source_id, state in source_states
        if state == RELATION_SOURCE_MISSING
    )
    relation_state = (
        RELATION_BLOCKED
        if active
        else RELATION_SOURCE_MISSING
        if missing
        else RELATION_NOT_BLOCKED
    )
    return BlacklistStudentRelationV2(
        student_token=student_token,
        relation_state=relation_state,
        active_source_ids=active,
        inactive_source_ids=inactive,
        source_missing_ids=missing,
    )


def _validate_student_token(source_region: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise DtsBlacklistRuleError("DTS_BLACKLIST_STUDENT_TOKEN_INVALID")
    if source_region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(value) is None:
        raise DtsBlacklistRuleError(
            "DTS_BLACKLIST_DOM_STUDENT_TOKEN_INVALID"
        )
    return value


def _normalize_permanent_flag(value: Any) -> bool | None:
    if value is None or type(value) is bool:
        return value
    if isinstance(value, Decimal) and value.is_finite() and value in {0, 1}:
        return bool(value)
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise DtsBlacklistRuleError("DTS_BLACKLIST_PERMANENT_FLAG_INVALID")


def _require_aware_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DtsBlacklistRuleError(code)
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise DtsBlacklistRuleError(code) from exc
    if offset is None:
        raise DtsBlacklistRuleError(code)
    return value


def _sorted_ids(
    values: Sequence[TypedBlacklistIdV2] | Any,
) -> tuple[TypedBlacklistIdV2, ...]:
    return tuple(sorted(values, key=_typed_id_sort_key))


def _typed_id_sort_key(value: TypedBlacklistIdV2) -> tuple[Any, ...]:
    if value.id_type == "NUMERIC":
        return (0, value.value)
    return (1, value.canonical_value.encode("utf-8"))


def _utf8_sort_key(value: str) -> bytes:
    return value.encode("utf-8")


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"
