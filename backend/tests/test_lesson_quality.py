from __future__ import annotations

import pytest

from app.lesson_quality import is_perfect_lesson


@pytest.mark.parametrize(
    (
        "lesson_lifecycle_status",
        "absence_reason_detail",
        "is_late",
        "is_early",
        "expected",
    ),
    [
        ("end", None, False, False, True),
        (" END ", "   ", False, False, True),
        ("end", "In-class Absence", False, False, False),
        ("end", "Unfilled Lesson Memo", False, False, False),
        ("end", None, True, False, False),
        ("end", None, False, True, False),
        ("end", None, None, False, False),
        ("s_absent", None, False, False, False),
        ("t_absent", None, False, False, False),
        ("completed", None, False, False, False),
    ],
)
def test_is_perfect_lesson_rule(
    lesson_lifecycle_status: str | None,
    absence_reason_detail: str | None,
    is_late: bool | None,
    is_early: bool | None,
    expected: bool,
) -> None:
    assert (
        is_perfect_lesson(
            lesson_lifecycle_status=lesson_lifecycle_status,
            absence_reason_detail=absence_reason_detail,
            is_late=is_late,
            is_early=is_early,
        )
        is expected
    )
