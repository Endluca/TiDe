"""Read persisted teacher score projections without recalculating them."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    ComplaintCategoryRuleRecord,
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from .lesson_quality import hardware_quality_passed, is_perfect_lesson
from .personalized_rules import normalize_text


DIMENSION_ORDER = {
    "USER_FEEDBACK": 0,
    "RELIABILITY": 1,
    "CLASS_QUALITY": 2,
    "CAPACITY": 3,
    "NEW_TEACHER_TASK": 4,
}
DIMENSION_LABELS = {
    "USER_FEEDBACK": "用户反馈",
    "RELIABILITY": "可靠性",
    "CLASS_QUALITY": "课堂质量",
    "CAPACITY": "供给达标（Peak slots）",
    "NEW_TEACHER_TASK": "成长任务（必修）",
}
_COMPLETED_LESSON_STATUSES = frozenset(
    {"已完课", "完课", "ended", "end", "completed", "complete", "finished"}
)
_LESSON_DIMENSION_COMPONENTS = {
    "USER_FEEDBACK": ("FEEDBACK_PRAISE", "FEEDBACK_FAVORITE"),
    "RELIABILITY": ("PERFECT_COMPLETED", "PEAK_COMPLETED"),
    "CLASS_QUALITY": ("CLASS_QUALITY_HARDWARE",),
}


class ScoreReadModelNotFound(LookupError):
    pass


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _source_lesson_is_completed(status: str | None) -> bool:
    return str(status or "").strip().casefold() in _COMPLETED_LESSON_STATUSES


def _evidence_status(statuses: list[str]) -> str:
    if not statuses:
        return "SOURCE_MISSING"
    if all(status == "CONFIRMED" for status in statuses):
        return "CONFIRMED"
    if all(status == "SOURCE_MISSING" for status in statuses):
        return "SOURCE_MISSING"
    return "PARTIAL"


def _source_lesson_dimensions(
    result: LessonScoreResultRecord,
) -> tuple[list[dict[str, Any]], list[str]]:
    persisted = result.dimensions if isinstance(result.dimensions, dict) else {}
    score_by_dimension = {
        "USER_FEEDBACK": result.user_feedback_score,
        "RELIABILITY": result.reliability_score,
        "CLASS_QUALITY": result.class_quality_score,
    }
    projected: list[dict[str, Any]] = []
    all_statuses: list[str] = []
    for dimension, component_codes in _LESSON_DIMENSION_COMPONENTS.items():
        raw_dimension = persisted.get(dimension)
        raw_dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
        raw_components = raw_dimension.get("components")
        raw_components = raw_components if isinstance(raw_components, list) else []
        components: list[dict[str, Any]] = []
        statuses: list[str] = []
        for index, component_code in enumerate(component_codes):
            raw_component = (
                raw_components[index]
                if index < len(raw_components)
                and isinstance(raw_components[index], dict)
                else {}
            )
            component = deepcopy(raw_component)
            component["code"] = component_code
            component_status = str(
                component.get("evidence_status") or "SOURCE_MISSING"
            )
            component["evidence_status"] = component_status
            components.append(component)
            statuses.append(component_status)
        all_statuses.extend(statuses)
        projected.append(
            {
                "code": dimension,
                "score": float(score_by_dimension[dimension]),
                "evidence_status": _evidence_status(statuses),
                "evidence_coverage": (
                    f"{sum(status == 'CONFIRMED' for status in statuses)}"
                    f"/{len(statuses)}"
                ),
                "business_facts": components,
            }
        )
    return projected, all_statuses


def _source_complaint_rules(
    session: Session,
    lessons: list[LessonSourceWideRecord],
) -> dict[str, ComplaintCategoryRuleRecord]:
    categories = {
        normalize_text(lesson.complaint_category_l3)
        for lesson in lessons
        if normalize_text(lesson.complaint_category_l3)
    }
    if not categories:
        return {}
    records = session.scalars(
        select(ComplaintCategoryRuleRecord)
        .where(
            ComplaintCategoryRuleRecord.category_l3_normalized.in_(categories)
        )
        .order_by(
            ComplaintCategoryRuleRecord.created_at.desc(),
            ComplaintCategoryRuleRecord.rule_id.desc(),
        )
    ).all()
    result: dict[str, ComplaintCategoryRuleRecord] = {}
    for record in records:
        result.setdefault(normalize_text(record.category_l3_normalized), record)
    return result


def _source_lesson_business_facts(
    lesson: LessonSourceWideRecord,
    complaint_rule: ComplaintCategoryRuleRecord | None,
) -> dict[str, Any]:
    is_perfect = is_perfect_lesson(
        lesson_lifecycle_status=lesson.lesson_status,
        is_late=lesson.is_late,
        is_early=lesson.is_early,
    )
    hardware_quality = hardware_quality_passed(
        is_camera_off=lesson.is_camera_off,
        is_cpu_usage_high=lesson.is_cpu_usage_high,
        is_network_delay_high=lesson.is_network_delay_high,
    )
    return {
        "attendance": {
            "lesson_lifecycle_status": lesson.lesson_status,
            "is_late": lesson.is_late,
            "is_early": lesson.is_early,
            "is_false_early_leave": lesson.is_false_early_leave,
            "absence_reason_detail": lesson.absence_reason_detail,
        },
        "user_feedback": {
            "has_positive_feedback_tag": lesson.has_positive_feedback_tag,
            "positive_tag_value": None,
            "has_negative_feedback_tag": lesson.has_negative_feedback_tag,
            "negative_tag_values": [],
            "feedback_detail": lesson.feedback_detail,
            "is_favorited": lesson.is_favorited,
            "is_rebooked": None,
            "is_blocked": lesson.is_blocked,
        },
        "classroom_quality": {
            "is_camera_off": lesson.is_camera_off,
            "is_cpu_usage_high": lesson.is_cpu_usage_high,
            "is_network_delay_high": lesson.is_network_delay_high,
            "hardware_quality_passed": hardware_quality,
            "is_perfect": is_perfect,
        },
        "capacity": {"is_peak": lesson.is_peak},
        "complaint": {
            "category_l1": lesson.complaint_category_l1,
            "category_l2": lesson.complaint_category_l2,
            "category_l3": lesson.complaint_category_l3,
            "level": complaint_rule.source_level if complaint_rule else None,
            "route": complaint_rule.default_route if complaint_rule else None,
        },
    }


def _source_lesson_page(
    session: Session,
    teacher_id: str,
    *,
    page: int,
    page_size: int,
) -> tuple[int, list[dict[str, Any]]]:
    lesson_join = (
        LessonScoreResultRecord.lesson_id == LessonSourceWideRecord.course_id
    )
    total = int(
        session.scalar(
            select(func.count())
            .select_from(LessonSourceWideRecord)
            .where(LessonSourceWideRecord.teacher_id == teacher_id)
        )
        or 0
    )
    rows = session.execute(
        select(LessonSourceWideRecord, LessonScoreResultRecord)
        .outerjoin(LessonScoreResultRecord, lesson_join)
        .where(LessonSourceWideRecord.teacher_id == teacher_id)
        .order_by(
            LessonSourceWideRecord.lesson_date.desc().nullslast(),
            LessonSourceWideRecord.lesson_time.desc().nullslast(),
            LessonSourceWideRecord.course_id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    lessons = [row[0] for row in rows]
    complaint_rules = _source_complaint_rules(session, lessons)
    items: list[dict[str, Any]] = []
    for lesson, result in rows:
        if result is None:
            dimensions: list[dict[str, Any]] = []
            component_statuses: list[str] = []
        else:
            dimensions, component_statuses = _source_lesson_dimensions(result)
        scheduled_start_at = (
            datetime.combine(
                lesson.lesson_date,
                lesson.lesson_time,
                tzinfo=timezone.utc,
            )
            if lesson.lesson_date is not None and lesson.lesson_time is not None
            else None
        )
        complaint_rule = complaint_rules.get(
            normalize_text(lesson.complaint_category_l3)
        )
        items.append(
            {
                "lesson_id": lesson.course_id,
                "source_appoint_id": lesson.course_id,
                "scheduled_start_at": _iso(scheduled_start_at),
                "lesson_local_date": _iso(lesson.lesson_date),
                "lesson_local_time": _iso(lesson.lesson_time),
                "status": (
                    str(lesson.lesson_status).strip()
                    if str(lesson.lesson_status or "").strip()
                    else "SOURCE_MISSING"
                ),
                "valid_for_scoring": _source_lesson_is_completed(
                    lesson.lesson_status
                ),
                "evidence_status": _evidence_status(component_statuses),
                "business_facts": _source_lesson_business_facts(
                    lesson,
                    complaint_rule,
                ),
                "dimensions": dimensions,
            }
        )
    return total, items


class ScoreReadService:
    def __init__(self, bind: Engine = default_engine) -> None:
        self.engine = bind

    def teacher_scorecard(
        self,
        teacher_id: str,
        *,
        lesson_page: int = 1,
        lesson_page_size: int = 12,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            teacher = session.get(TeacherRecord, teacher_id)
            if teacher is None:
                raise ScoreReadModelNotFound(teacher_id)

            teacher_source_present = (
                session.scalar(
                    select(TeacherSourceWideRecord.tchr_id).where(
                        TeacherSourceWideRecord.tchr_id == teacher_id
                    )
                )
                is not None
            )
            dimensions = list(
                session.scalars(
                    select(ScoreAccountRecord)
                    .where(ScoreAccountRecord.teacher_id == teacher_id)
                    .order_by(ScoreAccountRecord.dimension)
                ).all()
            )
            components = list(
                session.scalars(
                    select(ScoreComponentAccountRecord)
                    .where(ScoreComponentAccountRecord.teacher_id == teacher_id)
                    .order_by(
                        ScoreComponentAccountRecord.dimension,
                        ScoreComponentAccountRecord.component_code,
                    )
                ).all()
            )
            components_by_dimension: dict[
                str, list[ScoreComponentAccountRecord]
            ] = defaultdict(list)
            for component in components:
                components_by_dimension[component.dimension].append(component)

            lesson_total, lesson_items = _source_lesson_page(
                session,
                teacher_id,
                page=lesson_page,
                page_size=lesson_page_size,
            )

            calculated_at = max(
                [
                    item.calculated_at for item in components
                ]
                + [item.updated_at for item in dimensions],
                default=teacher.updated_at,
            )
            teacher_payload = (
                teacher.payload if isinstance(teacher.payload, dict) else {}
            )
            raw_thresholds = teacher_payload.get("thresholds")
            thresholds: dict[str, Any] = (
                deepcopy(raw_thresholds)
                if isinstance(raw_thresholds, dict)
                else {}
            )
            graduation_threshold = teacher_payload.get("graduation_threshold")
            if graduation_threshold is None:
                graduation_threshold = teacher.graduation_threshold
            thresholds.setdefault(
                "graduation_raw_score",
                float(graduation_threshold),
            )
            gold_threshold = teacher_payload.get("gold_threshold")
            if gold_threshold is not None:
                thresholds.setdefault("gold_raw_score", float(gold_threshold))
            return {
                "teacher_id": teacher.teacher_id,
                "camp_enrollment_id": teacher.camp_enrollment_id,
                "score_rule_version": (
                    teacher_payload.get("score_policy_version")
                    or teacher_payload.get("score_rule_version")
                ),
                "score_policy_sha256": teacher_payload.get(
                    "score_policy_sha256"
                ),
                "raw_total_score": float(teacher.total_score),
                "public_total_score": float(
                    teacher_payload.get(
                        "external_display_score",
                        teacher.total_score,
                    )
                ),
                "graduation_state": teacher.graduation_state,
                "graduation_qualified": teacher.graduation_state == "GRADUATED",
                "gold_qualified": bool(teacher.gold_qualified),
                "graduation_current_criteria_met": bool(
                    teacher_payload.get("graduation_criteria_met", False)
                ),
                "gold_current_criteria_met": bool(
                    teacher_payload.get("gold_criteria_met", False)
                ),
                "calculated_at": _iso(calculated_at),
                "source": {
                    "teacher_source_status": (
                        "CONFIRMED"
                        if teacher_source_present
                        else "SOURCE_MISSING"
                    ),
                    "score_projection_id": teacher_payload.get(
                        "score_projection_id"
                    ),
                },
                "dimensions": [
                    {
                        "code": dimension.dimension,
                        "label": DIMENSION_LABELS.get(
                            dimension.dimension,
                            dimension.dimension,
                        ),
                        "score": float(dimension.current_score),
                        "source_mode": (
                            (dimension.payload or {}).get(
                                "source_mode",
                                "UNKNOWN",
                            )
                        ),
                        "score_rule_version": dimension.score_rule_version,
                        "projection_revision": dimension.version,
                        "calculated_at": _iso(dimension.updated_at),
                        "components": [
                            {
                                "code": component.component_code,
                                "source_scope": component.source_scope,
                                "source_metric": component.source_metric,
                                "metric": component.source_metric,
                                "unit_count": float(component.unit_count),
                                "value": float(component.unit_count),
                                "points_per_unit": (
                                    float(component.points_per_unit)
                                    if component.points_per_unit is not None
                                    else None
                                ),
                                "score": float(component.current_score),
                                "source_mode": (
                                    (component.payload or {}).get(
                                        "source_mode",
                                        "PERSISTED_CURRENT",
                                    )
                                ),
                                "lesson_attributed_count": (
                                    component.lesson_attributed_count
                                ),
                                "lesson_attributed_score": float(
                                    component.lesson_attributed_score
                                ),
                                "unattributed_score": float(
                                    component.unattributed_score
                                ),
                                "reconciliation_status": (
                                    component.reconciliation_status
                                ),
                                "projection_revision": (
                                    component.projection_revision
                                ),
                            }
                            for component in components_by_dimension[
                                dimension.dimension
                            ]
                        ],
                    }
                    for dimension in sorted(
                        dimensions,
                        key=lambda item: DIMENSION_ORDER.get(
                            item.dimension, 999
                        ),
                    )
                ],
                "lessons": {
                    "page": lesson_page,
                    "page_size": lesson_page_size,
                    "total": lesson_total,
                    "items": lesson_items,
                },
                "thresholds": thresholds,
            }
