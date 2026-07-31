from __future__ import annotations

import pytest

from app.lesson_quality import hardware_quality_passed, is_perfect_lesson


@pytest.mark.parametrize(
    (
        "lesson_lifecycle_status",
        "is_late",
        "is_early",
        "expected",
    ),
    [
        ("end", False, False, True),
        (" END ", False, False, True),
        ("end", True, False, False),
        ("end", False, True, False),
        ("end", None, False, False),
        ("s_absent", False, False, False),
        ("t_absent", False, False, False),
        ("completed", False, False, False),
    ],
)
def test_is_perfect_lesson_rule(
    lesson_lifecycle_status: str | None,
    is_late: bool | None,
    is_early: bool | None,
    expected: bool,
) -> None:
    assert (
        is_perfect_lesson(
            lesson_lifecycle_status=lesson_lifecycle_status,
            is_late=is_late,
            is_early=is_early,
        )
        is expected
    )


@pytest.mark.parametrize(
    (
        "is_camera_off",
        "is_cpu_usage_high",
        "is_network_delay_high",
        "expected",
    ),
    [
        (False, False, False, True),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (True, True, True, False),
        (None, False, False, None),
        (False, None, False, None),
        (False, False, None, None),
    ],
)
def test_hardware_quality_requires_three_explicit_normal_facts(
    is_camera_off: bool | None,
    is_cpu_usage_high: bool | None,
    is_network_delay_high: bool | None,
    expected: bool | None,
) -> None:
    assert (
        hardware_quality_passed(
            is_camera_off=is_camera_off,
            is_cpu_usage_high=is_cpu_usage_high,
            is_network_delay_high=is_network_delay_high,
        )
        is expected
    )
