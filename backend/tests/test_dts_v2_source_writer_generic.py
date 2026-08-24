from __future__ import annotations

from app.dts_source_contract_v2 import V2SourceRouteDecision
from app.dts_v2_shadow_source_writer import (
    _canonical_source_key,
    _source_dependency_keys,
)


def _route(
    table: str,
    row: dict[str, object],
    field_types: dict[str, str],
) -> V2SourceRouteDecision:
    return V2SourceRouteDecision(
        route_status="VERSIONED",
        source_table=table,
        operation="INSERT",
        source_key="9001",
        source_key_type="NUMERIC",
        source_schema_profile_id="profile:test",
        source_field_types={"id": "NUMERIC", **field_types},
        after_row={"id": 9001, **row},
        protected_payload_hash="0" * 64,
    )


def test_generic_source_dependencies_route_course_teacher_label_and_category() -> None:
    grading = _source_dependency_keys(
        _route(
            "dom_user_teacher_grading",
            {"appoint_id": 101, "teacher_id": 202},
            {"appoint_id": "NUMERIC", "teacher_id": "NUMERIC"},
        ),
        "dom",
    )
    assert grading == {
        "course_ids": ["101"],
        "teacher_ids": ["202"],
        "student_subjects": [],
        "label_ids": [],
        "category_ids": [],
    }

    label = _source_dependency_keys(
        _route(
            "ovs_grading_label_log",
            {"appoint_id": 101, "label_id": 303},
            {"appoint_id": "NUMERIC", "label_id": "NUMERIC"},
        ),
        "ovs",
    )
    assert label["course_ids"] == ["101"]
    assert label["label_ids"] == ["303"]

    complaint = _source_dependency_keys(
        _route(
            "dom_complaint",
            {
                "appoint_id": 101,
                "tea_id": 202,
                "complaint_type_grandson": 404,
            },
            {
                "appoint_id": "NUMERIC",
                "tea_id": "NUMERIC",
                "complaint_type_grandson": "NUMERIC",
            },
        ),
        "dom",
    )
    assert complaint["course_ids"] == ["101"]
    assert complaint["teacher_ids"] == ["202"]
    assert complaint["category_ids"] == ["404"]


def test_dom_protected_relationship_dependency_never_needs_raw_student_id() -> None:
    token = "dom:v1:" + "a" * 64
    dependencies = _source_dependency_keys(
        _route(
            "dom_teacher_favorite",
            {"tea_id": 202, "student_token": token},
            {"tea_id": "NUMERIC", "student_token": "TEXT"},
        ),
        "dom",
    )
    assert dependencies["teacher_ids"] == ["202"]
    assert dependencies["student_subjects"] == [token]


def test_user_complaint_is_audit_only_and_creates_no_business_dependencies() -> None:
    dependencies = _source_dependency_keys(
        _route(
            "ovs_user_complaint",
            {"appoint_id": 101, "teacher_id": 202, "user_id": 303},
            {
                "appoint_id": "NUMERIC",
                "teacher_id": "NUMERIC",
                "user_id": "NUMERIC",
            },
        ),
        "ovs",
    )
    assert dependencies == {
        "course_ids": [],
        "teacher_ids": [],
        "student_subjects": [],
        "label_ids": [],
        "category_ids": [],
    }


def test_source_primary_key_canonicalization_supports_numeric_and_text_profiles() -> None:
    assert _canonical_source_key("009.00", "NUMERIC") == "9"
    assert _canonical_source_key("009", "TEXT") == "009"
