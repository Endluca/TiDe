from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest
from kafka.errors import (
    GroupAuthorizationFailedError,
    KafkaConnectionError,
    KafkaTimeoutError,
    NoBrokersAvailable,
    SaslAuthenticationFailedError,
    TopicAuthorizationFailedError,
    UnknownTopicOrPartitionError,
    UnsupportedVersionError,
)
from sqlalchemy.exc import OperationalError

from app.dts_source_consumer import DtsConfigurationError
from app.dts_wide_projector import DtsWideProjectionError
from scripts import run_dts_ingest
from scripts import run_dts_source_consumer
from scripts.run_dts_ingest import (
    _diagnostic_sslmode,
    _env_flag,
    _projection_activation_settings,
    _safe_operational_error_payload,
    _should_wait_before_next_batch,
)


@pytest.fixture(autouse=True)
def _single_pipeline_capture_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep legacy transport tests focused on their original boundary."""

    monkeypatch.setenv(
        "TIT_DTS_SOURCE_PARTITION_EPOCH_ID",
        "test-single-pipeline-epoch",
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "_v2_source_profile_manifest_evidence",
        lambda _source_region: "a" * 64,
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


def test_projection_batch_defaults_favor_backlog_drain_with_fresh_heartbeat() -> None:
    args = run_dts_ingest.build_parser().parse_args([])

    assert args.max_messages == 2000
    assert args.max_projection_keys == 1000
    assert args.projection_time_budget_seconds == 20.0


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (["--max-projection-keys", "0"], "DTS_MAX_PROJECTION_KEYS_INVALID"),
        (
            ["--projection-time-budget-seconds", "0"],
            "DTS_PROJECTION_TIME_BUDGET_SECONDS_INVALID",
        ),
    ],
)
def test_projection_batch_limits_fail_before_runtime_initialization(
    arguments: list[str],
    error: str,
) -> None:
    args = run_dts_ingest.build_parser().parse_args(arguments)

    with pytest.raises(DtsConfigurationError, match=f"^{error}$"):
        run_dts_ingest._load_runtime_contract(args)


def test_transport_defaults_to_python_and_accepts_official_java() -> None:
    assert run_dts_ingest._dts_transport_mode({}) == "kafka_python"
    assert (
        run_dts_ingest._dts_transport_mode(
            {"TIT_DTS_TRANSPORT": "official_java"}
        )
        == "official_java"
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_TRANSPORT_INVALID$",
    ):
        run_dts_ingest._dts_transport_mode(
            {"TIT_DTS_TRANSPORT": "unknown"}
        )


def test_healthcheck_does_not_initialize_database_or_broker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    readiness = tmp_path / "readiness.json"
    now = run_dts_ingest.datetime.now(run_dts_ingest.timezone.utc).isoformat()
    heartbeat.write_text(
        json.dumps({"status": "ok", "checked_at": now}),
        encoding="utf-8",
    )
    readiness.write_text('{"status":"ready"}', encoding="utf-8")
    monkeypatch.setattr(
        run_dts_ingest.DtsConsumerSettings,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("healthcheck must not parse DTS runtime settings")
        ),
    )
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--healthcheck",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0


def test_domestic_projection_is_forbidden_before_sink_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_settings = SimpleNamespace(
        source_region="dom",
        topic="dom-topic",
        require_target_transport=lambda **_kwargs: None,
        domestic_student_hmac_fingerprint=lambda: None,
    )
    database_settings = SimpleNamespace(
        sslmode="verify-full",
        host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        port=5432,
    )
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
            "PostgresDtsSourceEventSink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("domestic projection must fail before DB creation")
        ),
    )
    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "true")
    args = run_dts_ingest.build_parser().parse_args([])

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_LEGACY_PROJECTION_RETIRED$",
    ):
        run_dts_ingest._run(args)


def test_domestic_private_line_plaintext_reuses_existing_database_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "TIT_DTS_SOURCE_REGION": "dom",
        "TIT_DTS_EXECUTION_REGION": "cn",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "dom-topic",
        "TIT_DTS_GROUP_ID": "dtsdom1234567890",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD": "a" * 64,
        "TIT_DTS_INGEST_DB_HOST": (
            "tide-system.rwlb.singapore.rds.aliyuncs.com"
        ),
        "TIT_DTS_INGEST_DB_PASSWORD": "database-secret",
        "TIT_DTS_INGEST_DB_SSLMODE": "disable",
        "TIT_DTS_ALLOW_INSECURE_DB": "true",
        "TIT_DTS_PROJECTION_ENABLED": "false",
    }
    for name, value in runtime.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        run_dts_ingest,
            "PostgresDtsSourceEventSink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("plaintext passed the pre-connection gates")
        ),
    )
    args = run_dts_ingest.build_parser().parse_args([])

    with pytest.raises(
        AssertionError,
        match="^plaintext passed the pre-connection gates$",
    ):
        run_dts_ingest._run(args)


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


def test_invalid_runtime_arguments_clear_previous_health_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    readiness.write_text('{"status":"ready"}', encoding="utf-8")
    heartbeat.write_text('{"status":"ok"}', encoding="utf-8")
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--interval-seconds",
            "-1",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_INTERVAL_SECONDS_INVALID$",
    ):
        run_dts_ingest._run(args)

    assert not readiness.exists()
    assert not heartbeat.exists()


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

    assert _safe_operational_error_payload(TimeoutError("not kafka")) == {
        "error_code": "DTS_INGEST_UNEXPECTED_ERROR",
        "error_type": "TimeoutError",
    }


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_retriable"),
    [
        (
            NoBrokersAvailable("broker.internal account=consumer password=secret"),
            "DTS_BROKER_UNAVAILABLE",
            True,
        ),
        (
            socket.gaierror("broker.internal password=secret"),
            "DTS_INGEST_UNEXPECTED_ERROR",
            None,
        ),
        (
            KafkaConnectionError("DNS failure broker.internal password=secret"),
            "DTS_BROKER_CONNECTION_FAILED",
            True,
        ),
        (
            SaslAuthenticationFailedError(
                "account=consumer password=secret"
            ),
            "DTS_BROKER_SASL_AUTHENTICATION_FAILED",
            False,
        ),
        (
            KafkaTimeoutError("broker.internal password=secret"),
            "DTS_BROKER_KAFKA_REQUEST_TIMEOUT",
            True,
        ),
        (
            KafkaConnectionError("timeout broker.internal password=secret"),
            "DTS_BROKER_CONNECTION_FAILED",
            True,
        ),
        (
            TopicAuthorizationFailedError("topic-v2 password=secret"),
            "DTS_BROKER_TOPIC_AUTHORIZATION_FAILED",
            False,
        ),
        (
            GroupAuthorizationFailedError("dtsdom1234567890 password=secret"),
            "DTS_BROKER_GROUP_AUTHORIZATION_FAILED",
            False,
        ),
        (
            UnknownTopicOrPartitionError("topic-v2 password=secret"),
            "DTS_BROKER_TOPIC_PARTITION_UNAVAILABLE",
            True,
        ),
        (
            UnsupportedVersionError("broker.internal password=secret"),
            "DTS_BROKER_PROTOCOL_UNSUPPORTED",
            False,
        ),
    ],
)
def test_kafka_error_payload_is_stable_and_never_echoes_connection_details(
    error: Exception,
    expected_code: str,
    expected_retriable: bool | None,
) -> None:
    payload = _safe_operational_error_payload(error)

    expected_payload: dict[str, bool | str] = {
        "error_code": expected_code,
        "error_type": type(error).__name__,
    }
    if expected_retriable is not None:
        expected_payload["retriable"] = expected_retriable
    assert payload == expected_payload
    rendered = json.dumps(payload)
    assert "broker.internal" not in rendered
    assert "consumer" not in rendered
    assert "secret" not in rendered


def test_nested_kafka_authentication_failure_keeps_outer_error_text_private(
) -> None:
    try:
        try:
            raise SaslAuthenticationFailedError(
                "account=consumer password=secret"
            )
        except SaslAuthenticationFailedError as cause:
            raise RuntimeError(
                "broker.internal account=consumer password=secret"
            ) from cause
    except RuntimeError as error:
        payload = _safe_operational_error_payload(error)

    assert payload == {
        "error_code": "DTS_BROKER_SASL_AUTHENTICATION_FAILED",
        "error_type": "SaslAuthenticationFailedError",
        "retriable": False,
    }
    rendered = json.dumps(payload)
    assert "broker.internal" not in rendered
    assert "consumer" not in rendered
    assert "secret" not in rendered


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


def test_main_emits_safe_broker_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_fragments = (
        "broker.internal",
        "account=consumer",
        "password=runtime-secret",
    )
    error = NoBrokersAvailable(" ".join(secret_fragments))
    monkeypatch.setattr(
        run_dts_ingest,
        "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: SimpleNamespace()),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "_run",
        lambda _args: (_ for _ in ()).throw(error),
    )

    assert run_dts_ingest.main() == 1

    stderr = capsys.readouterr().err
    assert json.loads(stderr) == {
        "error_code": "DTS_BROKER_UNAVAILABLE",
        "error_type": "NoBrokersAvailable",
        "retriable": True,
        "status": "error",
    }
    assert all(fragment not in stderr for fragment in secret_fragments)


def test_shadow_main_emits_safe_kafka_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = SaslAuthenticationFailedError(
        "account=consumer password=runtime-secret"
    )
    monkeypatch.setattr(
        run_dts_source_consumer,
        "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: SimpleNamespace()),
    )
    monkeypatch.setattr(
        run_dts_source_consumer,
        "_run",
        lambda _args: (_ for _ in ()).throw(error),
    )

    assert run_dts_source_consumer.main() == 1

    stderr = capsys.readouterr().err
    assert json.loads(stderr) == {
        "error_code": "DTS_BROKER_SASL_AUTHENTICATION_FAILED",
        "error_type": "SaslAuthenticationFailedError",
        "retriable": False,
        "status": "error",
    }
    assert "consumer" not in stderr
    assert "runtime-secret" not in stderr


@pytest.mark.skip(reason="legacy projection worker was removed from DTS ingest")
def test_unexpected_projection_failure_prevents_a_success_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    stream_settings = SimpleNamespace(
        source_region="ovs",
        topic="ovs-topic",
        group_id="ovs-group",
        partition=0,
        start_timestamp_seconds=1786550400,
        require_target_transport=lambda **_kwargs: None,
        domestic_student_hmac_fingerprint=lambda: None,
        safe_summary=lambda: {"source_region": "ovs", "topic": "ovs-topic"},
    )
    database_settings = SimpleNamespace(
        sslmode="disable",
        host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        port=5432,
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

        def validate_startup(self, **_kwargs: object) -> object:
            return SimpleNamespace(next_offset=42, source_timestamp=1786550300)

        def validate_domestic_student_privacy_state(self) -> None:
            pass

        def validate_domestic_student_privacy_contract(
            self, **_kwargs: object
        ) -> None:
            pass

        def acquire_projection_activation(self, _settings: object) -> None:
            self.activation_acquired = True

        def assert_projection_lock_held(self) -> None:
            self.lock_checked = True

        def close(self) -> None:
            self.closed = True

        def run_batch(self, **_kwargs: object) -> object:
            raise DtsWideProjectionError("DTS_WIDE_PROJECTION_FAILED")

    class Consumer:
        startup_probed = False

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def startup_probe(
            self,
            *,
            phase_callback: object,
        ) -> dict[str, object]:
            assert not readiness.exists()
            self.startup_probed = True
            assert callable(phase_callback)
            phase_callback(
                {
                    "probe": "broker_tcp",
                    "status": "ok",
                    "broker_count": 1,
                }
            )
            return {
                "status": "ok",
                "tcp": "ok",
                "partition": 0,
                "initial_offset": 42,
            }

        def run(self, **_kwargs: object) -> dict[str, int]:
            assert self.startup_probed is True
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
        "PostgresDtsSourceEventSink",
        lambda *_args, **_kwargs: sink,
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
        match="^DTS_WIDE_PROJECTION_FAILED$",
    ):
        run_dts_ingest._run(args)

    assert readiness.exists()
    assert json.loads(readiness.read_text(encoding="utf-8"))["target"] == {
        "database": "tide_system_test",
        "sslmode": "disable",
        "insecure_transport_authorized": True,
    }
    assert json.loads(readiness.read_text(encoding="utf-8"))["broker_probe"] == {
        "initial_offset": 42,
        "partition": 0,
        "status": "ok",
        "tcp": "ok",
    }
    assert not heartbeat.exists()
    assert sink.activation_acquired is True
    assert sink.lock_checked is True
    assert sink.closed is True


def test_startup_probe_failure_prevents_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    stream_settings = SimpleNamespace(
        source_region="dom",
        topic="dom-topic",
        group_id="dom-group",
        partition=0,
        start_timestamp_seconds=1786550400,
        require_target_transport=lambda **_kwargs: None,
        domestic_student_hmac_fingerprint=lambda: "a" * 64,
        safe_summary=lambda: {"source_region": "dom", "topic": "dom-topic"},
    )
    database_settings = SimpleNamespace(
        sslmode="verify-full",
        host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        port=5432,
        safe_summary=lambda: {},
    )

    class Sink:
        engine = object()
        closed = False
        privacy_contract_registered = False

        def validate_startup(self, **_kwargs: object) -> object:
            return SimpleNamespace(next_offset=42, source_timestamp=1786550300)

        def validate_domestic_student_privacy_state(self) -> None:
            pass

        def validate_domestic_student_privacy_contract(
            self, **_kwargs: object
        ) -> None:
            self.privacy_contract_registered = True

        def close(self) -> None:
            self.closed = True

    class Consumer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def startup_probe(self, *, phase_callback: object) -> object:
            assert callable(phase_callback)
            raise NoBrokersAvailable("broker.internal password=secret")

        def run(self, **_kwargs: object) -> object:
            raise AssertionError("ingest must not run after a failed probe")

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
        "PostgresDtsSourceEventSink",
        lambda *_args, **_kwargs: sink,
    )
    monkeypatch.setattr(run_dts_ingest, "DtsKafkaConsumer", Consumer)
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "false")
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    readiness.write_text('{"status":"ready"}', encoding="utf-8")
    heartbeat.write_text('{"status":"ok"}', encoding="utf-8")
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    with pytest.raises(NoBrokersAvailable):
        run_dts_ingest._run(args)

    assert not readiness.exists()
    assert not heartbeat.exists()
    assert sink.privacy_contract_registered is False
    assert sink.closed is True


@pytest.mark.parametrize(
    "raw_value",
    ["0", "-1", "nan", "inf", "61", "not-a-number"],
)
def test_startup_retry_interval_rejects_non_positive_or_unbounded_values(
    raw_value: str,
) -> None:
    args = run_dts_ingest.build_parser().parse_args(
        ["--startup-retry-seconds", raw_value]
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_STARTUP_RETRY_SECONDS_INVALID$",
    ):
        run_dts_ingest._startup_retry_seconds(args)


def test_startup_retry_interval_uses_cli_then_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIT_DTS_STARTUP_RETRY_SECONDS", "20")
    assert run_dts_ingest._startup_retry_seconds(
        run_dts_ingest.build_parser().parse_args([])
    ) == 20.0
    assert run_dts_ingest._startup_retry_seconds(
        run_dts_ingest.build_parser().parse_args(
            ["--startup-retry-seconds", "2.5"]
        )
    ) == 2.5


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            KafkaConnectionError("broker.internal password=secret"),
            "DTS_BROKER_CONNECTION_FAILED",
        ),
        (
            KafkaTimeoutError("broker.internal password=secret"),
            "DTS_BROKER_KAFKA_REQUEST_TIMEOUT",
        ),
        (
            DtsConfigurationError("DTS_BROKER_TCP_CONNECTION_TIMEOUT"),
            "DTS_BROKER_TCP_CONNECTION_TIMEOUT",
        ),
        (
            run_dts_ingest.DtsIngestStoreError(
                "DTS_PROJECTION_CHECKPOINT_NOT_READY"
            ),
            "DTS_PROJECTION_CHECKPOINT_NOT_READY",
        ),
        (
            run_dts_ingest.DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_FAILED",
                retriable=True,
            ),
            "DTS_OFFICIAL_JAVA_TRANSPORT_FAILED",
        ),
        (
            OperationalError(
                "CONNECT",
                {},
                RuntimeError("db.internal password=secret connection refused"),
            ),
            "DTS_TARGET_CONNECTION_REFUSED",
        ),
    ],
)
def test_only_explicit_transient_startup_errors_are_retryable(
    error: Exception,
    expected_code: str,
) -> None:
    diagnostic = run_dts_ingest._retryable_startup_diagnostic(
        error,
        sslmode="disable",
    )

    assert diagnostic == {
        "error_code": expected_code,
        "error_type": (
            "OperationalError"
            if isinstance(error, OperationalError)
            else type(error).__name__
        ),
        "retriable": True,
    }
    rendered = json.dumps(diagnostic)
    assert "internal" not in rendered
    assert "secret" not in rendered


@pytest.mark.parametrize(
    "error",
    [
        SaslAuthenticationFailedError("account=consumer password=secret"),
        TopicAuthorizationFailedError("topic-v2 password=secret"),
        GroupAuthorizationFailedError("group-id password=secret"),
        UnsupportedVersionError("broker.internal password=secret"),
        DtsConfigurationError("DTS_TARGET_STATE_SCHEMA_MISMATCH"),
        run_dts_ingest.DtsIngestStoreError(
            "DTS_TARGET_STATE_SCHEMA_MISMATCH"
        ),
        run_dts_ingest.DtsJavaTransportError(
            "DTS_OFFICIAL_JAVA_AUTHENTICATION_FAILED",
            retriable=False,
        ),
        RuntimeError("broker.internal password=secret"),
    ],
)
def test_permanent_or_unknown_startup_errors_are_not_retried(
    error: Exception,
) -> None:
    assert (
        run_dts_ingest._retryable_startup_diagnostic(
            error,
            sslmode="disable",
        )
        is None
    )


def test_official_java_startup_receives_database_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    stream_settings = SimpleNamespace(
        source_region="dom",
        topic="dom-topic",
        group_id="dom-group",
        partition=0,
        start_timestamp_seconds=1786523400,
        domestic_student_hmac_fingerprint=lambda: "fingerprint",
    )
    database_settings = SimpleNamespace()

    class Sink:
        engine = object()

        def validate_startup(self, **kwargs: object) -> object:
            events.append(("resume", kwargs))
            return SimpleNamespace(
                next_offset=42,
                source_timestamp=1786523300,
            )

        def validate_domestic_student_privacy_state(self) -> None:
            events.append("privacy_state")

        def validate_domestic_student_privacy_contract(
            self, **kwargs: object
        ) -> None:
            events.append(("privacy_contract", kwargs))

        def close(self) -> None:
            events.append("sink_closed")

    class JavaTransport:
        def __init__(
            self,
            settings: object,
            processor: object,
            **kwargs: object,
        ) -> None:
            events.append(("java_init", settings, processor, kwargs))

        def startup_probe(self, *, phase_callback: object) -> dict[str, object]:
            assert callable(phase_callback)
            events.append("java_probe")
            return {
                "status": "ok",
                "transport": "official_dts_sdk",
                "first_record_offset": 41,
                "first_record_source_timestamp": 1786523300,
            }

        def close(self) -> None:
            events.append("java_closed")

    sink = Sink()
    monkeypatch.setattr(
        run_dts_ingest,
        "PostgresDtsSourceEventSink",
        lambda settings, **kwargs: (
            sink
            if settings is database_settings
            and kwargs
            == {
                "pipeline_mode": "V2_PRIMARY",
                "source_region": "dom",
                "source_partition_epoch_id": "test-single-pipeline-epoch",
                "consumer_group": "dom-group",
                "start_timestamp_seconds": 1786523400,
                "source_profile_manifest_sha256": "a" * 64,
            }
            else (_ for _ in ()).throw(AssertionError("sink contract"))
        ),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "OfficialJavaDtsTransport",
        JavaTransport,
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "DtsKafkaConsumer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("python Kafka transport must not be constructed")
        ),
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    contract = SimpleNamespace(
        stream_settings=stream_settings,
        database_settings=database_settings,
        transport_mode="official_java",
        pipeline_mode="SINGLE_PIPELINE",
        capture=SimpleNamespace(
            source_partition_epoch_id="test-single-pipeline-epoch",
            source_profile_manifest_sha256="a" * 64,
        ),
    )
    args = run_dts_ingest.build_parser().parse_args(
        ["--idle-timeout-ms", "4321"]
    )

    started = run_dts_ingest._start_ingest_once(args, contract)

    assert started is not None
    assert started.checkpoint == 42
    init = next(
        item
        for item in events
        if isinstance(item, tuple) and item[0] == "java_init"
    )
    assert init[1] is stream_settings
    assert init[3]["resume_offset"] == 42
    assert init[3]["resume_source_timestamp"] == 1786523300
    assert init[3]["lightweight_prefilter_enabled"] is False
    assert init[3]["idle_timeout_ms"] == 4321
    assert callable(init[3]["stop_requested"])
    assert events.index("privacy_state") < events.index("java_probe")
    assert events.index("java_probe") < next(
        index
        for index, item in enumerate(events)
        if isinstance(item, tuple) and item[0] == "privacy_contract"
    )
    started.consumer.close()
    started.sink.close()


def test_watch_retries_transient_startup_with_fresh_resources_before_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    stream_settings = SimpleNamespace(
        source_region="ovs",
        topic="ovs-topic",
        group_id="ovs-group",
        partition=0,
        start_timestamp_seconds=1786550400,
        require_target_transport=lambda **_kwargs: None,
        domestic_student_hmac_fingerprint=lambda: None,
        safe_summary=lambda: {"source_region": "ovs"},
    )
    database_settings = SimpleNamespace(
        sslmode="disable",
        host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        port=5432,
        safe_summary=lambda: {"database": "tide_system_test"},
    )

    class Sink:
        engine = object()

        def __init__(self) -> None:
            self.closed = False
            self.privacy_contract_registered = False

        def validate_startup(self, **_kwargs: object) -> object:
            return SimpleNamespace(next_offset=42, source_timestamp=1786550300)

        def validate_domestic_student_privacy_state(self) -> None:
            pass

        def validate_domestic_student_privacy_contract(
            self, **_kwargs: object
        ) -> None:
            self.privacy_contract_registered = True

        def close(self) -> None:
            self.closed = True

    class Consumer:
        instances = 0

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            type(self).instances += 1
            self.instance = type(self).instances

        def startup_probe(self, *, phase_callback: object) -> dict[str, object]:
            assert callable(phase_callback)
            assert not readiness.exists()
            assert not heartbeat.exists()
            if self.instance == 1:
                raise KafkaConnectionError(
                    "broker.internal account=consumer password=secret"
                )
            return {"status": "ok", "partition": 0, "initial_offset": 42}

        def run(self, **_kwargs: object) -> dict[str, int]:
            args.watch = False
            return {
                "seen": 0,
                "processed": 0,
                "ignored": 0,
                "duplicates": 0,
                "committed": 0,
            }

    sinks: list[Sink] = []

    def build_sink(*_args: object, **_kwargs: object) -> Sink:
        sink = Sink()
        sinks.append(sink)
        return sink

    def skip_wait(_seconds: float) -> bool:
        assert not readiness.exists()
        assert not heartbeat.exists()
        return True

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
        run_dts_ingest, "PostgresDtsSourceEventSink", build_sink
    )
    monkeypatch.setattr(run_dts_ingest, "DtsKafkaConsumer", Consumer)
    monkeypatch.setattr(run_dts_ingest, "_wait_for_startup_retry", skip_wait)
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    monkeypatch.setenv("TIT_DTS_PROJECTION_ENABLED", "false")
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--watch",
            "--startup-retry-seconds",
            "1",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0

    assert len(sinks) == 2
    assert Consumer.instances == 2
    assert all(sink.closed for sink in sinks)
    assert sinks[0].privacy_contract_registered is False
    assert sinks[1].privacy_contract_registered is True
    assert json.loads(readiness.read_text(encoding="utf-8"))["status"] == (
        "ready"
    )
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "ok"
    output_lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    retry = next(
        item for item in output_lines if item.get("mode") == "DTS_STARTUP_RETRY"
    )
    assert retry == {
        "attempt": 1,
        "error_code": "DTS_BROKER_CONNECTION_FAILED",
        "error_type": "KafkaConnectionError",
        "mode": "DTS_STARTUP_RETRY",
        "retriable": True,
        "retry_in_seconds": 1.0,
        "status": "waiting",
    }
    rendered = json.dumps(output_lines)
    assert "broker.internal" not in rendered
    assert "consumer" not in rendered
    assert "secret" not in rendered


def test_watch_exits_on_permanent_startup_error_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    contract = SimpleNamespace(
        database_settings=SimpleNamespace(sslmode="disable"),
        startup_retry_seconds=15.0,
    )
    attempts = 0

    def fail_startup(*_args: object, **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise SaslAuthenticationFailedError(
            "account=consumer password=secret"
        )

    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(run_dts_ingest, "_start_ingest_once", fail_startup)
    monkeypatch.setattr(
        run_dts_ingest,
        "_wait_for_startup_retry",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("permanent errors must not wait")
        ),
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--watch",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    with pytest.raises(SaslAuthenticationFailedError):
        run_dts_ingest._run(args)

    assert attempts == 1
    assert not readiness.exists()
    assert not heartbeat.exists()


def test_sigterm_during_startup_backoff_stops_without_another_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    contract = SimpleNamespace(
        database_settings=SimpleNamespace(sslmode="disable"),
        startup_retry_seconds=15.0,
    )
    attempts = 0

    def fail_startup(*_args: object, **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise KafkaTimeoutError("broker.internal password=secret")

    def stop_during_wait(_seconds: float) -> bool:
        run_dts_ingest._stop_requested = True
        return False

    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(run_dts_ingest, "_start_ingest_once", fail_startup)
    monkeypatch.setattr(
        run_dts_ingest, "_wait_for_startup_retry", stop_during_wait
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--watch",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0
    assert attempts == 1
    assert not readiness.exists()
    assert not heartbeat.exists()


def test_startup_retry_wait_is_interruptible_in_short_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    monkeypatch.setattr(run_dts_ingest.time, "monotonic", lambda: 0.0)

    def request_stop(seconds: float) -> None:
        sleeps.append(seconds)
        run_dts_ingest._stop_requested = True

    monkeypatch.setattr(run_dts_ingest.time, "sleep", request_stop)

    assert run_dts_ingest._wait_for_startup_retry(15.0) is False
    assert sleeps == [0.2]


def test_database_auth_classification_wins_over_connection_sqlstate() -> None:
    class AuthenticationError(RuntimeError):
        sqlstate = "08006"

    error = OperationalError(
        "CONNECT",
        {},
        AuthenticationError("password authentication failed for user secret"),
    )

    assert (
        run_dts_ingest._retryable_startup_diagnostic(
            error,
            sslmode="disable",
        )
        is None
    )


def test_startup_retry_backoff_is_exponential_and_bounded() -> None:
    assert [
        run_dts_ingest._startup_retry_delay(15.0, attempt)
        for attempt in range(1, 6)
    ] == [15.0, 30.0, 60.0, 60.0, 60.0]


@pytest.mark.parametrize("stop_stage", ["consumer"])
def test_sigterm_during_steady_work_removes_health_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    stop_stage: str,
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    stream_settings = SimpleNamespace(
        safe_summary=lambda: {"source_region": "ovs"}
    )
    database_settings = SimpleNamespace(
        sslmode="disable",
        safe_summary=lambda: {"database": "tide_system_test"},
    )
    contract = SimpleNamespace(
        stream_settings=stream_settings,
        database_settings=database_settings,
        pipeline_mode="SINGLE_PIPELINE",
        capture=SimpleNamespace(
            safe_summary=lambda: {
                "source_partition_epoch_id_sha256": "a" * 64,
                "source_profile_manifest_sha256": "b" * 64,
            }
        ),
        startup_retry_seconds=15.0,
    )

    class Sink:
        closed = False
        lock_checked = False

        def assert_projection_lock_held(self) -> None:
            self.lock_checked = True

        def close(self) -> None:
            self.closed = True

    class Consumer:
        def run(self, **_kwargs: object) -> dict[str, int]:
            if stop_stage == "consumer":
                run_dts_ingest._stop_requested = True
            return {
                "seen": 0,
                "processed": 0,
                "ignored": 0,
                "duplicates": 0,
                "committed": 0,
            }

    class Projector:
        def run_batch(self, **_kwargs: object) -> dict[str, int]:
            run_dts_ingest._stop_requested = True
            return {
                "dirty_keys": 0,
                "lesson_upserts": 0,
                "lesson_deletes": 0,
                "teacher_upserts": 0,
                "teacher_deletes": 0,
                "unchanged": 0,
                "retries": 0,
                "quarantined": 0,
            }

    sink = Sink()
    started = SimpleNamespace(
        sink=sink,
        consumer=Consumer(),
        projector=Projector(),
        checkpoint=42,
        broker_probe={"status": "ok"},
    )
    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(
        run_dts_ingest, "_start_ingest_once", lambda *_args: started
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0
    assert sink.closed is True
    assert sink.lock_checked is (stop_stage == "projector")
    assert not readiness.exists()
    assert not heartbeat.exists()


def test_sigterm_interrupting_java_transport_is_a_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    stream_settings = SimpleNamespace(
        safe_summary=lambda: {"source_region": "dom"}
    )
    database_settings = SimpleNamespace(
        sslmode="disable",
        safe_summary=lambda: {"database": "tide_system_test"},
    )
    contract = SimpleNamespace(
        stream_settings=stream_settings,
        database_settings=database_settings,
        pipeline_mode="SINGLE_PIPELINE",
        capture=SimpleNamespace(
            safe_summary=lambda: {
                "source_partition_epoch_id_sha256": "a" * 64,
                "source_profile_manifest_sha256": "b" * 64,
            }
        ),
        startup_retry_seconds=15.0,
    )

    class Sink:
        closed = False

        def close(self) -> None:
            self.closed = True

    class Consumer:
        closed = False

        def run(self, **_kwargs: object) -> dict[str, int]:
            run_dts_ingest._stop_requested = True
            raise run_dts_ingest.DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_STOP_REQUESTED"
            )

        def close(self) -> None:
            self.closed = True

    sink = Sink()
    consumer = Consumer()
    started = SimpleNamespace(
        sink=sink,
        consumer=consumer,
        projector=SimpleNamespace(),
        checkpoint=42,
        broker_probe={"status": "ok"},
    )
    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(
        run_dts_ingest, "_start_ingest_once", lambda *_args: started
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0
    assert consumer.closed is True
    assert sink.closed is True
    assert not readiness.exists()
    assert not heartbeat.exists()


def test_watch_rebuilds_after_transient_java_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"
    stream_settings = SimpleNamespace(
        safe_summary=lambda: {"source_region": "dom"}
    )
    database_settings = SimpleNamespace(
        sslmode="disable",
        safe_summary=lambda: {"database": "tide_system_test"},
    )
    contract = SimpleNamespace(
        stream_settings=stream_settings,
        database_settings=database_settings,
        pipeline_mode="SINGLE_PIPELINE",
        capture=SimpleNamespace(
            safe_summary=lambda: {
                "source_partition_epoch_id_sha256": "a" * 64,
                "source_profile_manifest_sha256": "b" * 64,
            }
        ),
        startup_retry_seconds=1.0,
    )

    class Sink:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Consumer:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt
            self.closed = False

        def run(self, **_kwargs: object) -> dict[str, int]:
            if self.attempt == 1:
                raise run_dts_ingest.DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_FAILED",
                    retriable=True,
                )
            args.watch = False
            return {
                "seen": 0,
                "processed": 0,
                "ignored": 0,
                "duplicates": 0,
                "committed": 0,
            }

        def close(self) -> None:
            self.closed = True

    sinks: list[Sink] = []
    consumers: list[Consumer] = []

    def start_once(*_args: object) -> SimpleNamespace:
        sink = Sink()
        consumer = Consumer(len(consumers) + 1)
        sinks.append(sink)
        consumers.append(consumer)
        return SimpleNamespace(
            sink=sink,
            consumer=consumer,
            projector=SimpleNamespace(),
            checkpoint=42,
            broker_probe={"status": "ok", "transport": "official_java"},
        )

    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(run_dts_ingest, "_start_ingest_once", start_once)
    monkeypatch.setattr(
        run_dts_ingest, "_wait_for_startup_retry", lambda _seconds: True
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    args = run_dts_ingest.build_parser().parse_args(
        [
            "--watch",
            "--heartbeat-path",
            str(heartbeat),
            "--readiness-path",
            str(readiness),
        ]
    )

    assert run_dts_ingest._run(args) == 0
    assert len(consumers) == 2
    assert all(consumer.closed for consumer in consumers)
    assert all(sink.closed for sink in sinks)
    assert json.loads(readiness.read_text(encoding="utf-8"))["status"] == (
        "ready"
    )
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "ok"
    retry = next(
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"mode": "DTS_STARTUP_RETRY"' in line
    )
    assert retry["error_code"] == "DTS_OFFICIAL_JAVA_TRANSPORT_FAILED"
    assert retry["attempt"] == 1
