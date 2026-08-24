from __future__ import annotations

from sqlalchemy import create_engine

from app.dts_v2_completion_correction import CompletionCorrectionResultV2
from app.operations_service import OperationsService


class _Store:
    def __init__(self) -> None:
        self.request = None

    def apply(self, connection, request):
        del connection
        self.request = request
        return CompletionCorrectionResultV2(
            outcome="APPLIED_PENDING_PROJECTION",
            decision_id=request.decision_id,
            case_id=request.case_id,
            case_status="RESOLVED",
            completion_conflict_status="RESOLVED_KEEP",
            projection_event_ids=("course-event", "participation-event"),
        )


def test_operations_service_uses_protected_completion_command() -> None:
    store = _Store()
    service = OperationsService(
        create_engine("sqlite+pysqlite:///:memory:"),
        completion_correction_store=store,  # type: ignore[arg-type]
    )

    result = service.apply_completion_correction(
        decision_id="decision-1",
        case_id="course-completion-correction:dom:9001",
        decision="KEEP_FROZEN_COMPLETION",
        actor_id="ops-1",
        reason="Verified the original completion owner.",
        expected_case_revision=2,
        expected_conflict_fingerprint="a" * 64,
        expected_source_revision=5,
        expected_source_position={
            "version_kind": "CDC",
            "source_partition_epoch_id": "epoch-1",
            "topic": "appoint",
            "partition": 0,
            "offset": 7,
        },
        target_participation_seq=None,
        completion_snapshot=None,
    )

    assert result == {
        "outcome": "APPLIED_PENDING_PROJECTION",
        "decision_id": "decision-1",
        "case_id": "course-completion-correction:dom:9001",
        "case_status": "RESOLVED",
        "completion_conflict_status": "RESOLVED_KEEP",
        "projection_event_ids": ["course-event", "participation-event"],
    }
    assert store.request is not None
    assert store.request.actor_id == "ops-1"
