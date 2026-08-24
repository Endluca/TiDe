"""Deterministic v2 per-course score component planning.

The functions in this module do not write a ledger.  They turn the frozen
completion identity and current course evidence into an exact settlement
transition that a transactional worker can persist together with score
entries, accounts, and qualifications.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Literal, Mapping


ComponentCode = Literal[
    "FEEDBACK_PRAISE",
    "PERFECT_COMPLETED",
    "PEAK_COMPLETED",
    "CLASS_QUALITY_HARDWARE",
]
EvidenceStatus = Literal["CONFIRMED", "SOURCE_MISSING"]

COMPONENT_SCORES: Mapping[ComponentCode, Decimal] = {
    "FEEDBACK_PRAISE": Decimal("5"),
    "PERFECT_COMPLETED": Decimal("4"),
    "PEAK_COMPLETED": Decimal("2"),
    "CLASS_QUALITY_HARDWARE": Decimal("2"),
}


class LessonScoreRuleError(ValueError):
    """The requested component transition cannot be proven safe."""


class SettlementStatus(str, Enum):
    AWARDED = "AWARDED"
    REVERSED = "REVERSED"


class SettlementAction(str, Enum):
    NOOP = "NOOP"
    AWARD = "AWARD"
    REVERSE = "REVERSE"
    REPLACE = "REPLACE"


@dataclass(frozen=True)
class FrozenCompletionIdentityV2:
    source_region: Literal["dom", "ovs"]
    source_appoint_id: str
    completion_participation_seq: int
    teacher_id: str

    def __post_init__(self) -> None:
        if self.source_region not in {"dom", "ovs"}:
            raise LessonScoreRuleError("LESSON_SCORE_REGION_INVALID")
        if not isinstance(self.source_appoint_id, str) or not self.source_appoint_id:
            raise LessonScoreRuleError("LESSON_SCORE_APPOINT_ID_INVALID")
        if (
            isinstance(self.completion_participation_seq, bool)
            or not isinstance(self.completion_participation_seq, int)
            or self.completion_participation_seq < 1
        ):
            raise LessonScoreRuleError("LESSON_SCORE_COMPLETION_SEQ_INVALID")
        if not isinstance(self.teacher_id, str) or not self.teacher_id:
            raise LessonScoreRuleError("LESSON_SCORE_TEACHER_ID_INVALID")


@dataclass(frozen=True)
class LessonComponentConditionV2:
    component_code: ComponentCode
    should_award: bool | None
    evidence_status: EvidenceStatus
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        if self.component_code not in COMPONENT_SCORES:
            raise LessonScoreRuleError("LESSON_SCORE_COMPONENT_INVALID")
        if self.should_award is not None and type(self.should_award) is not bool:
            raise LessonScoreRuleError("LESSON_SCORE_CONDITION_INVALID")
        if self.evidence_status not in {"CONFIRMED", "SOURCE_MISSING"}:
            raise LessonScoreRuleError("LESSON_SCORE_EVIDENCE_STATUS_INVALID")
        if (self.should_award is None) is not (
            self.evidence_status == "SOURCE_MISSING"
        ):
            raise LessonScoreRuleError("LESSON_SCORE_EVIDENCE_MISMATCH")
        if (
            not isinstance(self.evidence_fingerprint, str)
            or len(self.evidence_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in self.evidence_fingerprint)
        ):
            raise LessonScoreRuleError("LESSON_SCORE_EVIDENCE_HASH_INVALID")


@dataclass(frozen=True)
class PriorLessonComponentSettlementV2:
    identity: FrozenCompletionIdentityV2
    component_code: ComponentCode
    status: SettlementStatus
    award_generation: int
    current_score: Decimal | int | float
    score_rule_version: str
    evidence_fingerprint: str
    current_award_score_entry_id: str | None
    last_reversal_score_entry_id: str | None = None

    def __post_init__(self) -> None:
        if self.component_code not in COMPONENT_SCORES:
            raise LessonScoreRuleError("LESSON_SCORE_COMPONENT_INVALID")
        if not isinstance(self.status, SettlementStatus):
            raise LessonScoreRuleError("LESSON_SCORE_STATUS_INVALID")
        if (
            isinstance(self.award_generation, bool)
            or not isinstance(self.award_generation, int)
            or self.award_generation < 1
        ):
            raise LessonScoreRuleError("LESSON_SCORE_GENERATION_INVALID")
        score = _score(self.current_score)
        object.__setattr__(self, "current_score", score)
        _require_rule_version(self.score_rule_version)
        _require_hash(self.evidence_fingerprint)
        if self.status == SettlementStatus.AWARDED:
            if not self.current_award_score_entry_id:
                raise LessonScoreRuleError("LESSON_SCORE_AWARD_ENTRY_MISSING")
            if (
                self.award_generation == 1
                and self.last_reversal_score_entry_id is not None
            ):
                raise LessonScoreRuleError(
                    "LESSON_SCORE_FIRST_AWARD_HAS_REVERSAL"
                )
            if (
                self.award_generation > 1
                and not self.last_reversal_score_entry_id
            ):
                raise LessonScoreRuleError(
                    "LESSON_SCORE_REAWARD_REVERSAL_MISSING"
                )
        elif self.current_award_score_entry_id is not None:
            raise LessonScoreRuleError("LESSON_SCORE_REVERSED_HAS_CURRENT_AWARD")
        elif not self.last_reversal_score_entry_id:
            raise LessonScoreRuleError("LESSON_SCORE_REVERSAL_ENTRY_MISSING")


@dataclass(frozen=True)
class LessonComponentSettlementPlanV2:
    action: SettlementAction
    identity: FrozenCompletionIdentityV2
    component_code: ComponentCode
    resulting_status: SettlementStatus
    award_generation: int
    score: Decimal
    score_rule_version: str
    evidence_fingerprint: str
    award_idempotency_key: str | None
    reversal_idempotency_key: str | None
    reversal_of_score_entry_id: str | None


def build_lesson_component_conditions_v2(
    *,
    source_region: Literal["dom", "ovs"],
    grading_classification: Literal["POSITIVE", "NEGATIVE"] | None,
    grading_evidence_complete: bool,
    late: bool | None,
    early: bool | None,
    attendance_evidence_complete: bool,
    completion_is_peak: bool | None,
    camera_off: bool | None,
    cpu_high: bool | None,
    network_high: bool | None,
) -> tuple[LessonComponentConditionV2, ...]:
    """Evaluate the four confirmed non-favorite per-course components.

    CPU/network currently have no source, so callers must pass ``None`` until
    replacement sources are published.  This necessarily keeps hardware
    evidence missing and prevents a new hardware award.
    """

    if source_region not in {"dom", "ovs"}:
        raise LessonScoreRuleError("LESSON_SCORE_REGION_INVALID")
    for value, code in (
        (grading_evidence_complete, "LESSON_SCORE_GRADING_SCOPE_INVALID"),
        (attendance_evidence_complete, "LESSON_SCORE_ATTENDANCE_SCOPE_INVALID"),
    ):
        if type(value) is not bool:
            raise LessonScoreRuleError(code)
    for value in (late, early, completion_is_peak, camera_off, cpu_high, network_high):
        if value is not None and type(value) is not bool:
            raise LessonScoreRuleError("LESSON_SCORE_TRI_STATE_INVALID")
    if grading_classification not in {"POSITIVE", "NEGATIVE", None}:
        raise LessonScoreRuleError("LESSON_SCORE_GRADING_INVALID")

    praise = _condition(
        "FEEDBACK_PRAISE",
        (
            grading_classification == "POSITIVE"
            if grading_evidence_complete and source_region == "dom"
            else False
            if grading_evidence_complete and source_region == "ovs"
            else None
        ),
        {
            "source_region": source_region,
            "grading_classification": grading_classification,
            "scope_complete": grading_evidence_complete,
        },
    )
    perfect_value: bool | None
    if not attendance_evidence_complete or late is None or early is None:
        perfect_value = None
    else:
        perfect_value = late is False and early is False
    perfect = _condition(
        "PERFECT_COMPLETED",
        perfect_value,
        {
            "late": late,
            "early": early,
            "scope_complete": attendance_evidence_complete,
        },
    )
    peak = _condition(
        "PEAK_COMPLETED",
        completion_is_peak,
        {"completion_is_peak": completion_is_peak},
    )
    hardware_evidence = (camera_off, cpu_high, network_high)
    if any(value is None for value in hardware_evidence):
        hardware_value: bool | None = None
    elif any(value is True for value in hardware_evidence):
        hardware_value = False
    else:
        hardware_value = True
    hardware = _condition(
        "CLASS_QUALITY_HARDWARE",
        hardware_value,
        {
            "camera_off": camera_off,
            "cpu_high": cpu_high,
            "network_high": network_high,
        },
    )
    return praise, perfect, peak, hardware


def plan_lesson_component_settlement_v2(
    *,
    identity: FrozenCompletionIdentityV2,
    condition: LessonComponentConditionV2,
    score_rule_version: str,
    prior: PriorLessonComponentSettlementV2 | None,
) -> LessonComponentSettlementPlanV2:
    """Plan one idempotent award/reversal transition for a frozen completion."""

    if not isinstance(identity, FrozenCompletionIdentityV2):
        raise LessonScoreRuleError("LESSON_SCORE_IDENTITY_INVALID")
    if not isinstance(condition, LessonComponentConditionV2):
        raise LessonScoreRuleError("LESSON_SCORE_CONDITION_INVALID")
    _require_rule_version(score_rule_version)
    score = COMPONENT_SCORES[condition.component_code]
    should_award = condition.should_award is True

    if prior is not None:
        if not isinstance(prior, PriorLessonComponentSettlementV2):
            raise LessonScoreRuleError("LESSON_SCORE_PRIOR_INVALID")
        if prior.identity != identity or prior.component_code != condition.component_code:
            raise LessonScoreRuleError("LESSON_SCORE_PRIOR_IDENTITY_MISMATCH")

    if prior is None:
        if should_award:
            return _award_plan(
                action=SettlementAction.AWARD,
                identity=identity,
                condition=condition,
                generation=1,
                score=score,
                score_rule_version=score_rule_version,
            )
        return _no_history_plan(
            identity=identity,
            condition=condition,
            score=score,
            score_rule_version=score_rule_version,
        )

    if prior.status == SettlementStatus.REVERSED:
        if should_award:
            return _award_plan(
                action=SettlementAction.AWARD,
                identity=identity,
                condition=condition,
                generation=prior.award_generation + 1,
                score=score,
                score_rule_version=score_rule_version,
            )
        return LessonComponentSettlementPlanV2(
            action=SettlementAction.NOOP,
            identity=identity,
            component_code=condition.component_code,
            resulting_status=SettlementStatus.REVERSED,
            award_generation=prior.award_generation,
            score=prior.current_score,
            score_rule_version=prior.score_rule_version,
            evidence_fingerprint=condition.evidence_fingerprint,
            award_idempotency_key=None,
            reversal_idempotency_key=None,
            reversal_of_score_entry_id=None,
        )

    assert prior.current_award_score_entry_id is not None
    if not should_award:
        return _reverse_plan(
            identity=identity,
            condition=condition,
            prior=prior,
            resulting_rule_version=score_rule_version,
        )

    unchanged = (
        prior.current_score == score
        and prior.score_rule_version == score_rule_version
        and prior.evidence_fingerprint == condition.evidence_fingerprint
    )
    if unchanged:
        return LessonComponentSettlementPlanV2(
            action=SettlementAction.NOOP,
            identity=identity,
            component_code=condition.component_code,
            resulting_status=SettlementStatus.AWARDED,
            award_generation=prior.award_generation,
            score=score,
            score_rule_version=score_rule_version,
            evidence_fingerprint=condition.evidence_fingerprint,
            award_idempotency_key=None,
            reversal_idempotency_key=None,
            reversal_of_score_entry_id=None,
        )

    # A rule/evidence-semantic change cannot mutate an append-only award.  The
    # worker must reverse the current entry and award the next generation in
    # the same transaction.
    award = _award_plan(
        action=SettlementAction.REPLACE,
        identity=identity,
        condition=condition,
        generation=prior.award_generation + 1,
        score=score,
        score_rule_version=score_rule_version,
    )
    reversal_idempotency_key = (
        f"lesson-reversal:{prior.current_award_score_entry_id}"
    )
    _require_idempotency_key(reversal_idempotency_key)
    return LessonComponentSettlementPlanV2(
        **{
            **award.__dict__,
            "reversal_idempotency_key": reversal_idempotency_key,
            "reversal_of_score_entry_id": prior.current_award_score_entry_id,
        }
    )


def favorite_score_is_current(status: str | None) -> bool:
    """Both confirmed and evidence-held favorite attributions retain +5."""

    return status in {"AWARDED", "AWARDED_PENDING_EVIDENCE"}


def lesson_awarded_score_v2(
    settlements: Mapping[ComponentCode, SettlementStatus | str],
    *,
    favorite_attribution_status: str | None,
) -> Decimal:
    """Sum only current awards; a negative grading never contributes a debit."""

    total = Decimal("5") if favorite_score_is_current(favorite_attribution_status) else Decimal("0")
    for component_code, status in settlements.items():
        if component_code not in COMPONENT_SCORES:
            raise LessonScoreRuleError("LESSON_SCORE_COMPONENT_INVALID")
        normalized = status.value if isinstance(status, SettlementStatus) else status
        if normalized not in {"AWARDED", "REVERSED"}:
            raise LessonScoreRuleError("LESSON_SCORE_STATUS_INVALID")
        if normalized == "AWARDED":
            total += COMPONENT_SCORES[component_code]
    return total


def _condition(
    component_code: ComponentCode,
    value: bool | None,
    evidence: Mapping[str, object],
) -> LessonComponentConditionV2:
    return LessonComponentConditionV2(
        component_code=component_code,
        should_award=value,
        evidence_status="SOURCE_MISSING" if value is None else "CONFIRMED",
        evidence_fingerprint=hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )


def _award_plan(
    *,
    action: SettlementAction,
    identity: FrozenCompletionIdentityV2,
    condition: LessonComponentConditionV2,
    generation: int,
    score: Decimal,
    score_rule_version: str,
) -> LessonComponentSettlementPlanV2:
    award_idempotency_key = (
        f"lesson:{identity.source_region}:{identity.source_appoint_id}:"
        f"p{identity.completion_participation_seq}:{condition.component_code}:"
        f"gen{generation}:{score_rule_version}"
    )
    _require_idempotency_key(award_idempotency_key)
    return LessonComponentSettlementPlanV2(
        action=action,
        identity=identity,
        component_code=condition.component_code,
        resulting_status=SettlementStatus.AWARDED,
        award_generation=generation,
        score=score,
        score_rule_version=score_rule_version,
        evidence_fingerprint=condition.evidence_fingerprint,
        award_idempotency_key=award_idempotency_key,
        reversal_idempotency_key=None,
        reversal_of_score_entry_id=None,
    )


def _reverse_plan(
    *,
    identity: FrozenCompletionIdentityV2,
    condition: LessonComponentConditionV2,
    prior: PriorLessonComponentSettlementV2,
    resulting_rule_version: str,
) -> LessonComponentSettlementPlanV2:
    assert prior.current_award_score_entry_id is not None
    reversal_idempotency_key = (
        f"lesson-reversal:{prior.current_award_score_entry_id}"
    )
    _require_idempotency_key(reversal_idempotency_key)
    return LessonComponentSettlementPlanV2(
        action=SettlementAction.REVERSE,
        identity=identity,
        component_code=condition.component_code,
        resulting_status=SettlementStatus.REVERSED,
        award_generation=prior.award_generation,
        score=prior.current_score,
        score_rule_version=resulting_rule_version,
        evidence_fingerprint=condition.evidence_fingerprint,
        award_idempotency_key=None,
        reversal_idempotency_key=reversal_idempotency_key,
        reversal_of_score_entry_id=prior.current_award_score_entry_id,
    )


def _no_history_plan(
    *,
    identity: FrozenCompletionIdentityV2,
    condition: LessonComponentConditionV2,
    score: Decimal,
    score_rule_version: str,
) -> LessonComponentSettlementPlanV2:
    # No settlement row needs to be materialized for never-awarded false or
    # unknown evidence.  generation=0 is an explicit plan sentinel, not a
    # legal persisted settlement generation.
    return LessonComponentSettlementPlanV2(
        action=SettlementAction.NOOP,
        identity=identity,
        component_code=condition.component_code,
        resulting_status=SettlementStatus.REVERSED,
        award_generation=0,
        score=score,
        score_rule_version=score_rule_version,
        evidence_fingerprint=condition.evidence_fingerprint,
        award_idempotency_key=None,
        reversal_idempotency_key=None,
        reversal_of_score_entry_id=None,
    )


def _score(value: Decimal | int | float) -> Decimal:
    if isinstance(value, bool):
        raise LessonScoreRuleError("LESSON_SCORE_VALUE_INVALID")
    try:
        score = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LessonScoreRuleError("LESSON_SCORE_VALUE_INVALID") from exc
    if not score.is_finite() or score < 0:
        raise LessonScoreRuleError("LESSON_SCORE_VALUE_INVALID")
    return score


def _require_rule_version(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(ch.isspace() for ch in value)
    ):
        raise LessonScoreRuleError("LESSON_SCORE_RULE_VERSION_INVALID")


def _require_idempotency_key(value: str) -> None:
    if not value or len(value) > 256:
        raise LessonScoreRuleError("LESSON_SCORE_IDEMPOTENCY_KEY_INVALID")


def _require_hash(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise LessonScoreRuleError("LESSON_SCORE_EVIDENCE_HASH_INVALID")
