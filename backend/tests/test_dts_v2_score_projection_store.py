from __future__ import annotations

from typing import Any

import pytest

from app.dts_v2_favorite_runtime import FavoriteAttributionOutcomeV2
from app.dts_v2_score_projection_store import (
    DtsV2ScoreProjectionStoreError,
    PostgresDtsV2ScoreProjectionStore,
)


class _Result:
    def __init__(self, scalar: Any = None, row: dict[str, Any] | None = None):
        self.scalar = scalar
        self.row = row

    def scalar_one(self):
        return self.scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Connection:
    def __init__(self, *, mode: str = "V2_PRIMARY", generation: int = 3):
        self.mode = mode
        self.generation = generation
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, parameters=None) -> _Result:
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        if "FROM public.dts_pipeline_control" in sql:
            return _Result(
                row={
                    "mode": self.mode,
                    "projection_generation": self.generation,
                }
            )
        if "teacher_score_projection_vector_v2" in sql:
            return _Result(
                {
                    "v": 1,
                    "teacher_id": values["teacher_id"],
                    "score_entry_count": 4,
                }
            )
        if "rebuild_lesson_score_result_v2" in sql:
            return _Result({"lesson_result_changes": 1})
        if "rebuild_teacher_score_and_qualification_v2" in sql:
            return _Result(
                {
                    "score_account_changes": 2,
                    "qualification_changes": 1,
                }
            )
        raise AssertionError(sql)


def test_course_rebuilds_lesson_then_each_sorted_unique_teacher() -> None:
    connection = _Connection()
    store = PostgresDtsV2ScoreProjectionStore()

    result = store.refresh_course_and_teachers(
        connection,  # type: ignore[arg-type]
        source_region="dom",
        source_appoint_id="9001",
        teacher_ids=("B", "A", "B"),
        projection_generation=3,
    )

    assert result == {
        "lesson_result_changes": 1,
        "qualification_changes": 2,
        "score_account_changes": 4,
    }
    vector_calls = [
        parameters
        for sql, parameters in connection.calls
        if "teacher_score_projection_vector_v2" in sql
    ]
    assert [call["teacher_id"] for call in vector_calls] == ["A", "B"]
    rebuild_calls = [
        parameters
        for sql, parameters in connection.calls
        if "rebuild_teacher_score_and_qualification_v2" in sql
    ]
    assert all(call["projection_generation"] == 3 for call in rebuild_calls)
    assert '"score_entry_count":4' in rebuild_calls[0]["expected_vector"]


def test_favorite_rebuilds_old_and_new_courses_then_teacher_at_global_generation() -> None:
    connection = _Connection(generation=7)
    outcome = FavoriteAttributionOutcomeV2(
        action="RESELECT",
        source_region="dom",
        teacher_id="T1",
        student_token="dom:v1:" + "a" * 64,
        previous_source_appoint_id="9002",
        current_source_appoint_id="9001",
        award_generation=2,
        score_entry_ids=("reverse", "award"),
    )

    result = PostgresDtsV2ScoreProjectionStore().rebuild_after_favorite(
        connection,  # type: ignore[arg-type]
        outcome,
    )

    assert result == {
        "lesson_result_changes": 2,
        "qualification_changes": 1,
        "score_account_changes": 2,
    }
    lesson_calls = [
        parameters
        for sql, parameters in connection.calls
        if "rebuild_lesson_score_result_v2" in sql
    ]
    assert [row["source_appoint_id"] for row in lesson_calls] == ["9001", "9002"]
    assert all(row["projection_generation"] == 7 for row in lesson_calls)


@pytest.mark.parametrize("mode", ["V1_COMPAT_DUAL_CAPTURE", "ROLLED_BACK"])
def test_favorite_score_rebuild_is_closed_outside_v2_primary(mode: str) -> None:
    outcome = FavoriteAttributionOutcomeV2(
        action="AWARD",
        source_region="dom",
        teacher_id="T1",
        student_token="dom:v1:" + "a" * 64,
        previous_source_appoint_id=None,
        current_source_appoint_id="9001",
        award_generation=1,
        score_entry_ids=("award",),
    )

    with pytest.raises(
        DtsV2ScoreProjectionStoreError,
        match="DTS_V2_SCORE_PRIMARY_MODE_REQUIRED",
    ):
        PostgresDtsV2ScoreProjectionStore().rebuild_after_favorite(
            _Connection(mode=mode),  # type: ignore[arg-type]
            outcome,
        )
