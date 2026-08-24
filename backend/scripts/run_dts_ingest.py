from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dts_ingest_store import (  # noqa: E402
    APPROVED_INSECURE_PRE_HOST,
    APPROVED_INSECURE_PRE_PORT,
    DtsIngestDatabaseSettings,
    DtsIngestStoreError,
    DtsProjectionActivationSettings,
    PostgresDtsEventSink,
)
from app.dts_direct_projector import DtsDirectWideProjector  # noqa: E402
from app.dts_java_transport import (  # noqa: E402
    DtsJavaTransportError,
    OfficialJavaDtsTransport,
)
from app.dts_source_consumer import (  # noqa: E402
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaConsumer,
    DtsRecordError,
    safe_kafka_error_diagnostic,
)
from app.dts_wide_projector import (  # noqa: E402
    DtsWideProjectionError,
    DtsWideProjector,
)


_stop_requested = False
_LEGACY_V1_PIPELINE_MODE = "V1"
_DUAL_CAPTURE_PIPELINE_MODE = "V1_COMPAT_DUAL_CAPTURE"
_V2_PRIMARY_PIPELINE_MODE = "V2_PRIMARY"
_ROLLED_BACK_PIPELINE_MODE = "ROLLED_BACK"
_V2_CAPTURE_PIPELINE_MODES = frozenset(
    {
        _DUAL_CAPTURE_PIPELINE_MODE,
        _V2_PRIMARY_PIPELINE_MODE,
        _ROLLED_BACK_PIPELINE_MODE,
    }
)
_DEFAULT_STARTUP_RETRY_SECONDS = 15.0
_MAX_STARTUP_RETRY_SECONDS = 60.0
_RETRYABLE_DTS_STARTUP_ERROR_CODES = frozenset(
    {
        "DTS_BROKER_TCP_DNS_FAILED",
        "DTS_BROKER_TCP_CONNECTION_TIMEOUT",
        "DTS_BROKER_TCP_CONNECTION_REFUSED",
        "DTS_BROKER_TCP_UNREACHABLE",
        "DTS_BROKER_TCP_CONNECTION_FAILED",
        "DTS_BROKER_TCP_ALL_ENDPOINTS_FAILED",
        "DTS_BROKER_KAFKA_REQUEST_TIMEOUT",
        "DTS_KAFKA_STARTUP_PROBE_TIMEOUT",
        "DTS_KAFKA_GROUP_COORDINATOR_UNAVAILABLE",
    }
)
_RETRYABLE_DATABASE_STARTUP_ERROR_CODES = frozenset(
    {
        "DTS_TARGET_DNS_FAILED",
        "DTS_TARGET_CONNECTION_REFUSED",
        "DTS_TARGET_CONNECTION_TIMEOUT",
        "DTS_TARGET_CONNECTION_CLOSED",
        "DTS_TARGET_CONNECTION_UNAVAILABLE",
    }
)
_RETRYABLE_STORE_STARTUP_ERROR_CODES = frozenset(
    {
        "DTS_PROJECTION_LOCK_NOT_ACQUIRED",
        "DTS_PROJECTION_CHECKPOINT_NOT_READY",
        "DTS_PROJECTION_COMPLAINT_DICTIONARY_EMPTY",
        "DTS_PROJECTION_COMPLAINT_CATEGORY_DEPENDENCY_PENDING",
    }
)


@dataclass(frozen=True)
class _RuntimeContract:
    stream_settings: DtsConsumerSettings
    database_settings: DtsIngestDatabaseSettings
    projection_enabled: bool
    projection_mode: str
    activation_settings: DtsProjectionActivationSettings | None
    startup_retry_seconds: float
    transport_mode: str
    pipeline_mode: str
    dual_capture: _DualCaptureRuntimeContract | None


@dataclass(frozen=True)
class _DualCaptureRuntimeContract:
    source_partition_epoch_id: str
    control_group: str
    source_profile_manifest_sha256: str

    def safe_summary(self) -> dict[str, str]:
        return {
            "source_partition_epoch_id_sha256": hashlib.sha256(
                self.source_partition_epoch_id.encode("utf-8")
            ).hexdigest(),
            "source_profile_manifest_sha256": (
                self.source_profile_manifest_sha256
            ),
        }


@dataclass(frozen=True)
class _StartedIngest:
    sink: PostgresDtsEventSink
    consumer: Any
    projector: DtsWideProjector | DtsDirectWideProjector
    checkpoint: int | None
    broker_probe: dict[str, object]


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


def _clear_health_files(*paths: str | None) -> None:
    """Remove prior-process health evidence before any startup validation."""

    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)


def _emit_startup_probe(payload: dict[str, bool | int | str]) -> None:
    """Emit only fixed phase evidence, never endpoints or credentials."""

    print(
        json.dumps(
            {"mode": "DTS_STARTUP_PROBE", **payload},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


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


def _dts_transport_mode(
    environ: dict[str, str] | None = None,
) -> str:
    """Select the Kafka transport; Gaea explicitly defaults this to Java."""

    values = os.environ if environ is None else environ
    value = values.get("TIT_DTS_TRANSPORT", "kafka_python").strip().lower()
    if value not in {"kafka_python", "official_java"}:
        raise DtsConfigurationError("TIT_DTS_TRANSPORT_INVALID")
    return value


def _pipeline_mode(
    environ: dict[str, str] | None = None,
) -> str:
    """Select the database-controlled ingest contract for this process."""

    values = os.environ if environ is None else environ
    value = values.get(
        "TIT_DTS_PIPELINE_MODE",
        _LEGACY_V1_PIPELINE_MODE,
    ).strip()
    if value not in {_LEGACY_V1_PIPELINE_MODE, *_V2_CAPTURE_PIPELINE_MODES}:
        raise DtsConfigurationError("TIT_DTS_PIPELINE_MODE_INVALID")
    return value


def _required_dual_capture_text(
    values: Mapping[str, str],
    name: str,
    *,
    max_length: int,
) -> str:
    value = values.get(name, "")
    if (
        not value
        or value.strip() != value
        or len(value) > max_length
        or any(ord(character) < 32 for character in value)
    ):
        raise DtsConfigurationError(f"{name}_REQUIRED")
    return value


def _v2_source_profile_manifest_evidence(source_region: str) -> str:
    """Require an attested profile for every v2 business table in-region."""

    try:
        from app.dts_source_contract_v2 import (
            V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION,
            V2_SOURCE_PROFILE_REGISTRY,
        )
    except ValueError as exc:
        raise DtsConfigurationError(
            "DTS_V2_SOURCE_PROFILE_MANIFEST_INVALID"
        ) from exc

    expected_tables = {
        f"{source_region}_{suffix}"
        for suffix in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION[source_region]
    }
    configured_tables = {
        table
        for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
        if profile.region == source_region
    }
    if configured_tables != expected_tables:
        raise DtsConfigurationError(
            "DTS_V2_SOURCE_PROFILE_REGION_INCOMPLETE"
        )
    return V2_SOURCE_PROFILE_REGISTRY.manifest_sha256


def _dual_capture_runtime_contract(
    *,
    pipeline_mode: str,
    projection_mode: str,
    source_region: str,
    environ: dict[str, str] | None = None,
) -> _DualCaptureRuntimeContract | None:
    if pipeline_mode == _LEGACY_V1_PIPELINE_MODE:
        return None
    if pipeline_mode not in _V2_CAPTURE_PIPELINE_MODES:
        raise DtsConfigurationError("TIT_DTS_PIPELINE_MODE_INVALID")
    if projection_mode != "queued":
        raise DtsConfigurationError(
            "DTS_V2_DUAL_CAPTURE_DIRECT_FORBIDDEN"
        )

    values = os.environ if environ is None else environ
    source_partition_epoch_id = _required_dual_capture_text(
        values,
        "TIT_DTS_V2_SOURCE_PARTITION_EPOCH_ID",
        max_length=160,
    )
    control_group = _required_dual_capture_text(
        values,
        "TIT_DTS_V2_CONTROL_GROUP",
        max_length=256,
    )
    manifest_sha256 = _v2_source_profile_manifest_evidence(source_region)
    return _DualCaptureRuntimeContract(
        source_partition_epoch_id=source_partition_epoch_id,
        control_group=control_group,
        source_profile_manifest_sha256=manifest_sha256,
    )


def _startup_retry_seconds(args: argparse.Namespace) -> float:
    raw = getattr(args, "startup_retry_seconds", None)
    if raw is None:
        raw = os.environ.get(
            "TIT_DTS_STARTUP_RETRY_SECONDS",
            str(_DEFAULT_STARTUP_RETRY_SECONDS),
        )
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise DtsConfigurationError(
            "TIT_DTS_STARTUP_RETRY_SECONDS_INVALID"
        ) from exc
    if (
        not isfinite(seconds)
        or seconds <= 0
        or seconds > _MAX_STARTUP_RETRY_SECONDS
    ):
        raise DtsConfigurationError(
            "TIT_DTS_STARTUP_RETRY_SECONDS_INVALID"
        )
    return seconds


def _should_wait_before_next_batch(*, seen: int, max_messages: int) -> bool:
    """Throttle only after an under-filled or idle consumer batch."""

    return seen < max_messages


def _safe_operational_error_payload(
    exc: Exception,
    *,
    sslmode: str | None = None,
) -> dict[str, bool | str]:
    """Classify database and broker failures without emitting error text."""

    payload: dict[str, bool | str] = {
        "error_code": "DTS_INGEST_UNEXPECTED_ERROR",
        "error_type": type(exc).__name__,
    }
    kafka_diagnostic = safe_kafka_error_diagnostic(
        exc,
        fallback_error_code="DTS_BROKER_REQUEST_FAILED",
    )
    if kafka_diagnostic is not None:
        payload.update(kafka_diagnostic)
        return payload
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


def _projection_mode(
    *,
    enabled: bool,
    environ: dict[str, str] | None = None,
) -> str:
    values = os.environ if environ is None else environ
    mode = values.get("TIT_DTS_PROJECTION_MODE", "queued").strip().lower()
    if mode not in {"queued", "direct"}:
        raise DtsConfigurationError("TIT_DTS_PROJECTION_MODE_INVALID")
    if mode == "direct" and not enabled:
        raise DtsConfigurationError("DTS_DIRECT_PROJECTION_REQUIRES_ENABLED")
    return mode


def _load_runtime_contract(args: argparse.Namespace) -> _RuntimeContract:
    if not isfinite(args.interval_seconds) or args.interval_seconds < 0:
        raise DtsConfigurationError("DTS_INTERVAL_SECONDS_INVALID")
    if args.max_projection_keys < 1:
        raise DtsConfigurationError("DTS_MAX_PROJECTION_KEYS_INVALID")
    if (
        not isfinite(args.projection_time_budget_seconds)
        or args.projection_time_budget_seconds <= 0
    ):
        raise DtsConfigurationError(
            "DTS_PROJECTION_TIME_BUDGET_SECONDS_INVALID"
        )
    stream_settings = DtsConsumerSettings.from_env()
    database_settings = DtsIngestDatabaseSettings.from_env()
    stream_settings.require_target_transport(
        host=database_settings.host,
        port=database_settings.port,
        expected_host=APPROVED_INSECURE_PRE_HOST,
        expected_port=APPROVED_INSECURE_PRE_PORT,
    )
    projection_enabled = _env_flag("TIT_DTS_PROJECTION_ENABLED", False)
    projection_mode = _projection_mode(enabled=projection_enabled)
    pipeline_mode = _pipeline_mode()
    dual_capture = _dual_capture_runtime_contract(
        pipeline_mode=pipeline_mode,
        projection_mode=projection_mode,
        source_region=stream_settings.source_region,
    )
    if pipeline_mode in {
        _DUAL_CAPTURE_PIPELINE_MODE,
        _ROLLED_BACK_PIPELINE_MODE,
    } and not projection_enabled:
        raise DtsConfigurationError(
            "DTS_V1_COMPAT_PROJECTION_MUST_BE_ENABLED"
        )
    if (
        pipeline_mode == _V2_PRIMARY_PIPELINE_MODE
        and projection_enabled
    ):
        raise DtsConfigurationError(
            "DTS_V2_PRIMARY_LEGACY_PROJECTION_MUST_BE_DISABLED"
        )
    if (
        pipeline_mode == _LEGACY_V1_PIPELINE_MODE
        and stream_settings.source_region == "dom"
        and projection_enabled
        and projection_mode != "direct"
    ):
        raise DtsConfigurationError("DTS_DOM_PROJECTION_FORBIDDEN")
    activation_settings = _projection_activation_settings(
        enabled=projection_enabled and projection_mode == "queued",
    )
    if activation_settings is not None:
        activation_settings.require_current_stream(
            source_region=stream_settings.source_region,
            topic=stream_settings.topic,
        )
    return _RuntimeContract(
        stream_settings=stream_settings,
        database_settings=database_settings,
        projection_enabled=projection_enabled,
        projection_mode=projection_mode,
        activation_settings=activation_settings,
        startup_retry_seconds=_startup_retry_seconds(args),
        transport_mode=_dts_transport_mode(),
        pipeline_mode=pipeline_mode,
        dual_capture=dual_capture,
    )


def _new_ingest_sink(
    contract: _RuntimeContract,
) -> PostgresDtsEventSink:
    stream_settings = contract.stream_settings
    dual_capture = getattr(contract, "dual_capture", None)
    if dual_capture is None:
        return PostgresDtsEventSink(
            contract.database_settings,
            source_region=stream_settings.source_region,
            # Direct mode rebuilds fresh resources after a stale connection
            # and resumes from the authoritative DB checkpoint. Avoid one
            # otherwise redundant SELECT 1 before every bounded batch.
            pool_pre_ping=(
                getattr(contract, "projection_mode", "queued") != "direct"
            ),
        )

    return _create_dual_capture_sink(
        contract.database_settings,
        pipeline_mode=contract.pipeline_mode,
        source_region=stream_settings.source_region,
        source_partition_epoch_id=dual_capture.source_partition_epoch_id,
        consumer_group=stream_settings.group_id,
        control_group=dual_capture.control_group,
    )


def _create_dual_capture_sink(
    settings: DtsIngestDatabaseSettings,
    **kwargs: object,
) -> PostgresDtsEventSink:
    # Importing the v2 writer also loads the attested physical source-profile
    # registry.  Keep legacy V1 startup independent from that new contract.
    from app.dts_v2_dual_capture_store import PostgresDtsV2DualCaptureSink

    return PostgresDtsV2DualCaptureSink(settings, **kwargs)


def _start_ingest_once(
    args: argparse.Namespace,
    contract: _RuntimeContract,
) -> _StartedIngest | None:
    """Run every startup gate with fresh resources and no health writes."""

    if _stop_requested:
        return None
    stream_settings = contract.stream_settings
    sink = _new_ingest_sink(contract)
    handoff = False
    consumer: Any | None = None
    try:
        processor = DtsEventProcessor(sink)
        if getattr(contract, "projection_mode", "queued") == "direct":
            projector = DtsDirectWideProjector()
            sink.enable_direct_projection(projector)
        else:
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
        # These checks create no source rows and advance no offsets.
        resume_source_timestamp: int | None = None
        dual_resume_checkpoint = None
        if getattr(contract, "dual_capture", None) is not None:
            dual_resume_checkpoint = sink.validate_startup(
                source_region=stream_settings.source_region,
                topic=stream_settings.topic,
                partition=stream_settings.partition,
            )
        if dual_resume_checkpoint is not None:
            checkpoint = dual_resume_checkpoint.next_offset
            resume_source_timestamp = (
                dual_resume_checkpoint.source_timestamp
            )
        elif contract.transport_mode == "official_java":
            resume_checkpoint = sink.resume_checkpoint(
                source_region=stream_settings.source_region,
                topic=stream_settings.topic,
                partition=stream_settings.partition,
            )
            checkpoint = (
                None
                if resume_checkpoint is None
                else resume_checkpoint.next_offset
            )
            resume_source_timestamp = (
                None
                if resume_checkpoint is None
                else resume_checkpoint.source_timestamp
            )
        else:
            checkpoint = sink.resume_offset(
                source_region=stream_settings.source_region,
                topic=stream_settings.topic,
                partition=stream_settings.partition,
            )
        sink.validate_domestic_student_privacy_state()
        if _stop_requested:
            return None
        if contract.transport_mode == "official_java":
            consumer = OfficialJavaDtsTransport(
                stream_settings,
                processor,
                resume_offset=checkpoint,
                resume_source_timestamp=resume_source_timestamp,
                lightweight_prefilter_enabled=(
                    getattr(contract, "projection_mode", "queued") == "direct"
                ),
                idle_timeout_ms=args.idle_timeout_ms,
                stop_requested=lambda: _stop_requested,
            )
        else:
            consumer = DtsKafkaConsumer(
                stream_settings,
                processor,
                idle_timeout_ms=args.idle_timeout_ms,
            )
        broker_probe = consumer.startup_probe(
            phase_callback=_emit_startup_probe,
        )
        # A signal received while a blocking broker call was in flight must
        # never be followed by a database write or a transient ready state.
        if _stop_requested:
            return None
        sink.validate_domestic_student_privacy_contract(
            key_fingerprint=(
                stream_settings.domestic_student_hmac_fingerprint()
            ),
            topic=stream_settings.topic,
            partition=stream_settings.partition,
        )
        if _stop_requested:
            return None
        if contract.activation_settings is not None:
            sink.acquire_projection_activation(contract.activation_settings)
        if _stop_requested:
            return None
        started = _StartedIngest(
            sink=sink,
            consumer=consumer,
            projector=projector,
            checkpoint=checkpoint,
            broker_probe=dict(broker_probe),
        )
        handoff = True
        return started
    finally:
        if not handoff:
            try:
                if consumer is not None:
                    close = getattr(consumer, "close", None)
                    if callable(close):
                        close()
            finally:
                sink.close()


def _retryable_startup_diagnostic(
    exc: Exception,
    *,
    sslmode: str | None,
) -> dict[str, bool | str] | None:
    """Return a safe diagnostic only for an explicit transient allowlist."""

    if isinstance(exc, DtsJavaTransportError):
        if exc.retriable:
            return {
                "error_code": exc.error_code,
                "error_type": "DtsJavaTransportError",
                "retriable": True,
            }
        return None

    kafka_diagnostic = safe_kafka_error_diagnostic(
        exc,
        fallback_error_code="DTS_BROKER_REQUEST_FAILED",
    )
    if kafka_diagnostic is not None:
        if kafka_diagnostic.get("retriable") is True:
            return kafka_diagnostic
        return None

    if isinstance(exc, OperationalError):
        payload = _safe_operational_error_payload(exc, sslmode=sslmode)
        error_code = payload.get("error_code")
        sqlstate = payload.get("sqlstate")
        if error_code in _RETRYABLE_DATABASE_STARTUP_ERROR_CODES or (
            error_code == "DTS_TARGET_CONNECTION_FAILED"
            and isinstance(sqlstate, str)
            and sqlstate.startswith("08")
        ):
            return {
                "error_code": str(error_code),
                "error_type": "OperationalError",
                "retriable": True,
            }
        return None

    if (
        isinstance(exc, DtsConfigurationError)
        and len(exc.args) == 1
        and exc.args[0] in _RETRYABLE_DTS_STARTUP_ERROR_CODES
    ):
        return {
            "error_code": str(exc.args[0]),
            "error_type": "DtsConfigurationError",
            "retriable": True,
        }
    if (
        isinstance(exc, DtsIngestStoreError)
        and len(exc.args) == 1
        and exc.args[0] in _RETRYABLE_STORE_STARTUP_ERROR_CODES
    ):
        return {
            "error_code": str(exc.args[0]),
            "error_type": "DtsIngestStoreError",
            "retriable": True,
        }
    return None


def _startup_retry_delay(base_seconds: float, attempt: int) -> float:
    exponent = min(max(attempt - 1, 0), 10)
    return min(base_seconds * (2**exponent), _MAX_STARTUP_RETRY_SECONDS)


def _emit_startup_retry(
    *,
    attempt: int,
    diagnostic: dict[str, bool | str],
    retry_in_seconds: float,
) -> None:
    print(
        json.dumps(
            {
                "mode": "DTS_STARTUP_RETRY",
                "status": "waiting",
                "attempt": attempt,
                "error_code": diagnostic["error_code"],
                "error_type": diagnostic["error_type"],
                "retriable": True,
                "retry_in_seconds": retry_in_seconds,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def _wait_for_startup_retry(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not _stop_requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.2, remaining))
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persist one Aliyun DTS subscription into the restricted target "
            "and project it into both source-wide tables using the selected "
            "queued or direct mode."
        )
    )
    parser.add_argument("--max-messages", type=int, default=2_000)
    parser.add_argument("--max-projection-keys", type=int, default=1_000)
    parser.add_argument(
        "--projection-time-budget-seconds",
        type=float,
        default=20.0,
    )
    parser.add_argument("--idle-timeout-ms", type=int, default=10_000)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    parser.add_argument("--startup-retry-seconds")
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


def _close_started_ingest(started: _StartedIngest) -> None:
    try:
        close = getattr(started.consumer, "close", None)
        if callable(close):
            close()
    finally:
        started.sink.close()


def _pipeline_health_summary(
    contract: _RuntimeContract,
) -> dict[str, str]:
    summary = {
        "mode": getattr(
            contract,
            "pipeline_mode",
            _LEGACY_V1_PIPELINE_MODE,
        )
    }
    dual_capture = getattr(contract, "dual_capture", None)
    if dual_capture is not None:
        summary.update(dual_capture.safe_summary())
    return summary


def _run(args: argparse.Namespace) -> int:
    if args.healthcheck:
        return _healthcheck(args)
    _clear_health_files(args.heartbeat_path, args.readiness_path)
    contract = _load_runtime_contract(args)
    attempt = 0
    while not _stop_requested:
        started: _StartedIngest | None = None
        while not _stop_requested:
            _clear_health_files(args.heartbeat_path, args.readiness_path)
            try:
                started = _start_ingest_once(args, contract)
            except Exception as exc:
                _clear_health_files(args.heartbeat_path, args.readiness_path)
                diagnostic = _retryable_startup_diagnostic(
                    exc,
                    sslmode=contract.database_settings.sslmode,
                )
                if not args.watch or diagnostic is None:
                    raise
                attempt += 1
                retry_in_seconds = _startup_retry_delay(
                    contract.startup_retry_seconds,
                    attempt,
                )
                _emit_startup_retry(
                    attempt=attempt,
                    diagnostic=diagnostic,
                    retry_in_seconds=retry_in_seconds,
                )
                if not _wait_for_startup_retry(retry_in_seconds):
                    return 0
                continue
            break
        if started is None or _stop_requested:
            if started is not None:
                _close_started_ingest(started)
            return 0

        stream_settings = contract.stream_settings
        database_settings = contract.database_settings
        projection_enabled = contract.projection_enabled
        projection_mode = getattr(contract, "projection_mode", "queued")
        retry_diagnostic: dict[str, bool | str] | None = None
        try:
            started_at = datetime.now(timezone.utc).isoformat()
            _write_health(
                args.readiness_path,
                {
                    "status": "ready",
                    "started_at": started_at,
                    "checkpoint": started.checkpoint,
                    "broker_probe": started.broker_probe,
                    "connection": stream_settings.safe_summary(),
                    "target": database_settings.safe_summary(),
                    "pipeline": _pipeline_health_summary(contract),
                },
            )
            if _stop_requested:
                _clear_health_files(
                    args.heartbeat_path,
                    args.readiness_path,
                )
                return 0
            while not _stop_requested:
                try:
                    result = started.consumer.run(
                        max_messages=args.max_messages,
                        commit_offsets=True,
                    )
                except DtsJavaTransportError as exc:
                    # A SIGTERM observed during the adapter's interruptible
                    # wait is a clean shutdown. Explicit transient Java
                    # failures rebuild the complete DB/Kafka startup contract
                    # from the durable database checkpoint without exiting.
                    if _stop_requested:
                        _clear_health_files(
                            args.heartbeat_path,
                            args.readiness_path,
                        )
                        return 0
                    diagnostic = _retryable_startup_diagnostic(
                        exc,
                        sslmode=database_settings.sslmode,
                    )
                    if not args.watch or diagnostic is None:
                        raise
                    retry_diagnostic = diagnostic
                    _clear_health_files(
                        args.heartbeat_path,
                        args.readiness_path,
                    )
                    break
                if _stop_requested:
                    _clear_health_files(
                        args.heartbeat_path,
                        args.readiness_path,
                    )
                    return 0
                if projection_enabled:
                    if projection_mode == "direct":
                        projection = started.projector.drain_counts()
                    else:
                        # Prove the checked-out session is still the one that
                        # acquired the global projector lock before every batch.
                        started.sink.assert_projection_lock_held()
                        projection = started.projector.run_batch(
                            max_keys=args.max_projection_keys,
                            max_seconds=args.projection_time_budget_seconds,
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
                        "quarantined": 0,
                        "source_queries": 0,
                        "cache_hits": 0,
                        "elapsed_ms": 0,
                        "budget_exhausted": 0,
                    }
                if _stop_requested:
                    _clear_health_files(
                        args.heartbeat_path,
                        args.readiness_path,
                    )
                    return 0
                heartbeat = {
                    "status": "ok",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "connection": stream_settings.safe_summary(),
                    "target": database_settings.safe_summary(),
                    "ingest": result,
                    "projection": projection,
                    "projection_enabled": projection_enabled,
                    "projection_mode": projection_mode,
                    "pipeline": _pipeline_health_summary(contract),
                }
                _write_health(args.heartbeat_path, heartbeat)
                attempt = 0
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
            if _stop_requested:
                _clear_health_files(
                    args.heartbeat_path,
                    args.readiness_path,
                )
        finally:
            _close_started_ingest(started)

        if retry_diagnostic is None:
            return 0
        attempt += 1
        retry_in_seconds = _startup_retry_delay(
            contract.startup_retry_seconds,
            attempt,
        )
        _emit_startup_retry(
            attempt=attempt,
            diagnostic=retry_diagnostic,
            retry_in_seconds=retry_in_seconds,
        )
        if not _wait_for_startup_retry(retry_in_seconds):
            return 0
    if _stop_requested:
        _clear_health_files(args.heartbeat_path, args.readiness_path)
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
