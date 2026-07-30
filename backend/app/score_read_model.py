"""Build the persisted score read model consumed by operations and teachers.

The source tables remain authoritative:

- teacher_metric_snapshots: current teacher-wide scoring inputs;
- lesson_facts: current typed lesson business facts;
- task_assignments/task_templates: mandatory-growth facts.

This module writes query projections only.  Rebuilding the same dependency
snapshot is deterministic and never invents lesson attribution that the lesson
source does not provide.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.orm import Session, load_only

from .config_models import (
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .lesson_quality import hardware_quality_passed, is_perfect_lesson
from .db_models import (
    LessonDimensionScoreRecord,
    LessonFactRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherMetricSnapshotRecord,
    TeacherRecord,
)
from .services import GrowthService
from .task_catalog import MANDATORY_TASK_CODES
from .score_projection_lock import acquire_score_projection_lock


DIMENSION_ORDER = (
    "USER_FEEDBACK",
    "RELIABILITY",
    "CLASS_QUALITY",
    "CAPACITY",
    "NEW_TEACHER_TASK",
)
LESSON_DIMENSIONS = frozenset(
    {"USER_FEEDBACK", "RELIABILITY", "CLASS_QUALITY"}
)
FIXED_GROWTH_CODES = MANDATORY_TASK_CODES
COMPLETED_LESSON_STATUSES = frozenset(
    {
        "已完课",
        "完课",
        "ended",
        "end",
        "completed",
        "complete",
        "finished",
    }
)
PROJECTION_INSERT_BATCH_SIZE = 2_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _completed_lesson(value: str | None) -> bool:
    return str(value or "").strip().casefold() in COMPLETED_LESSON_STATUSES


def _hardware_quality_summary(
    lessons: Iterable[LessonFactRecord],
) -> dict[str, Any]:
    results = [
        hardware_quality_passed(
            is_camera_off=lesson.is_camera_off,
            is_cpu_usage_high=lesson.is_cpu_usage_high,
            is_network_delay_high=lesson.is_network_delay_high,
        )
        for lesson in lessons
    ]
    return {
        "count": sum(result is True for result in results),
        "source_mode": (
            "DERIVED_REAL"
            if all(result is not None for result in results)
            else "SOURCE_MISSING"
        ),
    }


def _perfect_completion_summary(
    lessons: Iterable[LessonFactRecord],
) -> dict[str, Any]:
    lesson_rows = list(lessons)
    appoint_ids = {
        lesson.source_appoint_id
        for lesson in lesson_rows
        if lesson.source_appoint_id
        and is_perfect_lesson(
            lesson_lifecycle_status=lesson.lesson_lifecycle_status,
            is_late=lesson.is_late,
            is_early=lesson.is_early,
        )
    }
    evidence_complete = all(
        bool(lesson.lesson_lifecycle_status)
        and lesson.is_late is not None
        and lesson.is_early is not None
        and bool(lesson.source_appoint_id)
        for lesson in lesson_rows
    )
    return {
        "count": len(appoint_ids),
        "source_mode": (
            "DERIVED_REAL" if evidence_complete else "SOURCE_MISSING"
        ),
    }


def _source_mode(payload: dict[str, Any] | None, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    return str(payload.get("source_mode") or payload.get("data_mode") or default)


def _score_config(
    session: Session,
    payload: dict[str, Any] | None,
    version_id: str | None,
) -> tuple[dict[str, Any], str]:
    if payload is not None:
        normalized = ScoreGraduationConfig.model_validate(payload).model_dump(mode="json")
        return normalized, version_id or str(normalized["policy_version"])
    record = session.scalar(
        select(ConfigVersionRecord)
        .where(
            ConfigVersionRecord.config_key == ConfigKey.SCORE_GRADUATION.value,
            ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
        )
        .order_by(ConfigVersionRecord.version_number.desc())
    )
    if record is None:
        # Standalone import/test databases may intentionally omit the config
        # center. The frozen current policy remains the deterministic fallback;
        # runtime PostgreSQL normally resolves the published row above.
        from .teacher_data_import import SCORE_POLICY_SNAPSHOT

        normalized = ScoreGraduationConfig.model_validate(
            SCORE_POLICY_SNAPSHOT
        ).model_dump(mode="json")
        return normalized, f"FROZEN_DEFAULT:{normalized['policy_version']}"
    normalized = ScoreGraduationConfig.model_validate(record.payload).model_dump(mode="json")
    return normalized, record.version_id


def _account_overrides(
    accounts: Iterable[ScoreAccountRecord],
    assignments: list[TaskAssignmentRecord],
    templates: dict[str, TaskTemplateRecord],
) -> dict[str, dict[str, Any]]:
    result = {
        item.dimension: {
            "score": float(item.current_score),
            "source_mode": _source_mode(item.payload, "PERSISTED_ACCOUNT"),
        }
        for item in accounts
    }
    fixed = [
        item
        for item in assignments
        if item.task_code in FIXED_GROWTH_CODES
        and item.task_kind == "FIXED_GROWTH"
        and item.creator_system == "TRIGGER_CENTER"
    ]
    task_components = _task_component_rows(fixed, templates)
    task = result.setdefault("NEW_TEACHER_TASK", {"score": 0.0})
    task.update(
        score=round(
            sum(float(item["score"]) for item in task_components),
            2,
        ),
        assignment_count=len({item.task_code for item in fixed}),
        completed_count=sum(item.status == "COMPLETED" for item in fixed),
        expected_count=len(FIXED_GROWTH_CODES),
        source_mode="SYSTEM_TASK_STATUS",
    )
    return result


def _points_by_component(dimensions: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for dimension in dimensions:
        for component in dimension.get("components") or []:
            code = str(component.get("code") or "")
            if not code:
                continue
            points = component.get("points_per_unit")
            if points is not None:
                result[code] = float(points)
    return result


def _lesson_component_payloads(
    lessons: list[LessonFactRecord],
    *,
    points: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """Return one persisted row per lesson/dimension and attribution totals."""

    rows: list[dict[str, Any]] = []
    attributed: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0.0, "score": 0.0}
    )
    favorite_credit: set[str] = set()
    ordered_lessons = sorted(
        lessons,
        key=lambda item: (
            item.lesson_local_date or datetime.min.date(),
            item.lesson_local_time or datetime.min.time(),
            item.lesson_id,
        ),
    )
    for lesson in ordered_lessons:
        is_completed = _completed_lesson(lesson.lesson_lifecycle_status)
        peak_known = bool(lesson.lesson_lifecycle_status) and lesson.is_peak is not None
        peak_completed = is_completed and lesson.is_peak is True

        praise_known = lesson.has_positive_feedback_tag is not None
        praise = is_completed and lesson.has_positive_feedback_tag is True
        favorite_known = lesson.is_favorited is not None
        favorite_source_hit = is_completed and lesson.is_favorited is True
        favorite_key = lesson.student_id_hash or f"lesson:{lesson.lesson_id}"
        favorite = favorite_source_hit and favorite_key not in favorite_credit
        if favorite:
            favorite_credit.add(favorite_key)
        hardware_quality = hardware_quality_passed(
            is_camera_off=lesson.is_camera_off,
            is_cpu_usage_high=lesson.is_cpu_usage_high,
            is_network_delay_high=lesson.is_network_delay_high,
        )
        perfect_known = (
            bool(lesson.lesson_lifecycle_status)
            and lesson.is_late is not None
            and lesson.is_early is not None
        )
        is_perfect = is_perfect_lesson(
            lesson_lifecycle_status=lesson.lesson_lifecycle_status,
            is_late=lesson.is_late,
            is_early=lesson.is_early,
        )
        component_sets = {
            "USER_FEEDBACK": [
                {
                    "code": "FEEDBACK_PRAISE",
                    "business_fact": "has_positive_feedback_tag",
                    "fact_value": lesson.has_positive_feedback_tag,
                    "awarded": praise,
                    "points_per_unit": points.get("FEEDBACK_PRAISE", 0.0),
                    "score": points.get("FEEDBACK_PRAISE", 0.0) if praise else 0.0,
                    "evidence_status": "CONFIRMED" if praise_known else "SOURCE_MISSING",
                },
                {
                    "code": "FEEDBACK_FAVORITE",
                    "business_fact": "is_favorited",
                    "fact_value": lesson.is_favorited,
                    "awarded": favorite,
                    "points_per_unit": points.get("FEEDBACK_FAVORITE", 0.0),
                    "score": points.get("FEEDBACK_FAVORITE", 0.0) if favorite else 0.0,
                    "evidence_status": "CONFIRMED" if favorite_known else "SOURCE_MISSING",
                    "dedupe": {
                        "key": "student_id_hash",
                        "credited_on_this_lesson": favorite,
                        "source_hit": favorite_source_hit,
                    },
                },
            ],
            "RELIABILITY": (
                [
                    {
                        "code": "PERFECT_COMPLETED",
                        "business_fact": "is_perfect",
                        "fact_value": (
                            is_perfect if perfect_known else None
                        ),
                        "awarded": is_perfect,
                        "points_per_unit": points.get(
                            "PERFECT_COMPLETED", 0.0
                        ),
                        "score": (
                            points.get("PERFECT_COMPLETED", 0.0)
                            if is_perfect
                            else 0.0
                        ),
                        "evidence_status": (
                            "CONFIRMED"
                            if perfect_known
                            else "SOURCE_MISSING"
                        ),
                        "inputs": {
                            "lesson_lifecycle_status": (
                                lesson.lesson_lifecycle_status
                            ),
                            "is_late": lesson.is_late,
                            "is_early": lesson.is_early,
                        },
                    },
                ]
                if "PERFECT_COMPLETED" in points
                else [
                    {
                        "code": "ON_TIME_COMPLETED",
                        "business_fact": (
                            "completed_without_late_or_early"
                        ),
                        "fact_value": (
                            is_completed
                            and lesson.is_late is False
                            and lesson.is_early is False
                            if bool(lesson.lesson_lifecycle_status)
                            and lesson.is_late is not None
                            and lesson.is_early is not None
                            else None
                        ),
                        "awarded": (
                            is_completed
                            and lesson.is_late is False
                            and lesson.is_early is False
                        ),
                        "points_per_unit": points.get(
                            "ON_TIME_COMPLETED", 0.0
                        ),
                        "score": (
                            points.get("ON_TIME_COMPLETED", 0.0)
                            if is_completed
                            and lesson.is_late is False
                            and lesson.is_early is False
                            else 0.0
                        ),
                        "evidence_status": (
                            "CONFIRMED"
                            if bool(lesson.lesson_lifecycle_status)
                            and lesson.is_late is not None
                            and lesson.is_early is not None
                            else "SOURCE_MISSING"
                        ),
                        "inputs": {
                            "lesson_lifecycle_status": (
                                lesson.lesson_lifecycle_status
                            ),
                            "is_late": lesson.is_late,
                            "is_early": lesson.is_early,
                        },
                    },
                ]
            )
            + [
                {
                    "code": "PEAK_COMPLETED",
                    "business_fact": "completed_in_peak_period",
                    "fact_value": peak_completed if peak_known else None,
                    "awarded": peak_completed,
                    "points_per_unit": points.get("PEAK_COMPLETED", 0.0),
                    "score": points.get("PEAK_COMPLETED", 0.0) if peak_completed else 0.0,
                    "evidence_status": "CONFIRMED" if peak_known else "SOURCE_MISSING",
                    "inputs": {
                        "lesson_lifecycle_status": lesson.lesson_lifecycle_status,
                        "is_peak": lesson.is_peak,
                    },
                },
            ],
            "CLASS_QUALITY": (
                [
                    {
                        "code": "CLASS_QUALITY_HARDWARE",
                        "business_fact": "hardware_quality_passed",
                        "fact_value": hardware_quality,
                        "awarded": hardware_quality is True,
                        "points_per_unit": points.get(
                            "CLASS_QUALITY_HARDWARE", 0.0
                        ),
                        "score": (
                            points.get("CLASS_QUALITY_HARDWARE", 0.0)
                            if hardware_quality is True
                            else 0.0
                        ),
                        "evidence_status": (
                            "CONFIRMED"
                            if hardware_quality is not None
                            else "SOURCE_MISSING"
                        ),
                        "inputs": {
                            "is_camera_off": lesson.is_camera_off,
                            "is_cpu_usage_high": lesson.is_cpu_usage_high,
                            "is_network_delay_high": (
                                lesson.is_network_delay_high
                            ),
                        },
                    }
                ]
                if "CLASS_QUALITY_HARDWARE" in points
                else (
                    [
                        {
                            "code": "CLASS_QUALITY_PERFECT_COUNT",
                            "business_fact": "perfect_cnt",
                            "fact_value": None,
                            "awarded": False,
                            "points_per_unit": points.get(
                                "CLASS_QUALITY_PERFECT_COUNT", 0.0
                            ),
                            "score": 0.0,
                            "evidence_status": "SOURCE_MISSING",
                        }
                    ]
                    if "CLASS_QUALITY_PERFECT_COUNT" in points
                    else []
                )
            ),
        }
        if "FEEDBACK_REBOOK_15D" in points:
            rebook_known = lesson.is_rebooked is not None
            rebook = is_completed and lesson.is_rebooked is True
            component_sets["USER_FEEDBACK"].append(
                {
                    "code": "FEEDBACK_REBOOK_15D",
                    "business_fact": "is_rebooked",
                    "fact_value": lesson.is_rebooked,
                    "awarded": rebook,
                    "points_per_unit": points["FEEDBACK_REBOOK_15D"],
                    "score": points["FEEDBACK_REBOOK_15D"] if rebook else 0.0,
                    "evidence_status": (
                        "CONFIRMED" if rebook_known else "SOURCE_MISSING"
                    ),
                }
            )
        for dimension, components in component_sets.items():
            for component in components:
                if component["awarded"]:
                    attributed[component["code"]]["count"] += 1
                    attributed[component["code"]]["score"] += float(
                        component["score"]
                    )
            evidence_statuses = {
                str(component["evidence_status"]) for component in components
            }
            if not components:
                evidence_status = "NOT_APPLICABLE"
                evidence_coverage = "NONE"
            elif evidence_statuses == {"CONFIRMED"}:
                evidence_status = "CONFIRMED"
                evidence_coverage = "FULL"
            elif evidence_statuses == {"SOURCE_MISSING"}:
                evidence_status = "SOURCE_MISSING"
                evidence_coverage = "NONE"
            else:
                evidence_status = "PARTIAL"
                evidence_coverage = "PARTIAL"
            rows.append(
                {
                    "lesson": lesson,
                    "dimension": dimension,
                    "current_score": round(
                        sum(float(item["score"]) for item in components), 2
                    ),
                    "evidence_status": evidence_status,
                    "evidence_coverage": evidence_coverage,
                    "components": components,
                }
            )
    return rows, attributed


def _reconciliation(
    *,
    source_scope: str,
    component_code: str,
    current_score: float,
    attributed_score: float,
    has_lessons: bool,
) -> str:
    if source_scope != "LESSON":
        return "NOT_APPLICABLE"
    if component_code == "CLASS_QUALITY_PERFECT_COUNT":
        return "SOURCE_MISSING"
    if math.isclose(current_score, attributed_score, abs_tol=1e-9):
        return "MATCHED" if current_score else "MATCHED_ZERO"
    if attributed_score < current_score:
        return "PARTIAL" if has_lessons else "SOURCE_MISSING"
    return "MISMATCH"


def _task_component_rows(
    assignments: list[TaskAssignmentRecord],
    templates: dict[str, TaskTemplateRecord],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_code = {
        item.task_code: item
        for item in assignments
        if item.task_code in FIXED_GROWTH_CODES
        and item.task_kind == "FIXED_GROWTH"
        and item.creator_system == "TRIGGER_CENTER"
    }
    for code in FIXED_GROWTH_CODES:
        assignment = by_code.get(code)
        template = (
            templates.get(assignment.template_version_id) if assignment else None
        )
        payload = template.payload if template and isinstance(template.payload, dict) else {}
        points = float(payload.get("score_value") or 0)
        completed = bool(assignment and assignment.status == "COMPLETED")
        rows.append(
            {
                "code": code,
                "metric": "task_assignments.status",
                "value": 1 if completed else 0,
                "points_per_unit": points,
                "score": points if completed else 0.0,
                "source_mode": (
                    "SYSTEM_TASK_STATUS" if assignment else "TASK_BASELINE_INCOMPLETE"
                ),
                "assignment_id": assignment.assignment_id if assignment else None,
                "status": assignment.status if assignment else None,
                "template_version_id": (
                    assignment.template_version_id if assignment else None
                ),
                "title": payload.get("title") or code,
            }
        )
    return rows


def refresh_persisted_score_read_models(
    session: Session,
    *,
    trigger_type: str,
    trigger_ref: str | None = None,
    teacher_ids: Iterable[str] | None = None,
    score_policy_payload: dict[str, Any] | None = None,
    score_config_version_id: str | None = None,
) -> dict[str, Any]:
    """Atomically refresh all persisted score projections for selected teachers."""

    acquire_score_projection_lock(session)
    policy_payload, config_version_id = _score_config(
        session,
        score_policy_payload,
        score_config_version_id,
    )
    policy = ScoreGraduationConfig.model_validate(policy_payload)
    policy_sha256 = _canonical_hash(policy_payload)
    selected_ids = sorted({str(item) for item in teacher_ids or []})
    teacher_query = select(TeacherRecord)
    if selected_ids:
        teacher_query = teacher_query.where(TeacherRecord.teacher_id.in_(selected_ids))
    teachers = list(session.scalars(teacher_query.order_by(TeacherRecord.teacher_id)).all())
    if selected_ids and len(teachers) != len(selected_ids):
        found = {item.teacher_id for item in teachers}
        raise RuntimeError(
            f"SCORE_PROJECTION_TEACHERS_MISSING:{sorted(set(selected_ids) - found)[:10]}"
        )
    ids = [item.teacher_id for item in teachers]
    projection_id = f"SPR-{uuid4().hex}"
    if not teachers:
        return {
            "projection_id": projection_id,
            "teacher_count": 0,
            "lesson_score_state_count": 0,
            "component_account_count": 0,
        }

    snapshots = list(
        session.scalars(
            select(TeacherMetricSnapshotRecord)
            .join(
                TeacherRecord,
                and_(
                    TeacherRecord.teacher_id
                    == TeacherMetricSnapshotRecord.teacher_id,
                    TeacherRecord.source_batch_id
                    == TeacherMetricSnapshotRecord.batch_id,
                ),
            )
            .where(TeacherRecord.teacher_id.in_(ids))
            .options(
                load_only(
                    TeacherMetricSnapshotRecord.snapshot_id,
                    TeacherMetricSnapshotRecord.batch_id,
                    TeacherMetricSnapshotRecord.teacher_id,
                    TeacherMetricSnapshotRecord.score_rule_version,
                    TeacherMetricSnapshotRecord.score_policy_snapshot,
                    TeacherMetricSnapshotRecord.score_policy_sha256,
                    TeacherMetricSnapshotRecord.total_completed_cnt,
                    TeacherMetricSnapshotRecord.perfect_cnt,
                    TeacherMetricSnapshotRecord.absent_cnt,
                    TeacherMetricSnapshotRecord.capacity_score,
                    TeacherMetricSnapshotRecord.new_teacher_task_score,
                    TeacherMetricSnapshotRecord.reliability_score,
                    TeacherMetricSnapshotRecord.user_feedback_score,
                    TeacherMetricSnapshotRecord.class_quality_score,
                    TeacherMetricSnapshotRecord.raw_total_score,
                    TeacherMetricSnapshotRecord.public_total_score,
                    TeacherMetricSnapshotRecord.metric_inputs,
                    TeacherMetricSnapshotRecord.metric_provenance,
                    TeacherMetricSnapshotRecord.updated_at,
                    raiseload=True,
                )
            )
        ).all()
    )
    snapshot_by_current = {
        (item.teacher_id, item.batch_id): item for item in snapshots
    }
    accounts = list(
        session.scalars(
            select(ScoreAccountRecord).where(ScoreAccountRecord.teacher_id.in_(ids))
        ).all()
    )
    accounts_by_teacher: dict[str, list[ScoreAccountRecord]] = defaultdict(list)
    for item in accounts:
        accounts_by_teacher[item.teacher_id].append(item)

    assignments = list(
        session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.teacher_id.in_(ids),
                TaskAssignmentRecord.task_code.in_(FIXED_GROWTH_CODES),
            )
        ).all()
    )
    assignments_by_teacher: dict[str, list[TaskAssignmentRecord]] = defaultdict(list)
    for item in assignments:
        assignments_by_teacher[item.teacher_id].append(item)
    template_ids = {
        item.template_version_id for item in assignments if item.template_version_id
    }
    templates = {
        item.row_id: item
        for item in session.scalars(
            select(TaskTemplateRecord).where(TaskTemplateRecord.row_id.in_(template_ids))
        ).all()
    } if template_ids else {}

    lessons = list(
        session.scalars(
            select(LessonFactRecord)
            .where(LessonFactRecord.teacher_id.in_(ids))
            .options(
                load_only(
                    LessonFactRecord.lesson_id,
                    LessonFactRecord.source_appoint_id,
                    LessonFactRecord.teacher_id,
                    LessonFactRecord.scheduled_start_at,
                    LessonFactRecord.lesson_lifecycle_status,
                    LessonFactRecord.lesson_local_date,
                    LessonFactRecord.lesson_local_time,
                    LessonFactRecord.student_id_hash,
                    LessonFactRecord.is_late,
                    LessonFactRecord.is_early,
                    LessonFactRecord.is_peak,
                    LessonFactRecord.is_favorited,
                    LessonFactRecord.has_positive_feedback_tag,
                    LessonFactRecord.is_rebooked,
                    LessonFactRecord.is_camera_off,
                    LessonFactRecord.is_cpu_usage_high,
                    LessonFactRecord.is_network_delay_high,
                    LessonFactRecord.source_batch_id,
                    LessonFactRecord.source_record_id,
                    LessonFactRecord.updated_at,
                    raiseload=True,
                )
            )
            .order_by(
                LessonFactRecord.teacher_id,
                LessonFactRecord.lesson_local_date,
                LessonFactRecord.lesson_local_time,
                LessonFactRecord.lesson_id,
            )
        ).all()
    )
    lessons_by_teacher: dict[str, list[LessonFactRecord]] = defaultdict(list)
    for item in lessons:
        lessons_by_teacher[item.teacher_id].append(item)

    # A teacher-wide source update changes aggregate inputs, but it does not
    # change either lesson facts or the published score policy. Keep the
    # existing per-lesson projection intact in that path. Policy publication
    # and lesson-source replacement still rebuild it atomically.
    rebuild_lesson_scores = trigger_type not in {
        "TEACHER_SOURCE_UPDATED",
        "TASK_STATUS_UPDATED",
    }
    old_lesson_revisions = (
        {
            (str(teacher_id), str(lesson_id), str(dimension)): int(revision or 0)
            for teacher_id, lesson_id, dimension, revision in session.execute(
                select(
                    LessonDimensionScoreRecord.teacher_id,
                    LessonDimensionScoreRecord.lesson_id,
                    LessonDimensionScoreRecord.dimension,
                    LessonDimensionScoreRecord.current_revision,
                ).where(LessonDimensionScoreRecord.teacher_id.in_(ids))
            ).all()
        }
        if rebuild_lesson_scores
        else {}
    )
    if rebuild_lesson_scores:
        session.execute(
            delete(LessonDimensionScoreRecord).where(
                LessonDimensionScoreRecord.teacher_id.in_(ids)
            )
        )
    old_component_revisions = {
        (str(teacher_id), str(component_code)): int(revision or 0)
        for teacher_id, component_code, revision in session.execute(
            select(
                ScoreComponentAccountRecord.teacher_id,
                ScoreComponentAccountRecord.component_code,
                ScoreComponentAccountRecord.projection_revision,
            ).where(ScoreComponentAccountRecord.teacher_id.in_(ids))
        ).all()
    }
    session.execute(
        delete(ScoreComponentAccountRecord).where(
            ScoreComponentAccountRecord.teacher_id.in_(ids)
        )
    )
    session.flush()

    projector = GrowthService(
        None,  # type: ignore[arg-type]
        config_reader=lambda key: (
            deepcopy(policy_payload)
            if ConfigKey(key) == ConfigKey.SCORE_GRADUATION
            else None
        ),
    )
    calculated_at = _now()
    lesson_state_count = 0
    component_count = 0
    lesson_insert_buffer: list[dict[str, Any]] = []
    component_insert_buffer: list[dict[str, Any]] = []
    teacher_batch_ids: set[str] = set()
    lesson_batch_ids: set[str] = set()

    for teacher in teachers:
        snapshot = snapshot_by_current.get(
            (teacher.teacher_id, teacher.source_batch_id)
        )
        if teacher.source_batch_id:
            teacher_batch_ids.add(teacher.source_batch_id)
        teacher_lessons = lessons_by_teacher.get(teacher.teacher_id, [])
        current_lesson_batches = sorted(
            {
                item.source_batch_id
                for item in teacher_lessons
                if item.source_batch_id
            }
        )
        lesson_batch_ids.update(current_lesson_batches)
        source_lesson_batch_id = (
            current_lesson_batches[0]
            if len(current_lesson_batches) == 1
            else "MULTIPLE"
            if current_lesson_batches
            else None
        )
        teacher_payload = deepcopy(teacher.payload or {})
        teacher_payload.setdefault("teacher_id", teacher.teacher_id)
        teacher_payload["graduation_qualified"] = bool(
            teacher.graduation_state == "GRADUATED"
            or teacher_payload.get("graduation_qualified")
        )
        teacher_payload["gold_qualified"] = bool(
            teacher.gold_qualified or teacher_payload.get("gold_qualified")
        )
        if snapshot is not None:
            teacher_payload["metric_inputs"] = deepcopy(snapshot.metric_inputs or {})
            teacher_payload["metric_provenance"] = deepcopy(
                snapshot.metric_provenance or {}
            )
            teacher_payload["metric_inputs"]["absent_cnt"] = int(
                snapshot.absent_cnt
            )
            teacher_payload["metric_provenance"].setdefault(
                "absent_cnt",
                {
                    "source_mode": "REAL",
                    "source_field": "absent_cnt",
                    "batch_id": snapshot.batch_id,
                    "note": (
                        "Direct absence count from the validated teacher snapshot."
                    ),
                },
            )
        has_confirmed_empty_lesson_set = bool(
            snapshot is not None
            and int(snapshot.total_completed_cnt or 0) == 0
        )
        perfect_summary = (
            _perfect_completion_summary(teacher_lessons)
            if teacher_lessons or has_confirmed_empty_lesson_set
            else None
        )
        if perfect_summary is not None:
            teacher_payload.setdefault("metric_inputs", {})
            teacher_payload.setdefault("metric_provenance", {})
            teacher_payload["metric_inputs"]["perfect_cnt"] = int(
                perfect_summary["count"]
            )
            teacher_payload["metric_provenance"]["perfect_cnt"] = {
                "source_mode": perfect_summary["source_mode"],
                "source_field": (
                    "lesson_facts.source_appoint_id,"
                    "lesson_lifecycle_status,is_late,is_early"
                ),
                "batch_id": source_lesson_batch_id,
                "note": (
                    "COUNT(DISTINCT source_appoint_id) where status=end, "
                    "is_late=false and is_early=false."
                ),
            }
        teacher_assignments = assignments_by_teacher.get(teacher.teacher_id, [])
        overrides = _account_overrides(
            accounts_by_teacher.get(teacher.teacher_id, []),
            teacher_assignments,
            templates,
        )
        quality_rule = getattr(policy.scoring_items, "classroom_quality", None)
        if (
            getattr(quality_rule, "metric", None)
            == "lesson_hardware_quality_passed"
        ):
            quality_summary = _hardware_quality_summary(teacher_lessons)
            overrides["CLASS_QUALITY"] = {
                **quality_summary,
                "score": (
                    float(quality_summary["count"])
                    * float(quality_rule.points_per_unit)
                ),
            }
        projection_score_rule_version = str(
            (
                policy.policy_version
                if trigger_type == "SCORE_POLICY_PUBLISHED"
                else teacher_payload.get("score_rule_version")
                or (snapshot.score_rule_version if snapshot is not None else None)
                or policy.policy_version
            )
        )
        projected = projector._project_teacher_scoring(
            teacher_payload,
            (policy, "PUBLISHED"),
            overrides,
        )
        dimensions = list(projected.get("dimensions") or [])
        if (
            trigger_type in {"TASK_STATUS_UPDATED", "LESSON_SOURCE_UPDATED"}
            and snapshot is not None
        ):
            # A task or lesson-source update must not silently recalculate
            # unrelated teacher-wide dimensions. Their current persisted
            # snapshot remains authoritative until the teacher source or score
            # policy itself changes.
            preserved_scores = {
                "RELIABILITY": float(snapshot.reliability_score),
                "USER_FEEDBACK": float(snapshot.user_feedback_score),
                "CLASS_QUALITY": float(snapshot.class_quality_score),
                "CAPACITY": float(snapshot.capacity_score),
                "NEW_TEACHER_TASK": float(snapshot.new_teacher_task_score),
            }
            if trigger_type == "LESSON_SOURCE_UPDATED":
                preserved_scores["RELIABILITY"] = float(
                    next(
                        (
                            item["score"]
                            for item in dimensions
                            if item.get("code") == "RELIABILITY"
                        ),
                        0.0,
                    )
                )
                preserved_scores["CLASS_QUALITY"] = float(
                    next(
                        (
                            item["score"]
                            for item in dimensions
                            if item.get("code") == "CLASS_QUALITY"
                        ),
                        0.0,
                    )
                )
            if trigger_type == "TASK_STATUS_UPDATED":
                preserved_scores["NEW_TEACHER_TASK"] = float(
                    next(
                        (
                            item["score"]
                            for item in dimensions
                            if item.get("code") == "NEW_TEACHER_TASK"
                        ),
                        snapshot.new_teacher_task_score,
                    )
                )
            dimensions = [
                {
                    **item,
                    "score": round(
                        preserved_scores.get(
                            str(item.get("code")),
                            float(item.get("score") or 0),
                        ),
                        2,
                    ),
                }
                for item in dimensions
            ]
            projected["dimensions"] = dimensions
            projected["raw_total_score"] = round(
                sum(float(item["score"]) for item in dimensions),
                2,
            )
            projected["external_display_score"] = round(
                min(
                    float(projected["raw_total_score"]),
                    float(policy.thresholds.gold_external_score),
                ),
                2,
            )
            for field in (
                "graduation_state",
                "graduation_criteria_met",
                "graduation_qualified",
                "gold_criteria_met",
                "gold_qualified",
                "hard_gates",
            ):
                if field in teacher_payload:
                    projected[field] = deepcopy(teacher_payload[field])
        points = _points_by_component(dimensions)
        lesson_rows, attributed = _lesson_component_payloads(
            teacher_lessons,
            points=points,
        )
        if rebuild_lesson_scores:
            for row in lesson_rows:
                lesson = row["lesson"]
                dimension = row["dimension"]
                lesson_insert_buffer.append(
                    {
                        "score_state_id": (
                            f"{teacher.camp_enrollment_id}:"
                            f"{lesson.lesson_id}:{dimension}"
                        ),
                        "camp_enrollment_id": teacher.camp_enrollment_id,
                        "lesson_id": lesson.lesson_id,
                        "teacher_id": teacher.teacher_id,
                        "dimension": dimension,
                        "current_score": float(row["current_score"]),
                        "evidence_status": str(row["evidence_status"]),
                        "evidence_coverage": str(row["evidence_coverage"]),
                        "score_rule_version": projection_score_rule_version,
                        "current_revision": (
                            old_lesson_revisions.get(
                                (
                                    teacher.teacher_id,
                                    lesson.lesson_id,
                                    dimension,
                                ),
                                0,
                            )
                            + 1
                        ),
                        "score_as_of": (
                            lesson.scheduled_start_at or lesson.updated_at
                        ),
                        "last_score_entry_id": None,
                        "payload": {
                            "source_scope": "LESSON",
                            "source_batch_id": lesson.source_batch_id,
                            "source_record_id": lesson.source_record_id,
                            "business_facts": row["components"],
                            "projection_id": projection_id,
                            "projection_trigger": {
                                "type": trigger_type,
                                "ref": trigger_ref,
                            },
                        },
                        "updated_at": calculated_at,
                    }
                )
                lesson_state_count += 1
                if len(lesson_insert_buffer) >= PROJECTION_INSERT_BATCH_SIZE:
                    session.execute(
                        insert(LessonDimensionScoreRecord),
                        lesson_insert_buffer,
                    )
                    lesson_insert_buffer.clear()

        task_components = _task_component_rows(teacher_assignments, templates)
        account_by_dimension = {
            item.dimension: item
            for item in accounts_by_teacher.get(teacher.teacher_id, [])
        }
        dimension_by_code = {
            str(item["code"]): item for item in dimensions
        }
        for dimension_code in DIMENSION_ORDER:
            dimension = dimension_by_code.get(dimension_code)
            if dimension is None:
                continue
            account = account_by_dimension.get(dimension_code)
            account_payload = {
                **deepcopy(dimension),
                "projection_id": projection_id,
                "projection_trigger": {
                    "type": trigger_type,
                    "ref": trigger_ref,
                },
                "projection_scope": "PERSISTED_CURRENT",
                "calculated_at": calculated_at.isoformat(),
            }
            target_rule_version = projection_score_rule_version
            if account is None:
                account = ScoreAccountRecord(
                    account_id=f"{teacher.teacher_id}:{dimension_code}",
                    teacher_id=teacher.teacher_id,
                    camp_enrollment_id=teacher.camp_enrollment_id,
                    dimension=dimension_code,
                    current_score=float(dimension["score"]),
                    minimum_score=0,
                    weight=0,
                    score_rule_version=target_rule_version,
                    version=1,
                    updated_at=calculated_at,
                    payload=account_payload,
                )
                session.add(account)
            else:
                preserve_task_lineage = (
                    dimension_code == "NEW_TEACHER_TASK"
                    and _source_mode(account.payload, "")
                    == "SYSTEM_TASK_STATUS"
                )
                changed = (
                    not math.isclose(
                        float(account.current_score),
                        float(dimension["score"]),
                        abs_tol=1e-9,
                    )
                    or (
                        not preserve_task_lineage
                        and account.score_rule_version != target_rule_version
                    )
                    or (
                        not preserve_task_lineage
                        and account.payload != account_payload
                    )
                )
                account.current_score = float(dimension["score"])
                if not preserve_task_lineage:
                    account.score_rule_version = target_rule_version
                    account.payload = account_payload
                if changed:
                    if trigger_type != "TEACHER_SOURCE_UPDATED":
                        account.version = int(account.version or 0) + 1
                    account.updated_at = calculated_at

            components = (
                task_components
                if dimension_code == "NEW_TEACHER_TASK"
                else list(dimension.get("components") or [])
            )
            for component in components:
                code = str(component.get("code") or "")
                if not code:
                    continue
                source_scope = (
                    "TASK"
                    if dimension_code == "NEW_TEACHER_TASK"
                    else "TEACHER"
                    if dimension_code == "CAPACITY"
                    else "LESSON"
                )
                component_score = float(component.get("score") or 0)
                attributed_value = attributed.get(
                    code, {"count": 0.0, "score": 0.0}
                )
                attributed_score = float(attributed_value["score"])
                status = _reconciliation(
                    source_scope=source_scope,
                    component_code=code,
                    current_score=component_score,
                    attributed_score=attributed_score,
                    has_lessons=bool(teacher_lessons),
                )
                component_insert_buffer.append(
                    {
                        "component_account_id": f"{teacher.teacher_id}:{code}",
                        "teacher_id": teacher.teacher_id,
                        "camp_enrollment_id": teacher.camp_enrollment_id,
                        "dimension": dimension_code,
                        "component_code": code,
                        "source_scope": source_scope,
                        "source_metric": component.get("metric"),
                        "unit_count": float(component.get("value") or 0),
                        "points_per_unit": (
                            float(component["points_per_unit"])
                            if component.get("points_per_unit") is not None
                            else None
                        ),
                        "current_score": component_score,
                        "lesson_attributed_count": int(
                            attributed_value["count"]
                        ),
                        "lesson_attributed_score": round(attributed_score, 2),
                        "unattributed_score": round(
                            component_score - attributed_score,
                            2,
                        ),
                        "reconciliation_status": status,
                        "score_rule_version": projection_score_rule_version,
                        "source_teacher_batch_id": teacher.source_batch_id,
                        "source_lesson_batch_id": source_lesson_batch_id,
                        "projection_revision": (
                            old_component_revisions.get(
                                (teacher.teacher_id, code),
                                0,
                            )
                            + 1
                        ),
                        "calculated_at": calculated_at,
                        "payload": {
                            **deepcopy(component),
                            "score_config_version_id": config_version_id,
                            "projection_id": projection_id,
                            "projection_trigger": {
                                "type": trigger_type,
                                "ref": trigger_ref,
                            },
                            "lesson_source_batch_ids": current_lesson_batches,
                            "attribution_contract": (
                                "teacher-total-is-authoritative; "
                                "lesson-attribution-must-reconcile"
                            ),
                        },
                    }
                )
                component_count += 1
                if len(component_insert_buffer) >= PROJECTION_INSERT_BATCH_SIZE:
                    session.execute(
                        insert(ScoreComponentAccountRecord),
                        component_insert_buffer,
                    )
                    component_insert_buffer.clear()

        raw_total = float(projected.get("raw_total_score") or 0)
        public_total = float(projected.get("external_display_score") or 0)
        if snapshot is not None:
            dimension_scores = {
                item["code"]: float(item["score"]) for item in dimensions
            }
            if perfect_summary is not None:
                snapshot.perfect_cnt = int(perfect_summary["count"])
            snapshot.metric_inputs = deepcopy(
                teacher_payload["metric_inputs"]
            )
            snapshot.metric_provenance = deepcopy(
                teacher_payload["metric_provenance"]
            )
            snapshot.reliability_score = dimension_scores.get("RELIABILITY", 0)
            snapshot.user_feedback_score = dimension_scores.get("USER_FEEDBACK", 0)
            snapshot.class_quality_score = dimension_scores.get("CLASS_QUALITY", 0)
            snapshot.capacity_score = dimension_scores.get("CAPACITY", 0)
            snapshot.new_teacher_task_score = dimension_scores.get(
                "NEW_TEACHER_TASK", 0
            )
            snapshot.raw_total_score = raw_total
            snapshot.public_total_score = public_total
            snapshot.score_rule_version = projection_score_rule_version
            snapshot.score_policy_snapshot = deepcopy(policy_payload)
            snapshot.score_policy_sha256 = policy_sha256
            snapshot.updated_at = calculated_at

        teacher_payload.update(
            {
                "dimensions": deepcopy(dimensions),
                "raw_total_score": raw_total,
                "total_score": raw_total,
                "external_display_score": public_total,
                "graduation_state": projected.get("graduation_state"),
                "graduation_criteria_met": projected.get(
                    "graduation_criteria_met"
                ),
                "graduation_qualified": projected.get(
                    "graduation_qualified"
                ),
                "gold_criteria_met": projected.get("gold_criteria_met"),
                "gold_qualified": projected.get("gold_qualified"),
                "hard_gates": deepcopy(projected.get("hard_gates") or {}),
                "score_policy_version": policy.policy_version,
                "score_rule_version": projection_score_rule_version,
                "score_policy_sha256": policy_sha256,
                "score_projection_scope": "PERSISTED_CURRENT",
                "score_projection_id": projection_id,
                "score_projection_trigger": {
                    "type": trigger_type,
                    "ref": trigger_ref,
                },
                "updated_at": calculated_at.isoformat(),
            }
        )
        teacher.total_score = raw_total
        teacher.graduation_state = str(
            projected.get("graduation_state") or teacher.graduation_state
        )
        teacher.gold_qualified = bool(projected.get("gold_qualified"))
        teacher.payload = teacher_payload
        teacher.updated_at = calculated_at

    if lesson_insert_buffer:
        session.execute(
            insert(LessonDimensionScoreRecord),
            lesson_insert_buffer,
        )
    if component_insert_buffer:
        session.execute(
            insert(ScoreComponentAccountRecord),
            component_insert_buffer,
        )
    session.flush()
    return {
        "projection_id": projection_id,
        "teacher_count": len(teachers),
        "lesson_score_state_count": lesson_state_count,
        "component_account_count": component_count,
        "score_rule_version": policy.policy_version,
        "source_versions": {
            "teacher_batch_ids": sorted(teacher_batch_ids),
            "lesson_batch_ids": sorted(lesson_batch_ids),
            "score_config_version_id": config_version_id,
            "score_policy_sha256": policy_sha256,
        },
    }
