from __future__ import annotations

import pytest

from app.dts_v2_outbox_worker import DtsV2OutboxEvent
from app.dts_v2_technical_cases import (
    DtsV2OutboxTechnicalCaseStore,
    DtsV2TechnicalCaseError,
    outbox_technical_case_identity_v2,
)


def _event(aggregate_type: str = "COURSE") -> DtsV2OutboxEvent:
    event_type = (
        "task.materialization.requested.v2"
        if aggregate_type == "TASK_PLAN"
        else "source_wide.changed.v2"
    )
    key = (
        {"assignment_dedupe_key": "personalized:P-X:1"}
        if aggregate_type == "TASK_PLAN"
        else {"source_region": "dom", "source_appoint_id": "9001"}
    )
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id=f"{event_type}:{aggregate_type}:v2:{aggregate_type}:hash:7",
        aggregate_type=aggregate_type,
        aggregate_id=f"v2:{aggregate_type}:hash",
        event_type=event_type,
        payload={"aggregate_revision": 7, "aggregate_key": key},
        payload_sha256="b" * 64,
        attempt_count=7,
        recovery_count=2,
        row_version=1,
    )


class _Result:
    def __init__(self, value: str) -> None:
        self.value = value

    def scalar_one(self) -> str:
        return self.value


class _Connection:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: dict[str, object]):
        self.calls.append((str(statement), parameters))
        return _Result(self.values.pop(0))


def test_domain_and_task_case_identities_are_stable_and_distinct() -> None:
    domain = outbox_technical_case_identity_v2(_event())
    task = outbox_technical_case_identity_v2(_event("TASK_PLAN"))

    assert domain[1:] == (
        "DOWNSTREAM_PROJECTION_DEAD",
        "tech-case:projection:source_wide.changed.v2:COURSE:"
        "v2:COURSE:hash:7",
    )
    assert task[1:] == (
        "TASK_MATERIALIZATION_DEAD",
        "tech-case:task-plan:v2:TASK_PLAN:hash:r7",
    )
    assert domain[0].startswith("v2case:") and len(domain[0]) == 71
    assert domain[0] != task[0]


def test_store_passes_only_typed_safe_evidence_to_database_commands() -> None:
    connection = _Connection(["CREATED", "RESOLVED"])
    store = DtsV2OutboxTechnicalCaseStore()
    event = _event()

    store.record_dead_letter(
        connection, event, error_code="DOWNSTREAM_PROJECTION_TRANSIENT"
    )
    store.resolve_after_success(connection, event)

    dead = connection.calls[0][1]
    assert dead["source_region"] == "dom"
    assert dead["source_appoint_id"] == "9001"
    assert dead["teacher_id"] is None
    assert dead["attempt_count"] == 8
    assert dead["aggregate_revision"] == 7
    assert "payload" not in dead and "student_id" not in dead
    assert "record_dts_v2_technical_case_recovery" in connection.calls[1][0]


def test_invalid_revision_or_error_code_fails_before_database_write() -> None:
    connection = _Connection([])
    store = DtsV2OutboxTechnicalCaseStore()
    event = _event()
    invalid = DtsV2OutboxEvent(
        **{
            **event.__dict__,
            "payload": {"aggregate_revision": 0, "aggregate_key": {}},
        }
    )

    with pytest.raises(DtsV2TechnicalCaseError, match="REVISION_INVALID"):
        store.record_dead_letter(connection, invalid, error_code="FAIL")
    with pytest.raises(DtsV2TechnicalCaseError, match="ERROR_CODE_INVALID"):
        store.record_dead_letter(connection, event, error_code="secret:stack")
    assert connection.calls == []
