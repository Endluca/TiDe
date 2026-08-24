"""Pure current-set selectors for DTS v2 child facts.

The source ingestor stores every source row first.  These selectors rebuild a
business result from the complete current, non-deleted set; they never assume
that the latest delivered DTS event is the latest business row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

from .dts_business_rules_v2 import (
    GradingClassification,
    certification_is_tesol,
    classify_dom_grading,
    complaint_is_valid,
)


SourceIdType = Literal["NUMERIC", "TEXT"]


@dataclass(frozen=True)
class SelectorSourceIdentity:
    source_id: str
    source_id_type: SourceIdType
    source_row_revision: int

    def __post_init__(self) -> None:
        source_id_type, source_id = _canonical_id(
            self.source_id_type,
            self.source_id,
        )
        if self.source_row_revision < 1:
            raise ValueError("source_row_revision must be >= 1")
        object.__setattr__(self, "source_id_type", source_id_type)
        object.__setattr__(self, "source_id", source_id)


@dataclass(frozen=True)
class DomGradingFact:
    identity: SelectorSourceIdentity
    use_point: object
    score: object
    grading_type: object
    update_time: datetime | None = None
    create_time: datetime | None = None
    start_time: datetime | None = None
    dt: datetime | None = None
    is_del: object = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        _require_aware_times(
            update_time=self.update_time,
            create_time=self.create_time,
            start_time=self.start_time,
            dt=self.dt,
        )


@dataclass(frozen=True)
class SelectedDomGrading:
    fact: DomGradingFact
    classification: GradingClassification | None


def select_current_dom_grading(
    rows: Iterable[DomGradingFact],
) -> SelectedDomGrading | None:
    """Select one current DOM grading without a ``status`` predicate."""

    current = tuple(rows)
    _require_one_source_id_type(row.identity for row in current)
    active = [
        row
        for row in current
        if not row.is_deleted and _is_active_dom_grading(row.is_del)
    ]
    if not active:
        return None
    selected = max(
        active,
        key=lambda row: _latest_key(
            (
                row.update_time,
                row.create_time,
                row.start_time,
                row.dt,
            ),
            row.identity,
        ),
    )
    return SelectedDomGrading(
        fact=selected,
        classification=classify_dom_grading(
            use_point=selected.use_point,
            score=selected.score,
            grading_type=selected.grading_type,
        ),
    )


@dataclass(frozen=True)
class GradingLabelLogFact:
    identity: SelectorSourceIdentity
    label_id: str | None
    label_id_type: SourceIdType | None
    label_name: str | None
    create_time: datetime | None = None
    dt: datetime | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        _require_aware_times(create_time=self.create_time, dt=self.dt)
        if self.label_id is None:
            if self.label_id_type is not None:
                raise ValueError("label_id_type requires label_id")
            return
        if self.label_id_type is None:
            raise ValueError("label_id requires label_id_type")
        label_id_type, label_id = _canonical_id(
            self.label_id_type,
            self.label_id,
        )
        object.__setattr__(self, "label_id_type", label_id_type)
        object.__setattr__(self, "label_id", label_id)


@dataclass(frozen=True)
class SelectedCourseLabel:
    label_id: str
    label_id_type: SourceIdType
    label_name: str | None
    source_fact: GradingLabelLogFact


@dataclass(frozen=True)
class CourseLabelSelection:
    labels: tuple[SelectedCourseLabel, ...]
    pending_source_ids: tuple[SelectorSourceIdentity, ...]


def select_current_grading_labels(
    rows: Iterable[GradingLabelLogFact],
) -> CourseLabelSelection:
    """Deduplicate course labels by typed label id and select the latest name."""

    current = tuple(rows)
    _require_one_source_id_type(row.identity for row in current)
    active = [row for row in current if not row.is_deleted]
    label_id_types = {
        row.label_id_type
        for row in active
        if row.label_id is not None and row.label_id_type is not None
    }
    if len(label_id_types) > 1:
        raise ValueError("label id type drift in one source table")
    pending = tuple(
        sorted(
            (row.identity for row in active if row.label_id is None),
            key=_identity_key,
        )
    )
    grouped: dict[tuple[SourceIdType, str], list[GradingLabelLogFact]] = {}
    for row in active:
        if row.label_id is None or row.label_id_type is None:
            continue
        grouped.setdefault((row.label_id_type, row.label_id), []).append(row)

    labels: list[SelectedCourseLabel] = []
    for label_key in sorted(grouped, key=_typed_id_key):
        selected = max(
            grouped[label_key],
            key=lambda row: _latest_key(
                (row.create_time, row.dt),
                row.identity,
            ),
        )
        labels.append(
            SelectedCourseLabel(
                label_id=label_key[1],
                label_id_type=label_key[0],
                label_name=selected.label_name,
                source_fact=selected,
            )
        )
    return CourseLabelSelection(labels=tuple(labels), pending_source_ids=pending)


@dataclass(frozen=True)
class ComplaintFact:
    identity: SelectorSourceIdentity
    complaint_type: object
    complaint_type_grandson: object
    approve: object
    validity: object
    add_time: datetime | None = None
    course_date: date | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        _require_aware_times(add_time=self.add_time)
        if isinstance(self.course_date, datetime):
            raise ValueError("course_date must be a date, not a datetime")
        if self.course_date is not None and not isinstance(self.course_date, date):
            raise ValueError("course_date must be a date or None")

    def as_business_row(self) -> dict[str, object]:
        return {
            "complaint_type": self.complaint_type,
            "complaint_type_grandson": self.complaint_type_grandson,
            "approve": self.approve,
            "validity": self.validity,
        }


@dataclass(frozen=True)
class ComplaintSelection:
    valid_facts: tuple[ComplaintFact, ...]
    latest_valid: ComplaintFact | None


def select_current_complaints(
    rows: Iterable[ComplaintFact],
) -> ComplaintSelection:
    """Keep every valid complaint for EXISTS/count and one latest for routing."""

    current = tuple(rows)
    _require_one_source_id_type(row.identity for row in current)
    valid = [
        row
        for row in current
        if not row.is_deleted and complaint_is_valid(row.as_business_row())
    ]
    valid.sort(key=lambda row: _identity_key(row.identity))
    latest = (
        max(
            valid,
            key=lambda row: _latest_key(
                (row.add_time, row.course_date),
                row.identity,
            ),
        )
        if valid
        else None
    )
    return ComplaintSelection(valid_facts=tuple(valid), latest_valid=latest)


@dataclass(frozen=True)
class TeacherCertificationFact:
    identity: SelectorSourceIdentity
    certification_code: object
    certification_status: object
    is_deleted: bool = False


def select_current_tesol_state(
    rows: Iterable[TeacherCertificationFact],
    *,
    scope_complete: bool,
) -> bool | None:
    current = tuple(rows)
    _require_one_source_id_type(row.identity for row in current)
    if any(
        not row.is_deleted
        and certification_is_tesol(
            {
                "certification_code": row.certification_code,
                "certification_status": row.certification_status,
            }
        )
        for row in current
    ):
        return True
    return False if scope_complete else None


@dataclass(frozen=True)
class CloseCameraFact:
    identity: SelectorSourceIdentity
    is_deleted: bool = False


def select_current_close_camera_state(
    rows: Iterable[CloseCameraFact],
    *,
    scope_complete: bool,
) -> bool | None:
    current = tuple(rows)
    _require_one_source_id_type(row.identity for row in current)
    if any(not row.is_deleted for row in current):
        return True
    return False if scope_complete else None


def _is_active_dom_grading(is_del: object) -> bool:
    if is_del is None:
        return True
    if isinstance(is_del, bool):
        return is_del is False
    if isinstance(is_del, int):
        return is_del == 0
    if isinstance(is_del, Decimal):
        return is_del == 0
    if isinstance(is_del, str):
        return is_del == "0"
    return False


def _latest_key(
    values: tuple[datetime | date | None, ...],
    identity: SelectorSourceIdentity,
) -> tuple[object, ...]:
    key: list[object] = []
    for value in values:
        key.extend((value is not None, value or date.min))
    key.extend((_source_id_sort_value(identity), identity.source_row_revision))
    return tuple(key)


def _identity_key(identity: SelectorSourceIdentity) -> tuple[object, ...]:
    return (
        identity.source_id_type,
        _source_id_sort_value(identity),
        identity.source_row_revision,
    )


def _typed_id_key(value: tuple[SourceIdType, str]) -> tuple[object, ...]:
    source_id_type, source_id = value
    identity = SelectorSourceIdentity(source_id, source_id_type, 1)
    return source_id_type, _source_id_sort_value(identity)


def _require_one_source_id_type(
    identities: Iterable[SelectorSourceIdentity],
) -> None:
    source_id_types = {identity.source_id_type for identity in identities}
    if len(source_id_types) > 1:
        raise ValueError("source id type drift in one source table")


def _canonical_id(source_id_type: str, source_id: str) -> tuple[SourceIdType, str]:
    normalized_type = source_id_type.upper() if isinstance(source_id_type, str) else ""
    if normalized_type == "TEXT":
        if not isinstance(source_id, str) or source_id == "":
            raise ValueError("text source id is invalid")
        return "TEXT", source_id
    if normalized_type != "NUMERIC" or isinstance(source_id, bool):
        raise ValueError("source id type is invalid")
    try:
        number = Decimal(str(source_id))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric source id is invalid") from exc
    if not number.is_finite():
        raise ValueError("numeric source id is invalid")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "NUMERIC", "0" if rendered in {"", "-0"} else rendered


def _source_id_sort_value(identity: SelectorSourceIdentity) -> Decimal | bytes:
    if identity.source_id_type == "NUMERIC":
        return Decimal(identity.source_id)
    return identity.source_id.encode("utf-8")


def _require_aware_times(**values: datetime | None) -> None:
    for field_name, value in values.items():
        if value is not None and (
            not isinstance(value, datetime) or value.utcoffset() is None
        ):
            raise ValueError(f"{field_name} must be a timezone-aware datetime")
