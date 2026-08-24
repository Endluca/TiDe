from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.dts_v2_outbox_processor_router import (
    DtsV2OutboxProcessorRouter,
    DtsV2OutboxProcessorRouterError,
    OUTBOX_PROCESSOR_AGGREGATE_TYPES,
)
from app.dts_v2_outbox_worker import DtsV2OutboxEvent


@dataclass
class _Processor:
    name: str

    def process_event(self, connection: object, event: DtsV2OutboxEvent):
        del connection, event
        return {self.name: 1}


def _event(aggregate_type: str) -> DtsV2OutboxEvent:
    event_type = (
        "task.materialization.requested.v2"
        if aggregate_type == "TASK_PLAN"
        else "source_wide.changed.v2"
    )
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id=f"{event_type}:{aggregate_type}:aggregate:1",
        aggregate_type=aggregate_type,
        aggregate_id="aggregate",
        event_type=event_type,
        payload={"protocol_version": "domain-aggregate-outbox-v2"},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


def test_complete_matrix_dispatches_and_namespaces_counts() -> None:
    router = DtsV2OutboxProcessorRouter(
        {
            key: _Processor(key.lower())
            for key in OUTBOX_PROCESSOR_AGGREGATE_TYPES
        }
    )

    assert router.process_event(object(), _event("COURSE")) == {
        "course.course": 1
    }
    assert router.process_event(object(), _event("TASK_PLAN")) == {
        "task_plan.task_plan": 1
    }


def test_production_matrix_cannot_start_incomplete() -> None:
    with pytest.raises(
        DtsV2OutboxProcessorRouterError,
        match="MATRIX_INCOMPLETE:",
    ):
        DtsV2OutboxProcessorRouter({"COURSE": _Processor("course")})


def test_partial_shadow_matrix_still_fails_on_unowned_event() -> None:
    router = DtsV2OutboxProcessorRouter(
        {"COURSE": _Processor("course")},
        require_complete_matrix=False,
    )

    with pytest.raises(
        DtsV2OutboxProcessorRouterError,
        match="PROCESSOR_UNAVAILABLE:TEACHER",
    ):
        router.process_event(object(), _event("TEACHER"))


def test_invalid_handler_result_cannot_publish_event() -> None:
    class _Invalid:
        def process_event(self, connection: object, event: DtsV2OutboxEvent):
            del connection, event
            return {"rows": -1}

    router = DtsV2OutboxProcessorRouter(
        {"COURSE": _Invalid()},
        require_complete_matrix=False,
    )
    with pytest.raises(
        DtsV2OutboxProcessorRouterError,
        match="PROCESSOR_RESULT_INVALID",
    ):
        router.process_event(object(), _event("COURSE"))
