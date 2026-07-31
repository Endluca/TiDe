from __future__ import annotations

from collections import Counter
from datetime import date, time
from types import SimpleNamespace

from app.simulated_cohort_seed import (
    DEFAULT_SEED,
    _lesson_score_rows,
    build_balanced_plan,
)
from app.task_catalog import MANDATORY_TASK_CODES


def test_balanced_simulated_cohort_plan_is_deterministic_and_stratified() -> None:
    first = build_balanced_plan(seed=DEFAULT_SEED)
    second = build_balanced_plan(seed=DEFAULT_SEED)

    assert first == second
    assert len(first) == 20
    assert len({item.teacher_id for item in first}) == 20
    assert min(item.camp_day for item in first) == 0
    assert max(item.camp_day for item in first) == 30
    assert min(item.lesson_count for item in first) == 0
    assert max(item.lesson_count for item in first) == 200
    assert min(item.completed_task_count for item in first) == 0
    assert max(item.completed_task_count for item in first) == 9

    lesson_bands = Counter(
        min(item.lesson_count // 40, 4) for item in first
    )
    assert lesson_bands == Counter({0: 4, 1: 4, 2: 4, 3: 4, 4: 4})
    assert Counter(item.employment_status for item in first) == Counter(
        {"on": 14, "off": 4, "hei": 2}
    )
    for item in first:
        assert 0 <= item.camp_day <= 30
        assert 0 <= item.lesson_count <= 200
        assert 0 <= item.completed_task_count <= 9
        assert sum(
            status == "COMPLETED"
            for status in item.task_statuses.values()
        ) == item.completed_task_count
        assert set(item.task_statuses) == set(MANDATORY_TASK_CODES)


def test_simulated_favorite_attribution_ignores_invalid_earlier_lesson() -> None:
    def lesson(
        lesson_id: str,
        *,
        valid_for_scoring: bool,
        lesson_time: time | None,
        lesson_date: date,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            lesson_id=lesson_id,
            teacher_id="T-SIM",
            student_id_hash="S-SIM",
            lesson_local_date=lesson_date,
            lesson_local_time=lesson_time,
            lesson_lifecycle_status="end",
            valid_for_scoring=valid_for_scoring,
            is_late=False,
            is_early=False,
            is_peak=False,
            is_favorited=True,
            has_positive_feedback_tag=False,
            is_rebooked=False,
            is_camera_off=False,
            is_cpu_usage_high=False,
            is_network_delay_high=False,
        )

    rows, attributed = _lesson_score_rows(
        [
            lesson(
                "INVALID-NO-TIME",
                valid_for_scoring=False,
                lesson_time=None,
                lesson_date=date(2026, 7, 1),
            ),
            lesson(
                "VALID-LATER",
                valid_for_scoring=True,
                lesson_time=time(10, 0),
                lesson_date=date(2026, 7, 2),
            ),
        ],
        policy_payload={
            "scoring_items": {
                "reliability_perfect": {"points_per_unit": 4},
                "reliability_peak": {"points_per_unit": 2},
                "feedback_praise": {"points_per_unit": 5},
                "feedback_favorite": {"points_per_unit": 5},
                "classroom_quality": {
                    "metric": "lesson_hardware_quality_passed",
                    "points_per_unit": 2,
                },
            }
        },
    )
    favorites = {
        row["lesson"].lesson_id: next(
            component
            for component in row["components"]
            if component["code"] == "FEEDBACK_FAVORITE"
        )
        for row in rows
        if row["dimension"] == "USER_FEEDBACK"
    }

    assert favorites["INVALID-NO-TIME"]["awarded"] is False
    assert favorites["VALID-LATER"]["awarded"] is True
    assert attributed["FEEDBACK_FAVORITE"] == {"count": 1.0, "score": 5.0}
