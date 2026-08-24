from __future__ import annotations

from app.dts_source_contract_v2 import V2SourceRouteDecision
from app.dts_v2_shadow_source_writer import _dirty_keys_for_route


def _route(
    table: str,
    *,
    source_key: str = "1",
    source_key_type: str = "NUMERIC",
    field_types: dict[str, str],
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> V2SourceRouteDecision:
    return V2SourceRouteDecision(
        route_status="VERSIONED",
        source_table=table,
        operation=(
            "INSERT" if before is None else "DELETE" if after is None else "UPDATE"
        ),
        source_key=source_key,
        source_key_type=source_key_type,
        source_key_data_json="{}",
        source_schema_profile_id="synthetic-profile",
        source_field_types=field_types,
        before_row=before,
        after_row=after,
        protected_payload_hash="a" * 64,
    )


def _identities(route: V2SourceRouteDecision, region: str) -> set[tuple[str, ...]]:
    return {
        (key.source_region, key.key_type, key.key_part_1, key.key_part_2)
        for key in _dirty_keys_for_route(route, region)
    }


def test_appoint_fanout_uses_union_and_preserves_typed_text_ids() -> None:
    route = _route(
        "ovs_appoint",
        source_key="9001",
        field_types={"id": "NUMERIC", "t_id": "TEXT"},
        before={"id": 9001, "t_id": " old teacher "},
        after={"id": 9001, "t_id": " new teacher "},
    )

    assert _identities(route, "ovs") == {
        ("ovs", "COURSE", "9001", ""),
        ("ovs", "TEACHER", " old teacher ", ""),
        ("ovs", "TEACHER", " new teacher ", ""),
    }


def test_appoint_fanout_rebuilds_each_real_teacher_student_pair() -> None:
    old_token = "dom:v1:" + "a" * 64
    new_token = "dom:v1:" + "b" * 64
    route = _route(
        "dom_appoint",
        source_key="9001",
        field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
        before={"id": 9001, "t_id": 10, "student_token": old_token},
        after={"id": 9001, "t_id": 20, "student_token": new_token},
    )

    assert _identities(route, "dom") == {
        ("dom", "COURSE", "9001", ""),
        ("dom", "TEACHER", "10", ""),
        ("dom", "TEACHER", "20", ""),
        ("dom", "TEACHER_STUDENT", "10", old_token),
        ("dom", "TEACHER_STUDENT", "20", new_token),
    }


def test_relationship_update_emits_two_real_pairs_not_cross_product() -> None:
    old_token = "dom:v1:" + "a" * 64
    new_token = "dom:v1:" + "b" * 64
    route = _route(
        "dom_teacher_blacklist",
        field_types={
            "id": "NUMERIC",
            "teacher_id": "NUMERIC",
            "student_token": "TEXT",
        },
        before={"id": 1, "teacher_id": 10, "student_token": old_token},
        after={"id": 1, "teacher_id": 20, "student_token": new_token},
    )

    assert _identities(route, "dom") == {
        ("dom", "TEACHER", "10", ""),
        ("dom", "TEACHER", "20", ""),
        ("dom", "TEACHER_STUDENT", "10", old_token),
        ("dom", "TEACHER_STUDENT", "20", new_token),
    }


def test_relationship_delete_refreshes_teacher_without_course_dependency() -> None:
    token = "dom:v1:" + "c" * 64
    route = _route(
        "dom_teacher_favorite",
        field_types={
            "id": "NUMERIC",
            "tea_id": "NUMERIC",
            "student_token": "TEXT",
        },
        before={"id": 1, "tea_id": 10, "student_token": token},
        after=None,
    )

    # No appoint/completion key is required for the relationship itself to
    # refresh both pair current and the teacher-wide distinct count.
    assert _identities(route, "dom") == {
        ("dom", "TEACHER", "10", ""),
        ("dom", "TEACHER_STUDENT", "10", token),
    }


def test_label_log_and_dictionary_have_exact_course_label_fanout() -> None:
    log = _route(
        "ovs_grading_label_log",
        field_types={
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "label_id": "NUMERIC",
        },
        before={"id": 1, "appoint_id": 100, "label_id": 7},
        after={"id": 1, "appoint_id": 200, "label_id": 8},
    )
    dictionary = _route(
        "ovs_grading_label",
        source_key="8",
        field_types={"id": "NUMERIC"},
        before={"id": 8},
        after={"id": 8},
    )

    assert _identities(log, "ovs") == {
        ("ovs", "COURSE", "100", ""),
        ("ovs", "COURSE", "200", ""),
        ("ovs", "LABEL", "7", ""),
        ("ovs", "LABEL", "8", ""),
    }
    assert _identities(dictionary, "ovs") == {
        ("ovs", "LABEL", "8", ""),
    }


def test_ovs_complaint_uses_ovs_course_teacher_and_dom_categories() -> None:
    route = _route(
        "ovs_complaint",
        field_types={
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "teacher_id": "NUMERIC",
            "complaint_type": "NUMERIC",
            "complaint_type_child": "NUMERIC",
            "complaint_type_grandson": "NUMERIC",
        },
        before={
            "id": 1,
            "appoint_id": 100,
            "teacher_id": 10,
            "complaint_type": 1,
            "complaint_type_child": 2,
            "complaint_type_grandson": 3,
        },
        after={
            "id": 1,
            "appoint_id": 100,
            "teacher_id": 11,
            "complaint_type": 1,
            "complaint_type_child": 4,
            "complaint_type_grandson": None,
        },
    )

    assert _identities(route, "ovs") == {
        ("ovs", "COURSE", "100", ""),
        ("ovs", "TEACHER", "10", ""),
        ("ovs", "TEACHER", "11", ""),
        ("dom", "COMPLAINT_CATEGORY", "1", ""),
        ("dom", "COMPLAINT_CATEGORY", "2", ""),
        ("dom", "COMPLAINT_CATEGORY", "3", ""),
        ("dom", "COMPLAINT_CATEGORY", "4", ""),
    }


def test_teacher_schedule_cert_and_course_child_matrix() -> None:
    teacher = _route(
        "dom_teacher",
        source_key="9",
        field_types={"id": "NUMERIC"},
        before={"id": 9},
        after={"id": 9},
    )
    schedule = _route(
        "dom_teacher_class_schedule",
        field_types={"id": "NUMERIC", "teacher_id": "NUMERIC"},
        before={"id": 1, "teacher_id": 9},
        after={"id": 1, "teacher_id": 10},
    )
    grading = _route(
        "dom_user_teacher_grading",
        field_types={
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "teacher_id": "NUMERIC",
        },
        before={"id": 1, "appoint_id": 90, "teacher_id": 9},
        after={"id": 1, "appoint_id": 91, "teacher_id": 10},
    )

    assert _identities(teacher, "dom") == {
        ("dom", "TEACHER", "9", ""),
        ("ovs", "TEACHER", "9", ""),
    }
    assert _identities(schedule, "dom") == {
        ("dom", "TEACHER", "9", ""),
        ("dom", "TEACHER", "10", ""),
    }
    assert _identities(grading, "dom") == {
        ("dom", "COURSE", "90", ""),
        ("dom", "COURSE", "91", ""),
        ("dom", "TEACHER", "9", ""),
        ("dom", "TEACHER", "10", ""),
    }


def test_user_complaint_is_versioned_but_has_no_business_dirty_key() -> None:
    route = _route(
        "dom_user_complaint",
        field_types={
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "teacher_id": "NUMERIC",
        },
        before={"id": 1, "appoint_id": 90, "teacher_id": 9},
        after={"id": 1, "appoint_id": 91, "teacher_id": 10},
    )

    assert _dirty_keys_for_route(route, "dom") == ()
