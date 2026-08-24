from __future__ import annotations

from typing import Any

import pytest

from app.dts_v2_course_materializer import (
    DtsV2CourseMaterializerError,
    PostgresDtsV2CourseMaterializer,
)
from app.dts_v2_course_source_wide_plan import (
    build_course_source_wide_plan_v2,
)
from tests.test_dts_v2_course_source_wide_plan import _state


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.scalar


class _Connection:
    def __init__(self, *, mode: str = "V2_PRIMARY", generation: int = 3):
        self.mode = mode
        self.generation = generation
        self.compatibility_values: dict[str, Any] | None = None

    def execute(self, statement: object, parameters=None) -> _Result:
        sql = str(statement)
        if "FROM public.dts_pipeline_control" in sql:
            return _Result(
                rows=[
                    {
                        "mode": self.mode,
                        "projection_generation": self.generation,
                    }
                ]
            )
        if "FROM public.teacher_student_relationship_current" in sql:
            return _Result(rows=[{"is_favorited": True, "is_blocked": False}])
        if "INSERT INTO public.lesson_source_wide" in sql:
            self.compatibility_values = dict(parameters)
            return _Result(scalar="9001")
        raise AssertionError(sql)


class _Components:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    def settle_course(self, connection, plan, **kwargs):
        del connection, plan
        self.call = dict(kwargs)
        return {"component_awards": 1}


class _Refresher:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    def refresh_course_and_teachers(self, connection, **kwargs):
        del connection
        self.call = dict(kwargs)
        return {"teacher_scorecards": 1}


class _Triggers:
    def __init__(self) -> None:
        self.plan = None
        self.call: dict[str, Any] | None = None

    def reconcile_course_matches(self, connection, plan, **kwargs):
        del connection
        self.plan = plan
        self.call = dict(kwargs)
        return {"trigger_match_changes": 1}


def _plan():
    return build_course_source_wide_plan_v2(
        source_region="dom",
        source_appoint_id="9001",
        aggregate_state=_state(),
    )


def test_uses_pipeline_generation_not_domain_aggregate_revision() -> None:
    connection = _Connection(generation=3)
    components = _Components()
    refresher = _Refresher()
    triggers = _Triggers()
    materializer = PostgresDtsV2CourseMaterializer(
        score_refresher=refresher,
        trigger_match_store=triggers,  # type: ignore[arg-type]
        component_store=components,  # type: ignore[arg-type]
    )

    result = materializer.apply_course_plan(
        connection,  # type: ignore[arg-type]
        _plan(),
        aggregate_revision=17,
        triggering_event_id="event-17",
    )

    assert result == {
        "compatibility_changes": 1,
        "trigger_match_changes": 1,
        "component_awards": 1,
        "teacher_scorecards": 1,
    }
    assert components.call == {
        "aggregate_revision": 17,
        "projection_generation": 3,
        "triggering_event_id": "event-17",
    }
    assert refresher.call is not None
    assert refresher.call["projection_generation"] == 3
    assert connection.compatibility_values is not None
    assert connection.compatibility_values["teacher_id"] == "B"
    assert connection.compatibility_values["complaint_l3"] == "教师缺席"
    assert triggers.call == {
        "aggregate_revision": 17,
        "projection_generation": 3,
        "triggering_event_id": "event-17",
    }
    assert triggers.plan is not None
    assert [item.teacher_id for item in triggers.plan.matches] == ["A"]


@pytest.mark.parametrize("mode", ["V1_COMPAT_DUAL_CAPTURE", "ROLLED_BACK"])
def test_rejects_production_materialization_outside_v2_primary(mode: str) -> None:
    materializer = PostgresDtsV2CourseMaterializer(
        score_refresher=_Refresher(),
        trigger_match_store=_Triggers(),  # type: ignore[arg-type]
        component_store=_Components(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        DtsV2CourseMaterializerError,
        match="DTS_V2_COURSE_PRIMARY_MODE_REQUIRED",
    ):
        materializer.apply_course_plan(
            _Connection(mode=mode),  # type: ignore[arg-type]
            _plan(),
            aggregate_revision=17,
            triggering_event_id="event-17",
        )
