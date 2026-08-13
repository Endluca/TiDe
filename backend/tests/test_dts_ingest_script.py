from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.dts_source_consumer import DtsConfigurationError
from app.dts_wide_projector import DtsWideProjectionError
from scripts import run_dts_ingest
from scripts.run_dts_ingest import (
    _env_flag,
    _projection_activation_settings,
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
