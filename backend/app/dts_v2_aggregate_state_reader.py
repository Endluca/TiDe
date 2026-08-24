"""Authoritative aggregate snapshot reads for DTS v2 Outbox consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_domain_aggregate import (
    DtsV2DomainAggregateError,
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)
from .dts_v2_outbox_worker import DtsV2OutboxEvent


class DtsV2AggregateStateReaderError(RuntimeError):
    """An Outbox event does not point to a provable aggregate snapshot."""


@dataclass(frozen=True)
class DtsV2AggregateSnapshot:
    aggregate_type: str
    aggregate_id: str
    aggregate_key: Mapping[str, Any]
    aggregate_state: Mapping[str, Any]
    current_revision: int
    event_revision: int
    is_superseded_event: bool


class DtsV2AggregateStateReader:
    """Lock and validate the current aggregate behind one immutable event."""

    def read_current(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> DtsV2AggregateSnapshot:
        if not isinstance(event, DtsV2OutboxEvent):
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_REQUIRED"
            )
        payload_key = event.payload.get("aggregate_key")
        event_revision = event.payload.get("aggregate_revision")
        if not isinstance(payload_key, Mapping):
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_KEY_INVALID"
            )
        if type(event_revision) is not int or event_revision < 1:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_REVISION_INVALID"
            )
        try:
            identity = build_domain_aggregate_identity_v2(
                event.aggregate_type,
                payload_key,
            )
        except DtsV2DomainAggregateError as exc:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_KEY_INVALID"
            ) from exc
        if identity.aggregate_id != event.aggregate_id:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_IDENTITY_MISMATCH"
            )

        row = connection.execute(
            text(
                """
                SELECT aggregate_type,aggregate_id,canonical_key,
                       canonical_key_sha256,revision,aggregate_state,
                       aggregate_state_sha256
                FROM public.domain_aggregate_revisions
                WHERE aggregate_type=:aggregate_type
                  AND aggregate_id=:aggregate_id
                FOR SHARE
                """
            ),
            {
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_MISSING"
            )
        if row.get("aggregate_type") != event.aggregate_type or row.get(
            "aggregate_id"
        ) != event.aggregate_id:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_IDENTITY_MISMATCH"
            )
        canonical_key = row.get("canonical_key")
        state = row.get("aggregate_state")
        revision = row.get("revision")
        if not isinstance(canonical_key, Mapping) or dict(canonical_key) != dict(
            identity.aggregate_key
        ):
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_KEY_MISMATCH"
            )
        if type(revision) is not int or revision < 1:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_REVISION_INVALID"
            )
        if event_revision > revision:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_EVENT_REVISION_AHEAD"
            )
        if not isinstance(state, Mapping):
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_STATE_INVALID"
            )
        _, state_sha256 = canonical_domain_state_v2(state)
        if row.get("aggregate_state_sha256") != state_sha256:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_STATE_HASH_MISMATCH"
            )
        key_sha256 = identity.aggregate_id.rsplit(":", 1)[-1]
        if row.get("canonical_key_sha256") != key_sha256:
            raise DtsV2AggregateStateReaderError(
                "DTS_V2_AGGREGATE_CURRENT_KEY_HASH_MISMATCH"
            )
        return DtsV2AggregateSnapshot(
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_key=dict(identity.aggregate_key),
            aggregate_state=dict(state),
            current_revision=revision,
            event_revision=event_revision,
            is_superseded_event=event_revision < revision,
        )


__all__ = [
    "DtsV2AggregateSnapshot",
    "DtsV2AggregateStateReader",
    "DtsV2AggregateStateReaderError",
]
