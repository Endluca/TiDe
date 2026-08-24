from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.dts_source_consumer import DtsConfigurationError
from scripts import run_dts_ingest


def _dual_contract(*, epoch_id: str = "epoch-dom-001") -> object:
    return run_dts_ingest._DualCaptureRuntimeContract(
        source_partition_epoch_id=epoch_id,
        control_group="tit-dts-v2-fleet",
        source_profile_manifest_sha256="a" * 64,
    )


def test_pipeline_mode_defaults_to_legacy_and_accepts_database_modes() -> None:
    assert run_dts_ingest._pipeline_mode({}) == "V1"
    for mode in (
        "V1_COMPAT_DUAL_CAPTURE",
        "V2_PRIMARY",
        "ROLLED_BACK",
    ):
        assert (
            run_dts_ingest._pipeline_mode(
                {"TIT_DTS_PIPELINE_MODE": mode}
            )
            == mode
        )

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_PIPELINE_MODE_INVALID$",
    ):
        run_dts_ingest._pipeline_mode({"TIT_DTS_PIPELINE_MODE": "v2"})


def test_legacy_mode_does_not_load_the_v2_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_dts_ingest,
        "_v2_source_profile_manifest_evidence",
        lambda _region: (_ for _ in ()).throw(
            AssertionError("legacy V1 must not load v2 source profiles")
        ),
    )

    assert (
        run_dts_ingest._dual_capture_runtime_contract(
            pipeline_mode="V1",
            projection_mode="direct",
            source_region="ovs",
            environ={},
        )
        is None
    )


@pytest.mark.parametrize(
    ("environ", "error"),
    [
        (
            {"TIT_DTS_V2_CONTROL_GROUP": "tit-dts-v2-fleet"},
            "TIT_DTS_V2_SOURCE_PARTITION_EPOCH_ID_REQUIRED",
        ),
        (
            {"TIT_DTS_V2_SOURCE_PARTITION_EPOCH_ID": "epoch-ovs-001"},
            "TIT_DTS_V2_CONTROL_GROUP_REQUIRED",
        ),
    ],
)
def test_dual_capture_requires_explicit_epoch_and_control_group(
    monkeypatch: pytest.MonkeyPatch,
    environ: dict[str, str],
    error: str,
) -> None:
    monkeypatch.setattr(
        run_dts_ingest,
        "_v2_source_profile_manifest_evidence",
        lambda _region: "a" * 64,
    )

    with pytest.raises(DtsConfigurationError, match=f"^{error}$"):
        run_dts_ingest._dual_capture_runtime_contract(
            pipeline_mode="V1_COMPAT_DUAL_CAPTURE",
            projection_mode="queued",
            source_region="ovs",
            environ=environ,
        )


def test_dual_capture_rejects_direct_before_profile_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_dts_ingest,
        "_v2_source_profile_manifest_evidence",
        lambda _region: (_ for _ in ()).throw(
            AssertionError("direct must fail before profile access")
        ),
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_V2_DUAL_CAPTURE_DIRECT_FORBIDDEN$",
    ):
        run_dts_ingest._dual_capture_runtime_contract(
            pipeline_mode="V1_COMPAT_DUAL_CAPTURE",
            projection_mode="direct",
            source_region="ovs",
            environ={},
        )


def test_dual_capture_fails_closed_when_region_profile_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_dts_ingest,
        "_v2_source_profile_manifest_evidence",
        lambda _region: (_ for _ in ()).throw(
            DtsConfigurationError("DTS_V2_SOURCE_PROFILE_REGION_INCOMPLETE")
        ),
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_V2_SOURCE_PROFILE_REGION_INCOMPLETE$",
    ):
        run_dts_ingest._dual_capture_runtime_contract(
            pipeline_mode="V1_COMPAT_DUAL_CAPTURE",
            projection_mode="queued",
            source_region="dom",
            environ={
                "TIT_DTS_V2_SOURCE_PARTITION_EPOCH_ID": "epoch-dom-001",
                "TIT_DTS_V2_CONTROL_GROUP": "tit-dts-v2-fleet",
            },
        )


def test_dual_sink_receives_route_group_and_separate_control_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = object()
    stream = SimpleNamespace(source_region="ovs", group_id="real-ovs-sid")
    contract = SimpleNamespace(
        stream_settings=stream,
        database_settings=settings,
        pipeline_mode="V2_PRIMARY",
        dual_capture=_dual_contract(epoch_id="epoch-ovs-001"),
    )
    captured: dict[str, object] = {}
    sentinel = object()

    def create(actual_settings: object, **kwargs: object) -> object:
        captured["settings"] = actual_settings
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(run_dts_ingest, "_create_dual_capture_sink", create)

    assert run_dts_ingest._new_ingest_sink(contract) is sentinel
    assert captured == {
        "settings": settings,
        "pipeline_mode": "V2_PRIMARY",
        "source_region": "ovs",
        "source_partition_epoch_id": "epoch-ovs-001",
        "consumer_group": "real-ovs-sid",
        "control_group": "tit-dts-v2-fleet",
    }


def test_dual_startup_validates_database_state_before_broker_transport(
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
        engine = object()

        def validate_startup(self, **kwargs: object) -> object:
            calls.append(("validate_startup", kwargs))
            return SimpleNamespace(
                next_offset=43,
                source_timestamp=1786523300,
            )

        def resume_checkpoint(self, **_kwargs: object) -> object:
            raise AssertionError("dual startup must use validate_startup")

        def resume_offset(self, **_kwargs: object) -> int:
            raise AssertionError("dual startup must use validate_startup")

        def validate_domestic_student_privacy_state(self) -> None:
            calls.append("privacy_state")

        def validate_domestic_student_privacy_contract(
            self, **_kwargs: object
        ) -> None:
            calls.append("privacy_contract")

        def close(self) -> None:
            calls.append("sink_closed")

    class Projector:
        settings = SimpleNamespace(
            require_subscription_boundary=lambda value: calls.append(
                ("boundary", value)
            )
        )

    class JavaTransport:
        def __init__(
            self,
            _settings: object,
            processor: object,
            **kwargs: object,
        ) -> None:
            assert processor._sink is sink
            calls.append(("transport_init", kwargs))

        def startup_probe(self, *, phase_callback: object) -> dict[str, object]:
            assert callable(phase_callback)
            calls.append("broker_probe")
            return {"status": "ok", "transport": "official_dts_sdk"}

        def close(self) -> None:
            calls.append("transport_closed")

    sink = Sink()
    monkeypatch.setattr(run_dts_ingest, "_new_ingest_sink", lambda _c: sink)
    monkeypatch.setattr(
        run_dts_ingest,
        "DtsWideProjector",
        lambda *_args, **_kwargs: Projector(),
    )
    monkeypatch.setattr(
        run_dts_ingest,
        "OfficialJavaDtsTransport",
        JavaTransport,
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    contract = SimpleNamespace(
        stream_settings=stream,
        database_settings=object(),
        projection_mode="queued",
        transport_mode="official_java",
        activation_settings=None,
        dual_capture=_dual_contract(epoch_id="epoch-ovs-001"),
    )

    started = run_dts_ingest._start_ingest_once(
        run_dts_ingest.build_parser().parse_args([]),
        contract,
    )

    assert started is not None
    assert started.checkpoint == 43
    transport_args = next(
        value[1]
        for value in calls
        if isinstance(value, tuple) and value[0] == "transport_init"
    )
    assert transport_args["resume_offset"] == 43
    assert transport_args["resume_source_timestamp"] == 1786523300
    assert calls.index("broker_probe") > next(
        index
        for index, value in enumerate(calls)
        if isinstance(value, tuple) and value[0] == "validate_startup"
    )
    started.consumer.close()
    started.sink.close()


def test_dual_health_evidence_hashes_epoch_and_never_emits_raw_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    epoch_id = "epoch-secret-looking-but-not-emitted"
    dual = _dual_contract(epoch_id=epoch_id)
    contract = SimpleNamespace(
        stream_settings=SimpleNamespace(
            safe_summary=lambda: {"source_region": "ovs"}
        ),
        database_settings=SimpleNamespace(
            sslmode="verify-full",
            safe_summary=lambda: {"database": "tide_system"},
        ),
        projection_enabled=False,
        projection_mode="queued",
        pipeline_mode="V1_COMPAT_DUAL_CAPTURE",
        dual_capture=dual,
        startup_retry_seconds=15.0,
    )

    class Sink:
        def close(self) -> None:
            pass

    class Consumer:
        def run(self, **kwargs: object) -> dict[str, int]:
            assert kwargs["commit_offsets"] is True
            return {
                "seen": 0,
                "processed": 0,
                "ignored": 0,
                "duplicates": 0,
                "committed": 0,
            }

        def close(self) -> None:
            pass

    started = SimpleNamespace(
        sink=Sink(),
        consumer=Consumer(),
        projector=SimpleNamespace(),
        checkpoint=43,
        broker_probe={"status": "ok", "transport": "official_dts_sdk"},
    )
    monkeypatch.setattr(
        run_dts_ingest, "_load_runtime_contract", lambda _args: contract
    )
    monkeypatch.setattr(
        run_dts_ingest, "_start_ingest_once", lambda *_args: started
    )
    monkeypatch.setattr(run_dts_ingest, "_stop_requested", False)
    readiness = tmp_path / "readiness.json"
    heartbeat = tmp_path / "heartbeat.json"

    assert (
        run_dts_ingest._run(
            run_dts_ingest.build_parser().parse_args(
                [
                    "--readiness-path",
                    str(readiness),
                    "--heartbeat-path",
                    str(heartbeat),
                ]
            )
        )
        == 0
    )

    expected_pipeline = {
        "mode": "V1_COMPAT_DUAL_CAPTURE",
        "source_partition_epoch_id_sha256": hashlib.sha256(
            epoch_id.encode("utf-8")
        ).hexdigest(),
        "source_profile_manifest_sha256": "a" * 64,
    }
    readiness_payload = json.loads(readiness.read_text(encoding="utf-8"))
    heartbeat_payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert readiness_payload["pipeline"] == expected_pipeline
    assert heartbeat_payload["pipeline"] == expected_pipeline
    assert epoch_id not in readiness.read_text(encoding="utf-8")
    assert epoch_id not in heartbeat.read_text(encoding="utf-8")
