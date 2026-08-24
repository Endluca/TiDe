from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.dts_v2_completion_conflict_outbox_processor import (
    DtsV2CompletionConflictOutboxProcessor,
    DtsV2CompletionConflictOutboxProcessorError,
)
from app.dts_v2_outbox_worker import DtsV2OutboxEvent


CASE_ID = "course-completion-correction:dom:9001"


def _event() -> DtsV2OutboxEvent:
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id="source_wide.changed.v2:COMPLETION_CONFLICT:event:3",
        aggregate_type="COMPLETION_CONFLICT",
        aggregate_id="aggregate",
        event_type="source_wide.changed.v2",
        payload={"aggregate_revision": 3},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


def _state(*, status: str = "PENDING") -> dict[str, object]:
    return {
        "completion_conflict": {
            "status": status,
            "case_id": CASE_ID if status == "PENDING" else None,
            "fingerprint": "c" * 64 if status == "PENDING" else None,
            "completion_teacher_id": "202",
            "completion_teacher_id_type": "NUMERIC",
            "completion_participation_seq": 2,
        }
    }


class _Reader:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def read_current(self, connection: object, event: DtsV2OutboxEvent):
        del connection, event
        return SimpleNamespace(
            aggregate_key={
                "source_region": "dom",
                "source_appoint_id": "9001",
            },
            aggregate_state=self.state,
            current_revision=7,
            is_superseded_event=True,
        )


class _Scalar:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _Connection:
    def __init__(self, result: object) -> None:
        self.result = result
        self.parameters: dict[str, object] | None = None
        self.sql = ""

    def execute(self, statement: object, parameters: dict[str, object]):
        self.sql = str(statement)
        self.parameters = parameters
        return _Scalar(self.result)


def test_pending_conflict_calls_protected_case_command() -> None:
    connection = _Connection({"outcome": "CREATED", "case_id": CASE_ID})
    processor = DtsV2CompletionConflictOutboxProcessor(
        aggregate_reader=_Reader(_state()),  # type: ignore[arg-type]
    )

    assert processor.process_event(
        connection, _event()  # type: ignore[arg-type]
    ) == {
        "cases_created": 1,
        "cases_updated": 0,
        "shadow_only": 0,
        "non_pending": 0,
        "superseded_events": 1,
    }
    assert "reconcile_completion_conflict_case_v2" in connection.sql
    assert connection.parameters == {
        "source_region": "dom",
        "source_appoint_id": "9001",
        "expected_aggregate_revision": 7,
        "triggering_event_id": _event().event_id,
    }


def test_non_pending_state_requires_explicit_empty_command_result() -> None:
    connection = _Connection({"outcome": "NOT_PENDING", "case_id": None})
    processor = DtsV2CompletionConflictOutboxProcessor(
        aggregate_reader=_Reader(_state(status="NONE")),  # type: ignore[arg-type]
    )

    assert processor.process_event(
        connection, _event()  # type: ignore[arg-type]
    )["non_pending"] == 1


def test_shadow_mode_verifies_conflict_without_exposing_case_pointer() -> None:
    connection = _Connection({"outcome": "SHADOW_ONLY", "case_id": None})
    processor = DtsV2CompletionConflictOutboxProcessor(
        aggregate_reader=_Reader(_state()),  # type: ignore[arg-type]
    )

    result = processor.process_event(
        connection, _event()  # type: ignore[arg-type]
    )
    assert result["shadow_only"] == 1
    assert result["cases_created"] == 0


def test_command_cannot_disagree_with_locked_aggregate() -> None:
    processor = DtsV2CompletionConflictOutboxProcessor(
        aggregate_reader=_Reader(_state()),  # type: ignore[arg-type]
    )
    with pytest.raises(
        DtsV2CompletionConflictOutboxProcessorError,
        match="STATE_MISMATCH",
    ):
        processor.process_event(
            _Connection({"outcome": "NOT_PENDING", "case_id": None}),
            _event(),  # type: ignore[arg-type]
        )


def test_pending_state_requires_canonical_case_and_fingerprint() -> None:
    state = _state()
    state["completion_conflict"]["fingerprint"] = None  # type: ignore[index]
    processor = DtsV2CompletionConflictOutboxProcessor(
        aggregate_reader=_Reader(state),  # type: ignore[arg-type]
    )
    with pytest.raises(
        DtsV2CompletionConflictOutboxProcessorError,
        match="PENDING_EVIDENCE_INVALID",
    ):
        processor.process_event(
            _Connection({"outcome": "CREATED", "case_id": CASE_ID}),
            _event(),  # type: ignore[arg-type]
        )
