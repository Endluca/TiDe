"""Incrementally project the two current source-wide tables into TiDe facts.

The upstream monitor owns ``teacher_source_wide`` and ``lesson_source_wide``.
Database triggers emit compact field-diff events; this worker consumes only
those events.  It never writes either source table and it never invokes the
retired whole-file import paths.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import Engine, literal_column, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .database import engine as default_engine
from .db_models import (
    ComplaintCategoryRuleRecord,
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherQualificationRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from .fixed_growth_baseline import ensure_fixed_growth_assignments
from .personalized_trigger_projection import (
    TRIGGER_RULE_VERSION,
    LessonTriggerRow,
    build_output_specs,
    feedback_labels,
    materialize_outputs,
    published_template_map,
)
from .lesson_quality import hardware_quality_passed, is_perfect_lesson
from .personalized_rules import ComplaintRule, normalize_text
from .score_projection_lock import acquire_score_projection_lock
from .source_change_router import (
    SourceChangeRoute,
    SourceChangeRoutingError,
    route_source_change,
)
from .task_catalog import MANDATORY_TASK_CODES


EVENT_TYPE = "source_wide.changed.v1"
SOURCE_AGGREGATE_TYPES = frozenset(
    {"TEACHER_SOURCE_WIDE", "LESSON_SOURCE_WIDE"}
)
SOURCE_SNAPSHOT_LABEL = "SOURCE_WIDE_CURRENT"
SOURCE_WORKER_ACTOR = "TRIGGER_CENTER:SOURCE_WIDE_WORKER"
CAPACITY_MILESTONE_ID = "CAPACITY_PEAK_SLOT_40"
CAPACITY_MILESTONE_REASON_CODE = "CAPACITY_PEAK_SLOT_40_ACHIEVED"

_COMPLETED_LESSON_STATUSES = frozenset(
    {"已完课", "完课", "ended", "end", "completed", "complete", "finished"}
)
_SCORE_OR_QUALIFICATION_HANDLERS = frozenset(
    {
        "LESSON_SCORE",
        "FAVORITE_PAIR",
        "TEACHER_RELIABILITY",
        "TEACHER_USER_FEEDBACK",
        "TEACHER_CLASS_QUALITY",
        "TEACHER_CAPACITY",
        "TEACHER_TOTAL",
        "TEACHER_QUALIFICATION",
        "TEACHER_SOURCE_REMOVAL",
    }
)
_TRIGGER_HANDLERS = frozenset(
    {
        "LESSON_TRIGGER",
        "NEGATIVE_LABEL",
        "BLACKLIST_TEACHER",
        "COMPLAINT_TEACHER",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _completed_lesson(value: str | None) -> bool:
    return str(value or "").strip().casefold() in _COMPLETED_LESSON_STATUSES


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class SourceWideProjectionError(RuntimeError):
    """A persisted source or derived fact violates the projection contract."""


class SourceWideEventDataError(SourceWideProjectionError):
    """One malformed Outbox event can be retried/dead-lettered in isolation."""

    def __init__(self, outbox_id: str, reason: str) -> None:
        super().__init__(reason)
        self.outbox_id = outbox_id


@dataclass(frozen=True)
class _LessonProjection:
    lesson_id: str
    user_feedback_score: float
    reliability_score: float
    class_quality_score: float
    lesson_total_score: float
    dimensions: dict[str, Any]


@dataclass(frozen=True)
class _PolicyContext:
    policy: ScoreGraduationConfig
    payload: dict[str, Any]
    version_id: str
    sha256: str


@dataclass(frozen=True)
class _TeacherRefreshResult:
    lesson_result_changes: int
    component_changes: int
    account_changes: int
    qualification_changes: int


def _policy_context(
    session: Session,
    *,
    score_policy_payload: Mapping[str, Any] | None = None,
    score_config_version_id: str | None = None,
) -> _PolicyContext:
    if score_policy_payload is not None:
        payload = deepcopy(dict(score_policy_payload))
        policy = ScoreGraduationConfig.model_validate(payload)
        return _PolicyContext(
            policy=policy,
            payload=payload,
            version_id=(
                str(score_config_version_id)
                if score_config_version_id
                else f"EXPLICIT:{policy.policy_version}"
            ),
            sha256=_canonical_hash(payload),
        )
    record = session.scalar(
        select(ConfigVersionRecord)
        .where(
            ConfigVersionRecord.config_key
            == ConfigKey.SCORE_GRADUATION.value,
            ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
        )
        .order_by(ConfigVersionRecord.version_number.desc())
    )
    if record is None:
        payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION])
        version_id = f"DEFAULT_EMPTY_DATABASE:{payload['policy_version']}"
    else:
        payload = deepcopy(record.payload)
        version_id = record.version_id
    policy = ScoreGraduationConfig.model_validate(payload)
    return _PolicyContext(
        policy=policy,
        payload=payload,
        version_id=version_id,
        sha256=_canonical_hash(payload),
    )


def _nonnegative_source_count(
    value: int | None,
    *,
    field: str,
) -> tuple[int, str]:
    if value is None:
        return 0, "SOURCE_MISSING"
    if isinstance(value, bool) or int(value) < 0:
        raise SourceWideProjectionError(
            f"SOURCE_COUNT_MUST_BE_NONNEGATIVE:{field}"
        )
    return int(value), "CONFIRMED"


def _teacher_profile_payload(source: TeacherSourceWideRecord) -> dict[str, Any]:
    return {
        "employment_status": source.status,
        "bu": source.bu,
        "teach_area_type": source.teach_area_type,
        "onboard_date": _iso(source.onboard_date),
        "onboard_30d_end_date": _iso(source.onboard_30d_end_date),
        "first_booked_date": _iso(source.first_booked_dt),
        "is_cpl_tesol": source.is_cpl_tesol,
        "is_self_introduce": source.is_self_introduce,
        "lessons_completed": source.total_completed_cnt,
        "profile_source": {
            "table": "teacher_source_wide",
            "status": "CURRENT",
        },
    }


def _project_teacher_identity(
    session: Session,
    teacher_id: str,
    *,
    source: TeacherSourceWideRecord | None,
    occurred_at: datetime,
) -> tuple[TeacherRecord | None, bool]:
    teacher = session.get(TeacherRecord, teacher_id)
    if source is None:
        if teacher is None:
            return None, False
        payload = deepcopy(teacher.payload or {})
        source_state = deepcopy(payload.get("profile_source") or {})
        source_state.update(
            table="teacher_source_wide",
            status="SOURCE_MISSING",
        )
        source_state.setdefault("observed_missing_at", occurred_at.isoformat())
        if payload.get("profile_source") != source_state:
            payload["profile_source"] = source_state
            teacher.payload = payload
            teacher.updated_at = occurred_at
        return teacher, False

    created = teacher is None
    name = str(source.real_name or source.tchr_id).strip() or source.tchr_id
    camp_day = min(max(int(source.job_days or 0), 0), 30)
    if teacher is None:
        teacher = TeacherRecord(
            teacher_id=source.tchr_id,
            camp_enrollment_id=f"CAMP:{source.tchr_id}",
            name=name,
            country=None,
            timezone="UTC",
            camp_day=camp_day,
            graduation_state="IN_PROGRESS",
            gold_qualified=False,
            total_score=0,
            graduation_threshold=0,
            data_mode="REAL",
            source_snapshot_label=SOURCE_SNAPSHOT_LABEL,
            payload={
                "teacher_id": source.tchr_id,
                "country": None,
                "timezone": None,
                "timezone_source_mode": "SOURCE_MISSING",
                **_teacher_profile_payload(source),
            },
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        session.add(teacher)
        session.flush()
        # PostgreSQL creates the fixed baseline in the AFTER INSERT trigger.
        # The application call is an idempotent verification there and supplies
        # the equivalent behavior in the disposable SQLite test harness.
        baseline = ensure_fixed_growth_assignments(
            session,
            [source.tchr_id],
            actor_id=SOURCE_WORKER_ACTOR,
            occurred_at=occurred_at,
        )
        if baseline.expected_assignment_count != len(MANDATORY_TASK_CODES):
            raise SourceWideProjectionError("FIXED_TASK_BASELINE_COUNT_INVALID")
        session.flush()
    else:
        payload = deepcopy(teacher.payload or {})
        payload.update(_teacher_profile_payload(source))
        payload.setdefault("teacher_id", teacher.teacher_id)
        payload.setdefault("country", None)
        payload.setdefault("timezone", None)
        payload["timezone_source_mode"] = "SOURCE_MISSING"
        changed = any(
            (
                teacher.name != name,
                teacher.camp_day != camp_day,
                teacher.data_mode != "REAL",
                teacher.source_snapshot_label != SOURCE_SNAPSHOT_LABEL,
                teacher.payload != payload,
            )
        )
        teacher.name = name
        teacher.camp_day = camp_day
        teacher.data_mode = "REAL"
        teacher.source_snapshot_label = SOURCE_SNAPSHOT_LABEL
        teacher.payload = payload
        if changed:
            teacher.updated_at = occurred_at
    return teacher, created


def _lesson_raw_payload(source: LessonSourceWideRecord) -> dict[str, Any]:
    return {
        "课程id": source.course_id,
        "上课日期": source.lesson_date,
        "上课时间": source.lesson_time,
        "是否高峰": source.is_peak,
        "老师id": source.teacher_id,
        "学员id": source.student_id,
        "课程状态": source.lesson_status,
        "缺席原因明细": source.absence_reason_detail,
        "迟到": source.is_late,
        "早退": source.is_early,
        "差评分": source.negative_score,
        "差评标签": source.has_negative_feedback_tag,
        "投诉一级分类": source.complaint_category_l1,
        "投诉二级分类": source.complaint_category_l2,
        "投诉三级分类": source.complaint_category_l3,
        "是否拉黑": source.is_blocked,
        "收藏": source.is_favorited,
        "好评标签": source.has_positive_feedback_tag,
        "评价详情": source.feedback_detail,
        "未开摄像头": source.is_camera_off,
        "cpu占用过高": source.is_cpu_usage_high,
        "网络延迟过高": source.is_network_delay_high,
        "假早退": source.is_false_early_leave,
    }


def _trigger_lesson_row(source: LessonSourceWideRecord) -> LessonTriggerRow:
    local_date = source.lesson_date or date.max
    local_time = source.lesson_time or time.max
    row_token = int(hashlib.sha256(source.course_id.encode("utf-8")).hexdigest()[:8], 16)
    return LessonTriggerRow(
        row_number=row_token,
        raw_payload=_lesson_raw_payload(source),
        lesson_id=source.course_id,
        teacher_id=source.teacher_id,
        student_id=str(source.student_id or ""),
        local_date=local_date,
        local_time=local_time,
        local_start_at=datetime.combine(local_date, local_time).replace(
            tzinfo=timezone.utc
        ),
        lifecycle_status=str(source.lesson_status or ""),
        is_peak=source.is_peak,
        is_late=source.is_late,
        is_early=source.is_early,
        is_false_early_leave=source.is_false_early_leave,
        negative_score=source.negative_score,
        has_negative_tag=source.has_negative_feedback_tag,
        feedback_detail=source.feedback_detail,
        negative_tags=feedback_labels(source.feedback_detail),
        absence_reason_detail=source.absence_reason_detail,
        complaint_l1=source.complaint_category_l1,
        complaint_l2=source.complaint_category_l2,
        complaint_l3=source.complaint_category_l3,
        is_blocked=source.is_blocked,
        is_favorited=source.is_favorited,
        has_positive_tag=source.has_positive_feedback_tag,
        is_rebooked=None,
        is_camera_off=source.is_camera_off,
        is_cpu_usage_high=source.is_cpu_usage_high,
        is_network_delay_high=source.is_network_delay_high,
    )


def _lesson_projections(
    lessons: Sequence[LessonSourceWideRecord],
    policy: ScoreGraduationConfig,
) -> tuple[list[_LessonProjection], dict[str, dict[str, float]]]:
    scoring = policy.scoring_items
    praise_points = float(scoring.feedback_praise.points_per_unit)
    favorite_points = float(scoring.feedback_favorite.points_per_unit)
    perfect_points = float(scoring.reliability_perfect.points_per_unit)
    peak_points = float(scoring.reliability_peak.points_per_unit)
    quality_rule = getattr(scoring, "classroom_quality", None)
    quality_points = float(getattr(quality_rule, "points_per_unit", 0) or 0)

    unresolved_favorite_pairs = {
        (lesson.teacher_id, lesson.student_id)
        for lesson in lessons
        if _completed_lesson(lesson.lesson_status)
        and lesson.is_favorited is True
        and lesson.student_id
        and (lesson.lesson_date is None or lesson.lesson_time is None)
    }
    ordered = sorted(
        lessons,
        key=lambda item: (
            item.lesson_date is None,
            item.lesson_date or date.max,
            item.lesson_time is None,
            item.lesson_time or time.max,
            item.course_id,
        ),
    )
    credited_favorites: set[tuple[str, str]] = set()
    attributed: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0.0, "score": 0.0}
    )
    results: list[_LessonProjection] = []
    for lesson in ordered:
        completed = _completed_lesson(lesson.lesson_status)
        status_known = bool(str(lesson.lesson_status or "").strip())
        praise_known = status_known and lesson.has_positive_feedback_tag is not None
        praise = completed and lesson.has_positive_feedback_tag is True

        favorite_pair = (
            (lesson.teacher_id, lesson.student_id)
            if lesson.student_id
            else None
        )
        favorite_source_hit = completed and lesson.is_favorited is True
        favorite_order_known = (
            lesson.lesson_date is not None and lesson.lesson_time is not None
        )
        favorite_attribution_known = bool(
            lesson.is_favorited is not None
            and status_known
            and (
                not favorite_source_hit
                or (
                    favorite_pair is not None
                    and favorite_order_known
                    and favorite_pair not in unresolved_favorite_pairs
                )
            )
        )
        favorite = bool(
            favorite_source_hit
            and favorite_attribution_known
            and favorite_pair not in credited_favorites
        )
        if favorite and favorite_pair is not None:
            credited_favorites.add(favorite_pair)

        perfect_known = (
            status_known
            and lesson.is_late is not None
            and lesson.is_early is not None
        )
        perfect = is_perfect_lesson(
            lesson_lifecycle_status=lesson.lesson_status,
            is_late=lesson.is_late,
            is_early=lesson.is_early,
        )
        peak_known = status_known and lesson.is_peak is not None
        peak = completed and lesson.is_peak is True
        hardware = hardware_quality_passed(
            is_camera_off=lesson.is_camera_off,
            is_cpu_usage_high=lesson.is_cpu_usage_high,
            is_network_delay_high=lesson.is_network_delay_high,
        )

        component_values = {
            "FEEDBACK_PRAISE": (
                praise,
                praise_points,
                "CONFIRMED" if praise_known else "SOURCE_MISSING",
            ),
            "FEEDBACK_FAVORITE": (
                favorite,
                favorite_points,
                "CONFIRMED"
                if favorite_attribution_known
                else "SOURCE_MISSING",
            ),
            "PERFECT_COMPLETED": (
                perfect,
                perfect_points,
                "CONFIRMED" if perfect_known else "SOURCE_MISSING",
            ),
            "PEAK_COMPLETED": (
                peak,
                peak_points,
                "CONFIRMED" if peak_known else "SOURCE_MISSING",
            ),
            "CLASS_QUALITY_HARDWARE": (
                hardware is True,
                quality_points,
                "CONFIRMED" if hardware is not None else "SOURCE_MISSING",
            ),
        }
        components: dict[str, dict[str, Any]] = {}
        for code, (awarded, points, evidence_status) in component_values.items():
            score = points if awarded else 0.0
            components[code] = {
                "awarded": bool(awarded),
                "points_per_unit": points,
                "score": round(score, 2),
                "evidence_status": evidence_status,
            }
            if awarded:
                attributed[code]["count"] += 1
                attributed[code]["score"] += score

        user_feedback_score = round(
            components["FEEDBACK_PRAISE"]["score"]
            + components["FEEDBACK_FAVORITE"]["score"],
            2,
        )
        reliability_score = round(
            components["PERFECT_COMPLETED"]["score"]
            + components["PEAK_COMPLETED"]["score"],
            2,
        )
        class_quality_score = round(
            components["CLASS_QUALITY_HARDWARE"]["score"],
            2,
        )
        dimensions = {
            "USER_FEEDBACK": {
                "score": user_feedback_score,
                "components": [
                    components["FEEDBACK_PRAISE"],
                    components["FEEDBACK_FAVORITE"],
                ],
                "component_codes": ["FEEDBACK_PRAISE", "FEEDBACK_FAVORITE"],
            },
            "RELIABILITY": {
                "score": reliability_score,
                "components": [
                    components["PERFECT_COMPLETED"],
                    components["PEAK_COMPLETED"],
                ],
                "component_codes": ["PERFECT_COMPLETED", "PEAK_COMPLETED"],
            },
            "CLASS_QUALITY": {
                "score": class_quality_score,
                "components": [components["CLASS_QUALITY_HARDWARE"]],
                "component_codes": ["CLASS_QUALITY_HARDWARE"],
            },
        }
        results.append(
            _LessonProjection(
                lesson_id=lesson.course_id,
                user_feedback_score=user_feedback_score,
                reliability_score=reliability_score,
                class_quality_score=class_quality_score,
                lesson_total_score=round(
                    user_feedback_score
                    + reliability_score
                    + class_quality_score,
                    2,
                ),
                dimensions=dimensions,
            )
        )
    return results, attributed


def _upsert_lesson_results(
    session: Session,
    projections: Sequence[_LessonProjection],
    *,
    policy_version: str,
    calculated_at: datetime,
) -> int:
    if not projections:
        return 0
    existing = {
        item.lesson_id: item
        for item in session.scalars(
            select(LessonScoreResultRecord).where(
                LessonScoreResultRecord.lesson_id.in_(
                    [item.lesson_id for item in projections]
                )
            )
        ).all()
    }
    changes = 0
    for projection in projections:
        values = {
            "user_feedback_score": projection.user_feedback_score,
            "reliability_score": projection.reliability_score,
            "class_quality_score": projection.class_quality_score,
            "lesson_total_score": projection.lesson_total_score,
            "dimensions": deepcopy(projection.dimensions),
            "score_rule_version": policy_version,
        }
        record = existing.get(projection.lesson_id)
        if record is None:
            session.add(
                LessonScoreResultRecord(
                    lesson_id=projection.lesson_id,
                    projection_revision=1,
                    calculated_at=calculated_at,
                    **values,
                )
            )
            changes += 1
            continue
        changed = any(getattr(record, key) != value for key, value in values.items())
        if not changed:
            continue
        for key, value in values.items():
            setattr(record, key, value)
        record.projection_revision = int(record.projection_revision or 0) + 1
        record.calculated_at = calculated_at
        changes += 1
    return changes


def _capacity_milestone_key(teacher_id: str) -> str:
    return f"CAPACITY_MILESTONE:{CAPACITY_MILESTONE_ID}:{teacher_id}"


def _capacity_milestone_id(teacher_id: str) -> str:
    digest = hashlib.sha256(_capacity_milestone_key(teacher_id).encode("utf-8")).hexdigest()[:32]
    return f"CAPM-{digest}"


def _capacity_milestone_achieved(
    session: Session,
    *,
    teacher: TeacherRecord,
    source_count: int,
    source_status: str,
    threshold: int,
    score_value: float,
    policy_version: str,
    occurred_at: datetime,
) -> tuple[bool, bool]:
    key = _capacity_milestone_key(teacher.teacher_id)
    existing = session.scalar(
        select(ScoreEntryRecord).where(ScoreEntryRecord.idempotency_key == key)
    )
    if existing is not None:
        return True, False
    if source_status != "CONFIRMED" or source_count < threshold:
        return False, False
    session.add(
        ScoreEntryRecord(
            score_entry_id=_capacity_milestone_id(teacher.teacher_id),
            camp_enrollment_id=teacher.camp_enrollment_id,
            lesson_id=None,
            teacher_id=teacher.teacher_id,
            dimension="CAPACITY",
            entry_type="MILESTONE_ACHIEVEMENT",
            delta_score=score_value,
            reason_code=CAPACITY_MILESTONE_REASON_CODE,
            evidence_status="CONFIRMED",
            score_rule_version=policy_version,
            occurred_at=occurred_at,
            recorded_at=occurred_at,
            reversal_of_score_entry_id=None,
            task_assignment_id=None,
            idempotency_key=key,
            payload={
                "milestone_id": CAPACITY_MILESTONE_ID,
                "metric": "teacher_source_wide.peak_slot_cnt",
                "operator": "GTE",
                "threshold": threshold,
                "observed_value": source_count,
                "source_mode": "DERIVED_REAL",
                "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
            },
        )
    )
    return True, True


def _complaint_rule_maps(
    session: Session,
) -> tuple[dict[str, ComplaintRule], dict[str, str]]:
    records = list(
        session.scalars(
            select(ComplaintCategoryRuleRecord).order_by(
                ComplaintCategoryRuleRecord.category_l3_normalized,
                ComplaintCategoryRuleRecord.created_at.desc(),
                ComplaintCategoryRuleRecord.rule_id.desc(),
            )
        ).all()
    )
    rules: dict[str, ComplaintRule] = {}
    ids: dict[str, str] = {}
    for record in records:
        key = normalize_text(record.category_l3_normalized)
        if not key or key in rules:
            continue
        rules[key] = ComplaintRule(
            level2_name=record.category_l2,
            level3_name=record.category_l3,
            source_level_code=record.source_level,
            severity_rank=record.severity_rank,
            route_domain=record.default_route,
        )
        ids[key] = record.rule_id
    return rules, ids


def _l0_complaint_summary(
    lessons: Sequence[LessonSourceWideRecord],
    complaint_rules: Mapping[str, ComplaintRule],
) -> tuple[int, str]:
    count = 0
    for lesson in lessons:
        categories = (
            lesson.complaint_category_l1,
            lesson.complaint_category_l2,
            lesson.complaint_category_l3,
        )
        if not any(normalize_text(value) for value in categories):
            continue
        key = normalize_text(lesson.complaint_category_l3)
        rule = complaint_rules.get(key)
        if rule is None:
            return count, "SOURCE_MISSING"
        source_level = normalize_text(rule.source_level_code).upper()
        if source_level in {"P0", "L0"}:
            count += 1
    return count, "CONFIRMED"


def _task_components(
    session: Session,
    teacher_id: str,
) -> tuple[list[dict[str, Any]], int, int]:
    assignments = list(
        session.scalars(
            select(TaskAssignmentRecord)
            .where(
                TaskAssignmentRecord.teacher_id == teacher_id,
                TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
                TaskAssignmentRecord.task_code.in_(MANDATORY_TASK_CODES),
            )
            .order_by(TaskAssignmentRecord.task_code)
        ).all()
    )
    by_code = {item.task_code: item for item in assignments}
    if set(by_code) != set(MANDATORY_TASK_CODES) or len(assignments) != len(
        MANDATORY_TASK_CODES
    ):
        raise SourceWideProjectionError(
            f"TASK_BASELINE_INCOMPLETE:{teacher_id}"
        )
    template_ids = [item.template_version_id for item in assignments]
    templates = {
        item.row_id: item
        for item in session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.row_id.in_(template_ids)
            )
        ).all()
    }
    components: list[dict[str, Any]] = []
    completed_count = 0
    for code in MANDATORY_TASK_CODES:
        assignment = by_code[code]
        template = templates.get(assignment.template_version_id)
        if template is None:
            raise SourceWideProjectionError(
                f"FIXED_TASK_TEMPLATE_MISSING:{assignment.template_version_id}"
            )
        try:
            points = float((template.payload or {})["score_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceWideProjectionError(
                f"FIXED_TASK_POINTS_INVALID:{code}"
            ) from exc
        completed = assignment.status == "COMPLETED"
        completed_count += int(completed)
        components.append(
            {
                "code": code,
                "dimension": "NEW_TEACHER_TASK",
                "source_scope": "TASK",
                "source_metric": "task_assignments.status",
                "unit_count": 1.0 if completed else 0.0,
                "points_per_unit": points,
                "score": points if completed else 0.0,
                "source_mode": "SYSTEM_TASK_STATUS",
                "lesson_attributed_count": 0,
                "lesson_attributed_score": 0.0,
                "reconciliation_status": "NOT_APPLICABLE",
            }
        )
    return components, len(assignments), completed_count


def _component_payloads(
    session: Session,
    *,
    teacher: TeacherRecord,
    source: TeacherSourceWideRecord | None,
    lessons: Sequence[LessonSourceWideRecord],
    attributed: Mapping[str, Mapping[str, float]],
    policy_context: _PolicyContext,
    complaint_rules: Mapping[str, ComplaintRule],
    occurred_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scoring = policy_context.policy.scoring_items
    source_counts: dict[str, tuple[int, str]] = {}
    for field in (
        "peak_completed_cnt",
        "feedback_praise_cnt",
        "feedback_favorite_cnt",
        "peak_slot_cnt",
        "late_cnt",
        "early_cnt",
        "absent_cnt",
    ):
        source_counts[field] = _nonnegative_source_count(
            getattr(source, field) if source is not None else None,
            field=field,
        )

    task_rows, assignment_count, task_completed_count = _task_components(
        session,
        teacher.teacher_id,
    )
    task_score = round(sum(float(item["score"]) for item in task_rows), 2)
    perfect_count = int(attributed.get("PERFECT_COMPLETED", {}).get("count", 0))
    hardware_count = int(
        attributed.get("CLASS_QUALITY_HARDWARE", {}).get("count", 0)
    )
    capacity_rule = scoring.capacity
    peak_slot_count, peak_slot_status = source_counts["peak_slot_cnt"]
    capacity_achieved, capacity_created = _capacity_milestone_achieved(
        session,
        teacher=teacher,
        source_count=peak_slot_count,
        source_status=peak_slot_status,
        threshold=int(capacity_rule.threshold),
        score_value=float(capacity_rule.score_value),
        policy_version=policy_context.policy.policy_version,
        occurred_at=occurred_at,
    )
    capacity_score = (
        float(capacity_rule.score_value) if capacity_achieved else 0.0
    )

    definitions = [
        (
            "FEEDBACK_PRAISE",
            "USER_FEEDBACK",
            "teacher_source_wide.feedback_praise_cnt",
            source_counts["feedback_praise_cnt"],
            float(scoring.feedback_praise.points_per_unit),
        ),
        (
            "FEEDBACK_FAVORITE",
            "USER_FEEDBACK",
            "teacher_source_wide.feedback_favorite_cnt",
            source_counts["feedback_favorite_cnt"],
            float(scoring.feedback_favorite.points_per_unit),
        ),
        (
            "PERFECT_COMPLETED",
            "RELIABILITY",
            "lesson_source_wide.课程状态+迟到+早退",
            (perfect_count, "DERIVED_REAL"),
            float(scoring.reliability_perfect.points_per_unit),
        ),
        (
            "PEAK_COMPLETED",
            "RELIABILITY",
            "teacher_source_wide.peak_completed_cnt",
            source_counts["peak_completed_cnt"],
            float(scoring.reliability_peak.points_per_unit),
        ),
        (
            "CLASS_QUALITY_HARDWARE",
            "CLASS_QUALITY",
            "lesson_source_wide.未开摄像头+cpu占用过高+网络延迟过高",
            (hardware_count, "DERIVED_REAL"),
            float(scoring.classroom_quality.points_per_unit),
        ),
        (
            CAPACITY_MILESTONE_ID,
            "CAPACITY",
            "teacher_source_wide.peak_slot_cnt",
            (1 if capacity_achieved else 0, "DERIVED_REAL" if capacity_achieved else peak_slot_status),
            float(capacity_rule.score_value),
        ),
    ]
    components: list[dict[str, Any]] = []
    for code, dimension, metric, (unit_count, source_status), points in definitions:
        score = (
            capacity_score
            if code == CAPACITY_MILESTONE_ID
            else round(float(unit_count) * points, 2)
        )
        attributed_value = attributed.get(code, {"count": 0, "score": 0})
        attributed_count = int(attributed_value.get("count", 0))
        attributed_score = round(float(attributed_value.get("score", 0)), 2)
        if code in {CAPACITY_MILESTONE_ID}:
            reconciliation = "NOT_APPLICABLE"
        elif source_status == "SOURCE_MISSING":
            reconciliation = "SOURCE_MISSING"
        elif abs(score - attributed_score) < 1e-9:
            reconciliation = "MATCHED" if score else "MATCHED_ZERO"
        elif attributed_score < score:
            reconciliation = "PARTIAL" if lessons else "SOURCE_MISSING"
        else:
            reconciliation = "MISMATCH"
        components.append(
            {
                "code": code,
                "dimension": dimension,
                "source_scope": (
                    "LESSON"
                    if code in {"PERFECT_COMPLETED", "CLASS_QUALITY_HARDWARE"}
                    else "TEACHER"
                ),
                "source_metric": metric,
                "unit_count": float(unit_count),
                "points_per_unit": points,
                "score": round(score, 2),
                "source_mode": source_status,
                "lesson_attributed_count": attributed_count,
                "lesson_attributed_score": attributed_score,
                "reconciliation_status": reconciliation,
            }
        )
    components.extend(task_rows)

    by_dimension: dict[str, float] = defaultdict(float)
    for component in components:
        by_dimension[str(component["dimension"])] += float(component["score"])
    raw_total = round(sum(by_dimension.values()), 2)
    l0_count, complaint_status = _l0_complaint_summary(
        lessons,
        complaint_rules,
    )
    late_count, late_status = source_counts["late_cnt"]
    early_count, early_status = source_counts["early_cnt"]
    absent_count, absent_status = source_counts["absent_cnt"]
    source_present = source is not None
    graduation_gate = policy_context.policy.hard_gates.graduation
    gold_gate = policy_context.policy.hard_gates.gold
    graduation_current = bool(
        source_present
        and raw_total >= float(policy_context.policy.thresholds.graduation_raw_score)
        and assignment_count == len(MANDATORY_TASK_CODES)
        and task_completed_count == int(graduation_gate.required_mandatory_task_count)
        and complaint_status == "CONFIRMED"
        and l0_count <= int(graduation_gate.maximum_l0_complaint_count)
    )
    attendance_confirmed = all(
        item == "CONFIRMED"
        for item in (late_status, early_status, absent_status)
    )
    gold_current = bool(
        graduation_current
        and raw_total >= float(policy_context.policy.thresholds.gold_raw_score)
        and attendance_confirmed
        and late_count <= int(gold_gate.maximum_late_count)
        and early_count <= int(gold_gate.maximum_early_count)
        and absent_count <= int(gold_gate.maximum_absent_count)
    )
    state = {
        "source_teacher_present": source_present,
        "dimensions": {key: round(value, 2) for key, value in by_dimension.items()},
        "raw_total_score": raw_total,
        "public_total_score": round(
            min(raw_total, float(policy_context.policy.thresholds.gold_external_score)),
            2,
        ),
        "assignment_count": assignment_count,
        "task_completed_count": task_completed_count,
        "task_score": task_score,
        "l0_complaint_count": l0_count,
        "l0_complaint_evidence_status": complaint_status,
        "late_count": late_count,
        "early_count": early_count,
        "absent_count": absent_count,
        "attendance_evidence_status": (
            "CONFIRMED" if attendance_confirmed else "SOURCE_MISSING"
        ),
        "graduation_current_criteria_met": graduation_current,
        "gold_current_criteria_met": gold_current,
        "capacity_milestone_achieved": capacity_achieved,
        "capacity_milestone_created": capacity_created,
    }
    return components, state


def _upsert_components(
    session: Session,
    *,
    teacher: TeacherRecord,
    components: Sequence[Mapping[str, Any]],
    policy_context: _PolicyContext,
    calculated_at: datetime,
    refresh_task_policy: bool = False,
) -> int:
    existing = {
        item.component_code: item
        for item in session.scalars(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher.teacher_id
            )
        ).all()
    }
    changes = 0
    for component in components:
        code = str(component["code"])
        score = float(component["score"])
        attributed_score = float(component["lesson_attributed_score"])
        values = {
            "dimension": str(component["dimension"]),
            "source_scope": str(component["source_scope"]),
            "source_metric": str(component["source_metric"]),
            "unit_count": float(component["unit_count"]),
            "points_per_unit": float(component["points_per_unit"]),
            "current_score": score,
            "lesson_attributed_count": int(component["lesson_attributed_count"]),
            "lesson_attributed_score": attributed_score,
            "unattributed_score": round(score - attributed_score, 2),
            "reconciliation_status": str(component["reconciliation_status"]),
            "score_rule_version": policy_context.policy.policy_version,
            "payload": {
                "source_mode": component["source_mode"],
                "score_config_version_id": policy_context.version_id,
                "source_contract": "SOURCE_WIDE_CURRENT",
            },
        }
        record = existing.get(code)
        if record is None:
            session.add(
                ScoreComponentAccountRecord(
                    teacher_id=teacher.teacher_id,
                    component_code=code,
                    projection_revision=1,
                    calculated_at=calculated_at,
                    **values,
                )
            )
            changes += 1
            continue
        existing_payload = (
            record.payload if isinstance(record.payload, dict) else {}
        )
        preserve_task_settlement_lineage = bool(
            component["dimension"] == "NEW_TEACHER_TASK"
            and existing_payload.get("attribution_contract")
            == "task-status-ledger-is-authoritative"
        )
        if preserve_task_settlement_lineage:
            if refresh_task_policy:
                merged_payload = deepcopy(existing_payload)
                merged_payload.update(
                    {
                        "score_config_version_id": policy_context.version_id,
                        "source_contract": "SOURCE_WIDE_CURRENT",
                    }
                )
                values["score_rule_version"] = (
                    policy_context.policy.policy_version
                )
                values["payload"] = merged_payload
            else:
                values["score_rule_version"] = record.score_rule_version
                values["payload"] = existing_payload
        changed = any(getattr(record, key) != value for key, value in values.items())
        if not changed:
            continue
        for key, value in values.items():
            setattr(record, key, value)
        record.projection_revision = int(record.projection_revision or 0) + 1
        record.calculated_at = calculated_at
        changes += 1
    return changes


def _upsert_score_accounts(
    session: Session,
    *,
    teacher: TeacherRecord,
    dimensions: Mapping[str, float],
    policy_context: _PolicyContext,
    calculated_at: datetime,
    refresh_task_policy: bool = False,
) -> int:
    existing = {
        item.dimension: item
        for item in session.scalars(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher.teacher_id
            )
        ).all()
    }
    changes = 0
    for dimension in (
        "USER_FEEDBACK",
        "RELIABILITY",
        "CLASS_QUALITY",
        "CAPACITY",
        "NEW_TEACHER_TASK",
    ):
        score = round(float(dimensions.get(dimension, 0)), 2)
        payload = {
            "source_mode": (
                "SYSTEM_TASK_STATUS"
                if dimension == "NEW_TEACHER_TASK"
                else "PERSISTED_CURRENT"
            ),
            "score_config_version_id": policy_context.version_id,
            "source_contract": "SOURCE_WIDE_CURRENT",
        }
        record = existing.get(dimension)
        if record is None:
            session.add(
                ScoreAccountRecord(
                    teacher_id=teacher.teacher_id,
                    dimension=dimension,
                    current_score=score,
                    score_rule_version=policy_context.policy.policy_version,
                    version=1,
                    updated_at=calculated_at,
                    payload=payload,
                )
            )
            changes += 1
            continue
        existing_payload = (
            record.payload if isinstance(record.payload, dict) else {}
        )
        preserve_task_ledger_lineage = bool(
            dimension == "NEW_TEACHER_TASK"
            and existing_payload.get("settlement_contract")
            == "shared-fixed-growth.v1"
        )
        values = {
            "current_score": score,
        }
        if preserve_task_ledger_lineage and refresh_task_policy:
            merged_payload = deepcopy(existing_payload)
            merged_payload.update(
                {
                    "score_config_version_id": policy_context.version_id,
                    "source_contract": "SOURCE_WIDE_CURRENT",
                }
            )
            values.update(
                {
                    "score_rule_version": policy_context.policy.policy_version,
                    "payload": merged_payload,
                }
            )
        elif not preserve_task_ledger_lineage:
            values.update(
                {
                    "score_rule_version": policy_context.policy.policy_version,
                    "payload": payload,
                }
            )
        changed = any(getattr(record, key) != value for key, value in values.items())
        if not changed:
            continue
        for key, value in values.items():
            setattr(record, key, value)
        record.version = int(record.version or 0) + 1
        record.updated_at = calculated_at
        changes += 1
    return changes


def refresh_source_wide_score_read_models(
    session: Session,
    *,
    teacher_ids: Iterable[str] | None = None,
    score_policy_payload: Mapping[str, Any] | None = None,
    score_config_version_id: str | None = None,
    occurred_at: datetime | None = None,
    acquire_lock: bool = True,
) -> dict[str, int | str]:
    """Refresh only current source-wide teachers inside the caller transaction.

    This is the full-rebuild primitive for score-policy publication and other
    explicit source-wide rebuilds.  It intentionally does not create trigger
    outputs and never reads or writes the retired snapshot/fact tables.  Task
    settlement uses a narrower task-only path so it never scans lesson rows.
    """

    if acquire_lock:
        acquire_score_projection_lock(session)
    selected_ids = sorted({str(item) for item in teacher_ids or []})
    teacher_query = select(TeacherRecord).where(
        TeacherRecord.source_snapshot_label == SOURCE_SNAPSHOT_LABEL
    )
    if selected_ids:
        teacher_query = teacher_query.where(
            TeacherRecord.teacher_id.in_(selected_ids)
        )
    teachers = list(
        session.scalars(
            teacher_query.order_by(TeacherRecord.teacher_id)
        ).all()
    )
    if selected_ids and len(teachers) != len(selected_ids):
        found = {item.teacher_id for item in teachers}
        raise SourceWideProjectionError(
            "SOURCE_WIDE_TEACHERS_MISSING:"
            f"{sorted(set(selected_ids) - found)[:10]}"
        )
    if not teachers:
        return {
            "teacher_count": 0,
            "lesson_result_changes": 0,
            "component_changes": 0,
            "account_changes": 0,
            "qualification_changes": 0,
        }

    ids = [item.teacher_id for item in teachers]
    teacher_sources = {
        item.tchr_id: item
        for item in session.scalars(
            select(TeacherSourceWideRecord).where(
                TeacherSourceWideRecord.tchr_id.in_(ids)
            )
        ).all()
    }
    lessons_by_teacher: dict[str, list[LessonSourceWideRecord]] = defaultdict(list)
    for lesson in session.scalars(
        select(LessonSourceWideRecord)
        .where(LessonSourceWideRecord.teacher_id.in_(ids))
        .order_by(
            LessonSourceWideRecord.teacher_id,
            LessonSourceWideRecord.lesson_date,
            LessonSourceWideRecord.lesson_time,
            LessonSourceWideRecord.course_id,
        )
    ).all():
        lessons_by_teacher[lesson.teacher_id].append(lesson)

    complaint_rules, _ = _complaint_rule_maps(session)
    policy_context = _policy_context(
        session,
        score_policy_payload=score_policy_payload,
        score_config_version_id=score_config_version_id,
    )
    calculated_at = occurred_at or _utcnow()
    totals = {
        "teacher_count": len(teachers),
        "lesson_result_changes": 0,
        "component_changes": 0,
        "account_changes": 0,
        "qualification_changes": 0,
    }
    for teacher in teachers:
        refreshed = _refresh_teacher(
            session,
            teacher=teacher,
            source=teacher_sources.get(teacher.teacher_id),
            lessons=lessons_by_teacher.get(teacher.teacher_id, []),
            policy_context=policy_context,
            complaint_rules=complaint_rules,
            occurred_at=calculated_at,
            refresh_task_policy=True,
        )
        totals["lesson_result_changes"] += refreshed.lesson_result_changes
        totals["component_changes"] += refreshed.component_changes
        totals["account_changes"] += refreshed.account_changes
        totals["qualification_changes"] += refreshed.qualification_changes
    totals["score_rule_version"] = policy_context.policy.policy_version
    return totals


def _update_qualifications(
    session: Session,
    *,
    teacher: TeacherRecord,
    state: Mapping[str, Any],
    policy_context: _PolicyContext,
    occurred_at: datetime,
) -> int:
    qualification = session.get(TeacherQualificationRecord, teacher.teacher_id)
    graduation_current = bool(state["graduation_current_criteria_met"])
    gold_current = bool(state["gold_current_criteria_met"])

    legacy_graduation_earned = bool(
        teacher.graduation_state == "GRADUATED" or teacher.gold_qualified
    )
    legacy_gold_earned = bool(teacher.gold_qualified)
    previous_graduation_earned = bool(
        qualification.graduation_qualified
        if qualification is not None
        else legacy_graduation_earned
    )
    previous_gold_earned = bool(
        qualification.gold_qualified
        if qualification is not None
        else legacy_gold_earned
    )
    gold_earned = previous_gold_earned or gold_current
    graduation_earned = (
        previous_graduation_earned or graduation_current or gold_earned
    )

    graduation_qualified_at = (
        qualification.graduation_qualified_at
        if qualification is not None
        else None
    )
    gold_qualified_at = (
        qualification.gold_qualified_at if qualification is not None else None
    )
    if not previous_graduation_earned and graduation_earned:
        graduation_qualified_at = occurred_at
    if not previous_gold_earned and gold_earned:
        gold_qualified_at = occurred_at

    gate_results = {
        "source_teacher_present": bool(state["source_teacher_present"]),
        "mandatory_task_assignment_count": int(state["assignment_count"]),
        "mandatory_task_completed_count": int(state["task_completed_count"]),
        "mandatory_task_expected_count": len(MANDATORY_TASK_CODES),
        "l0_complaint_count": int(state["l0_complaint_count"]),
        "l0_complaint_evidence_status": str(
            state["l0_complaint_evidence_status"]
        ),
        "late_count": int(state["late_count"]),
        "early_count": int(state["early_count"]),
        "absent_count": int(state["absent_count"]),
        "attendance_evidence_status": str(state["attendance_evidence_status"]),
        "raw_total_score": float(state["raw_total_score"]),
        "graduation_raw_score_threshold": float(
            policy_context.policy.thresholds.graduation_raw_score
        ),
        "gold_raw_score_threshold": float(
            policy_context.policy.thresholds.gold_raw_score
        ),
    }
    values = {
        "graduation_criteria_met": graduation_current,
        "graduation_qualified": graduation_earned,
        "graduation_qualified_at": graduation_qualified_at,
        "gold_criteria_met": gold_current,
        "gold_qualified": gold_earned,
        "gold_qualified_at": gold_qualified_at,
        "score_rule_version": policy_context.policy.policy_version,
        "gate_results": gate_results,
    }
    changed = 0
    if qualification is None:
        qualification = TeacherQualificationRecord(
            teacher_id=teacher.teacher_id,
            revision=1,
            calculated_at=occurred_at,
            **values,
        )
        session.add(qualification)
        changed = 1
    elif any(getattr(qualification, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(qualification, key, value)
        qualification.revision = int(qualification.revision or 0) + 1
        qualification.calculated_at = occurred_at
        changed = 1

    teacher.graduation_state = "GRADUATED" if graduation_earned else "IN_PROGRESS"
    teacher.gold_qualified = gold_earned
    return changed


def _refresh_teacher(
    session: Session,
    *,
    teacher: TeacherRecord,
    source: TeacherSourceWideRecord | None,
    lessons: Sequence[LessonSourceWideRecord],
    policy_context: _PolicyContext,
    complaint_rules: Mapping[str, ComplaintRule],
    occurred_at: datetime,
    refresh_task_policy: bool = False,
) -> _TeacherRefreshResult:
    projections, attributed = _lesson_projections(lessons, policy_context.policy)
    lesson_changes = _upsert_lesson_results(
        session,
        projections,
        policy_version=policy_context.policy.policy_version,
        calculated_at=occurred_at,
    )
    components, state = _component_payloads(
        session,
        teacher=teacher,
        source=source,
        lessons=lessons,
        attributed=attributed,
        policy_context=policy_context,
        complaint_rules=complaint_rules,
        occurred_at=occurred_at,
    )
    component_changes = _upsert_components(
        session,
        teacher=teacher,
        components=components,
        policy_context=policy_context,
        calculated_at=occurred_at,
        refresh_task_policy=refresh_task_policy,
    )
    account_changes = _upsert_score_accounts(
        session,
        teacher=teacher,
        dimensions=state["dimensions"],
        policy_context=policy_context,
        calculated_at=occurred_at,
        refresh_task_policy=refresh_task_policy,
    )
    qualification_changes = _update_qualifications(
        session,
        teacher=teacher,
        state=state,
        policy_context=policy_context,
        occurred_at=occurred_at,
    )
    payload = deepcopy(teacher.payload or {})
    payload.update(
        {
            "metric_inputs": {
                "new_teacher_task_score": state["task_score"],
                "capacity_score": state["dimensions"].get("CAPACITY", 0),
                "mandatory_task_assignment_count": state["assignment_count"],
                "mandatory_task_completed_count": state["task_completed_count"],
                "mandatory_task_expected_count": len(MANDATORY_TASK_CODES),
                "l0_complaint_cnt": state["l0_complaint_count"],
                "late_cnt": state["late_count"],
                "early_cnt": state["early_count"],
                "absent_cnt": state["absent_count"],
                "capacity_milestone_achieved": state[
                    "capacity_milestone_achieved"
                ],
            },
            "dimensions": [
                {"code": key, "score": value}
                for key, value in state["dimensions"].items()
            ],
            "raw_total_score": state["raw_total_score"],
            "total_score": state["raw_total_score"],
            "base_score": round(
                float(state["task_score"])
                + float(state["dimensions"].get("CAPACITY", 0)),
                2,
            ),
            "external_display_score": state["public_total_score"],
            "graduation_threshold": float(
                policy_context.policy.thresholds.graduation_raw_score
            ),
            "gold_threshold": float(
                policy_context.policy.thresholds.gold_raw_score
            ),
            "graduation_criteria_met": state[
                "graduation_current_criteria_met"
            ],
            "gold_criteria_met": state["gold_current_criteria_met"],
            "graduation_qualified": teacher.graduation_state == "GRADUATED",
            "gold_qualified": bool(teacher.gold_qualified),
            "score_policy_version": policy_context.policy.policy_version,
            "score_rule_version": policy_context.policy.policy_version,
            "score_policy_sha256": policy_context.sha256,
            "score_projection_scope": "SOURCE_WIDE_CURRENT",
        }
    )
    teacher_changed = (
        teacher.payload != payload
        or float(teacher.total_score) != float(state["raw_total_score"])
        or float(teacher.graduation_threshold)
        != float(policy_context.policy.thresholds.graduation_raw_score)
    )
    teacher.payload = payload
    teacher.total_score = float(state["raw_total_score"])
    teacher.graduation_threshold = float(
        policy_context.policy.thresholds.graduation_raw_score
    )
    if teacher_changed or any(
        (lesson_changes, component_changes, account_changes, qualification_changes)
    ):
        teacher.updated_at = occurred_at
    session.flush()
    return _TeacherRefreshResult(
        lesson_result_changes=lesson_changes,
        component_changes=component_changes,
        account_changes=account_changes,
        qualification_changes=qualification_changes,
    )


def _materialize_teacher_triggers(
    session: Session,
    *,
    teacher_ids: Sequence[str],
    lessons: Sequence[LessonSourceWideRecord],
    teachers: Mapping[str, TeacherRecord],
    complaint_rules: Mapping[str, ComplaintRule],
    complaint_rule_ids: Mapping[str, str],
    occurred_at: datetime,
) -> dict[str, int]:
    trigger_rows = [_trigger_lesson_row(item) for item in lessons]
    specs, _, _, _ = build_output_specs(
        trigger_rows,
        complaint_rules=complaint_rules,
        complaint_rule_ids=complaint_rule_ids,
    )
    templates = published_template_map(session)
    return materialize_outputs(
        session,
        specs=specs,
        templates=templates,
        teachers=teachers,
        materialized_at=occurred_at,
        reconcile_existing=True,
        reconcile_teacher_ids=teacher_ids,
        source_mode="DERIVED_REAL",
        evidence_context={
            "source_table": "lesson_source_wide",
            "source_contract": "SOURCE_WIDE_CURRENT",
            "trigger_rule_version": TRIGGER_RULE_VERSION,
        },
    )


class SourceWideWorker:
    """Claim and apply source-wide events with one transaction per teacher group."""

    def __init__(
        self,
        bind: Engine = default_engine,
        *,
        retry_delay: timedelta = timedelta(minutes=5),
        max_retry_delay: timedelta = timedelta(hours=1),
        max_attempts: int = 5,
        retry_jitter_ratio: float = 0.2,
        candidate_scan_limit: int = 100,
    ) -> None:
        if retry_delay < timedelta(0):
            raise ValueError("retry_delay must not be negative")
        if max_retry_delay < retry_delay:
            raise ValueError("max_retry_delay must be at least retry_delay")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not 0 <= retry_jitter_ratio <= 1:
            raise ValueError("retry_jitter_ratio must be between 0 and 1")
        if candidate_scan_limit < 1:
            raise ValueError("candidate_scan_limit must be at least 1")
        self.engine = bind
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self.max_attempts = max_attempts
        self.retry_jitter_ratio = retry_jitter_ratio
        self.candidate_scan_limit = candidate_scan_limit
        self._sessions = sessionmaker(
            bind=bind,
            expire_on_commit=False,
            class_=Session,
        )

    @staticmethod
    def _event_error(
        event: OutboxEventRecord,
        reason: str,
    ) -> SourceWideEventDataError:
        return SourceWideEventDataError(event.outbox_id, reason)

    def _route_event(
        self,
        event: OutboxEventRecord,
    ) -> SourceChangeRoute:
        if not isinstance(event.payload, dict):
            raise self._event_error(event, "OUTBOX_PAYLOAD_MUST_BE_AN_OBJECT")
        try:
            route = route_source_change(event.payload)
        except SourceChangeRoutingError as exc:
            raise self._event_error(
                event,
                f"OUTBOX_ROUTE_INVALID:{exc}",
            ) from exc
        expected_aggregate = (
            "TEACHER_SOURCE_WIDE"
            if route.source_table == "teacher_source_wide"
            else "LESSON_SOURCE_WIDE"
        )
        if (
            event.event_type != EVENT_TYPE
            or event.aggregate_type != expected_aggregate
            or event.aggregate_id != route.source_id
        ):
            raise self._event_error(event, "OUTBOX_AGGREGATE_MISMATCH")
        return route

    def _claim_next_group(
        self,
        session: Session,
        *,
        limit: int,
        excluded_outbox_ids: set[str],
    ) -> list[tuple[OutboxEventRecord, SourceChangeRoute]]:
        conditions = [
            OutboxEventRecord.status == literal_column("'PENDING'"),
            OutboxEventRecord.event_type == EVENT_TYPE,
            OutboxEventRecord.aggregate_type.in_(SOURCE_AGGREGATE_TYPES),
            OutboxEventRecord.available_at <= _utcnow(),
        ]
        if excluded_outbox_ids:
            conditions.append(
                OutboxEventRecord.outbox_id.notin_(excluded_outbox_ids)
            )
        candidates = list(
            session.scalars(
                select(OutboxEventRecord)
                .where(*conditions)
                .order_by(
                    OutboxEventRecord.available_at,
                    OutboxEventRecord.created_at,
                    OutboxEventRecord.outbox_id,
                )
                .with_for_update(skip_locked=True)
                .limit(max(limit, self.candidate_scan_limit))
            ).all()
        )
        if not candidates:
            return []
        first = candidates[0]
        first_route = self._route_event(first)
        group: list[tuple[OutboxEventRecord, SourceChangeRoute]] = [
            (first, first_route)
        ]
        affected = set(first_route.affected_teacher_ids)
        remaining = candidates[1:]
        changed = True
        while changed and len(group) < limit:
            changed = False
            still_remaining: list[OutboxEventRecord] = []
            for event in remaining:
                if len(group) >= limit:
                    still_remaining.append(event)
                    continue
                try:
                    route = self._route_event(event)
                except SourceWideEventDataError:
                    still_remaining.append(event)
                    continue
                if affected.intersection(route.affected_teacher_ids):
                    group.append((event, route))
                    before = len(affected)
                    affected.update(route.affected_teacher_ids)
                    changed = changed or len(affected) != before
                else:
                    still_remaining.append(event)
            remaining = still_remaining
        return group

    def _process_group(
        self,
        session: Session,
        grouped: Sequence[tuple[OutboxEventRecord, SourceChangeRoute]],
    ) -> dict[str, int]:
        acquire_score_projection_lock(session)
        now = _utcnow()
        affected_teacher_ids = {
            teacher_id
            for _, route in grouped
            for teacher_id in route.affected_teacher_ids
        }
        lesson_ids = {
            route.source_id
            for _, route in grouped
            if route.source_table == "lesson_source_wide"
        }
        current_event_lessons = (
            list(
                session.scalars(
                    select(LessonSourceWideRecord).where(
                        LessonSourceWideRecord.course_id.in_(lesson_ids)
                    )
                ).all()
            )
            if lesson_ids
            else []
        )
        affected_teacher_ids.update(
            item.teacher_id for item in current_event_lessons
        )
        normalized_teacher_ids = sorted(affected_teacher_ids)

        teacher_sources = {
            item.tchr_id: item
            for item in session.scalars(
                select(TeacherSourceWideRecord).where(
                    TeacherSourceWideRecord.tchr_id.in_(normalized_teacher_ids)
                )
            ).all()
        }
        teachers: dict[str, TeacherRecord] = {}
        created_count = 0
        for teacher_id in normalized_teacher_ids:
            teacher, created = _project_teacher_identity(
                session,
                teacher_id,
                source=teacher_sources.get(teacher_id),
                occurred_at=now,
            )
            if teacher is not None:
                teachers[teacher_id] = teacher
                created_count += int(created)

        for lesson in current_event_lessons:
            if lesson.teacher_id not in teachers:
                raise SourceWideProjectionError(
                    f"LESSON_SOURCE_TEACHER_NOT_FOUND:{lesson.teacher_id}"
                )

        handlers = {
            handler
            for _, route in grouped
            for handler in route.handlers
        }
        needs_scoring = bool(handlers & _SCORE_OR_QUALIFICATION_HANDLERS)
        needs_triggers = bool(handlers & _TRIGGER_HANDLERS)
        lessons = (
            list(
                session.scalars(
                    select(LessonSourceWideRecord)
                    .where(
                        LessonSourceWideRecord.teacher_id.in_(
                            sorted(teachers)
                        )
                    )
                    .order_by(
                        LessonSourceWideRecord.teacher_id,
                        LessonSourceWideRecord.lesson_date,
                        LessonSourceWideRecord.lesson_time,
                        LessonSourceWideRecord.course_id,
                    )
                ).all()
            )
            if teachers and (needs_scoring or needs_triggers)
            else []
        )
        lessons_by_teacher: dict[str, list[LessonSourceWideRecord]] = defaultdict(list)
        for lesson in lessons:
            lessons_by_teacher[lesson.teacher_id].append(lesson)

        complaint_rules, complaint_rule_ids = (
            _complaint_rule_maps(session)
            if needs_scoring or needs_triggers
            else ({}, {})
        )
        policy_context = _policy_context(session) if needs_scoring else None
        teacher_refreshes = 0
        lesson_result_changes = 0
        component_changes = 0
        account_changes = 0
        qualification_changes = 0
        if needs_scoring:
            assert policy_context is not None
            for teacher_id in sorted(teachers):
                refreshed = _refresh_teacher(
                    session,
                    teacher=teachers[teacher_id],
                    source=teacher_sources.get(teacher_id),
                    lessons=lessons_by_teacher.get(teacher_id, []),
                    policy_context=policy_context,
                    complaint_rules=complaint_rules,
                    occurred_at=now,
                )
                teacher_refreshes += 1
                lesson_result_changes += refreshed.lesson_result_changes
                component_changes += refreshed.component_changes
                account_changes += refreshed.account_changes
                qualification_changes += refreshed.qualification_changes

        trigger_counts: dict[str, int] = {}
        if needs_triggers and teachers:
            trigger_counts = _materialize_teacher_triggers(
                session,
                teacher_ids=sorted(teachers),
                lessons=lessons,
                teachers=teachers,
                complaint_rules=complaint_rules,
                complaint_rule_ids=complaint_rule_ids,
                occurred_at=now,
            )

        for event, _ in grouped:
            event.attempt_count = int(event.attempt_count or 0) + 1
            event.status = "PUBLISHED"
            event.last_error = None
            event.published_at = now
        return {
            "teachers_created": created_count,
            "teacher_refreshes": teacher_refreshes,
            "lesson_result_changes": lesson_result_changes,
            "component_changes": component_changes,
            "account_changes": account_changes,
            "qualification_changes": qualification_changes,
            "trigger_matches_created": int(
                trigger_counts.get("trigger_matches_created", 0)
            ),
        }

    def _retry_delay_for(
        self,
        *,
        outbox_id: str,
        attempt_number: int,
    ) -> timedelta:
        base_seconds = self.retry_delay.total_seconds() * (
            2 ** max(attempt_number - 1, 0)
        )
        digest = hashlib.sha256(
            f"{outbox_id}:{attempt_number}".encode("utf-8")
        ).digest()
        unit = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
        jitter = 1 + ((unit * 2) - 1) * self.retry_jitter_ratio
        return timedelta(
            seconds=min(
                max(base_seconds * jitter, 0),
                self.max_retry_delay.total_seconds(),
            )
        )

    def _record_failures(
        self,
        outbox_ids: Sequence[str],
        exc: Exception,
    ) -> dict[str, str | None]:
        safe_message = (
            f"{type(exc).__name__}:{exc}"
            if isinstance(exc, SourceWideProjectionError)
            else type(exc).__name__
        )[:1000]
        try:
            with self._sessions() as session, session.begin():
                events = list(
                    session.scalars(
                        select(OutboxEventRecord)
                        .where(
                            OutboxEventRecord.outbox_id.in_(outbox_ids),
                            OutboxEventRecord.status
                            == literal_column("'PENDING'"),
                            OutboxEventRecord.event_type == EVENT_TYPE,
                        )
                        .order_by(OutboxEventRecord.outbox_id)
                        .with_for_update(skip_locked=True)
                    ).all()
                )
                states = {outbox_id: None for outbox_id in outbox_ids}
                for event in events:
                    attempt_number = int(event.attempt_count or 0) + 1
                    event.attempt_count = attempt_number
                    event.last_error = safe_message
                    event.published_at = None
                    if attempt_number >= self.max_attempts:
                        event.status = "DEAD_LETTER"
                        event.available_at = _utcnow()
                        states[event.outbox_id] = "DEAD_LETTER"
                    else:
                        event.available_at = _utcnow() + self._retry_delay_for(
                            outbox_id=event.outbox_id,
                            attempt_number=attempt_number,
                        )
                        states[event.outbox_id] = "RETRY_SCHEDULED"
                return states
        except SQLAlchemyError:
            raise
        except Exception:
            return {outbox_id: None for outbox_id in outbox_ids}

    def run_once(self, *, max_events: int = 25) -> dict[str, Any]:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        result: dict[str, Any] = {
            "claimed": 0,
            "published": 0,
            "failed": 0,
            "dead_lettered": 0,
            "coalesced": 0,
            "teachers_created": 0,
            "teacher_refreshes": 0,
            "lesson_result_changes": 0,
            "component_changes": 0,
            "account_changes": 0,
            "qualification_changes": 0,
            "trigger_matches_created": 0,
        }
        attempted = 0
        excluded_outbox_ids: set[str] = set()
        while attempted < max_events:
            failure_ids: list[str] = []
            try:
                group_result: dict[str, int]
                with self._sessions() as session, session.begin():
                    grouped = self._claim_next_group(
                        session,
                        limit=max_events - attempted,
                        excluded_outbox_ids=excluded_outbox_ids,
                    )
                    if not grouped:
                        return result
                    failure_ids = [event.outbox_id for event, _ in grouped]
                    group_result = self._process_group(session, grouped)
                group_count = len(failure_ids)
                attempted += group_count
                result["claimed"] += group_count
                result["published"] += group_count
                result["coalesced"] += max(group_count - 1, 0)
                for key, value in group_result.items():
                    result[key] += value
            except Exception as exc:
                if isinstance(exc, SourceWideEventDataError):
                    failure_ids = [exc.outbox_id]
                failure_ids = list(dict.fromkeys(failure_ids))
                if not failure_ids:
                    raise
                attempted += len(failure_ids)
                result["claimed"] += len(failure_ids)
                result["failed"] += len(failure_ids)
                excluded_outbox_ids.update(failure_ids)
                states = self._record_failures(failure_ids, exc)
                result["dead_lettered"] += sum(
                    state == "DEAD_LETTER" for state in states.values()
                )
        return result


def process_source_wide_events_once(
    bind: Engine = default_engine,
    *,
    max_events: int = 25,
) -> dict[str, Any]:
    return SourceWideWorker(bind).run_once(max_events=max_events)


__all__ = [
    "EVENT_TYPE",
    "SOURCE_AGGREGATE_TYPES",
    "SourceWideEventDataError",
    "SourceWideProjectionError",
    "SourceWideWorker",
    "process_source_wide_events_once",
]
