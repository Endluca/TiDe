from __future__ import annotations

import hashlib
import json

import pytest

from app.dts_v2_completion_correction import (
    CompletionCorrectionRequestV2,
    DtsV2CompletionCorrectionError,
    PostgresDtsV2CompletionCorrectionStore,
)


def _position() -> dict[str, object]:
    return {
        "version_kind": "CDC",
        "source_partition_epoch_id": "epoch-1",
        "topic": "appoint",
        "partition": 0,
        "offset": 7,
    }


def _snapshot() -> dict[str, object]:
    return {
        "teacher_id": "202",
        "teacher_id_type": "NUMERIC",
        "status": "end",
        "end_time": "2026-08-22T10:00:00Z",
        "student_token": "dom:v1:" + "a" * 64,
        "lesson_local_date": "2026-08-22",
        "lesson_local_time": "18:00:00",
        "is_peak": False,
    }


def _request(**changes: object) -> CompletionCorrectionRequestV2:
    values: dict[str, object] = {
        "decision_id": "decision-1",
        "case_id": "course-completion-correction:dom:9001",
        "decision": "TRANSFER_COMPLETION",
        "actor_id": "ops-1",
        "reason": "Verified substitute teacher from authoritative records.",
        "expected_case_revision": 2,
        "expected_conflict_fingerprint": "a" * 64,
        "expected_source_revision": 5,
        "expected_source_position": _position(),
        "target_participation_seq": 2,
        "completion_snapshot": _snapshot(),
    }
    values.update(changes)
    return CompletionCorrectionRequestV2(**values)  # type: ignore[arg-type]


class _Scalar:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _Connection:
    def __init__(self, value: object) -> None:
        self.value = value
        self.parameters: dict[str, object] | None = None

    def execute(self, statement: object, parameters: dict[str, object]):
        assert "apply_completion_correction_decision_v2" in str(statement)
        self.parameters = dict(parameters)
        return _Scalar(self.value)


def test_store_hashes_exact_request_and_validates_result() -> None:
    request = _request()
    connection = _Connection(
        {
            "outcome": "APPLIED_PENDING_PROJECTION",
            "decision_id": "decision-1",
            "case_id": "course-completion-correction:dom:9001",
            "case_status": "RESOLVED",
            "completion_conflict_status": "RESOLVED_TRANSFER",
            "projection_event_ids": ["course-event", "participation-event"],
        }
    )
    result = PostgresDtsV2CompletionCorrectionStore().apply(  # type: ignore[arg-type]
        connection,
        request,
    )

    assert result.outcome == "APPLIED_PENDING_PROJECTION"
    assert result.projection_event_ids == (
        "course-event",
        "participation-event",
    )
    assert connection.parameters is not None
    expected = connection.parameters["request"]
    assert connection.parameters["expected_request_sha256"] == hashlib.sha256(
        str(expected).encode("utf-8")
    ).hexdigest()
    assert json.loads(str(expected))["expected_source_revision"] == 5


@pytest.mark.parametrize(
    ("decision", "target", "snapshot"),
    [
        ("KEEP_FROZEN_COMPLETION", 2, None),
        ("UPDATE_COMPLETION_SNAPSHOT", 2, _snapshot()),
        ("TRANSFER_COMPLETION", None, _snapshot()),
        ("VOID_COMPLETION", None, _snapshot()),
    ],
)
def test_decision_shape_fails_closed(
    decision: str,
    target: int | None,
    snapshot: dict[str, object] | None,
) -> None:
    with pytest.raises(
        DtsV2CompletionCorrectionError,
        match="DECISION_SHAPE_INVALID",
    ):
        _request(
            decision=decision,
            target_participation_seq=target,
            completion_snapshot=snapshot,
        )


def test_result_must_match_requested_decision() -> None:
    with pytest.raises(
        DtsV2CompletionCorrectionError,
        match="COMMAND_RESULT_DECISION_MISMATCH",
    ):
        PostgresDtsV2CompletionCorrectionStore().apply(  # type: ignore[arg-type]
            _Connection(
                {
                    "outcome": "APPLIED_PENDING_PROJECTION",
                    "decision_id": "decision-1",
                    "case_id": "course-completion-correction:dom:9001",
                    "case_status": "RESOLVED",
                    "completion_conflict_status": "RESOLVED_KEEP",
                    "projection_event_ids": ["event-1"],
                }
            ),
            _request(),
        )


def test_empty_reason_and_untyped_snapshot_are_rejected() -> None:
    with pytest.raises(DtsV2CompletionCorrectionError, match="REQUEST_INVALID"):
        _request(reason=" ")
    snapshot = _snapshot()
    snapshot["teacher_id_type"] = None
    with pytest.raises(
        DtsV2CompletionCorrectionError,
        match="DECISION_SHAPE_INVALID",
    ):
        _request(completion_snapshot=snapshot)
