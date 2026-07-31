"""Deterministic course-level classroom-quality facts."""

from __future__ import annotations


def is_perfect_lesson(
    *,
    lesson_lifecycle_status: str | None,
    is_late: bool | None,
    is_early: bool | None,
) -> bool:
    """Return whether a lesson meets the confirmed course-level perfect rule."""

    return (
        str(lesson_lifecycle_status or "").strip().casefold() == "end"
        and is_late is False
        and is_early is False
    )


def hardware_quality_passed(
    *,
    is_camera_off: bool | None,
    is_cpu_usage_high: bool | None,
    is_network_delay_high: bool | None,
) -> bool | None:
    """Return the hardware-quality result, preserving incomplete evidence."""

    values = (
        is_camera_off,
        is_cpu_usage_high,
        is_network_delay_high,
    )
    if any(value is None for value in values):
        return None
    return all(value is False for value in values)
