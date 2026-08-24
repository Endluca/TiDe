from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app import dts_source_contract_v2 as source_contract
from app.dts_source_consumer import DtsChangeEvent
from app.dts_source_consumer import DtsRecordError
from app.dts_source_contract_v2 import (
    V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE,
    V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
    V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
)
from app.dts_source_profile_registry_v2 import (
    DEFAULT_DTS_SOURCE_PROFILE_MANIFEST_PATH,
    DtsSourceMetadataAggregator,
    DtsSourceProfileManifestError,
    load_dts_source_profile_registry,
)


def _profile() -> dict[str, Any]:
    return {
        "table": "dom_appoint",
        "region": "dom",
        "primary_key_type": "NUMERIC",
        "selected_raw_fields": ["id", "student_id", "t_id"],
        "selected_field_set_policy": "EXACT",
        "persisted_protected_fields": ["id", "student_token", "t_id"],
        "protected_derived_fields": ["student_token"],
        "image_modes": {
            "INSERT": "FULL",
            "UPDATE": "SPARSE",
            "DELETE": "SPARSE",
        },
        "field_type_evidence": {
            "id": "NUMERIC",
            "student_id": "NUMERIC",
            "t_id": "NUMERIC",
        },
        "raw_field_type_numbers": {"id": 20, "student_id": 20, "t_id": 20},
        "evidence": {
            "provenance": "approved DTS decoded-metadata attestation",
            "sha256": "a" * 64,
        },
    }


def _write_manifest(
    path: Path,
    *,
    profiles: list[dict[str, Any]],
) -> None:
    path.write_text(
        json.dumps(
            {"manifest_version": 1, "profiles": profiles},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_production_manifest_and_contract_maps_are_empty_fail_closed() -> None:
    registry = load_dts_source_profile_registry()

    assert DEFAULT_DTS_SOURCE_PROFILE_MANIFEST_PATH.name == (
        "dts_source_profiles_v2.json"
    )
    assert dict(registry.profiles_by_table) == {}
    assert V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE == {}
    assert V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE == {}
    assert V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE == {}


def test_manifest_keeps_physical_and_protected_derived_fields_separate(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "profiles.json"
    _write_manifest(manifest, profiles=[_profile()])

    registry = load_dts_source_profile_registry(
        manifest,
        allowed_tables={"dom_appoint"},
    )
    loaded = registry.profile_for("dom_appoint")

    assert loaded is not None
    assert loaded.selected_raw_fields == {"id", "student_id", "t_id"}
    assert "student_token" not in loaded.selected_raw_fields
    assert loaded.selected_field_set_policy == "EXACT"
    assert loaded.persisted_protected_fields == {
        "id",
        "student_token",
        "t_id",
    }
    assert loaded.protected_derived_fields == {"student_token"}
    assert loaded.image_modes == {
        "INSERT": "FULL",
        "UPDATE": "SPARSE",
        "DELETE": "SPARSE",
    }
    assert loaded.field_type_evidence == {
        "id": "NUMERIC",
        "student_id": "NUMERIC",
        "t_id": "NUMERIC",
    }
    assert loaded.raw_field_type_numbers == {
        "id": 20,
        "student_id": 20,
        "t_id": 20,
    }
    assert loaded.evidence_provenance == (
        "approved DTS decoded-metadata attestation"
    )
    assert loaded.evidence_sha256 == "a" * 64
    assert re.fullmatch(
        r"dts-source-schema:v2:[0-9a-f]{64}",
        loaded.profile_id,
    )


def test_evidence_hash_changes_profile_identity(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first = _profile()
    second = deepcopy(first)
    second["evidence"]["sha256"] = "b" * 64
    _write_manifest(first_path, profiles=[first])
    _write_manifest(second_path, profiles=[second])

    first_registry = load_dts_source_profile_registry(first_path)
    second_registry = load_dts_source_profile_registry(second_path)

    assert (
        first_registry.profiles_by_table["dom_appoint"].profile_id
        != second_registry.profiles_by_table["dom_appoint"].profile_id
    )


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda profile: profile.update(
                selected_raw_fields=sorted(
                    profile["selected_raw_fields"] + ["student_token"]
                )
            ),
            "DTS_SOURCE_PROFILE_DERIVED_FIELD_MARKED_RAW",
        ),
        (
            lambda profile: profile.update(region="ovs"),
            "DTS_SOURCE_PROFILE_REGION_TABLE_MISMATCH",
        ),
        (
            lambda profile: profile["field_type_evidence"].update(
                id="TEXT"
            ),
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_EVIDENCE_MISMATCH",
        ),
        (
            lambda profile: profile["raw_field_type_numbers"].update(
                secret=999
            ),
            "DTS_SOURCE_PROFILE_TYPE_NUMBER_NOT_RAW",
        ),
        (
            lambda profile: profile["evidence"].update(sha256="not-a-hash"),
            "DTS_SOURCE_PROFILE_EVIDENCE_SHA256_INVALID",
        ),
        (
            lambda profile: profile["image_modes"].update(INSERT="SPARSE"),
            "DTS_SOURCE_PROFILE_INSERT_IMAGE_MUST_BE_FULL",
        ),
        (
            lambda profile: profile.update(
                persisted_protected_fields=["id", "student_id"]
            ),
            "DTS_SOURCE_PROFILE_PERSISTED_FIELD_NOT_PROTECTED",
        ),
        (
            lambda profile: profile.update(
                raw_field_type_numbers={"student_id": 20}
            ),
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_NUMBER_MISSING",
        ),
        (
            lambda profile: profile.update(
                raw_field_type_numbers={"id": -1}
            ),
            "DTS_SOURCE_PROFILE_TYPE_NUMBER_INVALID",
        ),
    ],
)
def test_manifest_rejects_ambiguous_or_unattested_profiles(
    tmp_path: Path,
    mutate: Any,
    error: str,
) -> None:
    profile = _profile()
    mutate(profile)
    manifest = tmp_path / "invalid.json"
    _write_manifest(manifest, profiles=[profile])

    with pytest.raises(DtsSourceProfileManifestError, match=f"^{error}$"):
        load_dts_source_profile_registry(manifest)


def test_manifest_rejects_table_outside_callers_business_registry(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "profiles.json"
    _write_manifest(manifest, profiles=[_profile()])

    with pytest.raises(
        DtsSourceProfileManifestError,
        match="^DTS_SOURCE_PROFILE_TABLE_NOT_ALLOWED$",
    ):
        load_dts_source_profile_registry(
            manifest,
            allowed_tables={"ovs_appoint"},
        )


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest = tmp_path / "duplicate.json"
    manifest.write_text(
        '{"manifest_version":1,"manifest_version":1,"profiles":[]}',
        encoding="utf-8",
    )

    with pytest.raises(
        DtsSourceProfileManifestError,
        match="^DTS_SOURCE_PROFILE_MANIFEST_JSON_KEY_DUPLICATE$",
    ):
        load_dts_source_profile_registry(manifest)


@pytest.mark.parametrize(
    ("profile_field_types", "profile_type_numbers", "error"),
    [
        (
            {"id": "TEXT"},
            {},
            "DTS_SOURCE_FIELD_TYPE_PROFILE_MISMATCH",
        ),
        (
            {},
            {"id": 7},
            "DTS_SOURCE_FIELD_TYPE_NUMBER_PROFILE_MISMATCH",
        ),
        (
            {},
            {"id": 20, "t_id": 20},
            "DTS_SOURCE_FIELD_TYPE_NUMBER_EVIDENCE_MISSING",
        ),
    ],
)
def test_completeness_attestation_rejects_profile_type_drift(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_v2_appoint_profiles: Any,
    profile_field_types: dict[str, str],
    profile_type_numbers: dict[str, int],
    error: str,
) -> None:
    del synthetic_v2_appoint_profiles
    raw_after = {
        field_name: None
        for field_name in source_contract.V2_SOURCE_FIELD_WHITELIST["appoint"]
        if field_name != "student_token"
    }
    raw_after["id"] = 1
    event = DtsChangeEvent(
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=1,
        record_id=1,
        source_timestamp=1,
        source_txid="tx",
        source_position="position",
        operation="INSERT",
        database_name="business",
        schema_name="public",
        table_name="ovs_appoint",
        before=None,
        after=raw_after,
        source_field_types={"id": "NUMERIC"},
        source_field_type_numbers={"id": 20},
    )
    monkeypatch.setitem(
        source_contract.V2_SOURCE_FIELD_TYPE_EVIDENCE_BY_TABLE,
        "ovs_appoint",
        profile_field_types,
    )
    monkeypatch.setitem(
        source_contract.V2_SOURCE_RAW_FIELD_TYPE_NUMBERS_BY_TABLE,
        "ovs_appoint",
        profile_type_numbers,
    )

    with pytest.raises(DtsRecordError, match=f"^{error}$"):
        source_contract.with_v2_source_image_completeness(event)


def test_profile_type_number_can_cover_a_null_value_without_value_hint(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_v2_appoint_profiles: Any,
) -> None:
    del synthetic_v2_appoint_profiles
    raw_after = {
        field_name: None
        for field_name in source_contract.V2_SOURCE_FIELD_WHITELIST["appoint"]
        if field_name != "student_token"
    }
    raw_after["id"] = 1
    event = DtsChangeEvent(
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=1,
        record_id=1,
        source_timestamp=1,
        source_txid="tx",
        source_position="position",
        operation="INSERT",
        database_name="business",
        schema_name="public",
        table_name="ovs_appoint",
        before=None,
        after=raw_after,
        source_field_types={"id": "NUMERIC"},
        source_field_type_numbers={"id": 20, "t_id": 20},
    )
    monkeypatch.setitem(
        source_contract.V2_SOURCE_FIELD_TYPE_EVIDENCE_BY_TABLE,
        "ovs_appoint",
        {"id": "NUMERIC", "t_id": "NUMERIC"},
    )
    monkeypatch.setitem(
        source_contract.V2_SOURCE_RAW_FIELD_TYPE_NUMBERS_BY_TABLE,
        "ovs_appoint",
        {"id": 20, "t_id": 20},
    )

    completed = source_contract.with_v2_source_image_completeness(event)

    assert completed.source_images_complete is True


@pytest.mark.parametrize(
    ("update_mode", "error"),
    [
        ("FULL", "DTS_SOURCE_IMAGE_MODE_VIOLATION"),
        ("SPARSE", None),
    ],
)
def test_profile_update_image_mode_controls_sparse_images(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_v2_appoint_profiles: Any,
    update_mode: str,
    error: str | None,
) -> None:
    del synthetic_v2_appoint_profiles
    monkeypatch.setitem(
        source_contract.V2_SOURCE_IMAGE_MODES_BY_TABLE,
        "ovs_appoint",
        {"INSERT": "FULL", "UPDATE": update_mode, "DELETE": "FULL"},
    )
    event = DtsChangeEvent(
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=1,
        record_id=1,
        source_timestamp=1,
        source_txid="tx",
        source_position="position",
        operation="UPDATE",
        database_name="business",
        schema_name="public",
        table_name="ovs_appoint",
        before={"id": 1, "t_id": 7},
        after={"id": 1, "t_id": 8},
        source_field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
        source_field_type_numbers={"id": 20, "t_id": 20},
    )

    if error is not None:
        with pytest.raises(DtsRecordError, match=f"^{error}$"):
            source_contract.with_v2_source_image_completeness(event)
    else:
        observed = source_contract.with_v2_source_image_completeness(event)
        assert observed.source_images_complete is False


def _event(*, student_value: Any) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region="dom",
        topic="fixture-diagnostic-topic",
        partition=2,
        offset=9001,
        record_id=81234,
        source_timestamp=1_786_000_000,
        source_txid="fixture-transaction",
        source_position="fixture-position",
        operation="UPDATE",
        database_name="business",
        schema_name="public",
        table_name="dom_appoint",
        before={"id": 1, "student_id": student_value, "t_id": 7},
        after={"id": 1, "student_id": student_value, "t_id": 8},
        source_field_types={
            "id": "NUMERIC",
            "student_id": "NUMERIC",
            "t_id": "NUMERIC",
        },
        source_field_type_numbers={"id": 20, "student_id": 20, "t_id": 20},
    )


def test_metadata_aggregator_emits_shapes_and_type_numbers_but_no_values() -> None:
    aggregator = DtsSourceMetadataAggregator()
    aggregator.observe(_event(student_value="fixture-person-alpha"))
    aggregator.observe(_event(student_value="another-student"))

    payload = aggregator.to_payload()
    rendered = aggregator.to_canonical_json()

    assert payload == {
        "schema_version": 1,
        "observations": [
            {
                "table_name": "dom_appoint",
                "operation": "UPDATE",
                "before_image": {
                    "presence": "FIELDS",
                    "field_count": 3,
                    "field_names": ["id", "student_id", "t_id"],
                },
                "after_image": {
                    "presence": "FIELDS",
                    "field_count": 3,
                    "field_names": ["id", "student_id", "t_id"],
                },
                "observed_field_types": {
                    "id": "NUMERIC",
                    "student_id": "NUMERIC",
                    "t_id": "NUMERIC",
                },
                "observed_field_type_numbers": {
                    "id": 20,
                    "student_id": 20,
                    "t_id": 20,
                },
                "sample_count": 2,
            }
        ],
    }
    for forbidden in (
        "fixture-person-alpha",
        "another-student",
        "fixture-diagnostic-topic",
        "fixture-transaction",
        "fixture-position",
        "81234",
        "9001",
    ):
        assert forbidden not in rendered
    assert re.fullmatch(r"[0-9a-f]{64}", aggregator.evidence_sha256())


def test_metadata_aggregator_never_inspects_image_values() -> None:
    class ValueThatMustNotBeRead:
        def __repr__(self) -> str:
            raise AssertionError("source value was inspected")

        def __str__(self) -> str:
            raise AssertionError("source value was inspected")

    aggregator = DtsSourceMetadataAggregator()
    aggregator.observe(_event(student_value=ValueThatMustNotBeRead()))

    assert aggregator.to_payload()["observations"][0]["sample_count"] == 1


def test_metadata_aggregator_rejects_unbounded_signature_growth() -> None:
    aggregator = DtsSourceMetadataAggregator(max_signatures=1)
    aggregator.observe(_event(student_value="student"))
    different_operation = DtsChangeEvent(
        **{
            **_event(student_value="student").__dict__,
            "operation": "INSERT",
            "before": None,
        }
    )

    with pytest.raises(
        ValueError,
        match="^DTS_SOURCE_METADATA_SIGNATURE_LIMIT_EXCEEDED$",
    ):
        aggregator.observe(different_operation)
