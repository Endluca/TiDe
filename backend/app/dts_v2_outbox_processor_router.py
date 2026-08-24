"""Fail-closed dispatch for every DTS v2 Outbox aggregate type."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy.engine import Connection

from .dts_v2_outbox_worker import DtsV2OutboxEvent


OUTBOX_PROCESSOR_AGGREGATE_TYPES = frozenset(
    {
        "COURSE",
        "PARTICIPATION",
        "TEACHER",
        "TEACHER_STUDENT",
        "LABEL",
        "COMPLAINT_CATEGORY",
        "COMPLETION_CONFLICT",
        "SOURCE_SCOPE",
        "TASK_PLAN",
    }
)


class DtsV2AggregateOutboxProcessor(Protocol):
    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]: ...


class DtsV2OutboxProcessorRouterError(RuntimeError):
    """The configured v2 downstream ownership matrix is unsafe."""


class DtsV2OutboxProcessorRouter:
    """Dispatch an event to exactly one aggregate owner.

    Production construction requires the full matrix.  Tests and shadow runs
    may opt into a partial matrix, but receiving an unregistered aggregate
    still fails instead of silently publishing its Outbox row.
    """

    def __init__(
        self,
        processors: Mapping[str, DtsV2AggregateOutboxProcessor],
        *,
        require_complete_matrix: bool = True,
    ) -> None:
        if not isinstance(processors, Mapping):
            raise DtsV2OutboxProcessorRouterError(
                "DTS_V2_OUTBOX_PROCESSOR_MATRIX_INVALID"
            )
        keys = frozenset(processors)
        if not keys or not keys.issubset(OUTBOX_PROCESSOR_AGGREGATE_TYPES):
            raise DtsV2OutboxProcessorRouterError(
                "DTS_V2_OUTBOX_PROCESSOR_MATRIX_INVALID"
            )
        if require_complete_matrix and keys != OUTBOX_PROCESSOR_AGGREGATE_TYPES:
            missing = ",".join(sorted(OUTBOX_PROCESSOR_AGGREGATE_TYPES - keys))
            raise DtsV2OutboxProcessorRouterError(
                f"DTS_V2_OUTBOX_PROCESSOR_MATRIX_INCOMPLETE:{missing}"
            )
        self._processors = dict(processors)

    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]:
        if not isinstance(event, DtsV2OutboxEvent):
            raise DtsV2OutboxProcessorRouterError(
                "DTS_V2_OUTBOX_EVENT_REQUIRED"
            )
        processor = self._processors.get(event.aggregate_type)
        if processor is None:
            raise DtsV2OutboxProcessorRouterError(
                "DTS_V2_OUTBOX_PROCESSOR_UNAVAILABLE:"
                f"{event.aggregate_type}"
            )
        result = processor.process_event(connection, event)
        if not isinstance(result, Mapping):
            raise DtsV2OutboxProcessorRouterError(
                "DTS_V2_OUTBOX_PROCESSOR_RESULT_INVALID"
            )
        normalized: dict[str, int] = {}
        namespace = event.aggregate_type.lower()
        for raw_name, raw_count in result.items():
            if (
                not isinstance(raw_name, str)
                or not raw_name
                or type(raw_count) is not int
                or raw_count < 0
            ):
                raise DtsV2OutboxProcessorRouterError(
                    "DTS_V2_OUTBOX_PROCESSOR_RESULT_INVALID"
                )
            normalized[f"{namespace}.{raw_name}"] = raw_count
        return normalized


__all__ = [
    "DtsV2AggregateOutboxProcessor",
    "DtsV2OutboxProcessorRouter",
    "DtsV2OutboxProcessorRouterError",
    "OUTBOX_PROCESSOR_AGGREGATE_TYPES",
]
