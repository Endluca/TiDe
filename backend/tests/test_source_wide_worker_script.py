from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import scripts.run_source_wide_worker as source_worker_script
from scripts.run_source_wide_worker import (
    DEFAULT_HEARTBEAT_PATH,
    DEFAULT_READINESS_PATH,
    SourceWideLeadership,
    _SOURCE_WORKER_IDENTITY,
    _heartbeat_is_fresh,
    _poll_source_once,
    _run_worker,
    _SOURCE_OUTBOX_HEALTH,
    _source_outbox_health_issue,
    _stable_leader_retry_seconds,
    _validate_source_worker_identity,
    _validated_source_worker_database_url,
    _worker_health_is_fresh,
    _write_heartbeat,
    _write_readiness,
)
from scripts.settle_shared_task_scores import (
    _LEADER_LOCK_KEY as SCORE_WORKER_LEADER_LOCK_KEY,
)


def test_source_worker_uses_an_independent_leader_key_and_heartbeat() -> None:
    assert SCORE_WORKER_LEADER_LOCK_KEY == 2
    assert DEFAULT_HEARTBEAT_PATH == Path("/tmp/tit-source-worker-heartbeat")
    assert DEFAULT_READINESS_PATH == Path("/tmp/tit-source-worker-readiness")


def test_source_worker_rejects_invalid_qualification_grant_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "TRUE",
    )
    assert source_worker_script.main() == 2
    assert capsys.readouterr().err.strip() == (
        "IRREVERSIBLE_QUALIFICATION_AWARD_GATE_INVALID"
    )


def test_source_worker_runtime_does_not_load_legacy_lesson_importer() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.source_wide_worker; "
                "raise SystemExit(int('app.lesson_ingestion' in sys.modules))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_source_worker_heartbeat_is_atomic_and_expires(tmp_path: Path) -> None:
    heartbeat = tmp_path / "source-worker-heartbeat"

    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False
    _write_heartbeat(heartbeat)
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True
    assert list(tmp_path.glob(".*.tmp")) == []

    old = time.time() - 30
    os.utime(heartbeat, (old, old))
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False


def test_source_worker_health_requires_fresh_liveness_and_readiness(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat"
    readiness = tmp_path / "readiness"

    _write_heartbeat(heartbeat)
    assert _worker_health_is_fresh(
        heartbeat,
        readiness,
        max_heartbeat_age_seconds=15,
        max_readiness_age_seconds=30,
    ) is False

    _write_readiness(readiness)
    assert _worker_health_is_fresh(
        heartbeat,
        readiness,
        max_heartbeat_age_seconds=15,
        max_readiness_age_seconds=30,
    ) is True

    old = time.time() - 60
    os.utime(readiness, (old, old))
    assert _worker_health_is_fresh(
        heartbeat,
        readiness,
        max_heartbeat_age_seconds=15,
        max_readiness_age_seconds=30,
    ) is False


def test_source_worker_healthcheck_cli_checks_outbox_after_fresh_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = tmp_path / "heartbeat"
    readiness = tmp_path / "readiness"
    _write_heartbeat(heartbeat)
    _write_readiness(readiness)

    thresholds: list[float] = []

    def check_outbox(*, max_pending_age_seconds: float) -> None:
        thresholds.append(max_pending_age_seconds)
        return None

    monkeypatch.setenv("TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS", "1200")
    monkeypatch.setattr(
        source_worker_script,
        "_source_outbox_database_health_issue",
        check_outbox,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/run_source_wide_worker.py",
            "--healthcheck",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
            "--max-heartbeat-age-seconds",
            "90",
            "--max-readiness-age-seconds",
            "90",
        ],
    )

    assert source_worker_script.main() == 0
    assert thresholds == [1200.0]


class _OutboxHealthResult:
    def __init__(self, row: dict[str, bool]) -> None:
        self.row = row

    def mappings(self):
        return self

    def one(self) -> dict[str, bool]:
        return self.row


class _OutboxHealthConnection:
    def __init__(self, *, dead_letter: bool, stale_pending: bool) -> None:
        self.row = {
            "has_dead_letter": dead_letter,
            "has_stale_pending": stale_pending,
        }
        self.parameters: dict[str, float] | None = None

    def execute(self, statement, parameters):
        assert statement is _SOURCE_OUTBOX_HEALTH
        self.parameters = parameters
        return _OutboxHealthResult(self.row)


@pytest.mark.parametrize(
    ("dead_letter", "stale_pending", "expected_issue"),
    [
        (False, False, None),
        (True, False, "SOURCE_WIDE_OUTBOX_DEAD_LETTER"),
        (False, True, "SOURCE_WIDE_OUTBOX_STALE_PENDING"),
        (True, True, "SOURCE_WIDE_OUTBOX_DEAD_LETTER"),
    ],
)
def test_source_outbox_health_fails_closed_on_terminal_or_stale_events(
    dead_letter: bool,
    stale_pending: bool,
    expected_issue: str | None,
) -> None:
    connection = _OutboxHealthConnection(
        dead_letter=dead_letter,
        stale_pending=stale_pending,
    )

    assert _source_outbox_health_issue(
        connection,
        max_pending_age_seconds=900,
    ) == expected_issue
    assert connection.parameters == {"max_pending_age_seconds": 900}
    sql = str(_SOURCE_OUTBOX_HEALTH)
    assert "event_type = 'source_wide.changed.v1'" in sql
    assert "status = 'DEAD_LETTER'" in sql
    assert "status = 'PENDING'" in sql
    assert "attempt_count > 0" in sql
    assert "available_at <= clock_timestamp() - make_interval" in sql


@pytest.mark.parametrize(
    "issue",
    ["SOURCE_WIDE_OUTBOX_DEAD_LETTER", "SOURCE_WIDE_OUTBOX_STALE_PENDING"],
)
def test_source_worker_healthcheck_exits_nonzero_for_outbox_issue(
    issue: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    heartbeat = tmp_path / "heartbeat"
    readiness = tmp_path / "readiness"
    _write_heartbeat(heartbeat)
    _write_readiness(readiness)
    monkeypatch.setattr(
        source_worker_script,
        "_source_outbox_database_health_issue",
        lambda **_kwargs: issue,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/run_source_wide_worker.py",
            "--healthcheck",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ],
    )

    assert source_worker_script.main() == 1
    assert capsys.readouterr().err.strip() == issue


def test_production_source_worker_reuses_verified_tide_backend_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://tit_growth_app@example/tit_growth?sslmode=verify-full",
    )
    monkeypatch.setenv("TIT_SOURCE_WORKER_EXPECTED_DATABASE", "tit_growth")
    assert "tit_growth_app@example" in _validated_source_worker_database_url()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://tit_growth_app@example/tit_growth?sslmode=verify-ca",
    )
    with pytest.raises(
        RuntimeError,
        match="SOURCE_WORKER_DATABASE_URL_REQUIRES_VERIFIED_TLS",
    ):
        _validated_source_worker_database_url()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://tit_growth_app@example/other?sslmode=verify-full",
    )
    with pytest.raises(RuntimeError, match="SOURCE_WORKER_DATABASE_TARGET_MISMATCH"):
        _validated_source_worker_database_url()


class _MappingResult:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


def _valid_identity() -> dict[str, object]:
    return {
        "database_name": "tit_growth_test_v2",
        "current_role": "tit_growth_app",
        "session_role": "tit_growth_app",
        "can_login": True,
        "is_superuser": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "can_create_public_objects": False,
        "can_read_teacher_source": True,
        "can_read_lesson_source": True,
        "can_write_teacher_source": False,
        "can_write_lesson_source": False,
        "has_outbox_crud": True,
        "has_outbox_non_crud_privileges": False,
    }


def test_source_worker_runtime_identity_is_restricted() -> None:
    class _IdentityConnection:
        def __init__(self, row: dict[str, object]) -> None:
            self.row = row

        def execute(self, _statement):
            return _MappingResult(self.row)

    _validate_source_worker_identity(
        _IdentityConnection(_valid_identity()),
        expected_database="tit_growth_test_v2",
    )

    wrong_login = _valid_identity()
    wrong_login["current_role"] = "another_source_worker_login"
    wrong_login["session_role"] = "another_source_worker_login"
    with pytest.raises(
        RuntimeError,
        match="SOURCE_WORKER_LOGIN_ROLE_MISMATCH",
    ):
        _validate_source_worker_identity(
            _IdentityConnection(wrong_login),
            expected_database="tit_growth_test_v2",
        )

    source_writer = _valid_identity()
    source_writer["can_write_teacher_source"] = True
    with pytest.raises(
        RuntimeError,
        match="SOURCE_WORKER_MUST_NOT_WRITE_SOURCE_TABLES",
    ):
        _validate_source_worker_identity(
            _IdentityConnection(source_writer),
            expected_database="tit_growth_test_v2",
        )

    missing_outbox_crud = _valid_identity()
    missing_outbox_crud["has_outbox_crud"] = False
    with pytest.raises(
        RuntimeError,
        match="SOURCE_WORKER_OUTBOX_PRIVILEGES_INVALID",
    ):
        _validate_source_worker_identity(
            _IdentityConnection(missing_outbox_crud),
            expected_database="tit_growth_test_v2",
        )

    outbox_non_crud = _valid_identity()
    outbox_non_crud["has_outbox_non_crud_privileges"] = True
    with pytest.raises(
        RuntimeError,
        match="SOURCE_WORKER_OUTBOX_PRIVILEGES_INVALID",
    ):
        _validate_source_worker_identity(
            _IdentityConnection(outbox_non_crud),
            expected_database="tit_growth_test_v2",
        )


def test_source_worker_identity_query_requires_outbox_crud() -> None:
    query = str(_SOURCE_WORKER_IDENTITY)

    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"'public.outbox_events', '{privilege}'" in query
    assert "has_column_privilege" not in query
    assert "'TRUNCATE, TRIGGER'" in query
    assert "'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER'" in query


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
        assert parameters == {"namespace": 544954, "key": 3}
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


def test_only_one_source_session_leads_and_disconnect_allows_takeover() -> None:
    registry = _SessionLockRegistry()
    engine = _FakePostgresEngine(registry)
    first = SourceWideLeadership(engine)
    second = SourceWideLeadership(engine)

    assert first.try_acquire() is True
    assert second.try_acquire() is False
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


def test_source_standby_is_healthy_and_never_processes(tmp_path: Path) -> None:
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

    calls = 0

    def process_once(*, bind, max_events: int) -> dict:
        nonlocal calls
        calls += 1
        return {"claimed": 0, "failed": 0}

    heartbeat = tmp_path / "standby-heartbeat"
    assert _run_worker(
        _Standby(),
        max_events=25,
        watch=False,
        interval_seconds=3,
        leader_retry_seconds=10,
        heartbeat_path=heartbeat,
        process_once=process_once,
    ) == 0
    assert calls == 0
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True


def test_source_processing_uses_the_verified_leader_connection() -> None:
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

    result = _poll_source_once(
        _Leader(),
        max_events=17,
        process_once=lambda *, bind, max_events: {
            "claimed": 0,
            "failed": 0,
            "same_connection": bind is leader_connection,
            "max_events": max_events,
        },
    )

    assert result == {
        "claimed": 0,
        "failed": 0,
        "same_connection": True,
        "max_events": 17,
    }


def test_source_leader_retry_jitter_is_stable_and_pod_specific() -> None:
    first = _stable_leader_retry_seconds(10, identity="pod-a:42")
    repeated = _stable_leader_retry_seconds(10, identity="pod-a:42")
    second = _stable_leader_retry_seconds(10, identity="pod-b:42")

    assert 9 <= first <= 11
    assert first == repeated
    assert first != second
