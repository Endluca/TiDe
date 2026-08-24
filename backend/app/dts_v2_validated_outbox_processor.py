"""Outbox handler for aggregates whose business impact is owned elsewhere.

PARTICIPATION, LABEL and COMPLAINT_CATEGORY changes always fan out a COURSE
dirty key in the domain projection transaction.  SOURCE_SCOPE completion
independently fans out the scope members.  Their own immutable Outbox rows are
therefore audit/coverage roots, not a second writer of the same business rows.

This processor is intentionally not a blind no-op: it locks and hash-validates
the current aggregate, rejects a future/mismatched event, and records whether
the immutable event was superseded by a newer revision before it can be marked
PUBLISHED by the shared worker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.engine import Connection

from .dts_v2_aggregate_state_reader import DtsV2AggregateStateReader
from .dts_v2_outbox_worker import DtsV2OutboxEvent


_VALIDATED_AGGREGATE_TYPES = frozenset(
    {"PARTICIPATION", "LABEL", "COMPLAINT_CATEGORY", "SOURCE_SCOPE"}
)


class DtsV2ValidatedOutboxProcessorError(RuntimeError):
    """An audit-only aggregate event is not safe to acknowledge."""


class DtsV2ValidatedOutboxProcessor:
    """Validate an immutable aggregate revision with no duplicate writer."""

    def __init__(
        self,
        *,
        aggregate_types: Iterable[str] = _VALIDATED_AGGREGATE_TYPES,
        aggregate_reader: DtsV2AggregateStateReader | None = None,
    ) -> None:
        normalized = frozenset(aggregate_types)
        if not normalized or not normalized.issubset(
            _VALIDATED_AGGREGATE_TYPES
        ):
            raise DtsV2ValidatedOutboxProcessorError(
                "DTS_V2_VALIDATED_OUTBOX_TYPE_SET_INVALID"
            )
        self.aggregate_types = normalized
        self.aggregate_reader = aggregate_reader or DtsV2AggregateStateReader()

    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]:
        if (
            not isinstance(event, DtsV2OutboxEvent)
            or event.event_type != "source_wide.changed.v2"
            or event.aggregate_type not in self.aggregate_types
        ):
            raise DtsV2ValidatedOutboxProcessorError(
                "DTS_V2_VALIDATED_OUTBOX_EVENT_REQUIRED"
            )
        snapshot = self.aggregate_reader.read_current(connection, event)
        if snapshot.aggregate_type != event.aggregate_type:
            raise DtsV2ValidatedOutboxProcessorError(
                "DTS_V2_VALIDATED_OUTBOX_SNAPSHOT_TYPE_MISMATCH"
            )
        return {
            "validated_snapshots": 1,
            "superseded_events": int(snapshot.is_superseded_event),
        }


__all__ = [
    "DtsV2ValidatedOutboxProcessor",
    "DtsV2ValidatedOutboxProcessorError",
]
