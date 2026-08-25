"""Transaction-bound persistence for the protected v2 source shadow.

The writer remains disabled by default and is activated only by the explicit
v2 dual-capture sink.  Its caller owns the surrounding PostgreSQL transaction.
The writer persists the immutable v2 source version and its v2-confirmed
current/tombstone, then returns the exact dirty-key fanout for that protected
revision.  In the same transaction it applies the revision to already-active
source-scope membership overlays; it never writes the ingest ledger,
checkpoint, dirty queue, course projection, or outbox itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_source_consumer import (
    DtsChangeEvent,
    DtsRecordError,
    _has_v2_source_image_completeness_proof,
    assert_domestic_event_protected,
)
from .dts_source_contract_v2 import (
    V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION,
    V2_SOURCE_FIELD_WHITELIST,
    V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
    V2SourceRouteDecision,
    V2ValidatedCurrentRow,
    build_v2_source_route,
    v2_source_profile_id,
)
from .dts_v2_dirty_queue_store import DirtyKeyV2, DtsV2DirtyQueueError


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DOM_STUDENT_TOKEN_PATTERN = re.compile(r"^dom:v1:[0-9a-f]{64}$")


class DtsV2ShadowSourceWriterError(RuntimeError):
    """One shadow write cannot be proven safe or internally consistent."""


@dataclass(frozen=True)
class DtsV2ShadowSourceWriteResult:
    status: str
    source_region: str
    source_table: str
    source_key: str
    source_key_type: str
    source_row_revision: int | None
    protected_payload_hash: str
    dirty_keys: tuple[DirtyKeyV2, ...] = ()


@dataclass
class DtsV2ShadowSourceBatchContext:
    """Locks and current identities shared by one serialized stream batch."""

    source_region: str
    topic: str
    partition: int
    source_partition_epoch_id: str
    incoming_epoch: Mapping[str, Any]
    locked_tables: frozenset[str]
    current_identities: set[tuple[str, str, str]]
    current_rows: dict[tuple[str, str, str], Mapping[str, Any]]
    new_offsets: frozenset[int]
    deferred_candidate_offsets: frozenset[int]
    deferred_offsets: set[int]
    deferred_writes: list["DtsV2ShadowDeferredWrite"]


@dataclass(frozen=True)
class DtsV2ShadowDeferredWrite:
    """One independently provable source transition awaiting batch flush."""

    event: DtsChangeEvent
    route: V2SourceRouteDecision
    position: Mapping[str, Any]
    source_timestamp: datetime
    revision: int
    dependency_keys: Mapping[str, Any]
    before_dependency_keys: Mapping[str, Any]
    after_dependency_keys: Mapping[str, Any]


class DtsV2ShadowSourceWriter:
    """Append one verified CDC version and advance only its shadow current."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def apply_appoint_cdc(
        self,
        connection: Connection,
        event: DtsChangeEvent,
        source_partition_epoch_id: str,
    ) -> DtsV2ShadowSourceWriteResult:
        """Compatibility entrypoint retained for the course projector tests."""

        if event.table_name not in {"dom_appoint", "ovs_appoint"}:
            raise DtsV2ShadowSourceWriterError("DTS_V2_SHADOW_APPOINT_ONLY")
        return self.apply_cdc(
            connection,
            event,
            source_partition_epoch_id,
        )

    def prepare_batch(
        self,
        connection: Connection,
        events: Sequence[DtsChangeEvent],
        source_partition_epoch_id: str,
    ) -> DtsV2ShadowSourceBatchContext | None:
        """Acquire invariant locks once and preload existing source current.

        The caller already owns the stream checkpoint row for the complete
        transaction.  Repeating epoch/table/identity advisory locks for every
        record adds several cross-region round trips without adding further
        serialization.  One shared source-table lock per table still excludes
        snapshot publication while the ordered CDC batch is applied.
        """

        business_events = tuple(
            event
            for event in events
            if _is_business_source_table(
                source_region=event.source_region,
                source_table=event.table_name or "",
            )
            and event.operation in {"INSERT", "UPDATE", "DELETE"}
        )
        if not business_events:
            return None
        first = business_events[0]
        stream_identity = (
            first.source_region,
            first.topic,
            first.partition,
        )
        if any(
            (event.source_region, event.topic, event.partition)
            != stream_identity
            for event in business_events
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_BATCH_STREAM_MISMATCH"
            )

        identities: set[tuple[str, str, str]] = set()
        identity_counts: dict[tuple[str, str, str], int] = {}
        event_identities: dict[int, tuple[str, str, str]] = {}
        tables: set[str] = set()
        for event in business_events:
            table = event.table_name or ""
            _source_key_type, source_key = _event_source_identity(event)
            identity = (event.source_region, table, source_key)
            tables.add(table)
            identities.add(identity)
            identity_counts[identity] = identity_counts.get(identity, 0) + 1
            event_identities[event.offset] = identity

        incoming_epoch = _require_broker_epoch(
            connection,
            event=first,
            source_partition_epoch_id=source_partition_epoch_id,
        )
        for table in sorted(tables):
            _lock_source_table_for_cdc(
                connection,
                source_region=first.source_region,
                source_table=table,
            )
        _lock_identities(
            connection,
            tuple(
                ("source-current", *identity)
                for identity in sorted(identities)
            ),
        )

        current_rows: dict[
            tuple[str, str, str], Mapping[str, Any]
        ] = {}
        if identities:
            rows = connection.execute(
                text(
                    """
                    SELECT current.source_region, current.source_table,
                           current.source_key, current.source_key_data,
                           current.dependency_keys, current.source_row,
                           current.is_deleted, current.source_timestamp,
                           current.last_record_id, current.source_position,
                           current.last_topic, current.last_partition,
                           current.last_offset, current.row_version,
                           current.source_row_revision,
                           current.last_source_partition_epoch_id,
                           current.last_version_kind,
                           current.source_position_v2,
                           current.record_id_type,
                           current.record_id_numeric,
                           current.record_id_text,
                           current.source_timestamp_v2,
                           current.source_payload_hash,
                           current.provenance_state,
                           current.source_key_type,
                           current.source_key_numeric,
                           current.source_key_text,
                           current.source_schema_profile_id,
                           current.source_field_types
                    FROM public.dts_source_rows AS current
                    JOIN jsonb_to_recordset(CAST(:identities AS jsonb))
                      AS requested(
                        source_region text,
                        source_table text,
                        source_key text
                      )
                      ON requested.source_region=current.source_region
                     AND requested.source_table=current.source_table
                     AND requested.source_key=current.source_key
                    FOR UPDATE OF current
                    """
                ),
                {
                    "identities": json.dumps(
                        [
                            {
                                "source_region": source_region,
                                "source_table": source_table,
                                "source_key": source_key,
                            }
                            for source_region, source_table, source_key
                            in sorted(identities)
                        ],
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                },
            ).mappings()
            for row in rows:
                identity = (
                    str(row["source_region"]),
                    str(row["source_table"]),
                    str(row["source_key"]),
                )
                current_rows[identity] = dict(row)

        current_identities = set(current_rows)
        deferred_candidate_offsets = frozenset(
            event.offset
            for event in business_events
            if identity_counts[event_identities[event.offset]] == 1
            and (
                (
                    event.operation == "INSERT"
                    and event_identities[event.offset]
                    not in current_identities
                )
                or (
                    event.operation in {"UPDATE", "DELETE"}
                    and event_identities[event.offset]
                    in current_identities
                )
            )
        )

        return DtsV2ShadowSourceBatchContext(
            source_region=first.source_region,
            topic=first.topic,
            partition=first.partition,
            source_partition_epoch_id=source_partition_epoch_id,
            incoming_epoch=incoming_epoch,
            locked_tables=frozenset(tables),
            current_identities=current_identities,
            current_rows=current_rows,
            new_offsets=frozenset(event.offset for event in business_events),
            deferred_candidate_offsets=deferred_candidate_offsets,
            deferred_offsets=set(),
            deferred_writes=[],
        )

    def flush_batch(
        self,
        connection: Connection,
        batch_context: DtsV2ShadowSourceBatchContext,
    ) -> None:
        """Persist independent source transitions without per-event RTTs."""

        if not connection.in_transaction():
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_TRANSACTION_REQUIRED"
            )
        writes = tuple(batch_context.deferred_writes)
        if not writes:
            return
        if len({write.event.offset for write in writes}) != len(writes):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_BATCH_DEFERRED_IDENTITY_INVALID"
            )
        identities = {
            (
                write.event.source_region,
                write.route.source_table,
                write.route.source_key,
            )
            for write in writes
        }
        if len(identities) != len(writes):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_BATCH_DEFERRED_IDENTITY_INVALID"
            )
        _append_versions_batch(
            connection,
            source_partition_epoch_id=batch_context.source_partition_epoch_id,
            writes=writes,
        )
        _upsert_currents_batch(
            connection,
            source_partition_epoch_id=batch_context.source_partition_epoch_id,
            writes=writes,
            initial_currents=batch_context.current_rows,
        )
        _apply_cdc_membership_overlays_batch(connection, writes=writes)
        batch_context.deferred_writes.clear()

    def apply_cdc(
        self,
        connection: Connection,
        event: DtsChangeEvent,
        source_partition_epoch_id: str,
        *,
        batch_context: DtsV2ShadowSourceBatchContext | None = None,
    ) -> DtsV2ShadowSourceWriteResult:
        if self.enabled is not True:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_WRITER_DISABLED"
            )
        if not connection.in_transaction():
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_TRANSACTION_REQUIRED"
            )

        table = event.table_name or ""
        expected_profile = v2_source_profile_id(table)
        if expected_profile is None:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_PROFILE_MISSING"
            )
        if not _is_business_source_table(
            source_region=event.source_region,
            source_table=table,
        ) or event.operation not in {"INSERT", "UPDATE", "DELETE"}:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_TABLE_NOT_PROFILED"
            )
        exact_complete_profile = (
            event.source_images_complete is True
            and event.source_image_profile_id == expected_profile
            and _has_v2_source_image_completeness_proof(
                event,
                expected_profile_id=expected_profile,
            )
        )
        completeness_claimed = bool(
            event.source_images_complete
            or event.source_image_profile_id is not None
            or event._v2_source_image_completeness_proof is not None
        )
        if (
            (event.operation == "INSERT" and not exact_complete_profile)
            or (completeness_claimed and not exact_complete_profile)
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_EXACT_SOURCE_PROFILE_REQUIRED"
            )
        try:
            assert_domestic_event_protected(
                event,
                require_process_proof=True,
            )
        except DtsRecordError as exc:
            raise DtsV2ShadowSourceWriterError(str(exc)) from exc
        source_key_type, source_key = _event_source_identity(event)
        position, source_timestamp = _source_position(
            event,
            source_partition_epoch_id=source_partition_epoch_id,
        )

        identity = (event.source_region, table, source_key)
        deferred_candidate = False
        if batch_context is None:
            incoming_epoch = _require_broker_epoch(
                connection,
                event=event,
                source_partition_epoch_id=source_partition_epoch_id,
            )
            _lock_source_table_for_cdc(
                connection,
                source_region=event.source_region,
                source_table=table,
            )
            _lock_identity(
                connection,
                "source-current",
                event.source_region,
                table,
                source_key,
            )
            _lock_identity(
                connection,
                "source-version",
                event.source_region,
                source_partition_epoch_id,
                event.topic,
                event.partition,
                event.offset,
            )
        else:
            if (
                batch_context.source_region != event.source_region
                or batch_context.topic != event.topic
                or batch_context.partition != event.partition
                or batch_context.source_partition_epoch_id
                != source_partition_epoch_id
                or table not in batch_context.locked_tables
                or event.offset not in batch_context.new_offsets
            ):
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_BATCH_CONTEXT_MISMATCH"
                )
            incoming_epoch = batch_context.incoming_epoch
            if (
                event.operation in {"UPDATE", "DELETE"}
                and identity not in batch_context.current_identities
            ):
                return _ignored_missing_current_result(
                    event=event,
                    source_key=source_key,
                    source_key_type=source_key_type,
                )
            deferred_candidate = (
                event.offset in batch_context.deferred_candidate_offsets
            )

        # Replay-prefix records are handled before a batch context is built.
        # For the locked contiguous new suffix, an existing immutable version
        # without its checkpoint is impossible because both commit in the same
        # transaction.  Avoid one cross-region lookup per new business event.
        replay = (
            _read_version_identity(
                connection,
                event=event,
                source_partition_epoch_id=source_partition_epoch_id,
            )
            if batch_context is None
            else None
        )
        if replay is not None:
            replay_route = _route_from_persisted_version(replay)
            if (
                replay_route.source_key_type != source_key_type
                or replay_route.source_key != source_key
            ):
                raise DtsV2ShadowSourceWriterError(
                    "SOURCE_VERSION_IDENTITY_CONFLICT"
                )
            _require_event_patch_matches_version(event, replay_route)
            _require_exact_replay(
                replay,
                replay_route,
                event=event,
                position=position,
                source_timestamp=source_timestamp,
            )
            return _result(
                "REPLAYED",
                event=event,
                route=replay_route,
                revision=replay["source_row_revision"],
            )

        if incoming_epoch.get("status") not in {
            "BARRIER_PENDING",
            "ACTIVE",
        }:
            # A superseded epoch may prove an exact immutable replay above, but
            # it must never append a new delivery or advance source current.
            raise DtsV2ShadowSourceWriterError("DTS_V2_SHADOW_EPOCH_REJECTED")

        stored_current = (
            batch_context.current_rows.get(identity)
            if batch_context is not None and deferred_candidate
            else _read_current_for_update(
                connection,
                event=event,
                source_table=table,
                source_key=source_key,
            )
        )
        legacy_current = bool(
            stored_current is not None
            and _is_legacy_pending_current(stored_current)
        )
        current = None
        if stored_current is not None and not legacy_current:
            current = _validated_current(stored_current, event=event)
        if stored_current is not None and not legacy_current:
            _require_source_position_advances(
                connection,
                event=event,
                source_partition_epoch_id=source_partition_epoch_id,
                incoming_epoch=incoming_epoch,
                stored_current=stored_current,
            )
        if (
            table in {"dom_appoint", "ovs_appoint"}
            and event.operation == "UPDATE"
            and current is None
        ):
            # Fresh-start deliberately has no pre-H0 course baseline.  An
            # UPDATE for a course that was never inserted into V2 cannot prove
            # the previous teacher or the rest of the course facts, so it is
            # acknowledged as an ignored source event without creating source
            # current/version state or dirty work.
            return _ignored_missing_current_result(
                event=event,
                source_key=source_key,
                source_key_type=source_key_type,
            )
        try:
            route = build_v2_source_route(event, current=current)
        except (DtsRecordError, ValueError) as exc:
            raise DtsV2ShadowSourceWriterError(str(exc)) from exc
        if route.route_status == "WAITING_CURRENT_ROW":
            # An event-only fresh start has no snapshot for rows created
            # before H0.  A sparse UPDATE/DELETE cannot safely reconstruct
            # that row, so acknowledge it without inventing source state or
            # blocking every later event in the partition.
            return _ignored_missing_current_result(
                event=event,
                source_key=source_key,
                source_key_type=source_key_type,
            )
        _require_versioned_route(route)
        if (
            route.source_key != source_key
            or route.source_key_type != source_key_type
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_IDENTITY_CHANGED_DURING_MERGE"
            )

        semantic_replay = False
        if stored_current is not None and not legacy_current:
            if event.operation == "INSERT" and not stored_current["is_deleted"]:
                raise DtsV2ShadowSourceWriterError("SOURCE_INSERT_CONFLICT")
            if event.operation in {"UPDATE", "DELETE"} and not _same_json(
                route.before_row,
                stored_current["source_row"],
            ):
                semantic_replay = (
                    event.operation == "UPDATE"
                    and not stored_current["is_deleted"]
                    and _same_json(
                        route.after_row,
                        stored_current["source_row"],
                    )
                    and _current_transition_matches(
                        connection,
                        stored_current=stored_current,
                        route=route,
                    )
                )
                if not semantic_replay:
                    raise DtsV2ShadowSourceWriterError(
                        "SOURCE_BEFORE_CONFLICT"
                    )

        semantic_noop = (
            not semantic_replay
            and not legacy_current
            and _is_semantic_noop(
                operation=event.operation,
                route=route,
                stored_current=stored_current,
            )
        )

        revision = (
            1
            if stored_current is None or legacy_current
            else int(stored_current["source_row_revision"]) + 1
        )
        dependency_keys = _source_dependency_keys(route, event.source_region)
        before_dependency_keys = _source_dependency_keys_for_image(
            route,
            event.source_region,
            route.before_row,
        )
        after_dependency_keys = _source_dependency_keys_for_image(
            route,
            event.source_region,
            None if event.operation == "DELETE" else route.after_row,
        )
        if batch_context is not None and deferred_candidate:
            batch_context.deferred_writes.append(
                DtsV2ShadowDeferredWrite(
                    event=event,
                    route=route,
                    position=position,
                    source_timestamp=source_timestamp,
                    revision=revision,
                    dependency_keys=dependency_keys,
                    before_dependency_keys=before_dependency_keys,
                    after_dependency_keys=after_dependency_keys,
                )
            )
            batch_context.deferred_offsets.add(event.offset)
        else:
            _append_version(
                connection,
                event=event,
                source_partition_epoch_id=source_partition_epoch_id,
                route=route,
                position=position,
                source_timestamp=source_timestamp,
                revision=revision,
            )
            _write_current(
                connection,
                event=event,
                source_partition_epoch_id=source_partition_epoch_id,
                route=route,
                position=position,
                source_timestamp=source_timestamp,
                revision=revision,
                dependency_keys=dependency_keys,
                stored_current=stored_current,
            )
            _apply_cdc_membership_overlay(
                connection,
                source_region=event.source_region,
                source_table=table,
                source_key=source_key,
                source_row_revision=revision,
                before_dependency_keys=before_dependency_keys,
                after_dependency_keys=after_dependency_keys,
                after_is_present=event.operation != "DELETE",
            )
        result = _result(
            "SEMANTIC_REPLAY"
            if semantic_replay
            else ("NOOP" if semantic_noop else "APPLIED"),
            event=event,
            route=route,
            revision=revision,
        )
        if batch_context is not None:
            batch_context.current_identities.add(identity)
        return result


def _event_source_identity(event: DtsChangeEvent) -> tuple[str, str]:
    """Resolve only the typed immutable source id before reading current.

    Sparse UPDATE/DELETE images are intentionally not routed here: a complete
    version cannot be proven until their locked v2 current is loaded.  The id
    itself must nevertheless be present in every authoritative source image
    so the correct current row can be locked without guessing.
    """

    table = event.table_name or ""
    expected_key_type = V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE.get(table)
    raw_key_type = event.source_field_types.get("id")
    normalized_raw_key_type = (
        raw_key_type.upper() if isinstance(raw_key_type, str) else None
    )
    if normalized_raw_key_type not in {"NUMERIC", "TEXT"} or (
        expected_key_type is not None
        and normalized_raw_key_type != expected_key_type
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_SOURCE_PRIMARY_KEY_TYPE_MISMATCH"
        )
    assert normalized_raw_key_type is not None
    images = (
        (event.after,)
        if event.operation == "INSERT"
        else (event.before,)
        if event.operation == "DELETE"
        else (event.before, event.after)
    )
    canonical: list[str] = []
    for image in images:
        if not isinstance(image, Mapping) or image.get("id") is None:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_PRIMARY_KEY_REQUIRED"
            )
        canonical.append(
            _canonical_source_key(image["id"], normalized_raw_key_type)
        )
    if not canonical or any(value != canonical[0] for value in canonical[1:]):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_PRIMARY_KEY_UPDATE_NOT_ALLOWED"
        )
    return normalized_raw_key_type, canonical[0]


def _canonical_source_key(value: Any, source_type: str) -> str:
    if source_type == "TEXT":
        if not isinstance(value, str) or not value:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_PRIMARY_KEY_INVALID"
            )
        return value
    if source_type != "NUMERIC" or isinstance(value, bool) or value is None:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_PRIMARY_KEY_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_PRIMARY_KEY_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_PRIMARY_KEY_INVALID"
        )
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _require_versioned_route(route: V2SourceRouteDecision) -> None:
    if (
        route.route_status != "VERSIONED"
        or not isinstance(route.source_table, str)
        or not any(
            _is_business_source_table(
                source_region=source_region,
                source_table=route.source_table,
            )
            for source_region in ("dom", "ovs")
        )
        or route.source_key is None
        or route.source_key_type not in {"NUMERIC", "TEXT"}
        or route.source_schema_profile_id is None
        or not isinstance(route.source_field_types, Mapping)
        or not isinstance(route.protected_payload_hash, str)
        or _SHA256_PATTERN.fullmatch(route.protected_payload_hash) is None
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_ROUTE_NOT_VERSIONED"
        )


def _is_business_source_table(
    *,
    source_region: str,
    source_table: str,
) -> bool:
    if source_region not in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION:
        return False
    prefix = f"{source_region}_"
    return source_table.startswith(prefix) and source_table.removeprefix(
        prefix
    ) in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION[source_region]


def _route_from_persisted_version(
    row: Mapping[str, Any],
) -> V2SourceRouteDecision:
    source_key_data = row.get("source_key_data")
    route = V2SourceRouteDecision(
        route_status="VERSIONED",
        source_table=row.get("source_table"),
        operation=row.get("operation"),
        source_key=row.get("source_key"),
        source_key_type=row.get("source_key_type"),
        source_key_data_json=(
            None
            if not isinstance(source_key_data, Mapping)
            else json.dumps(
                source_key_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        ),
        source_key_numeric=row.get("source_key_numeric"),
        source_key_text=row.get("source_key_text"),
        source_schema_profile_id=row.get("source_schema_profile_id"),
        source_field_types=row.get("source_field_types"),
        before_row=row.get("before_row"),
        after_row=row.get("after_row"),
        protected_payload_hash=row.get("protected_source_row_hash"),
    )
    _require_versioned_route(route)
    if route.source_key_data_json is None:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_VERSION_IDENTITY_CONFLICT"
        )
    return route


def _require_event_patch_matches_version(
    event: DtsChangeEvent,
    route: V2SourceRouteDecision,
) -> None:
    """Validate a broker replay without rebuilding it from a later current.

    A real sparse event cannot reconstruct historical untouched columns after
    later revisions have advanced current.  The immutable persisted version is
    therefore authoritative for those columns; every field actually present
    in the replay still has to match its corresponding persisted image and
    typed evidence exactly.
    """

    if (
        route.source_table != event.table_name
        or route.operation != event.operation
        or not isinstance(route.source_field_types, Mapping)
    ):
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_VERSION_IDENTITY_CONFLICT"
        )
    table = event.table_name or ""
    prefix = f"{event.source_region}_"
    if not _is_business_source_table(
        source_region=event.source_region,
        source_table=table,
    ):
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_VERSION_IDENTITY_CONFLICT"
        )
    whitelist = V2_SOURCE_FIELD_WHITELIST[table.removeprefix(prefix)]
    incoming_images = (
        (event.before, event.after)
        if event.operation == "UPDATE"
        else (event.before,)
        if event.operation == "DELETE"
        else (event.after,)
    )
    persisted_images = (
        (route.before_row, route.after_row)
        if event.operation == "UPDATE"
        else (route.before_row,)
        if event.operation == "DELETE"
        else (route.after_row,)
    )
    for incoming, persisted in zip(incoming_images, persisted_images):
        if not isinstance(incoming, Mapping) or not isinstance(persisted, Mapping):
            raise DtsV2ShadowSourceWriterError(
                "SOURCE_VERSION_IDENTITY_CONFLICT"
            )
        for field_name, value in incoming.items():
            if (
                field_name in whitelist
                and field_name != "id"
                and not _same_json(
                    value,
                    persisted.get(field_name),
                )
            ):
                raise DtsV2ShadowSourceWriterError(
                    "SOURCE_VERSION_IDENTITY_CONFLICT"
                )
            declared_type = event.source_field_types.get(field_name)
            if field_name == "student_token" and event.source_region == "dom":
                # DOM protection derives this field after the raw physical
                # selected-column type map was attested.  Its v2 type evidence
                # is nevertheless fixed and persisted as TEXT.
                declared_type = "TEXT"
            persisted_type = route.source_field_types.get(field_name)
            if (
                field_name in whitelist
                and value is not None
                and (
                    not isinstance(declared_type, str)
                    or declared_type.upper() != persisted_type
                )
            ):
                raise DtsV2ShadowSourceWriterError(
                    "SOURCE_VERSION_IDENTITY_CONFLICT"
                )


def _source_position(
    event: DtsChangeEvent,
    *,
    source_partition_epoch_id: str,
) -> tuple[dict[str, Any], datetime]:
    if (
        not isinstance(source_partition_epoch_id, str)
        or not source_partition_epoch_id.strip()
        or not isinstance(event.topic, str)
        or not event.topic
        or isinstance(event.partition, bool)
        or not isinstance(event.partition, int)
        or event.partition < 0
        or isinstance(event.offset, bool)
        or not isinstance(event.offset, int)
        or event.offset < 0
        or isinstance(event.record_id, bool)
        or not isinstance(event.record_id, int)
        or isinstance(event.source_timestamp, bool)
        or not isinstance(event.source_timestamp, int)
        or event.source_timestamp < 0
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_POSITION_INVALID"
        )
    try:
        timestamp = datetime.fromtimestamp(event.source_timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_POSITION_INVALID"
        ) from exc
    timestamp_text = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return (
        {
            "v": 1,
            "source_timestamp": timestamp_text,
            "record_id_type": "numeric",
            "record_id": str(event.record_id),
            "source_partition_epoch_id": source_partition_epoch_id,
            "topic": event.topic,
            "partition_id": event.partition,
            "offset_value": event.offset,
        },
        timestamp,
    )


def _require_broker_epoch(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
) -> Mapping[str, Any]:
    epoch = connection.execute(
        text(
            """
            SELECT source_partition_epoch_id, topic, partition_id,
                   epoch_kind, status, epoch_sequence,
                   predecessor_epoch_id, start_offset,
                   v2_epoch_bootstrap_floor
            FROM public.dts_source_partition_epochs
            WHERE source_region = :source_region
              AND source_partition_epoch_id = :epoch_id
              AND topic = :topic
              AND partition_id = :partition_id
            """
        ),
        {
            "source_region": event.source_region,
            "epoch_id": source_partition_epoch_id,
            "topic": event.topic,
            "partition_id": event.partition,
        },
    ).mappings().one_or_none()
    if (
        epoch is None
        or epoch["epoch_kind"] != "BROKER"
        or epoch["status"] not in {
            "BARRIER_PENDING",
            "ACTIVE",
            "SUPERSEDED",
        }
        or isinstance(epoch["start_offset"], bool)
        or not isinstance(epoch["start_offset"], int)
        or epoch["start_offset"] < 0
        or isinstance(epoch["v2_epoch_bootstrap_floor"], bool)
        or not isinstance(epoch["v2_epoch_bootstrap_floor"], int)
        or epoch["v2_epoch_bootstrap_floor"] < 0
        or event.offset < epoch["start_offset"]
        or event.offset < epoch["v2_epoch_bootstrap_floor"]
    ):
        raise DtsV2ShadowSourceWriterError("DTS_V2_SHADOW_EPOCH_REJECTED")
    return epoch


def _require_source_position_advances(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
    incoming_epoch: Mapping[str, Any],
    stored_current: Mapping[str, Any],
) -> None:
    """Reject a new delivery that cannot be ordered after source current.

    Exact broker replays have already returned through the immutable-version
    identity path before this function runs.  A genuinely new delivery must
    therefore advance the offset inside one broker epoch, or move to a broker
    epoch whose predecessor chain contains the current broker epoch.  A
    SNAPSHOT_DIFF current is followed only at or beyond its persisted broker
    barrier (or by a verified successor broker epoch); source timestamps never
    decide cross-kind order.
    """

    if stored_current.get("last_version_kind") == "SNAPSHOT_DIFF":
        diff_version = connection.execute(
            text(
                """
                SELECT covered_through_offsets
                FROM public.dts_source_row_versions
                WHERE source_region = :source_region
                  AND source_partition_epoch_id = :epoch_id
                  AND topic = :topic
                  AND partition_id = :partition_id
                  AND offset_value = :offset_value
                  AND version_kind = 'SNAPSHOT_DIFF'
                """
            ),
            {
                "source_region": event.source_region,
                "epoch_id": stored_current.get(
                    "last_source_partition_epoch_id"
                ),
                "topic": stored_current.get("last_topic"),
                "partition_id": stored_current.get("last_partition"),
                "offset_value": stored_current.get("last_offset"),
            },
        ).mappings().one_or_none()
        offsets = None if diff_version is None else diff_version.get(
            "covered_through_offsets"
        )
        if not isinstance(offsets, list):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SNAPSHOT_BARRIER_INVALID"
            )
        matching = [
            item
            for item in offsets
            if isinstance(item, Mapping)
            and item.get("source_region") == event.source_region
            and item.get("topic") == event.topic
            and item.get("partition_id") == event.partition
        ]
        if len(matching) != 1:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SNAPSHOT_BARRIER_INVALID"
            )
        barrier = matching[0]
        barrier_epoch_id = barrier.get("source_partition_epoch_id")
        barrier_end = barrier.get("end_next_offset")
        if (
            not isinstance(barrier_epoch_id, str)
            or not barrier_epoch_id
            or isinstance(barrier_end, bool)
            or not isinstance(barrier_end, int)
            or barrier_end < 0
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SNAPSHOT_BARRIER_INVALID"
            )
        if source_partition_epoch_id == barrier_epoch_id:
            if event.offset < barrier_end:
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_SOURCE_POSITION_NOT_ADVANCING"
                )
            return
        successor = connection.execute(
            text(
                """
                WITH RECURSIVE lineage AS (
                    SELECT source_partition_epoch_id,predecessor_epoch_id,
                           topic,partition_id,
                           ARRAY[source_partition_epoch_id]::varchar[] path,
                           false cycle
                    FROM public.dts_source_partition_epochs
                    WHERE source_region = :source_region
                      AND source_partition_epoch_id = :incoming_epoch_id
                    UNION ALL
                    SELECT parent.source_partition_epoch_id,
                           parent.predecessor_epoch_id,parent.topic,
                           parent.partition_id,
                           lineage.path || parent.source_partition_epoch_id,
                           parent.source_partition_epoch_id = ANY(lineage.path)
                    FROM lineage
                    JOIN public.dts_source_partition_epochs parent
                      ON parent.source_region = :source_region
                     AND parent.source_partition_epoch_id =
                            lineage.predecessor_epoch_id
                    WHERE lineage.predecessor_epoch_id IS NOT NULL
                      AND lineage.cycle IS FALSE
                )
                SELECT EXISTS (
                    SELECT 1 FROM lineage
                    WHERE source_partition_epoch_id = :barrier_epoch_id
                      AND topic = :topic
                      AND partition_id = :partition_id
                      AND cycle IS FALSE
                )
                """
            ),
            {
                "source_region": event.source_region,
                "incoming_epoch_id": source_partition_epoch_id,
                "barrier_epoch_id": barrier_epoch_id,
                "topic": event.topic,
                "partition_id": event.partition,
            },
        ).scalar_one()
        if successor is not True:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
            )
        return

    if stored_current.get("last_version_kind") != "CDC":
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_POSITION_KIND_UNSUPPORTED"
        )

    current_epoch_id = stored_current.get("last_source_partition_epoch_id")
    current_topic = stored_current.get("last_topic")
    current_partition = stored_current.get("last_partition")
    current_offset = stored_current.get("last_offset")
    if (
        not isinstance(current_epoch_id, str)
        or not current_epoch_id
        or not isinstance(current_topic, str)
        or not current_topic
        or isinstance(current_partition, bool)
        or not isinstance(current_partition, int)
        or current_partition < 0
        or isinstance(current_offset, bool)
        or not isinstance(current_offset, int)
        or current_offset < 0
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_CURRENT_POSITION_INVALID"
        )

    if current_epoch_id == source_partition_epoch_id:
        if (
            current_topic != event.topic
            or current_partition != event.partition
            or event.offset <= current_offset
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_POSITION_NOT_ADVANCING"
            )
        return

    # Epoch succession is scoped to one physical broker partition.  A
    # predecessor pointer is not sufficient evidence if an incorrectly seeded
    # epoch crosses topic/partition boundaries.
    if current_topic != event.topic or current_partition != event.partition:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
        )

    incoming_sequence = incoming_epoch.get("epoch_sequence")
    if (
        isinstance(incoming_sequence, bool)
        or not isinstance(incoming_sequence, int)
        or incoming_sequence < 1
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
        )

    lineage = list(
        connection.execute(
            text(
                """
                WITH RECURSIVE broker_lineage AS (
                    SELECT source_partition_epoch_id, predecessor_epoch_id,
                           topic, partition_id, epoch_kind, epoch_sequence,
                           ARRAY[source_partition_epoch_id]::varchar[] AS path,
                           false AS cycle,
                           0 AS depth
                    FROM public.dts_source_partition_epochs
                    WHERE source_region = :source_region
                      AND source_partition_epoch_id = :incoming_epoch_id

                    UNION ALL

                    SELECT parent.source_partition_epoch_id,
                           parent.predecessor_epoch_id,
                           parent.topic,
                           parent.partition_id,
                           parent.epoch_kind,
                           parent.epoch_sequence,
                           lineage.path || parent.source_partition_epoch_id,
                           parent.source_partition_epoch_id = ANY(lineage.path),
                           lineage.depth + 1
                    FROM broker_lineage AS lineage
                    JOIN public.dts_source_partition_epochs AS parent
                      ON parent.source_region = :source_region
                     AND parent.source_partition_epoch_id =
                         lineage.predecessor_epoch_id
                    WHERE lineage.predecessor_epoch_id IS NOT NULL
                      AND lineage.cycle IS FALSE
                )
                SELECT source_partition_epoch_id, predecessor_epoch_id,
                       topic, partition_id, epoch_kind, epoch_sequence, cycle
                FROM broker_lineage
                ORDER BY depth
                """
            ),
            {
                "source_region": event.source_region,
                "incoming_epoch_id": source_partition_epoch_id,
            },
        ).mappings()
    )
    if not lineage or lineage[0]["source_partition_epoch_id"] != (
        source_partition_epoch_id
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
        )

    seen: set[str] = set()
    previous_sequence: int | None = None
    current_found = False
    for row in lineage:
        epoch_id = row.get("source_partition_epoch_id")
        sequence = row.get("epoch_sequence")
        if (
            not isinstance(epoch_id, str)
            or not epoch_id
            or epoch_id in seen
            or row.get("cycle") is True
            or row.get("topic") != event.topic
            or row.get("partition_id") != event.partition
            or row.get("epoch_kind") != "BROKER"
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or (
                previous_sequence is not None
                and sequence != previous_sequence - 1
            )
        ):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
            )
        seen.add(epoch_id)
        previous_sequence = sequence
        if epoch_id == current_epoch_id:
            current_found = True
            break

    if not current_found:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID"
        )


def _identity_lock_id(*parts: Any) -> int:
    payload = json.dumps(
        list(parts),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return int.from_bytes(
        hashlib.sha256(payload).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _lock_identity(connection: Connection, *parts: Any) -> None:
    lock_id = _identity_lock_id(*parts)
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def _lock_identities(
    connection: Connection,
    identities: Sequence[Sequence[Any]],
) -> None:
    lock_ids = sorted({_identity_lock_id(*parts) for parts in identities})
    if not lock_ids:
        return
    connection.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(lock_id)
            FROM unnest(CAST(:lock_ids AS bigint[])) AS locks(lock_id)
            ORDER BY lock_id
            """
        ),
        {"lock_ids": lock_ids},
    )


def _lock_source_table_for_cdc(
    connection: Connection,
    *,
    source_region: str,
    source_table: str,
) -> None:
    locked = connection.execute(
        text(
            """
            SELECT public.lock_dts_source_table_for_ingest_v3(
                :source_region,:source_table
            )
            """
        ),
        {
            "source_region": source_region,
            "source_table": source_table,
        },
    ).scalar_one()
    if locked is not True:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SOURCE_TABLE_LOCK_REJECTED"
        )


def _read_version_identity(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT version_kind, source_table, source_schema_profile_id,
                   source_field_types, source_key, source_key_data,
                   source_key_type, source_key_numeric, source_key_text,
                   operation, before_row, after_row, source_timestamp,
                   record_id_type,
                   record_id_numeric, record_id_text, source_position,
                   protected_source_row_hash, source_row_revision
            FROM public.dts_source_row_versions
            WHERE source_region = :source_region
              AND source_partition_epoch_id = :epoch_id
              AND topic = :topic
              AND partition_id = :partition_id
              AND offset_value = :offset_value
            """
        ),
        {
            "source_region": event.source_region,
            "epoch_id": source_partition_epoch_id,
            "topic": event.topic,
            "partition_id": event.partition,
            "offset_value": event.offset,
        },
    ).mappings().one_or_none()


def _require_exact_replay(
    replay: Mapping[str, Any],
    route: V2SourceRouteDecision,
    *,
    event: DtsChangeEvent,
    position: Mapping[str, Any],
    source_timestamp: datetime,
) -> None:
    assert route.source_key_data_json is not None
    if (
        replay["version_kind"] != "CDC"
        or replay["source_table"] != route.source_table
        or not _same_json(
            replay["source_field_types"], route.source_field_types
        )
        or replay["source_key"] != route.source_key
        or not _same_json(
            replay["source_key_data"],
            json.loads(route.source_key_data_json),
        )
        or replay["source_key_type"] != route.source_key_type
        or replay["source_key_numeric"] != route.source_key_numeric
        or replay["source_key_text"] != route.source_key_text
        or replay["source_schema_profile_id"]
        != route.source_schema_profile_id
        or replay["operation"] != route.operation
        or replay["source_timestamp"] != source_timestamp
        or replay["record_id_type"] != "numeric"
        or replay["record_id_numeric"] != Decimal(event.record_id)
        or replay["record_id_text"] is not None
        or not _same_json(replay["source_position"], position)
        or replay["protected_source_row_hash"]
        != route.protected_payload_hash
        or replay["source_row_revision"] is None
    ):
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_VERSION_IDENTITY_CONFLICT"
        )


def _read_current_for_update(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_table: str,
    source_key: str,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT source_region, source_table, source_key, source_key_data,
                   dependency_keys, source_row, is_deleted, source_timestamp,
                   last_record_id, source_position, last_topic, last_partition,
                   last_offset, row_version, source_row_revision,
                   last_source_partition_epoch_id, last_version_kind,
                   source_position_v2, record_id_type, record_id_numeric,
                   record_id_text, source_timestamp_v2, source_payload_hash,
                   provenance_state, source_key_type, source_key_numeric,
                   source_key_text, source_schema_profile_id,
                   source_field_types
            FROM public.dts_source_rows
            WHERE source_region = :source_region
              AND source_table = :source_table
              AND source_key = :source_key
            FOR UPDATE
            """
        ),
        {
            "source_region": event.source_region,
            "source_table": source_table,
            "source_key": source_key,
        },
    ).mappings().one_or_none()


def _current_transition_matches(
    connection: Connection,
    *,
    stored_current: Mapping[str, Any],
    route: V2SourceRouteDecision,
) -> bool:
    previous = connection.execute(
        text(
            """
            SELECT operation, before_row, after_row, source_row_revision
            FROM public.dts_source_row_versions
            WHERE source_region = :source_region
              AND source_partition_epoch_id = :epoch_id
              AND topic = :topic
              AND partition_id = :partition_id
              AND offset_value = :offset_value
            """
        ),
        {
            "source_region": stored_current["source_region"],
            "epoch_id": stored_current["last_source_partition_epoch_id"],
            "topic": stored_current["last_topic"],
            "partition_id": stored_current["last_partition"],
            "offset_value": stored_current["last_offset"],
        },
    ).mappings().one_or_none()
    return bool(
        previous is not None
        and previous["operation"] == "UPDATE"
        and previous["source_row_revision"]
        == stored_current["source_row_revision"]
        and _same_json(previous["before_row"], route.before_row)
        and _same_json(previous["after_row"], route.after_row)
    )


def _validated_current(
    row: Mapping[str, Any],
    *,
    event: DtsChangeEvent,
) -> V2ValidatedCurrentRow:
    position = row.get("source_position_v2")
    revision = row.get("source_row_revision")
    source_hash = row.get("source_payload_hash")
    if (
        row.get("provenance_state") != "V2_CONFIRMED"
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not row.get("last_source_partition_epoch_id")
        or row.get("last_version_kind") not in {"CDC", "SNAPSHOT_DIFF"}
        or not isinstance(position, Mapping)
        or row.get("record_id_type") not in {"numeric", "text", "none"}
        or not isinstance(source_hash, str)
        or _SHA256_PATTERN.fullmatch(source_hash) is None
        or not isinstance(row.get("source_key_data"), Mapping)
        or not isinstance(row.get("source_field_types"), Mapping)
    ):
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        )
    if row["record_id_type"] == "numeric":
        if row.get("record_id_numeric") is None or row.get("record_id_text") is not None:
            raise DtsV2ShadowSourceWriterError(
                "SOURCE_CURRENT_PROVENANCE_REJECTED"
            )
    elif row["record_id_type"] == "text":
        if row.get("record_id_text") is None or row.get("record_id_numeric") is not None:
            raise DtsV2ShadowSourceWriterError(
                "SOURCE_CURRENT_PROVENANCE_REJECTED"
            )
    elif row.get("record_id_numeric") is not None or row.get("record_id_text") is not None:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        )
    if row.get("source_key_type") == "NUMERIC":
        key_union_valid = (
            row.get("source_key_numeric") is not None
            and row.get("source_key_text") is None
        )
    elif row.get("source_key_type") == "TEXT":
        key_union_valid = (
            row.get("source_key_numeric") is None
            and row.get("source_key_text") == row.get("source_key")
        )
    else:
        key_union_valid = False
    if not key_union_valid:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        )
    try:
        current = V2ValidatedCurrentRow(
            source_region=row["source_region"],
            source_table=row["source_table"],
            source_key=row["source_key"],
            source_key_type=row["source_key_type"],
            source_schema_profile_id=row["source_schema_profile_id"],
            source_field_types=row["source_field_types"],
            row=row["source_row"],
            provenance_state=row["provenance_state"],
        )
    except (DtsRecordError, TypeError, ValueError) as exc:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        ) from exc
    key_data = row["source_key_data"]
    if set(key_data) != {"id"} or _canonical_typed_id(
        key_data.get("id"),
        current.source_key_type,
    ) != current.source_key:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        )
    if row["source_region"] != event.source_region:
        raise DtsV2ShadowSourceWriterError(
            "SOURCE_CURRENT_PROVENANCE_REJECTED"
        )
    return current


def _is_legacy_pending_current(row: Mapping[str, Any]) -> bool:
    """Accept a pre-H0 v1 row only as an untrusted identity placeholder.

    Its business image is never merged into v2.  A complete first UPDATE or
    DELETE must reconstruct the v2 current row from the event images; a sparse
    event therefore remains fail-closed in ``build_v2_source_route``.
    """

    if row.get("provenance_state") not in {None, "LEGACY_PENDING"}:
        return False
    return all(
        row.get(field) is None
        for field in (
            "source_row_revision",
            "last_source_partition_epoch_id",
            "last_version_kind",
            "source_position_v2",
            "record_id_type",
            "record_id_numeric",
            "record_id_text",
            "source_timestamp_v2",
            "source_payload_hash",
            "source_key_type",
            "source_key_numeric",
            "source_key_text",
            "source_schema_profile_id",
            "source_field_types",
        )
    )


def _same_json(left: Any, right: Any) -> bool:
    return _canonical_json(left) == _canonical_json(right)


def _canonical_json(value: Any) -> str:
    def normalized(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): normalized(child)
                for key, child in sorted(item.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(item, (list, tuple)):
            return [normalized(child) for child in item]
        if isinstance(item, Decimal):
            rendered = format(item, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return rendered or "0"
        if isinstance(item, (datetime,)):
            return item.isoformat()
        return item

    return json.dumps(
        normalized(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _is_semantic_noop(
    *,
    operation: str,
    route: V2SourceRouteDecision,
    stored_current: Mapping[str, Any] | None,
) -> bool:
    if stored_current is None:
        return False
    if operation == "UPDATE":
        return not stored_current["is_deleted"] and _same_json(
            route.after_row,
            stored_current["source_row"],
        )
    if operation == "DELETE":
        return bool(stored_current["is_deleted"])
    return False


def _canonical_typed_id(value: Any, source_type: Any) -> str:
    if source_type == "TEXT":
        if not isinstance(value, str) or not value:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_DEPENDENCY_ID_INVALID"
            )
        return value
    if source_type != "NUMERIC" or isinstance(value, bool) or value is None:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_DEPENDENCY_ID_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_DEPENDENCY_ID_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_DEPENDENCY_ID_INVALID"
        )
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _source_dependency_keys_for_image(
    route: V2SourceRouteDecision,
    source_region: str,
    image: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """Build protected reverse keys for one complete protected image.

    Returning one document per image preserves real teacher/student pairs;
    callers can union documents without inventing an old-teacher/new-student
    cross product.
    """

    assert route.source_key is not None
    assert isinstance(route.source_field_types, Mapping)
    assert route.source_table is not None
    table_prefix = f"{source_region}_"
    if not route.source_table.startswith(table_prefix):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_TABLE_NOT_PROFILED"
        )
    suffix = route.source_table.removeprefix(table_prefix)
    course_ids: set[str] = set()
    teachers: set[str] = set()
    students: set[str] = set()
    label_ids: set[str] = set()
    category_ids: set[str] = set()
    if image is not None:
        def add_typed(target: set[str], *field_names: str) -> None:
            for field_name in field_names:
                value = image.get(field_name)
                if value is None:
                    continue
                target.add(
                    _canonical_typed_id(
                        value,
                        route.source_field_types.get(field_name),
                    )
                )

        if suffix == "appoint":
            course_ids.add(route.source_key)
        else:
            add_typed(course_ids, "appoint_id")

        if suffix == "teacher":
            teachers.add(route.source_key)
        add_typed(teachers, "t_id", "teacher_id", "tea_id")

        token = image.get("student_token")
        if token is not None:
            if not isinstance(token, str) or not token:
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_STUDENT_TOKEN_INVALID"
                )
            if (
                source_region == "dom"
                and _DOM_STUDENT_TOKEN_PATTERN.fullmatch(token) is None
            ):
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_STUDENT_TOKEN_INVALID"
                )
            students.add(token)
        elif source_region == "ovs":
            add_typed(students, "s_id", "stu_id", "student_id", "user_id")

        if suffix == "grading_label":
            label_ids.add(route.source_key)
        add_typed(label_ids, "label_id")

        if suffix == "complaint_cate":
            category_ids.add(route.source_key)
        add_typed(
            category_ids,
            "cate_parent",
            "complaint_type",
            "complaint_type_child",
            "complaint_type_grandson",
            "tag_id",
        )

    # user_complaint is retained for source audit only in the frozen v2 rule;
    # it must not create business dirty fanout.
    if suffix == "user_complaint":
        course_ids.clear()
        teachers.clear()
        students.clear()
        label_ids.clear()
        category_ids.clear()
    return {
        "course_ids": sorted(course_ids),
        "teacher_ids": sorted(teachers),
        "student_subjects": sorted(students),
        "label_ids": sorted(label_ids),
        "category_ids": sorted(category_ids),
    }


def _source_dependency_keys(
    route: V2SourceRouteDecision,
    source_region: str,
) -> dict[str, list[str]]:
    """Build reverse keys for the authoritative current/tombstone image."""

    image = route.before_row if route.operation == "DELETE" else route.after_row
    return _source_dependency_keys_for_image(route, source_region, image)


def _apply_cdc_membership_overlay(
    connection: Connection,
    *,
    source_region: str,
    source_table: str,
    source_key: str,
    source_row_revision: int,
    before_dependency_keys: Mapping[str, Any],
    after_dependency_keys: Mapping[str, Any],
    after_is_present: bool,
) -> None:
    response = connection.execute(
        text(
            """
            SELECT public.scope_membership_apply_cdc_v3(
                :source_region,:source_table,:source_key,
                :source_row_revision,CAST(:before_dependencies AS jsonb),
                CAST(:after_dependencies AS jsonb),:after_is_present
            )
            """
        ),
        {
            "source_region": source_region,
            "source_table": source_table,
            "source_key": source_key,
            "source_row_revision": source_row_revision,
            "before_dependencies": _json_dump(before_dependency_keys),
            "after_dependencies": _json_dump(after_dependency_keys),
            "after_is_present": after_is_present,
        },
    ).scalar_one()
    if (
        not isinstance(response, Mapping)
        or response.get("status") != "APPLIED"
        or response.get("source_row_revision") != source_row_revision
    ):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SOURCE_MEMBERSHIP_OVERLAY_REJECTED"
        )


def _apply_cdc_membership_overlays_batch(
    connection: Connection,
    *,
    writes: Sequence[DtsV2ShadowDeferredWrite],
) -> None:
    if not writes:
        return
    payload = [
        {
            "ordinal": ordinal,
            "source_region": write.event.source_region,
            "source_table": write.route.source_table,
            "source_key": write.route.source_key,
            "source_row_revision": write.revision,
            "before_dependencies": write.before_dependency_keys,
            "after_dependencies": write.after_dependency_keys,
            "after_is_present": write.event.operation != "DELETE",
        }
        for ordinal, write in enumerate(writes)
    ]
    total, accepted = connection.execute(
        text(
            """
            WITH inputs AS MATERIALIZED (
              SELECT *
              FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
                ordinal integer,source_region text,source_table text,
                source_key text,source_row_revision bigint,
                before_dependencies jsonb,after_dependencies jsonb,
                after_is_present boolean
              )
            ), applied AS MATERIALIZED (
              SELECT ordinal,source_row_revision,
                     public.scope_membership_apply_cdc_v3(
                       source_region,source_table,source_key,
                       source_row_revision,before_dependencies,
                       after_dependencies,after_is_present
                     ) response
              FROM inputs
              ORDER BY ordinal
            )
            SELECT count(*),count(*) FILTER (
              WHERE response ->> 'status' = 'APPLIED'
                AND (response ->> 'source_row_revision')::bigint =
                    source_row_revision
            )
            FROM applied
            """
        ),
        {
            "records": json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        },
    ).one()
    if total != len(writes) or accepted != len(writes):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SOURCE_MEMBERSHIP_OVERLAY_REJECTED"
        )


def _dirty_keys_for_route(
    route: V2SourceRouteDecision,
    source_region: str,
) -> tuple[DirtyKeyV2, ...]:
    """Return the exact dirty fanout for one complete source revision.

    Unlike ``dependency_keys`` on source current, ingest dirtiness is the
    union of the fully reconstructed before and after images.  That is what
    makes sparse ownership changes invalidate both the old and new aggregate
    without making the old owner a dependency of current.
    """

    assert route.source_table is not None
    assert route.source_key is not None
    assert route.source_key_type is not None
    assert isinstance(route.source_field_types, Mapping)
    prefix = f"{source_region}_"
    if not route.source_table.startswith(prefix):
        raise DtsV2ShadowSourceWriterError(
            "DTS_V2_SHADOW_SOURCE_TABLE_NOT_PROFILED"
        )
    suffix = route.source_table.removeprefix(prefix)
    values: set[tuple[str, str, str, str]] = set()

    def add_key(
        region: str,
        key_type: str,
        part_1: str,
        part_2: str = "",
    ) -> None:
        try:
            key = DirtyKeyV2(region, key_type, part_1, part_2)
        except DtsV2DirtyQueueError as exc:
            raise DtsV2ShadowSourceWriterError(str(exc)) from exc
        values.add(
            (key.source_region, key.key_type, key.key_part_1, key.key_part_2)
        )

    def typed(image: Mapping[str, Any], field_name: str) -> str | None:
        value = image.get(field_name)
        if value is None:
            return None
        return _canonical_typed_id(
            value,
            route.source_field_types.get(field_name),
        )

    def first_typed(
        image: Mapping[str, Any],
        *field_names: str,
    ) -> str | None:
        resolved: list[str] = []
        for field_name in field_names:
            value = typed(image, field_name)
            if value is not None:
                resolved.append(value)
        if not resolved:
            return None
        if any(value != resolved[0] for value in resolved[1:]):
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_DEPENDENCY_ID_CONFLICT"
            )
        return resolved[0]

    def student_subject(image: Mapping[str, Any]) -> str | None:
        token = image.get("student_token")
        if token is not None:
            if not isinstance(token, str) or not token:
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_STUDENT_TOKEN_INVALID"
                )
            if (
                source_region == "dom"
                and _DOM_STUDENT_TOKEN_PATTERN.fullmatch(token) is None
            ):
                raise DtsV2ShadowSourceWriterError(
                    "DTS_V2_SHADOW_STUDENT_TOKEN_INVALID"
                )
            return token
        if source_region == "dom":
            # A domestic relationship may only be addressed by its protected
            # token.  Raw student aliases are never a fallback dirty identity.
            return None
        return first_typed(
            image,
            "s_id",
            "stu_id",
            "student_id",
            "user_id",
        )

    images = tuple(
        image
        for image in (route.before_row, route.after_row)
        if isinstance(image, Mapping)
    )

    if suffix == "appoint":
        add_key(source_region, "COURSE", route.source_key)
        for image in images:
            teacher_id = typed(image, "t_id")
            if teacher_id is not None:
                add_key(source_region, "TEACHER", teacher_id)
            student_id = student_subject(image)
            if teacher_id is not None and student_id is not None:
                # Completion freeze/time/student changes affect the end+24h
                # observation even when no favorite row changed.  Enqueue the
                # real before/after pair from each source image; never build a
                # teacher x student cross product.
                add_key(
                    source_region,
                    "TEACHER_STUDENT",
                    teacher_id,
                    student_id,
                )

    elif suffix == "teacher":
        add_key(source_region, "TEACHER", route.source_key)
        if source_region == "dom":
            # The teacher profile exists only in DOM, while TEACHER aggregates
            # are regional and the global materializer requires both peers.
            # One profile revision must therefore create/refresh the empty OVS
            # peer instead of leaving a DOM-only teacher permanently blocked.
            add_key("ovs", "TEACHER", route.source_key)

    elif suffix in {"teacher_favorite", "teacher_blacklist"}:
        for image in images:
            teacher_id = first_typed(image, "t_id", "tea_id", "teacher_id")
            student_id = student_subject(image)
            if teacher_id is not None:
                # Relationship current contributes to teacher-wide distinct
                # favorite/block counts even when the pair has no lesson.
                add_key(source_region, "TEACHER", teacher_id)
            if teacher_id is not None and student_id is not None:
                # Build each old/new pair from one image.  Cross-product fanout
                # would invent relationships that never existed.
                add_key(
                    source_region,
                    "TEACHER_STUDENT",
                    teacher_id,
                    student_id,
                )

    elif suffix in {"teacher_class_schedule", "teacher_certification"}:
        for image in images:
            teacher_id = typed(image, "teacher_id")
            if teacher_id is not None:
                add_key(source_region, "TEACHER", teacher_id)

    elif suffix == "grading_label":
        add_key(source_region, "LABEL", route.source_key)

    elif suffix == "grading_label_log":
        for image in images:
            course_id = typed(image, "appoint_id")
            label_id = typed(image, "label_id")
            if course_id is not None:
                add_key(source_region, "COURSE", course_id)
            if label_id is not None:
                add_key(source_region, "LABEL", label_id)

    elif suffix == "complaint_cate":
        if source_region != "dom":
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_COMPLAINT_CATEGORY_REGION_INVALID"
            )
        add_key("dom", "COMPLAINT_CATEGORY", route.source_key)

    elif suffix == "user_complaint":
        # Frozen audit-only source.  It advances source current and the ingest
        # checkpoint but has no domain dirty fanout.
        pass

    else:
        # Remaining profiled sources are course children.
        for image in images:
            course_id = typed(image, "appoint_id")
            if course_id is not None:
                add_key(source_region, "COURSE", course_id)
            teacher_id = first_typed(image, "t_id", "tea_id", "teacher_id")
            if teacher_id is not None:
                add_key(source_region, "TEACHER", teacher_id)
            if suffix == "complaint":
                for field_name in (
                    "complaint_type",
                    "complaint_type_child",
                    "complaint_type_grandson",
                ):
                    category_id = typed(image, field_name)
                    if category_id is not None:
                        add_key(
                            "dom",
                            "COMPLAINT_CATEGORY",
                            category_id,
                        )

    return tuple(
        DirtyKeyV2(*identity)
        for identity in sorted(values)
    )


def _append_version(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
    route: V2SourceRouteDecision,
    position: Mapping[str, Any],
    source_timestamp: datetime,
    revision: int,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_row_versions (
                source_region, source_partition_epoch_id, topic, partition_id,
                offset_value, version_kind, source_table,
                source_schema_profile_id, source_field_types, source_key,
                source_key_data, source_key_type, source_key_numeric,
                source_key_text, operation, before_row, after_row,
                source_timestamp, record_id_type, record_id_numeric,
                record_id_text, source_position, source_row_revision,
                snapshot_id, snapshot_as_of, covered_through_offsets,
                diff_step, source_table_publish_generation,
                protected_source_row_hash
            ) VALUES (
                :source_region, :epoch_id, :topic, :partition_id,
                :offset_value, 'CDC', :source_table, :profile_id,
                CAST(:source_field_types AS jsonb), :source_key,
                CAST(:source_key_data AS jsonb), :source_key_type,
                :source_key_numeric, :source_key_text, :operation,
                CAST(:before_row AS jsonb), CAST(:after_row AS jsonb),
                :source_timestamp, 'numeric', :record_id_numeric, NULL,
                CAST(:source_position AS jsonb), :source_row_revision,
                NULL, NULL, NULL, NULL, NULL, :payload_hash
            )
            """
        ),
        _write_parameters(
            event=event,
            source_partition_epoch_id=source_partition_epoch_id,
            route=route,
            position=position,
            source_timestamp=source_timestamp,
            revision=revision,
        ),
    )


def _append_versions_batch(
    connection: Connection,
    *,
    source_partition_epoch_id: str,
    writes: Sequence[DtsV2ShadowDeferredWrite],
) -> None:
    if not writes:
        return
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_row_versions (
                source_region, source_partition_epoch_id, topic, partition_id,
                offset_value, version_kind, source_table,
                source_schema_profile_id, source_field_types, source_key,
                source_key_data, source_key_type, source_key_numeric,
                source_key_text, operation, before_row, after_row,
                source_timestamp, record_id_type, record_id_numeric,
                record_id_text, source_position, source_row_revision,
                snapshot_id, snapshot_as_of, covered_through_offsets,
                diff_step, source_table_publish_generation,
                protected_source_row_hash
            ) VALUES (
                :source_region, :epoch_id, :topic, :partition_id,
                :offset_value, 'CDC', :source_table, :profile_id,
                CAST(:source_field_types AS jsonb), :source_key,
                CAST(:source_key_data AS jsonb), :source_key_type,
                :source_key_numeric, :source_key_text, :operation,
                CAST(:before_row AS jsonb), CAST(:after_row AS jsonb),
                :source_timestamp, 'numeric', :record_id_numeric, NULL,
                CAST(:source_position AS jsonb), :source_row_revision,
                NULL, NULL, NULL, NULL, NULL, :payload_hash
            )
            """
        ),
        [
            _write_parameters(
                event=write.event,
                source_partition_epoch_id=source_partition_epoch_id,
                route=write.route,
                position=write.position,
                source_timestamp=write.source_timestamp,
                revision=write.revision,
            )
            for write in writes
        ],
    )


def _write_current(
    connection: Connection,
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
    route: V2SourceRouteDecision,
    position: Mapping[str, Any],
    source_timestamp: datetime,
    revision: int,
    dependency_keys: Mapping[str, Any],
    stored_current: Mapping[str, Any] | None,
) -> None:
    parameters = _current_write_parameters(
        event=event,
        source_partition_epoch_id=source_partition_epoch_id,
        route=route,
        position=position,
        source_timestamp=source_timestamp,
        revision=revision,
        dependency_keys=dependency_keys,
        stored_current=stored_current,
    )
    if stored_current is None:
        sql = """
            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version, source_row_revision,
                last_source_partition_epoch_id, last_version_kind,
                source_position_v2, record_id_type, record_id_numeric,
                record_id_text, source_timestamp_v2, source_payload_hash,
                provenance_state, source_key_type, source_key_numeric,
                source_key_text, source_schema_profile_id, source_field_types
            ) VALUES (
                :source_region, :source_table, :source_key,
                CAST(:source_key_data AS jsonb),
                CAST(:dependency_keys AS jsonb), CAST(:source_row AS jsonb),
                :is_deleted, :legacy_source_timestamp, :legacy_record_id,
                :legacy_source_position, :topic, :partition_id, :offset_value,
                :row_version, :source_row_revision, :epoch_id, 'CDC',
                CAST(:source_position AS jsonb), 'numeric',
                :record_id_numeric, NULL, :source_timestamp, :payload_hash,
                'V2_CONFIRMED', :source_key_type, :source_key_numeric,
                :source_key_text, :profile_id,
                CAST(:source_field_types AS jsonb)
            )
        """
    else:
        sql = """
            UPDATE public.dts_source_rows
            SET source_key_data = CAST(:source_key_data AS jsonb),
                dependency_keys = CAST(:dependency_keys AS jsonb),
                source_row = CAST(:source_row AS jsonb),
                is_deleted = :is_deleted,
                source_timestamp = :legacy_source_timestamp,
                last_record_id = :legacy_record_id,
                source_position = :legacy_source_position,
                last_topic = :topic,
                last_partition = :partition_id,
                last_offset = :offset_value,
                row_version = :row_version,
                updated_at = clock_timestamp(),
                source_row_revision = :source_row_revision,
                last_source_partition_epoch_id = :epoch_id,
                last_version_kind = 'CDC',
                source_position_v2 = CAST(:source_position AS jsonb),
                record_id_type = 'numeric',
                record_id_numeric = :record_id_numeric,
                record_id_text = NULL,
                source_timestamp_v2 = :source_timestamp,
                source_payload_hash = :payload_hash,
                provenance_state = 'V2_CONFIRMED',
                source_key_type = :source_key_type,
                source_key_numeric = :source_key_numeric,
                source_key_text = :source_key_text,
                source_schema_profile_id = :profile_id,
                source_field_types = CAST(:source_field_types AS jsonb)
            WHERE source_region = :source_region
              AND source_table = :source_table
              AND source_key = :source_key
        """
    connection.execute(text(sql), parameters)


def _current_write_parameters(
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
    route: V2SourceRouteDecision,
    position: Mapping[str, Any],
    source_timestamp: datetime,
    revision: int,
    dependency_keys: Mapping[str, Any],
    stored_current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current_row = (
        route.before_row if event.operation == "DELETE" else route.after_row
    )
    assert current_row is not None
    parameters = _write_parameters(
        event=event,
        source_partition_epoch_id=source_partition_epoch_id,
        route=route,
        position=position,
        source_timestamp=source_timestamp,
        revision=revision,
    )
    parameters.update(
        {
            "dependency_keys": _json_dump(dependency_keys),
            "source_row": _json_dump(current_row),
            "is_deleted": event.operation == "DELETE",
            "legacy_source_position": _json_dump(position),
            "row_version": (
                1
                if stored_current is None
                else int(stored_current["row_version"]) + 1
            ),
        }
    )
    return parameters


def _upsert_currents_batch(
    connection: Connection,
    *,
    source_partition_epoch_id: str,
    writes: Sequence[DtsV2ShadowDeferredWrite],
    initial_currents: Mapping[
        tuple[str, str, str], Mapping[str, Any]
    ],
) -> None:
    if not writes:
        return
    parameters: list[dict[str, Any]] = []
    for write in writes:
        identity = (
            write.event.source_region,
            write.route.source_table or "",
            write.route.source_key or "",
        )
        parameters.append(
            _current_write_parameters(
                event=write.event,
                source_partition_epoch_id=source_partition_epoch_id,
                route=write.route,
                position=write.position,
                source_timestamp=write.source_timestamp,
                revision=write.revision,
                dependency_keys=write.dependency_keys,
                stored_current=initial_currents.get(identity),
            )
        )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version, source_row_revision,
                last_source_partition_epoch_id, last_version_kind,
                source_position_v2, record_id_type, record_id_numeric,
                record_id_text, source_timestamp_v2, source_payload_hash,
                provenance_state, source_key_type, source_key_numeric,
                source_key_text, source_schema_profile_id, source_field_types
            ) VALUES (
                :source_region, :source_table, :source_key,
                CAST(:source_key_data AS jsonb),
                CAST(:dependency_keys AS jsonb), CAST(:source_row AS jsonb),
                :is_deleted, :legacy_source_timestamp, :legacy_record_id,
                :legacy_source_position, :topic, :partition_id, :offset_value,
                :row_version, :source_row_revision, :epoch_id, 'CDC',
                CAST(:source_position AS jsonb), 'numeric',
                :record_id_numeric, NULL, :source_timestamp, :payload_hash,
                'V2_CONFIRMED', :source_key_type, :source_key_numeric,
                :source_key_text, :profile_id,
                CAST(:source_field_types AS jsonb)
            )
            ON CONFLICT (source_region, source_table, source_key) DO UPDATE
            SET source_key_data = EXCLUDED.source_key_data,
                dependency_keys = EXCLUDED.dependency_keys,
                source_row = EXCLUDED.source_row,
                is_deleted = EXCLUDED.is_deleted,
                source_timestamp = EXCLUDED.source_timestamp,
                last_record_id = EXCLUDED.last_record_id,
                source_position = EXCLUDED.source_position,
                last_topic = EXCLUDED.last_topic,
                last_partition = EXCLUDED.last_partition,
                last_offset = EXCLUDED.last_offset,
                row_version = EXCLUDED.row_version,
                updated_at = clock_timestamp(),
                source_row_revision = EXCLUDED.source_row_revision,
                last_source_partition_epoch_id =
                    EXCLUDED.last_source_partition_epoch_id,
                last_version_kind = EXCLUDED.last_version_kind,
                source_position_v2 = EXCLUDED.source_position_v2,
                record_id_type = EXCLUDED.record_id_type,
                record_id_numeric = EXCLUDED.record_id_numeric,
                record_id_text = EXCLUDED.record_id_text,
                source_timestamp_v2 = EXCLUDED.source_timestamp_v2,
                source_payload_hash = EXCLUDED.source_payload_hash,
                provenance_state = EXCLUDED.provenance_state,
                source_key_type = EXCLUDED.source_key_type,
                source_key_numeric = EXCLUDED.source_key_numeric,
                source_key_text = EXCLUDED.source_key_text,
                source_schema_profile_id =
                    EXCLUDED.source_schema_profile_id,
                source_field_types = EXCLUDED.source_field_types
            """
        ),
        parameters,
    )


def _write_parameters(
    *,
    event: DtsChangeEvent,
    source_partition_epoch_id: str,
    route: V2SourceRouteDecision,
    position: Mapping[str, Any],
    source_timestamp: datetime,
    revision: int,
) -> dict[str, Any]:
    assert route.source_key_data_json is not None
    assert route.source_field_types is not None
    assert route.protected_payload_hash is not None
    return {
        "source_region": event.source_region,
        "epoch_id": source_partition_epoch_id,
        "topic": event.topic,
        "partition_id": event.partition,
        "offset_value": event.offset,
        "source_table": route.source_table,
        "profile_id": route.source_schema_profile_id,
        "source_field_types": _json_dump(route.source_field_types),
        "source_key": route.source_key,
        "source_key_data": route.source_key_data_json,
        "source_key_type": route.source_key_type,
        "source_key_numeric": route.source_key_numeric,
        "source_key_text": route.source_key_text,
        "operation": route.operation,
        "before_row": _json_dump(route.before_row),
        "after_row": _json_dump(route.after_row),
        "source_timestamp": source_timestamp,
        "record_id_numeric": Decimal(event.record_id),
        "source_position": _json_dump(position),
        "source_row_revision": revision,
        "payload_hash": route.protected_payload_hash,
        "legacy_source_timestamp": event.source_timestamp,
        "legacy_record_id": event.record_id,
    }


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _result(
    status: str,
    *,
    event: DtsChangeEvent,
    route: V2SourceRouteDecision,
    revision: Any,
) -> DtsV2ShadowSourceWriteResult:
    assert route.source_table is not None
    assert route.source_key is not None
    assert route.source_key_type is not None
    assert route.protected_payload_hash is not None
    return DtsV2ShadowSourceWriteResult(
        status=status,
        source_region=event.source_region,
        source_table=route.source_table,
        source_key=route.source_key,
        source_key_type=route.source_key_type,
        source_row_revision=(None if revision is None else int(revision)),
        protected_payload_hash=route.protected_payload_hash,
        dirty_keys=_dirty_keys_for_route(route, event.source_region),
    )


def _ignored_missing_current_result(
    *,
    event: DtsChangeEvent,
    source_key: str,
    source_key_type: str,
) -> DtsV2ShadowSourceWriteResult:
    table = event.table_name or ""
    payload_hash = hashlib.sha256(
        _canonical_json(
            {
                "contract": "dts-v2-ignore-missing-source-current-v1",
                "source_region": event.source_region,
                "source_table": table,
                "source_key": source_key,
                "source_key_type": source_key_type,
                "operation": event.operation,
                "before": event.before,
                "after": event.after,
                "source_field_types": event.source_field_types,
            }
        ).encode("utf-8")
    ).hexdigest()
    return DtsV2ShadowSourceWriteResult(
        status="IGNORED_MISSING_CURRENT",
        source_region=event.source_region,
        source_table=table,
        source_key=source_key,
        source_key_type=source_key_type,
        source_row_revision=None,
        protected_payload_hash=payload_hash,
        dirty_keys=(),
    )
