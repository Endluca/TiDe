"""Atomic row-lock worker for DTS v2 domain and task-plan Outbox events.

The worker never marks an event as processing and never releases the row lock
between business handling and technical settlement.  Handler writes run below
a savepoint; on failure they are rolled back before retry/dead-letter state is
written in the still-open outer transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    PostgresDtsV2PrimaryTransactionGuard,
)


_DOMAIN_TYPES = frozenset(
    {
        "COURSE",
        "PARTICIPATION",
        "TEACHER",
        "TEACHER_STUDENT",
        "LABEL",
        "COMPLAINT_CATEGORY",
        "COMPLETION_CONFLICT",
        "SOURCE_SCOPE",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_OUTBOX_RUNTIME_ROLE = "tit_dts_outbox_worker_runtime"
_DEFAULT_PRIMARY_GUARD = PostgresDtsV2PrimaryTransactionGuard()


class DtsV2OutboxWorkerError(RuntimeError):
    """A v2 Outbox row or worker configuration is unsafe."""


class DtsV2OutboxProcessingError(RuntimeError):
    """A handler failure whose durable public error code is approved."""

    def __init__(self, error_code: str) -> None:
        if not isinstance(error_code, str) or _ERROR_CODE.fullmatch(
            error_code
        ) is None:
            raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_ERROR_CODE_INVALID")
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class DtsV2OutboxEvent:
    outbox_id: str
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Mapping[str, Any]
    payload_sha256: str
    attempt_count: int
    recovery_count: int
    row_version: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DtsV2OutboxEvent":
        if row.get("status") != "PENDING":
            raise DtsV2OutboxWorkerError(
                "DTS_V2_OUTBOX_PENDING_EVENT_REQUIRED"
            )
        event_type = row.get("event_type")
        aggregate_type = row.get("aggregate_type")
        if event_type == "source_wide.changed.v2":
            if aggregate_type not in _DOMAIN_TYPES:
                raise DtsV2OutboxWorkerError(
                    "DTS_V2_OUTBOX_AGGREGATE_TYPE_INVALID"
                )
        elif event_type == "task.materialization.requested.v2":
            if aggregate_type != "TASK_PLAN":
                raise DtsV2OutboxWorkerError(
                    "DTS_V2_OUTBOX_AGGREGATE_TYPE_INVALID"
                )
        else:
            raise DtsV2OutboxWorkerError(
                "DTS_V2_OUTBOX_EVENT_TYPE_INVALID"
            )
        values: dict[str, str] = {}
        for field_name in (
            "outbox_id",
            "event_id",
            "aggregate_id",
        ):
            value = row.get(field_name)
            if not isinstance(value, str) or not value:
                raise DtsV2OutboxWorkerError(
                    "DTS_V2_OUTBOX_IDENTITY_INVALID"
                )
            values[field_name] = value
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_PAYLOAD_INVALID")
        payload_sha256 = row.get("payload_sha256")
        if not isinstance(payload_sha256, str) or _SHA256.fullmatch(
            payload_sha256
        ) is None:
            raise DtsV2OutboxWorkerError(
                "DTS_V2_OUTBOX_PAYLOAD_HASH_INVALID"
            )
        if row.get("payload_hash_matches") is not True:
            raise DtsV2OutboxWorkerError(
                "DTS_V2_OUTBOX_PAYLOAD_HASH_MISMATCH"
            )
        integers: dict[str, int] = {}
        for field_name in ("attempt_count", "recovery_count", "row_version"):
            value = row.get(field_name)
            minimum = 1 if field_name == "row_version" else 0
            if type(value) is not int or value < minimum:
                raise DtsV2OutboxWorkerError(
                    "DTS_V2_OUTBOX_TECHNICAL_STATE_INVALID"
                )
            integers[field_name] = value
        return cls(
            outbox_id=values["outbox_id"],
            event_id=values["event_id"],
            aggregate_type=str(aggregate_type),
            aggregate_id=values["aggregate_id"],
            event_type=str(event_type),
            payload=dict(payload),
            payload_sha256=payload_sha256,
            attempt_count=integers["attempt_count"],
            recovery_count=integers["recovery_count"],
            row_version=integers["row_version"],
        )


class DtsV2OutboxProcessor(Protocol):
    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]: ...


class DtsV2OutboxTechnicalCaseRecorder(Protocol):
    def record_dead_letter(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
        *,
        error_code: str,
    ) -> None: ...

    def resolve_after_success(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> None: ...


@dataclass
class DtsV2OutboxWorkerResult:
    claimed: int = 0
    published: int = 0
    retries: int = 0
    dead_letters: int = 0
    handler_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "published": self.published,
            "retries": self.retries,
            "dead_letters": self.dead_letters,
            "handler_counts": dict(sorted(self.handler_counts.items())),
        }


class DtsV2OutboxWorker:
    """Process a bounded set of PENDING events one locked transaction each."""

    def __init__(
        self,
        bind: Engine,
        *,
        processor: DtsV2OutboxProcessor,
        technical_cases: DtsV2OutboxTechnicalCaseRecorder,
        primary_guard: DtsV2PrimaryTransactionGuard | None = (
            _DEFAULT_PRIMARY_GUARD
        ),
    ) -> None:
        if bind.dialect.name != "postgresql":
            raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_POSTGRESQL_REQUIRED")
        self.engine = bind
        self.processor = processor
        self.technical_cases = technical_cases
        self.primary_guard = primary_guard

    def run_once(self, *, max_events: int = 25) -> dict[str, Any]:
        if type(max_events) is not int or not 1 <= max_events <= 1000:
            raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_BATCH_SIZE_INVALID")
        result = DtsV2OutboxWorkerResult()
        for _ in range(max_events):
            with self.engine.begin() as connection:
                _assert_runtime_role(connection)
                claim_generation: int | None = None
                if self.primary_guard is None:
                    # Explicit compatibility hook for revision-local tests.
                    # Production construction always supplies the protected
                    # guard. This hook does not provide a mode boundary.
                    pass
                else:
                    claim_generation = self.primary_guard.acquire(
                        connection,
                        component="OUTBOX",
                    )
                    if claim_generation is None:
                        break
                event = _claim_one(connection)
                if event is None:
                    break
                result.claimed += 1
                self._process_locked(
                    connection,
                    event,
                    claim_generation=claim_generation,
                    result=result,
                )
        return result.as_dict()

    def _process_locked(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
        *,
        claim_generation: int | None,
        result: DtsV2OutboxWorkerResult,
    ) -> None:
        handler = connection.begin_nested()
        try:
            counts = _handler_counts(
                self.processor.process_event(connection, event)
            )
            if self.primary_guard is not None:
                commit_generation = self.primary_guard.acquire(
                    connection,
                    component="OUTBOX",
                )
                if (
                    commit_generation is None
                    or commit_generation != claim_generation
                ):
                    # Never turn a cutover race into a business retry/dead
                    # write. Roll back the handler savepoint and leave the
                    # PENDING event untouched for a later PRIMARY claim.
                    handler.rollback()
                    return
            _publish_locked(connection, event)
            self.technical_cases.resolve_after_success(connection, event)
            handler.commit()
        except DtsV2OutboxProcessingError as exc:
            handler.rollback()
            self._record_failure(
                connection,
                event,
                error_code=exc.error_code,
                result=result,
            )
            return
        except Exception:
            handler.rollback()
            self._record_failure(
                connection,
                event,
                error_code="DOWNSTREAM_PROJECTION_TRANSIENT",
                result=result,
            )
            return
        for name, value in counts.items():
            result.handler_counts[name] = result.handler_counts.get(name, 0) + value
        result.published += 1

    def _record_failure(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
        *,
        error_code: str,
        result: DtsV2OutboxWorkerResult,
    ) -> None:
        next_attempt = event.attempt_count + 1
        if next_attempt >= 8:
            _dead_letter_locked(
                connection,
                event,
                error_code=error_code,
                attempt_count=next_attempt,
            )
            self.technical_cases.record_dead_letter(
                connection,
                event,
                error_code=error_code,
            )
            result.dead_letters += 1
            return
        _retry_locked(
            connection,
            event,
            error_code=error_code,
            attempt_count=next_attempt,
        )
        result.retries += 1


def _assert_runtime_role(connection: Connection) -> None:
    is_runtime_role = connection.execute(
        text("SELECT current_user = :expected_role"),
        {"expected_role": _OUTBOX_RUNTIME_ROLE},
    ).scalar_one()
    if is_runtime_role is not True:
        raise DtsV2OutboxWorkerError(
            "DTS_V2_OUTBOX_RUNTIME_ROLE_REQUIRED"
        )


def _claim_one(connection: Connection) -> DtsV2OutboxEvent | None:
    row = connection.execute(
        text(
            f"""
            SELECT outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                   payload,payload_sha256,status,attempt_count,recovery_count,
                   row_version,
                   public.dts_canonical_json_sha256_v1(payload)
                       = payload_sha256 AS payload_hash_matches
            FROM public.outbox_events
            WHERE status='PENDING'
              AND available_at <= clock_timestamp()
              AND event_type IN (
                    'source_wide.changed.v2',
                    'task.materialization.requested.v2'
              )
            ORDER BY available_at,created_at,outbox_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
    ).mappings().first()
    return None if row is None else DtsV2OutboxEvent.from_row(row)


def _publish_locked(connection: Connection, event: DtsV2OutboxEvent) -> None:
    changed = connection.execute(
        text(
            """
            UPDATE public.outbox_events
            SET status='PUBLISHED',published_at=transaction_timestamp(),
                last_error=NULL,row_version=row_version+1
            WHERE outbox_id=:outbox_id AND event_id=:event_id
              AND status='PENDING' AND row_version=:row_version
              AND payload_sha256=:payload_sha256
            """
        ),
        {
            "outbox_id": event.outbox_id,
            "event_id": event.event_id,
            "row_version": event.row_version,
            "payload_sha256": event.payload_sha256,
        },
    ).rowcount
    if changed != 1:
        raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_PUBLISH_CONFLICT")


def _retry_locked(
    connection: Connection,
    event: DtsV2OutboxEvent,
    *,
    error_code: str,
    attempt_count: int,
) -> None:
    delay_seconds = min(300, 2 ** max(0, attempt_count - 1))
    changed = connection.execute(
        text(
            """
            UPDATE public.outbox_events
            SET attempt_count=:attempt_count,last_error=:error_code,
                available_at=transaction_timestamp()
                    + make_interval(secs=>:delay_seconds),
                row_version=row_version+1
            WHERE outbox_id=:outbox_id AND event_id=:event_id
              AND status='PENDING' AND row_version=:row_version
              AND payload_sha256=:payload_sha256
            """
        ),
        {
            "attempt_count": attempt_count,
            "error_code": error_code,
            "delay_seconds": delay_seconds,
            "outbox_id": event.outbox_id,
            "event_id": event.event_id,
            "row_version": event.row_version,
            "payload_sha256": event.payload_sha256,
        },
    ).rowcount
    if changed != 1:
        raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_RETRY_CONFLICT")


def _dead_letter_locked(
    connection: Connection,
    event: DtsV2OutboxEvent,
    *,
    error_code: str,
    attempt_count: int,
) -> None:
    changed = connection.execute(
        text(
            """
            UPDATE public.outbox_events
            SET status='DEAD_LETTER',attempt_count=:attempt_count,
                last_error=:error_code,available_at=transaction_timestamp(),
                published_at=NULL,row_version=row_version+1
            WHERE outbox_id=:outbox_id AND event_id=:event_id
              AND status='PENDING' AND row_version=:row_version
              AND payload_sha256=:payload_sha256
            """
        ),
        {
            "attempt_count": attempt_count,
            "error_code": error_code,
            "outbox_id": event.outbox_id,
            "event_id": event.event_id,
            "row_version": event.row_version,
            "payload_sha256": event.payload_sha256,
        },
    ).rowcount
    if changed != 1:
        raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_DEAD_CONFLICT")


def _handler_counts(values: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(values, Mapping):
        raise DtsV2OutboxWorkerError("DTS_V2_OUTBOX_HANDLER_RESULT_INVALID")
    result: dict[str, int] = {}
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not key
            or type(value) is not int
            or value < 0
        ):
            raise DtsV2OutboxWorkerError(
                "DTS_V2_OUTBOX_HANDLER_RESULT_INVALID"
            )
        result[key] = value
    return result


__all__ = [
    "DtsV2OutboxEvent",
    "DtsV2OutboxProcessingError",
    "DtsV2OutboxTechnicalCaseRecorder",
    "DtsV2OutboxWorker",
    "DtsV2OutboxWorkerError",
]
