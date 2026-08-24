from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConsumerSettings,
    DtsRecordError,
    build_change_event,
    protect_domestic_student_ids,
)
from app.dts_source_contract_v2 import (
    V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION,
    V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE,
    V2ValidatedCurrentRow,
    V2_SOURCE_FIELD_WHITELIST,
    V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
    V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
    build_v2_source_route,
    with_v2_source_image_completeness,
)
from dts_v2_test_profiles import SYNTHETIC_APPOINT_PROFILE_IDS


_PRODUCTION_PROFILE_DEFAULTS = (
    dict(V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE),
    dict(V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE),
    dict(V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE),
)
pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")


def _dom_settings() -> DtsConsumerSettings:
    return DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.invalid:9092",),
        topic="dom-topic",
        group_id="dom-v2-source-contract-test",
        account="test-account",
        password="test-password",
        execution_region="cn",
        domestic_student_hmac_key="11" * 32,
    )


def _event(
    *,
    table: str,
    operation: str = "INSERT",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    region: str = "ovs",
    source_id_type: str | None = None,
    source_field_types: dict[str, str] | None = None,
    images_complete: bool = False,
) -> DtsChangeEvent:
    identity = (after or before or {}).get("id")
    if source_id_type is None and identity is not None:
        source_id_type = (
            "NUMERIC" if isinstance(identity, (int, float)) else "TEXT"
        )
    return DtsChangeEvent(
        source_region=region,
        topic=f"{region}-topic",
        partition=0,
        offset=9,
        record_id=10,
        source_timestamp=1_786_000_000,
        source_txid="tx-1",
        source_position="opaque-v1-position",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name=table,
        before=before,
        after=after,
        source_field_types={
            **({"id": source_id_type} if source_id_type is not None else {}),
            **(source_field_types or {}),
        },
        source_images_complete=images_complete,
    )


def _complete_appoint_insert(
    *,
    region: str,
    after: dict[str, Any],
    source_field_types: dict[str, str] | None = None,
) -> DtsChangeEvent:
    raw_after = {
        field_name: None
        for field_name in V2_SOURCE_FIELD_WHITELIST["appoint"]
        if field_name != "student_token"
    }
    raw_after.update(after)
    event = with_v2_source_image_completeness(
        _event(
            table=f"{region}_appoint",
            region=region,
            after=raw_after,
            source_field_types=source_field_types,
        )
    )
    if region == "dom":
        event = protect_domestic_student_ids(event, _dom_settings())
    assert event.source_images_complete is True
    return event


def _prepared_event_contract_insert(
    *, table: str, region: str, after: dict[str, Any]
) -> DtsChangeEvent:
    event = with_v2_source_image_completeness(
        _event(table=table, region=region, after=after)
    )
    if region == "dom":
        event = protect_domestic_student_ids(event, _dom_settings())
    return event


def _validated_appoint_current(
    *,
    region: str,
    row: dict[str, Any],
    source_key: str | None = None,
    source_key_type: str | None = None,
    source_table: str | None = None,
    source_schema_profile_id: str | None = None,
    source_field_types: dict[str, str] | None = None,
) -> V2ValidatedCurrentRow:
    table = source_table or f"{region}_appoint"
    key_type = source_key_type or (
        "NUMERIC" if isinstance(row["id"], (int, float, Decimal)) else "TEXT"
    )
    return V2ValidatedCurrentRow(
        source_region=region,
        source_table=table,
        source_key=source_key or str(row["id"]),
        source_key_type=key_type,
        source_schema_profile_id=(
            source_schema_profile_id
            or SYNTHETIC_APPOINT_PROFILE_IDS[f"{region}_appoint"]
        ),
        source_field_types=(
            source_field_types
            or _source_field_types_for_current(row, id_type=key_type)
        ),
        row=row,
    )


def _ovs_appoint_current(
    row: dict[str, Any],
    **overrides: Any,
) -> V2ValidatedCurrentRow:
    return _validated_appoint_current(region="ovs", row=row, **overrides)


def _dom_appoint_current(
    row: dict[str, Any],
    **overrides: Any,
) -> V2ValidatedCurrentRow:
    return _validated_appoint_current(region="dom", row=row, **overrides)


def _source_field_types_for_current(
    row: dict[str, Any],
    *,
    id_type: str,
) -> dict[str, str]:
    def field_type(value: Any) -> str:
        if isinstance(value, bool):
            return "BOOLEAN"
        if isinstance(value, (int, float, Decimal)):
            return "NUMERIC"
        return "TEXT"

    return {
        field_name: id_type if field_name == "id" else field_type(value)
        for field_name, value in row.items()
        if value is not None
    }


def test_grading_whitelist_retains_confirmed_rule_fields_until_profile_is_verified() -> None:
    assert {
        "id",
        "appoint_id",
        "teacher_id",
        "use_point",
        "type",
        "score",
    } <= V2_SOURCE_FIELD_WHITELIST["user_teacher_grading"]
    assert "private_note" not in V2_SOURCE_FIELD_WHITELIST["user_teacher_grading"]


def test_absence_and_certification_use_code_owned_event_contract() -> None:
    assert "reason_type" in V2_SOURCE_FIELD_WHITELIST["teacher_absent_reason"]
    assert "reason_desc" not in V2_SOURCE_FIELD_WHITELIST["teacher_absent_reason"]
    assert {
        "certification_code",
        "certification_status",
    } <= V2_SOURCE_FIELD_WHITELIST["teacher_certification"]

    for table, after in (
        (
            "dom_teacher_absent_reason",
            {"id": "r-1", "appoint_id": "a-1", "reason_type": "No Notification"},
        ),
        (
            "dom_teacher_certification",
            {"id": 1, "teacher_id": 7, "certification_code": "16", "certification_status": 1},
        ),
    ):
        route = build_v2_source_route(
            _prepared_event_contract_insert(
                table=table,
                region="dom",
                after=after,
            )
        )
        assert route.route_status == "VERSIONED"
        assert route.source_schema_profile_id == f"dts-event-fields:v1:{table}"


def test_production_defaults_use_event_images_without_physical_table_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _PRODUCTION_PROFILE_DEFAULTS == ({}, {}, {})
    for table in SYNTHETIC_APPOINT_PROFILE_IDS:
        monkeypatch.delitem(
            V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE,
            table,
            raising=False,
        )
        monkeypatch.delitem(
            V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
            table,
            raising=False,
        )
        monkeypatch.delitem(
            V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
            table,
            raising=False,
        )

    for region, suffixes in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION.items():
        for suffix in suffixes:
            event = with_v2_source_image_completeness(
                _event(
                    table=f"{region}_{suffix}",
                    region=region,
                    after={"id": 1},
                )
            )
            if region == "dom":
                event = protect_domestic_student_ids(event, _dom_settings())
            assert event.source_images_complete is True
            assert event.source_image_profile_id == (
                f"dts-event-fields:v1:{region}_{suffix}"
            )
            assert build_v2_source_route(event).route_status == "VERSIONED"


def test_appoint_rejects_removed_cancel_reason_when_not_physically_selected() -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_IMAGE_FIELD_SET_UNEXPECTED$",
    ):
        _complete_appoint_insert(
            region="dom",
            after={
                "id": 1,
                "t_id": "t-1",
                "status": "on",
                "cancel_reason": "not a v2 business source",
            },
        )


@pytest.mark.parametrize(
    ("region", "table", "expected_status"),
    [
        ("dom", "dom_qa_ac_classroom_record", "IGNORED_RETIRED_SOURCE"),
        (
            "ovs",
            "ovs_qa_task_fake_early_leave_record",
            "IGNORED_RETIRED_SOURCE",
        ),
    ],
)
def test_unsupported_qa_route_never_carries_source_payload(
    region: str,
    table: str,
    expected_status: str,
) -> None:
    decision = build_v2_source_route(
        _event(
            table=table,
            region=region,
            after={
                "id": 1,
                "info": {
                    "student_id": "raw-value-is-discarded-before-protection",
                    "secret": "must-not-be-copied",
                },
            },
        )
    )

    assert decision.route_status == expected_status
    assert decision.should_append_version is False
    assert decision.source_key is None
    assert decision.before_row is None
    assert decision.after_row is None
    assert decision.protected_payload_hash is None


def test_unknown_dom_table_with_raw_student_data_is_payload_free_ignored() -> None:
    decision = build_v2_source_route(
        _event(
            table="dom_unknown_business_table",
            region="dom",
            after={
                "id": 1,
                "student_id": "raw-student-must-not-cross-boundary",
                "secret": "must-not-be-copied",
            },
        )
    )

    assert decision.route_status == "IGNORED_TABLE_NOT_IN_V2_WHITELIST"
    assert decision.should_append_version is False
    assert decision.source_key is None
    assert decision.before_row is None
    assert decision.after_row is None
    assert decision.protected_payload_hash is None


@pytest.mark.parametrize(
    ("region", "table"),
    [("ovs", "dom_appoint"), ("dom", "ovs_teacher_favorite")],
)
def test_known_business_table_cannot_cross_the_event_region(
    region: str,
    table: str,
) -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_REGION_TABLE_MISMATCH$",
    ):
        build_v2_source_route(
            _event(
                table=table,
                region=region,
                after={"id": 1},
            )
        )


@pytest.mark.parametrize(
    ("operation", "before", "after"),
    [
        ("INSERT", None, {"status": "on"}),
        ("UPDATE", {"id": 1}, {"status": "end"}),
        ("UPDATE", {"status": "on"}, {"id": 1}),
        ("DELETE", {"status": "on"}, None),
    ],
)
def test_each_authoritative_dml_image_requires_a_non_null_id(
    operation: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_PRIMARY_KEY_MISSING$",
    ):
        build_v2_source_route(
            _event(
                table="ovs_appoint",
                operation=operation,
                before=before,
                after=after,
                source_id_type="NUMERIC",
            ),
            current=_ovs_appoint_current({"id": 1, "status": "on"}),
        )


def test_partial_appoint_insert_fails_closed() -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_IMAGE_INCOMPLETE$",
    ):
        build_v2_source_route(
            _event(
                table="ovs_appoint",
                after={"id": 1, "status": "on"},
                images_complete=False,
            )
        )


def test_partial_decoder_cannot_claim_appoint_image_completeness() -> None:
    decoded = build_change_event(
        {
            "id": 10,
            "sourceTimestamp": 1_786_000_000,
            "sourcePosition": "position",
            "sourceTxid": "tx-1",
            "operation": "INSERT",
            "objectName": "public.ovs_appoint",
            "fields": [{"name": "id", "dataTypeNumber": 20}],
            "beforeImages": None,
            "afterImages": [{"precision": 20, "value": "1"}],
        },
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=9,
    )
    event = with_v2_source_image_completeness(decoded)

    assert event.source_images_complete is False
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_IMAGE_INCOMPLETE$",
    ):
        build_v2_source_route(event)


def test_handmade_event_cannot_forge_complete_source_image_proof() -> None:
    event = replace(
        _event(
            table="ovs_appoint",
            after={"id": 1, "status": "on"},
            images_complete=True,
        ),
        source_image_profile_id=V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE[
            "ovs_appoint"
        ],
    )

    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_IMAGE_COMPLETENESS_PROOF_INVALID$",
    ):
        build_v2_source_route(event)


def test_handmade_dom_token_cannot_forge_domestic_protection_proof() -> None:
    event = _event(
        table="dom_appoint",
        region="dom",
        after={
            "id": 9001,
            "student_token": "dom:v1:" + "a" * 64,
            "status": "on",
        },
    )

    with pytest.raises(
        DtsRecordError,
        match="^DTS_DOM_PROTECTION_PROOF_INVALID$",
    ):
        build_v2_source_route(event)


def test_dom_complete_image_proof_survives_student_protection() -> None:
    event = _complete_appoint_insert(
        region="dom",
        after={
            "id": 9001,
            "t_id": 7,
            "s_id": 8,
            "status": "on",
        },
    )

    assert event.after is not None
    assert "s_id" not in event.after
    assert str(event.after["student_token"]).startswith("dom:v1:")
    decision = build_v2_source_route(event)
    assert decision.should_append_version is True
    assert decision.after_row is not None
    assert "s_id" not in decision.after_row
    assert str(decision.after_row["student_token"]).startswith("dom:v1:")


def test_dom_protected_event_proof_is_bound_to_the_protected_payload() -> None:
    event = _complete_appoint_insert(
        region="dom",
        after={
            "id": 9001,
            "t_id": 7,
            "s_id": 8,
            "status": "on",
        },
    )
    assert event.after is not None
    tampered = replace(
        event,
        after={
            **event.after,
            "student_token": "dom:v1:" + "b" * 64,
        },
    )

    with pytest.raises(
        DtsRecordError,
        match="^DTS_DOM_PROTECTION_PROOF_INVALID$",
    ):
        build_v2_source_route(tampered)


def test_synthetic_appoint_profiles_are_an_explicit_dom_ovs_closed_set() -> None:
    assert set(V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE) == {
        "dom_appoint",
        "ovs_appoint",
    }
    assert (
        V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE["dom_appoint"]
        != V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE["ovs_appoint"]
    )
    assert V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE == {
        "dom_appoint": "NUMERIC",
        "ovs_appoint": "NUMERIC",
    }


def test_versioned_appoint_propagates_profile_and_source_field_types() -> None:
    event = _complete_appoint_insert(
        region="ovs",
        after={"id": 9001, "t_id": "teacher-1", "status": "on"},
        source_field_types={"t_id": "TEXT"},
    )

    decision = build_v2_source_route(event)

    assert decision.source_schema_profile_id == event.source_image_profile_id
    assert decision.source_schema_profile_id == V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE[
        "ovs_appoint"
    ]
    assert decision.source_field_types == {
        "id": "NUMERIC",
        "status": "TEXT",
        "t_id": "TEXT",
    }


def test_source_field_types_change_appoint_payload_hash() -> None:
    after = {"id": 9001, "t_id": "001", "status": "on"}
    numeric = build_v2_source_route(
        _complete_appoint_insert(
            region="ovs",
            after=after,
            source_field_types={"t_id": "NUMERIC"},
        )
    )
    text = build_v2_source_route(
        _complete_appoint_insert(
            region="ovs",
            after=after,
            source_field_types={"t_id": "TEXT"},
        )
    )

    assert numeric.source_field_types == {
        "id": "NUMERIC",
        "status": "TEXT",
        "t_id": "NUMERIC",
    }
    assert text.source_field_types == {
        "id": "NUMERIC",
        "status": "TEXT",
        "t_id": "TEXT",
    }
    assert numeric.protected_payload_hash != text.protected_payload_hash


@pytest.mark.parametrize(
    ("current", "expected_error"),
    [
        (
            _dom_appoint_current({"id": 1, "t_id": "teacher-1"}),
            "DTS_SOURCE_CURRENT_PROVENANCE_MISMATCH",
        ),
        (
            _ovs_appoint_current(
                {"id": 1, "t_id": "teacher-1"},
                source_schema_profile_id="dts-source-schema:v1:wrong-profile",
            ),
            "DTS_SOURCE_CURRENT_PROVENANCE_MISMATCH",
        ),
        (
            _ovs_appoint_current(
                {"id": 1, "t_id": "teacher-1"},
                source_key="2",
            ),
            "DTS_SOURCE_CURRENT_IDENTITY_MISMATCH",
        ),
    ],
)
def test_appoint_current_requires_matching_provenance_profile_and_identity(
    current: V2ValidatedCurrentRow,
    expected_error: str,
) -> None:
    event = _event(
        table="ovs_appoint",
        operation="UPDATE",
        before={"id": 1, "t_id": "teacher-1"},
        after={"id": 1, "t_id": "teacher-2"},
    )

    with pytest.raises(DtsRecordError, match=f"^{expected_error}$"):
        build_v2_source_route(event, current=current)


def test_first_new_row_uses_event_as_its_baseline() -> None:
    route = build_v2_source_route(
        _prepared_event_contract_insert(
            table="ovs_user_teacher_grading",
            region="ovs",
            after={"id": 1, "appoint_id": 9, "score": 5},
        )
    )
    assert route.route_status == "VERSIONED"
    assert route.source_schema_profile_id == (
        "dts-event-fields:v1:ovs_user_teacher_grading"
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [("t_id", {"unexpected": "A"}), ("status", ["end"])],
)
def test_business_source_fields_must_be_scalar_or_null(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_FIELD_TYPE_INVALID$",
    ):
        build_v2_source_route(
            _event(
                table="ovs_appoint",
                after={"id": 1, field_name: invalid_value},
            )
        )


def test_appoint_rejects_text_id_that_disagrees_with_physical_profile() -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_PRIMARY_KEY_TYPE_MISMATCH$",
    ):
        build_v2_source_route(
            _complete_appoint_insert(
                region="ovs",
                after={"id": " 001 ", "t_id": "teacher", "status": "on"},
            )
        )


def test_update_cannot_change_typed_primary_key_identity() -> None:
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED$",
    ):
        event = _event(
            table="ovs_appoint",
            operation="UPDATE",
            before={"id": 1, "t_id": "A"},
            after={"id": 2, "t_id": "B"},
        )
        build_v2_source_route(
            event,
            current=_ovs_appoint_current(event.before),
        )


@pytest.mark.parametrize(
    ("operation", "before", "after", "expected_error"),
    [
        (
            "UPDATE",
            {"status": "on"},
            {"id": 2, "status": "end"},
            "DTS_SOURCE_PRIMARY_KEY_MISSING",
        ),
        (
            "UPDATE",
            {"id": 2, "status": "on"},
            {"status": "end"},
            "DTS_SOURCE_PRIMARY_KEY_MISSING",
        ),
        (
            "DELETE",
            {"id": 2, "status": "on"},
            None,
            "DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED",
        ),
    ],
)
def test_sparse_images_cannot_move_an_existing_typed_primary_key(
    operation: str,
    before: dict[str, Any],
    after: dict[str, Any] | None,
    expected_error: str,
) -> None:
    current = {"id": 1, "status": "on"}
    event = _event(
        table="ovs_appoint",
        operation=operation,
        before=before,
        after=after,
        source_id_type="NUMERIC",
    )

    with pytest.raises(
        DtsRecordError,
        match=f"^{expected_error}$",
    ):
        build_v2_source_route(
            event,
            current=_ovs_appoint_current(current),
        )


def test_numeric_primary_key_uses_canonical_equality_across_merged_images(
) -> None:
    event = _event(
        table="ovs_appoint",
        operation="UPDATE",
        before={"id": "01.0", "status": "on"},
        after={"id": Decimal("1.000"), "status": "end"},
        source_id_type="NUMERIC",
    )

    decision = build_v2_source_route(
        event,
        current=_ovs_appoint_current({"id": 1, "status": "on"}),
    )

    assert decision.should_append_version is True
    assert decision.source_key_type == "NUMERIC"
    assert decision.source_key == "1"
    assert decision.before_row == {"id": "01.0", "status": "on"}
    assert decision.after_row == {"id": "1.000", "status": "end"}


def test_sparse_grading_update_remains_profile_missing_even_with_current(
) -> None:
    current = {
        "id": "001",
        "teacher_id": "teacher-1",
        "appoint_id": "course-1",
        "use_point": "buy",
        "score": 2,
    }
    event = _event(
        table="dom_user_teacher_grading",
        operation="UPDATE",
        region="dom",
        before={"id": "001", "score": 2},
        after={"id": "001", "score": 5},
        source_id_type="NUMERIC",
    )

    unverified_current = V2ValidatedCurrentRow(
        source_region="dom",
        source_table="dom_user_teacher_grading",
        source_key="1",
        source_key_type="NUMERIC",
        source_schema_profile_id="dts-source-schema:v1:unverified-grading",
        source_field_types=_source_field_types_for_current(
            current,
            id_type="NUMERIC",
        ),
        row=current,
    )
    event = protect_domestic_student_ids(event, _dom_settings())
    with pytest.raises(
        DtsRecordError,
        match="^DTS_SOURCE_CURRENT_PROVENANCE_MISMATCH$",
    ):
        build_v2_source_route(event, current=unverified_current)


def test_first_complete_update_versions_without_an_existing_current_row() -> None:
    before = {
        field_name: None
        for field_name in V2_SOURCE_FIELD_WHITELIST["appoint"]
        if field_name != "student_token"
    }
    before.update({"id": 9001, "t_id": "A", "status": "on"})
    after = {**before, "t_id": "B", "status": "end"}

    event = with_v2_source_image_completeness(
        _event(
            table="ovs_appoint",
            operation="UPDATE",
            region="ovs",
            before=before,
            after=after,
        )
    )
    decision = build_v2_source_route(event)

    assert event.source_images_complete is True
    assert decision.should_append_version is True
    assert decision.before_row == before
    assert decision.after_row == after


def test_real_decoder_and_schema_adapter_reach_first_complete_update() -> None:
    field_names = sorted(
        V2_SOURCE_FIELD_WHITELIST["appoint"] - {"student_token"}
    )

    def images(*, teacher_id: bytes, status: bytes) -> list[object]:
        values: dict[str, object] = {
            field_name: None for field_name in field_names
        }
        values.update(
            {
                "id": {"precision": 20, "value": "9001"},
                "t_id": {"charset": "UTF-8", "value": teacher_id},
                "status": {"charset": "UTF-8", "value": status},
            }
        )
        return [values[field_name] for field_name in field_names]

    decoded = build_change_event(
        {
            "id": 10,
            "sourceTimestamp": 1_786_000_000,
            "sourcePosition": "position",
            "sourceTxid": "tx-1",
            "operation": "UPDATE",
            "objectName": "public.ovs_appoint",
            "fields": [
                {"name": field_name, "dataTypeNumber": 0}
                for field_name in field_names
            ],
            "beforeImages": images(teacher_id=b"A", status=b"on"),
            "afterImages": images(teacher_id=b"B", status=b"end"),
        },
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=9,
    )
    event = with_v2_source_image_completeness(decoded)

    decision = build_v2_source_route(event)

    assert event.source_images_complete is True
    assert decision.should_append_version is True
    assert decision.before_row is not None
    assert decision.after_row is not None
    assert decision.before_row["t_id"] == "A"
    assert decision.after_row["t_id"] == "B"


@pytest.mark.parametrize(
    ("raw_image", "expected_type", "expected_key"),
    [
        ({"precision": 20, "value": "001"}, "NUMERIC", "1"),
        ({"charset": "UTF-8", "value": b"001"}, "TEXT", "001"),
    ],
)
def test_avro_source_type_survives_value_decoding(
    raw_image: dict[str, Any],
    expected_type: str,
    expected_key: str,
) -> None:
    field_names = sorted(
        V2_SOURCE_FIELD_WHITELIST["appoint"] - {"student_token"}
    )
    values: dict[str, object] = {
        field_name: None for field_name in field_names
    }
    values["id"] = raw_image
    decoded = build_change_event(
        {
            "id": 10,
            "sourceTimestamp": 1_786_000_000,
            "sourcePosition": "position",
            "sourceTxid": "tx-1",
            "operation": "INSERT",
            "objectName": "public.ovs_appoint",
            "fields": [
                {"name": field_name, "dataTypeNumber": 20}
                for field_name in field_names
            ],
            "beforeImages": None,
            "afterImages": [values[field_name] for field_name in field_names],
        },
        source_region="ovs",
        topic="ovs-topic",
        partition=0,
        offset=9,
    )
    event = with_v2_source_image_completeness(decoded)

    assert event.after is not None
    assert event.after["id"] == "001"
    assert event.source_field_types == {"id": expected_type}
    if expected_type == "TEXT":
        with pytest.raises(
            DtsRecordError,
            match="^DTS_SOURCE_PRIMARY_KEY_TYPE_MISMATCH$",
        ):
            build_v2_source_route(event)
    else:
        decision = build_v2_source_route(event)
        assert decision.source_key_type == expected_type
        assert decision.source_key == expected_key
        assert decision.source_key_data_json == f'{{"id":{expected_key}}}'
        assert decision.source_key_numeric == Decimal(expected_key)
        assert decision.source_key_text is None


def test_source_key_type_comparison_is_case_insensitive() -> None:
    event = _event(
        table="ovs_appoint",
        operation="UPDATE",
        before={"id": 1, "status": "on"},
        after={"id": 1, "status": "end"},
        source_id_type="numeric",
    )

    decision = build_v2_source_route(
        event,
        current=_ovs_appoint_current(event.before),
    )

    assert decision.source_key_type == "NUMERIC"
    assert decision.source_key == "1"


@pytest.mark.parametrize(
    "unsafe_value",
    [float("nan"), float("inf"), {"not", "stable"}],
)
def test_non_canonical_payload_values_fail_closed(unsafe_value: object) -> None:
    current = {"id": 1, "t_id": "teacher-1"}
    with pytest.raises(
        DtsRecordError,
            match=(
                "^DTS_(NON_FINITE_NUMBER_NOT_ALLOWED|SOURCE_VALUE_TYPE_NOT_ALLOWED|SOURCE_FIELD_TYPE_INVALID)$"
            ),
    ):
        build_v2_source_route(
            _event(
                table="ovs_appoint",
                operation="UPDATE",
                before=current,
                after={"id": 1, "t_id": unsafe_value},
            ),
            current=_ovs_appoint_current(current),
        )


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE", "DELETE"])
def test_retired_dom_route_ignores_malformed_or_missing_images(
    operation: str,
) -> None:
    decision = build_v2_source_route(
        _event(
            table="dom_qa_ac_classroom_record",
            region="dom",
            operation=operation,
            before=None,
            after={"info": '{"student_id":123'} if operation != "DELETE" else None,
        )
    )

    assert decision.route_status == "IGNORED_RETIRED_SOURCE"
    assert decision.before_row is None
    assert decision.after_row is None
