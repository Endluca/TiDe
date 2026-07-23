from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    SCORE_POLICY_V2_PAYLOAD,
    SCORE_POLICY_V3_PAYLOAD,
    SCORE_POLICY_V4_PAYLOAD,
    ConfigKey,
    ScoreGraduationConfig,
)
from app.services import GrowthService


def _reader(payload: dict | None = None):
    selected = payload or DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]
    return lambda key: deepcopy(selected) if ConfigKey(key) == ConfigKey.SCORE_GRADUATION else None


def _provenance(metrics: dict, **overrides: str) -> dict:
    result = {
        key: {
            "source_mode": "REAL",
            "source_field": key,
            "batch_id": "BATCH-TEST",
            "note": "",
        }
        for key in metrics
    }
    for key, source_mode in overrides.items():
        result.setdefault(key, {})["source_mode"] = source_mode
    return result


def _project(teacher: dict, policy: dict | None = None) -> dict:
    service = GrowthService(None, config_reader=_reader(policy))  # type: ignore[arg-type]
    return service._project_teacher_scoring(teacher)


def _canonical_teacher(**metric_overrides) -> dict:
    metrics = {
        "total_completed_cnt": 20,
        "peak_completed_cnt": 5,
        "on_time_completed_cnt": 18,
        "perfect_cnt": 20,
        "feedback_praise_cnt": 2,
        "feedback_favorite_cnt": 1,
        "completed_again_student_15d_cnt": 1,
        "late_cnt": 1,
        "early_cnt": 0,
        "real_absent_cnt": 0,
        "severe_redline_event": False,
        "peak_slot_cnt": 40,
        "capacity_milestone_achieved": True,
        "capacity_score": 10,
        "new_teacher_task_score": 30,
        "mandatory_task_assignment_count": 10,
        "mandatory_task_completed_count": 10,
        "mandatory_task_expected_count": 10,
        "l0_complaint_cnt": 0,
        "class_quality_no_issue_rate": 0.8,
    }
    metrics.update(metric_overrides)
    if "total_completed_cnt" in metric_overrides and "perfect_cnt" not in metric_overrides:
        metrics["perfect_cnt"] = metric_overrides["total_completed_cnt"]
    return {
        "teacher_id": "T-V2",
        "graduation_state": "IN_PROGRESS",
        "employment_status": "on",
        "data_mode": "MIXED",
        "metric_inputs": metrics,
        "metric_provenance": _provenance(
            metrics,
            on_time_completed_cnt="DERIVED_REAL",
            peak_slot_cnt="REAL",
            capacity_score="DERIVED_REAL",
            new_teacher_task_score="SYSTEM_TASK_STATUS",
            mandatory_task_assignment_count="SYSTEM_TASK_STATUS",
            mandatory_task_completed_count="SYSTEM_TASK_STATUS",
            mandatory_task_expected_count="SYSTEM_CONFIG",
            l0_complaint_cnt="DERIVED_REAL",
            class_quality_no_issue_rate="DERIVED_REAL",
            severe_redline_event="REAL",
            real_absent_cnt="REAL",
        ),
    }


def test_v6_perfect_count_formula_external_cap_and_source_modes() -> None:
    teacher = _project(_canonical_teacher())
    dimensions = {item["code"]: item for item in teacher["dimensions"]}

    assert dimensions["USER_FEEDBACK"]["score"] == 23  # 2*5 + 1*5 + 1*8
    assert dimensions["RELIABILITY"]["score"] == 41  # 18*2 + 5*1
    assert dimensions["CLASS_QUALITY"]["score"] == 32  # 20*1.6
    assert dimensions["CLASS_QUALITY"]["components"][0]["metric"] == "perfect_cnt"
    assert dimensions["CAPACITY"]["score"] == 10
    assert dimensions["NEW_TEACHER_TASK"]["score"] == 30
    assert dimensions["USER_FEEDBACK"]["source_mode"] == "REAL"
    assert dimensions["RELIABILITY"]["source_mode"] == "DERIVED_REAL"
    assert dimensions["CLASS_QUALITY"]["source_mode"] == "DERIVED_REAL"
    assert teacher["base_score"] == 40
    assert teacher["raw_total_score"] == teacher["total_score"] == 136
    assert teacher["external_display_score"] == 136
    assert teacher["graduation_score_threshold_met"] is True
    assert teacher["graduation_criteria_met"] is True
    assert teacher["graduation_state"] == "GRADUATED"
    assert teacher["graduation_effect"] == "IMMEDIATE_ON_CRITERIA"
    assert teacher["gold_score_threshold_met"] is False
    assert teacher["gold_criteria_met"] is False
    assert teacher["hard_gates"]["gold"]["met"] is False
    assert teacher["hard_gates"]["gold"]["items"][0]["source_mode"] == "DERIVED_REAL"
    assert teacher["score_policy_version"] == "v6"
    assert teacher["score_policy_source"] == "PUBLISHED"


@pytest.mark.parametrize(
    ("raw_score", "external_score", "graduation_met", "gold_met"),
    [
        (99, 99, False, False),
        (100, 100, True, False),
        (199, 199, True, False),
        (200, 200, True, True),
        (201, 200, True, True),
        (660, 200, True, True),
    ],
)
def test_external_scale_and_score_threshold_boundaries(
    raw_score: float,
    external_score: float,
    graduation_met: bool,
    gold_met: bool,
) -> None:
    praise_count = (raw_score - 40) / 5
    metrics = {
        "peak_slot_cnt": 40,
        "capacity_milestone_achieved": True,
        "capacity_score": 10,
        "new_teacher_task_score": 30,
        "feedback_praise_cnt": praise_count,
    }
    teacher = _project(
        {
            "teacher_id": f"T-{raw_score}",
            "metric_inputs": metrics,
            "metric_provenance": _provenance(metrics),
        }
    )

    assert teacher["raw_total_score"] == raw_score
    assert teacher["external_display_score"] == external_score
    assert teacher["graduation_score_threshold_met"] is graduation_met
    assert teacher["gold_score_threshold_met"] is gold_met


def test_v6_gold_eligibility_has_only_graduation_and_total_score_gates() -> None:
    def gold_candidate(late_count: int) -> dict:
        on_time = 10 - late_count
        non_feedback_score = 40 + 10 * 2 * 0.8 + on_time * 2
        return _project(
            _canonical_teacher(
                total_completed_cnt=10,
                peak_completed_cnt=0,
                on_time_completed_cnt=on_time,
                feedback_praise_cnt=(200 - non_feedback_score) / 5,
                feedback_favorite_cnt=0,
                completed_again_student_15d_cnt=0,
                late_cnt=late_count,
            )
        )

    eligible = gold_candidate(1)
    also_eligible = gold_candidate(2)

    assert eligible["raw_total_score"] == also_eligible["raw_total_score"] == 200
    assert eligible["gold_score_threshold_met"] is True
    assert eligible["gold_criteria_met"] is True
    assert also_eligible["gold_score_threshold_met"] is True
    assert also_eligible["gold_criteria_met"] is True
    assert [
        item["code"] for item in also_eligible["hard_gates"]["gold"]["items"]
    ] == ["REQUIRES_GRADUATION_CRITERIA", "MINIMUM_GOLD_TOTAL_SCORE"]
    assert also_eligible["hard_gates"]["gold"]["met"] is True


def test_l0_complaint_blocks_both_graduation_and_gold() -> None:
    teacher = _canonical_teacher(
        total_completed_cnt=10,
        peak_completed_cnt=0,
        on_time_completed_cnt=10,
        feedback_praise_cnt=24.8,
        feedback_favorite_cnt=0,
        completed_again_student_15d_cnt=0,
        late_cnt=0,
        l0_complaint_cnt=1,
    )
    projected = _project(teacher)

    assert projected["raw_total_score"] == 200
    assert projected["gold_score_threshold_met"] is True
    assert projected["hard_gates"]["gold"]["met"] is False
    assert projected["hard_gates"]["gold"]["inherits_graduation"] is True
    l0_gate = next(
        item
        for item in projected["hard_gates"]["graduation"]["items"]
        if item["code"] == "NO_L0_COMPLAINT"
    )
    assert l0_gate["actual"] == 1
    assert l0_gate["met"] is False
    inherited_gate = projected["hard_gates"]["gold"]["items"][0]
    assert inherited_gate == {
        "code": "REQUIRES_GRADUATION_CRITERIA",
        "metric": "graduation_criteria_met",
        "operator": "==",
        "threshold": True,
        "actual": False,
        "met": False,
        "source_mode": "DERIVED_REAL",
    }
    assert projected["hard_gates"]["gold"]["items"][1] == {
        "code": "MINIMUM_GOLD_TOTAL_SCORE",
        "metric": "raw_total_score",
        "operator": ">=",
        "threshold": 200.0,
        "actual": 200.0,
        "met": True,
        "source_mode": "SYSTEM_TASK_STATUS",
    }
    assert projected["graduation_criteria_met"] is False
    assert projected["gold_criteria_met"] is False


def test_missing_evidence_scores_zero_and_l0_gate_fails_closed() -> None:
    metrics = {"total_completed_cnt": 10, "capacity_score": 10, "new_teacher_task_score": 30}
    teacher = _project(
        {
            "teacher_id": "T-MISSING",
            "metric_inputs": metrics,
            "metric_provenance": _provenance(metrics),
        }
    )
    feedback = next(item for item in teacher["dimensions"] if item["code"] == "USER_FEEDBACK")
    quality = next(item for item in teacher["dimensions"] if item["code"] == "CLASS_QUALITY")
    l0_gate = next(
        item
        for item in teacher["hard_gates"]["graduation"]["items"]
        if item["code"] == "NO_L0_COMPLAINT"
    )

    assert feedback["score"] == 0
    assert feedback["source_mode"] == "MISSING_INPUT_ZERO"
    assert quality["score"] == 0
    assert quality["source_mode"] == "SOURCE_MISSING"
    assert l0_gate["actual"] == 0
    assert l0_gate["met"] is False
    assert l0_gate["source_mode"] == "SOURCE_MISSING"
    assert teacher["graduation_criteria_met"] is False
    assert teacher["hard_gates"]["gold"]["items"][0]["source_mode"] == "MIXED_DERIVED"


def test_v3_rejects_severe_redline_override_instead_of_reusing_the_version_label() -> None:
    policy = deepcopy(SCORE_POLICY_V3_PAYLOAD)
    policy["hard_gates"]["graduation"]["allow_severe_redline"] = True

    with pytest.raises(ValueError, match="v3 已冻结"):
        ScoreGraduationConfig.model_validate(policy)


def test_v4_capacity_peak_slot_boundary_and_first_achievement_lock() -> None:
    below = _project(
        _canonical_teacher(
            peak_slot_cnt=39,
            capacity_milestone_achieved=False,
            capacity_score=10,  # historical Mock value must be ignored
        )
    )
    reached = _project(
        _canonical_teacher(
            peak_slot_cnt=40,
            capacity_milestone_achieved=False,
            capacity_score=0,
        )
    )
    locked_after_correction = _project(
        _canonical_teacher(
            peak_slot_cnt=39,
            capacity_milestone_achieved=True,
            capacity_score=10,
        )
    )

    below_capacity = next(item for item in below["dimensions"] if item["code"] == "CAPACITY")
    reached_capacity = next(
        item for item in reached["dimensions"] if item["code"] == "CAPACITY"
    )
    locked_capacity = next(
        item for item in locked_after_correction["dimensions"] if item["code"] == "CAPACITY"
    )
    assert below_capacity["score"] == 0
    assert reached_capacity["score"] == locked_capacity["score"] == 10
    assert reached_capacity["components"] == [
        {
            "code": "CAPACITY_PEAK_SLOT_40",
            "metric": "peak_slot_cnt",
            "value": 40.0,
            "score": 10.0,
            "source_mode": "DERIVED_REAL",
            "maximum_points": 10.0,
            "operator": "GTE",
            "threshold": 40,
            "milestone_achieved": True,
            "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
        }
    ]


def test_v4_ignores_historical_capacity_task_account_override() -> None:
    teacher = _canonical_teacher(
        peak_slot_cnt=39,
        capacity_milestone_achieved=False,
        capacity_score=10,
    )
    service = GrowthService(None, config_reader=_reader())  # type: ignore[arg-type]

    projected = service._project_teacher_scoring(
        teacher,
        score_account_overrides={
            "CAPACITY": {
                "score": 10,
                "source_mode": "SYSTEM_TASK_STATUS",
            }
        },
    )

    capacity = next(item for item in projected["dimensions"] if item["code"] == "CAPACITY")
    assert capacity["score"] == 0
    assert capacity["source_mode"] == "DERIVED_REAL"


def test_optional_supply_points_cannot_replace_mandatory_growth_for_graduation() -> None:
    projected = _project(
        _canonical_teacher(
            capacity_score=10,
            new_teacher_task_score=20,
            mandatory_task_assignment_count=9,
            mandatory_task_completed_count=9,
            feedback_praise_cnt=10,
        )
    )

    mandatory_gate = next(
        item
        for item in projected["hard_gates"]["graduation"]["items"]
        if item["code"] == "ALL_MANDATORY_GROWTH_TASKS_COMPLETED"
    )
    assert projected["base_score"] == 30
    assert projected["raw_total_score"] >= 100
    assert mandatory_gate["metric"] == "mandatory_task_completed_count"
    assert mandatory_gate["actual"] == 9
    assert mandatory_gate["threshold"] == 10
    assert mandatory_gate["met"] is False
    assert projected["raw_total_score"] >= 100
    assert projected["graduation_criteria_met"] is False


def test_historical_v4_quality_formula_remains_readable() -> None:
    projected = _project(
        _canonical_teacher(perfect_cnt=999),
        deepcopy(SCORE_POLICY_V4_PAYLOAD),
    )
    quality = next(
        item for item in projected["dimensions"] if item["code"] == "CLASS_QUALITY"
    )
    assert quality["score"] == 32
    assert quality["components"][0]["metric"] == "total_completed_cnt"
    assert projected["score_policy_version"] == "v4"


def test_old_dimensions_remain_readable_but_old_policy_never_changes_v4_rules() -> None:
    legacy_policy = {
        "dimensions": {
            code: {"cap": 20, "weight": 0.2, "minimum_score": 0}
            for code in (
                "reliability",
                "user_feedback",
                "classroom_quality",
                "capacity",
                "new_teacher_tasks",
            )
        },
        "total_threshold": 50,
        "settlement_window_hours": 72,
    }
    teacher = _project(
        {
            "teacher_id": "T-LEGACY",
            "lessons_completed": 12,
            "dimensions": [
                {"code": "RELIABILITY", "label": "可靠性", "score": 20},
                {"code": "USER_FEEDBACK", "label": "用户反馈", "score": 8},
                {"code": "CLASS_QUALITY", "label": "课堂质量", "score": 11},
                {"code": "CAPACITY", "label": "产能", "score": 10},
                {"code": "NEW_TEACHER_TASK", "label": "新师任务", "score": 30},
            ],
        },
        legacy_policy,
    )

    assert teacher["raw_total_score"] == 69
    assert teacher["graduation_threshold"] == 100
    assert teacher["score_policy_source"] == "CODE_DEFAULT_LEGACY_CONFIG"
    assert {item["source_mode"] for item in teacher["dimensions"]} == {"LEGACY_DIMENSION"}


def test_published_v2_policy_remains_readable_with_its_historical_piecewise_scale() -> None:
    metrics = {
        "capacity_score": 10,
        "new_teacher_task_score": 30,
        "feedback_praise_cnt": 68,
    }

    teacher = _project(
        {
            "teacher_id": "T-HISTORICAL-V2",
            "metric_inputs": metrics,
            "metric_provenance": _provenance(metrics),
        },
        deepcopy(SCORE_POLICY_V2_PAYLOAD),
    )

    assert teacher["raw_total_score"] == 380
    assert teacher["external_display_score"] == 150
    assert teacher["gold_threshold"] == 660
    assert teacher["score_policy_version"] == "v2"


def test_v4_never_projects_historical_negative_or_capacity_mock_as_a_deduction() -> None:
    teacher = _project(
        {
            "teacher_id": "T-NO-DEDUCTION",
            "dimensions": [
                {"code": "RELIABILITY", "label": "可靠性", "score": -20},
                {"code": "USER_FEEDBACK", "label": "用户反馈", "score": 5},
                {"code": "CLASS_QUALITY", "label": "课堂质量", "score": 0},
                {"code": "CAPACITY", "label": "产能", "score": 10},
                {"code": "NEW_TEACHER_TASK", "label": "新师任务", "score": 30},
            ],
        }
    )

    assert teacher["raw_total_score"] == 35
    assert next(item for item in teacher["dimensions"] if item["code"] == "RELIABILITY")["score"] == 0


def test_dashboard_excludes_graduated_and_off_teachers_from_active_count() -> None:
    on_teacher = _canonical_teacher()
    off_teacher = _canonical_teacher()
    off_teacher.update(teacher_id="T-OFF", employment_status="off", data_mode="REAL")
    state = SimpleNamespace(
        teachers={"T-V2": on_teacher, "T-OFF": off_teacher},
        tasks={},
        ops_cases={},
        executions={},
        notifications={},
        shared_assignment_counts=lambda: {"total": 0, "active": 0, "completed": 0},
    )
    service = GrowthService(state, config_reader=_reader())  # type: ignore[arg-type]

    dashboard = service.dashboard()

    assert dashboard["teacher_count"] == 2
    assert dashboard["active_teacher_count"] == 0
    assert dashboard["data_mode_counts"] == {"MIXED": 1, "REAL": 1}
    assert dashboard["employment_status_counts"] == {"on": 1, "off": 1}
    assert dashboard["graduation_score_reached_count"] == 2
    assert dashboard["graduation_criteria_met_count"] == 2
    assert dashboard["gold_score_reached_count"] == 0
    assert dashboard["gold_eligible_count"] == 0
    assert dashboard["gold_criteria_met_count"] == 0
    assert dashboard["funnel_by_employment_status"] == {
        "on": {
            "teacher_count": 1,
            "graduation_score_reached_count": 1,
            "graduation_criteria_met_count": 1,
            "gold_eligible_count": 0,
        },
        "off": {
            "teacher_count": 1,
            "graduation_score_reached_count": 1,
            "graduation_criteria_met_count": 1,
            "gold_eligible_count": 0,
        },
        "hei": {
            "teacher_count": 0,
            "graduation_score_reached_count": 0,
            "graduation_criteria_met_count": 0,
            "gold_eligible_count": 0,
        },
    }


def test_dashboard_groups_funnel_by_normalized_employment_status() -> None:
    on_gold = _canonical_teacher(feedback_praise_cnt=14.8)
    on_gold.update(teacher_id="T-ON-GOLD", employment_status=" ON ")
    off_graduated = _canonical_teacher()
    off_graduated.update(teacher_id="T-OFF-GRADUATED", employment_status="Off")
    hei_blocked = _canonical_teacher(l0_complaint_cnt=1)
    hei_blocked.update(teacher_id="T-HEI-BLOCKED", employment_status="HEI")
    state = SimpleNamespace(
        teachers={
            teacher["teacher_id"]: teacher
            for teacher in (on_gold, off_graduated, hei_blocked)
        },
        tasks={},
        ops_cases={},
        executions={},
        notifications={},
        shared_assignment_counts=lambda: {"total": 0, "active": 0, "completed": 0},
    )
    service = GrowthService(state, config_reader=_reader())  # type: ignore[arg-type]

    dashboard = service.dashboard()

    assert set(dashboard["funnel_by_employment_status"]) == {"on", "off", "hei"}
    assert dashboard["funnel_by_employment_status"] == {
        "on": {
            "teacher_count": 1,
            "graduation_score_reached_count": 1,
            "graduation_criteria_met_count": 1,
            "gold_eligible_count": 1,
        },
        "off": {
            "teacher_count": 1,
            "graduation_score_reached_count": 1,
            "graduation_criteria_met_count": 1,
            "gold_eligible_count": 0,
        },
        "hei": {
            "teacher_count": 1,
            "graduation_score_reached_count": 1,
            "graduation_criteria_met_count": 0,
            "gold_eligible_count": 0,
        },
    }


def test_dashboard_reads_score_accounts_once_when_no_overrides_exist() -> None:
    score_account_reads: list[set[str]] = []

    def score_account_values(teacher_ids: set[str]) -> dict:
        score_account_reads.append(set(teacher_ids))
        return {}

    state = SimpleNamespace(
        teachers={
            "T-V2": _canonical_teacher(),
            "T-V2-B": {**_canonical_teacher(), "teacher_id": "T-V2-B"},
        },
        tasks={},
        ops_cases={},
        executions={},
        notifications={},
        score_account_values=score_account_values,
        shared_assignment_counts=lambda: {"total": 0, "active": 0, "completed": 0},
    )
    service = GrowthService(state, config_reader=_reader())  # type: ignore[arg-type]

    dashboard = service.dashboard()

    assert dashboard["teacher_count"] == 2
    assert score_account_reads == [{"T-V2", "T-V2-B"}]


def test_task_baseline_incomplete_is_a_dominant_internal_source_mode() -> None:
    assert GrowthService._dominant_source_mode(
        "DERIVED_REAL",
        "TASK_BASELINE_INCOMPLETE",
    ) == "TASK_BASELINE_INCOMPLETE"
    # Compatibility for records produced before the source-mode rename.
    assert GrowthService._dominant_source_mode(
        "REAL",
        "TASK_STATUS_PARTIAL",
    ) == "TASK_STATUS_PARTIAL"
