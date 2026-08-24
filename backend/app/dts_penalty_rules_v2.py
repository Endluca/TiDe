"""Pure v2 teacher-penalty mapping and tri-state aggregation rules.

The caller supplies participations from one source course and only aggregates
active penalty rows that were uniquely mapped by this module.  Source-current
persistence, dirty-key fan-out, and scope ownership stay outside this file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal


TeacherIdType = Literal["NUMERIC", "TEXT"]
EvidenceStatus = Literal["CONFIRMED", "SOURCE_MISSING"]
PenaltyMappingError = Literal[
    "PENDING_DATA:PENALTY_PARTICIPATION_TEACHER_MISSING",
    "PENDING_DATA:PENALTY_PARTICIPATION_AMBIGUOUS",
]

PENALTY_PARTICIPATION_TEACHER_MISSING: PenaltyMappingError = (
    "PENDING_DATA:PENALTY_PARTICIPATION_TEACHER_MISSING"
)
PENALTY_PARTICIPATION_AMBIGUOUS: PenaltyMappingError = (
    "PENDING_DATA:PENALTY_PARTICIPATION_AMBIGUOUS"
)


@dataclass(frozen=True)
class PenaltyParticipation:
    """The minimum participation interval needed to locate a penalty."""

    participation_seq: int
    teacher_id: str | None
    teacher_id_type: str | None
    assigned_at: object
    ended_at: object = None
    is_completion: bool = False

    def __post_init__(self) -> None:
        if self.participation_seq < 1:
            raise ValueError("participation_seq must be >= 1")


@dataclass(frozen=True)
class PenaltyRecord:
    """One active source penalty row after source-version selection."""

    teacher_id: str | None
    teacher_id_type: str | None
    lesson_start_time: object
    in_time: object
    out_time: object
    appeal_status: object


@dataclass(frozen=True)
class PenaltyParticipationResolution:
    participation_seq: int | None
    pending_error: PenaltyMappingError | None

    def __post_init__(self) -> None:
        if (self.participation_seq is None) == (self.pending_error is None):
            raise ValueError(
                "resolution must contain exactly one participation or error"
            )

    @property
    def is_mapped(self) -> bool:
        return self.participation_seq is not None


@dataclass(frozen=True)
class PenaltyFlags:
    is_late: bool | None
    is_early: bool | None
    late_evidence_status: EvidenceStatus
    early_evidence_status: EvidenceStatus

    def __post_init__(self) -> None:
        _validate_flag(self.is_late, self.late_evidence_status)
        _validate_flag(self.is_early, self.early_evidence_status)


def resolve_penalty_participation(
    penalty: PenaltyRecord,
    participations: Sequence[PenaltyParticipation],
) -> PenaltyParticipationResolution:
    """Map a penalty to one participation without guessing missing evidence."""

    penalty_teacher = _typed_teacher_identity(
        penalty.teacher_id,
        penalty.teacher_id_type,
    )
    if penalty_teacher is None:
        return PenaltyParticipationResolution(
            participation_seq=None,
            pending_error=PENALTY_PARTICIPATION_TEACHER_MISSING,
        )

    completion_matches = [
        participation
        for participation in participations
        if participation.is_completion
        and _typed_teacher_identity(
            participation.teacher_id,
            participation.teacher_id_type,
        )
        == penalty_teacher
    ]
    if len(completion_matches) == 1:
        return PenaltyParticipationResolution(
            participation_seq=completion_matches[0].participation_seq,
            pending_error=None,
        )
    if len(completion_matches) > 1:
        return _ambiguous_resolution()

    lesson_start = _aware_datetime(penalty.lesson_start_time)
    if lesson_start is None:
        return _ambiguous_resolution()

    same_teacher: list[PenaltyParticipation] = []
    identity_evidence_incomplete = False
    for participation in participations:
        participant_teacher = _typed_teacher_identity(
            participation.teacher_id,
            participation.teacher_id_type,
        )
        if participant_teacher is None:
            identity_evidence_incomplete = True
        elif participant_teacher == penalty_teacher:
            same_teacher.append(participation)
    if identity_evidence_incomplete:
        return _ambiguous_resolution()

    matches: list[PenaltyParticipation] = []
    interval_evidence_incomplete = False
    for participation in same_teacher:
        assigned_at = _aware_datetime(participation.assigned_at)
        if assigned_at is None:
            interval_evidence_incomplete = True
            continue
        if participation.ended_at is None:
            ended_at = None
        else:
            ended_at = _aware_datetime(participation.ended_at)
            if ended_at is None:
                interval_evidence_incomplete = True
                continue
        if ended_at is not None and ended_at < assigned_at:
            interval_evidence_incomplete = True
            continue
        if assigned_at <= lesson_start and (
            ended_at is None or lesson_start < ended_at
        ):
            matches.append(participation)

    if interval_evidence_incomplete or len(matches) != 1:
        return _ambiguous_resolution()
    return PenaltyParticipationResolution(
        participation_seq=matches[0].participation_seq,
        pending_error=None,
    )


def evaluate_penalty_record(penalty: PenaltyRecord) -> PenaltyFlags:
    """Calculate late/early for one uniquely mapped active penalty row."""

    if penalty.appeal_status is None:
        return _flags(None, None)
    if _appeal_status_is_two(penalty.appeal_status):
        return _flags(False, False)

    lesson_start = _aware_datetime(penalty.lesson_start_time)
    in_time = _aware_datetime(penalty.in_time)
    out_time = _aware_datetime(penalty.out_time)
    is_late = (
        None
        if lesson_start is None or in_time is None
        else in_time - lesson_start > timedelta(seconds=30)
    )
    is_early = (
        None
        if lesson_start is None or out_time is None
        else lesson_start + timedelta(minutes=30) - out_time
        > timedelta(seconds=30)
    )
    return _flags(is_late, is_early)


def aggregate_penalty_flags(
    flags: Iterable[PenaltyFlags],
    *,
    scope_complete: bool,
) -> PenaltyFlags:
    """Aggregate one participation's complete active penalty evidence set."""

    values = tuple(flags)
    return _flags(
        _aggregate_flag(
            (value.is_late for value in values),
            scope_complete=scope_complete,
        ),
        _aggregate_flag(
            (value.is_early for value in values),
            scope_complete=scope_complete,
        ),
    )


def aggregate_penalty_records(
    penalties: Iterable[PenaltyRecord],
    *,
    scope_complete: bool,
) -> PenaltyFlags:
    """Evaluate and aggregate the uniquely mapped active rows."""

    return aggregate_penalty_flags(
        (evaluate_penalty_record(penalty) for penalty in penalties),
        scope_complete=scope_complete,
    )


def _ambiguous_resolution() -> PenaltyParticipationResolution:
    return PenaltyParticipationResolution(
        participation_seq=None,
        pending_error=PENALTY_PARTICIPATION_AMBIGUOUS,
    )


def _typed_teacher_identity(
    teacher_id: object,
    teacher_id_type: object,
) -> tuple[TeacherIdType, str] | None:
    if teacher_id_type == "TEXT":
        if not isinstance(teacher_id, str) or not teacher_id:
            return None
        return "TEXT", teacher_id
    if teacher_id_type != "NUMERIC" or isinstance(teacher_id, bool):
        return None
    rendered_source = str(teacher_id)
    if not rendered_source or rendered_source.strip() != rendered_source:
        return None
    try:
        number = Decimal(rendered_source)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    return "NUMERIC", rendered


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value and value.strip() == value:
        rendered = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(rendered)
        except ValueError:
            return None
    else:
        return None
    try:
        return parsed if parsed.utcoffset() is not None else None
    except (OverflowError, ValueError):
        return None


def _appeal_status_is_two(value: object) -> bool:
    if isinstance(value, bool):
        return False
    rendered = str(value)
    if not rendered or rendered.strip() != rendered:
        return False
    try:
        numeric = Decimal(rendered)
    except (InvalidOperation, ValueError):
        return False
    return numeric.is_finite() and numeric == 2


def _aggregate_flag(
    values: Iterable[bool | None],
    *,
    scope_complete: bool,
) -> bool | None:
    observed = tuple(values)
    if any(value is True for value in observed):
        return True
    if any(value is None for value in observed):
        return None
    return False if scope_complete else None


def _flags(is_late: bool | None, is_early: bool | None) -> PenaltyFlags:
    return PenaltyFlags(
        is_late=is_late,
        is_early=is_early,
        late_evidence_status=(
            "CONFIRMED" if is_late is not None else "SOURCE_MISSING"
        ),
        early_evidence_status=(
            "CONFIRMED" if is_early is not None else "SOURCE_MISSING"
        ),
    )


def _validate_flag(
    value: bool | None,
    evidence_status: EvidenceStatus,
) -> None:
    if value is not None and not isinstance(value, bool):
        raise ValueError("penalty flag must be bool or None")
    expected = "CONFIRMED" if value is not None else "SOURCE_MISSING"
    if evidence_status != expected:
        raise ValueError("penalty flag and evidence status disagree")
