from decimal import Decimal

from app.dts_lesson_score_rules_v2 import (
    FrozenCompletionIdentityV2,
    PriorLessonComponentSettlementV2,
    SettlementAction,
    SettlementStatus,
)
from app.dts_v2_course_source_wide_plan import build_course_source_wide_plan_v2
from app.dts_v2_lesson_component_store import (
    PersistedLessonComponentV2,
    plan_course_component_transitions_v2,
)
from tests.test_dts_v2_course_source_wide_plan import _state


def _persisted(seq, teacher, component, *, status=SettlementStatus.AWARDED):
    award_id = f"award-{seq}-{component}"
    return PersistedLessonComponentV2(
        prior=PriorLessonComponentSettlementV2(
            identity=FrozenCompletionIdentityV2("dom", "9001", seq, teacher),
            component_code=component,
            status=status,
            award_generation=1,
            current_score=Decimal("5") if component == "FEEDBACK_PRAISE" else Decimal("4"),
            score_rule_version="dts-lesson-score-v2",
            evidence_fingerprint="a" * 64,
            current_award_score_entry_id=award_id if status == SettlementStatus.AWARDED else None,
            last_reversal_score_entry_id=None if status == SettlementStatus.AWARDED else "reverse-old",
        ),
        award_projection_generation=2,
        row_version=3,
        materialization_origin="V2_LIVE",
        materialized_by_run_id=None,
    )


def test_transition_plan_reverses_old_completion_before_awarding_substitute():
    course = build_course_source_wide_plan_v2(
        source_region="dom", source_appoint_id="9001", aggregate_state=_state()
    )
    transitions = plan_course_component_transitions_v2(
        course_plan=course,
        persisted=[_persisted(1, "A", "FEEDBACK_PRAISE")],
        score_rule_version="dts-lesson-score-v2",
    )
    assert transitions[0].plan.action == SettlementAction.REVERSE
    assert transitions[0].evidence_status == "CONFIRMED"
    by_component = {item.plan.component_code: item.plan for item in transitions[1:]}
    assert by_component["FEEDBACK_PRAISE"].action == SettlementAction.AWARD
    assert by_component["PERFECT_COMPLETED"].action == SettlementAction.AWARD
    assert by_component["PEAK_COMPLETED"].action == SettlementAction.AWARD
    assert by_component["CLASS_QUALITY_HARDWARE"].action == SettlementAction.NOOP


def test_pending_completion_conflict_preserves_every_existing_award():
    state = _state()
    state["course"]["completion_conflict_status"] = "PENDING"
    course = build_course_source_wide_plan_v2(
        source_region="dom", source_appoint_id="9001", aggregate_state=state
    )
    assert plan_course_component_transitions_v2(
        course_plan=course,
        persisted=[_persisted(2, "B", "FEEDBACK_PRAISE")],
        score_rule_version="dts-lesson-score-v2",
    ) == ()
    assert course.favorite_observation_required is False
    assert course.favorite_observation_blocker == "COMPLETION_CONFLICT_PENDING"


def test_deleted_course_reverses_current_awards_and_creates_no_new_award():
    state = _state()
    state["course"]["source_is_deleted"] = True
    course = build_course_source_wide_plan_v2(
        source_region="dom", source_appoint_id="9001", aggregate_state=state
    )
    transitions = plan_course_component_transitions_v2(
        course_plan=course,
        persisted=[_persisted(2, "B", "FEEDBACK_PRAISE")],
        score_rule_version="dts-lesson-score-v2",
    )
    assert [item.plan.action for item in transitions] == [SettlementAction.REVERSE]
