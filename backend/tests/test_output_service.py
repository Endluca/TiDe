from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import engine, session_scope
from app.db_models import AuditEventRecord, OutboundOutputRecord, OutboxEventRecord
from app.main import app


client = TestClient(app)


def _failed_output(
    output_id: str,
    *,
    requires_human_approval: bool = False,
) -> OutboundOutputRecord:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    return OutboundOutputRecord(
        output_id=output_id,
        output_type="DELIVERY_INTENT",
        display_type="REMINDER",
        delivery_kind="WEBAPP",
        audience_type="TEACHER",
        recipient_id="T-OUTPUT",
        recipient_name="Output Teacher",
        channel="WEBAPP_INBOX",
        source_type="TEST",
        source_id=f"SOURCE-{output_id}",
        teacher_id=None,
        task_id=None,
        case_id=None,
        status="FAILED",
        title="Retryable reminder",
        body="Delivery failed.",
        scheduled_at=None,
        created_at=now,
        sent_at=None,
        delivered_at=None,
        attempt_count=1,
        max_attempts=3,
        next_retry_at=None,
        last_error="temporary failure",
        retryable=True,
        requires_human_approval=requires_human_approval,
        payload={"test": True},
        idempotency_key=f"test:{output_id}",
        updated_at=now,
    )


def test_output_retry_is_atomic_and_persists_audit_outbox() -> None:
    with session_scope(engine) as session:
        session.add(_failed_output("OUTPUT-RETRY-1"))

    listed = client.get("/api/outputs?status=FAILED&page=1&page_size=10")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["output_id"] == "OUTPUT-RETRY-1"

    retried = client.post("/api/outputs/OUTPUT-RETRY-1/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "REQUESTED"
    assert retried.json()["attempt_count"] == 2
    assert retried.json()["last_error"] is None

    repeated = client.post("/api/outputs/OUTPUT-RETRY-1/retry")
    assert repeated.status_code == 409
    assert repeated.json()["error_code"] == "COMMAND_NOT_ALLOWED"

    with session_scope(engine) as session:
        output = session.get(OutboundOutputRecord, "OUTPUT-RETRY-1")
        assert output is not None
        assert output.status == "REQUESTED"
        assert output.attempt_count == 2
        event = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type
                == "outbound_output.retry_requested.v1"
            )
        )
        assert event is not None
        assert event.payload["payload"]["output_id"] == "OUTPUT-RETRY-1"
        outbox = session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.event_id == event.event_id
            )
        )
        assert outbox is not None
        assert outbox.status == "PENDING"
        assert outbox.aggregate_id == "OUTPUT-RETRY-1"


def test_output_retry_rejects_human_approval_without_mutation() -> None:
    with session_scope(engine) as session:
        session.add(
            _failed_output(
                "OUTPUT-HUMAN-1",
                requires_human_approval=True,
            )
        )

    response = client.post("/api/outputs/OUTPUT-HUMAN-1/retry")
    assert response.status_code == 409
    assert response.json()["message_key"] == "output.error.human_approval_required"

    with session_scope(engine) as session:
        output = session.get(OutboundOutputRecord, "OUTPUT-HUMAN-1")
        assert output is not None
        assert output.status == "FAILED"
        assert output.attempt_count == 1
