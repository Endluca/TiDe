from __future__ import annotations

import hashlib

import pytest

from app.dts_v2_course_source_wide_plan import build_course_source_wide_plan_v2
from app.dts_v2_course_trigger_plan import build_course_trigger_plan_v2
from app.dts_v2_trigger_match_store import (
    DtsV2TriggerMatchStoreError,
    PostgresDtsV2TriggerMatchStore,
)

from tests.test_dts_v2_course_source_wide_plan import _state


class _Scalar:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    def scalar_one(self):
        parameters = self.connection.parameters
        assert parameters is not None
        return {
            "plan_sha256": parameters["expected_plan_sha256"],
            "counts": self.connection.counts,
        }


class _Connection:
    def __init__(self, counts=None) -> None:
        self.counts = counts or {"matches_created": 1, "task_plan_events": 1}
        self.parameters = None
        self.sql = ""

    def execute(self, statement, parameters):
        self.sql = str(statement)
        self.parameters = dict(parameters)
        return _Scalar(self)


def _plan():
    return build_course_trigger_plan_v2(
        build_course_source_wide_plan_v2(
            source_region="dom",
            source_appoint_id="9001",
            aggregate_state=_state(),
        )
    )


def test_calls_exact_hash_checked_protected_command() -> None:
    connection = _Connection()

    result = PostgresDtsV2TriggerMatchStore().reconcile_course_matches(
        connection,  # type: ignore[arg-type]
        _plan(),
        aggregate_revision=17,
        projection_generation=3,
        triggering_event_id="event-17",
    )

    assert result == {"matches_created": 1, "task_plan_events": 1}
    assert "reconcile_course_trigger_matches_v2" in connection.sql
    assert connection.parameters is not None
    payload = connection.parameters["match_plan"]
    assert connection.parameters["expected_plan_sha256"] == hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    assert connection.parameters["expected_course_aggregate_revision"] == 17
    assert connection.parameters["projection_generation"] == 3


def test_invalid_command_counts_fail_closed() -> None:
    with pytest.raises(
        DtsV2TriggerMatchStoreError,
        match="COMMAND_RESULT_INVALID",
    ):
        PostgresDtsV2TriggerMatchStore().reconcile_course_matches(
            _Connection({"matches_created": -1}),  # type: ignore[arg-type]
            _plan(),
            aggregate_revision=1,
            projection_generation=1,
            triggering_event_id="event-1",
        )
