"""Pure favorite relationship and 24-hour attribution rules for DTS v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable, Literal


TypedIdKind = Literal["NUMERIC", "TEXT"]


class FavoriteObservationStatus(str, Enum):
    PENDING = "PENDING"
    EVALUATING = "EVALUATING"
    CONFIRMED_TRUE = "CONFIRMED_TRUE"
    CONFIRMED_FALSE = "CONFIRMED_FALSE"
    WAITING_HISTORY = "WAITING_HISTORY"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"
    RETRY = "RETRY"
    DEAD = "DEAD"
    INVALIDATED = "INVALIDATED"
    VOIDED = "VOIDED"


class FavoriteCandidateDisposition(str, Enum):
    SELECTED = "SELECTED"
    NO_CANDIDATE = "NO_CANDIDATE"
    HOLD_CURRENT_ATTRIBUTION = "HOLD_CURRENT_ATTRIBUTION"


@dataclass(frozen=True)
class FavoriteSourceRecord:
    """One current favorite source row for a teacher/student pair."""

    source_id: str
    source_id_type: TypedIdKind
    teacher_id: str
    teacher_id_type: TypedIdKind
    student_token: str
    add_time: datetime | None
    source_timestamp: datetime | None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        source_type, source_id = _canonical_id(
            self.source_id_type,
            self.source_id,
        )
        if not self.teacher_id or not self.student_token:
            raise ValueError("favorite relationship identity is incomplete")
        if self.teacher_id_type not in {"NUMERIC", "TEXT"}:
            raise ValueError("favorite teacher id type is invalid")
        teacher_type, teacher_id = _canonical_id(
            self.teacher_id_type,
            self.teacher_id,
        )
        object.__setattr__(self, "teacher_id_type", teacher_type)
        object.__setattr__(self, "teacher_id", teacher_id)
        _require_aware_or_none(self.add_time, "add_time")
        _require_aware_or_none(self.source_timestamp, "source_timestamp")
        object.__setattr__(self, "source_id_type", source_type)
        object.__setattr__(self, "source_id", source_id)


@dataclass(frozen=True)
class FavoriteRelationshipCurrent:
    is_favorited: bool | None
    effective_time_evidence_status: Literal["CONFIRMED", "SOURCE_MISSING"]
    latest_business_effective_at: datetime | None


def rebuild_favorite_relationship_current(
    rows: Iterable[FavoriteSourceRecord],
    *,
    scope_complete: bool,
) -> FavoriteRelationshipCurrent:
    """Rebuild current favorite state without consulting any course."""

    current = tuple(rows)
    _require_same_pair(current)
    active = [row for row in current if not row.is_deleted]
    boundary_times: list[datetime] = []
    boundary_evidence_complete = True
    for row in current:
        if row.add_time is None:
            boundary_evidence_complete = False
        else:
            boundary_times.append(row.add_time)
        if row.is_deleted:
            if row.source_timestamp is None:
                boundary_evidence_complete = False
            else:
                boundary_times.append(row.source_timestamp)

    latest_business_effective_at = (
        max(boundary_times) if boundary_times else None
    )
    if active:
        return FavoriteRelationshipCurrent(
            is_favorited=True,
            effective_time_evidence_status=(
                "CONFIRMED" if boundary_evidence_complete else "SOURCE_MISSING"
            ),
            latest_business_effective_at=latest_business_effective_at,
        )
    return FavoriteRelationshipCurrent(
        is_favorited=False if scope_complete else None,
        effective_time_evidence_status=(
            "CONFIRMED"
            if scope_complete and boundary_evidence_complete
            else "SOURCE_MISSING"
        ),
        latest_business_effective_at=latest_business_effective_at,
    )


@dataclass(frozen=True)
class FavoriteInterval:
    """A reconstructed relationship interval using authoritative boundaries.

    ``end_at=None`` is a confirmed open interval only when
    ``end_evidence_confirmed`` is true.  A missing INSERT/UPDATE ``add_time`` or
    missing DELETE source timestamp must set the corresponding evidence flag
    false instead of substituting processing time.
    """

    teacher_id: str
    teacher_id_type: TypedIdKind
    student_token: str
    start_at: datetime | None
    end_at: datetime | None
    start_evidence_confirmed: bool
    end_evidence_confirmed: bool

    def __post_init__(self) -> None:
        if not self.teacher_id or not self.student_token:
            raise ValueError("favorite interval identity is incomplete")
        if self.teacher_id_type not in {"NUMERIC", "TEXT"}:
            raise ValueError("favorite teacher id type is invalid")
        teacher_type, teacher_id = _canonical_id(
            self.teacher_id_type,
            self.teacher_id,
        )
        object.__setattr__(self, "teacher_id_type", teacher_type)
        object.__setattr__(self, "teacher_id", teacher_id)
        _require_aware_or_none(self.start_at, "start_at")
        _require_aware_or_none(self.end_at, "end_at")
        if self.start_evidence_confirmed and self.start_at is None:
            raise ValueError("confirmed favorite start requires start_at")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError("favorite interval ends before it starts")


def favorite_interval_from_source_record(
    row: FavoriteSourceRecord,
) -> FavoriteInterval:
    """Build one interval without substituting DTS arrival/processing time.

    ``add_time`` is the authoritative INSERT/UPDATE start.  A DELETE ends the
    row at its DTS ``source_timestamp``.  Missing authoritative boundaries are
    retained as missing evidence for the observation evaluator.
    """

    return FavoriteInterval(
        teacher_id=row.teacher_id,
        teacher_id_type=row.teacher_id_type,
        student_token=row.student_token,
        start_at=row.add_time,
        end_at=row.source_timestamp if row.is_deleted else None,
        start_evidence_confirmed=row.add_time is not None,
        end_evidence_confirmed=(
            not row.is_deleted or row.source_timestamp is not None
        ),
    )


@dataclass(frozen=True)
class FavoriteObservationEvaluation:
    status: FavoriteObservationStatus
    relation_state: bool | None
    relation_evidence_status: Literal[
        "CONFIRMED",
        "SOURCE_MISSING",
        "HISTORY_INCOMPLETE",
    ]
    error_code: str | None = None


def favorite_observed_at(completion_end_time: datetime) -> datetime:
    if completion_end_time.utcoffset() is None:
        raise ValueError("completion_end_time must be timezone-aware")
    return completion_end_time + timedelta(hours=24)


def evaluate_favorite_at(
    intervals: Iterable[FavoriteInterval],
    *,
    observed_at: datetime,
    history_complete: bool,
) -> FavoriteObservationEvaluation:
    """Evaluate the relationship exactly at completion end + 24 hours."""

    if observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    rows = tuple(intervals)
    _require_same_interval_pair(rows)
    interval_states = tuple(
        _favorite_interval_state_at(row, observed_at=observed_at)
        for row in rows
    )
    has_confirmed_true = any(state is True for state in interval_states)
    if not has_confirmed_true and any(
        state is None for state in interval_states
    ):
        return FavoriteObservationEvaluation(
            status=FavoriteObservationStatus.WAITING_EVIDENCE,
            relation_state=None,
            relation_evidence_status="SOURCE_MISSING",
            error_code="SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING",
        )
    if not history_complete:
        return FavoriteObservationEvaluation(
            status=FavoriteObservationStatus.WAITING_HISTORY,
            relation_state=None,
            relation_evidence_status="HISTORY_INCOMPLETE",
            error_code="PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE",
        )
    is_favorited = has_confirmed_true
    return FavoriteObservationEvaluation(
        status=(
            FavoriteObservationStatus.CONFIRMED_TRUE
            if is_favorited
            else FavoriteObservationStatus.CONFIRMED_FALSE
        ),
        relation_state=is_favorited,
        relation_evidence_status="CONFIRMED",
    )


def relationship_change_requires_observation_rebuild(
    *,
    business_effective_at: datetime | None,
    observed_at: datetime,
) -> bool | None:
    """Return true for historical corrections, false for later current changes."""

    _require_aware_or_none(business_effective_at, "business_effective_at")
    if observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if business_effective_at is None:
        return None
    return business_effective_at <= observed_at


@dataclass(frozen=True)
class FavoriteCourseObservation:
    source_region: Literal["dom", "ovs"]
    source_appoint_id: str
    source_appoint_id_type: TypedIdKind
    observation_revision: int
    teacher_id: str
    teacher_id_type: TypedIdKind
    student_token: str
    completion_participation_seq: int
    observed_at: datetime
    status: FavoriteObservationStatus

    def __post_init__(self) -> None:
        if self.source_region not in {"dom", "ovs"}:
            raise ValueError("favorite source region is invalid")
        appoint_type, appoint_id = _canonical_id(
            self.source_appoint_id_type,
            self.source_appoint_id,
        )
        if self.observation_revision < 1 or self.completion_participation_seq < 1:
            raise ValueError("favorite observation revision/participation is invalid")
        if not self.teacher_id or not self.student_token:
            raise ValueError("favorite observation identity is incomplete")
        if self.teacher_id_type not in {"NUMERIC", "TEXT"}:
            raise ValueError("favorite teacher id type is invalid")
        teacher_type, teacher_id = _canonical_id(
            self.teacher_id_type,
            self.teacher_id,
        )
        object.__setattr__(self, "teacher_id_type", teacher_type)
        object.__setattr__(self, "teacher_id", teacher_id)
        if self.observed_at.utcoffset() is None:
            raise ValueError("favorite observed_at must be timezone-aware")
        object.__setattr__(self, "source_appoint_id_type", appoint_type)
        object.__setattr__(self, "source_appoint_id", appoint_id)


@dataclass(frozen=True)
class FavoriteCandidateSelection:
    disposition: FavoriteCandidateDisposition
    selected: FavoriteCourseObservation | None


def select_favorite_attribution_candidate(
    observations: Iterable[FavoriteCourseObservation],
    *,
    held_observation: FavoriteCourseObservation | None = None,
) -> FavoriteCandidateSelection:
    """Select one lifetime course for a teacher/student favorite award.

    A held award keeps its current course while that exact observation is
    waiting for evidence; absence of another confirmed candidate is not proof
    that the old award should be reversed.
    """

    rows = tuple(observations)
    _require_same_observation_pair(rows)
    if held_observation is not None:
        _require_same_observation_pair((*rows, held_observation))
        if held_observation.status in {
            FavoriteObservationStatus.PENDING,
            FavoriteObservationStatus.EVALUATING,
            FavoriteObservationStatus.WAITING_HISTORY,
            FavoriteObservationStatus.WAITING_EVIDENCE,
            FavoriteObservationStatus.RETRY,
            FavoriteObservationStatus.DEAD,
        }:
            return FavoriteCandidateSelection(
                disposition=FavoriteCandidateDisposition.HOLD_CURRENT_ATTRIBUTION,
                selected=held_observation,
            )
    candidate_rows = rows
    if (
        held_observation is not None
        and held_observation.status == FavoriteObservationStatus.CONFIRMED_TRUE
        and held_observation not in rows
    ):
        # Once held evidence becomes true again, the exact held observation is
        # a candidate even when the caller supplied it only via the hold slot.
        # Omitting it must never silently move the lifetime award to a later
        # course or turn it into NO_CANDIDATE.
        candidate_rows = (*rows, held_observation)
    confirmed = [
        row
        for row in candidate_rows
        if row.status == FavoriteObservationStatus.CONFIRMED_TRUE
    ]
    if not confirmed:
        return FavoriteCandidateSelection(
            disposition=FavoriteCandidateDisposition.NO_CANDIDATE,
            selected=None,
        )
    _require_one_appoint_id_type(confirmed)
    selected = min(
        confirmed,
        key=lambda row: (
            row.observed_at,
            _typed_sort_value(row.source_appoint_id_type, row.source_appoint_id),
            row.observation_revision,
        ),
    )
    return FavoriteCandidateSelection(
        disposition=FavoriteCandidateDisposition.SELECTED,
        selected=selected,
    )


def _require_same_pair(rows: tuple[FavoriteSourceRecord, ...]) -> None:
    pairs = {
        (row.teacher_id_type, row.teacher_id, row.student_token)
        for row in rows
    }
    if len(pairs) > 1:
        raise ValueError("favorite rows span more than one teacher/student pair")
    source_types = {row.source_id_type for row in rows}
    if len(source_types) > 1:
        raise ValueError("favorite source id type drift")


def _favorite_interval_state_at(
    row: FavoriteInterval,
    *,
    observed_at: datetime,
) -> bool | None:
    """Return the row's state when its known evidence already decides it.

    An unknown boundary only blocks an observation when that boundary could
    cross ``observed_at``.  A confirmed start after the observation or a
    confirmed end at/before it proves the row inactive without guessing the
    opposite boundary.
    """

    if (
        row.start_evidence_confirmed
        and row.start_at is not None
        and row.start_at > observed_at
    ):
        return False
    if (
        row.end_evidence_confirmed
        and row.end_at is not None
        and observed_at >= row.end_at
    ):
        return False
    if not row.start_evidence_confirmed or not row.end_evidence_confirmed:
        return None
    assert row.start_at is not None
    return True


def _require_same_interval_pair(rows: tuple[FavoriteInterval, ...]) -> None:
    pairs = {
        (row.teacher_id_type, row.teacher_id, row.student_token)
        for row in rows
    }
    if len(pairs) > 1:
        raise ValueError("favorite intervals span more than one pair")


def _require_same_observation_pair(
    rows: tuple[FavoriteCourseObservation, ...],
) -> None:
    pairs = {
        (
            row.source_region,
            row.teacher_id_type,
            row.teacher_id,
            row.student_token,
        )
        for row in rows
    }
    if len(pairs) > 1:
        raise ValueError("favorite observations span more than one pair")


def _require_one_appoint_id_type(
    rows: Iterable[FavoriteCourseObservation],
) -> None:
    kinds = {row.source_appoint_id_type for row in rows}
    if len(kinds) > 1:
        raise ValueError("appoint id type drift in one favorite route")


def _canonical_id(source_type: str, value: str) -> tuple[TypedIdKind, str]:
    normalized_type = source_type.upper() if isinstance(source_type, str) else ""
    if normalized_type == "TEXT":
        if not isinstance(value, str) or value == "":
            raise ValueError("text id is invalid")
        return "TEXT", value
    if normalized_type != "NUMERIC" or isinstance(value, bool):
        raise ValueError("id type is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric id is invalid") from exc
    if not number.is_finite():
        raise ValueError("numeric id is invalid")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "NUMERIC", "0" if rendered in {"", "-0"} else rendered


def _typed_sort_value(source_type: TypedIdKind, value: str) -> Decimal | bytes:
    return Decimal(value) if source_type == "NUMERIC" else value.encode("utf-8")


def _require_aware_or_none(value: datetime | None, field_name: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
