from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.dts_lesson_score_rules_v2 import (
    FrozenCompletionIdentityV2,
    LessonComponentConditionV2,
    LessonScoreRuleError,
    PriorLessonComponentSettlementV2,
    SettlementAction,
    SettlementStatus,
    build_lesson_component_conditions_v2,
    favorite_score_is_current,
    lesson_awarded_score_v2,
    plan_lesson_component_settlement_v2,
)


IDENTITY = FrozenCompletionIdentityV2(
    source_region="dom",
    source_appoint_id="9001",
    completion_participation_seq=2,
    teacher_id="B",
)


def _conditions(**overrides: object):
    values = {
        "source_region": "dom",
        "grading_classification": "POSITIVE",
        "grading_evidence_complete": True,
        "late": False,
        "early": False,
        "attendance_evidence_complete": True,
        "completion_is_peak": True,
        "camera_off": False,
        "cpu_high": None,
        "network_high": None,
    }
    values.update(overrides)
    return build_lesson_component_conditions_v2(**values)  # type: ignore[arg-type]


def _condition(code: str, **overrides: object):
    return next(item for item in _conditions(**overrides) if item.component_code == code)


def _prior(
    code: str,
    *,
    generation: int = 1,
    status: SettlementStatus = SettlementStatus.AWARDED,
    condition=None,
    rule_version: str = "score-v1",
    score: int | Decimal | None = None,
):
    selected = condition or _condition(code)
    default_scores = {
        "FEEDBACK_PRAISE": 5,
        "PERFECT_COMPLETED": 4,
        "PEAK_COMPLETED": 2,
        "CLASS_QUALITY_HARDWARE": 2,
    }
    return PriorLessonComponentSettlementV2(
        identity=IDENTITY,
        component_code=code,  # type: ignore[arg-type]
        status=status,
        award_generation=generation,
        current_score=default_scores[code] if score is None else score,
        score_rule_version=rule_version,
        evidence_fingerprint=selected.evidence_fingerprint,
        current_award_score_entry_id=(
            "ENTRY-1" if status == SettlementStatus.AWARDED else None
        ),
        last_reversal_score_entry_id=(
            None if status == SettlementStatus.AWARDED else "REV-1"
        ),
    )


def test_all_course_components_belong_to_frozen_completion_teacher() -> None:
    plans = [
        plan_lesson_component_settlement_v2(
            identity=IDENTITY,
            condition=condition,
            score_rule_version="score-v1",
            prior=None,
        )
        for condition in _conditions()
    ]

    assert [plan.action for plan in plans] == [
        SettlementAction.AWARD,
        SettlementAction.AWARD,
        SettlementAction.AWARD,
        SettlementAction.NOOP,
    ]
    assert all(plan.identity.teacher_id == "B" for plan in plans)
    assert all("p2" in plan.award_idempotency_key for plan in plans[:3])
    assert plans[3].award_generation == 0


def test_hardware_cannot_award_while_cpu_and_network_sources_are_missing() -> None:
    hardware = _condition("CLASS_QUALITY_HARDWARE")

    assert hardware.should_award is None
    assert hardware.evidence_status == "SOURCE_MISSING"


@pytest.mark.parametrize("value", [0, 1])
def test_tri_state_inputs_reject_integer_bool_aliases(value: int) -> None:
    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_TRI_STATE_INVALID$",
    ):
        _conditions(late=value)

    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_CONDITION_INVALID$",
    ):
        LessonComponentConditionV2(
            component_code="PEAK_COMPLETED",
            should_award=value,  # type: ignore[arg-type]
            evidence_status="CONFIRMED",
            evidence_fingerprint="a" * 64,
        )


def test_hardware_unknown_takes_precedence_over_a_known_failure() -> None:
    hardware = _condition(
        "CLASS_QUALITY_HARDWARE",
        camera_off=True,
        cpu_high=None,
        network_high=None,
    )

    assert hardware.should_award is None
    assert hardware.evidence_status == "SOURCE_MISSING"


@pytest.mark.parametrize(
    ("camera_off", "cpu_high", "network_high", "should_award"),
    [
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ],
)
def test_hardware_is_confirmed_only_when_all_three_sources_are_known(
    camera_off: bool,
    cpu_high: bool,
    network_high: bool,
    should_award: bool,
) -> None:
    hardware = _condition(
        "CLASS_QUALITY_HARDWARE",
        camera_off=camera_off,
        cpu_high=cpu_high,
        network_high=network_high,
    )

    assert hardware.should_award is should_award
    assert hardware.evidence_status == "CONFIRMED"


def test_dom_grading_branch_only_awards_positive_and_ovs_has_no_dom_policy() -> None:
    positive = _condition("FEEDBACK_PRAISE")
    negative = _condition(
        "FEEDBACK_PRAISE",
        grading_classification="NEGATIVE",
    )
    ovs = _condition("FEEDBACK_PRAISE", source_region="ovs")
    missing = _condition(
        "FEEDBACK_PRAISE",
        grading_classification=None,
        grading_evidence_complete=False,
    )

    assert positive.should_award is True
    assert negative.should_award is False
    assert ovs.should_award is False
    assert missing.should_award is None


def test_perfect_requires_both_explicit_false_and_complete_evidence() -> None:
    assert _condition("PERFECT_COMPLETED").should_award is True
    assert _condition("PERFECT_COMPLETED", late=True).should_award is False
    assert _condition("PERFECT_COMPLETED", early=None).should_award is None
    assert _condition(
        "PERFECT_COMPLETED",
        attendance_evidence_complete=False,
    ).should_award is None


def test_first_true_awards_generation_one_with_canonical_key() -> None:
    condition = _condition("PEAK_COMPLETED")
    plan = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=condition,
        score_rule_version="score-v1",
        prior=None,
    )

    assert plan.action == SettlementAction.AWARD
    assert plan.award_generation == 1
    assert plan.score == Decimal("2")
    assert plan.award_idempotency_key == (
        "lesson:dom:9001:p2:PEAK_COMPLETED:gen1:score-v1"
    )


def test_same_current_award_is_a_noop() -> None:
    condition = _condition("PEAK_COMPLETED")
    plan = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=condition,
        score_rule_version="score-v1",
        prior=_prior("PEAK_COMPLETED", condition=condition),
    )

    assert plan.action == SettlementAction.NOOP
    assert plan.award_generation == 1
    assert plan.award_idempotency_key is None
    assert plan.reversal_idempotency_key is None


@pytest.mark.parametrize(
    "condition",
    [
        _condition("PERFECT_COMPLETED", late=True),
        _condition("PERFECT_COMPLETED", late=None),
    ],
)
def test_award_becoming_false_or_unknown_reverses_exactly_once(condition) -> None:
    awarded_condition = _condition("PERFECT_COMPLETED")
    plan = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=condition,
        score_rule_version="score-v1",
        prior=_prior("PERFECT_COMPLETED", condition=awarded_condition),
    )

    assert plan.action == SettlementAction.REVERSE
    assert plan.resulting_status == SettlementStatus.REVERSED
    assert plan.reversal_idempotency_key == "lesson-reversal:ENTRY-1"
    assert plan.reversal_of_score_entry_id == "ENTRY-1"


def test_reversed_false_stays_noop_and_true_reawards_next_generation() -> None:
    false_condition = _condition("PEAK_COMPLETED", completion_is_peak=False)
    reversed_prior = _prior(
        "PEAK_COMPLETED",
        generation=1,
        status=SettlementStatus.REVERSED,
        condition=false_condition,
    )
    noop = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=false_condition,
        score_rule_version="score-v1",
        prior=reversed_prior,
    )
    restored = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=_condition("PEAK_COMPLETED"),
        score_rule_version="score-v1",
        prior=reversed_prior,
    )

    assert noop.action == SettlementAction.NOOP
    assert restored.action == SettlementAction.AWARD
    assert restored.award_generation == 2
    assert restored.award_idempotency_key.endswith(":gen2:score-v1")


def test_reawarded_prior_retains_last_reversal_history() -> None:
    condition = _condition("PEAK_COMPLETED")
    prior = PriorLessonComponentSettlementV2(
        identity=IDENTITY,
        component_code="PEAK_COMPLETED",
        status=SettlementStatus.AWARDED,
        award_generation=2,
        current_score=2,
        score_rule_version="score-v1",
        evidence_fingerprint=condition.evidence_fingerprint,
        current_award_score_entry_id="ENTRY-2",
        last_reversal_score_entry_id="REV-1",
    )

    plan = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=condition,
        score_rule_version="score-v1",
        prior=prior,
    )

    assert plan.action == SettlementAction.NOOP
    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_REAWARD_REVERSAL_MISSING$",
    ):
        replace(prior, last_reversal_score_entry_id=None)
    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_FIRST_AWARD_HAS_REVERSAL$",
    ):
        replace(prior, award_generation=1)


def test_rule_or_semantic_change_reverses_and_reawards_atomically() -> None:
    old_condition = _condition("FEEDBACK_PRAISE")
    new_condition = replace(
        old_condition,
        evidence_fingerprint="f" * 64,
    )
    plan = plan_lesson_component_settlement_v2(
        identity=IDENTITY,
        condition=new_condition,
        score_rule_version="score-v2",
        prior=_prior("FEEDBACK_PRAISE", condition=old_condition),
    )

    assert plan.action == SettlementAction.REPLACE
    assert plan.award_generation == 2
    assert plan.reversal_idempotency_key == "lesson-reversal:ENTRY-1"
    assert plan.award_idempotency_key.endswith(":gen2:score-v2")


def test_rule_version_and_generated_idempotency_key_fit_score_entry_columns() -> None:
    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_RULE_VERSION_INVALID$",
    ):
        plan_lesson_component_settlement_v2(
            identity=IDENTITY,
            condition=_condition("PEAK_COMPLETED"),
            score_rule_version="v" * 65,
            prior=None,
        )

    oversized_identity = replace(IDENTITY, source_appoint_id="9" * 300)
    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_IDEMPOTENCY_KEY_INVALID$",
    ):
        plan_lesson_component_settlement_v2(
            identity=oversized_identity,
            condition=_condition("PEAK_COMPLETED"),
            score_rule_version="score-v1",
            prior=None,
        )


def test_prior_for_another_teacher_or_participation_is_rejected() -> None:
    prior = replace(
        _prior("PEAK_COMPLETED"),
        identity=replace(IDENTITY, teacher_id="A", completion_participation_seq=1),
    )

    with pytest.raises(
        LessonScoreRuleError,
        match="^LESSON_SCORE_PRIOR_IDENTITY_MISMATCH$",
    ):
        plan_lesson_component_settlement_v2(
            identity=IDENTITY,
            condition=_condition("PEAK_COMPLETED"),
            score_rule_version="score-v1",
            prior=prior,
        )


def test_favorite_held_evidence_retains_score_without_a_second_award() -> None:
    assert favorite_score_is_current("AWARDED") is True
    assert favorite_score_is_current("AWARDED_PENDING_EVIDENCE") is True
    assert favorite_score_is_current("REVERSED") is False


def test_lesson_total_sums_current_components_and_favorite_only() -> None:
    assert lesson_awarded_score_v2(
        {
            "FEEDBACK_PRAISE": SettlementStatus.AWARDED,
            "PERFECT_COMPLETED": SettlementStatus.AWARDED,
            "PEAK_COMPLETED": SettlementStatus.REVERSED,
            "CLASS_QUALITY_HARDWARE": SettlementStatus.REVERSED,
        },
        favorite_attribution_status="AWARDED_PENDING_EVIDENCE",
    ) == Decimal("14")
