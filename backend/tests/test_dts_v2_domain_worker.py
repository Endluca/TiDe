from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass

import pytest

from app.dts_v2_dirty_queue_store import (
    DirtyClaimV2,
    DirtyDependencyV2,
    DirtyKeyV2,
)
from app.dts_v2_domain_worker import (
    DtsV2DomainDependencyPending,
    DtsV2DomainTransientError,
    DtsV2DomainWorker,
    DtsV2DomainWorkerError,
)
from app.dts_v2_runtime_guard import DtsV2RuntimeTransactionState


class _Nested:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def commit(self) -> None:
        self.log.append("savepoint_commit")

    def rollback(self) -> None:
        self.log.append("savepoint_rollback")


class _Connection:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def begin_nested(self) -> _Nested:
        self.log.append("savepoint_begin")
        return _Nested(self.log)

    def execute(self, statement):
        assert str(statement) == (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
        )
        self.log.append("repeatable_read")


class _Begin(AbstractContextManager[_Connection]):
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> _Connection:
        self.log.append("transaction_begin")
        return _Connection(self.log)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        del traceback
        self.log.append("transaction_rollback" if exc_type else "transaction_commit")
        return False


class _Dialect:
    name = "postgresql"


class _Engine:
    dialect = _Dialect()

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def begin(self) -> _Begin:
        return _Begin(self.log)


@dataclass
class _Queue:
    claims: tuple[DirtyClaimV2, ...]
    terminal_results: list[dict[str, object]]
    log: list[str]

    def claim_domain(self, connection, **kwargs):
        del connection, kwargs
        self.log.append("claim")
        return self.claims

    def complete_domain(self, connection, claim):
        del connection, claim
        self.log.append("complete")
        return self.terminal_results.pop(0)

    def wait_domain(self, connection, claim, dependencies):
        del connection, claim, dependencies
        self.log.append("wait")
        return self.terminal_results.pop(0)

    def fail_domain(self, connection, claim, *, error_code):
        del connection, claim
        self.log.append(f"fail:{error_code}")
        return self.terminal_results.pop(0)

    def reap_domain(self, connection, *, batch_size):
        del connection, batch_size
        self.log.append("reap")
        return 2


class _Processor:
    def __init__(self, outcomes, log: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.log = log

    def process_claim(self, connection, claim):
        del connection, claim
        self.log.append("project")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _claim(value: str) -> DirtyClaimV2:
    return DirtyClaimV2(
        DirtyKeyV2("dom", "COURSE", value),
        f"lease-{value}",
        1,
        2,
    )


def test_guarded_domain_leaves_lease_when_mode_changes_after_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dts_v2_domain_worker as worker_module

    log: list[str] = []
    queue = _Queue((_claim("mode-change"),), [], log)
    states = iter(
        (
            DtsV2RuntimeTransactionState("V1_COMPAT_DUAL_CAPTURE", 0),
            DtsV2RuntimeTransactionState("ROLLED_BACK", 0),
        )
    )
    monkeypatch.setattr(
        worker_module,
        "guarded_runtime_state",
        lambda _connection: next(states),
    )

    class _Guard:
        def acquire(self, connection, *, component):
            del connection
            assert component == "DOMAIN"
            return 0

    worker = DtsV2DomainWorker(
        _Engine(log),
        worker_id="domain-mode-test",
        processor=_Processor(({"facts": 1},), log),
        queue_store=queue,
        primary_guard=_Guard(),
    )
    assert worker.run_once(max_claims=1) == {
        "claimed": 1,
        "completed": 0,
        "superseded": 0,
        "waiting_dependency": 0,
        "retry_scheduled": 0,
        "dead": 0,
        "projection_counts": {},
        "failure_diagnostics": {},
    }
    assert "project" not in log
    assert "complete" not in log


def _worker(*, outcomes, terminal_results, claims=None):
    log: list[str] = []
    queue = _Queue(
        tuple(claims or (_claim("1"),)),
        list(terminal_results),
        log,
    )
    return (
        DtsV2DomainWorker(
            _Engine(log),
            worker_id="domain-1",
            processor=_Processor(outcomes, log),
            queue_store=queue,
        ),
        log,
    )


def test_success_commits_projection_outbox_and_queue_completion_together() -> None:
    worker, log = _worker(
        outcomes=[{"course_changes": 1, "outbox_events": 2}],
        terminal_results=[{"status": "COMPLETED"}],
    )

    result = worker.run_once()

    assert result == {
        "claimed": 1,
        "completed": 1,
        "superseded": 0,
        "waiting_dependency": 0,
        "retry_scheduled": 0,
        "dead": 0,
        "projection_counts": {"course_changes": 1, "outbox_events": 2},
        "failure_diagnostics": {},
    }
    assert log == [
        "transaction_begin",
        "claim",
        "transaction_commit",
        "transaction_begin",
        "repeatable_read",
        "savepoint_begin",
        "project",
        "complete",
        "savepoint_commit",
        "transaction_commit",
    ]


def test_dependency_rolls_back_handler_before_wait_state_is_written() -> None:
    dependency = DirtyDependencyV2(
        "TEACHER", "dom", "7", 1, "a" * 64
    )
    worker, log = _worker(
        outcomes=[DtsV2DomainDependencyPending([dependency])],
        terminal_results=[{"status": "WAITING_DEPENDENCY"}],
    )

    result = worker.run_once()

    assert result["waiting_dependency"] == 1
    assert log.index("savepoint_rollback") < log.index("wait")
    assert log[-1] == "transaction_commit"


@pytest.mark.parametrize(
    ("exception", "terminal", "counter", "expected_fail"),
    [
        (
            DtsV2DomainTransientError("DB_DEADLOCK_TRANSIENT"),
            "RETRY",
            "retry_scheduled",
            "fail:DB_DEADLOCK_TRANSIENT",
        ),
        (RuntimeError("sensitive source value"), "DEAD", "dead", "fail:DOMAIN_PROJECTOR_TRANSIENT"),
        (RuntimeError("boom"), "PENDING", "superseded", "fail:DOMAIN_PROJECTOR_TRANSIENT"),
    ],
)
def test_failure_is_value_blind_and_recorded_after_savepoint_rollback(
    exception: Exception,
    terminal: str,
    counter: str,
    expected_fail: str,
) -> None:
    worker, log = _worker(
        outcomes=[exception],
        terminal_results=[{"status": terminal}],
    )

    result = worker.run_once()

    assert result[counter] == 1
    assert sum(result["failure_diagnostics"].values()) == 1
    assert log.index("savepoint_rollback") < log.index(expected_fail)
    assert "sensitive source value" not in " ".join(log)


def test_database_permission_error_fails_fast_with_stable_code() -> None:
    class PermissionFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__("must-not-leak")
            self.orig = type("Original", (), {"sqlstate": "42501"})()

    worker, log = _worker(
        outcomes=[PermissionFailure()],
        terminal_results=[],
    )

    with pytest.raises(
        DtsV2DomainWorkerError,
        match="DTS_V2_DOMAIN_DATABASE_PERMISSION_REQUIRED",
    ):
        worker.run_once()

    assert "savepoint_rollback" in log
    assert not any(value.startswith("fail:") for value in log)


def test_serialization_failure_retries_claim_without_consuming_queue_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SerializationFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__("must-not-leak")
            self.orig = type("Original", (), {"sqlstate": "40001"})()

    monkeypatch.setattr("app.dts_v2_domain_worker.time.sleep", lambda _: None)
    worker, log = _worker(
        outcomes=[SerializationFailure(), {"facts": 1}],
        terminal_results=[{"status": "COMPLETED"}],
    )

    result = worker.run_once()

    assert result["completed"] == 1
    assert result["retry_scheduled"] == 0
    assert result["failure_diagnostics"] == {}
    assert log.count("transaction_begin") == 3
    assert "fail:DB_SERIALIZATION_TRANSIENT" not in log


def test_serialization_failure_uses_one_queue_attempt_after_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SerializationFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__("must-not-leak")
            self.orig = type("Original", (), {"sqlstate": "40001"})()

    monkeypatch.setattr("app.dts_v2_domain_worker.time.sleep", lambda _: None)
    worker, log = _worker(
        outcomes=[SerializationFailure() for _ in range(4)],
        terminal_results=[{"status": "RETRY"}],
    )

    result = worker.run_once()

    assert result["retry_scheduled"] == 1
    assert result["failure_diagnostics"] == {"SQLSTATE_40001": 1}
    assert log.count("project") == 4
    assert log.count("fail:DB_SERIALIZATION_TRANSIENT") == 1


def test_newer_dirty_input_keeps_current_projection_and_requeues_claim() -> None:
    worker, _ = _worker(
        outcomes=[{"course_changes": 1}],
        terminal_results=[{"status": "PENDING"}],
    )
    result = worker.run_once()
    assert result["superseded"] == 1
    assert result["projection_counts"] == {"course_changes": 1}


def test_reaper_uses_protected_queue_command() -> None:
    worker, log = _worker(outcomes=[], terminal_results=[], claims=())
    assert worker.reap_expired(max_claims=10) == 2
    assert "reap" in log


def test_configuration_fails_closed() -> None:
    log: list[str] = []
    engine = _Engine(log)
    engine.dialect = type("Dialect", (), {"name": "sqlite"})()
    with pytest.raises(DtsV2DomainWorkerError, match="POSTGRESQL_REQUIRED"):
        DtsV2DomainWorker(
            engine,
            worker_id="domain-1",
            processor=_Processor([], log),
            queue_store=_Queue((), [], log),
        )
