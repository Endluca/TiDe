from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.dts_v2_outbox_worker import (
    DtsV2OutboxProcessingError,
    DtsV2OutboxWorker,
    DtsV2OutboxWorkerError,
)


def _event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "outbox_id": "outbox:v2:" + "a" * 64,
        "event_id": "source_wide.changed.v2:COURSE:v2:COURSE:" + "b" * 64 + ":1",
        "aggregate_type": "COURSE",
        "aggregate_id": "v2:COURSE:" + "b" * 64,
        "event_type": "source_wide.changed.v2",
        "payload": {"protocol_version": "domain-aggregate-outbox-v2"},
        "payload_sha256": "c" * 64,
        "payload_hash_matches": True,
        "status": "PENDING",
        "attempt_count": 0,
        "recovery_count": 0,
        "row_version": 1,
    }
    value.update(overrides)
    return value


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, object] | None = None,
        rowcount: int = 0,
        scalar: object | None = None,
    ):
        self._row = row
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, object] | None:
        return self._row

    def scalar_one(self) -> object:
        return self._scalar


class _Savepoint:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.domain_length = len(connection.domain_writes)
        self.case_resolution_length = len(connection.case_resolutions)

    def commit(self) -> None:
        self.connection.log.append("savepoint_commit")

    def rollback(self) -> None:
        del self.connection.domain_writes[self.domain_length :]
        del self.connection.case_resolutions[self.case_resolution_length :]
        self.connection.log.append("savepoint_rollback")


class _Connection:
    def __init__(
        self,
        row: dict[str, object],
        *,
        runtime_role_ok: bool = True,
        pipeline_mode: str = "V2_PRIMARY",
    ) -> None:
        self.row = row
        self.runtime_role_ok = runtime_role_ok
        self.pipeline_mode = pipeline_mode
        self.log: list[str] = []
        self.domain_writes: list[str] = []
        self.case_resolutions: list[str] = []
        self.dead_cases: list[tuple[str, str]] = []

    def begin_nested(self) -> _Savepoint:
        self.log.append("savepoint_begin")
        return _Savepoint(self)

    def execute(self, statement: object, parameters: dict[str, object] | None = None) -> _Result:
        sql = str(statement)
        if "current_user = :expected_role" in sql:
            self.log.append("runtime_role")
            return _Result(scalar=self.runtime_role_ok)
        if "pg_advisory_xact_lock_shared" in sql:
            self.log.append("cutover_lock")
            return _Result()
        if "FROM public.outbox_events" in sql and "FOR UPDATE SKIP LOCKED" in sql:
            self.log.append("claim")
            assert "dts_pipeline_control" not in sql
            if self.row["status"] != "PENDING":
                return _Result(row=None)
            return _Result(row=dict(self.row))
        assert parameters is not None
        if "SET status='PUBLISHED'" in sql:
            self.log.append("publish")
            if parameters["row_version"] != self.row["row_version"]:
                return _Result(rowcount=0)
            self.row["status"] = "PUBLISHED"
            self.row["last_error"] = None
            self.row["row_version"] = int(self.row["row_version"]) + 1
            return _Result(rowcount=1)
        if "SET status='DEAD_LETTER'" in sql:
            self.log.append("dead")
            self.row["status"] = "DEAD_LETTER"
            self.row["attempt_count"] = parameters["attempt_count"]
            self.row["last_error"] = parameters["error_code"]
            self.row["row_version"] = int(self.row["row_version"]) + 1
            return _Result(rowcount=1)
        if "SET attempt_count=:attempt_count" in sql:
            self.log.append("retry")
            self.row["attempt_count"] = parameters["attempt_count"]
            self.row["last_error"] = parameters["error_code"]
            self.row["row_version"] = int(self.row["row_version"]) + 1
            return _Result(rowcount=1)
        raise AssertionError(sql)


class _Engine:
    def __init__(
        self,
        row: dict[str, object],
        *,
        runtime_role_ok: bool = True,
        pipeline_mode: str = "V2_PRIMARY",
    ) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.connection = _Connection(
            row,
            runtime_role_ok=runtime_role_ok,
            pipeline_mode=pipeline_mode,
        )

    @contextmanager
    def begin(self):
        self.connection.log.append("outer_begin")
        yield self.connection
        self.connection.log.append("outer_commit")


class _Processor:
    def __init__(
        self,
        error_code: str | None = None,
        *,
        unexpected: bool = False,
        counts: dict[str, int] | None = None,
    ):
        self.error_code = error_code
        self.unexpected = unexpected
        self.counts = counts

    def process_event(self, connection: _Connection, event: object) -> dict[str, int]:
        connection.domain_writes.append("partial-or-complete-domain-write")
        if self.error_code is not None:
            raise DtsV2OutboxProcessingError(self.error_code)
        if self.unexpected:
            raise RuntimeError("must not be persisted")
        if self.counts is not None:
            return self.counts
        return {"teachers": 1, "scores": 2}


class _Cases:
    def record_dead_letter(
        self,
        connection: _Connection,
        event: Any,
        *,
        error_code: str,
    ) -> None:
        connection.log.append("case_dead")
        connection.dead_cases.append((event.event_id, error_code))

    def resolve_after_success(self, connection: _Connection, event: Any) -> None:
        connection.log.append("case_recovery")
        connection.case_resolutions.append(event.event_id)


def _worker(
    row: dict[str, object],
    *,
    processor: _Processor,
) -> tuple[DtsV2OutboxWorker, _Connection]:
    engine = _Engine(row)
    return (
        DtsV2OutboxWorker(
            engine,  # type: ignore[arg-type]
            processor=processor,
            technical_cases=_Cases(),
            primary_guard=None,
        ),
        engine.connection,
    )


def test_success_publishes_in_the_same_savepoint_and_resolves_old_case() -> None:
    worker, connection = _worker(_event(recovery_count=1), processor=_Processor())

    result = worker.run_once(max_events=2)

    assert result == {
        "claimed": 1,
        "published": 1,
        "retries": 0,
        "dead_letters": 0,
        "handler_counts": {"scores": 2, "teachers": 1},
    }
    assert connection.row["status"] == "PUBLISHED"
    assert connection.domain_writes == ["partial-or-complete-domain-write"]
    assert len(connection.case_resolutions) == 1
    assert connection.log.index("runtime_role") < connection.log.index("claim")
    assert connection.log.index("publish") < connection.log.index(
        "case_recovery"
    )
    assert connection.log.index("publish") < connection.log.index("savepoint_commit")


def test_dependency_wait_does_not_consume_retry_or_enter_dead_letter() -> None:
    worker, connection = _worker(
        _event(attempt_count=7),
        processor=_Processor(
            counts={"teacher_regional_dependency_waits": 1}
        ),
    )

    result = worker.run_once(max_events=1)

    assert result == {
        "claimed": 1,
        "published": 1,
        "retries": 0,
        "dead_letters": 0,
        "handler_counts": {"teacher_regional_dependency_waits": 1},
    }
    assert connection.row["status"] == "PUBLISHED"
    assert connection.row["attempt_count"] == 7
    assert connection.row["last_error"] is None


def test_production_guarded_worker_is_inert_outside_primary() -> None:
    engine = _Engine(_event())

    class _ClosedGuard:
        def acquire(self, connection, *, component):
            del connection
            assert component == "OUTBOX"
            return None

    worker = DtsV2OutboxWorker(
        engine,  # type: ignore[arg-type]
        processor=_Processor(),
        technical_cases=_Cases(),
        primary_guard=_ClosedGuard(),
    )
    assert worker.run_once(max_events=1) == {
        "claimed": 0,
        "published": 0,
        "retries": 0,
        "dead_letters": 0,
        "handler_counts": {},
    }
    assert "claim" not in engine.connection.log
    assert engine.connection.row["status"] == "PENDING"


def test_handler_failure_rolls_back_business_writes_before_retry_state() -> None:
    worker, connection = _worker(
        _event(),
        processor=_Processor("SCORE_DEPENDENCY_TRANSIENT"),
    )

    result = worker.run_once(max_events=1)

    assert result["retries"] == 1
    assert connection.domain_writes == []
    assert connection.row["status"] == "PENDING"
    assert connection.row["attempt_count"] == 1
    assert connection.row["last_error"] == "SCORE_DEPENDENCY_TRANSIENT"
    assert connection.log.index("savepoint_rollback") < connection.log.index("retry")


def test_eighth_failure_writes_case_and_dead_letter_in_one_outer_transaction() -> None:
    worker, connection = _worker(
        _event(attempt_count=7),
        processor=_Processor("TASK_PLAN_DEPENDENCY_TRANSIENT"),
    )

    result = worker.run_once(max_events=1)

    assert result["dead_letters"] == 1
    assert connection.domain_writes == []
    assert connection.row["status"] == "DEAD_LETTER"
    assert connection.row["attempt_count"] == 8
    assert connection.dead_cases == [
        (connection.row["event_id"], "TASK_PLAN_DEPENDENCY_TRANSIENT")
    ]
    assert connection.log.index("savepoint_rollback") < connection.log.index("dead")
    assert connection.log.index("dead") < connection.log.index("case_dead")


def test_worker_rejects_non_dedicated_database_role_before_claim() -> None:
    engine = _Engine(_event(), runtime_role_ok=False)
    worker = DtsV2OutboxWorker(
        engine,  # type: ignore[arg-type]
        processor=_Processor(),
        technical_cases=_Cases(),
        primary_guard=None,
    )

    with pytest.raises(
        DtsV2OutboxWorkerError,
        match="DTS_V2_OUTBOX_RUNTIME_ROLE_REQUIRED",
    ):
        worker.run_once(max_events=1)

    assert "claim" not in engine.connection.log


@pytest.mark.parametrize(
    "pipeline_mode", ["V1_COMPAT_DUAL_CAPTURE", "ROLLED_BACK"]
)
def test_worker_does_not_claim_production_outbox_outside_v2_primary(
    pipeline_mode: str,
) -> None:
    engine = _Engine(_event(), pipeline_mode=pipeline_mode)

    class _ModeGuard:
        def acquire(self, connection, *, component):
            assert component == "OUTBOX"
            return 1 if connection.pipeline_mode == "V2_PRIMARY" else None

    worker = DtsV2OutboxWorker(
        engine,  # type: ignore[arg-type]
        processor=_Processor(),
        technical_cases=_Cases(),
        primary_guard=_ModeGuard(),
    )

    result = worker.run_once(max_events=1)

    assert result["claimed"] == 0
    assert engine.connection.row["status"] == "PENDING"
    assert engine.connection.domain_writes == []


def test_unexpected_exception_is_not_persisted_and_uses_public_error_code() -> None:
    worker, connection = _worker(
        _event(),
        processor=_Processor(unexpected=True),
    )

    worker.run_once(max_events=1)

    assert connection.domain_writes == []
    assert connection.row["last_error"] == "DOWNSTREAM_PROJECTION_TRANSIENT"


def test_payload_hash_mismatch_fails_before_handler() -> None:
    worker, connection = _worker(
        _event(payload_hash_matches=False),
        processor=_Processor(),
    )

    with pytest.raises(
        DtsV2OutboxWorkerError,
        match="DTS_V2_OUTBOX_PAYLOAD_HASH_MISMATCH",
    ):
        worker.run_once(max_events=1)

    assert connection.domain_writes == []
    assert connection.row["status"] == "PENDING"


def test_task_plan_event_requires_task_plan_aggregate() -> None:
    worker, _connection = _worker(
        _event(event_type="task.materialization.requested.v2"),
        processor=_Processor(),
    )

    with pytest.raises(
        DtsV2OutboxWorkerError,
        match="DTS_V2_OUTBOX_AGGREGATE_TYPE_INVALID",
    ):
        worker.run_once(max_events=1)


def test_invalid_handler_error_code_never_enters_last_error() -> None:
    with pytest.raises(
        DtsV2OutboxWorkerError,
        match="DTS_V2_OUTBOX_ERROR_CODE_INVALID",
    ):
        DtsV2OutboxProcessingError("contains sensitive text")
