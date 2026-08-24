from __future__ import annotations

from types import SimpleNamespace

from app.dts_v2_course_outbox_processor import DtsV2CourseOutboxProcessor
from app.dts_v2_outbox_worker import DtsV2OutboxEvent

from tests.test_dts_v2_course_source_wide_plan import _state


def _event() -> DtsV2OutboxEvent:
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id="source_wide.changed.v2:COURSE:event:1",
        aggregate_type="COURSE",
        aggregate_id="aggregate",
        event_type="source_wide.changed.v2",
        payload={"aggregate_revision": 1},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


class _Reader:
    def read_current(self, connection: object, event: DtsV2OutboxEvent):
        del connection, event
        return SimpleNamespace(
            aggregate_key={
                "source_region": "dom",
                "source_appoint_id": "9001",
            },
            aggregate_state=_state(),
            current_revision=4,
            is_superseded_event=True,
        )


class _Materializer:
    def __init__(self) -> None:
        self.call: tuple[object, int, str] | None = None

    def apply_course_plan(
        self,
        connection: object,
        plan: object,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ):
        del connection
        self.call = (plan, aggregate_revision, triggering_event_id)
        return {"participation_rows": 2, "score_components": 3}


def test_reduces_current_state_and_materializes_latest_revision() -> None:
    materializer = _Materializer()
    processor = DtsV2CourseOutboxProcessor(
        materializer=materializer,
        aggregate_reader=_Reader(),  # type: ignore[arg-type]
    )

    result = processor.process_event(object(), _event())  # type: ignore[arg-type]

    assert result == {
        "participation_rows": 2,
        "score_components": 3,
        "superseded_events": 1,
    }
    assert materializer.call is not None
    plan, revision, event_id = materializer.call
    assert revision == 4
    assert event_id == _event().event_id
    assert plan.completion_teacher_id == "B"
    assert [row.teacher_id for row in plan.participation_rows] == ["A", "B"]
