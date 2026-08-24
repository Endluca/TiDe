from __future__ import annotations

from datetime import date

import pytest

from app.dts_business_rules_v2 import (
    absence_task_code,
    center_type_description,
    certification_is_tesol,
    classify_dom_grading,
    classify_teacher_online_state,
    complaint_is_valid,
    no_notice_state,
    tesol_state,
)


@pytest.mark.parametrize(
    ("use_point", "score", "grading_type", "expected"),
    [
        ("buy", 5, None, "POSITIVE"),
        ("buy", "4", None, "POSITIVE"),
        ("buy", 2, None, "NEGATIVE"),
        ("buy", 1, None, "NEGATIVE"),
        ("buy", 3, None, None),
        ("free", None, "satisfactory", "POSITIVE"),
        ("free", None, "unsatisfactory", "NEGATIVE"),
        (" Buy ", "5", "unsatisfactory", "POSITIVE"),
        ("FREE", 1, " SATISFACTORY ", "POSITIVE"),
        ("free", 5, " UNSATISFACTORY ", "NEGATIVE"),
        ("free", None, "unknown", None),
        (None, 5, "satisfactory", None),
        ("other", 1, "unsatisfactory", None),
    ],
)
def test_dom_grading_classification(
    use_point: object,
    score: object,
    grading_type: object,
    expected: str | None,
) -> None:
    assert classify_dom_grading(
        use_point=use_point,
        score=score,
        grading_type=grading_type,
    ) == expected


@pytest.mark.parametrize("grandson", [None, 0, 81, 83])
def test_complaint_accepts_null_or_non_82_grandson(grandson: object) -> None:
    assert complaint_is_valid(
        {
            "complaint_type": 13,
            "complaint_type_grandson": grandson,
            "approve": "y",
            "validity": 1,
        }
    ) is True


@pytest.mark.parametrize(
    "override",
    [
        {"complaint_type": 12},
        {"complaint_type_grandson": 82},
        {"complaint_type_grandson": "invalid"},
        {"approve": "n"},
        {"validity": 0},
    ],
)
def test_complaint_rejects_any_failed_condition(override: dict[str, object]) -> None:
    row: dict[str, object] = {
        "complaint_type": 13,
        "complaint_type_grandson": None,
        "approve": "y",
        "validity": 1,
    }
    row.update(override)
    assert complaint_is_valid(row) is False


def test_tesol_is_code_16_and_status_1_over_the_current_set() -> None:
    valid = {"certification_code": "16", "certification_status": 1}
    invalid = {"certification_code": "16", "certification_status": 0}

    assert certification_is_tesol(valid) is True
    assert certification_is_tesol(invalid) is False
    assert tesol_state([invalid, valid], scope_complete=False) is True
    assert tesol_state([invalid], scope_complete=True) is False
    assert tesol_state([invalid], scope_complete=False) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, "CBT"), ("1", "CBT"), (5, "TBT"), (None, "HBT"), (9, "HBT")],
)
def test_center_type_mapping(raw: object, expected: str) -> None:
    assert center_type_description(raw) == expected


def test_teacher_online_state_uses_beijing_business_day_0_to_29() -> None:
    as_of = date(2026, 8, 22)

    assert classify_teacher_online_state(
        status="on",
        status_on_time="2026-07-24 09:00:00",
        business_date=as_of,
    ).state == "NEW"
    assert classify_teacher_online_state(
        status="on", status_on_time="2026-07-23", business_date=as_of
    ).state == "EXISTING"
    assert classify_teacher_online_state(
        status="off", status_on_time=None, business_date=as_of
    ).state == "LEFT"
    assert classify_teacher_online_state(
        status="hei", status_on_time=None, business_date=as_of
    ).state == "BLOCKED"
    missing = classify_teacher_online_state(
        status="on", status_on_time=None, business_date=as_of
    )
    assert missing.state is None
    assert missing.evidence_status == "SOURCE_MISSING"

    unknown = classify_teacher_online_state(
        status="unknown", status_on_time="2026-07-24", business_date=as_of
    )
    assert unknown.state is None
    assert unknown.evidence_status == "SOURCE_MISSING"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(" ON ", "NEW"), ("Off", "LEFT"), (" HEI ", "BLOCKED")],
)
def test_teacher_online_state_normalizes_source_status(
    status: str,
    expected: str,
) -> None:
    result = classify_teacher_online_state(
        status=status,
        status_on_time="2026-08-22",
        business_date=date(2026, 8, 22),
    )

    assert result.state == expected
    assert result.evidence_status == "CONFIRMED"


@pytest.mark.parametrize(
    "status_on_time",
    [
        "2026-07-24garbage",
        " 2026-07-24",
        "2026-13-24",
        "not-a-date",
        20260724,
    ],
)
def test_teacher_online_state_rejects_non_authoritative_dates(
    status_on_time: object,
) -> None:
    result = classify_teacher_online_state(
        status="on",
        status_on_time=status_on_time,
        business_date=date(2026, 8, 22),
    )

    assert result.state is None
    assert result.evidence_status == "SOURCE_MISSING"


def test_teacher_online_state_rejects_future_onboard_date() -> None:
    result = classify_teacher_online_state(
        status="on",
        status_on_time="2026-08-23T00:00:00+08:00",
        business_date=date(2026, 8, 22),
    )

    assert result.state is None
    assert result.evidence_status == "SOURCE_MISSING"


def test_no_notice_uses_only_exact_reason_type() -> None:
    assert no_notice_state("No Notification") is True
    assert no_notice_state("Unfilled Lesson Memo") is False
    assert no_notice_state(None) is None
    assert no_notice_state(1) is None


@pytest.mark.parametrize(
    ("reason_type", "expected"),
    [
        ("Unfilled Lesson Memo", "P-REL-MEMO"),
        ("No Notification", "P-REL-ATTENDANCE"),
        ("Other confirmed reason", "P-REL-ATTENDANCE"),
        (" ", "P-REL-ATTENDANCE"),
        ("", None),
        (None, None),
        (1, None),
    ],
)
def test_absence_reason_maps_to_one_confirmed_task_rule(
    reason_type: object,
    expected: str | None,
) -> None:
    assert absence_task_code(reason_type) == expected
