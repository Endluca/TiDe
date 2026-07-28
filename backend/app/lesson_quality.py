"""Deterministic course-level classroom-quality facts."""

from __future__ import annotations


def is_perfect_lesson(
    *,
    lesson_lifecycle_status: str | None,
    absence_reason_detail: str | None,
    is_late: bool | None,
    is_early: bool | None,
) -> bool:
    """Return whether a lesson meets the confirmed course-level perfect rule."""

    return (
        str(lesson_lifecycle_status or "").strip().casefold() == "end"
        and not str(absence_reason_detail or "").strip()
        and is_late is False
        and is_early is False
    )
