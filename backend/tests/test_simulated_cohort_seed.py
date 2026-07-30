from __future__ import annotations

from collections import Counter

from app.simulated_cohort_seed import (
    DEFAULT_SEED,
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
