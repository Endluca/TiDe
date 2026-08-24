"""Pure v2 score display and irreversible qualification reconstruction.

The module intentionally has no database or worker dependency.  Scope and
online-status projection happen elsewhere: a teacher's ``LEFT``/``BLOCKED``
status and the end of the first 30 days never suppress score or qualification
facts here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


GRADUATION_RAW_SCORE_THRESHOLD = Decimal("100")
GOLD_RAW_SCORE_THRESHOLD = Decimal("200")
PUBLIC_TOTAL_SCORE_CAP = Decimal("200")
GRADUATION_SCORE_LOCK_VALUE = Decimal("100")
MANDATORY_TASK_EXPECTED_COUNT = 9

CAMP_STATES = frozenset({"IN_CAMP", "GRADUATED"})
ONLINE_STATUSES = frozenset({"NEW", "EXISTING", "LEFT", "BLOCKED"})


class DtsQualificationRuleError(ValueError):
    """The requested reconstruction cannot be proven internally consistent."""


@dataclass(frozen=True)
class PriorQualificationStateV2:
    """Only the irreversible facts required from the previous projection."""

    camp_state: str = "IN_CAMP"
    graduation_qualified: bool = False
    graduation_qualified_at: datetime | None = None
    graduation_score_locked: Decimal | int | float | None = None
    gold_qualified: bool = False
    gold_qualified_at: datetime | None = None


@dataclass(frozen=True)
class QualificationProjectionV2:
    """Current score/criteria plus irreversible qualification facts."""

    raw_total_score: Decimal
    public_total_score: Decimal
    graduation_score_threshold_met: bool
    gold_score_threshold_met: bool
    graduation_criteria_met: bool
    graduation_qualified: bool
    graduation_qualified_at: datetime | None
    graduation_score_locked: Decimal | None
    camp_state: str
    gold_criteria_met: bool
    gold_qualified: bool
    gold_qualified_at: datetime | None


def rebuild_teacher_qualification_v2(
    *,
    raw_total_score: Decimal | int | float,
    mandatory_task_assignment_count: int | None,
    mandatory_task_completed_count: int | None,
    l0_complaint_count: int | None,
    late_count: int | None,
    early_count: int | None,
    absent_count: int | None,
    occurred_at: datetime,
    grants_enabled: bool,
    prior: PriorQualificationStateV2 | None = None,
    online_status: str | None = None,
    onboarded_at: datetime | None = None,
) -> QualificationProjectionV2:
    """Rebuild current criteria while preserving all previously earned facts.

    ``None`` on a gate count means its evidence is incomplete, so that current
    criterion fails closed.  ``grants_enabled`` is the explicit cutover/
    reconciliation gate for creating new irreversible facts; it never revokes
    qualifications already present in ``prior``.  ``online_status`` and
    ``onboarded_at`` are validated context only; neither filters score nor
    creates a 30-day qualification deadline.
    """

    _require_aware_datetime(occurred_at, "DTS_QUALIFICATION_OCCURRED_AT_INVALID")
    if onboarded_at is not None:
        _require_aware_datetime(
            onboarded_at,
            "DTS_QUALIFICATION_ONBOARDED_AT_INVALID",
        )
        if onboarded_at > occurred_at:
            raise DtsQualificationRuleError(
                "DTS_QUALIFICATION_ONBOARDED_AT_IN_FUTURE"
            )
    if online_status is not None and online_status not in ONLINE_STATUSES:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_ONLINE_STATUS_INVALID"
        )
    if type(grants_enabled) is not bool:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_GRANT_GATE_INVALID"
        )

    raw_score = _non_negative_score(
        raw_total_score,
        "DTS_QUALIFICATION_RAW_SCORE_INVALID",
    )
    assignment_count = _optional_non_negative_count(
        mandatory_task_assignment_count,
        "DTS_QUALIFICATION_ASSIGNMENT_COUNT_INVALID",
    )
    completed_count = _optional_non_negative_count(
        mandatory_task_completed_count,
        "DTS_QUALIFICATION_COMPLETED_COUNT_INVALID",
    )
    if (
        assignment_count is not None
        and completed_count is not None
        and completed_count > assignment_count
    ):
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_COMPLETED_COUNT_EXCEEDS_ASSIGNMENTS"
        )
    l0_count = _optional_non_negative_count(
        l0_complaint_count,
        "DTS_QUALIFICATION_L0_COUNT_INVALID",
    )
    normalized_late_count = _optional_non_negative_count(
        late_count,
        "DTS_QUALIFICATION_LATE_COUNT_INVALID",
    )
    normalized_early_count = _optional_non_negative_count(
        early_count,
        "DTS_QUALIFICATION_EARLY_COUNT_INVALID",
    )
    normalized_absent_count = _optional_non_negative_count(
        absent_count,
        "DTS_QUALIFICATION_ABSENT_COUNT_INVALID",
    )

    previous = prior or PriorQualificationStateV2()
    previous_locked_score = _validate_prior(previous, occurred_at=occurred_at)

    graduation_score_threshold_met = (
        raw_score >= GRADUATION_RAW_SCORE_THRESHOLD
    )
    gold_score_threshold_met = raw_score >= GOLD_RAW_SCORE_THRESHOLD
    mandatory_tasks_complete = (
        assignment_count == MANDATORY_TASK_EXPECTED_COUNT
        and completed_count == MANDATORY_TASK_EXPECTED_COUNT
    )
    graduation_criteria_met = bool(
        graduation_score_threshold_met
        and mandatory_tasks_complete
        and l0_count == 0
    )
    attendance_gate_met = bool(
        normalized_late_count is not None
        and normalized_early_count is not None
        and normalized_absent_count is not None
        and normalized_late_count <= 1
        and normalized_early_count == 0
        and normalized_absent_count == 0
    )
    gold_criteria_met = bool(
        graduation_criteria_met
        and gold_score_threshold_met
        and attendance_gate_met
    )

    graduation_qualified = bool(
        previous.graduation_qualified
        or (grants_enabled and graduation_criteria_met)
    )
    gold_qualified = bool(
        previous.gold_qualified or (grants_enabled and gold_criteria_met)
    )
    if gold_qualified and not graduation_qualified:  # defensive invariant
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_GOLD_WITHOUT_GRADUATION"
        )

    graduation_qualified_at = previous.graduation_qualified_at
    graduation_score_locked = previous_locked_score
    if not previous.graduation_qualified and graduation_qualified:
        graduation_qualified_at = occurred_at
        graduation_score_locked = GRADUATION_SCORE_LOCK_VALUE

    gold_qualified_at = previous.gold_qualified_at
    if not previous.gold_qualified and gold_qualified:
        gold_qualified_at = occurred_at

    if (
        gold_qualified_at is not None
        and graduation_qualified_at is not None
        and gold_qualified_at < graduation_qualified_at
    ):
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_GOLD_TIME_BEFORE_GRADUATION"
        )

    return QualificationProjectionV2(
        raw_total_score=raw_score,
        public_total_score=min(raw_score, PUBLIC_TOTAL_SCORE_CAP),
        graduation_score_threshold_met=graduation_score_threshold_met,
        gold_score_threshold_met=gold_score_threshold_met,
        graduation_criteria_met=graduation_criteria_met,
        graduation_qualified=graduation_qualified,
        graduation_qualified_at=graduation_qualified_at,
        graduation_score_locked=graduation_score_locked,
        camp_state="GRADUATED" if graduation_qualified else "IN_CAMP",
        gold_criteria_met=gold_criteria_met,
        gold_qualified=gold_qualified,
        gold_qualified_at=gold_qualified_at,
    )


def _validate_prior(
    prior: PriorQualificationStateV2,
    *,
    occurred_at: datetime,
) -> Decimal | None:
    if not isinstance(prior, PriorQualificationStateV2):
        raise DtsQualificationRuleError("DTS_QUALIFICATION_PRIOR_INVALID")
    if prior.camp_state not in CAMP_STATES:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_CAMP_STATE_INVALID"
        )
    if type(prior.graduation_qualified) is not bool:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_GRADUATION_FLAG_INVALID"
        )
    if type(prior.gold_qualified) is not bool:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_GOLD_FLAG_INVALID"
        )
    if (prior.camp_state == "GRADUATED") is not prior.graduation_qualified:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_CAMP_GRADUATION_MISMATCH"
        )
    if prior.gold_qualified and not prior.graduation_qualified:
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_GOLD_WITHOUT_GRADUATION"
        )

    _validate_earned_time(
        earned=prior.graduation_qualified,
        earned_at=prior.graduation_qualified_at,
        occurred_at=occurred_at,
        code="DTS_QUALIFICATION_PRIOR_GRADUATION_TIME_INVALID",
    )
    _validate_earned_time(
        earned=prior.gold_qualified,
        earned_at=prior.gold_qualified_at,
        occurred_at=occurred_at,
        code="DTS_QUALIFICATION_PRIOR_GOLD_TIME_INVALID",
    )

    if prior.graduation_qualified:
        locked_score = _non_negative_score(
            prior.graduation_score_locked,
            "DTS_QUALIFICATION_PRIOR_GRADUATION_LOCK_INVALID",
        )
        if locked_score != GRADUATION_SCORE_LOCK_VALUE:
            raise DtsQualificationRuleError(
                "DTS_QUALIFICATION_PRIOR_GRADUATION_LOCK_INVALID"
            )
        locked_score = GRADUATION_SCORE_LOCK_VALUE
    else:
        if prior.graduation_score_locked is not None:
            raise DtsQualificationRuleError(
                "DTS_QUALIFICATION_PRIOR_GRADUATION_LOCK_INVALID"
            )
        locked_score = None

    if (
        prior.gold_qualified_at is not None
        and prior.graduation_qualified_at is not None
        and prior.gold_qualified_at < prior.graduation_qualified_at
    ):
        raise DtsQualificationRuleError(
            "DTS_QUALIFICATION_PRIOR_GOLD_TIME_BEFORE_GRADUATION"
        )
    return locked_score


def _validate_earned_time(
    *,
    earned: bool,
    earned_at: datetime | None,
    occurred_at: datetime,
    code: str,
) -> None:
    if earned is not (earned_at is not None):
        raise DtsQualificationRuleError(code)
    if earned_at is None:
        return
    _require_aware_datetime(earned_at, code)
    if earned_at > occurred_at:
        raise DtsQualificationRuleError(code)


def _require_aware_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DtsQualificationRuleError(code)
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise DtsQualificationRuleError(code) from exc
    if offset is None:
        raise DtsQualificationRuleError(code)
    return value


def _optional_non_negative_count(value: Any, code: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise DtsQualificationRuleError(code)
    return value


def _non_negative_score(value: Any, code: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise DtsQualificationRuleError(code)
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsQualificationRuleError(code) from exc
    if not normalized.is_finite() or normalized < 0:
        raise DtsQualificationRuleError(code)
    return Decimal("0") if normalized == 0 else normalized
