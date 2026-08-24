from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.dts_v2_outbox_worker import DtsV2OutboxEvent
from app.dts_v2_task_plan_outbox_processor import (
    DtsV2TaskPlanOutboxProcessor,
    DtsV2TaskPlanOutboxProcessorError,
)


def _state(*, materializable: bool = True) -> dict[str, object]:
    state: dict[str, object] = {
        "protocol_version": "task-plan-state-v1",
        "assignment_dedupe_key": "personalized:P-REL-ATTENDANCE:101",
        "teacher_id": "101",
        "target_task_code": "P-REL-ATTENDANCE",
        "materializable": materializable,
        "eligibility_generation": 1,
        "eligible_since_at": "2026-08-22T10:00:00Z",
        "timezone_used": "Asia/Shanghai",
        "timezone_source": "TEACHER_PROFILE",
        "timezone_verified_at": "2026-08-22T10:00:00Z",
        "teacher_row_version": 2,
        "template_version_id": "template-row-1",
        "template_revision": 1,
        "teacher_copy_version_id": "copy-1",
        "teacher_copy_config_key": "teacher_personalized_copy",
        "teacher_copy_version_number": 1,
        "active_match_set_hash": "a" * 64,
        "blocker_code": "NONE" if materializable else "NO_ACTIVE_MATCH",
        "blocker_set_hash": (
            hashlib.sha256(b"[]").hexdigest()
            if materializable
            else "b" * 64
        ),
    }
    state["plan_state_hash"] = hashlib.sha256(
        json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return state


def _event() -> DtsV2OutboxEvent:
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id="task.materialization.requested.v2:TASK_PLAN:event:4",
        aggregate_type="TASK_PLAN",
        aggregate_id="aggregate",
        event_type="task.materialization.requested.v2",
        payload={"aggregate_revision": 4},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


class _Reader:
    def __init__(self, state, *, superseded=False) -> None:
        self.state = state
        self.superseded = superseded

    def read_current(self, connection, event):
        del connection, event
        return SimpleNamespace(
            aggregate_key={
                "assignment_dedupe_key": self.state["assignment_dedupe_key"]
            },
            aggregate_state=self.state,
            current_revision=4,
            is_superseded_event=self.superseded,
        )


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, value) -> None:
        self.value = value
        self.parameters = None

    def execute(self, statement, parameters):
        assert "materialize_task_plan_v2" in str(statement)
        self.parameters = dict(parameters)
        return _Scalar(self.value)


def test_materializes_current_plan_once_through_protected_command() -> None:
    connection = _Connection(
        {"outcome": "MATERIALIZED", "assignment_id": "TAS-1"}
    )
    result = DtsV2TaskPlanOutboxProcessor(
        aggregate_reader=_Reader(_state()),  # type: ignore[arg-type]
    ).process_event(connection, _event())  # type: ignore[arg-type]

    assert result["assignments_created"] == 1
    assert connection.parameters["aggregate_revision"] == 4
    assert connection.parameters["expected_plan_state_hash"] == _state()[
        "plan_state_hash"
    ]


def test_superseded_plan_never_materializes_old_seed() -> None:
    class _NoExecute:
        def execute(self, *args, **kwargs):
            raise AssertionError("superseded plan must not call command")

    result = DtsV2TaskPlanOutboxProcessor(
        aggregate_reader=_Reader(_state(), superseded=True),  # type: ignore[arg-type]
    ).process_event(_NoExecute(), _event())  # type: ignore[arg-type]

    assert result == {
        "superseded_events": 1,
        "assignments_created": 0,
        "assignments_linked": 0,
        "not_materializable": 0,
    }


def test_non_materializable_plan_requires_explicit_empty_result() -> None:
    result = DtsV2TaskPlanOutboxProcessor(
        aggregate_reader=_Reader(_state(materializable=False)),  # type: ignore[arg-type]
    ).process_event(
        _Connection({"outcome": "NOT_MATERIALIZABLE", "assignment_id": None}),
        _event(),  # type: ignore[arg-type]
    )
    assert result["not_materializable"] == 1


def test_plan_state_hash_drift_fails_closed() -> None:
    state = _state()
    state["blocker_code"] = "DRIFT"
    with pytest.raises(
        DtsV2TaskPlanOutboxProcessorError,
        match="STATE_HASH_MISMATCH",
    ):
        DtsV2TaskPlanOutboxProcessor(
            aggregate_reader=_Reader(state),  # type: ignore[arg-type]
        ).process_event(
            _Connection({"outcome": "MATERIALIZED", "assignment_id": "TAS-1"}),
            _event(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("teacher_copy_config_key", "another_config"),
        ("teacher_copy_version_number", 0),
        ("template_revision", 0),
        ("timezone_used", "not/a-zone"),
        ("eligible_since_at", "2026-08-22T10:00:00"),
    ],
)
def test_materializable_plan_rejects_invalid_frozen_basis(
    field: str,
    value: object,
) -> None:
    state = _state()
    state[field] = value
    state["plan_state_hash"] = hashlib.sha256(
        json.dumps(
            {key: item for key, item in state.items() if key != "plan_state_hash"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(
        DtsV2TaskPlanOutboxProcessorError,
        match="STATE_INVALID",
    ):
        DtsV2TaskPlanOutboxProcessor(
            aggregate_reader=_Reader(state),  # type: ignore[arg-type]
        ).process_event(
            _Connection({"outcome": "MATERIALIZED", "assignment_id": "TAS-1"}),
            _event(),  # type: ignore[arg-type]
        )


def test_task_plan_assignment_key_must_match_teacher_and_task() -> None:
    state = _state()
    state["assignment_dedupe_key"] = "personalized:P-REL-MEMO:999"
    state["plan_state_hash"] = hashlib.sha256(
        json.dumps(
            {key: item for key, item in state.items() if key != "plan_state_hash"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(
        DtsV2TaskPlanOutboxProcessorError,
        match="STATE_INVALID",
    ):
        DtsV2TaskPlanOutboxProcessor(
            aggregate_reader=_Reader(state),  # type: ignore[arg-type]
        ).process_event(
            _Connection({"outcome": "MATERIALIZED", "assignment_id": "TAS-1"}),
            _event(),  # type: ignore[arg-type]
        )
