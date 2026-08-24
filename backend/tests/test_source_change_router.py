from __future__ import annotations

import pytest

from app.source_change_router import (
    SourceChangeRoute,
    SourceChangeRoutingError,
    route_source_change,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_table": "teacher_source_wide",
        "source_id": "T-001",
        "operation": "UPDATE",
        "changed_fields": ["perfect_cnt"],
        "old_teacher_id": "T-001",
        "new_teacher_id": "T-001",
    }
    payload.update(overrides)
    return payload


def test_teacher_fields_route_to_deduplicated_stably_sorted_work() -> None:
    route = route_source_change(
        _payload(
            changed_fields=[
                "feedback_praise_cnt",
                "perfect_cnt",
                "feedback_praise_cnt",
            ]
        )
    )

    assert route == SourceChangeRoute(
        source_table="teacher_source_wide",
        source_region=None,
        source_id="T-001",
        operation="UPDATE",
        changed_fields=("feedback_praise_cnt", "perfect_cnt"),
        handlers=(
            "TEACHER_QUALIFICATION",
            "TEACHER_TOTAL",
            "TEACHER_USER_FEEDBACK",
        ),
        scopes=(
            "SINGLE_TEACHER_USER_FEEDBACK",
        ),
        affected_teacher_ids=("T-001",),
    )


def test_source_only_field_is_valid_and_emits_no_downstream_work() -> None:
    route = route_source_change(_payload(changed_fields=["center_type_id"]))

    assert route.handlers == ()
    assert route.scopes == ()
    assert route.affected_teacher_ids == ("T-001",)


def test_g01_status_fields_only_refresh_the_teacher_profile() -> None:
    route = route_source_change(
        _payload(changed_fields=["is_self_introduce", "is_cpl_tesol"])
    )

    assert route.handlers == ("TEACHER_PROFILE",)
    assert route.scopes == ("SINGLE_TEACHER",)
    assert route.affected_teacher_ids == ("T-001",)


def test_lesson_teacher_change_affects_both_old_and_new_teacher() -> None:
    route = route_source_change(
        _payload(
            source_table="lesson_source_wide",
            source_region="ovs",
            source_id="L-001",
            changed_fields=["老师id", "迟到"],
            old_teacher_id="T-OLD",
            new_teacher_id="T-NEW",
        )
    )

    assert route.handlers == (
        "BLACKLIST_TEACHER",
        "COMPLAINT_TEACHER",
        "FAVORITE_PAIR",
        "LESSON_SCORE",
        "LESSON_TRIGGER",
        "NEGATIVE_LABEL",
        "TEACHER_CLASS_QUALITY",
        "TEACHER_QUALIFICATION",
        "TEACHER_RELIABILITY",
        "TEACHER_TOTAL",
    )
    assert route.scopes == (
        "SINGLE_LESSON_AND_OLD_NEW_TEACHER_COMPONENTS",
        "SINGLE_LESSON_AND_TEACHER_RELIABILITY",
    )
    assert route.affected_teacher_ids == ("T-NEW", "T-OLD")


def test_retired_cpu_network_nullification_still_routes_score_reconciliation() -> None:
    route = route_source_change(
        _payload(
            source_table="lesson_source_wide",
            source_region="ovs",
            source_id="L-CPU-NETWORK-RETIRED",
            changed_fields=["cpu占用过高", "网络延迟过高"],
            old_teacher_id="T-001",
            new_teacher_id="T-001",
        )
    )

    assert route.handlers == (
        "LESSON_SCORE",
        "TEACHER_CLASS_QUALITY",
        "TEACHER_QUALIFICATION",
        "TEACHER_TOTAL",
    )
    assert route.scopes == ("SINGLE_LESSON_AND_TEACHER_CLASS_QUALITY",)
    assert route.affected_teacher_ids == ("T-001",)


def test_changed_field_order_does_not_change_the_route() -> None:
    first = route_source_change(
        _payload(changed_fields=["perfect_cnt", "feedback_praise_cnt"])
    )
    second = route_source_change(
        _payload(changed_fields=["feedback_praise_cnt", "perfect_cnt"])
    )

    assert first == second


@pytest.mark.parametrize("source_table", ["teachers", "unknown", ""])
def test_unknown_or_empty_source_table_is_rejected(source_table: str) -> None:
    with pytest.raises(SourceChangeRoutingError):
        route_source_change(_payload(source_table=source_table))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(SourceChangeRoutingError, match="unknown fields"):
        route_source_change(_payload(changed_fields=["not_in_contract"]))


@pytest.mark.parametrize("operation", ["UPSERT", "update", "", "CREATE"])
def test_invalid_operation_is_rejected(operation: str) -> None:
    with pytest.raises(SourceChangeRoutingError):
        route_source_change(_payload(operation=operation))


@pytest.mark.parametrize("source_id", [None, "", "   ", 123])
def test_missing_or_invalid_source_id_is_rejected(source_id: object) -> None:
    with pytest.raises(SourceChangeRoutingError, match="source_id"):
        route_source_change(_payload(source_id=source_id))


@pytest.mark.parametrize(
    ("operation", "old_teacher_id", "new_teacher_id", "missing_name"),
    [
        ("INSERT", None, None, "new_teacher_id"),
        ("DELETE", None, None, "old_teacher_id"),
        ("UPDATE", None, "T-001", "old_teacher_id"),
        ("UPDATE", "T-001", None, "new_teacher_id"),
    ],
)
def test_operation_requires_the_relevant_teacher_identifiers(
    operation: str,
    old_teacher_id: object,
    new_teacher_id: object,
    missing_name: str,
) -> None:
    with pytest.raises(SourceChangeRoutingError, match=missing_name):
        route_source_change(
            _payload(
                operation=operation,
                old_teacher_id=old_teacher_id,
                new_teacher_id=new_teacher_id,
            )
        )


@pytest.mark.parametrize("changed_fields", [None, [], "perfect_cnt", [""]])
def test_changed_fields_must_be_a_non_empty_field_sequence(
    changed_fields: object,
) -> None:
    with pytest.raises(SourceChangeRoutingError, match="changed_fields"):
        route_source_change(_payload(changed_fields=changed_fields))


def test_insert_and_delete_select_the_operation_specific_teacher() -> None:
    inserted = route_source_change(
        _payload(
            operation="INSERT",
            changed_fields=["tchr_id", "perfect_cnt"],
            old_teacher_id=None,
            new_teacher_id="T-NEW",
        )
    )
    deleted = route_source_change(
        _payload(
            operation="DELETE",
            changed_fields=["tchr_id", "perfect_cnt"],
            old_teacher_id="T-OLD",
            new_teacher_id=None,
        )
    )

    assert inserted.affected_teacher_ids == ("T-NEW",)
    assert deleted.affected_teacher_ids == ("T-OLD",)
    assert "FIXED_TASK_BASELINE" in inserted.handlers
    assert "FIXED_TASK_BASELINE" not in deleted.handlers
    assert "TEACHER_IDENTITY" not in deleted.handlers
    assert "TEACHER_SOURCE_REMOVAL" in deleted.handlers
    assert "SINGLE_TEACHER_SOURCE_REMOVAL" in deleted.scopes
