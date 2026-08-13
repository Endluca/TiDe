from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.qualification_award_gate import (
    QualificationAwardGateConfigurationError,
    irreversible_qualification_grants_enabled,
)
from app.runtime_settings import source_worker_database_transport_mode


DEFAULT_HEARTBEAT_PATH = Path("/tmp/tit-source-worker-heartbeat")
DEFAULT_READINESS_PATH = Path("/tmp/tit-source-worker-readiness")
_LEADER_LOCK_NAMESPACE = 544954
_LEADER_LOCK_KEY = 3
_TRY_LEADER_LOCK = text("SELECT pg_try_advisory_lock(:namespace, :key)")
_LEADER_CONNECTION_PING = text("SELECT 1")
_SOURCE_WORKER_IDENTITY = text(
    """
    SELECT
        current_database() AS database_name,
        current_user::text AS current_role,
        session_user::text AS session_role,
        login_role.rolcanlogin AS can_login,
        login_role.rolsuper AS is_superuser,
        login_role.rolcreatedb AS can_create_database,
        login_role.rolcreaterole AS can_create_role,
        login_role.rolreplication AS can_replicate,
        login_role.rolbypassrls AS can_bypass_rls,
        has_schema_privilege(current_user, 'public', 'CREATE')
            AS can_create_public_objects,
        has_table_privilege(
            current_user, 'public.teacher_source_wide', 'SELECT'
        ) AS can_read_teacher_source,
        has_table_privilege(
            current_user, 'public.lesson_source_wide', 'SELECT'
        ) AS can_read_lesson_source,
        has_table_privilege(
            current_user,
            'public.teacher_source_wide',
            'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER'
        ) AS can_write_teacher_source,
        has_table_privilege(
            current_user,
            'public.lesson_source_wide',
            'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER'
        ) AS can_write_lesson_source,
        (
            has_table_privilege(
                current_user, 'public.outbox_events', 'SELECT'
            )
            AND has_table_privilege(
                current_user, 'public.outbox_events', 'INSERT'
            )
            AND has_table_privilege(
                current_user, 'public.outbox_events', 'UPDATE'
            )
            AND has_table_privilege(
                current_user, 'public.outbox_events', 'DELETE'
            )
        ) AS has_outbox_crud,
        has_table_privilege(
            current_user,
            'public.outbox_events',
            'TRUNCATE, TRIGGER'
        ) AS has_outbox_non_crud_privileges
    FROM pg_roles AS login_role
    WHERE login_role.rolname = session_user
    """
)
_SOURCE_OUTBOX_HEALTH = text(
    """
    SELECT
        EXISTS (
            SELECT 1
            FROM public.outbox_events
            WHERE event_type = 'source_wide.changed.v1'
              AND status = 'DEAD_LETTER'
        ) AS has_dead_letter,
        EXISTS (
            SELECT 1
            FROM public.outbox_events
            WHERE event_type = 'source_wide.changed.v1'
              AND status = 'PENDING'
              AND attempt_count > 0
              AND available_at <= clock_timestamp() - make_interval(
                  secs => CAST(:max_pending_age_seconds AS double precision)
              )
        ) AS has_stale_pending
    """
)
_DEFAULT_LEADER_RETRY_SECONDS = 10.0
_DEFAULT_MAX_PENDING_AGE_SECONDS = 900.0


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "local").strip().lower() in {
        "prod",
        "production",
    }


def _validated_source_worker_database_url() -> str:
    """Resolve the TiDe backend URL shared by API and internal workers."""

    resolved = os.environ.get("DATABASE_URL", "").strip()
    if not resolved:
        raise RuntimeError("SOURCE_WORKER_DATABASE_URL_REQUIRED")
    if not resolved.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("SOURCE_WORKER_DATABASE_URL_MUST_USE_POSTGRESQL")

    parsed = urlparse(
        resolved.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    expected_database = os.environ.get(
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE",
        "",
    ).strip()
    selected_database = parsed.path.removeprefix("/")
    if _is_production() and not expected_database:
        raise RuntimeError(
            "TIT_SOURCE_WORKER_EXPECTED_DATABASE_REQUIRED_IN_PRODUCTION"
        )
    if expected_database and selected_database != expected_database:
        raise RuntimeError("SOURCE_WORKER_DATABASE_TARGET_MISMATCH")
    if _is_production():
        try:
            source_worker_database_transport_mode(resolved)
        except ValueError:
            raise RuntimeError(
                "SOURCE_WORKER_DATABASE_URL_REQUIRES_VERIFIED_TLS"
            ) from None
    return resolved


def _validate_source_worker_identity(
    connection: Connection,
    *,
    expected_database: str | None,
) -> None:
    row = connection.execute(_SOURCE_WORKER_IDENTITY).mappings().one_or_none()
    if row is None:
        raise RuntimeError("SOURCE_WORKER_LOGIN_ROLE_NOT_FOUND")
    if expected_database and row["database_name"] != expected_database:
        raise RuntimeError("SOURCE_WORKER_DATABASE_IDENTITY_MISMATCH")
    if row["current_role"] != row["session_role"]:
        raise RuntimeError("SOURCE_WORKER_SET_ROLE_IS_FORBIDDEN")
    if row["session_role"] != "tit_growth_app":
        raise RuntimeError("SOURCE_WORKER_LOGIN_ROLE_MISMATCH")
    if not row["can_login"] or row["is_superuser"]:
        raise RuntimeError("SOURCE_WORKER_LOGIN_MUST_BE_RESTRICTED")
    if any(
        row[field]
        for field in (
            "can_create_database",
            "can_create_role",
            "can_replicate",
            "can_bypass_rls",
            "can_create_public_objects",
        )
    ):
        raise RuntimeError("SOURCE_WORKER_LOGIN_HAS_ADMIN_PRIVILEGES")
    if not row["can_read_teacher_source"] or not row["can_read_lesson_source"]:
        raise RuntimeError("SOURCE_WORKER_SOURCE_READ_PRIVILEGES_MISSING")
    if row["can_write_teacher_source"] or row["can_write_lesson_source"]:
        raise RuntimeError("SOURCE_WORKER_MUST_NOT_WRITE_SOURCE_TABLES")
    if not row["has_outbox_crud"] or row["has_outbox_non_crud_privileges"]:
        raise RuntimeError("SOURCE_WORKER_OUTBOX_PRIVILEGES_INVALID")


class SourceWideLeadership:
    """Hold the SourceWide leader lock on one PostgreSQL session."""

    def __init__(
        self,
        bind: Engine,
        *,
        identity_validator: Callable[[Connection], None] | None = None,
    ) -> None:
        self._bind = bind
        self._connection: Connection | None = None
        self._identity_validator = identity_validator

    @property
    def is_leader(self) -> bool:
        return self._connection is not None

    @property
    def connection(self) -> Connection | None:
        return self._connection

    def try_acquire(self) -> bool:
        if self._connection is not None:
            return True
        if self._bind.dialect.name != "postgresql":
            raise RuntimeError("SourceWide leader election requires PostgreSQL")

        connection = self._bind.connect()
        try:
            if self._identity_validator is not None:
                self._identity_validator(connection)
            acquired = bool(
                connection.scalar(
                    _TRY_LEADER_LOCK,
                    {
                        "namespace": _LEADER_LOCK_NAMESPACE,
                        "key": _LEADER_LOCK_KEY,
                    },
                )
            )
            # The session lock survives COMMIT. End the implicit transaction so
            # idle_in_transaction_session_timeout cannot evict a healthy leader.
            connection.commit()
        except BaseException:
            connection.close()
            raise
        if not acquired:
            connection.close()
            return False
        self._connection = connection
        return True

    def verify(self) -> bool:
        connection = self._connection
        if connection is None:
            return False
        try:
            connection.execute(_LEADER_CONNECTION_PING)
            connection.commit()
        except SQLAlchemyError:
            self.close()
            raise
        return True

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        # A physical disconnect is the release protocol. PostgreSQL also
        # releases the session advisory lock after network loss.
        try:
            connection.invalidate()
        finally:
            connection.close()


def _write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{time.time():.6f}\n", encoding="ascii")
    temporary.replace(path)


def _write_readiness(path: Path) -> None:
    _write_heartbeat(path)


def _heartbeat_is_fresh(path: Path, *, max_age_seconds: float) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age_seconds <= max_age_seconds


def _worker_health_is_fresh(
    heartbeat_path: Path,
    readiness_path: Path,
    *,
    max_heartbeat_age_seconds: float,
    max_readiness_age_seconds: float,
) -> bool:
    return _heartbeat_is_fresh(
        heartbeat_path,
        max_age_seconds=max_heartbeat_age_seconds,
    ) and _heartbeat_is_fresh(
        readiness_path,
        max_age_seconds=max_readiness_age_seconds,
    )


def _source_outbox_health_issue(
    connection: Connection,
    *,
    max_pending_age_seconds: float,
) -> str | None:
    if max_pending_age_seconds <= 0:
        raise ValueError("max_pending_age_seconds must be greater than 0")
    row = connection.execute(
        _SOURCE_OUTBOX_HEALTH,
        {"max_pending_age_seconds": max_pending_age_seconds},
    ).mappings().one()
    if bool(row["has_dead_letter"]):
        return "SOURCE_WIDE_OUTBOX_DEAD_LETTER"
    if bool(row["has_stale_pending"]):
        return "SOURCE_WIDE_OUTBOX_STALE_PENDING"
    return None


def _source_outbox_database_health_issue(
    *,
    max_pending_age_seconds: float,
) -> str | None:
    source_worker_url = _validated_source_worker_database_url()
    from app.database import build_engine

    health_engine = build_engine(source_worker_url, poolclass=NullPool)
    try:
        with health_engine.connect() as connection:
            expected_database = (
                os.environ.get(
                    "TIT_SOURCE_WORKER_EXPECTED_DATABASE",
                    "",
                ).strip()
                or None
            )
            if _is_production() or expected_database:
                _validate_source_worker_identity(
                    connection,
                    expected_database=expected_database,
                )
            return _source_outbox_health_issue(
                connection,
                max_pending_age_seconds=max_pending_age_seconds,
            )
    finally:
        health_engine.dispose()


def _stable_leader_retry_seconds(
    base_seconds: float,
    *,
    identity: str | None = None,
) -> float:
    """Return a Pod-stable 90%-110% retry delay to avoid a lock herd."""

    resolved_identity = identity or (
        f"{os.environ.get('HOSTNAME', 'local')}:{os.getpid()}"
    )
    digest = hashlib.sha256(resolved_identity.encode("utf-8")).digest()
    unit = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
    return base_seconds * (0.9 + (unit * 0.2))


def _poll_source_once(
    leadership: SourceWideLeadership,
    *,
    max_events: int,
    process_once: Callable[..., dict[str, Any]],
    attempt_leadership: bool = True,
) -> dict[str, Any] | None:
    if not leadership.is_leader:
        if not attempt_leadership or not leadership.try_acquire():
            return None
    if not leadership.verify():
        return None
    connection = leadership.connection
    if connection is None:
        return None
    # Consume on the session holding leadership. This closes the gap between
    # verifying the leader and opening an unrelated work connection.
    return process_once(bind=connection, max_events=max_events)


def _log_role_transition(
    current_role: str | None,
    *,
    event: str,
    role: str,
) -> str:
    if current_role != role:
        print(
            json.dumps(
                {"event": event, "role": role, "worker": "source_wide"},
                ensure_ascii=True,
                sort_keys=True,
            ),
            flush=True,
        )
    return role


def _run_worker(
    leadership: SourceWideLeadership,
    *,
    max_events: int,
    watch: bool,
    interval_seconds: float,
    leader_retry_seconds: float,
    heartbeat_path: Path,
    readiness_path: Path | None = None,
    process_once: Callable[..., dict[str, Any]],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    resolved_readiness_path = readiness_path or heartbeat_path.with_name(
        f"{heartbeat_path.name}-readiness"
    )
    _write_heartbeat(heartbeat_path)
    next_leadership_attempt_at = 0.0
    reported_role: str | None = None
    while not stop_requested():
        # Heartbeat proves this local candidate is alive. A standby is healthy
        # and ready for takeover even though it must not consume events.
        _write_heartbeat(heartbeat_path)
        now = monotonic()
        attempt_leadership = (
            leadership.is_leader or now >= next_leadership_attempt_at
        )
        was_leader = leadership.is_leader
        try:
            result = _poll_source_once(
                leadership,
                max_events=max_events,
                process_once=process_once,
                attempt_leadership=attempt_leadership,
            )
            if attempt_leadership:
                # A leader ping or a standby lock attempt both prove that this
                # candidate can authenticate to the intended database.  A mere
                # process heartbeat must not be treated as database readiness.
                _write_readiness(resolved_readiness_path)
            if attempt_leadership and not leadership.is_leader:
                next_leadership_attempt_at = now + leader_retry_seconds
            if leadership.is_leader:
                reported_role = _log_role_transition(
                    reported_role,
                    event="leader_acquired",
                    role="leader",
                )
            elif result is None:
                reported_role = _log_role_transition(
                    reported_role,
                    event="standby",
                    role="standby",
                )
        except SQLAlchemyError:
            leadership.close()
            next_leadership_attempt_at = now + leader_retry_seconds
            reported_role = _log_role_transition(
                reported_role,
                event=(
                    "leader_connection_lost_retry"
                    if was_leader
                    else "leader_election_retry"
                ),
                role="standby",
            )
            if not watch:
                return 2
            result = None
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _write_heartbeat(heartbeat_path)
        if result is not None:
            if not watch or result["claimed"] or result["failed"]:
                print(
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    flush=True,
                )
            if not watch:
                return 0 if result["failed"] == 0 else 1
        elif not watch:
            return 0

        if stop_requested():
            return 0

        try:
            sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0
    return 0


def main() -> int:
    try:
        irreversible_qualification_grants_enabled()
    except QualificationAwardGateConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        description="Consume SourceWide changes and refresh affected projections."
    )
    parser.add_argument("--max-events", type=int, default=25)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling instead of exiting after one batch.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=3.0,
        help="Polling interval used with --watch (default: 3 seconds).",
    )
    parser.add_argument(
        "--leader-retry-seconds",
        type=float,
        default=_DEFAULT_LEADER_RETRY_SECONDS,
        help=(
            "Base standby leader-election retry interval before stable "
            "Pod jitter (default: 10 seconds)."
        ),
    )
    parser.add_argument(
        "--heartbeat-path",
        type=Path,
        default=Path(
            os.environ.get(
                "TIT_SOURCE_WORKER_HEARTBEAT",
                str(DEFAULT_HEARTBEAT_PATH),
            )
        ),
    )
    parser.add_argument(
        "--readiness-path",
        type=Path,
        default=Path(
            os.environ.get(
                "TIT_SOURCE_WORKER_READINESS",
                str(DEFAULT_READINESS_PATH),
            )
        ),
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help=(
            "Exit 0 only when both process heartbeat and database readiness "
            "are fresh."
        ),
    )
    parser.add_argument(
        "--max-heartbeat-age-seconds",
        type=float,
        default=15.0,
        help="Maximum heartbeat age accepted by --healthcheck.",
    )
    parser.add_argument(
        "--max-readiness-age-seconds",
        type=float,
        default=30.0,
        help="Maximum database-readiness age accepted by --healthcheck.",
    )
    parser.add_argument(
        "--max-pending-age-seconds",
        type=float,
        default=os.environ.get(
            "TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS",
            str(_DEFAULT_MAX_PENDING_AGE_SECONDS),
        ),
        help=(
            "Maximum overdue age for a SourceWide PENDING outbox event "
            "accepted by --healthcheck (default: 900 seconds)."
        ),
    )
    args = parser.parse_args()
    if args.max_events < 1:
        parser.error("--max-events must be at least 1")
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be greater than 0")
    if args.leader_retry_seconds <= 0:
        parser.error("--leader-retry-seconds must be greater than 0")
    if args.max_heartbeat_age_seconds <= 0:
        parser.error("--max-heartbeat-age-seconds must be greater than 0")
    if args.max_readiness_age_seconds <= 0:
        parser.error("--max-readiness-age-seconds must be greater than 0")
    if args.max_pending_age_seconds <= 0:
        parser.error("--max-pending-age-seconds must be greater than 0")
    if args.healthcheck:
        if not _worker_health_is_fresh(
            args.heartbeat_path,
            args.readiness_path,
            max_heartbeat_age_seconds=args.max_heartbeat_age_seconds,
            max_readiness_age_seconds=args.max_readiness_age_seconds,
        ):
            return 1
        try:
            health_issue = _source_outbox_database_health_issue(
                max_pending_age_seconds=args.max_pending_age_seconds,
            )
        except (RuntimeError, ValueError, SQLAlchemyError):
            print(
                "SOURCE_WIDE_OUTBOX_HEALTHCHECK_FAILED",
                file=sys.stderr,
            )
            return 1
        if health_issue is not None:
            print(health_issue, file=sys.stderr)
            return 1
        return 0

    source_worker_url = _validated_source_worker_database_url()
    os.environ["DATABASE_URL"] = source_worker_url
    from app.database import build_engine
    from app.source_wide_worker import process_source_wide_events_once

    leadership_engine = build_engine(poolclass=NullPool)
    expected_database = (
        os.environ.get("TIT_SOURCE_WORKER_EXPECTED_DATABASE", "").strip()
        or None
    )
    enforce_dedicated_identity = _is_production() or bool(expected_database)
    identity_validator = (
        lambda connection: _validate_source_worker_identity(
            connection,
            expected_database=expected_database,
        )
        if enforce_dedicated_identity
        else None
    )
    leadership = SourceWideLeadership(
        leadership_engine,
        identity_validator=identity_validator,
    )
    stop_state = {"requested": False}

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_state["requested"] = True

    previous_handlers = {
        signal_number: signal.signal(signal_number, request_stop)
        for signal_number in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return _run_worker(
            leadership,
            max_events=args.max_events,
            watch=args.watch,
            interval_seconds=args.interval_seconds,
            leader_retry_seconds=_stable_leader_retry_seconds(
                args.leader_retry_seconds
            ),
            heartbeat_path=args.heartbeat_path,
            readiness_path=args.readiness_path,
            process_once=process_source_wide_events_once,
            stop_requested=lambda: stop_state["requested"],
        )
    finally:
        for signal_number, previous in previous_handlers.items():
            signal.signal(signal_number, previous)
        leadership.close()
        leadership_engine.dispose()
        for marker in (args.heartbeat_path, args.readiness_path):
            try:
                marker.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
