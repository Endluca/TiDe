"""COURSE Outbox handler: validate current aggregate, reduce, materialize."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from sqlalchemy.engine import Connection

from .dts_v2_aggregate_state_reader import DtsV2AggregateStateReader
from .dts_v2_course_source_wide_plan import (
    CourseSourceWidePlanV2,
    build_course_source_wide_plan_v2,
)
from .dts_v2_outbox_worker import DtsV2OutboxEvent


class DtsV2CourseMaterializer(Protocol):
    def apply_course_plan(
        self,
        connection: Connection,
        plan: CourseSourceWidePlanV2,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class DtsV2CourseOutboxProcessorError(RuntimeError):
    """A COURSE event or materializer result violates the ownership contract."""


class DtsV2CourseOutboxProcessor:
    def __init__(
        self,
        *,
        materializer: DtsV2CourseMaterializer,
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
            event.aggregate_type != "COURSE"
            or event.event_type != "source_wide.changed.v2"
        ):
            raise DtsV2CourseOutboxProcessorError(
                "DTS_V2_COURSE_OUTBOX_EVENT_REQUIRED"
            )
        snapshot = self.aggregate_reader.read_current(connection, event)
        key = snapshot.aggregate_key
        plan = build_course_source_wide_plan_v2(
            source_region=str(key["source_region"]),
            source_appoint_id=str(key["source_appoint_id"]),
            aggregate_state=snapshot.aggregate_state,
        )
        result = self.materializer.apply_course_plan(
            connection,
            plan,
            aggregate_revision=snapshot.current_revision,
            triggering_event_id=event.event_id,
        )
        if not isinstance(result, Mapping):
            raise DtsV2CourseOutboxProcessorError(
                "DTS_V2_COURSE_MATERIALIZER_RESULT_INVALID"
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
                raise DtsV2CourseOutboxProcessorError(
                    "DTS_V2_COURSE_MATERIALIZER_RESULT_INVALID"
                )
            counts[name] = count
        return counts


__all__ = [
    "DtsV2CourseMaterializer",
    "DtsV2CourseOutboxProcessor",
    "DtsV2CourseOutboxProcessorError",
]
