from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.dts_source_consumer import DtsConfigurationError
from scripts import run_dts_ingest


def _capture(*, epoch_id: str = "epoch-dom-reset-001") -> object:
    return run_dts_ingest._SourceCaptureRuntimeContract(
        source_partition_epoch_id=epoch_id,
        source_profile_manifest_sha256="a" * 64,
    )


def test_only_single_pipeline_mode_is_accepted() -> None:
    assert run_dts_ingest._pipeline_mode({}) == "SINGLE_PIPELINE"
    assert run_dts_ingest._pipeline_mode(
        {"TIT_DTS_PIPELINE_MODE": "SINGLE_PIPELINE"}
    ) == "SINGLE_PIPELINE"
    for retired in (
        "V1",
        "V1_COMPAT_DUAL_CAPTURE",
        "V2_PRIMARY",
        "ROLLED_BACK",
    ):
        with pytest.raises(
            DtsConfigurationError,
            match="^TIT_DTS_PIPELINE_MODE_RETIRED$",
        ):
            run_dts_ingest._pipeline_mode(
                {"TIT_DTS_PIPELINE_MODE": retired}
            )
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_LEGACY_PROJECTION_RETIRED$",
    ):
        run_dts_ingest._pipeline_mode(
            {"TIT_DTS_PROJECTION_ENABLED": "true"}
        )


def test_source_capture_requires_new_explicit_epoch() -> None:
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_SOURCE_PARTITION_EPOCH_ID_REQUIRED$",
    ):
        run_dts_ingest._source_capture_runtime_contract(
            source_region="ovs",
            environ={},
        )


def test_source_capture_uses_code_owned_event_contract() -> None:
    contract = run_dts_ingest._source_capture_runtime_contract(
        source_region="dom",
        environ={
            "TIT_DTS_SOURCE_PARTITION_EPOCH_ID": "epoch-dom-reset-001"
        },
    )
    assert contract.source_partition_epoch_id == "epoch-dom-reset-001"
    assert len(contract.source_profile_manifest_sha256) == 64


def test_single_sink_receives_start_policy_and_no_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = object()
    stream = SimpleNamespace(
        source_region="ovs",
        group_id="real-ovs-sid",
        start_timestamp_seconds=1786523400,
    )
    contract = SimpleNamespace(
        stream_settings=stream,
        database_settings=settings,
        pipeline_mode="SINGLE_PIPELINE",
        capture=_capture(epoch_id="epoch-ovs-reset-001"),
    )
    captured: dict[str, object] = {}
    sentinel = object()

    def create(actual_settings: object, **kwargs: object) -> object:
        captured["settings"] = actual_settings
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        run_dts_ingest, "_create_source_capture_sink", create
    )
    assert run_dts_ingest._new_ingest_sink(contract) is sentinel
    assert captured == {
        "settings": settings,
        "pipeline_mode": "V2_PRIMARY",
        "source_region": "ovs",
        "source_partition_epoch_id": "epoch-ovs-reset-001",
        "consumer_group": "real-ovs-sid",
        "start_timestamp_seconds": 1786523400,
        "source_profile_manifest_sha256": "a" * 64,
    }


def test_empty_reset_starts_transport_from_configured_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    stream = SimpleNamespace(
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        group_id="real-ovs-sid",
        start_timestamp_seconds=1786523400,
        domestic_student_hmac_fingerprint=lambda: None,
    )

    class Sink:
        def validate_startup(self, **kwargs: object) -> None:
            calls.append(("validate_startup", kwargs))
            return None

        def validate_domestic_student_privacy_state(self) -> None:
            calls.append("privacy_state")

        def validate_domestic_student_privacy_contract(
            self, **_kwargs: object
        ) -> None:
            calls.append("privacy_contract")

        def close(self) -> None:
            calls.append("sink_closed")

    class JavaTransport:
        def __init__(
            self,
            _settings: object,
            processor: object,
            **kwargs: object,
        ) -> None:
            assert processor._sink is sink
            assert kwargs["resume_offset"] is None
            assert kwargs["resume_source_timestamp"] is None
            calls.append("transport_init")

        def startup_probe(
            self, *, phase_callback: object
        ) -> dict[str, object]:
            assert callable(phase_callback)
            return {"status": "ok", "transport": "official_dts_sdk"}

        def close(self) -> None:
            calls.append("transport_closed")

    sink = Sink()
    monkeypatch.setattr(run_dts_ingest, "_new_ingest_sink", lambda _c: sink)
    monkeypatch.setattr(
        run_dts_ingest, "OfficialJavaDtsTransport", JavaTransport
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    contract = SimpleNamespace(
        stream_settings=stream,
        database_settings=object(),
        pipeline_mode="SINGLE_PIPELINE",
        transport_mode="official_java",
        capture=_capture(epoch_id="epoch-ovs-reset-001"),
    )
    started = run_dts_ingest._start_ingest_once(
        run_dts_ingest.build_parser().parse_args([]), contract
    )
    assert started is not None
    assert started.checkpoint is None
    assert calls.index("transport_init") > 0
    started.consumer.close()
    started.sink.close()


def test_health_evidence_hashes_epoch_and_never_emits_raw_identity() -> None:
    epoch_id = "epoch-secret-looking-but-not-emitted"
    contract = SimpleNamespace(
        pipeline_mode="SINGLE_PIPELINE",
        capture=_capture(epoch_id=epoch_id),
    )
    summary = run_dts_ingest._pipeline_health_summary(contract)
    assert summary == {
        "mode": "SINGLE_PIPELINE",
        "source_partition_epoch_id_sha256": hashlib.sha256(
            epoch_id.encode("utf-8")
        ).hexdigest(),
        "source_profile_manifest_sha256": "a" * 64,
    }
    assert epoch_id not in str(summary)
