from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dts_ingest_store import (  # noqa: E402
    DtsIngestDatabaseSettings,
    DtsIngestStoreError,
    DtsProjectionActivationSettings,
    PostgresDtsEventSink,
)
from app.dts_source_consumer import (  # noqa: E402
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaConsumer,
    DtsRecordError,
)
from app.dts_wide_projector import (  # noqa: E402
    DtsWideProjectionError,
    DtsWideProjector,
)


_stop_requested = False


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def _write_health(path: str | None, payload: dict[str, object]) -> None:
    if not path:
        return
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DtsConfigurationError(f"{name}_INVALID")


def _should_wait_before_next_batch(*, seen: int, max_messages: int) -> bool:
    """Throttle only after an under-filled or idle consumer batch."""

    return seen < max_messages


def _safe_operational_error_payload(
    exc: Exception,
    *,
    sslmode: str | None = None,
) -> dict[str, str]:
    """Classify connection failures without emitting driver error text."""

    payload = {
        "error_code": "DTS_INGEST_UNEXPECTED_ERROR",
        "error_type": type(exc).__name__,
    }
    if not isinstance(exc, OperationalError):
        return payload
    if sslmode in {"verify-full", "disable"}:
        payload["sslmode"] = sslmode

    try:
        original: Any = getattr(exc, "orig", exc)
    except Exception:
        original = None
    try:
        message = str(original).lower() if original is not None else ""
    except Exception:
        message = ""
    try:
        sqlstate = getattr(original, "sqlstate", None)
        if sqlstate is None:
            sqlstate = getattr(getattr(original, "diag", None), "sqlstate", None)
    except Exception:
        sqlstate = None
    normalized_sqlstate = None
    if isinstance(sqlstate, str):
        candidate_sqlstate = sqlstate.upper()
        if len(candidate_sqlstate) == 5 and candidate_sqlstate.isalnum():
            normalized_sqlstate = candidate_sqlstate

    if "pg_hba" in message or "no pg_hba.conf entry" in message:
        error_code = "DTS_TARGET_PG_HBA_REJECTED"
    elif normalized_sqlstate in {"28P01", "28000"} or (
        "password authentication failed" in message
    ):
        error_code = "DTS_TARGET_AUTHENTICATION_FAILED"
    elif any(
        marker in message
        for marker in (
            "could not translate host name",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
        )
    ):
        error_code = "DTS_TARGET_DNS_FAILED"
    elif "connection refused" in message:
        error_code = "DTS_TARGET_CONNECTION_REFUSED"
    elif any(
        marker in message
        for marker in ("timeout expired", "timed out", "connection timeout")
    ):
        error_code = "DTS_TARGET_CONNECTION_TIMEOUT"
    elif any(
        marker in message
        for marker in (
            "server closed the connection unexpectedly",
            "connection reset",
            "terminating connection",
            "connection is closed",
        )
    ):
        error_code = "DTS_TARGET_CONNECTION_CLOSED"
    elif any(
        marker in message
        for marker in (
            "weak sslmode",
            "sslrootcert",
            "sslnegotiation",
            "channel binding",
        )
    ):
        error_code = "DTS_TARGET_LIBPQ_TRANSPORT_CONFIG_CONFLICT"
    elif any(
        marker in message
        for marker in (
            "ssl is required",
            "ssl required",
            "requires ssl",
            "must use ssl",
            "tls is required",
            "tls required",
            "requires tls",
            "must use tls",
        )
    ):
        error_code = "DTS_TARGET_SERVER_REQUIRES_TLS"
    elif sslmode == "disable" and ("ssl" in message or "tls" in message):
        # A plaintext connection performs no TLS handshake. Preserve the
        # policy signal without claiming a handshake occurred.
        error_code = "DTS_TARGET_SSL_POLICY_CONFLICT"
    elif any(
        marker in message
        for marker in (
            "ssl handshake",
            "tls handshake",
            "certificate verify failed",
            "certificate verification failed",
        )
    ):
        error_code = "DTS_TARGET_SSL_HANDSHAKE_FAILED"
    elif "ssl" in message or "tls" in message:
        error_code = "DTS_TARGET_SSL_POLICY_CONFLICT"
    elif normalized_sqlstate == "57P03":
        error_code = "DTS_TARGET_CONNECTION_UNAVAILABLE"
    else:
        error_code = "DTS_TARGET_CONNECTION_FAILED"

    payload["error_code"] = error_code
    if normalized_sqlstate is not None:
        payload["sqlstate"] = normalized_sqlstate
    return payload


def _diagnostic_sslmode(
    environ: dict[str, str] | None = None,
) -> str | None:
    values = os.environ if environ is None else environ
    value = values.get("TIT_DTS_INGEST_DB_SSLMODE", "verify-full").strip()
    return value if value in {"verify-full", "disable"} else None


def _projection_activation_settings(
    *,
    enabled: bool,
    environ: dict[str, str] | None = None,
) -> DtsProjectionActivationSettings | None:
    if not enabled:
        return None
    return DtsProjectionActivationSettings.from_env(environ)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persist one Aliyun DTS subscription into the restricted target "
            "ledger and source mirror, then project dirty keys into both "
            "source-wide tables."
        )
    )
    parser.add_argument("--max-messages", type=int, default=100)
    parser.add_argument("--max-projection-keys", type=int, default=200)
    parser.add_argument("--idle-timeout-ms", type=int, default=10_000)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    parser.add_argument("--heartbeat-path")
    parser.add_argument("--readiness-path")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--max-heartbeat-age-seconds", type=float, default=90.0)
    return parser


def _healthcheck(args: argparse.Namespace) -> int:
    if not args.heartbeat_path or not args.readiness_path:
        raise DtsConfigurationError("DTS_HEALTH_PATHS_REQUIRED")
    heartbeat = json.loads(Path(args.heartbeat_path).read_text(encoding="utf-8"))
    readiness = json.loads(Path(args.readiness_path).read_text(encoding="utf-8"))
    if readiness.get("status") != "ready" or heartbeat.get("status") != "ok":
        raise DtsIngestStoreError("DTS_INGEST_NOT_READY")
    checked_at = datetime.fromisoformat(str(heartbeat["checked_at"]))
    age = (datetime.now(timezone.utc) - checked_at).total_seconds()
    if age < 0 or age > args.max_heartbeat_age_seconds:
        raise DtsIngestStoreError("DTS_INGEST_HEARTBEAT_STALE")
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.healthcheck:
        return _healthcheck(args)
    if args.interval_seconds < 0:
        raise DtsConfigurationError("DTS_INTERVAL_SECONDS_INVALID")
    stream_settings = DtsConsumerSettings.from_env()
    database_settings = DtsIngestDatabaseSettings.from_env()
    projection_enabled = _env_flag("TIT_DTS_PROJECTION_ENABLED", False)
    activation_settings = _projection_activation_settings(
        enabled=projection_enabled,
    )
    if activation_settings is not None:
        activation_settings.require_current_stream(
            source_region=stream_settings.source_region,
            topic=stream_settings.topic,
        )
    sink = PostgresDtsEventSink(
        database_settings,
        source_region=stream_settings.source_region,
    )
    try:
        processor = DtsEventProcessor(sink)
        projector = DtsWideProjector(
            sink.engine,
            worker_id=(
                f"{stream_settings.source_region}:"
                f"{socket.gethostname()}:{os.getpid()}"
            ),
        )
        projector.settings.require_subscription_boundary(
            stream_settings.start_timestamp_seconds
        )
        # Validate database identity and exact ACL before the first broker
        # connection.  This creates no rows and does not advance either offset.
        checkpoint = sink.resume_offset(
            source_region=stream_settings.source_region,
            topic=stream_settings.topic,
            partition=stream_settings.partition,
        )
        if activation_settings is not None:
            sink.acquire_projection_activation(activation_settings)
        started_at = datetime.now(timezone.utc).isoformat()
        _write_health(
            args.readiness_path,
            {
                "status": "ready",
                "started_at": started_at,
                "checkpoint": checkpoint,
                "connection": stream_settings.safe_summary(),
                "target": database_settings.safe_summary(),
            },
        )
        while not _stop_requested:
            consumer = DtsKafkaConsumer(
                stream_settings,
                processor,
                idle_timeout_ms=args.idle_timeout_ms,
            )
            result = consumer.run(
                max_messages=args.max_messages,
                commit_offsets=True,
            )
            if projection_enabled:
                # Prove the checked-out session is still the one that acquired
                # the global projector lock before every projection batch.
                sink.assert_projection_lock_held()
                projection = projector.run_batch(
                    max_keys=args.max_projection_keys
                )
            else:
                projection = {
                    "dirty_keys": 0,
                    "lesson_upserts": 0,
                    "lesson_deletes": 0,
                    "teacher_upserts": 0,
                    "teacher_deletes": 0,
                    "unchanged": 0,
                    "retries": 0,
                }
            heartbeat = {
                "status": "ok",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "connection": stream_settings.safe_summary(),
                "target": database_settings.safe_summary(),
                "ingest": result,
                "projection": projection,
                "projection_enabled": projection_enabled,
            }
            _write_health(args.heartbeat_path, heartbeat)
            print(
                json.dumps(
                    {"mode": "DTS_WIDE_PROJECTION", **heartbeat},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            if not args.watch:
                break
            if not _should_wait_before_next_batch(
                seen=result["seen"],
                max_messages=args.max_messages,
            ):
                continue
            deadline = time.monotonic() + args.interval_seconds
            while not _stop_requested and time.monotonic() < deadline:
                time.sleep(min(0.2, deadline - time.monotonic()))
    finally:
        sink.close()
    return 0


def main() -> int:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        return _run(build_parser().parse_args())
    except (
        DtsConfigurationError,
        DtsRecordError,
        DtsIngestStoreError,
        DtsWideProjectionError,
    ) as exc:
        print(
            json.dumps(
                {"status": "error", "error_code": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    except Exception as exc:  # Never echo a credential-bearing exception.
        print(
            json.dumps(
                {
                    "status": "error",
                    **_safe_operational_error_payload(
                        exc,
                        sslmode=_diagnostic_sslmode(),
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
