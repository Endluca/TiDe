"""TEACHER_STUDENT dirty-key projection for DTS v2.

This module deliberately keeps three different facts separate:

* the course-independent current FAVORITE/BLOCK relationship;
* the course ``completion_end_time + 24h`` observation requirements;
* the unique-course attribution decision that a later observation/score
  worker may materialize.

It does not create favorite observations, mutate attributions, or write score
entries.  Missing current/history scope or authoritative business timestamps
remain three-valued evidence and are never converted to ``False``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from .dts_blacklist_rules_v2 import (
    BlacklistSourceRecordV2,
    RELATION_BLOCKED,
    RELATION_SOURCE_MISSING,
    TypedBlacklistIdV2,
    rebuild_blacklist_threshold_v2,
)
from .dts_favorite_rules_v2 import (
    FavoriteCourseObservation,
    FavoriteInterval,
    FavoriteObservationStatus,
    FavoriteSourceRecord,
    evaluate_favorite_at,
    favorite_observed_at,
    rebuild_favorite_relationship_current,
    select_favorite_attribution_candidate,
)
from .dts_v2_course_domain_projector import _read_claim_trigger_evidence
from .dts_v2_dirty_queue_store import DirtyClaimV2
from .dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from .dts_v2_source_repository import (
    DtsV2CurrentSourceRow,
    DtsV2SourceRepository,
)
from .dts_v2_teacher_domain_projector import (
    publish_regional_teacher_aggregate_v2,
)


_REGIONS = frozenset({"dom", "ovs"})
_ID_TYPES = frozenset({"NUMERIC", "TEXT"})
_RELATIONSHIP_OPERATIONS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "SNAPSHOT_INSERT",
        "SNAPSHOT_UPDATE",
        "SNAPSHOT_DELETE",
        "SNAPSHOT_BOOTSTRAP_PRESENT",
        "SNAPSHOT_BOOTSTRAP_TOMBSTONE",
    }
)
_INSERT_OPERATIONS = frozenset(
    {"INSERT", "SNAPSHOT_INSERT", "SNAPSHOT_BOOTSTRAP_PRESENT"}
)
_UPDATE_OPERATIONS = frozenset({"UPDATE", "SNAPSHOT_UPDATE"})
_DELETE_OPERATIONS = frozenset(
    {"DELETE", "SNAPSHOT_DELETE", "SNAPSHOT_BOOTSTRAP_TOMBSTONE"}
)
_DOM_STUDENT_TOKEN = re.compile(r"^dom:v1:[0-9a-f]{64}$")
_DOMAIN_RULE_VERSION = "dts-teacher-student-domain-v1"


class DtsV2TeacherStudentDomainProjectorError(RuntimeError):
    """A relationship dirty input cannot be projected without guessing."""


@dataclass(frozen=True, order=True)
class _Pair:
    source_region: str
    teacher_id_type: str
    teacher_id: str
    student_token: str

    def __post_init__(self) -> None:
        if self.source_region not in _REGIONS:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_REGION_INVALID"
            )
        teacher_id = _canonical_typed_id(self.teacher_id, self.teacher_id_type)
        if not isinstance(self.student_token, str) or not self.student_token:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_TOKEN_INVALID"
            )
        if self.source_region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(
            self.student_token
        ) is None:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_DOM_TOKEN_INVALID"
            )
        object.__setattr__(self, "teacher_id", teacher_id)


@dataclass(frozen=True)
class _RelationshipVersion:
    source_region: str
    source_partition_epoch_id: str
    topic: str
    partition_id: int
    offset_value: int
    source_table: str
    source_record_id: str
    source_record_id_type: str
    source_row_revision: int
    operation: str
    before_row: Mapping[str, Any] | None
    after_row: Mapping[str, Any] | None
    source_field_types: Mapping[str, str]
    source_timestamp: datetime | None


@dataclass(frozen=True)
class _RelationshipEventPlan:
    version: _RelationshipVersion
    relationship_type: str
    old_pair: _Pair | None
    new_pair: _Pair | None
    old_valid_start_at: datetime | None
    old_valid_end_at: datetime | None
    new_valid_start_at: datetime | None
    new_valid_end_at: datetime | None
    old_is_valid_forever: bool | None
    new_is_valid_forever: bool | None
    effective_at: datetime | None
    effective_time_evidence_status: str


@dataclass(frozen=True)
class _ScopeEvidence:
    source_table: str
    scope_kind: str
    scope_level: str | None
    scope_key: str | None
    state: str
    row_version: int | None
    active_snapshot_id: str | None
    active_fence_hash: str | None
    history_from: datetime | None
    history_through: datetime | None

    @property
    def complete(self) -> bool:
        return self.state == "COMPLETE"


@dataclass(frozen=True)
class _CurrentRelationship:
    is_favorited: bool | None
    favorite_evidence_status: str
    is_blocked: bool | None
    block_evidence_status: str
    blacklist_threshold_state: str
    blacklist_threshold_evidence_status: str
    blacklist_source_collection_complete: bool
    blacklist_distinct_active_student_count: int
    blacklist_source_missing_student_count: int
    blacklist_active_student_token_set_hash: str
    blacklist_source_missing_student_token_set_hash: str


@dataclass(frozen=True)
class _FavoriteCourse:
    source_appoint_id: str
    appoint_id_type: str | None
    completion_participation_seq: int
    completion_teacher_id: str
    completion_teacher_id_type: str
    completion_end_time: datetime | None
    completion_source_revision: int | None
    completion_conflict_status: str
    course_evidence_status: str
    course_row_version: int


@dataclass(frozen=True)
class _ObservationRequirement:
    course: _FavoriteCourse
    observed_at: datetime | None
    observation_due: bool | None
    status: str
    relation_state: bool | None
    relation_evidence_status: str
    relation_error_code: str | None
    existing_observation_revision: int | None
    existing_observation_status: str | None
    requires_materialization: bool


class DtsV2TeacherStudentDomainProjector:
    """Process one TEACHER_STUDENT dirty key in the caller transaction."""

    def __init__(
        self,
        *,
        cutover_coverage_identity: Mapping[str, Any],
        source_repository: DtsV2SourceRepository | None = None,
        revision_store: DtsV2DomainRevisionStore | None = None,
    ) -> None:
        if not isinstance(cutover_coverage_identity, Mapping) or not (
            cutover_coverage_identity
        ):
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_COVERAGE_IDENTITY_REQUIRED"
            )
        if "trigger" in cutover_coverage_identity:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_TRIGGER_COVERAGE_RESERVED"
            )
        self.coverage_identity = dict(cutover_coverage_identity)
        self.sources = source_repository or DtsV2SourceRepository()
        self.revisions = revision_store or DtsV2DomainRevisionStore()

    def process_claim(
        self,
        connection: Any,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        if claim.key.key_type != "TEACHER_STUDENT":
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_DIRTY_KEY_REQUIRED"
            )
        region = claim.key.source_region
        teacher_id = claim.key.key_part_1
        student_token = claim.key.key_part_2
        _validate_dirty_identity(region, teacher_id, student_token)

        trigger = _read_claim_trigger_evidence(
            connection,
            claim,
            base_coverage_identity=self.coverage_identity,
        )
        source_identities = _read_claim_relationship_source_identities(
            connection,
            claim,
        )
        source_identities.update(
            _read_existing_event_source_identities(
                connection,
                source_region=region,
                teacher_id=teacher_id,
                student_token=student_token,
            )
        )
        initial_rows = self.sources.read_for_dependency(
            connection,
            source_region=region,
            source_tables=_relationship_tables(region),
            dependency_kind="teacher_ids",
            dependency_value=teacher_id,
        )
        for row in initial_rows:
            if _source_row_pair(row) == (teacher_id, student_token):
                source_identities.add((row.source_table, row.source_key))

        versions = _read_relationship_versions(
            connection,
            source_region=region,
            source_identities=source_identities,
        )
        plans = tuple(_relationship_event_plan(row) for row in versions)
        affected_pairs: set[_Pair] = set()
        teacher_types: set[str] = set()
        inserted_event_count = 0
        for plan in plans:
            inserted_event_count += int(_insert_relationship_event(connection, plan))
            for pair in (plan.old_pair, plan.new_pair):
                if pair is not None:
                    affected_pairs.add(pair)
                    if (
                        pair.teacher_id == teacher_id
                        and pair.student_token == student_token
                    ):
                        teacher_types.add(pair.teacher_id_type)

        teacher_types.update(
            _read_pair_teacher_types(
                connection,
                source_region=region,
                teacher_id=teacher_id,
                student_token=student_token,
            )
        )
        if len(teacher_types) != 1:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_TEACHER_TYPE_REQUIRED"
                if not teacher_types
                else "DTS_V2_TEACHER_STUDENT_TEACHER_TYPE_CONFLICT"
            )
        claimed_pair = _Pair(
            region,
            next(iter(teacher_types)),
            teacher_id,
            student_token,
        )
        affected_pairs.add(claimed_pair)

        as_of = _transaction_timestamp(connection)
        counts = {
            "relationship_event_changes": inserted_event_count,
            "relationship_current_changes": 0,
            "observation_requirements": 0,
            "attribution_intents": 0,
            "aggregate_events": 0,
            "teacher_aggregate_events": 0,
        }
        for pair in sorted(affected_pairs):
            scopes = _read_preferred_scopes(connection, pair=pair)
            current_rows = self._read_pair_current_rows(
                connection,
                pair=pair,
            )
            current = _evaluate_current_relationship(
                pair=pair,
                rows=current_rows,
                scopes=scopes,
                business_as_of=as_of,
            )
            latest_event = _read_latest_pair_event(connection, pair=pair)
            if latest_event is None:
                if pair == claimed_pair:
                    raise DtsV2TeacherStudentDomainProjectorError(
                        "DTS_V2_TEACHER_STUDENT_EVENT_REQUIRED"
                    )
                continue
            if _upsert_relationship_current(
                connection,
                pair=pair,
                current=current,
                latest_event=latest_event,
            ):
                counts["relationship_current_changes"] += 1

            state, requirement_count, has_intent = _build_aggregate_state(
                connection,
                pair=pair,
                current=current,
                scopes=scopes,
                business_as_of=as_of,
            )
            result = self.revisions.publish_change(
                connection,
                aggregate_type="TEACHER_STUDENT",
                aggregate_key={
                    "source_region": pair.source_region,
                    "teacher_id": pair.teacher_id,
                    "student_token": pair.student_token,
                },
                aggregate_state=state,
                changed_fields=(
                    "relationship_current",
                    "relationship_evidence",
                    "blacklist_threshold",
                    "favorite_observation_requirements",
                    "favorite_attribution_intent",
                    "scope_evidence",
                ),
                source_row_revision=trigger.source_row_revision,
                source_position=trigger.source_position,
                rule_version=_DOMAIN_RULE_VERSION,
                cutover_coverage_identity=trigger.coverage_identity,
            )
            counts["observation_requirements"] += requirement_count
            counts["attribution_intents"] += 1 if has_intent else 0
            counts["aggregate_events"] += 1 if result.status == "CHANGED" else 0

        # Relationship facts are course-independent: even a pair with no
        # completed lesson contributes to the teacher's distinct favorite /
        # block counts.  Publish each real old/new teacher after current facts
        # are rebuilt; never construct a teacher x student cross product.
        for affected_region, affected_teacher in sorted(
            {(pair.source_region, pair.teacher_id) for pair in affected_pairs}
        ):
            teacher_result = publish_regional_teacher_aggregate_v2(
                connection,
                source_region=affected_region,
                teacher_id=affected_teacher,
                source_row_revision=trigger.source_row_revision,
                source_position=trigger.source_position,
                cutover_coverage_identity=trigger.coverage_identity,
                source_repository=self.sources,
                revision_store=self.revisions,
            )
            changed = int(teacher_result.status == "CHANGED")
            counts["teacher_aggregate_events"] += changed
            counts["aggregate_events"] += changed
        return counts

    def _read_pair_current_rows(
        self,
        connection: Any,
        *,
        pair: _Pair,
    ) -> tuple[DtsV2CurrentSourceRow, ...]:
        rows = {
            (row.source_table, row.source_key): row
            for row in self.sources.read_for_dependency(
                connection,
                source_region=pair.source_region,
                source_tables=_relationship_tables(pair.source_region),
                dependency_kind="teacher_ids",
                dependency_value=pair.teacher_id,
            )
        }
        identities = _read_existing_event_source_identities(
            connection,
            source_region=pair.source_region,
            teacher_id=pair.teacher_id,
            student_token=pair.student_token,
        )
        by_table: dict[str, list[str]] = {}
        for table, source_key in identities:
            by_table.setdefault(table, []).append(source_key)
        for table, source_keys in by_table.items():
            for row in self.sources.read_by_source_keys(
                connection,
                source_region=pair.source_region,
                source_table=table,
                source_keys=tuple(source_keys),
            ):
                rows[(row.source_table, row.source_key)] = row
        return tuple(
            row
            for _, row in sorted(
                rows.items(),
                key=lambda item: (
                    item[0][0].encode("utf-8"),
                    _typed_sort_key(item[1].source_key_type, item[1].source_key),
                ),
            )
            if _required_source_row_pair(row)[0]
            == (pair.teacher_id, pair.teacher_id_type)
        )


def _validate_dirty_identity(
    source_region: str,
    teacher_id: str,
    student_token: str,
) -> None:
    if source_region not in _REGIONS or not teacher_id or not student_token:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_IDENTITY_INVALID"
        )
    if source_region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(
        student_token
    ) is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_DOM_TOKEN_INVALID"
        )


def _relationship_tables(source_region: str) -> tuple[str, str]:
    return (
        f"{source_region}_teacher_favorite",
        f"{source_region}_teacher_blacklist",
    )


def _read_claim_relationship_source_identities(
    connection: Any,
    claim: DirtyClaimV2,
) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            """
            SELECT input.input_identity
            FROM public.dts_dirty_key_inputs AS input
            JOIN public.dts_dirty_keys AS dirty
              ON dirty.source_region=input.source_region
             AND dirty.key_type=input.key_type
             AND dirty.key_part_1=input.key_part_1
             AND dirty.key_part_2=input.key_part_2
            WHERE input.source_region=:source_region
              AND input.key_type=:key_type
              AND input.key_part_1=:key_part_1
              AND input.key_part_2=:key_part_2
              AND input.dirty_work_revision>dirty.completed_work_revision
              AND input.dirty_work_revision<=:claimed_work_revision
              AND input.input_kind='SOURCE_REVISION'
              AND dirty.status='PROCESSING'
              AND dirty.lease_token=:lease_token
              AND dirty.claimed_through_work_revision=:claimed_work_revision
            ORDER BY input.dirty_work_revision
            """
        ),
        {
            "source_region": claim.key.source_region,
            "key_type": claim.key.key_type,
            "key_part_1": claim.key.key_part_1,
            "key_part_2": claim.key.key_part_2,
            "lease_token": claim.lease_token,
            "claimed_work_revision": claim.claimed_work_revision,
        },
    ).mappings()
    result: set[tuple[str, str]] = set()
    allowed = set(_relationship_tables(claim.key.source_region))
    for row in rows:
        identity = row.get("input_identity")
        if not isinstance(identity, Mapping):
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_INPUT_IDENTITY_INVALID"
            )
        table = identity.get("source_table")
        key = identity.get("source_key")
        if table in allowed:
            if not isinstance(key, str) or not key:
                raise DtsV2TeacherStudentDomainProjectorError(
                    "DTS_V2_TEACHER_STUDENT_INPUT_IDENTITY_INVALID"
                )
            result.add((table, key))
    return result


def _read_existing_event_source_identities(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
    student_token: str,
) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT source_table,source_record_id
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND (
                (old_teacher_id=:teacher_id
                 AND old_student_token=:student_token)
                OR
                (new_teacher_id=:teacher_id
                 AND new_student_token=:student_token)
              )
            """
        ),
        {
            "source_region": source_region,
            "teacher_id": teacher_id,
            "student_token": student_token,
        },
    ).mappings()
    return {
        (str(row["source_table"]), str(row["source_record_id"])) for row in rows
    }


def _read_relationship_versions(
    connection: Any,
    *,
    source_region: str,
    source_identities: set[tuple[str, str]],
) -> tuple[_RelationshipVersion, ...]:
    if not source_identities:
        return ()
    identity_values = [
        {"source_table": table, "source_key": key}
        for table, key in sorted(source_identities)
    ]
    rows = connection.execute(
        text(
            """
            SELECT version.source_region,version.source_partition_epoch_id,
                   version.topic,version.partition_id,version.offset_value,
                   version.source_table,version.source_key,
                   version.source_key_type,version.source_row_revision,
                   version.operation,version.before_row,version.after_row,
                   version.source_field_types,version.source_timestamp
            FROM public.dts_source_row_versions AS version
            WHERE version.source_region=:source_region
              AND version.source_row_revision IS NOT NULL
              AND version.operation=ANY(CAST(:operations AS text[]))
              AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(CAST(:identities AS jsonb))
                         AS identity
                    WHERE identity->>'source_table'=version.source_table
                      AND identity->>'source_key'=version.source_key
                  )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.teacher_student_relationship_events AS event
                    WHERE event.source_region=version.source_region
                      AND event.source_partition_epoch_id=
                          version.source_partition_epoch_id
                      AND event.topic=version.topic
                      AND event.partition_id=version.partition_id
                      AND event.offset_value=version.offset_value
                  )
            ORDER BY convert_to(version.source_table,'UTF8'),
                     convert_to(version.source_key_type,'UTF8'),
                     version.source_key_numeric NULLS LAST,
                     convert_to(COALESCE(version.source_key_text,''),'UTF8'),
                     version.source_row_revision,version.topic,
                     version.partition_id,version.offset_value
            """
        ),
        {
            "source_region": source_region,
            "operations": sorted(_RELATIONSHIP_OPERATIONS),
            "identities": _json_dump(identity_values),
        },
    ).mappings()
    return tuple(_relationship_version(row) for row in rows)


def _relationship_version(row: Mapping[str, Any]) -> _RelationshipVersion:
    region = row.get("source_region")
    table = row.get("source_table")
    operation = row.get("operation")
    revision = row.get("source_row_revision")
    if (
        region not in _REGIONS
        or table not in _relationship_tables(str(region))
        or operation not in _RELATIONSHIP_OPERATIONS
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(row.get("source_field_types"), Mapping)
    ):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_VERSION_INVALID"
        )
    before = row.get("before_row")
    after = row.get("after_row")
    if before is not None and not isinstance(before, Mapping):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_VERSION_INVALID"
        )
    if after is not None and not isinstance(after, Mapping):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_VERSION_INVALID"
        )
    return _RelationshipVersion(
        source_region=str(region),
        source_partition_epoch_id=str(row["source_partition_epoch_id"]),
        topic=str(row["topic"]),
        partition_id=int(row["partition_id"]),
        offset_value=int(row["offset_value"]),
        source_table=str(table),
        source_record_id=_canonical_typed_id(
            row["source_key"], str(row["source_key_type"])
        ),
        source_record_id_type=str(row["source_key_type"]),
        source_row_revision=int(revision),
        operation=str(operation),
        before_row=before,
        after_row=after,
        source_field_types=dict(row["source_field_types"]),
        source_timestamp=_aware_datetime_or_none(row.get("source_timestamp")),
    )


def _relationship_event_plan(
    version: _RelationshipVersion,
) -> _RelationshipEventPlan:
    is_favorite = version.source_table.endswith("_teacher_favorite")
    relation_type = "FAVORITE" if is_favorite else "BLOCK"
    old_pair = (
        _pair_from_image(version, version.before_row)
        if version.before_row is not None
        else None
    )
    new_pair = (
        _pair_from_image(version, version.after_row)
        if version.after_row is not None
        else None
    )
    if (
        version.operation in _INSERT_OPERATIONS
        and (old_pair is not None or new_pair is None)
    ) or (
        version.operation in _UPDATE_OPERATIONS
        and (old_pair is None or new_pair is None)
    ) or (
        version.operation in _DELETE_OPERATIONS
        and (old_pair is None or new_pair is not None)
    ):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_IMAGES_INVALID"
        )

    old_start = _relationship_start(version, version.before_row)
    new_start = _relationship_start(version, version.after_row)
    if is_favorite:
        old_end = (
            version.source_timestamp
            if version.operation in _DELETE_OPERATIONS
            else None
        )
        new_end = None
        old_forever = new_forever = None
    else:
        old_end = _optional_datetime(version, version.before_row, "valid_end_time")
        new_end = _optional_datetime(version, version.after_row, "valid_end_time")
        old_forever = _optional_boolean(
            version, version.before_row, "is_valid_forever"
        )
        new_forever = _optional_boolean(
            version, version.after_row, "is_valid_forever"
        )
        if version.operation in _DELETE_OPERATIONS:
            old_end = version.source_timestamp

    effective_at = (
        version.source_timestamp
        if version.operation in _DELETE_OPERATIONS
        else new_start
    )
    return _RelationshipEventPlan(
        version=version,
        relationship_type=relation_type,
        old_pair=old_pair,
        new_pair=new_pair,
        old_valid_start_at=old_start,
        old_valid_end_at=old_end,
        new_valid_start_at=new_start,
        new_valid_end_at=new_end,
        old_is_valid_forever=old_forever,
        new_is_valid_forever=new_forever,
        effective_at=effective_at,
        effective_time_evidence_status=(
            "CONFIRMED" if effective_at is not None else "SOURCE_MISSING"
        ),
    )


def _pair_from_image(
    version: _RelationshipVersion,
    image: Mapping[str, Any],
) -> _Pair:
    teacher = _first_typed_id(
        image,
        version.source_field_types,
        ("t_id", "tea_id", "teacher_id"),
    )
    if teacher is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_TEACHER_REQUIRED"
        )
    student = _student_subject(
        version.source_region,
        image,
        version.source_field_types,
    )
    return _Pair(
        version.source_region,
        teacher[1],
        teacher[0],
        student,
    )


def _relationship_start(
    version: _RelationshipVersion,
    image: Mapping[str, Any] | None,
) -> datetime | None:
    if image is None:
        return None
    if version.source_table.endswith("_teacher_favorite"):
        return _optional_datetime(version, image, "add_time")
    return (
        _optional_datetime(version, image, "valid_start_time")
        or _optional_datetime(version, image, "add_time")
        or version.source_timestamp
    )


def _insert_relationship_event(
    connection: Any,
    plan: _RelationshipEventPlan,
) -> bool:
    version = plan.version
    parameters = {
        "source_region": version.source_region,
        "epoch_id": version.source_partition_epoch_id,
        "topic": version.topic,
        "partition_id": version.partition_id,
        "offset_value": version.offset_value,
        "source_table": version.source_table,
        "source_record_id": version.source_record_id,
        "source_record_id_type": version.source_record_id_type,
        "source_record_id_numeric": (
            Decimal(version.source_record_id)
            if version.source_record_id_type == "NUMERIC"
            else None
        ),
        "source_record_id_text": (
            version.source_record_id
            if version.source_record_id_type == "TEXT"
            else None
        ),
        "source_row_revision": version.source_row_revision,
        "relationship_type": plan.relationship_type,
        "operation": version.operation,
        "old_teacher_id": plan.old_pair.teacher_id if plan.old_pair else None,
        "old_teacher_id_type": (
            plan.old_pair.teacher_id_type if plan.old_pair else None
        ),
        "old_student_token": (
            plan.old_pair.student_token if plan.old_pair else None
        ),
        "new_teacher_id": plan.new_pair.teacher_id if plan.new_pair else None,
        "new_teacher_id_type": (
            plan.new_pair.teacher_id_type if plan.new_pair else None
        ),
        "new_student_token": (
            plan.new_pair.student_token if plan.new_pair else None
        ),
        "old_valid_start_at": plan.old_valid_start_at,
        "old_valid_end_at": plan.old_valid_end_at,
        "new_valid_start_at": plan.new_valid_start_at,
        "new_valid_end_at": plan.new_valid_end_at,
        "old_is_valid_forever": plan.old_is_valid_forever,
        "new_is_valid_forever": plan.new_is_valid_forever,
        "effective_at": plan.effective_at,
        "effective_status": plan.effective_time_evidence_status,
        "source_timestamp": version.source_timestamp,
    }
    inserted = connection.execute(
        text(
            """
            INSERT INTO public.teacher_student_relationship_events (
                source_region,source_partition_epoch_id,topic,partition_id,
                offset_value,source_table,source_record_id,
                source_record_id_type,source_record_id_numeric,
                source_record_id_text,source_row_revision,relationship_type,
                operation,old_teacher_id,old_teacher_id_type,
                old_student_token,new_teacher_id,new_teacher_id_type,
                new_student_token,old_valid_start_at,old_valid_end_at,
                new_valid_start_at,new_valid_end_at,old_is_valid_forever,
                new_is_valid_forever,effective_at,
                effective_time_evidence_status,source_timestamp
            ) VALUES (
                :source_region,:epoch_id,:topic,:partition_id,:offset_value,
                :source_table,:source_record_id,:source_record_id_type,
                :source_record_id_numeric,:source_record_id_text,
                :source_row_revision,:relationship_type,:operation,
                :old_teacher_id,:old_teacher_id_type,:old_student_token,
                :new_teacher_id,:new_teacher_id_type,:new_student_token,
                :old_valid_start_at,:old_valid_end_at,:new_valid_start_at,
                :new_valid_end_at,:old_is_valid_forever,
                :new_is_valid_forever,:effective_at,:effective_status,
                :source_timestamp
            ) ON CONFLICT (
                source_region,source_partition_epoch_id,topic,partition_id,
                offset_value
            ) DO NOTHING
            RETURNING event_sequence
            """
        ),
        parameters,
    ).scalar_one_or_none()
    if inserted is not None:
        return True
    existing = connection.execute(
        text(
            """
            SELECT source_table,source_record_id,source_record_id_type,
                   source_row_revision,relationship_type,operation,
                   old_teacher_id,old_teacher_id_type,old_student_token,
                   new_teacher_id,new_teacher_id_type,new_student_token,
                   effective_at,effective_time_evidence_status
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND source_partition_epoch_id=:epoch_id
              AND topic=:topic AND partition_id=:partition_id
              AND offset_value=:offset_value
            """
        ),
        parameters,
    ).mappings().one_or_none()
    if existing is None or tuple(existing.get(field) for field in (
        "source_table",
        "source_record_id",
        "source_record_id_type",
        "source_row_revision",
        "relationship_type",
        "operation",
        "old_teacher_id",
        "old_teacher_id_type",
        "old_student_token",
        "new_teacher_id",
        "new_teacher_id_type",
        "new_student_token",
        "effective_at",
        "effective_time_evidence_status",
    )) != tuple(parameters[field] for field in (
        "source_table",
        "source_record_id",
        "source_record_id_type",
        "source_row_revision",
        "relationship_type",
        "operation",
        "old_teacher_id",
        "old_teacher_id_type",
        "old_student_token",
        "new_teacher_id",
        "new_teacher_id_type",
        "new_student_token",
        "effective_at",
        "effective_status",
    )):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_EVENT_REPLAY_CONFLICT"
        )
    return False


def _read_pair_teacher_types(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
    student_token: str,
) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT teacher_id_type
            FROM public.teacher_student_relationship_current
            WHERE source_region=:source_region
              AND teacher_id=:teacher_id AND student_token=:student_token
            UNION
            SELECT old_teacher_id_type
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND old_teacher_id=:teacher_id
              AND old_student_token=:student_token
            UNION
            SELECT new_teacher_id_type
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND new_teacher_id=:teacher_id
              AND new_student_token=:student_token
            UNION
            SELECT completion_teacher_id_type
            FROM public.source_courses
            WHERE source_region=:source_region
              AND completion_teacher_id=:teacher_id
              AND completion_student_token=:student_token
            """
        ),
        {
            "source_region": source_region,
            "teacher_id": teacher_id,
            "student_token": student_token,
        },
    ).scalars()
    return {str(value) for value in rows if value is not None}


def _read_preferred_scopes(
    connection: Any,
    *,
    pair: _Pair,
) -> dict[tuple[str, str], _ScopeEvidence]:
    tables = _relationship_tables(pair.source_region)
    rows = connection.execute(
        text(
            """
            SELECT state.source_table,state.scope_kind,state.scope_level,
                   state.scope_key,state.state,state.row_version,
                   state.active_snapshot_id,snapshot.snapshot_fence_hash,
                   snapshot.history_from,snapshot.history_through
            FROM public.dts_source_scope_states state
            LEFT JOIN public.dts_source_scope_snapshots snapshot
              ON snapshot.snapshot_id=state.active_snapshot_id
             AND snapshot.source_region=state.source_region
             AND snapshot.source_table=state.source_table
             AND snapshot.scope_kind=state.scope_kind
             AND snapshot.scope_level=state.scope_level
             AND snapshot.scope_key=state.scope_key
            WHERE state.source_region=:source_region
              AND state.source_table=ANY(CAST(:source_tables AS text[]))
              AND state.scope_kind IN ('CURRENT','HISTORY')
              AND ((state.scope_level='TEACHER'
                    AND state.scope_key=:teacher_id)
                   OR (state.scope_level='GLOBAL' AND state.scope_key='*'))
            ORDER BY state.source_table,state.scope_kind,
                     CASE state.scope_level WHEN 'TEACHER' THEN 0 ELSE 1 END
            """
        ),
        {
            "source_region": pair.source_region,
            "source_tables": list(tables),
            "teacher_id": pair.teacher_id,
        },
    ).mappings()
    result: dict[tuple[str, str], _ScopeEvidence] = {}
    for row in rows:
        key = (str(row["source_table"]), str(row["scope_kind"]))
        if key in result:
            continue
        result[key] = _ScopeEvidence(
            source_table=key[0],
            scope_kind=key[1],
            scope_level=str(row["scope_level"]),
            scope_key=str(row["scope_key"]),
            state=str(row["state"]),
            row_version=int(row["row_version"]),
            active_snapshot_id=row.get("active_snapshot_id"),
            active_fence_hash=row.get("snapshot_fence_hash"),
            history_from=_aware_datetime_or_none(row.get("history_from")),
            history_through=_aware_datetime_or_none(row.get("history_through")),
        )
    for table in tables:
        for kind in ("CURRENT", "HISTORY"):
            result.setdefault(
                (table, kind),
                _ScopeEvidence(
                    table,
                    kind,
                    None,
                    None,
                    "UNKNOWN",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
    return result


def _evaluate_current_relationship(
    *,
    pair: _Pair,
    rows: Sequence[DtsV2CurrentSourceRow],
    scopes: Mapping[tuple[str, str], _ScopeEvidence],
    business_as_of: datetime,
) -> _CurrentRelationship:
    favorite_table, blacklist_table = _relationship_tables(pair.source_region)
    favorite_rows = tuple(
        row
        for row in rows
        if row.source_table == favorite_table
        and _required_source_row_pair(row)
        == ((pair.teacher_id, pair.teacher_id_type), pair.student_token)
    )
    blacklist_rows = tuple(row for row in rows if row.source_table == blacklist_table)
    favorite = rebuild_favorite_relationship_current(
        tuple(_favorite_source_record(row) for row in favorite_rows),
        scope_complete=scopes[(favorite_table, "CURRENT")].complete,
    )

    blacklist = rebuild_blacklist_threshold_v2(
        source_region=pair.source_region,
        teacher_id=TypedBlacklistIdV2(pair.teacher_id_type, pair.teacher_id),
        source_records=tuple(_blacklist_source_record(row) for row in blacklist_rows),
        business_as_of=business_as_of,
        source_collection_complete=scopes[(blacklist_table, "CURRENT")].complete,
    )
    relation = next(
        (
            value
            for value in blacklist.student_relations
            if value.student_token == pair.student_token
        ),
        None,
    )
    if relation is not None and relation.relation_state == RELATION_BLOCKED:
        is_blocked: bool | None = True
    elif (
        relation is not None
        and relation.relation_state == RELATION_SOURCE_MISSING
    ):
        is_blocked = None
    elif scopes[(blacklist_table, "CURRENT")].complete:
        is_blocked = False
    else:
        is_blocked = None
    return _CurrentRelationship(
        is_favorited=favorite.is_favorited,
        favorite_evidence_status=favorite.effective_time_evidence_status,
        is_blocked=is_blocked,
        block_evidence_status=(
            "CONFIRMED" if is_blocked is not None else "SOURCE_MISSING"
        ),
        blacklist_threshold_state=blacklist.threshold_state,
        blacklist_threshold_evidence_status=blacklist.evidence_status,
        blacklist_source_collection_complete=(
            blacklist.source_collection_complete
        ),
        blacklist_distinct_active_student_count=(
            blacklist.distinct_active_student_count
        ),
        blacklist_source_missing_student_count=len(
            blacklist.source_missing_student_tokens
        ),
        blacklist_active_student_token_set_hash=_student_token_set_hash(
            blacklist.active_student_tokens
        ),
        blacklist_source_missing_student_token_set_hash=(
            _student_token_set_hash(blacklist.source_missing_student_tokens)
        ),
    )


def _favorite_source_record(row: DtsV2CurrentSourceRow) -> FavoriteSourceRecord:
    pair = _required_source_row_pair(row)
    return FavoriteSourceRecord(
        source_id=row.source_key,
        source_id_type=row.source_key_type,
        teacher_id=pair[0][0],
        teacher_id_type=pair[0][1],
        student_token=pair[1],
        add_time=_row_optional_datetime(row, "add_time"),
        source_timestamp=_source_position_timestamp(row.source_position),
        is_deleted=row.is_deleted,
    )


def _blacklist_source_record(
    row: DtsV2CurrentSourceRow,
) -> BlacklistSourceRecordV2:
    pair = _required_source_row_pair(row)
    return BlacklistSourceRecordV2(
        source_region=row.source_region,
        source_id=TypedBlacklistIdV2(row.source_key_type, row.source_key),
        teacher_id=TypedBlacklistIdV2(pair[0][1], pair[0][0]),
        student_token=pair[1],
        is_deleted=row.is_deleted,
        valid_start_time=_row_optional_datetime(row, "valid_start_time"),
        add_time=_row_optional_datetime(row, "add_time"),
        source_timestamp=_source_position_timestamp(row.source_position),
        valid_end_time=_row_optional_datetime(row, "valid_end_time"),
        is_valid_forever=_row_optional_boolean(row, "is_valid_forever"),
    )


def _read_latest_pair_event(
    connection: Any,
    *,
    pair: _Pair,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT event_sequence,source_partition_epoch_id,topic,
                   partition_id,offset_value,source_row_revision,effective_at,
                   effective_time_evidence_status
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND ((old_teacher_id_type=:teacher_id_type
                    AND old_teacher_id=:teacher_id
                    AND old_student_token=:student_token)
                   OR (new_teacher_id_type=:teacher_id_type
                    AND new_teacher_id=:teacher_id
                    AND new_student_token=:student_token))
            ORDER BY event_sequence DESC
            LIMIT 1
            """
        ),
        _pair_params(pair),
    ).mappings().one_or_none()


def _upsert_relationship_current(
    connection: Any,
    *,
    pair: _Pair,
    current: _CurrentRelationship,
    latest_event: Mapping[str, Any],
) -> bool:
    changed = connection.execute(
        text(
            """
            INSERT INTO public.teacher_student_relationship_current (
                source_region,teacher_id,teacher_id_type,student_token,
                is_favorited,is_blocked,last_business_effective_at,
                effective_time_evidence_status,last_event_sequence,
                last_source_partition_epoch_id,last_topic,last_partition_id,
                last_offset_value,last_source_row_revision
            ) VALUES (
                :source_region,:teacher_id,:teacher_id_type,:student_token,
                :is_favorited,:is_blocked,:effective_at,:effective_status,
                :event_sequence,:epoch_id,:topic,:partition_id,:offset_value,
                :source_row_revision
            ) ON CONFLICT (source_region,teacher_id,student_token)
            DO UPDATE SET
                teacher_id_type=EXCLUDED.teacher_id_type,
                is_favorited=EXCLUDED.is_favorited,
                is_blocked=EXCLUDED.is_blocked,
                last_business_effective_at=EXCLUDED.last_business_effective_at,
                effective_time_evidence_status=
                    EXCLUDED.effective_time_evidence_status,
                last_event_sequence=EXCLUDED.last_event_sequence,
                last_source_partition_epoch_id=
                    EXCLUDED.last_source_partition_epoch_id,
                last_topic=EXCLUDED.last_topic,
                last_partition_id=EXCLUDED.last_partition_id,
                last_offset_value=EXCLUDED.last_offset_value,
                last_source_row_revision=EXCLUDED.last_source_row_revision,
                row_version=
                    teacher_student_relationship_current.row_version+1,
                updated_at=clock_timestamp()
            WHERE ROW(
                teacher_student_relationship_current.teacher_id_type,
                teacher_student_relationship_current.is_favorited,
                teacher_student_relationship_current.is_blocked,
                teacher_student_relationship_current.last_business_effective_at,
                teacher_student_relationship_current.effective_time_evidence_status,
                teacher_student_relationship_current.last_event_sequence,
                teacher_student_relationship_current.last_source_partition_epoch_id,
                teacher_student_relationship_current.last_topic,
                teacher_student_relationship_current.last_partition_id,
                teacher_student_relationship_current.last_offset_value,
                teacher_student_relationship_current.last_source_row_revision
            ) IS DISTINCT FROM ROW(
                EXCLUDED.teacher_id_type,EXCLUDED.is_favorited,
                EXCLUDED.is_blocked,EXCLUDED.last_business_effective_at,
                EXCLUDED.effective_time_evidence_status,
                EXCLUDED.last_event_sequence,
                EXCLUDED.last_source_partition_epoch_id,EXCLUDED.last_topic,
                EXCLUDED.last_partition_id,EXCLUDED.last_offset_value,
                EXCLUDED.last_source_row_revision
            )
            RETURNING row_version
            """
        ),
        {
            **_pair_params(pair),
            "is_favorited": current.is_favorited,
            "is_blocked": current.is_blocked,
            "effective_at": latest_event.get("effective_at"),
            "effective_status": latest_event.get(
                "effective_time_evidence_status"
            ),
            "event_sequence": latest_event.get("event_sequence"),
            "epoch_id": latest_event.get("source_partition_epoch_id"),
            "topic": latest_event.get("topic"),
            "partition_id": latest_event.get("partition_id"),
            "offset_value": latest_event.get("offset_value"),
            "source_row_revision": latest_event.get("source_row_revision"),
        },
    ).scalar_one_or_none()
    return changed is not None


def _build_aggregate_state(
    connection: Any,
    *,
    pair: _Pair,
    current: _CurrentRelationship,
    scopes: Mapping[tuple[str, str], _ScopeEvidence],
    business_as_of: datetime,
) -> tuple[dict[str, Any], int, bool]:
    current_row = connection.execute(
        text(
            """
            SELECT is_favorited,is_blocked,last_business_effective_at,
                   effective_time_evidence_status,row_version,
                   last_event_sequence,last_source_partition_epoch_id,
                   last_topic,last_partition_id,last_offset_value,
                   last_source_row_revision
            FROM public.teacher_student_relationship_current
            WHERE source_region=:source_region
              AND teacher_id=:teacher_id AND student_token=:student_token
            FOR SHARE
            """
        ),
        _pair_params(pair),
    ).mappings().one()
    courses = _read_pair_courses(connection, pair=pair)
    existing_observations = _read_existing_observations(connection, pair=pair)
    attribution = _read_existing_attribution(connection, pair=pair)
    intervals = _read_favorite_intervals(connection, pair=pair)
    requirements = _build_observation_requirements(
        pair=pair,
        courses=courses,
        intervals=intervals,
        favorite_history_scope=scopes[
            (f"{pair.source_region}_teacher_favorite", "HISTORY")
        ],
        existing_observations=existing_observations,
        business_as_of=business_as_of,
    )
    attribution_intent = _build_attribution_intent(
        pair=pair,
        requirements=requirements,
        existing_attribution=attribution,
    )
    scope_state = [_scope_state_json(value) for value in sorted(
        scopes.values(), key=lambda item: (item.source_table, item.scope_kind)
    )]
    state = {
        "protocol_version": "teacher-student-domain-v1",
        "relationship_current": {
            "teacher_id_type": pair.teacher_id_type,
            "is_favorited": current.is_favorited,
            "favorite_evidence_status": current.favorite_evidence_status,
            "is_blocked": current.is_blocked,
            "block_evidence_status": current.block_evidence_status,
            "last_business_effective_at": _iso_or_none(
                current_row.get("last_business_effective_at")
            ),
            "latest_event_evidence_status": current_row.get(
                "effective_time_evidence_status"
            ),
        },
        "relationship_evidence": {
            "current_row_version": int(current_row["row_version"]),
            "last_event_sequence": int(current_row["last_event_sequence"]),
            "last_source_partition_epoch_id": current_row[
                "last_source_partition_epoch_id"
            ],
            "last_topic": current_row["last_topic"],
            "last_partition_id": int(current_row["last_partition_id"]),
            "last_offset_value": int(current_row["last_offset_value"]),
            "last_source_row_revision": int(
                current_row["last_source_row_revision"]
            ),
        },
        "blacklist_threshold": {
            "protocol_version": "blacklist-threshold-evidence-v1",
            "source_region": pair.source_region,
            "teacher_id": pair.teacher_id,
            "teacher_id_type": pair.teacher_id_type,
            "threshold": 2,
            "threshold_state": current.blacklist_threshold_state,
            "evidence_status": current.blacklist_threshold_evidence_status,
            "source_collection_complete": (
                current.blacklist_source_collection_complete
            ),
            "distinct_active_student_count": (
                current.blacklist_distinct_active_student_count
            ),
            "source_missing_student_count": (
                current.blacklist_source_missing_student_count
            ),
            "active_student_token_set_hash": (
                current.blacklist_active_student_token_set_hash
            ),
            "source_missing_student_token_set_hash": (
                current.blacklist_source_missing_student_token_set_hash
            ),
            "scope": _scope_state_json(
                scopes[(f"{pair.source_region}_teacher_blacklist", "CURRENT")]
            ),
        },
        "favorite_observation_requirements": [
            _observation_requirement_json(value) for value in requirements
        ],
        "favorite_attribution_intent": attribution_intent,
        "scope_evidence": scope_state,
    }
    return state, len(requirements), attribution_intent["action"] != "NONE"


def _read_pair_courses(
    connection: Any,
    *,
    pair: _Pair,
) -> tuple[_FavoriteCourse, ...]:
    rows = connection.execute(
        text(
            """
            SELECT course.source_appoint_id,appoint.source_key_type
                       AS appoint_id_type,
                   course.completion_participation_seq,
                   course.completion_teacher_id,
                   course.completion_teacher_id_type,
                   course.completion_end_time,course.completion_source_revision,
                   course.completion_conflict_status,
                   course.evidence_status,course.row_version
            FROM public.source_courses course
            LEFT JOIN public.dts_source_rows appoint
              ON appoint.source_region=course.source_region
             AND appoint.source_table=
                    course.source_region || '_appoint'
             AND appoint.source_key=course.source_appoint_id
             AND appoint.provenance_state='V2_CONFIRMED'
            WHERE course.source_region=:source_region
              AND course.completion_teacher_id=:teacher_id
              AND course.completion_teacher_id_type=:teacher_id_type
              AND course.completion_student_token=:student_token
              AND course.completion_participation_seq IS NOT NULL
              AND course.completion_voided_at IS NULL
            ORDER BY course.completion_end_time NULLS FIRST,
                     course.source_appoint_id
            FOR SHARE OF course
            """
        ),
        _pair_params(pair),
    ).mappings()
    return tuple(
        _FavoriteCourse(
            source_appoint_id=str(row["source_appoint_id"]),
            appoint_id_type=(
                str(row["appoint_id_type"])
                if row.get("appoint_id_type") in _ID_TYPES
                else None
            ),
            completion_participation_seq=int(
                row["completion_participation_seq"]
            ),
            completion_teacher_id=str(row["completion_teacher_id"]),
            completion_teacher_id_type=str(row["completion_teacher_id_type"]),
            completion_end_time=_aware_datetime_or_none(
                row.get("completion_end_time")
            ),
            completion_source_revision=(
                int(row["completion_source_revision"])
                if row.get("completion_source_revision") is not None
                else None
            ),
            completion_conflict_status=str(row["completion_conflict_status"]),
            course_evidence_status=str(row["evidence_status"]),
            course_row_version=int(row["row_version"]),
        )
        for row in rows
    )


def _read_existing_observations(
    connection: Any,
    *,
    pair: _Pair,
) -> dict[str, Mapping[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT source_appoint_id,observation_revision,status,observed_at,
                   relation_state,relation_evidence_status,
                   relation_error_code,completion_participation_seq,
                   teacher_id_type
            FROM public.course_favorite_observations
            WHERE source_region=:source_region
              AND teacher_id=:teacher_id
              AND teacher_id_type=:teacher_id_type
              AND student_token=:student_token
              AND status NOT IN ('INVALIDATED','VOIDED')
            ORDER BY source_appoint_id,observation_revision DESC
            """
        ),
        _pair_params(pair),
    ).mappings()
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        result.setdefault(str(row["source_appoint_id"]), row)
    return result


def _read_existing_attribution(
    connection: Any,
    *,
    pair: _Pair,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT source_appoint_id,observation_revision,
                   completion_participation_seq,status,hold_reason,
                   award_generation,row_version
            FROM public.course_favorite_attributions
            WHERE source_region=:source_region
              AND teacher_id=:teacher_id
              AND teacher_id_type=:teacher_id_type
              AND student_token=:student_token
            """
        ),
        _pair_params(pair),
    ).mappings().one_or_none()


def _read_favorite_intervals(
    connection: Any,
    *,
    pair: _Pair,
) -> tuple[FavoriteInterval, ...]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (
                source_table,source_record_id_type,source_record_id
            ) source_record_id,source_record_id_type,operation,
              old_teacher_id,old_teacher_id_type,old_student_token,
              new_teacher_id,new_teacher_id_type,new_student_token,
              old_valid_start_at,old_valid_end_at,new_valid_start_at,
              new_valid_end_at,effective_time_evidence_status,
              source_timestamp,source_row_revision,event_sequence
            FROM public.teacher_student_relationship_events
            WHERE source_region=:source_region
              AND relationship_type='FAVORITE'
              AND ((old_teacher_id=:teacher_id
                    AND old_student_token=:student_token)
                   OR (new_teacher_id=:teacher_id
                    AND new_student_token=:student_token))
            ORDER BY source_table,source_record_id_type,source_record_id,
                     source_row_revision DESC,event_sequence DESC
            """
        ),
        _pair_params(pair),
    ).mappings()
    result: list[FavoriteInterval] = []
    for row in rows:
        operation = str(row["operation"])
        new_is_pair = (
            row.get("new_teacher_id_type") == pair.teacher_id_type
            and row.get("new_teacher_id") == pair.teacher_id
            and row.get("new_student_token") == pair.student_token
        )
        old_is_pair = (
            row.get("old_teacher_id_type") == pair.teacher_id_type
            and row.get("old_teacher_id") == pair.teacher_id
            and row.get("old_student_token") == pair.student_token
        )
        if new_is_pair:
            start = _aware_datetime_or_none(row.get("new_valid_start_at"))
            result.append(
                FavoriteInterval(
                    teacher_id=pair.teacher_id,
                    teacher_id_type=pair.teacher_id_type,
                    student_token=pair.student_token,
                    start_at=start,
                    end_at=None,
                    start_evidence_confirmed=start is not None,
                    end_evidence_confirmed=True,
                )
            )
        elif old_is_pair and operation in _DELETE_OPERATIONS:
            start = _aware_datetime_or_none(row.get("old_valid_start_at"))
            end = _aware_datetime_or_none(row.get("old_valid_end_at"))
            result.append(
                FavoriteInterval(
                    teacher_id=pair.teacher_id,
                    teacher_id_type=pair.teacher_id_type,
                    student_token=pair.student_token,
                    start_at=start,
                    end_at=end,
                    start_evidence_confirmed=start is not None,
                    end_evidence_confirmed=end is not None,
                )
            )
        # An UPDATE that moves/corrects the identity away from this pair
        # revokes the before image; it is not treated as a historical period.
    return tuple(result)


def _build_observation_requirements(
    *,
    pair: _Pair,
    courses: Sequence[_FavoriteCourse],
    intervals: Sequence[FavoriteInterval],
    favorite_history_scope: _ScopeEvidence,
    existing_observations: Mapping[str, Mapping[str, Any]],
    business_as_of: datetime,
) -> tuple[_ObservationRequirement, ...]:
    completion_times = [
        row.completion_end_time
        for row in courses
        if row.completion_end_time is not None
    ]
    earliest_completion = min(completion_times) if completion_times else None
    result: list[_ObservationRequirement] = []
    for course in courses:
        observed_at = (
            favorite_observed_at(course.completion_end_time)
            if course.completion_end_time is not None
            else None
        )
        observation_due = (
            observed_at <= business_as_of if observed_at is not None else None
        )
        existing = existing_observations.get(course.source_appoint_id)
        if course.appoint_id_type is None:
            status = "WAITING_EVIDENCE"
            relation_state = None
            evidence = "SOURCE_MISSING"
            error = "SOURCE_MISSING:APPOINT_ID_TYPE_MISSING"
        elif course.completion_end_time is None:
            status = "WAITING_EVIDENCE"
            relation_state = None
            evidence = "SOURCE_MISSING"
            error = "SOURCE_MISSING:COMPLETION_END_TIME_MISSING"
        elif course.completion_conflict_status == "PENDING" or (
            course.course_evidence_status == "SOURCE_CONFLICT"
        ):
            status = "WAITING_EVIDENCE"
            relation_state = None
            evidence = "SOURCE_MISSING"
            error = "SOURCE_CONFLICT:COURSE_COMPLETION_PENDING"
        elif observed_at is not None and observed_at > business_as_of:
            status = FavoriteObservationStatus.PENDING.value
            relation_state = None
            evidence = "PENDING"
            error = None
        else:
            assert observed_at is not None
            history_complete = _history_covers(
                favorite_history_scope,
                history_from=earliest_completion,
                history_through=observed_at,
            )
            evaluation = evaluate_favorite_at(
                intervals,
                observed_at=observed_at,
                history_complete=history_complete,
            )
            status = evaluation.status.value
            relation_state = evaluation.relation_state
            evidence = evaluation.relation_evidence_status
            error = evaluation.error_code
        existing_revision = (
            int(existing["observation_revision"]) if existing else None
        )
        existing_status = str(existing["status"]) if existing else None
        requires_materialization = (
            existing is None
            or existing.get("completion_participation_seq")
            != course.completion_participation_seq
            or _aware_datetime_or_none(existing.get("observed_at"))
            != observed_at
            or existing_status != status
            or existing.get("relation_state") is not relation_state
            or existing.get("relation_evidence_status") != evidence
            or existing.get("relation_error_code") != error
        )
        result.append(
            _ObservationRequirement(
                course=course,
                observed_at=observed_at,
                observation_due=observation_due,
                status=status,
                relation_state=relation_state,
                relation_evidence_status=evidence,
                relation_error_code=error,
                existing_observation_revision=existing_revision,
                existing_observation_status=existing_status,
                requires_materialization=requires_materialization,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda row: (
                row.observed_at or datetime.min.replace(tzinfo=timezone.utc),
                _typed_sort_key(
                    row.course.appoint_id_type or "TEXT",
                    row.course.source_appoint_id,
                ),
            ),
        )
    )


def _build_attribution_intent(
    *,
    pair: _Pair,
    requirements: Sequence[_ObservationRequirement],
    existing_attribution: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current_course = (
        str(existing_attribution["source_appoint_id"])
        if existing_attribution is not None
        and existing_attribution.get("status")
        in {"AWARDED", "AWARDED_PENDING_EVIDENCE"}
        else None
    )
    if (
        existing_attribution is not None
        and existing_attribution.get("status") == "AWARDED_PENDING_EVIDENCE"
    ):
        return {
            "disposition": "HOLD_CURRENT_ATTRIBUTION",
            "action": "HOLD",
            "selected_source_appoint_id": current_course,
            "reason": existing_attribution.get("hold_reason"),
            "existing_status": existing_attribution.get("status"),
        }
    if current_course is not None:
        current_requirement = next(
            (
                row
                for row in requirements
                if row.course.source_appoint_id == current_course
            ),
            None,
        )
        if current_requirement is None or current_requirement.status not in {
            FavoriteObservationStatus.CONFIRMED_TRUE.value,
            FavoriteObservationStatus.CONFIRMED_FALSE.value,
        }:
            return {
                "disposition": "HOLD_CURRENT_ATTRIBUTION",
                "action": "HOLD",
                "selected_source_appoint_id": current_course,
                "reason": (
                    "ATTRIBUTED_COURSE_EVIDENCE_MISSING"
                    if current_requirement is None
                    else "REVALIDATION_PENDING"
                ),
                "existing_status": existing_attribution.get("status"),
            }

    confirmed: list[FavoriteCourseObservation] = []
    unresolved: list[_ObservationRequirement] = []
    for row in requirements:
        if (
            row.status == FavoriteObservationStatus.CONFIRMED_TRUE.value
            and row.course.appoint_id_type is not None
            and row.observed_at is not None
        ):
            confirmed.append(
                FavoriteCourseObservation(
                    source_region=pair.source_region,
                    source_appoint_id=row.course.source_appoint_id,
                    source_appoint_id_type=row.course.appoint_id_type,
                    observation_revision=(
                        row.existing_observation_revision or 1
                    ),
                    teacher_id=pair.teacher_id,
                    teacher_id_type=pair.teacher_id_type,
                    student_token=pair.student_token,
                    completion_participation_seq=(
                        row.course.completion_participation_seq
                    ),
                    observed_at=row.observed_at,
                    status=FavoriteObservationStatus.CONFIRMED_TRUE,
                )
            )
        elif row.status not in {
            FavoriteObservationStatus.CONFIRMED_FALSE.value,
        }:
            unresolved.append(row)
    selection = select_favorite_attribution_candidate(confirmed)
    selected = selection.selected
    if selected is not None:
        selected_key = (
            selected.observed_at,
            _typed_sort_key(
                selected.source_appoint_id_type,
                selected.source_appoint_id,
            ),
        )
        blocking = [
            row
            for row in unresolved
            if row.observed_at is None
            or (
                row.observed_at,
                _typed_sort_key(
                    row.course.appoint_id_type or "TEXT",
                    row.course.source_appoint_id,
                ),
            )
            < selected_key
        ]
        if blocking:
            return {
                "disposition": "PENDING_EVIDENCE",
                "action": "HOLD" if current_course is not None else "NONE",
                "selected_source_appoint_id": current_course,
                "reason": "EARLIER_OBSERVATION_UNRESOLVED",
                "existing_status": (
                    existing_attribution.get("status")
                    if existing_attribution is not None
                    else None
                ),
            }
        selected_course = selected.source_appoint_id
    elif unresolved:
        return {
            "disposition": "PENDING_EVIDENCE",
            "action": "HOLD" if current_course is not None else "NONE",
            "selected_source_appoint_id": current_course,
            "reason": "OBSERVATION_UNRESOLVED",
            "existing_status": (
                existing_attribution.get("status")
                if existing_attribution is not None
                else None
            ),
        }
    else:
        selected_course = None

    if selected_course is None and current_course is None:
        action = "NONE"
        disposition = "NO_CANDIDATE"
    elif selected_course is None:
        action = "REVERSE"
        disposition = "REVERSAL_REQUIRED"
    elif current_course is None:
        action = "AWARD"
        disposition = "SELECTED"
    elif current_course == selected_course:
        action = "NONE"
        disposition = "KEEP_CURRENT_ATTRIBUTION"
    else:
        action = "RESELECT"
        disposition = "RESELECT_REQUIRED"
    return {
        "disposition": disposition,
        "action": action,
        "selected_source_appoint_id": selected_course,
        "reason": None,
        "existing_status": (
            existing_attribution.get("status")
            if existing_attribution is not None
            else None
        ),
    }


def _observation_requirement_json(
    value: _ObservationRequirement,
) -> dict[str, Any]:
    return {
        "source_appoint_id": value.course.source_appoint_id,
        "appoint_id_type": value.course.appoint_id_type,
        "completion_participation_seq": (
            value.course.completion_participation_seq
        ),
        "completion_source_revision": value.course.completion_source_revision,
        "completion_end_time": _iso_or_none(
            value.course.completion_end_time
        ),
        "observed_at": _iso_or_none(value.observed_at),
        "observation_due": value.observation_due,
        "status": value.status,
        "relation_state": value.relation_state,
        "relation_evidence_status": value.relation_evidence_status,
        "relation_error_code": value.relation_error_code,
        "existing_observation_revision": value.existing_observation_revision,
        "existing_observation_status": value.existing_observation_status,
        "requires_materialization": value.requires_materialization,
    }


def _scope_state_json(value: _ScopeEvidence) -> dict[str, Any]:
    return {
        "source_table": value.source_table,
        "scope_kind": value.scope_kind,
        "scope_level": value.scope_level,
        "scope_key": value.scope_key,
        "state": value.state,
        "row_version": value.row_version,
        "active_snapshot_id": value.active_snapshot_id,
        "active_fence_hash": value.active_fence_hash,
        "history_from": _iso_or_none(value.history_from),
        "history_through": _iso_or_none(value.history_through),
    }


def _history_covers(
    scope: _ScopeEvidence,
    *,
    history_from: datetime | None,
    history_through: datetime,
) -> bool:
    return bool(
        scope.complete
        and history_from is not None
        and scope.history_from is not None
        and scope.history_through is not None
        and scope.history_from <= history_from
        and scope.history_through >= history_through
    )


def _source_row_pair(
    row: DtsV2CurrentSourceRow,
) -> tuple[str, str]:
    teacher, student = _required_source_row_pair(row)
    return teacher[0], student


def _required_source_row_pair(
    row: DtsV2CurrentSourceRow,
) -> tuple[tuple[str, str], str]:
    teacher = _first_typed_id(
        row.source_row,
        row.source_field_types,
        ("t_id", "tea_id", "teacher_id"),
    )
    if teacher is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_TEACHER_REQUIRED"
        )
    student = _student_subject(
        row.source_region,
        row.source_row,
        row.source_field_types,
    )
    return teacher, student


def _first_typed_id(
    image: Mapping[str, Any],
    field_types: Mapping[str, Any],
    field_names: Sequence[str],
) -> tuple[str, str] | None:
    values: list[tuple[str, str]] = []
    for field_name in field_names:
        raw = image.get(field_name)
        if raw is None:
            continue
        source_type = field_types.get(field_name)
        if source_type not in _ID_TYPES:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_SOURCE_ID_TYPE_MISSING"
            )
        values.append((_canonical_typed_id(raw, str(source_type)), str(source_type)))
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_ID_CONFLICT"
        )
    return values[0]


def _student_subject(
    source_region: str,
    image: Mapping[str, Any],
    field_types: Mapping[str, Any],
) -> str:
    token = image.get("student_token")
    if token is not None:
        if not isinstance(token, str) or not token:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_TOKEN_INVALID"
            )
        if source_region == "dom" and _DOM_STUDENT_TOKEN.fullmatch(
            token
        ) is None:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_DOM_TOKEN_INVALID"
            )
        return token
    if source_region == "dom":
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_DOM_TOKEN_REQUIRED"
        )
    value = _first_typed_id(
        image,
        field_types,
        ("s_id", "stu_id", "student_id", "user_id"),
    )
    if value is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_STUDENT_REQUIRED"
        )
    return value[0]


def _optional_datetime(
    version: _RelationshipVersion,
    image: Mapping[str, Any] | None,
    field_name: str,
) -> datetime | None:
    if image is None or image.get(field_name) is None:
        return None
    if version.source_field_types.get(field_name) not in {"TEXT", "TEMPORAL"}:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_SOURCE_TIME_TYPE_MISSING"
        )
    return _aware_datetime_or_none(image[field_name])


def _optional_boolean(
    version: _RelationshipVersion,
    image: Mapping[str, Any] | None,
    field_name: str,
) -> bool | None:
    if image is None or image.get(field_name) is None:
        return None
    value = image[field_name]
    source_type = version.source_field_types.get(field_name)
    if source_type == "BOOLEAN" and type(value) is bool:
        return value
    if source_type == "NUMERIC" and not isinstance(value, bool):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_SOURCE_BOOLEAN_INVALID"
            ) from exc
        if number in {0, 1}:
            return bool(number)
    raise DtsV2TeacherStudentDomainProjectorError(
        "DTS_V2_TEACHER_STUDENT_SOURCE_BOOLEAN_INVALID"
    )


def _row_optional_datetime(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> datetime | None:
    return row.datetime_value(field_name) if row.value(field_name) is not None else None


def _row_optional_boolean(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> bool | None:
    value = row.value(field_name)
    if value is None:
        return None
    source_type = row.source_field_types.get(field_name)
    if source_type == "BOOLEAN" and type(value) is bool:
        return value
    if source_type == "NUMERIC" and not isinstance(value, bool):
        number = Decimal(str(value))
        if number in {0, 1}:
            return bool(number)
    raise DtsV2TeacherStudentDomainProjectorError(
        "DTS_V2_TEACHER_STUDENT_SOURCE_BOOLEAN_INVALID"
    )


def _transaction_timestamp(connection: Any) -> datetime:
    value = connection.execute(
        text("SELECT transaction_timestamp()")
    ).scalar_one()
    parsed = _aware_datetime_or_none(value)
    if parsed is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_DATABASE_TIME_INVALID"
        )
    return parsed


def _source_position_timestamp(position: Mapping[str, Any]) -> datetime | None:
    value = position.get("source_timestamp")
    return _aware_datetime_or_none(value) if value is not None else None


def _aware_datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError as exc:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_DATETIME_INVALID"
            ) from exc
    else:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_DATETIME_INVALID"
        )
    if result.utcoffset() is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_DATETIME_TIMEZONE_REQUIRED"
        )
    return result


def _canonical_typed_id(value: Any, source_type: str) -> str:
    if source_type == "TEXT":
        if not isinstance(value, str) or not value:
            raise DtsV2TeacherStudentDomainProjectorError(
                "DTS_V2_TEACHER_STUDENT_TEXT_ID_INVALID"
            )
        return value
    if source_type != "NUMERIC" or isinstance(value, bool) or value is None:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_NUMERIC_ID_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_NUMERIC_ID_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2TeacherStudentDomainProjectorError(
            "DTS_V2_TEACHER_STUDENT_NUMERIC_ID_INVALID"
        )
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _typed_sort_key(source_type: str, value: str) -> tuple[Any, ...]:
    return (
        (0, Decimal(value))
        if source_type == "NUMERIC"
        else (1, value.encode("utf-8"))
    )


def _pair_params(pair: _Pair) -> dict[str, Any]:
    return {
        "source_region": pair.source_region,
        "teacher_id": pair.teacher_id,
        "teacher_id_type": pair.teacher_id_type,
        "student_token": pair.student_token,
    }


def _iso_or_none(value: Any) -> str | None:
    parsed = _aware_datetime_or_none(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _student_token_set_hash(tokens: Sequence[str]) -> str:
    return hashlib.sha256(_json_dump(list(tokens)).encode("utf-8")).hexdigest()


__all__ = [
    "DtsV2TeacherStudentDomainProjector",
    "DtsV2TeacherStudentDomainProjectorError",
]
