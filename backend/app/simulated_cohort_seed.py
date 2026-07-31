"""Deterministic, explicit test-data seed for a balanced teacher cohort.

This module is never called during application startup.  It exists only for
manual test-environment seeding and marks every generated fact as simulation
data so it cannot be confused with the imported business baseline.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from copy import deepcopy
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, MetaData, Table, delete, func, or_, select, text
from sqlalchemy.orm import Session

from .config_models import (
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .database import session_scope
from .db_models import (
    AgentDecisionRecord,
    AuditEventRecord,
    ComplaintCategoryRuleRecord,
    DataImportBatchRecord,
    IdempotencyRecord,
    LessonDimensionScoreRecord,
    LessonFactRecord,
    NotificationEventRecord,
    NotificationRecord,
    OpsCaseRecord,
    OpsDecisionRecord,
    OutboundOutputRecord,
    OutboxEventRecord,
    PersonalizedTriggerMatchRecord,
    ProviderCallRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    SourceRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from .lesson_ingestion import (
    _LessonRow,
    _build_output_specs,
    _materialize_outputs,
    _template_map,
)
from .lesson_quality import hardware_quality_passed, is_perfect_lesson
from .task_catalog import MANDATORY_TASK_CODES


SCENARIO = "BALANCED_TEACHER_COHORT_20260728_V1"
BATCH_ID = "SIM-COHORT-20260728-V1"
SNAPSHOT_LABEL = "simulation-balanced-2026-07-28-v1"
SOURCE_SHEET = "SIMULATED_COHORT"
DEFAULT_SEED = 20260728
DEFAULT_TEACHER_COUNT = 20
TEACHER_PREFIX = "SIM260728"
ACTOR = "TIT_TEST_SIMULATOR"
TEACHER_ACTOR = "SIMULATED_TEACHER_APP"
FIXED_TASK_CODES = MANDATORY_TASK_CODES
DIMENSION_ORDER = (
    "USER_FEEDBACK",
    "RELIABILITY",
    "CLASS_QUALITY",
    "CAPACITY",
    "NEW_TEACHER_TASK",
)
TASK_STATUS_PATHS = {
    "ASSIGNED": (),
    "VIEWED": ("VIEWED",),
    "IN_PROGRESS": ("VIEWED", "IN_PROGRESS"),
    "SUBMITTED": ("VIEWED", "IN_PROGRESS", "SUBMITTED"),
    "UNDER_REVIEW": (
        "VIEWED",
        "IN_PROGRESS",
        "SUBMITTED",
        "UNDER_REVIEW",
    ),
    "FAILED": ("VIEWED", "IN_PROGRESS", "FAILED"),
    "COMPLETED": (
        "VIEWED",
        "IN_PROGRESS",
        "SUBMITTED",
        "UNDER_REVIEW",
        "COMPLETED",
    ),
}
PERSONALIZED_STATUS_SEQUENCE = (
    "ASSIGNED",
    "VIEWED",
    "IN_PROGRESS",
    "IN_PROGRESS",
    "SUBMITTED",
    "UNDER_REVIEW",
    "FAILED",
    "COMPLETED",
)

_NAMES = (
    "Ava Carter",
    "Liam Brooks",
    "Maya Bennett",
    "Noah Collins",
    "Ella Foster",
    "Ethan Reed",
    "Sofia Hayes",
    "Lucas Morgan",
    "Chloe Turner",
    "Mason Perry",
    "Grace Cooper",
    "Leo Richardson",
    "Nora Bailey",
    "Owen Ward",
    "Zoe Murphy",
    "Caleb Hughes",
    "Mia Sanders",
    "Henry Powell",
    "Lily Ross",
    "Jack Coleman",
)

_LOCATIONS = (
    ("Philippines", "Asia/Manila"),
    ("United States", "America/New_York"),
    ("United Kingdom", "Europe/London"),
    ("South Africa", "Africa/Johannesburg"),
)

_NEGATIVE_LABELS = (
    "Needs clearer explanations",
    "Insufficient correction",
    "Low student engagement",
)


@dataclass(frozen=True)
class SimulatedTeacherPlan:
    teacher_id: str
    name: str
    country: str
    teacher_timezone: str
    camp_day: int
    lesson_count: int
    completed_task_count: int
    employment_status: str
    task_statuses: dict[str, str]


@dataclass(frozen=True)
class SimulatedLesson:
    lesson_id: str
    source_appoint_id: str
    teacher_id: str
    camp_enrollment_id: str
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    lesson_local_date: date
    lesson_local_time: time
    lesson_lifecycle_status: str
    student_id_hash: str
    is_late: bool | None
    is_early: bool | None
    is_false_early_leave: bool | None
    is_peak: bool | None
    has_negative_feedback_tag: bool | None
    feedback_detail: str | None
    negative_tag_values: list[str]
    absence_reason_detail: str | None
    is_blocked: bool | None
    is_favorited: bool | None
    has_positive_feedback_tag: bool | None
    positive_tag_value: str | None
    is_rebooked: bool | None
    is_camera_off: bool | None
    is_cpu_usage_high: bool | None
    is_network_delay_high: bool | None


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _score_config(session: Session) -> tuple[dict[str, Any], str]:
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
        raise RuntimeError("A published SCORE_GRADUATION config is required")
    payload = ScoreGraduationConfig.model_validate(record.payload).model_dump(
        mode="json"
    )
    if payload["policy_version"] not in {"v1", "v8", "v9", "v10"}:
        raise RuntimeError(
            "The reviewed simulation projection currently supports score policy "
            f"current v1 or legacy v8-v10; published={payload['policy_version']}"
        )
    return payload, record.version_id


def _snapshot_table(session: Session) -> Table:
    """Reflect the live table so an unapplied next migration cannot block seeding."""

    return Table(
        "teacher_metric_snapshots",
        MetaData(),
        autoload_with=session.get_bind(),
    )


def _stratified_values(
    rng: random.Random,
    bands: tuple[tuple[int, int, int], ...],
    *,
    force_first: int | None = None,
    force_last: int | None = None,
) -> list[int]:
    values: list[int] = []
    for lower, upper, count in bands:
        population = list(range(lower, upper + 1))
        values.extend(
            rng.sample(population, count)
            if count <= len(population)
            else rng.choices(population, k=count)
        )
    values.sort()
    if force_first is not None:
        values[0] = force_first
    if force_last is not None:
        values[-1] = force_last
    return sorted(values)


def build_balanced_plan(
    *,
    seed: int = DEFAULT_SEED,
    teacher_count: int = DEFAULT_TEACHER_COUNT,
) -> list[SimulatedTeacherPlan]:
    if teacher_count != DEFAULT_TEACHER_COUNT:
        raise ValueError("This reviewed scenario requires exactly 20 teachers")
    rng = random.Random(seed)
    camp_days = _stratified_values(
        rng,
        (
            (0, 6, 5),
            (7, 14, 5),
            (15, 22, 5),
            (23, 30, 5),
        ),
        force_first=0,
        force_last=30,
    )
    lesson_counts = _stratified_values(
        rng,
        (
            (0, 39, 4),
            (40, 79, 4),
            (80, 119, 4),
            (120, 159, 4),
            (160, 200, 4),
        ),
        force_first=0,
        force_last=200,
    )
    mandatory_task_count = len(FIXED_TASK_CODES)
    task_band_edges = [
        (index * (mandatory_task_count + 1)) // 5
        for index in range(6)
    ]
    completed_task_counts = _stratified_values(
        rng,
        tuple(
            (
                task_band_edges[index],
                (
                    mandatory_task_count
                    if index == 4
                    else task_band_edges[index + 1] - 1
                ),
                4,
            )
            for index in range(5)
        ),
        force_first=0,
        force_last=mandatory_task_count,
    )
    employment_statuses = ["on"] * 14 + ["off"] * 4 + ["hei"] * 2
    plans: list[SimulatedTeacherPlan] = []
    for index in range(teacher_count):
        teacher_id = f"{TEACHER_PREFIX}{index + 1:02d}"
        completed_codes = set(
            rng.sample(
                list(FIXED_TASK_CODES),
                min(completed_task_counts[index], len(FIXED_TASK_CODES)),
            )
        )
        statuses: dict[str, str] = {}
        for code in FIXED_TASK_CODES:
            if code in completed_codes:
                statuses[code] = "COMPLETED"
                continue
            status_pool = (
                ("ASSIGNED", "VIEWED", "IN_PROGRESS")
                if camp_days[index] <= 6
                else (
                    "ASSIGNED",
                    "VIEWED",
                    "IN_PROGRESS",
                    "SUBMITTED",
                    "UNDER_REVIEW",
                    "FAILED",
                )
            )
            statuses[code] = rng.choice(status_pool)
        country, teacher_timezone = _LOCATIONS[index % len(_LOCATIONS)]
        plans.append(
            SimulatedTeacherPlan(
                teacher_id=teacher_id,
                name=f"{_NAMES[index]} · Sim",
                country=country,
                teacher_timezone=teacher_timezone,
                camp_day=camp_days[index],
                lesson_count=lesson_counts[index],
                completed_task_count=completed_task_counts[index],
                employment_status=employment_statuses[index],
                task_statuses=statuses,
            )
        )
    return plans


def _lesson_rows(
    plan: SimulatedTeacherPlan,
    *,
    seed: int,
    as_of: datetime,
) -> list[SimulatedLesson]:
    rng = random.Random(seed)
    if plan.lesson_count == 0:
        return []
    teacher_zone = ZoneInfo(plan.teacher_timezone)
    local_as_of = as_of.astimezone(teacher_zone)
    onboarding_date = local_as_of.date() - timedelta(days=plan.camp_day)
    lessons: list[SimulatedLesson] = []
    student_pool_size = max(4, min(45, plan.lesson_count // 3 or 4))
    clean_attendance = plan.completed_task_count == len(FIXED_TASK_CODES)
    for index in range(plan.lesson_count):
        span_days = max(plan.camp_day, 1)
        day_offset = min(
            span_days,
            int(index * (span_days + 1) / plan.lesson_count),
        )
        lesson_date = onboarding_date + timedelta(days=day_offset)
        hour = 5 + (index * 3 + rng.randrange(0, 4)) % 18
        minute = rng.choice((0, 30))
        local_start = datetime.combine(
            lesson_date,
            time(hour=hour, minute=minute),
            tzinfo=teacher_zone,
        )
        if local_start > local_as_of:
            local_start = local_as_of - timedelta(minutes=30)
        scheduled_start_at = local_start.astimezone(timezone.utc)
        scheduled_end_at = scheduled_start_at + timedelta(minutes=25)

        roll = rng.random()
        if roll < (0.97 if clean_attendance else 0.91):
            status = "end"
        elif not clean_attendance and roll < 0.96:
            status = "teacher_absent"
        else:
            status = "cancelled"
        completed = status == "end"
        is_late = (
            False
            if completed and clean_attendance
            else rng.random() < 0.08
            if completed
            else None
        )
        is_early = (
            False
            if completed and clean_attendance
            else rng.random() < 0.05
            if completed
            else None
        )
        is_false_early_leave = (
            False
            if completed and clean_attendance
            else rng.random() < 0.02
            if completed
            else None
        )
        if status == "teacher_absent":
            absence_reason = rng.choice(
                (
                    "Unfilled Lesson Memo",
                    "Teacher No Show",
                    "Emergency Leave",
                )
            )
        elif completed and rng.random() < 0.02:
            absence_reason = "Unfilled Lesson Memo"
        else:
            absence_reason = None

        negative = completed and rng.random() < 0.06
        repeated_label = _NEGATIVE_LABELS[(index // 2) % len(_NEGATIVE_LABELS)]
        negative_labels = [repeated_label] if negative else []
        feedback_detail = (
            f"3,{repeated_label}" if negative else None
        )
        positive = completed and not negative and rng.random() < 0.18
        favorite = completed and rng.random() < 0.10
        student_index = rng.randrange(student_pool_size)
        student_id_hash = hashlib.sha256(
            f"{SCENARIO}:{plan.teacher_id}:student:{student_index}".encode("utf-8")
        ).hexdigest()
        lesson_id = f"{plan.teacher_id}-L{index + 1:03d}"
        lessons.append(
            SimulatedLesson(
                lesson_id=lesson_id,
                source_appoint_id=f"SIM-APPOINT-{plan.teacher_id}-{index + 1:03d}",
                teacher_id=plan.teacher_id,
                camp_enrollment_id=f"CAMP-{plan.teacher_id}",
                scheduled_start_at=scheduled_start_at,
                scheduled_end_at=scheduled_end_at,
                lesson_local_date=local_start.date(),
                lesson_local_time=local_start.time().replace(tzinfo=None),
                lesson_lifecycle_status=status,
                student_id_hash=student_id_hash,
                is_late=is_late,
                is_early=is_early,
                is_false_early_leave=is_false_early_leave,
                is_peak=(rng.random() < 0.45) if completed else None,
                has_negative_feedback_tag=negative if completed else None,
                feedback_detail=feedback_detail,
                negative_tag_values=negative_labels,
                absence_reason_detail=absence_reason,
                is_blocked=(rng.random() < 0.015) if completed else None,
                is_favorited=favorite if completed else None,
                has_positive_feedback_tag=positive if completed else None,
                positive_tag_value="Positive feedback" if positive else None,
                is_rebooked=(rng.random() < 0.14) if completed else None,
                is_camera_off=(rng.random() < 0.03) if completed else None,
                is_cpu_usage_high=(rng.random() < 0.04) if completed else None,
                is_network_delay_high=(rng.random() < 0.05) if completed else None,
            )
        )
    return lessons


def _metric_payload(
    lessons: list[SimulatedLesson],
    *,
    peak_slot_cnt: int,
    batch_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    completed = [item for item in lessons if item.lesson_lifecycle_status == "end"]
    late_count = sum(item.is_late is True for item in completed)
    early_count = sum(item.is_early is True for item in completed)
    on_time_count = sum(
        item.is_late is False and item.is_early is False
        for item in completed
    )
    peak_completed_count = sum(item.is_peak is True for item in completed)
    perfect_count = sum(
        is_perfect_lesson(
            lesson_lifecycle_status=item.lesson_lifecycle_status,
            is_late=item.is_late,
            is_early=item.is_early,
        )
        for item in completed
    )
    praise_count = sum(
        item.has_positive_feedback_tag is True for item in completed
    )
    favorited_students = {
        item.student_id_hash
        for item in completed
        if item.is_favorited is True
    }
    metrics = {
        "total_completed_cnt": len(completed),
        "peak_completed_cnt": peak_completed_count,
        "peak_slot_cnt": peak_slot_cnt,
        "perfect_cnt": perfect_count,
        "on_time_completed_cnt": on_time_count,
        "feedback_praise_cnt": praise_count,
        "feedback_favorite_cnt": len(favorited_students),
        "completed_again_student_15d_cnt": sum(
            item.is_rebooked is True for item in completed
        ),
        "late_cnt": late_count,
        "early_cnt": early_count,
        "absent_cnt": sum(
            item.lesson_lifecycle_status == "teacher_absent" for item in lessons
        ),
        "real_absent_cnt": sum(
            item.lesson_lifecycle_status == "teacher_absent" for item in lessons
        ),
        "severe_redline_event": False,
        "l0_complaint_cnt": 0,
        "capacity_score": 10.0 if peak_slot_cnt >= 40 else 0.0,
        "capacity_milestone_achieved": peak_slot_cnt >= 40,
        "capacity_milestone_currently_meets_threshold": peak_slot_cnt >= 40,
        "capacity_milestone_id": "CAPACITY_PEAK_SLOT_40",
        "capacity_milestone_settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
        "new_teacher_task_score": 0.0,
        "class_quality_no_issue_rate": 1.0,
    }
    provenance = {
        key: {
            "source_mode": "MOCK_SIMULATION",
            "source_field": key,
            "batch_id": batch_id,
            "note": "Deterministic balanced test-cohort simulation.",
        }
        for key in metrics
    }
    return metrics, provenance


def _source_record(
    *,
    source_record_id: str,
    row_number: int,
    business_key: str,
    teacher_id: str,
    lesson_id: str | None,
    occurred_at: datetime,
    raw_payload: dict[str, Any],
) -> SourceRecord:
    return SourceRecord(
        source_record_id=source_record_id,
        batch_id=BATCH_ID,
        source_sheet=SOURCE_SHEET,
        source_row_number=row_number,
        business_key=business_key,
        teacher_id=teacher_id,
        lesson_id=lesson_id,
        occurred_at=occurred_at,
        row_sha256=_sha256_json(raw_payload),
        raw_payload=raw_payload,
        created_at=occurred_at,
    )


def _insert_base_facts(
    session: Session,
    plans: list[SimulatedTeacherPlan],
    *,
    seed: int,
    as_of: datetime,
) -> tuple[list[str], int]:
    existing_batch = session.get(DataImportBatchRecord, BATCH_ID)
    teacher_ids = [plan.teacher_id for plan in plans]
    if existing_batch is not None:
        if (existing_batch.payload or {}).get("scenario") != SCENARIO:
            raise RuntimeError(f"Batch ID {BATCH_ID} is already used by another source")
        stored_teacher_count = len(
            session.scalars(
                select(TeacherRecord.teacher_id).where(
                    TeacherRecord.teacher_id.in_(teacher_ids)
                )
            ).all()
        )
        stored_lesson_count = int(
            session.scalar(
                select(func.count())
                .select_from(LessonFactRecord)
                .where(LessonFactRecord.teacher_id.in_(teacher_ids))
            )
            or 0
        )
        expected_lessons = sum(plan.lesson_count for plan in plans)
        if (
            stored_teacher_count != len(plans)
            or stored_lesson_count != expected_lessons
        ):
            raise RuntimeError(
                "Existing simulation batch is incomplete; refuse silent repair "
                f"(teachers={stored_teacher_count}/{len(plans)}, "
                f"lessons={stored_lesson_count}/{expected_lessons})"
            )
        return teacher_ids, expected_lessons

    collisions = session.scalars(
        select(TeacherRecord.teacher_id).where(
            TeacherRecord.teacher_id.in_(teacher_ids)
        )
    ).all()
    if collisions:
        raise RuntimeError(f"Simulation teacher IDs already exist: {collisions[:5]}")

    lesson_rows_by_teacher = {
        plan.teacher_id: _lesson_rows(
            plan,
            seed=seed + index * 997,
            as_of=as_of,
        )
        for index, plan in enumerate(plans)
    }
    lesson_count = sum(len(items) for items in lesson_rows_by_teacher.values())
    source_sha256 = _sha256_json(
        {
            "scenario": SCENARIO,
            "seed": seed,
            "plans": [
                {
                    "teacher_id": plan.teacher_id,
                    "camp_day": plan.camp_day,
                    "lesson_count": plan.lesson_count,
                    "completed_task_count": plan.completed_task_count,
                }
                for plan in plans
            ],
        }
    )
    policy_payload, policy_version_id = _score_config(session)
    policy_sha256 = _sha256_json(policy_payload)
    session.add(
        DataImportBatchRecord(
            batch_id=BATCH_ID,
            source_kind="SIMULATED_COHORT",
            sync_mode="MANUAL_BASELINE",
            source_system="TIT_TEST_SIMULATOR",
            source_filename="generated-balanced-cohort-v1",
            source_uri=f"simulation://{SCENARIO}",
            source_sha256=source_sha256,
            source_sheet=SOURCE_SHEET,
            snapshot_label=SNAPSHOT_LABEL,
            data_mode="MIXED",
            column_count=12,
            row_count=len(plans) + lesson_count,
            header=[
                "teacher_id",
                "camp_day",
                "lesson_count",
                "task_statuses",
                "lesson_id",
                "lesson_status",
                "is_late",
                "is_early",
                "is_peak",
                "feedback",
                "quality",
                "source_mode",
            ],
            status="COMPLETED",
            imported_at=as_of,
            payload={
                "scenario": SCENARIO,
                "mock_only": True,
                "seed": seed,
                "teacher_count": len(plans),
                "lesson_count": lesson_count,
                "distribution": {
                    "camp_day": "4 strata across 0-30 days",
                    "lesson_count": "5 strata across 0-200 lessons",
                    "mandatory_completion": (
                        f"5 strata across 0-{len(FIXED_TASK_CODES)} tasks"
                    ),
                },
            },
            created_at=as_of,
            updated_at=as_of,
        )
    )
    session.flush()

    templates = {
        item.template_id: item
        for item in session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.template_id.in_(FIXED_TASK_CODES),
                TaskTemplateRecord.status == "PUBLISHED",
            )
        ).all()
    }
    if set(templates) != set(FIXED_TASK_CODES):
        raise RuntimeError("Published current mandatory templates are required")

    snapshot_table = _snapshot_table(session)
    source_row_number = 1
    for index, plan in enumerate(plans):
        lessons = lesson_rows_by_teacher[plan.teacher_id]
        teacher_zone = ZoneInfo(plan.teacher_timezone)
        local_as_of = as_of.astimezone(teacher_zone)
        onboard_date = local_as_of.date() - timedelta(days=plan.camp_day)
        first_booked_date = (
            min(item.lesson_local_date for item in lessons) if lessons else None
        )
        peak_slot_cnt = (
            random.Random(seed + index * 31).randint(40, 80)
            if index % 2
            else random.Random(seed + index * 31).randint(0, 39)
        )
        metrics, provenance = _metric_payload(
            lessons,
            peak_slot_cnt=peak_slot_cnt,
            batch_id=BATCH_ID,
        )
        teacher_raw = {
            "scenario": SCENARIO,
            "mock_only": True,
            "teacher_id": plan.teacher_id,
            "name": plan.name,
            "camp_day": plan.camp_day,
            "lesson_count": plan.lesson_count,
            "completed_task_count": plan.completed_task_count,
            "employment_status": plan.employment_status,
        }
        teacher_source_id = f"SRC-{BATCH_ID}-T{index + 1:02d}"
        session.add(
            _source_record(
                source_record_id=teacher_source_id,
                row_number=source_row_number,
                business_key=f"teacher:{plan.teacher_id}",
                teacher_id=plan.teacher_id,
                lesson_id=None,
                occurred_at=as_of,
                raw_payload=teacher_raw,
            )
        )
        source_row_number += 1
        teacher_payload = {
            **teacher_raw,
            "camp_enrollment_id": f"CAMP-{plan.teacher_id}",
            "country": plan.country,
            "timezone": plan.teacher_timezone,
            "graduation_state": "IN_PROGRESS",
            "graduation_qualified": False,
            "gold_qualified": False,
            "graduation_threshold": 100.0,
            "employment_status": plan.employment_status,
            "bu": "TIT_SIMULATION",
            "based_type": "HOME_BASED",
            "teach_area_type": "OVERSEAS",
            "first_booked_date": (
                first_booked_date.isoformat() if first_booked_date else None
            ),
            "is_cpl_tesol": plan.camp_day >= 7,
            "is_self_introduce": plan.camp_day >= 3,
            "metric_inputs": metrics,
            "metric_provenance": provenance,
            "dimensions": [],
            "raw_total_score": 0.0,
            "total_score": 0.0,
            "external_display_score": 0.0,
            "data_mode": "MOCK",
            "source_batch_id": BATCH_ID,
            "source_snapshot_label": SNAPSHOT_LABEL,
        }
        teacher = TeacherRecord(
            teacher_id=plan.teacher_id,
            camp_enrollment_id=f"CAMP-{plan.teacher_id}",
            name=plan.name,
            country=plan.country,
            timezone=plan.teacher_timezone,
            camp_day=plan.camp_day,
            graduation_state="IN_PROGRESS",
            gold_qualified=False,
            total_score=0.0,
            graduation_threshold=100.0,
            data_mode="MOCK",
            source_batch_id=BATCH_ID,
            source_snapshot_label=SNAPSHOT_LABEL,
            payload=teacher_payload,
            created_at=as_of,
            updated_at=as_of,
        )
        session.add(teacher)
        snapshot_values = {
            "snapshot_id": f"{BATCH_ID}:{plan.teacher_id}",
            "batch_id": BATCH_ID,
            "teacher_id": plan.teacher_id,
            "snapshot_label": SNAPSHOT_LABEL,
            "source_row_number": index + 1,
            "data_mode": "MIXED",
            "score_rule_version": str(policy_payload["policy_version"]),
            "score_policy_snapshot": policy_payload,
            "score_policy_sha256": policy_sha256,
            "real_name": plan.name,
            "employment_status": plan.employment_status,
            "bu": "TIT_SIMULATION",
            "based_type": "HOME_BASED",
            "teach_area_type": "OVERSEAS",
            "onboard_date": onboard_date,
            "onboard_30d_end_date": onboard_date + timedelta(days=30),
            "first_booked_date": first_booked_date,
            "is_cpl_tesol": plan.camp_day >= 7,
            "is_self_introduce": plan.camp_day >= 3,
            "lessons_completed": int(metrics["total_completed_cnt"]),
            "total_completed_cnt": int(metrics["total_completed_cnt"]),
            "peak_completed_cnt": int(metrics["peak_completed_cnt"]),
            "peak_slot_cnt": int(metrics["peak_slot_cnt"]),
            "perfect_cnt": int(metrics["perfect_cnt"]),
            "on_time_completed_cnt": int(metrics["on_time_completed_cnt"]),
            "feedback_praise_cnt": int(metrics["feedback_praise_cnt"]),
            "feedback_favorite_cnt": int(metrics["feedback_favorite_cnt"]),
            "completed_again_student_15d_cnt": int(
                metrics["completed_again_student_15d_cnt"]
            ),
            "late_cnt": int(metrics["late_cnt"]),
            "early_cnt": int(metrics["early_cnt"]),
            "real_absent_cnt": int(metrics["real_absent_cnt"]),
            "severe_redline_event": False,
            "capacity_score": float(metrics["capacity_score"]),
            "new_teacher_task_score": 0.0,
            "class_quality_no_issue_rate": 1.0,
            "reliability_score": 0.0,
            "user_feedback_score": 0.0,
            "class_quality_score": 0.0,
            "raw_total_score": 0.0,
            "public_total_score": 0.0,
            "metric_inputs": metrics,
            "metric_provenance": provenance,
            "raw_payload": {
                **teacher_raw,
                "score_config_version_id": policy_version_id,
            },
            "created_at": as_of,
            "updated_at": as_of,
        }
        if "absent_cnt" in snapshot_table.c:
            snapshot_values["absent_cnt"] = int(metrics["absent_cnt"])
        session.execute(snapshot_table.insert().values(**snapshot_values))
        session.flush()

        for lesson_index, lesson in enumerate(lessons):
            raw_lesson = {
                "scenario": SCENARIO,
                "mock_only": True,
                "lesson_id": lesson.lesson_id,
                "teacher_id": lesson.teacher_id,
                "status": lesson.lesson_lifecycle_status,
                "scheduled_start_at": lesson.scheduled_start_at.isoformat(),
                "is_late": lesson.is_late,
                "is_early": lesson.is_early,
                "is_peak": lesson.is_peak,
                "negative_tags": lesson.negative_tag_values,
            }
            source_record_id = (
                f"SRC-{BATCH_ID}-L{index + 1:02d}-{lesson_index + 1:03d}"
            )
            session.add(
                _source_record(
                    source_record_id=source_record_id,
                    row_number=source_row_number,
                    business_key=f"lesson:{lesson.lesson_id}",
                    teacher_id=lesson.teacher_id,
                    lesson_id=lesson.lesson_id,
                    occurred_at=lesson.scheduled_start_at,
                    raw_payload=raw_lesson,
                )
            )
            session.flush()
            source_row_number += 1
            session.add(
                LessonFactRecord(
                    lesson_id=lesson.lesson_id,
                    source_appoint_id=lesson.source_appoint_id,
                    camp_enrollment_id=lesson.camp_enrollment_id,
                    teacher_id=lesson.teacher_id,
                    scheduled_start_at=lesson.scheduled_start_at,
                    scheduled_end_at=lesson.scheduled_end_at,
                    lesson_lifecycle_status=lesson.lesson_lifecycle_status,
                    lesson_local_date=lesson.lesson_local_date,
                    lesson_local_time=lesson.lesson_local_time,
                    student_id_hash=lesson.student_id_hash,
                    is_late=lesson.is_late,
                    is_early=lesson.is_early,
                    is_false_early_leave=lesson.is_false_early_leave,
                    is_peak=lesson.is_peak,
                    negative_score=-1.0
                    if lesson.has_negative_feedback_tag
                    else 0.0,
                    has_negative_feedback_tag=lesson.has_negative_feedback_tag,
                    feedback_detail=lesson.feedback_detail,
                    negative_tag_values=lesson.negative_tag_values,
                    absence_reason_detail=lesson.absence_reason_detail,
                    complaint_category_l1=None,
                    complaint_category_l2=None,
                    complaint_category_l3=None,
                    complaint_source_level=None,
                    complaint_level_rank=None,
                    complaint_route=None,
                    complaint_rule_id=None,
                    is_blocked=lesson.is_blocked,
                    is_favorited=lesson.is_favorited,
                    has_positive_feedback_tag=(
                        lesson.has_positive_feedback_tag
                    ),
                    positive_tag_value=lesson.positive_tag_value,
                    is_rebooked=lesson.is_rebooked,
                    is_camera_off=lesson.is_camera_off,
                    is_cpu_usage_high=lesson.is_cpu_usage_high,
                    is_network_delay_high=lesson.is_network_delay_high,
                    source_batch_id=BATCH_ID,
                    source_record_id=source_record_id,
                    valid_for_scoring=(
                        lesson.lesson_lifecycle_status == "end"
                    ),
                    evidence_status="MOCK_SIMULATION",
                    data_mode="MOCK_SIMULATION",
                    payload={
                        "scenario": SCENARIO,
                        "mock_only": True,
                        "source_batch_id": BATCH_ID,
                        "source_record_id": source_record_id,
                    },
                    created_at=as_of,
                    updated_at=as_of,
                )
            )
    session.flush()
    return teacher_ids, lesson_count


def _advance_task_statuses(
    bind: Engine,
    plans: list[SimulatedTeacherPlan],
) -> int:
    transition_count = 0
    with bind.begin() as connection:
        if bind.dialect.name != "postgresql":
            raise RuntimeError("The shared task state simulation requires PostgreSQL")
        can_set_teacher_role = bool(
            connection.scalar(
                text(
                    "SELECT pg_has_role("
                    "current_user, 'tit_teacher_crud', 'member')"
                )
            )
        )
        if can_set_teacher_role:
            connection.execute(text("SET LOCAL ROLE tit_teacher_crud"))
        for plan in plans:
            for code, target_status in plan.task_statuses.items():
                row = connection.execute(
                    text(
                        "SELECT assignment_id, status, row_version, "
                        "       status_changed_at "
                        "FROM task_assignments "
                        "WHERE teacher_id = :teacher_id "
                        "  AND task_code = :task_code "
                        "  AND task_kind = 'FIXED_GROWTH'"
                    ),
                    {
                        "teacher_id": plan.teacher_id,
                        "task_code": code,
                    },
                ).mappings().one()
                assignment_id = str(row["assignment_id"])
                current_status = str(row["status"])
                if current_status == target_status:
                    continue
                path = list(TASK_STATUS_PATHS[target_status])
                if current_status not in ("ASSIGNED", *path):
                    raise RuntimeError(
                        f"Cannot resume {assignment_id}: "
                        f"{current_status} -> {target_status}"
                    )
                if current_status in path:
                    path = path[path.index(current_status) + 1 :]
                previous_changed_at = row["status_changed_at"]
                for step, next_status in enumerate(path, start=1):
                    changed_at = previous_changed_at + timedelta(
                        microseconds=step
                    )
                    reason_code = (
                        "SIMULATED_RETRY_REQUIRED"
                        if next_status == "FAILED"
                        else None
                    )
                    result = connection.execute(
                        text(
                            "UPDATE task_assignments "
                            "SET status = :status, "
                            "    status_reason_code = :reason_code, "
                            "    status_changed_at = :changed_at, "
                            "    completed_at = CASE "
                            "        WHEN :is_completed "
                            "        THEN :changed_at ELSE NULL END, "
                            "    updated_by = :updated_by "
                            "WHERE assignment_id = :assignment_id "
                            "  AND row_version = :row_version"
                        ),
                        {
                            "status": next_status,
                            "is_completed": next_status == "COMPLETED",
                            "reason_code": reason_code,
                            "changed_at": changed_at,
                            "updated_by": TEACHER_ACTOR,
                            "assignment_id": assignment_id,
                            "row_version": int(row["row_version"]),
                        },
                    )
                    if result.rowcount != 1:
                        raise RuntimeError(
                            f"Optimistic update failed for {assignment_id}"
                        )
                    row = connection.execute(
                        text(
                            "SELECT status, row_version, status_changed_at "
                            "FROM task_assignments "
                            "WHERE assignment_id = :assignment_id"
                        ),
                        {"assignment_id": assignment_id},
                    ).mappings().one()
                    previous_changed_at = row["status_changed_at"]
                    transition_count += 1
    return transition_count


def _simulated_lesson_rows(
    session: Session,
    teacher_ids: list[str],
) -> tuple[list[_LessonRow], dict[str, str]]:
    facts = session.scalars(
        select(LessonFactRecord)
        .where(
            LessonFactRecord.teacher_id.in_(teacher_ids),
            LessonFactRecord.source_batch_id == BATCH_ID,
        )
        .order_by(
            LessonFactRecord.teacher_id,
            LessonFactRecord.lesson_local_date,
            LessonFactRecord.lesson_local_time,
            LessonFactRecord.lesson_id,
        )
    ).all()
    sources = {
        item.lesson_id: item
        for item in session.scalars(
            select(SourceRecord).where(
                SourceRecord.batch_id == BATCH_ID,
                SourceRecord.lesson_id.is_not(None),
            )
        ).all()
        if item.lesson_id
    }
    rows: list[_LessonRow] = []
    source_ids: dict[str, str] = {}
    for index, fact in enumerate(facts, start=1):
        source = sources.get(fact.lesson_id)
        if source is None:
            raise RuntimeError(
                f"Simulation lesson {fact.lesson_id} has no source record"
            )
        local_date = fact.lesson_local_date
        local_time = fact.lesson_local_time
        if local_date is None or local_time is None:
            if fact.scheduled_start_at is None:
                raise RuntimeError(
                    f"Simulation lesson {fact.lesson_id} has no lesson time"
                )
            local_date = fact.scheduled_start_at.date()
            local_time = fact.scheduled_start_at.time().replace(tzinfo=None)
        local_start_at = datetime.combine(local_date, local_time)
        negative_tags = tuple(
            str(item).strip()
            for item in (fact.negative_tag_values or [])
            if str(item).strip()
        )
        raw_payload = {
            "课程id": fact.lesson_id,
            "上课日期": local_date.isoformat(),
            "上课时间": local_time.isoformat(),
            "是否高峰": fact.is_peak,
            "老师id": fact.teacher_id,
            "学员id": fact.student_id_hash,
            "课程状态": fact.lesson_lifecycle_status,
            "缺席原因明细": fact.absence_reason_detail,
            "迟到": fact.is_late,
            "早退": fact.is_early,
            "差评分": fact.negative_score,
            "差评标签": fact.has_negative_feedback_tag,
            "投诉一级分类": fact.complaint_category_l1,
            "投诉二级分类": fact.complaint_category_l2,
            "投诉三级分类": fact.complaint_category_l3,
            "是否拉黑": fact.is_blocked,
            "收藏": fact.is_favorited,
            "好评标签": fact.has_positive_feedback_tag,
            "评价详情": fact.feedback_detail,
            "是否复约": fact.is_rebooked,
            "未开摄像头": fact.is_camera_off,
            "cpu占用过高": fact.is_cpu_usage_high,
            "网络延迟过高": fact.is_network_delay_high,
            "假早退": fact.is_false_early_leave,
        }
        rows.append(
            _LessonRow(
                row_number=source.source_row_number,
                raw_payload=raw_payload,
                lesson_id=fact.lesson_id,
                teacher_id=fact.teacher_id,
                student_id=fact.student_id_hash or f"UNKNOWN-{index}",
                local_date=local_date,
                local_time=local_time,
                local_start_at=local_start_at,
                lifecycle_status=fact.lesson_lifecycle_status,
                is_peak=fact.is_peak,
                is_late=fact.is_late,
                is_early=fact.is_early,
                is_false_early_leave=fact.is_false_early_leave,
                negative_score=fact.negative_score,
                has_negative_tag=fact.has_negative_feedback_tag,
                feedback_detail=fact.feedback_detail,
                negative_tags=negative_tags,
                absence_reason_detail=fact.absence_reason_detail,
                complaint_l1=fact.complaint_category_l1,
                complaint_l2=fact.complaint_category_l2,
                complaint_l3=fact.complaint_category_l3,
                is_blocked=fact.is_blocked,
                is_favorited=fact.is_favorited,
                has_positive_tag=fact.has_positive_feedback_tag,
                is_rebooked=fact.is_rebooked,
                is_camera_off=fact.is_camera_off,
                is_cpu_usage_high=fact.is_cpu_usage_high,
                is_network_delay_high=fact.is_network_delay_high,
            )
        )
        source_ids[fact.lesson_id] = source.source_record_id
    return rows, source_ids


def _materialize_simulated_personalized_outputs(
    session: Session,
    teacher_ids: list[str],
    *,
    materialized_at: datetime,
) -> dict[str, Any]:
    lessons, source_ids = _simulated_lesson_rows(session, teacher_ids)
    specs, blacklist_count, negative_pending_count, unmatched_complaints = (
        _build_output_specs(
            lessons,
            lesson_batch_id=BATCH_ID,
            complaint_rules={},
            complaint_rule_ids={},
        )
    )
    specs = [
        replace(
            item,
            source_record_id=(
                source_ids.get(item.lesson_id)
                if item.lesson_id is not None
                else None
            ),
        )
        for item in specs
    ]
    teachers = {
        item.teacher_id: item
        for item in session.scalars(
            select(TeacherRecord).where(
                TeacherRecord.teacher_id.in_(teacher_ids)
            )
        ).all()
    }
    counts = _materialize_outputs(
        session,
        specs=specs,
        templates=_template_map(session),
        teachers=teachers,
        materialized_at=materialized_at,
        source_mode="MOCK_SIMULATION",
        evidence_context={
            "scenario": SCENARIO,
            "source_batch_id": BATCH_ID,
        },
    )
    session.flush()

    assignments = session.scalars(
        select(TaskAssignmentRecord).where(
            TaskAssignmentRecord.teacher_id.in_(teacher_ids),
            TaskAssignmentRecord.task_kind == "PERSONALIZED_IMPROVEMENT",
        )
    ).all()

    return {
        **counts,
        "spec_count": len(specs),
        "personalized_task_count": len(assignments),
        "blacklist_task_count": blacklist_count,
        "negative_pending_teacher_count": negative_pending_count,
        "unmatched_complaint_count": unmatched_complaints,
        "trigger_counts": dict(Counter(item.rule_code for item in specs)),
    }


def _advance_personalized_task_statuses(
    bind: Engine,
) -> dict[str, Any]:
    transition_count = 0
    target_counts: Counter[str] = Counter()
    with bind.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT assignment_id, status, row_version, status_changed_at "
                "FROM task_assignments "
                "WHERE teacher_id LIKE :prefix "
                "  AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
                "ORDER BY teacher_id, task_code, dedupe_key"
            ),
            {"prefix": f"{TEACHER_PREFIX}%"},
        ).mappings().all()
        for index, initial_row in enumerate(rows):
            target_status = PERSONALIZED_STATUS_SEQUENCE[
                index % len(PERSONALIZED_STATUS_SEQUENCE)
            ]
            target_counts[target_status] += 1
            current_status = str(initial_row["status"])
            if current_status == target_status:
                continue
            if current_status in {
                "COMPLETED",
                "FAILED",
                "EXPIRED",
                "WAIVED",
                "CANCELLED",
            }:
                continue
            path = list(TASK_STATUS_PATHS[target_status])
            if current_status in path:
                path = path[path.index(current_status) + 1 :]
            elif current_status != "ASSIGNED":
                continue
            row = initial_row
            previous_changed_at = row["status_changed_at"]
            for step, next_status in enumerate(path, start=1):
                changed_at = previous_changed_at + timedelta(microseconds=step)
                reason_code = (
                    "SIMULATED_RETRY_REQUIRED"
                    if next_status == "FAILED"
                    else None
                )
                result = connection.execute(
                    text(
                        "UPDATE task_assignments "
                        "SET status = :status, "
                        "    status_reason_code = :reason_code, "
                        "    status_changed_at = :changed_at, "
                        "    completed_at = CASE "
                        "        WHEN :is_completed "
                        "        THEN :changed_at ELSE NULL END, "
                        "    updated_by = :updated_by "
                        "WHERE assignment_id = :assignment_id "
                        "  AND row_version = :row_version"
                    ),
                    {
                        "status": next_status,
                        "is_completed": next_status == "COMPLETED",
                        "reason_code": reason_code,
                        "changed_at": changed_at,
                        "updated_by": TEACHER_ACTOR,
                        "assignment_id": row["assignment_id"],
                        "row_version": int(row["row_version"]),
                    },
                )
                if result.rowcount != 1:
                    raise RuntimeError(
                        "Optimistic update failed for "
                        f"{row['assignment_id']}"
                    )
                row = connection.execute(
                    text(
                        "SELECT assignment_id, status, row_version, "
                        "       status_changed_at "
                        "FROM task_assignments "
                        "WHERE assignment_id = :assignment_id"
                    ),
                    {"assignment_id": row["assignment_id"]},
                ).mappings().one()
                previous_changed_at = row["status_changed_at"]
                transition_count += 1
    return {
        "task_count": sum(target_counts.values()),
        "transition_count": transition_count,
        "target_status_counts": dict(target_counts),
    }


def _score_component(
    *,
    code: str,
    metric: str,
    value: float,
    score: float,
    points_per_unit: float | None,
    source_mode: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "metric": metric,
        "value": value,
        "points_per_unit": points_per_unit,
        "score": round(score, 2),
        "source_mode": source_mode,
        **extra,
    }


def _reconciliation_status(
    *,
    source_scope: str,
    component_code: str,
    current_score: float,
    lesson_attributed_score: float,
    has_lessons: bool,
) -> str:
    if source_scope != "LESSON":
        return "NOT_APPLICABLE"
    if component_code == "CLASS_QUALITY_PERFECT_COUNT":
        return "SOURCE_MISSING"
    if math.isclose(current_score, lesson_attributed_score, abs_tol=1e-9):
        return "MATCHED" if current_score else "MATCHED_ZERO"
    if lesson_attributed_score < current_score:
        return "PARTIAL" if has_lessons else "SOURCE_MISSING"
    return "MISMATCH"


def _lesson_score_rows(
    lessons: list[LessonFactRecord],
    *,
    policy_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    scoring = policy_payload["scoring_items"]
    reliability_uses_perfect = "reliability_perfect" in scoring
    primary_points = float(
        scoring[
            "reliability_perfect"
            if reliability_uses_perfect
            else "reliability_on_time"
        ]["points_per_unit"]
    )
    peak_points = float(scoring["reliability_peak"]["points_per_unit"])
    praise_points = float(scoring["feedback_praise"]["points_per_unit"])
    favorite_points = float(scoring["feedback_favorite"]["points_per_unit"])
    quality_rule = scoring.get("classroom_quality")
    quality_points = (
        float(quality_rule["points_per_unit"])
        if quality_rule is not None
        else 0.0
    )
    favorite_credit: set[tuple[str, str]] = set()
    unresolved_favorite_pairs = {
        (lesson.teacher_id, lesson.student_id_hash)
        for lesson in lessons
        if (
            lesson.valid_for_scoring
            and lesson.lesson_lifecycle_status == "end"
            and lesson.is_favorited is True
            and lesson.student_id_hash
            and (
                lesson.lesson_local_date is None
                or lesson.lesson_local_time is None
            )
        )
    }
    rows: list[dict[str, Any]] = []
    attributed: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0.0, "score": 0.0}
    )
    ordered = sorted(
        lessons,
        key=lambda item: (
            item.lesson_local_date is None,
            item.lesson_local_date or date.max,
            item.lesson_local_time is None,
            item.lesson_local_time or time.max,
            item.lesson_id,
        ),
    )
    for lesson in ordered:
        completed = lesson.lesson_lifecycle_status == "end"
        praise = completed and lesson.has_positive_feedback_tag is True
        favorite_source_hit = (
            lesson.valid_for_scoring
            and completed
            and lesson.is_favorited is True
        )
        favorite_key = (
            (lesson.teacher_id, lesson.student_id_hash)
            if lesson.student_id_hash
            else None
        )
        favorite_order_known = (
            lesson.lesson_local_date is not None
            and lesson.lesson_local_time is not None
        )
        favorite_attribution_known = (
            favorite_key is not None
            and favorite_order_known
            and favorite_key not in unresolved_favorite_pairs
        )
        favorite = (
            favorite_source_hit
            and favorite_attribution_known
            and favorite_key not in favorite_credit
        )
        if favorite:
            assert favorite_key is not None
            favorite_credit.add(favorite_key)
        favorite_evidence_status = (
            "SOURCE_MISSING"
            if (
                lesson.is_favorited is None
                or (
                    favorite_source_hit
                    and not favorite_attribution_known
                )
            )
            else "CONFIRMED"
        )
        on_time = (
            completed
            and lesson.is_late is False
            and lesson.is_early is False
        )
        peak = completed and lesson.is_peak is True
        is_perfect = is_perfect_lesson(
            lesson_lifecycle_status=lesson.lesson_lifecycle_status,
            is_late=lesson.is_late,
            is_early=lesson.is_early,
        )
        hardware_quality = hardware_quality_passed(
            is_camera_off=lesson.is_camera_off,
            is_cpu_usage_high=lesson.is_cpu_usage_high,
            is_network_delay_high=lesson.is_network_delay_high,
        )
        reliability_primary = (
            {
                "code": "PERFECT_COMPLETED",
                "business_fact": "is_perfect",
                "fact_value": is_perfect,
                "awarded": is_perfect,
                "points_per_unit": primary_points,
                "score": primary_points if is_perfect else 0.0,
                "evidence_status": "CONFIRMED",
                "inputs": {
                    "lesson_lifecycle_status": (
                        lesson.lesson_lifecycle_status
                    ),
                    "is_late": lesson.is_late,
                    "is_early": lesson.is_early,
                },
            }
            if reliability_uses_perfect
            else {
                "code": "ON_TIME_COMPLETED",
                "business_fact": "completed_without_late_or_early",
                "fact_value": on_time if completed else False,
                "awarded": on_time,
                "points_per_unit": primary_points,
                "score": primary_points if on_time else 0.0,
                "evidence_status": "CONFIRMED",
                "inputs": {
                    "lesson_lifecycle_status": lesson.lesson_lifecycle_status,
                    "is_late": lesson.is_late,
                    "is_early": lesson.is_early,
                },
            }
        )
        component_sets = {
            "USER_FEEDBACK": [
                {
                    "code": "FEEDBACK_PRAISE",
                    "business_fact": "has_positive_feedback_tag",
                    "fact_value": lesson.has_positive_feedback_tag,
                    "awarded": praise,
                    "points_per_unit": praise_points,
                    "score": praise_points if praise else 0.0,
                    "evidence_status": (
                        "CONFIRMED"
                        if lesson.has_positive_feedback_tag is not None
                        else "SOURCE_MISSING"
                    ),
                },
                {
                    "code": "FEEDBACK_FAVORITE",
                    "business_fact": "is_favorited",
                    "fact_value": lesson.is_favorited,
                    "awarded": favorite,
                    "points_per_unit": favorite_points,
                    "score": favorite_points if favorite else 0.0,
                    "evidence_status": favorite_evidence_status,
                    "dedupe": {
                        "key": "teacher_id+student_id_hash",
                        "credited_on_this_lesson": favorite,
                        "source_hit": favorite_source_hit,
                        "pair_available": favorite_key is not None,
                        "lesson_time_available": favorite_order_known,
                    },
                },
            ],
            "RELIABILITY": [
                reliability_primary,
                {
                    "code": "PEAK_COMPLETED",
                    "business_fact": "completed_in_peak_period",
                    "fact_value": peak if lesson.is_peak is not None else None,
                    "awarded": peak,
                    "points_per_unit": peak_points,
                    "score": peak_points if peak else 0.0,
                    "evidence_status": (
                        "CONFIRMED"
                        if lesson.is_peak is not None or not completed
                        else "SOURCE_MISSING"
                    ),
                    "inputs": {
                        "lesson_lifecycle_status": lesson.lesson_lifecycle_status,
                        "is_peak": lesson.is_peak,
                    },
                },
            ],
            "CLASS_QUALITY": (
                [
                    (
                        {
                            "code": "CLASS_QUALITY_HARDWARE",
                            "business_fact": "hardware_quality_passed",
                            "fact_value": hardware_quality,
                            "awarded": hardware_quality is True,
                            "points_per_unit": quality_points,
                            "score": (
                                quality_points
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
                        if quality_rule.get("metric")
                        == "lesson_hardware_quality_passed"
                        else {
                            "code": "CLASS_QUALITY_PERFECT_COUNT",
                            "business_fact": "is_perfect",
                            "fact_value": is_perfect,
                            "awarded": False,
                            "points_per_unit": quality_points,
                            "score": 0.0,
                            "evidence_status": "SOURCE_MISSING",
                            "note": (
                                "The current contract does not attribute "
                                "teacher-wide perfect-count points to a lesson."
                            ),
                        }
                    )
                ]
                if quality_rule is not None
                else []
            ),
        }
        for dimension, components in component_sets.items():
            for component in components:
                if component["awarded"]:
                    account = attributed[component["code"]]
                    account["count"] += 1
                    account["score"] += float(component["score"])
            statuses = {
                str(component["evidence_status"]) for component in components
            }
            if not components:
                evidence_status = "NOT_APPLICABLE"
                evidence_coverage = "NONE"
            elif statuses == {"CONFIRMED"}:
                evidence_status = "CONFIRMED"
                evidence_coverage = "FULL"
            elif "CONFIRMED" in statuses:
                evidence_status = "PARTIAL"
                evidence_coverage = "PARTIAL"
            else:
                evidence_status = "SOURCE_MISSING"
                evidence_coverage = "NONE"
            rows.append(
                {
                    "lesson": lesson,
                    "dimension": dimension,
                    "current_score": round(
                        sum(float(item["score"]) for item in components),
                        2,
                    ),
                    "evidence_status": evidence_status,
                    "evidence_coverage": evidence_coverage,
                    "components": components,
                }
            )
    return rows, attributed


def _refresh_simulated_score_projections(
    session: Session,
    teacher_ids: list[str],
    *,
    calculated_at: datetime,
) -> dict[str, Any]:
    policy_payload, config_version_id = _score_config(session)
    policy_sha256 = _sha256_json(policy_payload)
    projection_id = f"SPR-SIM-{uuid4().hex}"
    snapshot_table = _snapshot_table(session)
    snapshot_rows = session.execute(
        select(snapshot_table).where(
            snapshot_table.c.teacher_id.in_(teacher_ids),
            snapshot_table.c.batch_id == BATCH_ID,
        )
    ).mappings().all()
    snapshots = {str(item["teacher_id"]): dict(item) for item in snapshot_rows}
    teachers = list(
        session.scalars(
            select(TeacherRecord)
            .where(TeacherRecord.teacher_id.in_(teacher_ids))
            .order_by(TeacherRecord.teacher_id)
        ).all()
    )
    assignments = list(
        session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.teacher_id.in_(teacher_ids),
                TaskAssignmentRecord.task_code.in_(FIXED_TASK_CODES),
            )
        ).all()
    )
    assignments_by_teacher: dict[str, list[TaskAssignmentRecord]] = defaultdict(
        list
    )
    for assignment in assignments:
        assignments_by_teacher[assignment.teacher_id].append(assignment)
    template_ids = {
        item.template_version_id for item in assignments if item.template_version_id
    }
    templates = {
        item.row_id: item
        for item in session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.row_id.in_(template_ids)
            )
        ).all()
    }
    lessons = list(
        session.scalars(
            select(LessonFactRecord)
            .where(LessonFactRecord.teacher_id.in_(teacher_ids))
            .order_by(
                LessonFactRecord.teacher_id,
                LessonFactRecord.lesson_local_date,
                LessonFactRecord.lesson_local_time,
                LessonFactRecord.lesson_id,
            )
        ).all()
    )
    lessons_by_teacher: dict[str, list[LessonFactRecord]] = defaultdict(list)
    for lesson in lessons:
        lessons_by_teacher[lesson.teacher_id].append(lesson)

    session.execute(
        delete(LessonDimensionScoreRecord).where(
            LessonDimensionScoreRecord.teacher_id.in_(teacher_ids)
        )
    )
    session.execute(
        delete(ScoreComponentAccountRecord).where(
            ScoreComponentAccountRecord.teacher_id.in_(teacher_ids)
        )
    )
    session.execute(
        delete(ScoreAccountRecord).where(
            ScoreAccountRecord.teacher_id.in_(teacher_ids)
        )
    )
    session.flush()

    scoring = policy_payload["scoring_items"]
    thresholds = policy_payload["thresholds"]
    lesson_state_count = 0
    component_account_count = 0
    graduated_count = 0
    gold_count = 0
    for teacher in teachers:
        snapshot = snapshots.get(teacher.teacher_id)
        if snapshot is None:
            raise RuntimeError(
                f"Missing simulation teacher snapshot: {teacher.teacher_id}"
            )
        metrics = deepcopy(snapshot["metric_inputs"] or {})
        provenance = deepcopy(snapshot["metric_provenance"] or {})
        teacher_lessons = lessons_by_teacher.get(teacher.teacher_id, [])
        teacher_assignments = assignments_by_teacher[teacher.teacher_id]
        assignment_by_code = {
            item.task_code: item for item in teacher_assignments
        }
        completed_task_count = sum(
            item.status == "COMPLETED" for item in teacher_assignments
        )
        task_components: list[dict[str, Any]] = []
        for code in FIXED_TASK_CODES:
            assignment = assignment_by_code.get(code)
            template = (
                templates.get(assignment.template_version_id)
                if assignment is not None
                else None
            )
            template_payload = (
                template.payload
                if template is not None and isinstance(template.payload, dict)
                else {}
            )
            points = float(template_payload.get("score_value") or 0)
            completed = bool(
                assignment is not None and assignment.status == "COMPLETED"
            )
            task_components.append(
                _score_component(
                    code=code,
                    metric="task_assignments.status",
                    value=1.0 if completed else 0.0,
                    points_per_unit=points,
                    score=points if completed else 0.0,
                    source_mode="SYSTEM_TASK_STATUS",
                    assignment_id=(
                        assignment.assignment_id if assignment is not None else None
                    ),
                    status=assignment.status if assignment is not None else None,
                    template_version_id=(
                        assignment.template_version_id
                        if assignment is not None
                        else None
                    ),
                    title=template_payload.get("title") or code,
                )
            )
        new_teacher_task_score = round(
            sum(float(item["score"]) for item in task_components),
            2,
        )
        reliability_primary_key = (
            "reliability_perfect"
            if "reliability_perfect" in scoring
            else "reliability_on_time"
        )
        reliability_primary_code = (
            "PERFECT_COMPLETED"
            if reliability_primary_key == "reliability_perfect"
            else "ON_TIME_COMPLETED"
        )
        reliability_primary_metric = (
            "perfect_cnt"
            if reliability_primary_key == "reliability_perfect"
            else "on_time_completed_cnt"
        )
        reliability_components = [
            _score_component(
                code=reliability_primary_code,
                metric=reliability_primary_metric,
                value=float(metrics[reliability_primary_metric]),
                points_per_unit=float(
                    scoring[reliability_primary_key]["points_per_unit"]
                ),
                score=(
                    float(metrics[reliability_primary_metric])
                    * float(scoring[reliability_primary_key]["points_per_unit"])
                ),
                source_mode="MOCK_SIMULATION",
            ),
            _score_component(
                code="PEAK_COMPLETED",
                metric="peak_completed_cnt",
                value=float(metrics["peak_completed_cnt"]),
                points_per_unit=float(
                    scoring["reliability_peak"]["points_per_unit"]
                ),
                score=(
                    float(metrics["peak_completed_cnt"])
                    * float(scoring["reliability_peak"]["points_per_unit"])
                ),
                source_mode="MOCK_SIMULATION",
            ),
        ]
        feedback_components = [
            _score_component(
                code="FEEDBACK_PRAISE",
                metric="feedback_praise_cnt",
                value=float(metrics["feedback_praise_cnt"]),
                points_per_unit=float(
                    scoring["feedback_praise"]["points_per_unit"]
                ),
                score=(
                    float(metrics["feedback_praise_cnt"])
                    * float(scoring["feedback_praise"]["points_per_unit"])
                ),
                source_mode="MOCK_SIMULATION",
            ),
            _score_component(
                code="FEEDBACK_FAVORITE",
                metric="feedback_favorite_cnt",
                value=float(metrics["feedback_favorite_cnt"]),
                points_per_unit=float(
                    scoring["feedback_favorite"]["points_per_unit"]
                ),
                score=(
                    float(metrics["feedback_favorite_cnt"])
                    * float(scoring["feedback_favorite"]["points_per_unit"])
                ),
                source_mode="MOCK_SIMULATION",
                dedupe="DISTINCT student_id_hash",
            ),
        ]
        quality_rule = scoring.get("classroom_quality")
        hardware_quality_count = sum(
            hardware_quality_passed(
                is_camera_off=lesson.is_camera_off,
                is_cpu_usage_high=lesson.is_cpu_usage_high,
                is_network_delay_high=lesson.is_network_delay_high,
            )
            is True
            for lesson in teacher_lessons
        )
        quality_components = (
            [
                _score_component(
                    code=(
                        "CLASS_QUALITY_HARDWARE"
                        if quality_rule["metric"]
                        == "lesson_hardware_quality_passed"
                        else "CLASS_QUALITY_PERFECT_COUNT"
                    ),
                    metric=str(quality_rule["metric"]),
                    value=(
                        float(hardware_quality_count)
                        if quality_rule["metric"]
                        == "lesson_hardware_quality_passed"
                        else float(metrics["perfect_cnt"])
                    ),
                    points_per_unit=float(quality_rule["points_per_unit"]),
                    score=(
                        float(hardware_quality_count)
                        * float(quality_rule["points_per_unit"])
                        if quality_rule["metric"]
                        == "lesson_hardware_quality_passed"
                        else float(metrics["perfect_cnt"])
                        * float(quality_rule["points_per_unit"])
                    ),
                    source_mode="MOCK_SIMULATION",
                )
            ]
            if quality_rule is not None
            else []
        )
        capacity_achieved = bool(
            float(metrics["peak_slot_cnt"])
            >= float(scoring["capacity"]["threshold"])
        )
        capacity_components = [
            _score_component(
                code=str(scoring["capacity"]["milestone_id"]),
                metric=str(scoring["capacity"]["metric"]),
                value=float(metrics["peak_slot_cnt"]),
                points_per_unit=None,
                score=(
                    float(scoring["capacity"]["score_value"])
                    if capacity_achieved
                    else 0.0
                ),
                source_mode="MOCK_SIMULATION",
                maximum_points=float(scoring["capacity"]["maximum_points"]),
                operator=str(scoring["capacity"]["operator"]),
                threshold=float(scoring["capacity"]["threshold"]),
                milestone_achieved=capacity_achieved,
                settlement_mode=str(scoring["capacity"]["settlement_mode"]),
            )
        ]
        component_sets = {
            "USER_FEEDBACK": feedback_components,
            "RELIABILITY": reliability_components,
            "CLASS_QUALITY": quality_components,
            "CAPACITY": capacity_components,
            "NEW_TEACHER_TASK": task_components,
        }
        labels = {
            "USER_FEEDBACK": "用户反馈",
            "RELIABILITY": "可靠性",
            "CLASS_QUALITY": "课堂质量",
            "CAPACITY": "供给达标（Peak slots）",
            "NEW_TEACHER_TASK": "成长任务（必修）",
        }
        dimensions = [
            {
                "code": code,
                "label": labels[code],
                "score": round(
                    sum(float(item["score"]) for item in component_sets[code]),
                    2,
                ),
                "source_mode": (
                    "SYSTEM_TASK_STATUS"
                    if code == "NEW_TEACHER_TASK"
                    else "NOT_APPLICABLE"
                    if not component_sets[code]
                    else "MOCK_SIMULATION"
                ),
                "components": deepcopy(component_sets[code]),
            }
            for code in DIMENSION_ORDER
        ]
        dimension_scores = {
            str(item["code"]): float(item["score"]) for item in dimensions
        }
        raw_total = round(sum(dimension_scores.values()), 2)
        public_total = round(
            min(raw_total, float(thresholds["gold_external_score"])),
            2,
        )
        l0_count = int(metrics.get("l0_complaint_cnt") or 0)
        graduation_criteria_met = bool(
            len(teacher_assignments) == len(FIXED_TASK_CODES)
            and completed_task_count == len(FIXED_TASK_CODES)
            and l0_count == 0
            and raw_total >= float(thresholds["graduation_raw_score"])
        )
        gold_config = policy_payload["hard_gates"]["gold"]
        gold_attendance_met = bool(
            int(metrics.get("late_cnt") or 0)
            <= int(gold_config.get("maximum_late_count", 10**9))
            and int(metrics.get("early_cnt") or 0)
            <= int(gold_config.get("maximum_early_count", 10**9))
            and int(metrics.get("absent_cnt") or 0)
            <= int(gold_config.get("maximum_absent_count", 10**9))
        )
        gold_criteria_met = bool(
            graduation_criteria_met
            and raw_total >= float(thresholds["gold_raw_score"])
            and gold_attendance_met
        )
        graduation_state = (
            "GRADUATED" if graduation_criteria_met else "IN_PROGRESS"
        )
        gold_items = [
            {
                "code": "REQUIRES_GRADUATION_CRITERIA",
                "metric": "graduation_criteria_met",
                "operator": "==",
                "threshold": True,
                "actual": graduation_criteria_met,
                "met": graduation_criteria_met,
                "source_mode": "MOCK_SIMULATION",
            },
            {
                "code": "MINIMUM_GOLD_TOTAL_SCORE",
                "metric": "raw_total_score",
                "operator": ">=",
                "threshold": float(thresholds["gold_raw_score"]),
                "actual": raw_total,
                "met": raw_total >= float(thresholds["gold_raw_score"]),
                "source_mode": "MOCK_SIMULATION",
            },
        ]
        if "maximum_late_count" in gold_config:
            gold_items.extend(
                [
                    {
                        "code": "MAXIMUM_LATE_COUNT",
                        "metric": "late_cnt",
                        "operator": "<=",
                        "threshold": int(gold_config["maximum_late_count"]),
                        "actual": int(metrics.get("late_cnt") or 0),
                        "met": int(metrics.get("late_cnt") or 0)
                        <= int(gold_config["maximum_late_count"]),
                        "source_mode": "MOCK_SIMULATION",
                    },
                    {
                        "code": "ZERO_EARLY_COUNT",
                        "metric": "early_cnt",
                        "operator": "==",
                        "threshold": int(gold_config["maximum_early_count"]),
                        "actual": int(metrics.get("early_cnt") or 0),
                        "met": int(metrics.get("early_cnt") or 0)
                        == int(gold_config["maximum_early_count"]),
                        "source_mode": "MOCK_SIMULATION",
                    },
                    {
                        "code": "ZERO_ABSENT_COUNT",
                        "metric": "absent_cnt",
                        "operator": "==",
                        "threshold": int(gold_config["maximum_absent_count"]),
                        "actual": int(metrics.get("absent_cnt") or 0),
                        "met": int(metrics.get("absent_cnt") or 0)
                        == int(gold_config["maximum_absent_count"]),
                        "source_mode": "MOCK_SIMULATION",
                    },
                ]
            )
        hard_gates = {
            "graduation": {
                "met": graduation_criteria_met,
                "items": [
                    {
                        "code": "ALL_MANDATORY_GROWTH_TASKS_COMPLETED",
                        "metric": "mandatory_task_completed_count",
                        "operator": "==",
                        "threshold": len(FIXED_TASK_CODES),
                        "actual": completed_task_count,
                        "met": (
                            len(teacher_assignments) == len(FIXED_TASK_CODES)
                            and completed_task_count == len(FIXED_TASK_CODES)
                        ),
                        "source_mode": "SYSTEM_TASK_STATUS",
                    },
                    {
                        "code": "NO_L0_COMPLAINT",
                        "metric": "l0_complaint_cnt",
                        "operator": "<=",
                        "threshold": 0,
                        "actual": l0_count,
                        "met": l0_count == 0,
                        "source_mode": "MOCK_SIMULATION",
                    },
                    {
                        "code": "MINIMUM_TOTAL_SCORE",
                        "metric": "raw_total_score",
                        "operator": ">=",
                        "threshold": float(
                            thresholds["graduation_raw_score"]
                        ),
                        "actual": raw_total,
                        "met": raw_total
                        >= float(thresholds["graduation_raw_score"]),
                        "source_mode": "MOCK_SIMULATION",
                    },
                ],
            },
            "gold": {
                "met": gold_criteria_met,
                "items": gold_items,
            },
        }

        lesson_rows, attributed = _lesson_score_rows(
            teacher_lessons,
            policy_payload=policy_payload,
        )
        for row in lesson_rows:
            lesson = row["lesson"]
            dimension = str(row["dimension"])
            session.add(
                LessonDimensionScoreRecord(
                    score_state_id=(
                        f"{teacher.camp_enrollment_id}:"
                        f"{lesson.lesson_id}:{dimension}"
                    ),
                    camp_enrollment_id=teacher.camp_enrollment_id,
                    lesson_id=lesson.lesson_id,
                    teacher_id=teacher.teacher_id,
                    dimension=dimension,
                    current_score=float(row["current_score"]),
                    evidence_status=str(row["evidence_status"]),
                    evidence_coverage=str(row["evidence_coverage"]),
                    score_rule_version=str(policy_payload["policy_version"]),
                    current_revision=1,
                    score_as_of=lesson.scheduled_start_at or lesson.updated_at,
                    last_score_entry_id=None,
                    payload={
                        "source_scope": "LESSON",
                        "source_mode": "MOCK_SIMULATION",
                        "source_batch_id": lesson.source_batch_id,
                        "source_record_id": lesson.source_record_id,
                        "business_facts": deepcopy(row["components"]),
                        "projection_id": projection_id,
                        "projection_trigger": {
                            "type": "SIMULATION_SEED",
                            "ref": BATCH_ID,
                        },
                    },
                    updated_at=calculated_at,
                )
            )
            lesson_state_count += 1

        source_lesson_batch_id = BATCH_ID if teacher_lessons else None
        for dimension in dimensions:
            dimension_code = str(dimension["code"])
            session.add(
                ScoreAccountRecord(
                    account_id=f"{teacher.teacher_id}:{dimension_code}",
                    teacher_id=teacher.teacher_id,
                    camp_enrollment_id=teacher.camp_enrollment_id,
                    dimension=dimension_code,
                    current_score=float(dimension["score"]),
                    minimum_score=0.0,
                    weight=0.0,
                    score_rule_version=str(policy_payload["policy_version"]),
                    version=1,
                    updated_at=calculated_at,
                    payload={
                        **deepcopy(dimension),
                        "projection_id": projection_id,
                        "projection_trigger": {
                            "type": "SIMULATION_SEED",
                            "ref": BATCH_ID,
                        },
                        "projection_scope": "PERSISTED_CURRENT",
                        "calculated_at": calculated_at.isoformat(),
                    },
                )
            )
            for component in component_sets[dimension_code]:
                code = str(component["code"])
                source_scope = (
                    "TASK"
                    if dimension_code == "NEW_TEACHER_TASK"
                    else "TEACHER"
                    if dimension_code == "CAPACITY"
                    else "LESSON"
                )
                attributed_values = attributed.get(
                    code,
                    {"count": 0.0, "score": 0.0},
                )
                attributed_score = round(
                    float(attributed_values["score"]),
                    2,
                )
                component_score = float(component["score"])
                session.add(
                    ScoreComponentAccountRecord(
                        component_account_id=f"{teacher.teacher_id}:{code}",
                        teacher_id=teacher.teacher_id,
                        camp_enrollment_id=teacher.camp_enrollment_id,
                        dimension=dimension_code,
                        component_code=code,
                        source_scope=source_scope,
                        source_metric=str(component.get("metric") or ""),
                        unit_count=float(component.get("value") or 0),
                        points_per_unit=(
                            float(component["points_per_unit"])
                            if component.get("points_per_unit") is not None
                            else None
                        ),
                        current_score=component_score,
                        lesson_attributed_count=int(
                            attributed_values["count"]
                        ),
                        lesson_attributed_score=attributed_score,
                        unattributed_score=round(
                            component_score - attributed_score,
                            2,
                        ),
                        reconciliation_status=_reconciliation_status(
                            source_scope=source_scope,
                            component_code=code,
                            current_score=component_score,
                            lesson_attributed_score=attributed_score,
                            has_lessons=bool(teacher_lessons),
                        ),
                        score_rule_version=str(
                            policy_payload["policy_version"]
                        ),
                        source_teacher_batch_id=BATCH_ID,
                        source_lesson_batch_id=source_lesson_batch_id,
                        projection_revision=1,
                        calculated_at=calculated_at,
                        payload={
                            **deepcopy(component),
                            "score_config_version_id": config_version_id,
                            "projection_id": projection_id,
                            "projection_trigger": {
                                "type": "SIMULATION_SEED",
                                "ref": BATCH_ID,
                            },
                            "lesson_source_batch_ids": (
                                [BATCH_ID] if teacher_lessons else []
                            ),
                            "attribution_contract": (
                                "teacher-total-is-authoritative; "
                                "lesson-attribution-must-reconcile"
                            ),
                            "source_mode": "MOCK_SIMULATION",
                        },
                    )
                )
                component_account_count += 1

        metrics.update(
            {
                "mandatory_task_assignment_count": len(teacher_assignments),
                "mandatory_task_completed_count": completed_task_count,
                "mandatory_task_expected_count": len(FIXED_TASK_CODES),
                "new_teacher_task_score": new_teacher_task_score,
            }
        )
        for metric in (
            "mandatory_task_assignment_count",
            "mandatory_task_completed_count",
            "mandatory_task_expected_count",
            "new_teacher_task_score",
        ):
            provenance[metric] = {
                "source_mode": "SYSTEM_TASK_STATUS",
                "source_field": "task_assignments",
                "batch_id": BATCH_ID,
                "note": "Calculated from the shared simulated task facts.",
            }
        snapshot_update = {
            "score_rule_version": str(policy_payload["policy_version"]),
            "score_policy_snapshot": policy_payload,
            "score_policy_sha256": policy_sha256,
            "capacity_score": dimension_scores["CAPACITY"],
            "new_teacher_task_score": new_teacher_task_score,
            "reliability_score": dimension_scores["RELIABILITY"],
            "user_feedback_score": dimension_scores["USER_FEEDBACK"],
            "class_quality_score": dimension_scores["CLASS_QUALITY"],
            "raw_total_score": raw_total,
            "public_total_score": public_total,
            "metric_inputs": metrics,
            "metric_provenance": provenance,
            "raw_payload": {
                **deepcopy(snapshot["raw_payload"] or {}),
                "score_config_version_id": config_version_id,
                "score_projection_id": projection_id,
            },
            "updated_at": calculated_at,
        }
        session.execute(
            snapshot_table.update()
            .where(
                snapshot_table.c.snapshot_id == snapshot["snapshot_id"]
            )
            .values(**snapshot_update)
        )
        teacher_payload = deepcopy(teacher.payload or {})
        teacher_payload.update(
            {
                "metric_inputs": metrics,
                "metric_provenance": provenance,
                "dimensions": deepcopy(dimensions),
                "raw_total_score": raw_total,
                "total_score": raw_total,
                "external_display_score": public_total,
                "graduation_state": graduation_state,
                "graduation_criteria_met": graduation_criteria_met,
                "graduation_qualified": graduation_criteria_met,
                "gold_criteria_met": gold_criteria_met,
                "gold_qualified": gold_criteria_met,
                "hard_gates": hard_gates,
                "score_policy_version": policy_payload["policy_version"],
                "score_rule_version": policy_payload["policy_version"],
                "score_policy_sha256": policy_sha256,
                "score_projection_scope": "PERSISTED_CURRENT",
                "score_projection_id": projection_id,
                "score_projection_trigger": {
                    "type": "SIMULATION_SEED",
                    "ref": BATCH_ID,
                },
                "updated_at": calculated_at.isoformat(),
            }
        )
        teacher.total_score = raw_total
        teacher.graduation_state = graduation_state
        teacher.gold_qualified = gold_criteria_met
        teacher.payload = teacher_payload
        teacher.updated_at = calculated_at
        graduated_count += int(graduation_criteria_met)
        gold_count += int(gold_criteria_met)

    session.flush()
    return {
        "projection_id": projection_id,
        "teacher_count": len(teachers),
        "lesson_score_state_count": lesson_state_count,
        "component_account_count": component_account_count,
        "graduated_count": graduated_count,
        "gold_count": gold_count,
        "score_rule_version": policy_payload["policy_version"],
    }


def seed_balanced_simulated_cohort(
    bind: Engine,
    *,
    seed: int = DEFAULT_SEED,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    plans = build_balanced_plan(seed=seed)
    generated_at = as_of or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    with session_scope(bind) as session:
        teacher_ids, lesson_count = _insert_base_facts(
            session,
            plans,
            seed=seed,
            as_of=generated_at,
        )
        personalized = _materialize_simulated_personalized_outputs(
            session,
            teacher_ids,
            materialized_at=generated_at,
        )
    transition_count = _advance_task_statuses(bind, plans)
    personalized_statuses = _advance_personalized_task_statuses(bind)
    with session_scope(bind) as session:
        session.execute(
            text(
                "UPDATE outbox_events "
                "SET status = 'CANCELLED', "
                "    last_error = 'MOCK_SIMULATION_NO_SETTLEMENT' "
                "WHERE aggregate_id IN ("
                "    SELECT assignment_id FROM task_assignments "
                "    WHERE teacher_id LIKE 'SIM260728%'"
                ") "
                "  AND status = 'PENDING'"
            )
        )
        projection = _refresh_simulated_score_projections(
            session,
            teacher_ids,
            calculated_at=generated_at,
        )
    return {
        "scenario": SCENARIO,
        "batch_id": BATCH_ID,
        "teacher_count": len(plans),
        "lesson_count": lesson_count,
        "task_assignment_count": len(plans) * len(FIXED_TASK_CODES),
        "task_transition_count": transition_count,
        "personalized": personalized,
        "personalized_statuses": personalized_statuses,
        "camp_day_min": min(plan.camp_day for plan in plans),
        "camp_day_max": max(plan.camp_day for plan in plans),
        "lesson_count_min": min(plan.lesson_count for plan in plans),
        "lesson_count_max": max(plan.lesson_count for plan in plans),
        "completed_task_count_min": min(
            plan.completed_task_count for plan in plans
        ),
        "completed_task_count_max": max(
            plan.completed_task_count for plan in plans
        ),
        "projection_id": projection["projection_id"],
        "graduated_count": projection["graduated_count"],
        "gold_count": projection["gold_count"],
        "lesson_score_state_count": projection[
            "lesson_score_state_count"
        ],
        "component_account_count": projection[
            "component_account_count"
        ],
    }


def simulated_cohort_summary(bind: Engine) -> dict[str, Any]:
    with Session(bind) as session:
        rows = session.execute(
            text(
                "SELECT t.teacher_id, t.camp_day, "
                "       count(DISTINCT l.lesson_id) AS lesson_count, "
                "       count(DISTINCT a.assignment_id) FILTER "
                "           (WHERE a.status = 'COMPLETED') AS completed_tasks, "
                "       max(s.raw_total_score) AS raw_total_score "
                "FROM teachers t "
                "LEFT JOIN lesson_facts l ON l.teacher_id = t.teacher_id "
                "LEFT JOIN task_assignments a ON a.teacher_id = t.teacher_id "
                "LEFT JOIN teacher_metric_snapshots s "
                "  ON s.teacher_id = t.teacher_id "
                " AND s.batch_id = t.source_batch_id "
                "WHERE t.teacher_id LIKE :prefix "
                "GROUP BY t.teacher_id, t.camp_day "
                "ORDER BY t.camp_day, t.teacher_id"
            ),
            {"prefix": f"{TEACHER_PREFIX}%"},
        ).mappings().all()
    return {
        "scenario": SCENARIO,
        "teachers": [dict(item) for item in rows],
    }


def remove_balanced_simulated_cohort(
    bind: Engine,
    *,
    expected_teacher_count: int = DEFAULT_TEACHER_COUNT,
    apply: bool = False,
) -> dict[str, Any]:
    """Remove only the explicitly seeded July 28 simulation cohort.

    The guard requires the reviewed batch, prefix, source system and scenario.
    A dry run is the default so this helper cannot delete a real teacher cohort
    through a broad ``data_mode`` condition.
    """

    with session_scope(bind) as session:
        batch = session.get(DataImportBatchRecord, BATCH_ID)
        if batch is None:
            return {
                "scenario": SCENARIO,
                "batch_id": BATCH_ID,
                "teacher_count": 0,
                "applied": False,
                "status": "ABSENT",
            }
        batch_payload = (
            batch.payload if isinstance(batch.payload, dict) else {}
        )
        if (
            batch.source_system != ACTOR
            or batch.source_sheet != SOURCE_SHEET
            or batch.snapshot_label != SNAPSHOT_LABEL
            or batch_payload.get("scenario") != SCENARIO
            or batch_payload.get("mock_only") is not True
        ):
            raise RuntimeError(
                "The simulation cleanup guard rejected the selected batch"
            )

        teachers = session.scalars(
            select(TeacherRecord).where(
                TeacherRecord.source_batch_id == BATCH_ID,
                TeacherRecord.teacher_id.like(f"{TEACHER_PREFIX}%"),
                TeacherRecord.data_mode == "MOCK",
            )
        ).all()
        teacher_ids = {item.teacher_id for item in teachers}
        if len(teacher_ids) != expected_teacher_count:
            raise RuntimeError(
                "The simulation cleanup guard expected "
                f"{expected_teacher_count} teachers but found "
                f"{len(teacher_ids)}"
            )

        task_ids = set(
            session.scalars(
                select(TaskAssignmentRecord.assignment_id).where(
                    TaskAssignmentRecord.teacher_id.in_(teacher_ids)
                )
            ).all()
        )
        case_ids = set(
            session.scalars(
                select(OpsCaseRecord.case_id).where(
                    OpsCaseRecord.teacher_id.in_(teacher_ids)
                )
            ).all()
        )
        notification_ids = set(
            session.scalars(
                select(NotificationRecord.notification_id).where(
                    NotificationRecord.teacher_id.in_(teacher_ids)
                )
            ).all()
        )
        lesson_ids = set(
            session.scalars(
                select(LessonFactRecord.lesson_id).where(
                    LessonFactRecord.teacher_id.in_(teacher_ids)
                )
            ).all()
        )
        reference_ids = (
            teacher_ids | task_ids | case_ids | notification_ids | lesson_ids
        )
        preview = {
            "scenario": SCENARIO,
            "batch_id": BATCH_ID,
            "teacher_count": len(teacher_ids),
            "lesson_count": len(lesson_ids),
            "task_assignment_count": len(task_ids),
            "case_count": len(case_ids),
            "notification_count": len(notification_ids),
            "applied": apply,
            "status": "READY" if not apply else "REMOVED",
        }
        if not apply:
            session.rollback()
            return preview

        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text(
                    "ALTER TABLE public.task_assignments "
                    "DISABLE TRIGGER trg_task_assignment_reject_delete"
                )
            )

        def remove(model: Any, *conditions: Any) -> int:
            result = session.execute(delete(model).where(*conditions))
            return max(int(result.rowcount or 0), 0)

        removed: dict[str, int] = {}
        removed["provider_calls"] = remove(
            ProviderCallRecord,
            ProviderCallRecord.task_id.in_(task_ids),
        )
        removed["agent_decisions"] = remove(
            AgentDecisionRecord,
            AgentDecisionRecord.teacher_id.in_(teacher_ids),
        )
        removed["notification_events"] = remove(
            NotificationEventRecord,
            NotificationEventRecord.notification_id.in_(notification_ids),
        )
        removed["ops_decisions"] = remove(
            OpsDecisionRecord,
            OpsDecisionRecord.case_id.in_(case_ids),
        )
        removed["audit_events"] = remove(
            AuditEventRecord,
            or_(
                AuditEventRecord.teacher_id.in_(teacher_ids),
                AuditEventRecord.task_id.in_(task_ids),
                AuditEventRecord.case_id.in_(case_ids),
            ),
        )
        removed["outbound_outputs"] = remove(
            OutboundOutputRecord,
            or_(
                OutboundOutputRecord.teacher_id.in_(teacher_ids),
                OutboundOutputRecord.task_id.in_(task_ids),
                OutboundOutputRecord.case_id.in_(case_ids),
            ),
        )
        removed["outbox_events"] = remove(
            OutboxEventRecord,
            OutboxEventRecord.aggregate_id.in_(reference_ids),
        )
        removed["idempotency_records"] = remove(
            IdempotencyRecord,
            IdempotencyRecord.resource_id.in_(reference_ids),
        )
        removed["notifications"] = remove(
            NotificationRecord,
            NotificationRecord.teacher_id.in_(teacher_ids),
        )
        removed["ops_cases"] = remove(
            OpsCaseRecord,
            OpsCaseRecord.teacher_id.in_(teacher_ids),
        )
        removed["score_entries"] = remove(
            ScoreEntryRecord,
            ScoreEntryRecord.teacher_id.in_(teacher_ids),
        )
        removed["trigger_matches"] = remove(
            PersonalizedTriggerMatchRecord,
            PersonalizedTriggerMatchRecord.teacher_id.in_(teacher_ids),
        )
        removed["lesson_dimension_scores"] = remove(
            LessonDimensionScoreRecord,
            LessonDimensionScoreRecord.teacher_id.in_(teacher_ids),
        )
        removed["score_component_accounts"] = remove(
            ScoreComponentAccountRecord,
            ScoreComponentAccountRecord.teacher_id.in_(teacher_ids),
        )
        removed["score_accounts"] = remove(
            ScoreAccountRecord,
            ScoreAccountRecord.teacher_id.in_(teacher_ids),
        )
        removed["task_assignments"] = remove(
            TaskAssignmentRecord,
            TaskAssignmentRecord.teacher_id.in_(teacher_ids),
        )
        removed["lesson_facts"] = remove(
            LessonFactRecord,
            LessonFactRecord.teacher_id.in_(teacher_ids),
        )
        snapshot_table = _snapshot_table(session)
        removed["teacher_metric_snapshots"] = remove(
            snapshot_table,
            snapshot_table.c.batch_id == BATCH_ID,
        )
        removed["source_records"] = remove(
            SourceRecord,
            SourceRecord.batch_id == BATCH_ID,
        )
        removed["teachers"] = remove(
            TeacherRecord,
            TeacherRecord.teacher_id.in_(teacher_ids),
        )
        removed["complaint_category_rules"] = remove(
            ComplaintCategoryRuleRecord,
            ComplaintCategoryRuleRecord.batch_id == BATCH_ID,
        )
        removed["data_import_batches"] = remove(
            DataImportBatchRecord,
            DataImportBatchRecord.batch_id == BATCH_ID,
        )
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text(
                    "ALTER TABLE public.task_assignments "
                    "ENABLE TRIGGER trg_task_assignment_reject_delete"
                )
            )
        preview["removed"] = removed
        return preview


__all__ = [
    "BATCH_ID",
    "DEFAULT_SEED",
    "SCENARIO",
    "SimulatedTeacherPlan",
    "build_balanced_plan",
    "remove_balanced_simulated_cohort",
    "seed_balanced_simulated_cohort",
    "simulated_cohort_summary",
]
