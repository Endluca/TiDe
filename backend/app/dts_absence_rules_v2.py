"""Deterministic absence-reason mapping for DTS v2 course participations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable, Protocol

from .dts_business_rules_v2 import absence_task_code, no_notice_state


class AbsenceMappingDisposition(str, Enum):
    MATCHED = "MATCHED"
    PENDING_DATA = "PENDING_DATA"
    NOT_CURRENT = "NOT_CURRENT"


class AbsenceParticipation(Protocol):
    participation_seq: int
    teacher_id: str
    teacher_id_type: str | None
    participation_status: str | None
    ended_at: datetime | None


@dataclass(frozen=True)
class AbsenceReasonFact:
    """One current/tombstone ``teacher_absent_reason`` source fact.

    Source adapters must normalize ``add_time`` into an aware datetime.  This
    rule layer deliberately does not guess the timezone of a naive database
    value.
    """

    source_reason_id: str
    source_reason_id_type: str
    source_row_revision: int
    teacher_id: str | None
    teacher_id_type: str | None
    reason_type: str | None
    add_time: datetime | None
    source_timestamp: datetime | None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        normalized_type, normalized_id = _canonical_source_id(
            self.source_reason_id,
            self.source_reason_id_type,
        )
        if self.source_row_revision < 1:
            raise ValueError("source_row_revision must be >= 1")
        if self.teacher_id is None:
            if self.teacher_id_type is not None:
                raise ValueError("teacher_id_type requires teacher_id")
        elif self.teacher_id == "" or self.teacher_id_type not in {
            "NUMERIC",
            "TEXT",
            None,
        }:
            raise ValueError("teacher identity is invalid")
        elif self.teacher_id_type is not None:
            teacher_id_type, teacher_id = _canonical_source_id(
                self.teacher_id,
                self.teacher_id_type,
            )
            object.__setattr__(self, "teacher_id_type", teacher_id_type)
            object.__setattr__(self, "teacher_id", teacher_id)
        _require_aware_or_none(self.add_time, "add_time")
        _require_aware_or_none(self.source_timestamp, "source_timestamp")
        object.__setattr__(self, "source_reason_id_type", normalized_type)
        object.__setattr__(self, "source_reason_id", normalized_id)

    @property
    def mapping_time(self) -> datetime | None:
        return self.add_time or self.source_timestamp


@dataclass(frozen=True)
class AbsenceReasonMapping:
    reason: AbsenceReasonFact
    disposition: AbsenceMappingDisposition
    participation_seq: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SelectedAbsenceReason:
    participation_seq: int
    teacher_id: str
    teacher_id_type: str | None
    reason: AbsenceReasonFact
    no_notice: bool | None
    task_code: str | None


@dataclass(frozen=True)
class AbsenceReasonSelection:
    selected: tuple[SelectedAbsenceReason, ...]
    pending: tuple[AbsenceReasonMapping, ...]

    def for_participation(
        self,
        participation_seq: int,
    ) -> SelectedAbsenceReason | None:
        return next(
            (
                item
                for item in self.selected
                if item.participation_seq == participation_seq
            ),
            None,
        )


def map_absence_reason_to_participation(
    reason: AbsenceReasonFact,
    participations: Iterable[AbsenceParticipation],
) -> AbsenceReasonMapping:
    """Map one reason to exactly one final ``t_absent`` participation."""

    if reason.is_deleted:
        return AbsenceReasonMapping(
            reason=reason,
            disposition=AbsenceMappingDisposition.NOT_CURRENT,
        )
    if reason.teacher_id is None or reason.mapping_time is None:
        return _pending(reason)

    candidates = [
        row
        for row in participations
        if row.participation_status == "t_absent"
        and (row.teacher_id_type, row.teacher_id)
        == (reason.teacher_id_type, reason.teacher_id)
    ]
    seqs = [row.participation_seq for row in candidates]
    if any(seq < 1 for seq in seqs) or len(seqs) != len(set(seqs)):
        return _pending(reason)
    # An undated matching absence could fall on either side of the reason.
    # Choosing a dated row in its presence would manufacture uniqueness.
    if not candidates or any(row.ended_at is None for row in candidates):
        return _pending(reason)
    if any(
        row.ended_at is not None and row.ended_at.utcoffset() is None
        for row in candidates
    ):
        return _pending(reason)

    mapping_time = reason.mapping_time
    assert mapping_time is not None
    preceding = [
        row
        for row in candidates
        if row.ended_at is not None and row.ended_at <= mapping_time
    ]
    if preceding:
        selected = min(
            preceding,
            key=lambda row: (-_epoch_microseconds(row.ended_at), row.participation_seq),
        )
    else:
        following = [
            row
            for row in candidates
            if row.ended_at is not None and row.ended_at > mapping_time
        ]
        if not following:
            return _pending(reason)
        selected = min(
            following,
            key=lambda row: (_epoch_microseconds(row.ended_at), row.participation_seq),
        )
    return AbsenceReasonMapping(
        reason=reason,
        disposition=AbsenceMappingDisposition.MATCHED,
        participation_seq=selected.participation_seq,
    )


def select_current_absence_reasons(
    reasons: Iterable[AbsenceReasonFact],
    participations: Iterable[AbsenceParticipation],
) -> AbsenceReasonSelection:
    """Select the latest current reason for every uniquely mapped absence.

    A selected row with ``reason_type=NULL`` intentionally suppresses an older
    non-null row for that participation.  DELETE/tombstones are excluded, so
    another current source row can become the selected reason.
    """

    reason_rows = tuple(reasons)
    active_types = {
        reason.source_reason_id_type
        for reason in reason_rows
        if not reason.is_deleted
    }
    if len(active_types) > 1:
        raise ValueError("absence reason source id type drift")
    participation_rows = tuple(participations)
    mapped: dict[int, list[AbsenceReasonFact]] = {}
    pending: list[AbsenceReasonMapping] = []
    for reason in reason_rows:
        result = map_absence_reason_to_participation(reason, participation_rows)
        if result.disposition == AbsenceMappingDisposition.MATCHED:
            assert result.participation_seq is not None
            mapped.setdefault(result.participation_seq, []).append(reason)
        elif result.disposition == AbsenceMappingDisposition.PENDING_DATA:
            pending.append(result)

    by_seq = {row.participation_seq: row for row in participation_rows}
    selected: list[SelectedAbsenceReason] = []
    for participation_seq in sorted(mapped):
        latest = max(mapped[participation_seq], key=_latest_reason_key)
        participation = by_seq[participation_seq]
        selected.append(
            SelectedAbsenceReason(
                participation_seq=participation_seq,
                teacher_id=participation.teacher_id,
                teacher_id_type=participation.teacher_id_type,
                reason=latest,
                no_notice=no_notice_state(latest.reason_type),
                task_code=absence_task_code(latest.reason_type),
            )
        )
    return AbsenceReasonSelection(
        selected=tuple(selected),
        pending=tuple(
            sorted(
                pending,
                key=lambda item: (
                    item.reason.source_reason_id_type,
                    _source_id_sort_value(item.reason),
                    item.reason.source_row_revision,
                ),
            )
        ),
    )


def _pending(reason: AbsenceReasonFact) -> AbsenceReasonMapping:
    return AbsenceReasonMapping(
        reason=reason,
        disposition=AbsenceMappingDisposition.PENDING_DATA,
        error_code="PENDING_DATA:ABSENCE_PARTICIPATION_AMBIGUOUS",
    )


def _latest_reason_key(reason: AbsenceReasonFact) -> tuple[object, ...]:
    return (
        reason.add_time is not None,
        reason.add_time or datetime.min.replace(tzinfo=timezone.utc),
        _source_id_sort_value(reason),
        reason.source_row_revision,
    )


def _source_id_sort_value(reason: AbsenceReasonFact) -> Decimal | bytes:
    if reason.source_reason_id_type == "NUMERIC":
        return Decimal(reason.source_reason_id)
    return reason.source_reason_id.encode("utf-8")


def _canonical_source_id(value: str, source_type: str) -> tuple[str, str]:
    normalized_type = source_type.upper() if isinstance(source_type, str) else ""
    if normalized_type == "TEXT":
        if not isinstance(value, str) or value == "":
            raise ValueError("text source id is invalid")
        return normalized_type, value
    if normalized_type != "NUMERIC" or isinstance(value, bool):
        raise ValueError("source id type is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric source id is invalid") from exc
    if not number.is_finite():
        raise ValueError("numeric source id is invalid")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return normalized_type, "0" if rendered in {"", "-0"} else rendered


def _require_aware_or_none(value: datetime | None, field_name: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _epoch_microseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)
