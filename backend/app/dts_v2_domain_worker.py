"""Lease-safe DTS v2 Domain Projector worker shell.

The queue owns retry and completion state.  A processor implementation owns
normalized domain facts and Outbox publication.  This shell guarantees that
domain writes plus queue completion share one PostgreSQL transaction and that
a failed handler is rolled back to a savepoint before the queue records WAIT,
RETRY, or DEAD state.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_v2_dirty_queue_store import (
    DirtyClaimV2,
    DirtyDependencyV2,
    DtsV2DirtyQueueStore,
)
from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    DtsV2RuntimeTransactionState,
    guarded_runtime_state,
)


class DtsV2DomainProcessor(Protocol):
    def process_claim(
        self,
        connection: Connection,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]: ...


class DtsV2DomainWorkerError(RuntimeError):
    """Stable worker configuration or orchestration failure."""


class DtsV2DomainDependencyPending(RuntimeError):
    """A normalized fact cannot be proven until typed dependencies arrive."""

    def __init__(self, dependencies: Sequence[DirtyDependencyV2]) -> None:
        if not dependencies:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_DEPENDENCY_REQUIRED"
            )
        self.dependencies = tuple(dependencies)
        super().__init__("DTS_V2_DOMAIN_DEPENDENCY_PENDING")


class DtsV2DomainTransientError(RuntimeError):
    """A retryable technical failure with a database-approved code."""

    _ALLOWED_CODES = frozenset(
        {
            "DB_CONNECTION_TRANSIENT",
            "DB_SERIALIZATION_TRANSIENT",
            "DB_DEADLOCK_TRANSIENT",
            "DOMAIN_PROJECTOR_TRANSIENT",
            "DIRTY_LEASE_EXPIRED",
        }
    )

    def __init__(self, error_code: str = "DOMAIN_PROJECTOR_TRANSIENT") -> None:
        if error_code not in self._ALLOWED_CODES:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_TRANSIENT_CODE_INVALID"
            )
        self.error_code = error_code
        super().__init__(error_code)


class _DtsV2DomainFreshTransactionRetry(RuntimeError):
    """Settle an aborted repeatable-read claim from a fresh transaction."""

    def __init__(self, error_code: str, diagnostic: str) -> None:
        self.error_code = error_code
        self.diagnostic = diagnostic
        super().__init__(error_code)


_CLAIM_TRANSACTION_MAX_ATTEMPTS = 4
_CLAIM_TRANSACTION_RETRY_DELAYS = (0.01, 0.025, 0.05)


@dataclass
class DtsV2DomainWorkerRunResult:
    claimed: int = 0
    completed: int = 0
    superseded: int = 0
    waiting_dependency: int = 0
    retry_scheduled: int = 0
    dead: int = 0
    projection_counts: dict[str, int] = field(default_factory=dict)
    failure_diagnostics: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "superseded": self.superseded,
            "waiting_dependency": self.waiting_dependency,
            "retry_scheduled": self.retry_scheduled,
            "dead": self.dead,
            "projection_counts": dict(sorted(self.projection_counts.items())),
            "failure_diagnostics": dict(
                sorted(self.failure_diagnostics.items())
            ),
        }


class DtsV2DomainWorker:
    """Claim a bounded batch and settle each claim in its own transaction."""

    def __init__(
        self,
        bind: Engine,
        *,
        worker_id: str,
        processor: DtsV2DomainProcessor,
        queue_store: DtsV2DirtyQueueStore | None = None,
        primary_guard: DtsV2PrimaryTransactionGuard | None = None,
        lease_seconds: int = 120,
    ) -> None:
        if bind.dialect.name != "postgresql":
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_POSTGRESQL_REQUIRED"
            )
        if not worker_id or worker_id.strip() != worker_id:
            raise DtsV2DomainWorkerError("DTS_V2_DOMAIN_WORKER_ID_INVALID")
        if not 15 <= lease_seconds <= 300:
            raise DtsV2DomainWorkerError("DTS_V2_DOMAIN_LEASE_INVALID")
        self.engine = bind
        self.worker_id = worker_id
        self.processor = processor
        self.queue = queue_store or DtsV2DirtyQueueStore()
        self.primary_guard = primary_guard
        self.lease_seconds = lease_seconds

    def run_once(self, *, max_claims: int = 25) -> dict[str, Any]:
        if not 1 <= max_claims <= 1000:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_MAX_CLAIMS_INVALID"
            )
        with self.engine.begin() as connection:
            claim_state = self._runtime_state(connection)
            if self.primary_guard is not None and claim_state is None:
                return DtsV2DomainWorkerRunResult().as_dict()
            claims = self.queue.claim_domain(
                connection,
                worker_id=self.worker_id,
                batch_size=max_claims,
                lease_seconds=self.lease_seconds,
            )

        result = DtsV2DomainWorkerRunResult(claimed=len(claims))
        for claim in claims:
            self._process_one(
                claim,
                claim_state=claim_state,
                result=result,
            )
        return result.as_dict()

    def reap_expired(self, *, max_claims: int = 100) -> int:
        if not 1 <= max_claims <= 1000:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_MAX_CLAIMS_INVALID"
            )
        with self.engine.begin() as connection:
            state = self._runtime_state(connection)
            if self.primary_guard is not None and state is None:
                return 0
            return self.queue.reap_domain(
                connection,
                batch_size=max_claims,
            )

    def _process_one(
        self,
        claim: DirtyClaimV2,
        *,
        claim_state: DtsV2RuntimeTransactionState | None,
        result: DtsV2DomainWorkerRunResult,
    ) -> None:
        retry: _DtsV2DomainFreshTransactionRetry | None = None
        for attempt in range(_CLAIM_TRANSACTION_MAX_ATTEMPTS):
            try:
                self._process_one_transaction(
                    claim,
                    claim_state=claim_state,
                    result=result,
                )
                return
            except _DtsV2DomainFreshTransactionRetry as exc:
                retry = exc
                if attempt + 1 < _CLAIM_TRANSACTION_MAX_ATTEMPTS:
                    # PostgreSQL requires the complete failed transaction to
                    # be retried from a new snapshot.  These retries are
                    # technical transaction retries and deliberately do not
                    # consume the dirty key's business attempt_count.
                    time.sleep(_CLAIM_TRANSACTION_RETRY_DELAYS[attempt])
                    continue
                break

        if retry is None:  # pragma: no cover - defensive loop invariant
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_TRANSACTION_RETRY_STATE_INVALID"
            )

        # Only after all in-process transaction retries are exhausted is the
        # key settled once through the queue's bounded technical retry policy.
        # This settlement itself is a fresh transaction; a conflict here is
        # allowed to escape to the runtime-level transaction retry loop rather
        # than terminating the process.
        with self.engine.begin() as connection:
            process_state = self._runtime_state(connection)
            if self.primary_guard is not None and (
                process_state is None or process_state != claim_state
            ):
                return
            failure = self.queue.fail_domain(
                connection,
                claim,
                error_code=retry.error_code,
            )
        self._record_failure_diagnostic(
            retry.diagnostic,
            result=result,
        )
        self._record_failure_result(failure, result=result)

    def _process_one_transaction(
        self,
        claim: DirtyClaimV2,
        *,
        claim_state: DtsV2RuntimeTransactionState | None,
        result: DtsV2DomainWorkerRunResult,
    ) -> None:
        with self.engine.begin() as connection:
            # Evidence tables are intentionally SELECT-only for the application
            # role.  A repeatable-read snapshot gives one claim a stable source
            # view without granting UPDATE merely to support row-lock syntax.
            # Writable normalized facts retain their explicit row locks.
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            process_state = self._runtime_state(connection)
            if self.primary_guard is not None and (
                process_state is None or process_state != claim_state
            ):
                # Cutover may happen after the short claim transaction.  The
                # claimed row remains PROCESSING and is deliberately left for
                # a later reaper; no domain or queue terminal write is allowed
                # under a different cutover mode/generation.
                return
            handler = connection.begin_nested()
            try:
                projection_counts = self.processor.process_claim(
                    connection,
                    claim,
                )
                normalized_counts = _projection_counts(projection_counts)
                completion = self.queue.complete_domain(connection, claim)
                handler.commit()
            except DtsV2DomainDependencyPending as exc:
                handler.rollback()
                waiting = self.queue.wait_domain(
                    connection,
                    claim,
                    exc.dependencies,
                )
                self._record_wait_result(waiting, result=result)
                return
            except DtsV2DomainTransientError as exc:
                handler.rollback()
                self._record_failure_diagnostic(
                    exc.error_code,
                    result=result,
                )
                failure = self.queue.fail_domain(
                    connection,
                    claim,
                    error_code=exc.error_code,
                )
                self._record_failure_result(failure, result=result)
                return
            except Exception as exc:
                handler.rollback()
                diagnostic = _safe_failure_diagnostic(exc)
                retry_code = {
                    "SQLSTATE_40001": "DB_SERIALIZATION_TRANSIENT",
                    "SQLSTATE_40P01": "DB_DEADLOCK_TRANSIENT",
                }.get(diagnostic)
                if retry_code is not None:
                    raise _DtsV2DomainFreshTransactionRetry(
                        retry_code,
                        diagnostic,
                    ) from exc
                if diagnostic == "SQLSTATE_42501":
                    # Permission failures are deterministic deployment defects,
                    # never transient source-data failures.  Fail the process
                    # with a stable code instead of silently burning retries.
                    raise DtsV2DomainWorkerError(
                        "DTS_V2_DOMAIN_DATABASE_PERMISSION_REQUIRED"
                    ) from exc
                self._record_failure_diagnostic(
                    diagnostic,
                    result=result,
                )
                failure = self.queue.fail_domain(
                    connection,
                    claim,
                    error_code="DOMAIN_PROJECTOR_TRANSIENT",
                )
                self._record_failure_result(failure, result=result)
                return

            for name, count in normalized_counts.items():
                result.projection_counts[name] = (
                    result.projection_counts.get(name, 0) + count
                )
            status = completion.get("status")
            if status == "COMPLETED":
                result.completed += 1
            elif status == "PENDING":
                # A newer dirty input arrived while this lease was running.
                # This transaction's facts and Outbox remain valid, and the
                # next claim processes the higher dirty-local revision.
                result.superseded += 1
            else:
                raise DtsV2DomainWorkerError(
                    "DTS_V2_DOMAIN_COMPLETE_RESULT_INVALID"
                )

    @staticmethod
    def _record_failure_diagnostic(
        diagnostic: str,
        *,
        result: DtsV2DomainWorkerRunResult,
    ) -> None:
        result.failure_diagnostics[diagnostic] = (
            result.failure_diagnostics.get(diagnostic, 0) + 1
        )

    @staticmethod
    def _record_wait_result(
        waiting: Mapping[str, Any],
        *,
        result: DtsV2DomainWorkerRunResult,
    ) -> None:
        status = waiting.get("status")
        if status == "WAITING_DEPENDENCY":
            result.waiting_dependency += 1
        elif status == "PENDING":
            result.superseded += 1
        else:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_WAIT_RESULT_INVALID"
            )

    @staticmethod
    def _record_failure_result(
        failure: Mapping[str, Any],
        *,
        result: DtsV2DomainWorkerRunResult,
    ) -> None:
        status = failure.get("status")
        if status == "RETRY":
            result.retry_scheduled += 1
        elif status == "DEAD":
            result.dead += 1
        elif status == "PENDING":
            result.superseded += 1
        else:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_FAILURE_RESULT_INVALID"
            )

    def _runtime_state(
        self,
        connection: Connection,
    ) -> DtsV2RuntimeTransactionState | None:
        if self.primary_guard is None:
            return None
        generation = self.primary_guard.acquire(
            connection,
            component="DOMAIN",
        )
        if generation is None:
            return None
        state = guarded_runtime_state(connection)
        if state.projection_generation != generation:
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_GUARD_STATE_MISMATCH"
            )
        return state


def _projection_counts(values: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(values, Mapping):
        raise DtsV2DomainWorkerError(
            "DTS_V2_DOMAIN_PROCESSOR_RESULT_INVALID"
        )
    result: dict[str, int] = {}
    for name, count in values.items():
        if (
            not isinstance(name, str)
            or not name
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise DtsV2DomainWorkerError(
                "DTS_V2_DOMAIN_PROCESSOR_RESULT_INVALID"
            )
        result[name] = count
    return result


_SAFE_DTS_CODE = re.compile(r"\bDTS_[A-Z0-9_]{1,127}\b")


def _safe_failure_diagnostic(exc: Exception) -> str:
    """Return a bounded diagnostic without SQL, parameters or source data."""

    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        return f"SQLSTATE_{sqlstate}"
    for candidate in (exc, original):
        if candidate is None:
            continue
        match = _SAFE_DTS_CODE.search(str(candidate))
        if match is not None:
            return match.group(0)
    name = type(exc).__name__
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        return name
    return "Exception"


__all__ = [
    "DtsV2DomainDependencyPending",
    "DtsV2DomainProcessor",
    "DtsV2DomainTransientError",
    "DtsV2DomainWorker",
    "DtsV2DomainWorkerError",
]
