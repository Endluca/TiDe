"""Side-effect-free business rules shared by the future v2 projectors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


GradingClassification = Literal["POSITIVE", "NEGATIVE"]
TeacherOnlineState = Literal["NEW", "EXISTING", "LEFT", "BLOCKED"]
AbsenceTaskCode = Literal["P-REL-MEMO", "P-REL-ATTENDANCE"]


def classify_dom_grading(
    *,
    use_point: Any,
    score: Any,
    grading_type: Any,
) -> GradingClassification | None:
    """Classify the selected current DOM grading; unknown clears contribution."""

    normalized_use_point = (
        use_point.strip().lower() if isinstance(use_point, str) else None
    )
    normalized_type = (
        grading_type.strip().lower()
        if isinstance(grading_type, str)
        else None
    )
    if normalized_use_point == "buy":
        numeric_score = _integer_value(score)
        if numeric_score in {4, 5}:
            return "POSITIVE"
        if numeric_score in {1, 2}:
            return "NEGATIVE"
        return None
    if normalized_use_point == "free":
        if normalized_type == "satisfactory":
            return "POSITIVE"
        if normalized_type == "unsatisfactory":
            return "NEGATIVE"
    return None


def complaint_is_valid(row: Mapping[str, Any]) -> bool:
    """Apply the four confirmed complaint conditions, including NULL grandson."""

    raw_grandson = row.get("complaint_type_grandson")
    grandson = _integer_value(raw_grandson)
    if raw_grandson is not None and grandson is None:
        return False
    return (
        _integer_value(row.get("complaint_type")) == 13
        and grandson != 82
        and row.get("approve") == "y"
        and _integer_value(row.get("validity")) == 1
    )


def certification_is_tesol(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("certification_code")) == "16"
        and _integer_value(row.get("certification_status")) == 1
    )


def tesol_state(
    current_rows: Sequence[Mapping[str, Any]],
    *,
    scope_complete: bool,
) -> bool | None:
    """Return tri-state TESOL current fact from the complete active set."""

    if any(certification_is_tesol(row) for row in current_rows):
        return True
    return False if scope_complete else None


def center_type_description(center_type: Any) -> str:
    center_id = _integer_value(center_type)
    if center_id == 1:
        return "CBT"
    if center_id == 5:
        return "TBT"
    return "HBT"


@dataclass(frozen=True)
class TeacherOnlineClassification:
    state: TeacherOnlineState | None
    evidence_status: Literal["CONFIRMED", "SOURCE_MISSING"]


def classify_teacher_online_state(
    *,
    status: Any,
    status_on_time: Any,
    business_date: date,
) -> TeacherOnlineClassification:
    normalized_status = status.strip().lower() if isinstance(status, str) else None
    if normalized_status == "off":
        return TeacherOnlineClassification("LEFT", "CONFIRMED")
    if normalized_status == "hei":
        return TeacherOnlineClassification("BLOCKED", "CONFIRMED")
    if normalized_status != "on":
        return TeacherOnlineClassification(None, "SOURCE_MISSING")
    onboard_date = _date_value(status_on_time)
    if onboard_date is None:
        return TeacherOnlineClassification(None, "SOURCE_MISSING")
    job_day = (business_date - onboard_date).days
    if 0 <= job_day <= 29:
        return TeacherOnlineClassification("NEW", "CONFIRMED")
    if job_day >= 30:
        return TeacherOnlineClassification("EXISTING", "CONFIRMED")
    return TeacherOnlineClassification(None, "SOURCE_MISSING")


def no_notice_state(reason_type: Any) -> bool | None:
    if reason_type is None:
        return None
    if not isinstance(reason_type, str):
        return None
    return reason_type == "No Notification"


def absence_task_code(reason_type: Any) -> AbsenceTaskCode | None:
    """Map a confirmed absence reason to its single task rule.

    Whether the teacher participation is actually absent is intentionally not
    decided here.  Callers may materialize this result only after the reason
    has been uniquely attached to a ``t_absent`` participation.
    """

    if reason_type == "Unfilled Lesson Memo":
        return "P-REL-MEMO"
    if isinstance(reason_type, str) and reason_type != "":
        return "P-REL-ATTENDANCE"
    return None


def _integer_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    rendered = str(value)
    if not rendered or rendered.strip() != rendered:
        return None
    try:
        return int(rendered)
    except ValueError:
        return None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    rendered = value
    try:
        if len(rendered) == 10:
            return date.fromisoformat(rendered)
        return datetime.fromisoformat(
            rendered[:-1] + "+00:00" if rendered.endswith("Z") else rendered
        ).date()
    except ValueError:
        return None
