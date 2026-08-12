from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime_settings import validate_production_runtime
from app.qualification_award_gate import (
    QualificationAwardGateConfigurationError,
    irreversible_qualification_grants_enabled,
)


validate_production_runtime()

from app.database import build_engine
from app.shared_task_score_settlement import settle_shared_task_scores_once
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool


DEFAULT_HEARTBEAT_PATH = Path("/tmp/tit-score-worker-heartbeat")
_LEADER_LOCK_NAMESPACE = 544954
_LEADER_LOCK_KEY = 2
_TRY_LEADER_LOCK = text(
    "SELECT pg_try_advisory_lock(:namespace, :key)"
)
_LEADER_CONNECTION_PING = text("SELECT 1")
_DEFAULT_LEADER_RETRY_SECONDS = 10.0


class ScoreSettlementLeadership:
    """Hold one PostgreSQL session lock for the lifetime of the active worker."""

    def __init__(self, bind: Engine) -> None:
        self._bind = bind
        self._connection: Connection | None = None

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
            raise RuntimeError(
                "score settlement leader election requires PostgreSQL"
            )

        connection = self._bind.connect()
        try:
            acquired = bool(
                connection.scalar(
                    _TRY_LEADER_LOCK,
                    {
                        "namespace": _LEADER_LOCK_NAMESPACE,
                        "key": _LEADER_LOCK_KEY,
                    },
                )
            )
            # A session advisory lock survives COMMIT.  Ending this implicit
            # transaction prevents idle_in_transaction_session_timeout from
            # killing an otherwise healthy leader connection.
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
        # Physical disconnect is the release protocol.  PostgreSQL releases
        # session advisory locks automatically, including on network loss.
        try:
            connection.invalidate()
        finally:
            connection.close()


def _write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{time.time():.6f}\n", encoding="ascii")
    temporary.replace(path)


def _heartbeat_is_fresh(path: Path, *, max_age_seconds: float) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age_seconds <= max_age_seconds


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


def _poll_settlement_once(
    leadership: ScoreSettlementLeadership,
    *,
    max_events: int,
    settle_once: Callable[..., dict[str, Any]],
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
    # Settlement runs on the same PostgreSQL session that owns the election
    # lock.  This removes the verify-then-open-a-second-connection gap.  The
    # settlement transaction still takes the existing projection advisory
    # lock as an independent second guard.
    return settle_once(bind=connection, max_events=max_events)


def _log_role_transition(
    current_role: str | None,
    *,
    event: str,
    role: str,
) -> str:
    if current_role != role:
        print(
            json.dumps(
                {"event": event, "role": role},
                ensure_ascii=True,
                sort_keys=True,
            ),
            flush=True,
        )
    return role


def _run_worker(
    leadership: ScoreSettlementLeadership,
    *,
    max_events: int,
    watch: bool,
    interval_seconds: float,
    leader_retry_seconds: float,
    heartbeat_path: Path,
    settle_once: Callable[..., dict[str, Any]] = settle_shared_task_scores_once,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    _write_heartbeat(heartbeat_path)
    next_leadership_attempt_at = 0.0
    reported_role: str | None = None
    while True:
        # This heartbeat represents the local candidate process, not only the
        # elected leader.  Standby Pods therefore remain healthy and ready to
        # take over.
        _write_heartbeat(heartbeat_path)
        now = monotonic()
        attempt_leadership = (
            leadership.is_leader or now >= next_leadership_attempt_at
        )
        was_leader = leadership.is_leader
        try:
            result = _poll_settlement_once(
                leadership,
                max_events=max_events,
                settle_once=settle_once,
                attempt_leadership=attempt_leadership,
            )
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

        try:
            sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0


def main() -> int:
    try:
        irreversible_qualification_grants_enabled()
    except QualificationAwardGateConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        description="Consume shared-task events and settle current mandatory scores."
    )
    parser.add_argument("--max-events", type=int, default=100)
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
                "TIT_SCORE_WORKER_HEARTBEAT",
                str(DEFAULT_HEARTBEAT_PATH),
            )
        ),
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Exit 0 only when the worker heartbeat is fresh.",
    )
    parser.add_argument(
        "--max-heartbeat-age-seconds",
        type=float,
        default=15.0,
        help="Maximum heartbeat age accepted by --healthcheck.",
    )
    args = parser.parse_args()
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be greater than 0")
    if args.leader_retry_seconds <= 0:
        parser.error("--leader-retry-seconds must be greater than 0")
    if args.max_heartbeat_age_seconds <= 0:
        parser.error("--max-heartbeat-age-seconds must be greater than 0")
    if args.healthcheck:
        return (
            0
            if _heartbeat_is_fresh(
                args.heartbeat_path,
                max_age_seconds=args.max_heartbeat_age_seconds,
            )
            else 1
        )

    leadership_engine = build_engine(poolclass=NullPool)
    leadership = ScoreSettlementLeadership(leadership_engine)
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
        )
    finally:
        leadership.close()
        leadership_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
