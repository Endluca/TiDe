#!/usr/bin/env python3
"""Verify that two API workers cannot accept the same output retry.

Uses one disposable row in the configured PostgreSQL database and removes all
created verification facts before exit.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from sqlalchemy import delete, select


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import engine, session_scope  # noqa: E402
from app.db_models import (  # noqa: E402
    AuditEventRecord,
    OutboundOutputRecord,
    OutboxEventRecord,
)
from app.errors import DomainError  # noqa: E402
from app.output_service import OutputService  # noqa: E402


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise SystemExit("PostgreSQL is required for this concurrency check.")

    token = uuid4().hex
    output_id = f"VERIFY-OUTPUT-{token}"
    now = datetime.now(timezone.utc)
    with session_scope(engine) as session:
        session.add(
            OutboundOutputRecord(
                output_id=output_id,
                output_type="DELIVERY_INTENT",
                display_type="REMINDER",
                delivery_kind="WEBAPP",
                audience_type="TEACHER",
                recipient_id="VERIFY",
                recipient_name="Concurrency verification",
                channel="WEBAPP_INBOX",
                source_type="VERIFICATION",
                source_id=f"VERIFY-SOURCE-{token}",
                teacher_id=None,
                task_id=None,
                case_id=None,
                status="FAILED",
                title="Disposable retry verification",
                body="Removed after verification.",
                scheduled_at=None,
                created_at=now,
                sent_at=None,
                delivered_at=None,
                attempt_count=1,
                max_attempts=3,
                next_retry_at=None,
                last_error="verification failure",
                retryable=True,
                requires_human_approval=False,
                payload={"verification_token": token},
                idempotency_key=f"verification:{token}",
                updated_at=now,
            )
        )

    barrier = Barrier(2)

    def retry(worker: int) -> tuple[str, str]:
        barrier.wait()
        try:
            result = OutputService(engine).retry_output(
                output_id,
                actor_id=f"verification-worker-{worker}",
            )
            return "accepted", str(result["attempt_count"])
        except DomainError as exc:
            return "rejected", exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(retry, (1, 2)))
        with session_scope(engine) as session:
            output = session.get(OutboundOutputRecord, output_id)
            event_ids = list(
                session.scalars(
                    select(AuditEventRecord.event_id).where(
                        AuditEventRecord.event_type
                        == "outbound_output.retry_requested.v1",
                        AuditEventRecord.payload["payload"]["output_id"].as_string()
                        == output_id,
                    )
                ).all()
            )
            outbox_count = len(
                session.scalars(
                    select(OutboxEventRecord.outbox_id).where(
                        OutboxEventRecord.event_id.in_(event_ids)
                    )
                ).all()
            )
            valid = (
                outcomes
                == [
                    ("accepted", "2"),
                    ("rejected", "COMMAND_NOT_ALLOWED"),
                ]
                and output is not None
                and output.status == "REQUESTED"
                and output.attempt_count == 2
                and len(event_ids) == 1
                and outbox_count == 1
            )
            print(
                {
                    "ok": valid,
                    "outcomes": outcomes,
                    "attempt_count": output.attempt_count if output else None,
                    "audit_events": len(event_ids),
                    "outbox_events": outbox_count,
                }
            )
            return 0 if valid else 1
    finally:
        with session_scope(engine) as session:
            event_ids = list(
                session.scalars(
                    select(AuditEventRecord.event_id).where(
                        AuditEventRecord.event_type
                        == "outbound_output.retry_requested.v1",
                        AuditEventRecord.payload["payload"]["output_id"].as_string()
                        == output_id,
                    )
                ).all()
            )
            if event_ids:
                session.execute(
                    delete(OutboxEventRecord).where(
                        OutboxEventRecord.event_id.in_(event_ids)
                    )
                )
                session.execute(
                    delete(AuditEventRecord).where(
                        AuditEventRecord.event_id.in_(event_ids)
                    )
                )
            session.execute(
                delete(OutboundOutputRecord).where(
                    OutboundOutputRecord.output_id == output_id
                )
            )


if __name__ == "__main__":
    raise SystemExit(main())
