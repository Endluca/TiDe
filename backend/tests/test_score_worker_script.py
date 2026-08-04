from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app.database import engine
from app.shared_task_score_settlement import settle_shared_task_scores_once
from scripts.settle_shared_task_scores import (
    ScoreSettlementLeadership,
    _heartbeat_is_fresh,
    _poll_settlement_once,
    _run_worker,
    _stable_leader_retry_seconds,
    _write_heartbeat,
)


def test_score_worker_heartbeat_is_atomic_and_expires(tmp_path: Path) -> None:
    heartbeat = tmp_path / "score-worker-heartbeat"

    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False
    _write_heartbeat(heartbeat)
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True
    assert list(tmp_path.glob(".*.tmp")) == []

    old = time.time() - 30
    os.utime(heartbeat, (old, old))
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False


class _PostgresDialect:
    name = "postgresql"


class _SessionLockRegistry:
    def __init__(self) -> None:
        self.holder = None


class _FakeConnection:
    def __init__(self, registry: _SessionLockRegistry) -> None:
        self.registry = registry
        self.commit_count = 0
        self.closed = False
        self.broken = False

    def scalar(self, statement, parameters):
        assert "pg_try_advisory_lock" in str(statement)
        assert parameters == {"namespace": 544954, "key": 2}
        if self.registry.holder is None:
            self.registry.holder = self
            return True
        return self.registry.holder is self

    def execute(self, statement):
        assert str(statement) == "SELECT 1"
        if self.broken:
            if self.registry.holder is self:
                self.registry.holder = None
            raise OperationalError("SELECT 1", {}, RuntimeError("disconnected"))

    def commit(self) -> None:
        self.commit_count += 1

    def invalidate(self) -> None:
        if self.registry.holder is self:
            self.registry.holder = None

    def close(self) -> None:
        self.closed = True


class _FakePostgresEngine:
    dialect = _PostgresDialect()

    def __init__(self, registry: _SessionLockRegistry) -> None:
        self.registry = registry
        self.connections: list[_FakeConnection] = []

    def connect(self) -> _FakeConnection:
        connection = _FakeConnection(self.registry)
        self.connections.append(connection)
        return connection


def test_only_one_session_leads_and_connection_loss_allows_takeover() -> None:
    registry = _SessionLockRegistry()
    engine = _FakePostgresEngine(registry)
    first = ScoreSettlementLeadership(engine)
    second = ScoreSettlementLeadership(engine)

    assert first.try_acquire() is True
    assert first.is_leader is True
    assert second.try_acquire() is False
    assert second.is_leader is False
    assert engine.connections[0].commit_count == 1

    engine.connections[0].broken = True
    with pytest.raises(OperationalError):
        first.verify()
    assert first.is_leader is False
    assert second.try_acquire() is True
    assert second.verify() is True
    assert engine.connections[-1].commit_count == 2

    second.close()
    assert registry.holder is None


def test_standby_is_healthy_and_never_runs_settlement(tmp_path: Path) -> None:
    class _Standby:
        is_leader = False
        connection = None

        @staticmethod
        def try_acquire() -> bool:
            return False

        @staticmethod
        def verify() -> bool:
            raise AssertionError("standby must not verify leadership")

        @staticmethod
        def close() -> None:
            return None

    settlement_calls = 0

    def settle_once(*, bind, max_events: int) -> dict:
        nonlocal settlement_calls
        settlement_calls += 1
        return {"claimed": 0, "failed": 0}

    heartbeat = tmp_path / "standby-heartbeat"
    exit_code = _run_worker(
        _Standby(),
        max_events=25,
        watch=False,
        interval_seconds=3,
        leader_retry_seconds=10,
        heartbeat_path=heartbeat,
        settle_once=settle_once,
    )

    assert exit_code == 0
    assert settlement_calls == 0
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True


def test_standby_retries_lock_less_often_than_it_refreshes_heartbeat(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Standby:
        is_leader = False
        connection = None
        acquisition_attempts = 0

        def try_acquire(self) -> bool:
            self.acquisition_attempts += 1
            return False

        @staticmethod
        def verify() -> bool:
            raise AssertionError("standby must not verify leadership")

        @staticmethod
        def close() -> None:
            return None

    standby = _Standby()
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        if now[0] > 12:
            raise KeyboardInterrupt

    heartbeat = tmp_path / "standby-watch-heartbeat"
    exit_code = _run_worker(
        standby,
        max_events=25,
        watch=True,
        interval_seconds=3,
        leader_retry_seconds=10,
        heartbeat_path=heartbeat,
        settle_once=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("standby must not settle")
        ),
        monotonic=lambda: now[0],
        sleep=sleep,
    )

    assert exit_code == 0
    assert standby.acquisition_attempts == 2
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True
    assert capsys.readouterr().out.splitlines() == [
        '{"event": "standby", "role": "standby"}'
    ]


def test_leader_retry_jitter_is_small_stable_and_pod_specific() -> None:
    first = _stable_leader_retry_seconds(10, identity="pod-a:42")
    repeated = _stable_leader_retry_seconds(10, identity="pod-a:42")
    second = _stable_leader_retry_seconds(10, identity="pod-b:42")

    assert 9 <= first <= 11
    assert first == repeated
    assert first != second


def test_role_logs_cover_leader_acquisition_and_connection_loss(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    leader_connection = object()

    class _Candidate:
        is_leader = False
        connection = None

        def try_acquire(self) -> bool:
            self.is_leader = True
            self.connection = leader_connection
            return True

        @staticmethod
        def verify() -> bool:
            return True

        def close(self) -> None:
            self.is_leader = False
            self.connection = None

    candidate = _Candidate()
    assert _run_worker(
        candidate,
        max_events=1,
        watch=False,
        interval_seconds=3,
        leader_retry_seconds=10,
        heartbeat_path=tmp_path / "leader-heartbeat",
        settle_once=lambda *, bind, max_events: {
            "claimed": 0,
            "failed": 0,
            "bind_matches": bind is leader_connection,
            "max_events": max_events,
        },
    ) == 0
    acquisition_logs = capsys.readouterr().out.splitlines()
    assert acquisition_logs[0] == (
        '{"event": "leader_acquired", "role": "leader"}'
    )
    assert '"bind_matches": true' in acquisition_logs[1]

    class _LostLeader:
        is_leader = True
        connection = leader_connection

        @staticmethod
        def try_acquire() -> bool:
            raise AssertionError("existing leader must not reacquire")

        @staticmethod
        def verify() -> bool:
            raise OperationalError(
                "SELECT 1",
                {},
                RuntimeError("disconnected"),
            )

        def close(self) -> None:
            self.is_leader = False
            self.connection = None

    assert _run_worker(
        _LostLeader(),
        max_events=1,
        watch=False,
        interval_seconds=3,
        leader_retry_seconds=10,
        heartbeat_path=tmp_path / "lost-heartbeat",
        settle_once=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("lost leader must not settle")
        ),
    ) == 2
    assert capsys.readouterr().out.splitlines() == [
        '{"event": "leader_connection_lost_retry", "role": "standby"}'
    ]


def test_polling_calls_settlement_only_after_leadership_is_verified() -> None:
    leader_connection = object()

    class _Leader:
        is_leader = True
        connection = leader_connection

        @staticmethod
        def try_acquire() -> bool:
            raise AssertionError("held leadership must not be reacquired")

        @staticmethod
        def verify() -> bool:
            return True

    calls: list[int] = []

    result = _poll_settlement_once(
        _Leader(),
        max_events=17,
        settle_once=lambda *, bind, max_events: (
            calls.append(max_events)
            or {"claimed": 0, "failed": 0, "bind": bind}
        ),
    )

    assert result == {
        "claimed": 0,
        "failed": 0,
        "bind": leader_connection,
    }
    assert calls == [17]


def test_settlement_can_run_on_the_connection_that_holds_leadership() -> None:
    with engine.connect() as connection:
        result = settle_shared_task_scores_once(
            bind=connection,
            max_events=1,
        )

    assert result["claimed"] == 0
    assert result["failed"] == 0
