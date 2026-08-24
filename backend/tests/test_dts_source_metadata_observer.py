from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.dts_source_metadata_observer import (
    DtsSourceMetadataObserverError,
    DtsSourceMetadataObserverSettings,
    OfficialJavaEventJsonlSource,
    metadata_event_from_official_java_envelope,
    observe_official_java_metadata,
)
from scripts import observe_dts_source_metadata as observer_cli


def _official_java_event(
    *,
    operation: str = "UPDATE",
    table: str = "dom_appoint",
    sample_value: str = "fixture-person-alpha",
) -> dict[str, Any]:
    return {
        "type": "EVENT",
        "topic": "fixture-production-topic",
        "partition": 7,
        "offset": 9001,
        "source_timestamp": 1_786_000_000,
        "account": "fixture-source-account",
        "record": {
            "id": 81234,
            "sourceTimestamp": 1_786_000_000,
            "sourcePosition": "fixture-source-position",
            "safeSourcePosition": "fixture-safe-position",
            "sourceTxid": "fixture-transaction",
            "operation": operation,
            "objectName": f"business.public.{table}",
            "tags": {"tableName": table},
            "fields": [
                "id",
                "student_id",
                "active",
                "happened_at",
                "memo",
            ],
            "fieldTypeNumbers": {
                "id": 20,
                "student_id": 20,
                "active": 1,
                "happened_at": 93,
                "memo": 12,
            },
            "beforeImages": [
                {"precision": 20, "value": "1"},
                {"precision": 20, "value": sample_value},
                {"value": False},
                {"timestamp": 1_786_000_000},
                {"charset": "utf8", "value": f"before-{sample_value}"},
            ],
            "afterImages": [
                {"precision": 20, "value": "1"},
                {"precision": 20, "value": sample_value},
                {"value": True},
                {"timestamp": 1_786_000_001},
                {"charset": "utf8", "value": f"after-{sample_value}"},
            ],
        },
    }


def _observer_env(tmp_path: Path) -> dict[str, str]:
    return {
        "TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED": "true",
        "TIT_DTS_SOURCE_METADATA_SOURCE_MODE": "offline_jsonl",
        "TIT_DTS_SOURCE_METADATA_REGION": "dom",
        "TIT_DTS_SOURCE_METADATA_DIAGNOSTIC_GROUP_ID": (
            "diagnostic-system-id"
        ),
        "TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID": "formal-system-id",
        "TIT_DTS_SOURCE_METADATA_INPUT_JSONL": str(
            tmp_path / "input.jsonl"
        ),
        "TIT_DTS_SOURCE_METADATA_OUTPUT": str(tmp_path / "evidence.json"),
        "TIT_DTS_SOURCE_METADATA_MAX_EVENTS": "100",
        "TIT_DTS_SOURCE_METADATA_MAX_DURATION_SECONDS": "30",
    }


def test_observation_is_canonical_value_blind_and_region_bound() -> None:
    report = observe_official_java_metadata(
        [
            _official_java_event(sample_value="fixture-person-alpha"),
            _official_java_event(sample_value="fixture-person-beta"),
        ],
        source_region="dom",
        max_events=10,
        max_duration_seconds=10,
    )

    payload = json.loads(report.canonical_json)
    assert payload == {
        "schema_version": 1,
        "source_region": "dom",
        "evidence_sha256": report.evidence_sha256,
        "observations": [
            {
                "table_name": "dom_appoint",
                "operation": "UPDATE",
                "before_image": {
                    "presence": "FIELDS",
                    "field_count": 5,
                    "field_names": [
                        "active",
                        "happened_at",
                        "id",
                        "memo",
                        "student_id",
                    ],
                },
                "after_image": {
                    "presence": "FIELDS",
                    "field_count": 5,
                    "field_names": [
                        "active",
                        "happened_at",
                        "id",
                        "memo",
                        "student_id",
                    ],
                },
                "observed_field_types": {
                    "active": "BOOLEAN",
                    "happened_at": "TEMPORAL",
                    "id": "NUMERIC",
                    "memo": "TEXT",
                    "student_id": "NUMERIC",
                },
                "observed_field_type_numbers": {
                    "active": 1,
                    "happened_at": 93,
                    "id": 20,
                    "memo": 12,
                    "student_id": 20,
                },
                "sample_count": 2,
            }
        ],
    }
    canonical_without_sha = json.dumps(
        {
            key: value
            for key, value in payload.items()
            if key != "evidence_sha256"
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert report.evidence_sha256 == hashlib.sha256(
        canonical_without_sha.encode("utf-8")
    ).hexdigest()
    assert report.canonical_json == json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for forbidden in (
        "fixture-person-alpha",
        "fixture-person-beta",
        "fixture-production-topic",
        "fixture-source-account",
        "fixture-source-position",
        "fixture-safe-position",
        "fixture-transaction",
        "81234",
        "9001",
    ):
        assert forbidden not in report.canonical_json


def test_adapter_never_stringifies_or_compares_business_values() -> None:
    class ValueThatMustNotBeRead:
        def __eq__(self, other: object) -> bool:
            del other
            raise AssertionError("source value was compared")

        def __repr__(self) -> str:
            raise AssertionError("source value was represented")

        def __str__(self) -> str:
            raise AssertionError("source value was stringified")

    envelope = _official_java_event()
    record = envelope["record"]
    record["fields"] = ["opaque_value"]
    record["fieldTypeNumbers"] = {"opaque_value": 999}
    record["beforeImages"] = [ValueThatMustNotBeRead()]
    record["afterImages"] = [ValueThatMustNotBeRead()]

    event = metadata_event_from_official_java_envelope(
        envelope,
        source_region="dom",
    )

    assert event is not None
    assert tuple(event.before or {}) == ("opaque_value",)
    assert tuple(event.after or {}) == ("opaque_value",)
    assert dict(event.source_field_types) == {}
    assert dict(event.source_field_type_numbers) == {"opaque_value": 999}


def test_settings_require_explicit_offline_enable_and_distinct_group(
    tmp_path: Path,
) -> None:
    values = _observer_env(tmp_path)
    values.pop("TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED")
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^TIT_DTS_SOURCE_METADATA_OBSERVER_EXPLICIT_ENABLE_REQUIRED$",
    ):
        DtsSourceMetadataObserverSettings.from_env(values)

    values = _observer_env(tmp_path)
    values["TIT_DTS_SOURCE_METADATA_SOURCE_MODE"] = "official_java_live"
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^TIT_DTS_SOURCE_METADATA_SOURCE_MODE_UNSUPPORTED$",
    ):
        DtsSourceMetadataObserverSettings.from_env(values)

    values = _observer_env(tmp_path)
    values["TIT_DTS_SOURCE_METADATA_DIAGNOSTIC_GROUP_ID"] = (
        "formal-system-id"
    )
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_REUSE_FORBIDDEN$",
    ):
        DtsSourceMetadataObserverSettings.from_env(values)

    values = _observer_env(tmp_path)
    values["TIT_DTS_GROUP_ID"] = "different-formal-system-id"
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID_MISMATCH$",
    ):
        DtsSourceMetadataObserverSettings.from_env(values)

    values["TIT_DTS_GROUP_ID"] = "formal-system-id"
    settings = DtsSourceMetadataObserverSettings.from_env(values)
    assert settings.diagnostic_group_id == "diagnostic-system-id"
    assert settings.formal_group_id == "formal-system-id"


def test_adapter_skips_controls_and_fails_closed_on_unsafe_shapes() -> None:
    control = _official_java_event(operation="HEARTBEAT")
    assert (
        metadata_event_from_official_java_envelope(
            control,
            source_region="dom",
        )
        is None
    )

    lightweight = _official_java_event()
    lightweight["lightweight"] = True
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^DTS_SOURCE_METADATA_LIGHTWEIGHT_EVENT_FORBIDDEN$",
    ):
        metadata_event_from_official_java_envelope(
            lightweight,
            source_region="dom",
        )

    wrong_region = _official_java_event(table="ovs_appoint")
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^DTS_SOURCE_METADATA_TABLE_REGION_MISMATCH$",
    ):
        metadata_event_from_official_java_envelope(
            wrong_region,
            source_region="dom",
        )

    mismatched_identity = _official_java_event()
    mismatched_identity["record"]["tags"] = {"tableName": "dom_teacher"}
    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^DTS_SOURCE_METADATA_TABLE_IDENTITY_CONFLICT$",
    ):
        metadata_event_from_official_java_envelope(
            mismatched_identity,
            source_region="dom",
        )


def test_event_count_and_duration_bound_the_observed_vector() -> None:
    first = _official_java_event(table="dom_appoint")
    second = _official_java_event(table="dom_teacher")
    by_count = observe_official_java_metadata(
        [first, second],
        source_region="dom",
        max_events=1,
        max_duration_seconds=10,
    )
    assert [
        item["table_name"]
        for item in json.loads(by_count.canonical_json)["observations"]
    ] == ["dom_appoint"]

    ticks: Iterator[float] = iter((0.0, 0.0, 0.0, 2.0))
    by_duration = observe_official_java_metadata(
        [first, second],
        source_region="dom",
        max_events=10,
        max_duration_seconds=1,
        monotonic=lambda: next(ticks),
    )
    assert [
        item["table_name"]
        for item in json.loads(by_duration.canonical_json)["observations"]
    ] == ["dom_appoint"]


def test_jsonl_cli_atomically_writes_owner_only_canonical_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = _observer_env(tmp_path)
    input_path = Path(values["TIT_DTS_SOURCE_METADATA_INPUT_JSONL"])
    input_path.write_text(
        "\n"
        + json.dumps(
            _official_java_event(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    result = observer_cli._run(
        observer_cli.build_parser().parse_args([]),
        environ=values,
    )

    output_path = Path(values["TIT_DTS_SOURCE_METADATA_OUTPUT"])
    output = output_path.read_text(encoding="utf-8")
    assert result == 0
    assert capsys.readouterr().out == output + "\n"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert json.dumps(
        json.loads(output),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == output
    assert list(tmp_path.glob(f".{output_path.name}.*.tmp")) == []


def test_jsonl_errors_are_stable_and_never_echo_input_values(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "invalid.jsonl"
    input_path.write_text(
        '{"duplicate":"fixture-alpha","duplicate":"fixture-beta"}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        DtsSourceMetadataObserverError,
        match="^DTS_SOURCE_METADATA_INPUT_JSON_KEY_DUPLICATE$",
    ) as failure:
        list(OfficialJavaEventJsonlSource(input_path))

    assert "fixture-alpha" not in str(failure.value)
    assert "fixture-beta" not in str(failure.value)


def test_cli_failure_prints_only_canonical_error_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["observe_dts_source_metadata.py"])
    monkeypatch.setenv("TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED", "false")

    assert observer_cli.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        '{"error_code":'
        '"TIT_DTS_SOURCE_METADATA_OBSERVER_EXPLICIT_ENABLE_REQUIRED"}\n'
    )


def test_observer_has_no_formal_transport_database_or_ack_dependency() -> None:
    module_path = Path(__file__).parents[1] / "app" / (
        "dts_source_metadata_observer.py"
    )
    script_path = Path(__file__).parents[1] / "scripts" / (
        "observe_dts_source_metadata.py"
    )
    source = module_path.read_text(encoding="utf-8") + script_path.read_text(
        encoding="utf-8"
    )

    for forbidden in (
        "from .dts_source_consumer import",
        "OfficialJavaDtsTransport",
        "DURABLE_ACK",
        "sqlalchemy",
        "psycopg",
        "dts_ingest_store",
        "run_dts_ingest",
    ):
        assert forbidden not in source


def test_production_example_defaults_disabled_and_requires_human_approval() -> None:
    backend_root = Path(__file__).parents[1]
    example = (
        backend_root / ".env.dts-source-metadata.production.example"
    ).read_text(encoding="utf-8")
    guide = (
        backend_root.parent / "docs" / "DTS_source_profile元数据观察工具.md"
    ).read_text(encoding="utf-8")

    assert "TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED=false" in example
    assert "TIT_DTS_SOURCE_METADATA_SOURCE_MODE=offline_jsonl" in example
    assert "TIT_DTS_SOURCE_METADATA_DIAGNOSTIC_GROUP_ID=" in example
    assert "TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID=" in example
    assert "TIT_DTS_BROKER" not in example
    assert "DATABASE_URL" not in example
    assert "人工批准" in guide
    assert "不能据此宣称已经连接生产" in guide
