from __future__ import annotations

import pytest

from app.dts_complaint_routing_v2 import (
    ComplaintCategoryRuleV2,
    ComplaintCategorySnapshotV2,
    ComplaintRouteDisposition,
    ComplaintRoutingInputV2,
    route_current_complaint_v2,
)


SHA = "a" * 64
DEFAULT_L3 = "教学态度"


def _complaint(
    *,
    child_id: str | None = "23",
    grandson_id: str | None = "83",
    teacher_id: str | None = "T1",
    approve: str = "y",
) -> ComplaintRoutingInputV2:
    return ComplaintRoutingInputV2(
        source_region="dom",
        source_appoint_id="009",
        source_appoint_id_type="NUMERIC",
        complaint_row={
            "complaint_type": 13,
            "complaint_type_child": child_id,
            "complaint_type_grandson": grandson_id,
            "approve": approve,
            "validity": 1,
        },
        child_id=child_id,
        child_id_type="NUMERIC" if child_id is not None else None,
        grandson_id=grandson_id,
        grandson_id_type="NUMERIC" if grandson_id is not None else None,
        completion_teacher_id=teacher_id,
        completion_teacher_id_type="TEXT" if teacher_id is not None else None,
        completion_participation_seq=3 if teacher_id is not None else None,
    )


def _category(
    category_id: str,
    name: str | None,
    *,
    id_type: str = "NUMERIC",
    deleted: bool = False,
) -> ComplaintCategorySnapshotV2:
    return ComplaintCategorySnapshotV2(
        source_region="dom",
        category_id=category_id,
        category_id_type=id_type,  # type: ignore[arg-type]
        cate_cn_name=name,
        is_deleted=deleted,
    )


def _rule(
    severity: int,
    *,
    category_l3_normalized: str = DEFAULT_L3,
    row_number: int = 1,
) -> ComplaintCategoryRuleV2:
    return ComplaintCategoryRuleV2(
        category_l3_normalized=category_l3_normalized,
        severity_rank=severity,
        complaint_rule_id=f"complaint-rule:{SHA}:{row_number}",
        source_sha256=SHA,
    )


def _route(
    complaint: ComplaintRoutingInputV2,
    *,
    child_name: str | None = "其他",
    grandson_name: str = DEFAULT_L3,
    severity: int = 2,
):
    child_category = (
        _category(complaint.child_id, child_name)
        if complaint.child_id is not None
        else None
    )
    grandson_category = (
        _category(complaint.grandson_id, grandson_name)
        if complaint.grandson_id is not None
        else None
    )
    return route_current_complaint_v2(
        complaint,
        category_rule=(
            _rule(severity, category_l3_normalized=grandson_name)
            if complaint.grandson_id is not None
            else None
        ),
        child_category=child_category,
        grandson_category=grandson_category,
    )


@pytest.mark.parametrize(
    (
        "child_name",
        "severity",
        "rule_code",
        "output_type",
        "task_code",
        "output_key",
    ),
    [
        (
            "出席问题",
            4,
            "TR-REL-ATTENDANCE",
            "TEACHER_TASK",
            "P-REL-ATTENDANCE",
            "personalized:P-REL-ATTENDANCE:T1",
        ),
        (
            "网络设备问题",
            0,
            "TR-QUALITY-NETWORK-EQUIPMENT",
            "NOTIFICATION",
            None,
            "complaint-notification:TR-QUALITY-NETWORK-EQUIPMENT:dom:9:3:83",
        ),
        (
            "其他",
            1,
            "TR-FB-SEVERE-COMPLAINT",
            "OPS_CASE",
            None,
            "complaint-case:TR-FB-SEVERE-COMPLAINT:dom:9:3:83",
        ),
        (
            "其他",
            2,
            "TR-FB-GENERAL-COMPLAINT",
            "TEACHER_TASK",
            "P-FB-COMPLAINT",
            "personalized:P-FB-COMPLAINT:T1:83",
        ),
        (
            None,
            4,
            "TR-FB-GENERAL-COMPLAINT",
            "TEACHER_TASK",
            "P-FB-COMPLAINT",
            "personalized:P-FB-COMPLAINT:T1:83",
        ),
    ],
)
def test_complaint_routes_from_child_dictionary_then_l3_rule(
    child_name: str | None,
    severity: int,
    rule_code: str,
    output_type: str,
    task_code: str | None,
    output_key: str,
) -> None:
    complaint = _complaint(child_id=None if child_name is None else "23")
    result = _route(
        complaint,
        child_name=child_name,
        severity=severity,
    )

    assert result.disposition == ComplaintRouteDisposition.ROUTED
    assert result.rule_code == rule_code
    assert result.output_type == output_type
    assert result.task_code == task_code
    assert result.match_key == f"complaint:{rule_code}:dom:9:3:83"
    assert result.output_key == output_key
    assert result.complaint_rule_id == f"complaint-rule:{SHA}:1"
    assert result.source_sha256 == SHA
    assert result.category_l2 == child_name
    assert result.category_l3_normalized == DEFAULT_L3


def test_l3_dictionary_name_uses_nfkc_strip_and_collapsed_ascii_space() -> None:
    result = route_current_complaint_v2(
        _complaint(child_id=None),
        category_rule=_rule(2, category_l3_normalized="A 类"),
        grandson_category=_category("83", "  Ａ\u3000\t类  "),
    )

    assert result.disposition == ComplaintRouteDisposition.ROUTED
    assert result.category_l3_normalized == "A 类"


def test_null_child_never_guesses_attendance_or_network_from_the_rule() -> None:
    result = route_current_complaint_v2(
        _complaint(child_id=None),
        category_rule=_rule(4),
        grandson_category=_category("83", DEFAULT_L3),
    )

    assert result.rule_code == "TR-FB-GENERAL-COMPLAINT"
    assert result.category_l2 is None


def test_null_grandson_is_valid_for_metric_but_pending_for_output() -> None:
    complaint = _complaint(child_id=None, grandson_id=None, teacher_id=None)
    result = route_current_complaint_v2(complaint, category_rule=None)

    assert result.disposition == ComplaintRouteDisposition.PENDING_CATEGORY
    assert result.error_code == "PENDING_DATA:COMPLAINT_CATEGORY_MISSING"
    assert result.match_key is None
    assert result.output_key is None


@pytest.mark.parametrize("missing_level", ["child", "grandson"])
@pytest.mark.parametrize("deleted", [False, True])
def test_non_null_category_id_requires_a_live_dom_dictionary_snapshot(
    missing_level: str,
    deleted: bool,
) -> None:
    complaint = _complaint()
    child = _category("23", "其他")
    grandson = _category("83", DEFAULT_L3)
    if missing_level == "child":
        child = _category("23", "其他", deleted=True) if deleted else None
    else:
        grandson = (
            _category("83", DEFAULT_L3, deleted=True) if deleted else None
        )

    result = route_current_complaint_v2(
        complaint,
        category_rule=_rule(2),
        child_category=child,
        grandson_category=grandson,
    )

    assert result.disposition == (
        ComplaintRouteDisposition.SOURCE_MISSING_CATEGORY
    )
    assert result.error_code == "SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND"
    assert result.output_key is None


def test_missing_rule_is_not_hidden_by_missing_completion() -> None:
    result = route_current_complaint_v2(
        _complaint(teacher_id=None),
        category_rule=None,
        child_category=_category("23", "其他"),
        grandson_category=_category("83", DEFAULT_L3),
    )

    assert result.disposition == ComplaintRouteDisposition.PENDING_CATEGORY
    assert result.error_code == "PENDING_DATA:COMPLAINT_CATEGORY_RULE_MISSING"


def test_valid_category_waits_for_frozen_completion_without_guessing() -> None:
    result = route_current_complaint_v2(
        _complaint(teacher_id=None),
        category_rule=_rule(2),
        child_category=_category("23", "其他"),
        grandson_category=_category("83", DEFAULT_L3),
    )

    assert result.disposition == ComplaintRouteDisposition.WAITING_COMPLETION
    assert result.error_code == "WAITING_DEPENDENCY:COURSE_COMPLETION_REQUIRED"


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"complaint_type": 12},
        {"complaint_type_grandson": 82},
        {"complaint_type_grandson": "82"},
        {"approve": "n"},
        {"approve": "Y"},
        {"validity": 0},
    ],
)
def test_each_invalid_complaint_condition_blocks_routing(
    invalid_fields: dict[str, object],
) -> None:
    original = _complaint()
    row = dict(original.complaint_row)
    row.update(invalid_fields)
    child = row["complaint_type_child"]
    grandson = row["complaint_type_grandson"]
    complaint = ComplaintRoutingInputV2(
        source_region=original.source_region,
        source_appoint_id=original.source_appoint_id,
        source_appoint_id_type=original.source_appoint_id_type,
        complaint_row=row,
        child_id=str(child) if child is not None else None,
        child_id_type="NUMERIC" if child is not None else None,
        grandson_id=str(grandson) if grandson is not None else None,
        grandson_id_type="NUMERIC" if grandson is not None else None,
        completion_teacher_id=original.completion_teacher_id,
        completion_teacher_id_type=original.completion_teacher_id_type,
        completion_participation_seq=original.completion_participation_seq,
    )

    result = route_current_complaint_v2(complaint, category_rule=None)

    assert result.disposition == ComplaintRouteDisposition.NOT_VALID
    assert result.output_key is None


def test_malformed_numeric_category_id_fails_closed_at_typed_boundary() -> None:
    with pytest.raises(ValueError, match="numeric id is invalid"):
        ComplaintRoutingInputV2(
            source_region="dom",
            source_appoint_id="9",
            source_appoint_id_type="NUMERIC",
            complaint_row={
                "complaint_type": 13,
                "complaint_type_child": None,
                "complaint_type_grandson": "not-an-id",
                "approve": "y",
                "validity": 1,
            },
            child_id=None,
            child_id_type=None,
            grandson_id="not-an-id",
            grandson_id_type="NUMERIC",
            completion_teacher_id="T1",
            completion_teacher_id_type="TEXT",
            completion_participation_seq=1,
        )


def test_rule_must_match_the_normalized_grandson_dictionary_name() -> None:
    with pytest.raises(ValueError, match="normalized level-3"):
        route_current_complaint_v2(
            _complaint(child_id=None),
            category_rule=_rule(2, category_l3_normalized="其他分类"),
            grandson_category=_category("83", DEFAULT_L3),
        )


def test_dictionary_snapshot_must_match_the_selected_typed_id() -> None:
    with pytest.raises(ValueError, match="dictionary snapshot"):
        route_current_complaint_v2(
            _complaint(),
            category_rule=_rule(2),
            child_category=_category("24", "其他"),
            grandson_category=_category("83", DEFAULT_L3),
        )


def test_typed_category_id_must_match_the_source_business_row() -> None:
    with pytest.raises(ValueError, match="category identity"):
        ComplaintRoutingInputV2(
            source_region="dom",
            source_appoint_id="9",
            source_appoint_id_type="NUMERIC",
            complaint_row={
                "complaint_type": 13,
                "complaint_type_child": None,
                "complaint_type_grandson": 84,
                "approve": "y",
                "validity": 1,
            },
            child_id=None,
            child_id_type=None,
            grandson_id="83",
            grandson_id_type="NUMERIC",
            completion_teacher_id="T1",
            completion_teacher_id_type="TEXT",
            completion_participation_seq=1,
        )


def test_numeric_and_text_dictionary_identities_do_not_collide() -> None:
    with pytest.raises(ValueError, match="dictionary snapshot"):
        route_current_complaint_v2(
            ComplaintRoutingInputV2(
                source_region="dom",
                source_appoint_id="9",
                source_appoint_id_type="NUMERIC",
                complaint_row={
                    "complaint_type": 13,
                    "complaint_type_child": None,
                    "complaint_type_grandson": "83",
                    "approve": "y",
                    "validity": 1,
                },
                child_id=None,
                child_id_type=None,
                grandson_id="83",
                grandson_id_type="TEXT",
                completion_teacher_id="T1",
                completion_teacher_id_type="TEXT",
                completion_participation_seq=1,
            ),
            category_rule=_rule(2),
            grandson_category=_category("83", DEFAULT_L3),
        )


@pytest.mark.parametrize(
    ("complaint_rule_id", "source_sha256"),
    [
        (f"complaint-rule:{'A' * 64}:1", "A" * 64),
        (f"complaint-rule:{'b' * 64}:1", SHA),
        (f"complaint-rule:{SHA}:0", SHA),
        (f"complaint-rule:{SHA}:01", SHA),
        ("rule-1", SHA),
    ],
)
def test_complaint_rule_id_and_sha_must_be_an_exact_version_pair(
    complaint_rule_id: str,
    source_sha256: str,
) -> None:
    with pytest.raises(ValueError, match="rule identity"):
        ComplaintCategoryRuleV2(
            category_l3_normalized=DEFAULT_L3,
            severity_rank=2,
            complaint_rule_id=complaint_rule_id,
            source_sha256=source_sha256,
        )


@pytest.mark.parametrize("invalid_severity", [True, -1, 5, 2.0, "2"])
def test_invalid_severity_fails_closed(invalid_severity: object) -> None:
    with pytest.raises(ValueError, match="severity"):
        ComplaintCategoryRuleV2(
            category_l3_normalized=DEFAULT_L3,
            severity_rank=invalid_severity,  # type: ignore[arg-type]
            complaint_rule_id=f"complaint-rule:{SHA}:1",
            source_sha256=SHA,
        )


@pytest.mark.parametrize("invalid_name", ["", " 教学态度", "Ａ 类", "A\t类"])
def test_rule_requires_an_already_normalized_l3_name(invalid_name: str) -> None:
    with pytest.raises(ValueError, match="category_l3_normalized"):
        ComplaintCategoryRuleV2(
            category_l3_normalized=invalid_name,
            severity_rank=2,
            complaint_rule_id=f"complaint-rule:{SHA}:1",
            source_sha256=SHA,
        )


@pytest.mark.parametrize("invalid_seq", [True, 0, -1, 1.0, "1"])
def test_invalid_completion_sequence_fails_closed(invalid_seq: object) -> None:
    with pytest.raises(ValueError, match="completion pointer"):
        ComplaintRoutingInputV2(
            source_region="ovs",
            source_appoint_id="A-1",
            source_appoint_id_type="TEXT",
            complaint_row={
                "complaint_type": 13,
                "complaint_type_child": None,
                "complaint_type_grandson": 83,
                "approve": "y",
                "validity": 1,
            },
            child_id=None,
            child_id_type=None,
            grandson_id="83",
            grandson_id_type="NUMERIC",
            completion_teacher_id="T1",
            completion_teacher_id_type="TEXT",
            completion_participation_seq=invalid_seq,  # type: ignore[arg-type]
        )


def test_category_dictionary_snapshot_must_always_be_dom() -> None:
    with pytest.raises(ValueError, match="must be DOM"):
        ComplaintCategorySnapshotV2(
            source_region="ovs",  # type: ignore[arg-type]
            category_id="83",
            category_id_type="NUMERIC",
            cate_cn_name=DEFAULT_L3,
        )


def test_ovs_complaint_uses_dom_dictionary_and_canonical_typed_keys() -> None:
    complaint = ComplaintRoutingInputV2(
        source_region="ovs",
        source_appoint_id="009.00",
        source_appoint_id_type="NUMERIC",
        complaint_row={
            "complaint_type": 13,
            "complaint_type_child": 23,
            "complaint_type_grandson": 83,
            "approve": "y",
            "validity": 1,
        },
        child_id="023.0",
        child_id_type="NUMERIC",
        grandson_id="083.0",
        grandson_id_type="NUMERIC",
        completion_teacher_id="0007",
        completion_teacher_id_type="NUMERIC",
        completion_participation_seq=2,
    )

    result = route_current_complaint_v2(
        complaint,
        category_rule=_rule(4),
        child_category=_category("23", "网络设备问题"),
        grandson_category=_category("83", DEFAULT_L3),
    )

    assert result.match_key == (
        "complaint:TR-QUALITY-NETWORK-EQUIPMENT:ovs:9:2:83"
    )
    assert result.output_key == (
        "complaint-notification:TR-QUALITY-NETWORK-EQUIPMENT:ovs:9:2:83"
    )
