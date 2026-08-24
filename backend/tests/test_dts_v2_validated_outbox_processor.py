from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.dts_v2_outbox_worker import DtsV2OutboxEvent
from app.dts_v2_validated_outbox_processor import (
    DtsV2ValidatedOutboxProcessor,
    DtsV2ValidatedOutboxProcessorError,
)


def _event(aggregate_type: str) -> DtsV2OutboxEvent:
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id=f"source_wide.changed.v2:{aggregate_type}:event:2",
        aggregate_type=aggregate_type,
        aggregate_id="aggregate",
        event_type="source_wide.changed.v2",
        payload={"aggregate_revision": 2},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


class _Reader:
    def __init__(self, *, aggregate_type: str, superseded: bool) -> None:
        self.aggregate_type = aggregate_type
        self.superseded = superseded
        self.calls = 0

    def read_current(self, connection: object, event: DtsV2OutboxEvent):
        del connection, event
        self.calls += 1
        return SimpleNamespace(
            aggregate_type=self.aggregate_type,
            is_superseded_event=self.superseded,
        )


@pytest.mark.parametrize(
    "aggregate_type",
    ["PARTICIPATION", "LABEL", "COMPLAINT_CATEGORY", "SOURCE_SCOPE"],
)
def test_validates_current_snapshot_without_duplicate_business_write(
    aggregate_type: str,
) -> None:
    reader = _Reader(aggregate_type=aggregate_type, superseded=True)
    processor = DtsV2ValidatedOutboxProcessor(
        aggregate_types={aggregate_type},
        aggregate_reader=reader,  # type: ignore[arg-type]
    )

    assert processor.process_event(
        object(), _event(aggregate_type)  # type: ignore[arg-type]
    ) == {"validated_snapshots": 1, "superseded_events": 1}
    assert reader.calls == 1


def test_rejects_an_unowned_or_wrong_event_type() -> None:
    processor = DtsV2ValidatedOutboxProcessor(
        aggregate_types={"LABEL"},
        aggregate_reader=_Reader(  # type: ignore[arg-type]
            aggregate_type="LABEL", superseded=False
        ),
    )
    with pytest.raises(
        DtsV2ValidatedOutboxProcessorError,
        match="EVENT_REQUIRED",
    ):
        processor.process_event(
            object(), _event("PARTICIPATION")  # type: ignore[arg-type]
        )


def test_snapshot_type_mismatch_fails_closed() -> None:
    processor = DtsV2ValidatedOutboxProcessor(
        aggregate_types={"LABEL"},
        aggregate_reader=_Reader(  # type: ignore[arg-type]
            aggregate_type="PARTICIPATION", superseded=False
        ),
    )
    with pytest.raises(
        DtsV2ValidatedOutboxProcessorError,
        match="SNAPSHOT_TYPE_MISMATCH",
    ):
        processor.process_event(object(), _event("LABEL"))  # type: ignore[arg-type]


def test_constructor_rejects_business_writer_types() -> None:
    with pytest.raises(
        DtsV2ValidatedOutboxProcessorError,
        match="TYPE_SET_INVALID",
    ):
        DtsV2ValidatedOutboxProcessor(aggregate_types={"COURSE"})
