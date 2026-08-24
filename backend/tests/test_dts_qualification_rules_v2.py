from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.dts_qualification_rules_v2 import (
    DtsQualificationRuleError,
    PriorQualificationStateV2,
    QualificationProjectionV2,
    rebuild_teacher_qualification_v2,
)


UTC = timezone.utc
OCCURRED_AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _rebuild(**overrides: object) -> QualificationProjectionV2:
    values: dict[str, object] = {
        "raw_total_score": 0,
        "mandatory_task_assignment_count": 9,
        "mandatory_task_completed_count": 9,
        "l0_complaint_count": 0,
        "late_count": 0,
        "early_count": 0,
        "absent_count": 0,
        "occurred_at": OCCURRED_AT,
        "grants_enabled": True,
    }
    values.update(overrides)
    return rebuild_teacher_qualification_v2(**values)  # type: ignore[arg-type]


def _as_prior(projected: QualificationProjectionV2) -> PriorQualificationStateV2:
    return PriorQualificationStateV2(
        camp_state=projected.camp_state,
        graduation_qualified=projected.graduation_qualified,
        graduation_qualified_at=projected.graduation_qualified_at,
        graduation_score_locked=projected.graduation_score_locked,
        gold_qualified=projected.gold_qualified,
        gold_qualified_at=projected.gold_qualified_at,
    )


@pytest.mark.parametrize(
    ("raw_score", "graduation_met", "gold_met", "public_score"),
    [
        (Decimal("99.999"), False, False, Decimal("99.999")),
        (Decimal("100"), True, False, Decimal("100")),
        (Decimal("199.999"), True, False, Decimal("199.999")),
        (Decimal("200"), True, True, Decimal("200")),
        (Decimal("250.25"), True, True, Decimal("200")),
        (Decimal("1E+100"), True, True, Decimal("200")),
    ],
)
def test_score_boundaries_and_public_cap(
    raw_score: Decimal,
    graduation_met: bool,
    gold_met: bool,
    public_score: Decimal,
) -> None:
    projected = _rebuild(raw_total_score=raw_score)

    assert projected.raw_total_score == raw_score
    assert projected.public_total_score == public_score
    assert projected.graduation_score_threshold_met is graduation_met
    assert projected.gold_score_threshold_met is gold_met
    assert projected.graduation_criteria_met is graduation_met
    assert projected.gold_criteria_met is gold_met
    assert projected.graduation_qualified is graduation_met
    assert projected.gold_qualified is gold_met
    assert projected.camp_state == (
        "GRADUATED" if graduation_met else "IN_CAMP"
    )


def test_first_graduation_and_gold_lock_first_times_and_only_graduation_score() -> None:
    projected = _rebuild(raw_total_score=250)

    assert projected.raw_total_score == Decimal("250")
    assert projected.public_total_score == Decimal("200")
    assert projected.graduation_qualified_at == OCCURRED_AT
    assert projected.graduation_score_locked == Decimal("100")
    assert projected.gold_qualified_at == OCCURRED_AT

    later = _rebuild(
        raw_total_score=320,
        occurred_at=OCCURRED_AT + timedelta(days=10),
        prior=PriorQualificationStateV2(
            camp_state=projected.camp_state,
            graduation_qualified=projected.graduation_qualified,
            graduation_qualified_at=projected.graduation_qualified_at,
            graduation_score_locked=projected.graduation_score_locked,
            gold_qualified=projected.gold_qualified,
            gold_qualified_at=projected.gold_qualified_at,
        ),
    )

    assert later.raw_total_score == Decimal("320")
    assert later.public_total_score == Decimal("200")
    assert later.graduation_qualified_at == OCCURRED_AT
    assert later.graduation_score_locked == Decimal("100")
    assert later.gold_qualified_at == OCCURRED_AT


def test_correction_can_retract_current_criteria_but_not_earned_facts() -> None:
    graduation_at = OCCURRED_AT - timedelta(days=20)
    gold_at = OCCURRED_AT - timedelta(days=10)
    prior = PriorQualificationStateV2(
        camp_state="GRADUATED",
        graduation_qualified=True,
        graduation_qualified_at=graduation_at,
        graduation_score_locked=100,
        gold_qualified=True,
        gold_qualified_at=gold_at,
    )

    projected = _rebuild(
        raw_total_score=0,
        mandatory_task_completed_count=0,
        l0_complaint_count=1,
        late_count=2,
        early_count=1,
        absent_count=1,
        prior=prior,
    )

    assert projected.graduation_criteria_met is False
    assert projected.gold_criteria_met is False
    assert projected.graduation_qualified is True
    assert projected.gold_qualified is True
    assert projected.graduation_qualified_at == graduation_at
    assert projected.gold_qualified_at == gold_at
    assert projected.graduation_score_locked == Decimal("100")
    assert projected.camp_state == "GRADUATED"
    assert projected.raw_total_score == Decimal("0")


def test_grant_gate_blocks_new_irreversible_facts_but_keeps_criteria() -> None:
    blocked = _rebuild(raw_total_score=250, grants_enabled=False)

    assert blocked.graduation_criteria_met is True
    assert blocked.gold_criteria_met is True
    assert blocked.graduation_qualified is False
    assert blocked.gold_qualified is False
    assert blocked.graduation_qualified_at is None
    assert blocked.gold_qualified_at is None
    assert blocked.graduation_score_locked is None
    assert blocked.camp_state == "IN_CAMP"

    earned_at = OCCURRED_AT - timedelta(days=1)
    retained = _rebuild(
        raw_total_score=0,
        grants_enabled=False,
        prior=PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=earned_at,
            graduation_score_locked=100,
            gold_qualified=True,
            gold_qualified_at=earned_at,
        ),
    )
    assert retained.graduation_qualified is True
    assert retained.gold_qualified is True
    assert retained.graduation_qualified_at == earned_at
    assert retained.gold_qualified_at == earned_at


@pytest.mark.parametrize("online_status", ["NEW", "EXISTING", "LEFT", "BLOCKED"])
def test_online_status_never_filters_score_or_qualification(
    online_status: str,
) -> None:
    projected = _rebuild(raw_total_score=250, online_status=online_status)

    assert projected.raw_total_score == Decimal("250")
    assert projected.graduation_qualified is True
    assert projected.gold_qualified is True


def test_thirty_days_is_not_a_graduation_deadline() -> None:
    onboarded_at = OCCURRED_AT - timedelta(days=365)

    projected = _rebuild(
        raw_total_score=100,
        onboarded_at=onboarded_at,
    )

    assert OCCURRED_AT - onboarded_at > timedelta(days=30)
    assert projected.graduation_criteria_met is True
    assert projected.graduation_qualified is True
    assert projected.camp_state == "GRADUATED"


def test_thirty_day_boundary_without_criteria_stays_in_camp_then_can_graduate() -> None:
    onboarded_at = OCCURRED_AT - timedelta(days=30)

    at_day_thirty = _rebuild(
        raw_total_score=99,
        onboarded_at=onboarded_at,
        online_status="EXISTING",
    )
    assert at_day_thirty.camp_state == "IN_CAMP"
    assert at_day_thirty.graduation_qualified is False

    after_day_thirty = _rebuild(
        raw_total_score=100,
        occurred_at=OCCURRED_AT + timedelta(days=10),
        onboarded_at=onboarded_at,
        online_status="LEFT",
        prior=_as_prior(at_day_thirty),
    )
    assert after_day_thirty.camp_state == "GRADUATED"
    assert after_day_thirty.graduation_qualified_at == OCCURRED_AT + timedelta(
        days=10
    )
    assert after_day_thirty.graduation_score_locked == Decimal("100")


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "mandatory_task_assignment_count": 8,
            "mandatory_task_completed_count": 8,
        },
        {"mandatory_task_completed_count": 8},
        {"l0_complaint_count": 1},
        {"l0_complaint_count": None},
    ],
)
def test_graduation_gates_fail_closed(overrides: dict[str, object]) -> None:
    projected = _rebuild(raw_total_score=250, **overrides)

    assert projected.graduation_criteria_met is False
    assert projected.gold_criteria_met is False
    assert projected.graduation_qualified is False
    assert projected.graduation_qualified_at is None
    assert projected.graduation_score_locked is None
    assert projected.camp_state == "IN_CAMP"


@pytest.mark.parametrize(
    "overrides",
    [
        {"late_count": 2},
        {"early_count": 1},
        {"absent_count": 1},
        {"late_count": None},
        {"early_count": None},
        {"absent_count": None},
    ],
)
def test_gold_attendance_gates_fail_closed_without_revoking_graduation(
    overrides: dict[str, object],
) -> None:
    projected = _rebuild(raw_total_score=250, **overrides)

    assert projected.graduation_criteria_met is True
    assert projected.graduation_qualified is True
    assert projected.gold_criteria_met is False
    assert projected.gold_qualified is False


def test_gold_late_boundary_allows_one_but_not_two() -> None:
    assert _rebuild(raw_total_score=200, late_count=1).gold_criteria_met is True
    assert _rebuild(raw_total_score=200, late_count=2).gold_criteria_met is False


@pytest.mark.parametrize(
    "raw_score",
    [-1, Decimal("NaN"), Decimal("Infinity"), float("nan"), True, "100"],
)
def test_invalid_raw_score_fails_closed(raw_score: object) -> None:
    with pytest.raises(
        DtsQualificationRuleError,
        match="^DTS_QUALIFICATION_RAW_SCORE_INVALID$",
    ):
        _rebuild(raw_total_score=raw_score)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mandatory_task_assignment_count": -1},
        {"mandatory_task_completed_count": 10},
        {"l0_complaint_count": False},
        {"late_count": 1.0},
        {"early_count": -1},
        {"absent_count": "0"},
    ],
)
def test_invalid_gate_counts_fail_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(DtsQualificationRuleError):
        _rebuild(**overrides)


@pytest.mark.parametrize(
    "prior",
    [
        PriorQualificationStateV2(camp_state="NOT_IN_CAMP"),
        PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=False,
        ),
        PriorQualificationStateV2(
            graduation_qualified=True,
            graduation_qualified_at=OCCURRED_AT,
            graduation_score_locked=100,
        ),
        PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=None,
            graduation_score_locked=100,
        ),
        PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=OCCURRED_AT,
            graduation_score_locked=None,
        ),
        PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=OCCURRED_AT,
            graduation_score_locked=99,
        ),
        PriorQualificationStateV2(
            gold_qualified=True,
            gold_qualified_at=OCCURRED_AT,
        ),
        PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=OCCURRED_AT,
            graduation_score_locked=100,
            gold_qualified=True,
            gold_qualified_at=OCCURRED_AT - timedelta(seconds=1),
        ),
    ],
)
def test_illegal_prior_state_fails_closed(
    prior: PriorQualificationStateV2,
) -> None:
    with pytest.raises(DtsQualificationRuleError):
        _rebuild(prior=prior)


def test_naive_occurred_at_and_prior_times_fail_closed() -> None:
    with pytest.raises(
        DtsQualificationRuleError,
        match="^DTS_QUALIFICATION_OCCURRED_AT_INVALID$",
    ):
        _rebuild(occurred_at=datetime(2026, 8, 22, 12, 0))

    with pytest.raises(
        DtsQualificationRuleError,
        match="^DTS_QUALIFICATION_PRIOR_GRADUATION_TIME_INVALID$",
    ):
        _rebuild(
            prior=PriorQualificationStateV2(
                camp_state="GRADUATED",
                graduation_qualified=True,
                graduation_qualified_at=datetime(2026, 8, 1, 12, 0),
                graduation_score_locked=100,
            )
        )


def test_new_gold_keeps_original_graduation_time() -> None:
    graduation_at = OCCURRED_AT - timedelta(days=40)
    projected = _rebuild(
        raw_total_score=250,
        prior=PriorQualificationStateV2(
            camp_state="GRADUATED",
            graduation_qualified=True,
            graduation_qualified_at=graduation_at,
            graduation_score_locked=100,
        ),
    )

    assert projected.graduation_qualified_at == graduation_at
    assert projected.gold_qualified_at == OCCURRED_AT
    assert projected.graduation_score_locked == Decimal("100")


def test_lifecycle_rebuild_preserves_first_qualification_facts_across_corrections() -> None:
    onboarded_at = OCCURRED_AT - timedelta(days=90)

    before_graduation = _rebuild(
        raw_total_score=Decimal("99.9"),
        online_status="EXISTING",
        onboarded_at=onboarded_at,
    )
    graduation_at = OCCURRED_AT + timedelta(days=1)
    graduated = _rebuild(
        raw_total_score=100,
        occurred_at=graduation_at,
        online_status="LEFT",
        onboarded_at=onboarded_at,
        prior=_as_prior(before_graduation),
    )

    corrected = _rebuild(
        raw_total_score=75,
        mandatory_task_completed_count=8,
        l0_complaint_count=1,
        occurred_at=OCCURRED_AT + timedelta(days=2),
        online_status="BLOCKED",
        onboarded_at=onboarded_at,
        prior=_as_prior(graduated),
    )
    gold_at = OCCURRED_AT + timedelta(days=3)
    gold = _rebuild(
        raw_total_score=225,
        occurred_at=gold_at,
        online_status="BLOCKED",
        onboarded_at=onboarded_at,
        prior=_as_prior(corrected),
    )
    corrected_again = _rebuild(
        raw_total_score=25,
        mandatory_task_completed_count=0,
        l0_complaint_count=2,
        late_count=3,
        early_count=1,
        absent_count=1,
        occurred_at=OCCURRED_AT + timedelta(days=4),
        online_status="LEFT",
        onboarded_at=onboarded_at,
        prior=_as_prior(gold),
    )

    assert corrected.graduation_criteria_met is False
    assert corrected.graduation_qualified is True
    assert corrected.graduation_qualified_at == graduation_at
    assert gold.gold_qualified is True
    assert gold.gold_qualified_at == gold_at
    assert corrected_again.raw_total_score == Decimal("25")
    assert corrected_again.public_total_score == Decimal("25")
    assert corrected_again.graduation_criteria_met is False
    assert corrected_again.gold_criteria_met is False
    assert corrected_again.graduation_qualified is True
    assert corrected_again.gold_qualified is True
    assert corrected_again.graduation_qualified_at == graduation_at
    assert corrected_again.gold_qualified_at == gold_at
    assert corrected_again.graduation_score_locked == Decimal("100")
    assert corrected_again.camp_state == "GRADUATED"


@pytest.mark.parametrize("online_status", ["LEFT", "BLOCKED"])
def test_non_active_online_status_allows_unbounded_growth_after_gold(
    online_status: str,
) -> None:
    earned_at = OCCURRED_AT - timedelta(days=5)
    prior = PriorQualificationStateV2(
        camp_state="GRADUATED",
        graduation_qualified=True,
        graduation_qualified_at=earned_at,
        graduation_score_locked=100,
        gold_qualified=True,
        gold_qualified_at=earned_at,
    )

    previous_raw = Decimal("200")
    for index, raw_score in enumerate(
        (Decimal("201"), Decimal("250.5"), Decimal("1000000000")),
        start=1,
    ):
        projected = _rebuild(
            raw_total_score=raw_score,
            online_status=online_status,
            occurred_at=OCCURRED_AT + timedelta(days=index),
            prior=prior,
        )

        assert projected.raw_total_score > previous_raw
        assert projected.raw_total_score == raw_score
        assert projected.public_total_score == Decimal("200")
        assert projected.graduation_qualified_at == earned_at
        assert projected.gold_qualified_at == earned_at
        assert projected.graduation_score_locked == Decimal("100")
        previous_raw = projected.raw_total_score
        prior = _as_prior(projected)


def test_projection_can_only_emit_the_two_confirmed_camp_states() -> None:
    for raw_score in (
        Decimal("0"),
        Decimal("99.99"),
        Decimal("100"),
        Decimal("999"),
    ):
        projected = _rebuild(raw_total_score=raw_score)

        assert projected.camp_state in {"IN_CAMP", "GRADUATED"}
        assert (projected.camp_state == "GRADUATED") is projected.graduation_qualified


@pytest.mark.parametrize(
    "online_status",
    ["left", "blocked", "OFF", "hei", "UNKNOWN"],
)
def test_unmapped_online_status_fails_closed(online_status: str) -> None:
    with pytest.raises(
        DtsQualificationRuleError,
        match="^DTS_QUALIFICATION_ONLINE_STATUS_INVALID$",
    ):
        _rebuild(raw_total_score=250, online_status=online_status)
