from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.dts_source_consumer import DtsConfigurationError
from app.dts_wide_projector import DtsWideProjectionError
from scripts import run_dts_ingest
from scripts.run_dts_ingest import (
    _diagnostic_sslmode,
    _env_flag,
    _projection_activation_settings,
    _safe_operational_error_payload,
    _should_wait_before_next_batch,
)


def test_projection_flag_defaults_off_and_accepts_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIT_DTS_PROJECTION_ENABLED", raising=False)
    assert _env_flag("TIT_DTS_PROJECTION_ENABLED", False) is False

    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "true")
    assert _env_flag("TIT_DTS_PROJECTION_ENABLED", False) is True

    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "off")
    assert _env_flag("TIT_DTS_PROJECTION_ENABLED", True) is False

    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "sometimes")
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_PROJECTION_ENABLED_INVALID",
    ):
        _env_flag("TIT_DTS_PROJECTION_ENABLED", False)


def test_projection_activation_variables_are_required_only_when_enabled() -> None:
    assert _projection_activation_settings(enabled=False, environ={}) is None

    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_ACTIVATION_AT_REQUIRED",
    ):
        _projection_activation_settings(enabled=True, environ={})

    settings = _projection_activation_settings(
        enabled=True,
        environ={
            "TIT_DTS_ACTIVATION_AT": "2026-08-13T00:00:00+08:00",
            "TIT_DTS_REQUIRED_OVS_TOPIC": "ovs-topic",
            "TIT_DTS_REQUIRED_DOM_TOPIC": "dom-topic",
        },
    )
    assert settings is not None
    assert settings.activation_timestamp_seconds == 1786550400


@pytest.mark.parametrize(
    ("seen", "max_messages", "expected"),
    [
        (100, 100, False),
        (99, 100, True),
        (1, 100, True),
        (0, 100, True),
    ],
)
def test_watch_waits_only_after_underfilled_or_idle_batch(
    seen: int,
    max_messages: int,
    expected: bool,
) -> None:
    assert (
        _should_wait_before_next_batch(
            seen=seen,
            max_messages=max_messages,
        )
        is expected
    )


def test_diagnostic_sslmode_is_strict_and_safe() -> None:
    assert _diagnostic_sslmode({}) == "verify-full"
    assert _diagnostic_sslmode({"TIT_DTS_INGEST_DB_SSLMODE": "disable"}) == (
        "disable"
    )
    assert _diagnostic_sslmode({"TIT_DTS_INGEST_DB_SSLMODE": "unexpected"}) is None


@pytest.mark.parametrize(
    ("driver_message", "expected_code"),
    [
        (
            "password authentication failed for user runtime-secret",
            "DTS_TARGET_AUTHENTICATION_FAILED",
        ),
        (
            "no pg_hba.conf entry for host 10.9.15.125",
            "DTS_TARGET_PG_HBA_REJECTED",
        ),
        (
            "could not translate host name db.internal",
            "DTS_TARGET_DNS_FAILED",
        ),
        ("connection refused", "DTS_TARGET_CONNECTION_REFUSED"),
        ("connection timeout expired", "DTS_TARGET_CONNECTION_TIMEOUT"),
        (
            "server closed the connection unexpectedly",
            "DTS_TARGET_CONNECTION_CLOSED",
        ),
        ("SSL handshake failed", "DTS_TARGET_SSL_HANDSHAKE_FAILED"),
        ("unknown driver connection error", "DTS_TARGET_CONNECTION_FAILED"),
    ],
)
def test_operational_error_payload_is_stable_and_never_echoes_driver_message(
    driver_message: str,
    expected_code: str,
) -> None:
    exc = OperationalError("CONNECT", {}, RuntimeError(driver_message))

    payload = _safe_operational_error_payload(exc)

    assert payload == {
        "error_code": expected_code,
        "error_type": "OperationalError",
    }
    assert driver_message not in json.dumps(payload)


@pytest.mark.parametrize(
    ("driver_message", "expected_code"),
    [
        (
            'weak sslmode "disable" may not be used with sslrootcert=system',
            "DTS_TARGET_LIBPQ_TRANSPORT_CONFIG_CONFLICT",
        ),
        (
            "server policy requires SSL",
            "DTS_TARGET_SERVER_REQUIRES_TLS",
        ),
        (
            "server policy requires TLS",
            "DTS_TARGET_SERVER_REQUIRES_TLS",
        ),
        (
            "SSL handshake failed",
            "DTS_TARGET_SSL_POLICY_CONFLICT",
        ),
        (
            "relation pg_catalog.pg_stat_ssl is unavailable",
            "DTS_TARGET_SSL_POLICY_CONFLICT",
        ),
    ],
)
def test_plaintext_mode_never_claims_a_tls_handshake(
    driver_message: str,
    expected_code: str,
) -> None:
    payload = _safe_operational_error_payload(
        OperationalError("CONNECT", {}, RuntimeError(driver_message)),
        sslmode="disable",
    )

    assert payload == {
        "error_code": expected_code,
        "error_type": "OperationalError",
        "sslmode": "disable",
    }


def test_unexpected_error_payload_does_not_inspect_or_echo_exception_text() -> None:
    payload = _safe_operational_error_payload(
        RuntimeError("password=must-never-appear")
    )

    assert payload == {
        "error_code": "DTS_INGEST_UNEXPECTED_ERROR",
        "error_type": "RuntimeError",
    }


def test_operational_error_payload_survives_broken_driver_error_object() -> None:
    class BrokenDriverError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("driver-string-must-not-escape")

        @property
        def sqlstate(self) -> str:
            raise RuntimeError("driver-sqlstate-must-not-escape")

        @property
        def diag(self) -> object:
            raise RuntimeError("driver-diag-must-not-escape")

    payload = _safe_operational_error_payload(
        OperationalError("CONNECT", {}, BrokenDriverError()),
        sslmode="disable",
    )

    assert payload == {
        "error_code": "DTS_TARGET_CONNECTION_FAILED",
        "error_type": "OperationalError",
        "sslmode": "disable",
    }
    assert "must-never-appear" not in json.dumps(payload)


def test_hba_message_takes_priority_over_generic_authorization_sqlstate() -> None:
    class AuthorizationError(RuntimeError):
        sqlstate = "28000"

    payload = _safe_operational_error_payload(
        OperationalError(
            "CONNECT",
            {},
            AuthorizationError("no pg_hba.conf entry for host 10.9.15.125"),
        )
    )

    assert payload == {
        "error_code": "DTS_TARGET_PG_HBA_REJECTED",
        "error_type": "OperationalError",
        "sqlstate": "28000",
    }


def test_operational_error_payload_uses_diag_sqlstate_only_when_well_formed(
) -> None:
    malformed = RuntimeError("connection failed")
    malformed.diag = SimpleNamespace(sqlstate="bad!?")  # type: ignore[attr-defined]
    assert "sqlstate" not in _safe_operational_error_payload(
        OperationalError("CONNECT", {}, malformed)
    )

    unavailable = RuntimeError("database is starting")
    unavailable.diag = SimpleNamespace(sqlstate="57P03")  # type: ignore[attr-defined]
    assert _safe_operational_error_payload(
        OperationalError("CONNECT", {}, unavailable)
    ) == {
        "error_code": "DTS_TARGET_CONNECTION_UNAVAILABLE",
        "error_type": "OperationalError",
        "sqlstate": "57P03",
    }


def test_main_never_echoes_connection_error_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_fragments = (
        "password=runtime-secret",
        "user=tit_dts_ingest_runtime",
        "host=db.internal",
    )
    error = OperationalError(
        "CONNECT",
        {},
        RuntimeError(" ".join(secret_fragments) + " connection refused"),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: SimpleNamespace()),
    )
    monkeypatch.setenv("TIT_DTS_INGEST_DB_SSLMODE", "disable")
    monkeypatch.setattr(
        run_dts_ingest,
        "_run",
        lambda _args: (_ for _ in ()).throw(error),
    )

    assert run_dts_ingest.main() == 1

    stderr = capsys.readouterr().err
    assert json.loads(stderr) == {
        "error_code": "DTS_TARGET_CONNECTION_REFUSED",
        "error_type": "OperationalError",
        "sslmode": "disable",
        "status": "error",
    }
    assert all(fragment not in stderr for fragment in secret_fragments)


def test_exhausted_projection_prevents_a_success_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    stream_settings = SimpleNamespace(
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        start_timestamp_seconds=1786550400,
        safe_summary=lambda: {"source_region": "ovs", "topic": "ovs-topic"},
    )
    database_settings = SimpleNamespace(
        safe_summary=lambda: {
            "database": "tide_system_test",
            "sslmode": "disable",
            "insecure_transport_authorized": True,
        }
    )

    class Sink:
        engine = object()
        closed = False
        activation_acquired = False
        lock_checked = False

        def resume_offset(self, **_kwargs: object) -> int:
            return 42

        def acquire_projection_activation(self, _settings: object) -> None:
            self.activation_acquired = True

        def assert_projection_lock_held(self) -> None:
            self.lock_checked = True

        def close(self) -> None:
            self.closed = True

    class Projector:
        settings = SimpleNamespace(
            require_subscription_boundary=lambda _value: None
        )

        def run_batch(self, **_kwargs: object) -> object:
            raise DtsWideProjectionError(
                "DTS_WIDE_PROJECTION_RETRY_EXHAUSTED"
            )

    class Consumer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self, **_kwargs: object) -> dict[str, int]:
            return {
                "seen": 1,
                "processed": 1,
                "ignored": 0,
                "duplicates": 0,
                "committed": 1,
            }

    sink = Sink()
    monkeypatch.setattr(
        run_dts_ingest,
        "DtsConsumerSettings",
        SimpleNamespace(from_env=lambda: stream_settings),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "DtsIngestDatabaseSettings",
        SimpleNamespace(from_env=lambda: database_settings),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "PostgresDtsEventSink",
        lambda *_args, **_kwargs: sink,
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "DtsWideProjector",
        lambda *_args, **_kwargs: Projector(),
    )
    monkeypatch.setattr(run_dts_ingest, "DtsKafkaConsumer", Consumer)
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "true")
    monkeypatch.setenv(
        "TIT_DTS_ACTIVATION_AT",
        "2026-08-13T00:00:00+08:00",
    )
    monkeypatch.setenv("TIT_DTS_REQUIRED_OVS_TOPIC", "ovs-topic")
    monkeypatch.setenv("TIT_DTS_REQUIRED_DOM_TOPIC", "dom-topic")
    heartbeat = tmp_path / "heartbeat.json"
    readiness = tmp_path / "readiness.json"
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--max-messages",
            "1",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    with pytest.raises(
        DtsWideProjectionError,
        match="^DTS_WIDE_PROJECTION_RETRY_EXHAUSTED$",
    ):
        run_dts_ingest._run(args)

    assert readiness.exists()
    assert json.loads(readiness.read_text(encoding="utf-8"))["target"] == {
        "database": "tide_system_test",
        "sslmode": "disable",
        "insecure_transport_authorized": True,
    }
    assert not heartbeat.exists()
    assert sink.activation_acquired is True
    assert sink.lock_checked is True
    assert sink.closed is True
