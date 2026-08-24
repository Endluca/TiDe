from __future__ import annotations

import json
from typing import Any

import pytest

from app.dts_v2_course_materialization_store import (
    DtsV2ProtectedCourseMaterializerError,
    PostgresDtsV2CourseMaterializationCommandStore,
    PostgresDtsV2ProtectedCourseMaterializer,
)
from app.dts_v2_course_source_wide_plan import build_course_source_wide_plan_v2
from tests.test_dts_v2_course_source_wide_plan import _state


class _Result:
    def __init__(self, value: Any):
        self.value = value

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.value)

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, *, wrong_hash: bool = False) -> None:
        self.wrong_hash = wrong_hash
        self.sql: list[str] = []
        self.request: dict[str, Any] | None = None

    def execute(self, statement: object, parameters=None):
        sql = str(statement)
        self.sql.append(sql)
        if "FROM public.domain_aggregate_revisions" in sql:
            return _Result([{"aggregate_state_sha256": "a" * 64}])
        if "materialize_course_source_wide_v2" in sql:
            self.request = json.loads(parameters["request"])
            return _Result(
                {
                    "protocol_version": "course-materialization-result-v1",
                    "request_sha256": (
                        "f" * 64
                        if self.wrong_hash
                        else parameters["expected_request_sha256"]
                    ),
                    "aggregate_state_sha256": "a" * 64,
                    "projection_generation": 3,
                    "affected_teacher_ids": ["A", "B"],
                    "counts": {
                        "compatibility_changes": 1,
                        "component_awards": 3,
                    },
                }
            )
        raise AssertionError(sql)


class _Guard:
    def acquire(self, connection, *, component: str):
        del connection
        assert component == "COURSE"
        return 3


class _Triggers:
    def reconcile_course_matches(self, connection, plan, **kwargs):
        del connection, plan
        assert kwargs["projection_generation"] == 3
        return {"trigger_match_changes": 1}


class _Scores:
    def refresh_course_and_teachers(self, connection, **kwargs):
        del connection
        assert kwargs["projection_generation"] == 3
        return {"teacher_scorecards": 2}


class _NonTaskOutputs:
    def reconcile_course_outputs(self, connection, **kwargs):
        del connection
        assert kwargs["projection_generation"] == 3
        assert kwargs["source_region"] == "dom"
        assert kwargs["source_appoint_id"] == "9001"
        return {"notifications_created": 1}


def _plan():
    return build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=_state(),
    )


def test_protected_command_uses_only_hash_bound_request_and_no_python_dml() -> None:
    connection = _Connection()
    result = PostgresDtsV2ProtectedCourseMaterializer(
        score_refresher=_Scores(),  # type: ignore[arg-type]
        trigger_match_store=_Triggers(),  # type: ignore[arg-type]
        non_task_output_store=_NonTaskOutputs(),  # type: ignore[arg-type]
        primary_guard=_Guard(),  # type: ignore[arg-type]
    ).apply_course_plan(
        connection,  # type: ignore[arg-type]
        _plan(),
        aggregate_revision=17,
        triggering_event_id="event-17",
    )

    assert result == {
        "compatibility_changes": 1,
        "component_awards": 3,
        "trigger_match_changes": 1,
        "notifications_created": 1,
        "teacher_scorecards": 2,
    }
    assert connection.request is not None
    assert connection.request["aggregate_state_sha256"] == "a" * 64
    assert connection.request["participation_versions"] == [
        {
            "participation_seq": 1,
            "participation_row_version": 3,
            "participation_fact_row_version": 2,
        },
        {
            "participation_seq": 2,
            "participation_row_version": 4,
            "participation_fact_row_version": 5,
        },
    ]
    assert all(
        token not in "\n".join(connection.sql).upper()
        for token in (
            "INSERT INTO PUBLIC.LESSON_SOURCE_WIDE",
            "UPDATE PUBLIC.LESSON_SCORE_COMPONENT_SETTLEMENTS",
            "INSERT INTO PUBLIC.SCORE_ENTRIES",
        )
    )


def test_protected_command_rejects_database_hash_mismatch() -> None:
    with pytest.raises(
        DtsV2ProtectedCourseMaterializerError,
        match="DTS_V2_COURSE_COMMAND_REQUEST_HASH_MISMATCH",
    ):
        PostgresDtsV2CourseMaterializationCommandStore().apply(
            _Connection(wrong_hash=True),  # type: ignore[arg-type]
            _plan(),
            aggregate_revision=17,
            projection_generation=3,
            triggering_event_id="event-17",
        )
