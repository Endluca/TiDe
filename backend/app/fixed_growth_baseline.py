from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import (
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)


FIXED_GROWTH_TASK_CODES = tuple(f"G{number:02d}" for number in range(1, 11))
FIXED_GROWTH_TASK_CODE_SET = frozenset(FIXED_GROWTH_TASK_CODES)
FIXED_GROWTH_CREATOR_SYSTEM = "TRIGGER_CENTER"
FIXED_GROWTH_SOURCE_MODE = "REAL"
FIXED_GROWTH_TRIGGER_CODE = "NEW_TEACHER_CREATED"
DEFAULT_FIXED_GROWTH_ACTOR = "TRIGGER_CENTER:TEACHER_INGESTION"

_T = TypeVar("_T")


class FixedGrowthBaselineError(RuntimeError):
    """The fixed-growth baseline cannot be created without trusted configuration."""


@dataclass(frozen=True)
class FixedGrowthBaselineResult:
    teacher_count: int
    expected_assignment_count: int
    existing_assignment_count: int
    created_assignment_count: int


def _chunks(values: Sequence[_T], size: int = 500) -> Iterator[Sequence[_T]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _published_fixed_growth_templates(
    session: Session,
) -> dict[str, TaskTemplateRecord]:
    templates = list(
        session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.status == "PUBLISHED",
                TaskTemplateRecord.integration_mode == "INBOUND_STATUS_ONLY",
            )
        ).all()
    )
    templates_by_code = {template.template_id: template for template in templates}
    if (
        len(templates) != len(FIXED_GROWTH_TASK_CODES)
        or len(templates_by_code) != len(FIXED_GROWTH_TASK_CODES)
        or set(templates_by_code) != FIXED_GROWTH_TASK_CODE_SET
    ):
        raise FixedGrowthBaselineError(
            "FIXED_GROWTH_CATALOG_MUST_CONTAIN_EXACTLY_PUBLISHED_G01_G10"
        )

    total_points = 0.0
    for task_code in FIXED_GROWTH_TASK_CODES:
        template = templates_by_code[task_code]
        payload = template.payload if isinstance(template.payload, dict) else {}
        try:
            score_value = float(payload["score_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FixedGrowthBaselineError(
                f"FIXED_GROWTH_TEMPLATE_INVALID:{task_code}"
            ) from exc
        if (
            template.source_mode != FIXED_GROWTH_SOURCE_MODE
            or payload.get("template_id") != task_code
            or payload.get("dimension") != "NEW_TEACHER_TASK"
            or payload.get("score_type") != "FIXED"
            or not str(payload.get("why_template") or "").strip()
            or payload.get("priority") not in {"P0", "P1", "P2", "P3"}
            or score_value <= 0
        ):
            raise FixedGrowthBaselineError(
                f"FIXED_GROWTH_TEMPLATE_INVALID:{task_code}"
            )
        total_points += score_value
    if abs(total_points - 30.0) > 1e-9:
        raise FixedGrowthBaselineError("FIXED_GROWTH_TEMPLATE_TOTAL_MUST_EQUAL_30")
    return templates_by_code


def ensure_fixed_growth_assignments(
    session: Session,
    teacher_ids: Iterable[str],
    *,
    actor_id: str = DEFAULT_FIXED_GROWTH_ACTOR,
    occurred_at: datetime | None = None,
) -> FixedGrowthBaselineResult:
    """Idempotently ensure every new teacher has the complete G01-G10 baseline.

    This function is the reusable application boundary for the current workbook
    import and the future ``API_DAILY`` teacher upsert.  It creates task facts
    only; it deliberately creates no notification or outbound-delivery record.
    """

    normalized_teacher_ids = sorted(
        {
            str(teacher_id).strip()
            for teacher_id in teacher_ids
            if str(teacher_id).strip()
        }
    )
    if not normalized_teacher_ids:
        return FixedGrowthBaselineResult(0, 0, 0, 0)
    normalized_actor_id = actor_id.strip()
    if not normalized_actor_id:
        raise FixedGrowthBaselineError("FIXED_GROWTH_ACTOR_IS_REQUIRED")

    templates_by_code = _published_fixed_growth_templates(session)
    session.flush()

    stored_teacher_ids: set[str] = set()
    for teacher_id_chunk in _chunks(normalized_teacher_ids):
        stored_teacher_ids.update(
            session.scalars(
                select(TeacherRecord.teacher_id).where(
                    TeacherRecord.teacher_id.in_(teacher_id_chunk)
                )
            ).all()
        )
    missing_teacher_ids = set(normalized_teacher_ids) - stored_teacher_ids
    if missing_teacher_ids:
        raise FixedGrowthBaselineError(
            "FIXED_GROWTH_TEACHERS_NOT_FOUND:"
            + ",".join(sorted(missing_teacher_ids))
        )

    existing_by_key: dict[tuple[str, str], TaskAssignmentRecord] = {}
    for teacher_id_chunk in _chunks(normalized_teacher_ids):
        assignments = session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.teacher_id.in_(teacher_id_chunk),
                TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
            )
        ).all()
        for assignment in assignments:
            key = (assignment.teacher_id, assignment.task_code)
            if (
                assignment.task_code not in FIXED_GROWTH_TASK_CODE_SET
                or assignment.creator_system != FIXED_GROWTH_CREATOR_SYSTEM
                or assignment.source_mode != FIXED_GROWTH_SOURCE_MODE
                or assignment.dedupe_key
                != f"fixed:{assignment.teacher_id}:{assignment.task_code}"
                or key in existing_by_key
            ):
                raise FixedGrowthBaselineError(
                    "FIXED_GROWTH_BASELINE_CONTAINS_INCOMPATIBLE_ASSIGNMENT:"
                    f"{assignment.assignment_id}"
                )
            existing_by_key[key] = assignment

    now = occurred_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        # SQLite drops tzinfo when it round-trips a timezone-aware column in
        # the disposable test harness.  Stored ingestion timestamps are UTC.
        now = now.replace(tzinfo=timezone.utc)

    created_count = 0
    for teacher_id in normalized_teacher_ids:
        for task_code in FIXED_GROWTH_TASK_CODES:
            if (teacher_id, task_code) in existing_by_key:
                continue
            template = templates_by_code[task_code]
            payload = template.payload
            session.add(
                TaskAssignmentRecord(
                    assignment_id=f"FIXED:{teacher_id}:{task_code}",
                    teacher_id=teacher_id,
                    task_code=task_code,
                    template_version_id=template.row_id,
                    task_kind="FIXED_GROWTH",
                    creator_system=FIXED_GROWTH_CREATOR_SYSTEM,
                    status="ASSIGNED",
                    priority=str(payload["priority"]),
                    why=str(payload["why_template"]),
                    display_title=None,
                    evidence_snapshot={
                        "trigger_code": FIXED_GROWTH_TRIGGER_CODE,
                    },
                    due_at=None,
                    timezone_used=None,
                    timezone_source=None,
                    timezone_verified_at=None,
                    status_reason_code=None,
                    source_mode=FIXED_GROWTH_SOURCE_MODE,
                    dedupe_key=f"fixed:{teacher_id}:{task_code}",
                    created_by=normalized_actor_id,
                    updated_by=normalized_actor_id,
                    row_version=1,
                    assigned_at=now,
                    status_changed_at=now,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            created_count += 1

    return FixedGrowthBaselineResult(
        teacher_count=len(normalized_teacher_ids),
        expected_assignment_count=(
            len(normalized_teacher_ids) * len(FIXED_GROWTH_TASK_CODES)
        ),
        existing_assignment_count=len(existing_by_key),
        created_assignment_count=created_count,
    )


__all__ = [
    "DEFAULT_FIXED_GROWTH_ACTOR",
    "FIXED_GROWTH_CREATOR_SYSTEM",
    "FIXED_GROWTH_SOURCE_MODE",
    "FIXED_GROWTH_TASK_CODES",
    "FIXED_GROWTH_TRIGGER_CODE",
    "FixedGrowthBaselineError",
    "FixedGrowthBaselineResult",
    "ensure_fixed_growth_assignments",
]
