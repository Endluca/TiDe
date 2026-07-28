"""Read persisted teacher score projections without recalculating them."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from sqlalchemy import Engine, func, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    LessonDimensionScoreRecord,
    LessonFactRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TeacherMetricSnapshotRecord,
    TeacherRecord,
)
from .lesson_quality import is_perfect_lesson


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


class ScoreReadModelNotFound(LookupError):
    pass


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _lesson_business_facts(lesson: LessonFactRecord) -> dict[str, Any]:
    """Return the typed business facts that are safe for both consumers."""

    is_perfect = is_perfect_lesson(
        lesson_lifecycle_status=lesson.lesson_lifecycle_status,
        absence_reason_detail=lesson.absence_reason_detail,
        is_late=lesson.is_late,
        is_early=lesson.is_early,
    )
    return {
        "attendance": {
            "lesson_lifecycle_status": lesson.lesson_lifecycle_status,
            "is_late": lesson.is_late,
            "is_early": lesson.is_early,
            "is_false_early_leave": lesson.is_false_early_leave,
            "absence_reason_detail": lesson.absence_reason_detail,
        },
        "user_feedback": {
            "has_positive_feedback_tag": lesson.has_positive_feedback_tag,
            "positive_tag_value": lesson.positive_tag_value,
            "has_negative_feedback_tag": lesson.has_negative_feedback_tag,
            "negative_tag_values": deepcopy(lesson.negative_tag_values or []),
            "feedback_detail": lesson.feedback_detail,
            "is_favorited": lesson.is_favorited,
            "is_rebooked": lesson.is_rebooked,
            "is_blocked": lesson.is_blocked,
        },
        "classroom_quality": {
            "is_camera_off": lesson.is_camera_off,
            "is_cpu_usage_high": lesson.is_cpu_usage_high,
            "is_network_delay_high": lesson.is_network_delay_high,
            "is_perfect": is_perfect,
        },
        "capacity": {
            "is_peak": lesson.is_peak,
        },
        "complaint": {
            "category_l1": lesson.complaint_category_l1,
            "category_l2": lesson.complaint_category_l2,
            "category_l3": lesson.complaint_category_l3,
            "level": lesson.complaint_source_level,
            "route": lesson.complaint_route,
        },
    }


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

            snapshot = None
            if teacher.source_batch_id:
                snapshot = session.scalar(
                    select(TeacherMetricSnapshotRecord).where(
                        TeacherMetricSnapshotRecord.teacher_id == teacher_id,
                        TeacherMetricSnapshotRecord.batch_id
                        == teacher.source_batch_id,
                    )
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

            lesson_total = int(
                session.scalar(
                    select(func.count())
                    .select_from(LessonFactRecord)
                    .where(LessonFactRecord.teacher_id == teacher_id)
                )
                or 0
            )
            lesson_query = (
                select(LessonFactRecord)
                .where(LessonFactRecord.teacher_id == teacher_id)
                .order_by(
                    LessonFactRecord.lesson_local_date.desc(),
                    LessonFactRecord.lesson_local_time.desc(),
                    LessonFactRecord.lesson_id,
                )
                .offset((lesson_page - 1) * lesson_page_size)
                .limit(lesson_page_size)
            )
            lessons = list(session.scalars(lesson_query).all())
            lesson_ids = [item.lesson_id for item in lessons]
            lesson_scores = (
                list(
                    session.scalars(
                        select(LessonDimensionScoreRecord)
                        .where(
                            LessonDimensionScoreRecord.teacher_id == teacher_id,
                            LessonDimensionScoreRecord.lesson_id.in_(lesson_ids),
                        )
                        .order_by(
                            LessonDimensionScoreRecord.lesson_id,
                            LessonDimensionScoreRecord.dimension,
                        )
                    ).all()
                )
                if lesson_ids
                else []
            )
            scores_by_lesson: dict[
                str, list[LessonDimensionScoreRecord]
            ] = defaultdict(list)
            for row in lesson_scores:
                scores_by_lesson[row.lesson_id].append(row)

            calculated_at = max(
                [
                    item.calculated_at for item in components
                ]
                + [item.updated_at for item in dimensions],
                default=teacher.updated_at,
            )
            snapshot_payload = snapshot.score_policy_snapshot if snapshot else {}
            teacher_payload = teacher.payload if isinstance(teacher.payload, dict) else {}
            return {
                "teacher_id": teacher.teacher_id,
                "camp_enrollment_id": teacher.camp_enrollment_id,
                "score_rule_version": (
                    snapshot.score_rule_version
                    if snapshot
                    else teacher_payload.get("score_policy_version")
                ),
                "score_policy_sha256": (
                    snapshot.score_policy_sha256
                    if snapshot
                    else teacher_payload.get("score_policy_sha256")
                ),
                "raw_total_score": (
                    float(snapshot.raw_total_score)
                    if snapshot
                    else float(teacher.total_score)
                ),
                "public_total_score": (
                    float(snapshot.public_total_score)
                    if snapshot
                    else float(
                        teacher_payload.get(
                            "external_display_score",
                            teacher.total_score,
                        )
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
                    "teacher_batch_id": teacher.source_batch_id,
                    "lesson_batch_ids": sorted(
                        {
                            item.source_lesson_batch_id
                            for item in components
                            if item.source_lesson_batch_id
                        }
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
                    "items": [
                        {
                            "lesson_id": lesson.lesson_id,
                            "source_appoint_id": lesson.source_appoint_id,
                            "scheduled_start_at": _iso(
                                lesson.scheduled_start_at
                            ),
                            "lesson_local_date": (
                                lesson.lesson_local_date.isoformat()
                                if lesson.lesson_local_date
                                else None
                            ),
                            "lesson_local_time": (
                                lesson.lesson_local_time.isoformat()
                                if lesson.lesson_local_time
                                else None
                            ),
                            "status": lesson.lesson_lifecycle_status,
                            "valid_for_scoring": lesson.valid_for_scoring,
                            "evidence_status": lesson.evidence_status,
                            "business_facts": _lesson_business_facts(lesson),
                            "dimensions": [
                                {
                                    "code": score.dimension,
                                    "score": float(score.current_score),
                                    "evidence_status": score.evidence_status,
                                    "evidence_coverage": score.evidence_coverage,
                                    "business_facts": deepcopy(
                                        (score.payload or {}).get(
                                            "business_facts", []
                                        )
                                    ),
                                }
                                for score in sorted(
                                    scores_by_lesson[lesson.lesson_id],
                                    key=lambda item: DIMENSION_ORDER.get(
                                        item.dimension, 999
                                    ),
                                )
                            ],
                        }
                        for lesson in lessons
                    ],
                },
                "thresholds": deepcopy(
                    snapshot_payload.get("thresholds", {})
                    if isinstance(snapshot_payload, dict)
                    else {}
                ),
            }
