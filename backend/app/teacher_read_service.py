from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, and_, case, func, literal, or_, select

from .config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    NotificationRecord,
    OpsCaseRecord,
    ScoreAccountRecord,
    TaskAssignmentRecord,
    TeacherRecord,
)
from .task_service import TaskService


DIMENSION_LABELS = {
    "RELIABILITY": "可靠性",
    "USER_FEEDBACK": "用户反馈",
    "CLASS_QUALITY": "课堂质量",
    "CAPACITY": "供给达标（Peak slots）",
    "NEW_TEACHER_TASK": "成长任务（必修）",
}
DIMENSION_ORDER = {
    "USER_FEEDBACK": 0,
    "RELIABILITY": 1,
    "CLASS_QUALITY": 2,
    "CAPACITY": 3,
    "NEW_TEACHER_TASK": 4,
}
_TEACHER_DISPLAY_SCORE_CAP = 200.0
ACTIVE_TASK_STATUSES = {
    "ASSIGNED",
    "VIEWED",
    "IN_PROGRESS",
    "SUBMITTED",
    "UNDER_REVIEW",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _policy_from_payload(
    published_payload: dict[str, Any] | None,
) -> tuple[ScoreGraduationConfig, str, str]:
    payload = deepcopy(published_payload)
    source = "PUBLISHED"
    if payload is None:
        payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION])
        source = "DEFAULT_EMPTY_DATABASE"
    policy = ScoreGraduationConfig.model_validate(payload)
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return policy, source, digest


def _employment_status(teacher: TeacherRecord) -> str | None:
    value = (teacher.payload or {}).get("employment_status")
    return str(value) if value is not None else None


def _teacher_projection(teacher: TeacherRecord) -> dict[str, Any]:
    payload = deepcopy(teacher.payload or {})
    raw_total = float(teacher.total_score)
    public_total = min(
        float(payload.get("external_display_score", raw_total)),
        _TEACHER_DISPLAY_SCORE_CAP,
    )
    graduation_threshold = float(
        payload.get("graduation_threshold", teacher.graduation_threshold)
        or 0
    )
    gold_threshold = float(payload.get("gold_threshold", 200) or 0)
    graduation_qualified = bool(
        teacher.graduation_state == "GRADUATED"
        or payload.get("graduation_qualified")
    )
    gold_qualified = bool(teacher.gold_qualified or payload.get("gold_qualified"))
    return {
        **payload,
        "teacher_id": teacher.teacher_id,
        "name": teacher.name,
        "country": teacher.country,
        "timezone": teacher.timezone,
        "camp_day": teacher.camp_day,
        "raw_total_score": raw_total,
        "total_score": raw_total,
        "external_display_score": public_total,
        "graduation_threshold": graduation_threshold,
        "gold_threshold": gold_threshold,
        "graduation_external_score": float(
            payload.get("graduation_external_score", graduation_threshold)
            or 0
        ),
        "gold_external_score": float(
            payload.get("gold_external_score", gold_threshold) or 0
        ),
        "graduation_score_threshold_met": raw_total >= graduation_threshold,
        "gold_score_threshold_met": raw_total >= gold_threshold,
        "graduation_criteria_met": bool(
            payload.get("graduation_criteria_met", graduation_qualified)
        ),
        "graduation_qualified": graduation_qualified,
        "gold_criteria_met": bool(
            payload.get("gold_criteria_met", gold_qualified)
        ),
        "gold_qualified": gold_qualified,
        "graduation_state": teacher.graduation_state,
        "data_mode": teacher.data_mode,
        "employment_status": _employment_status(teacher),
        "source_snapshot_label": teacher.source_snapshot_label,
        "first_booked_date": payload.get("first_booked_date"),
        "is_cpl_tesol": payload.get("is_cpl_tesol"),
        "is_self_introduce": payload.get("is_self_introduce"),
        "lessons_completed": int(payload.get("lessons_completed") or 0),
        "score_policy_version": payload.get(
            "score_policy_version",
            payload.get("score_rule_version"),
        ),
        "score_policy_sha256": payload.get("score_policy_sha256"),
        "updated_at": _iso(teacher.updated_at),
    }


def _case_payload(record: OpsCaseRecord) -> dict[str, Any]:
    return {
        **deepcopy(record.payload or {}),
        "case_id": record.case_id,
        "case_type": record.case_type,
        "teacher_id": record.teacher_id,
        "task_id": record.task_id,
        "priority": record.priority,
        "status": record.status,
        "source_reason": record.source_reason,
        "external_action_status": record.external_action_status,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


def _teacher_list_summary(projected: dict[str, Any]) -> dict[str, Any]:
    scalar_fields = (
        "teacher_id",
        "name",
        "avatar",
        "camp_day",
        "lessons_completed",
        "raw_total_score",
        "total_score",
        "external_display_score",
        "base_score",
        "graduation_threshold",
        "gold_threshold",
        "graduation_external_score",
        "gold_external_score",
        "graduation_effect",
        "graduation_score_threshold_met",
        "graduation_criteria_met",
        "graduation_qualified",
        "gold_score_threshold_met",
        "gold_criteria_met",
        "gold_qualified",
        "graduation_state",
        "data_mode",
        "employment_status",
        "source_snapshot_label",
        "score_policy_version",
        "score_policy_source",
        "updated_at",
        "active_task_count",
        "open_case_count",
        "next_best_action",
    )
    summary = {
        field: deepcopy(projected[field])
        for field in scalar_fields
        if field in projected
    }
    summary["dimensions"] = [
        {
            field: deepcopy(dimension[field])
            for field in ("code", "label", "score", "source_mode")
            if field in dimension
        }
        for dimension in projected.get("dimensions", [])
    ]
    summary["risk_tags"] = deepcopy(projected.get("risk_tags", []))
    return summary


class TeacherReadService:
    """Current teacher list/detail reads from normalized persisted facts."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    @staticmethod
    def _base_statement() -> Any:
        return select(TeacherRecord)

    def data_mode_counts(self) -> dict[str, int]:
        with session_scope(self.engine) as session:
            return {
                str(mode or "UNKNOWN").upper(): int(count)
                for mode, count in session.execute(
                    select(TeacherRecord.data_mode, func.count())
                    .group_by(TeacherRecord.data_mode)
                    .order_by(TeacherRecord.data_mode)
                ).all()
            }

    def list_teachers(
        self,
        *,
        page: int = 1,
        page_size: int = 24,
        keyword: str | None = None,
        data_mode: str | None = None,
        employment_status: str | None = None,
    ) -> dict[str, Any]:
        normalized_data_mode = (data_mode or "").strip().upper()
        normalized_employment = (employment_status or "").strip().casefold()
        if normalized_data_mode == "ALL":
            normalized_data_mode = ""
        if normalized_employment == "all":
            normalized_employment = ""

        with session_scope(self.engine) as session:
            statement = self._base_statement()
            if normalized_data_mode:
                statement = statement.where(
                    func.upper(TeacherRecord.data_mode) == normalized_data_mode
                )
            employment_expression = func.lower(
                func.trim(
                    func.coalesce(
                        TeacherRecord.payload["employment_status"].as_string(),
                        "UNKNOWN",
                    )
                )
            )
            if normalized_employment:
                statement = statement.where(
                    employment_expression == normalized_employment
                )
            needle = (keyword or "").strip()
            if needle:
                pattern = f"%{needle}%"
                statement = statement.where(
                    or_(
                        TeacherRecord.teacher_id.ilike(pattern),
                        TeacherRecord.name.ilike(pattern),
                        employment_expression.ilike(pattern),
                    )
                )
            total = int(
                session.scalar(
                    select(func.count()).select_from(statement.subquery())
                )
                or 0
            )
            teachers = session.scalars(
                statement.order_by(
                    case(
                        (func.upper(TeacherRecord.data_mode) == "REAL", 0),
                        (func.upper(TeacherRecord.data_mode) == "MIXED", 1),
                        (func.upper(TeacherRecord.data_mode) == "MOCK", 2),
                        else_=3,
                    ),
                    TeacherRecord.teacher_id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            teacher_ids = [teacher.teacher_id for teacher in teachers]
            active_task_counts = {
                teacher_id: int(count)
                for teacher_id, count in session.execute(
                    select(TaskAssignmentRecord.teacher_id, func.count())
                    .where(
                        TaskAssignmentRecord.teacher_id.in_(teacher_ids),
                        TaskAssignmentRecord.status.in_(ACTIVE_TASK_STATUSES),
                    )
                    .group_by(TaskAssignmentRecord.teacher_id)
                ).all()
            }
            open_case_counts = {
                teacher_id: int(count)
                for teacher_id, count in session.execute(
                    select(OpsCaseRecord.teacher_id, func.count())
                    .where(
                        OpsCaseRecord.teacher_id.in_(teacher_ids),
                        OpsCaseRecord.status == "OPEN",
                    )
                    .group_by(OpsCaseRecord.teacher_id)
                ).all()
            }
            items: list[dict[str, Any]] = []
            for teacher in teachers:
                item = _teacher_projection(teacher)
                item["active_task_count"] = active_task_counts.get(
                    teacher.teacher_id, 0
                )
                item["open_case_count"] = open_case_counts.get(
                    teacher.teacher_id, 0
                )
                items.append(_teacher_list_summary(item))

            data_modes = [
                str(value or "UNKNOWN").upper()
                for value in session.scalars(
                    select(TeacherRecord.data_mode)
                    .distinct()
                    .order_by(TeacherRecord.data_mode)
                ).all()
            ]
            employment_statuses = sorted(
                {
                    str(value or "UNKNOWN")
                    for value in session.scalars(
                        select(
                            func.coalesce(
                                TeacherRecord.payload[
                                    "employment_status"
                                ].as_string(),
                                "UNKNOWN",
                            )
                        )
                    .select_from(TeacherRecord)
                    .distinct()
                    ).all()
                },
                key=str.casefold,
            )
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "filters": {
                    "keyword": needle,
                    "data_mode": normalized_data_mode or None,
                    "employment_status": normalized_employment or None,
                    "available_data_modes": data_modes,
                    "available_employment_statuses": employment_statuses,
                },
            }

    def teacher_options(
        self,
        *,
        keyword: str | None = None,
        limit: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Return a bounded scalar projection for remote-search selectors."""

        normalized_keyword = str(keyword or "").strip().casefold()
        employment_status = TeacherRecord.payload[
            "employment_status"
        ].as_string().label("employment_status")
        timezone_source = TeacherRecord.payload[
            "timezone_source_mode"
        ].as_string().label("timezone_source")
        with session_scope(self.engine) as session:
            statement = (
                select(
                    TeacherRecord.teacher_id,
                    TeacherRecord.name,
                    TeacherRecord.data_mode,
                    employment_status,
                    TeacherRecord.graduation_state,
                    TeacherRecord.timezone,
                    timezone_source,
                )
                .select_from(TeacherRecord)
            )
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                statement = statement.where(
                    or_(
                        func.lower(TeacherRecord.teacher_id).like(pattern),
                        func.lower(TeacherRecord.name).like(pattern),
                    )
                )
            rows = session.execute(
                statement.order_by(TeacherRecord.teacher_id)
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
            result: list[dict[str, Any]] = []
            for row in rows:
                blockers: list[str] = []
                if row.graduation_state != "IN_CAMP":
                    blockers.append("GRADUATED")
                timezone_source = str(
                    row.timezone_source or ""
                ).upper()
                if (
                    not str(row.timezone or "").strip()
                    or (
                        str(row.data_mode or "").upper()
                        in {"REAL", "MIXED"}
                        and timezone_source
                        in {
                            "",
                            "MISSING",
                            "SOURCE_MISSING",
                            "MISSING_INPUT_ZERO",
                            "STORAGE_PLACEHOLDER",
                        }
                    )
                ):
                    blockers.append("TIMEZONE_UNAVAILABLE")
                result.append(
                    {
                        "teacher_id": row.teacher_id,
                        "name": row.name,
                        "data_mode": row.data_mode,
                        "employment_status": row.employment_status,
                        "graduation_state": row.graduation_state,
                        "task_issuance_blockers": blockers,
                    }
                )
            return result

    def teacher_detail(self, teacher_id: str) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            teacher = session.scalar(
                self._base_statement().where(
                    TeacherRecord.teacher_id == teacher_id
                )
            )
            if teacher is None:
                raise LookupError(teacher_id)
            detail = _teacher_projection(teacher)
            detail["ops_cases"] = [
                _case_payload(item)
                for item in session.scalars(
                    select(OpsCaseRecord)
                    .where(OpsCaseRecord.teacher_id == teacher_id)
                    .order_by(
                        OpsCaseRecord.created_at.desc(),
                        OpsCaseRecord.case_id,
                    )
                ).all()
            ]
            detail["notifications"] = [
                {
                    **deepcopy(item.payload or {}),
                    "notification_id": item.notification_id,
                    "teacher_id": item.teacher_id,
                    "status": item.status,
                    "requested_at": _iso(item.requested_at),
                }
                for item in session.scalars(
                    select(NotificationRecord)
                    .where(NotificationRecord.teacher_id == teacher_id)
                    .order_by(
                        NotificationRecord.requested_at.desc(),
                        NotificationRecord.notification_id,
                    )
                ).all()
            ]
        detail["task_assignments"] = TaskService(
            self.engine
        ).list_assignments(teacher_id=teacher_id)
        return detail


class DashboardReadService:
    """Aggregate the operator dashboard directly in SQL."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    def dashboard(self) -> dict[str, Any]:
        funnel = {
            status: {
                "teacher_count": 0,
                "graduation_score_reached_count": 0,
                "graduation_criteria_met_count": 0,
                "graduation_qualified_count": 0,
                "gold_eligible_count": 0,
                "gold_qualified_count": 0,
            }
            for status in ("on", "off", "hei")
        }
        with session_scope(self.engine) as session:
            published_payload = session.scalar(
                select(ConfigVersionRecord.payload)
                .where(
                    ConfigVersionRecord.config_key
                    == ConfigKey.SCORE_GRADUATION.value,
                    ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
                )
                .order_by(ConfigVersionRecord.version_number.desc())
            )
            policy, policy_source, policy_sha256 = _policy_from_payload(
                published_payload
            )
            graduation_threshold = policy.thresholds.graduation_raw_score
            gold_threshold = policy.thresholds.gold_raw_score

            employment_expression = func.lower(
                func.trim(
                    func.coalesce(
                        TeacherRecord.payload["employment_status"].as_string(),
                        "UNKNOWN",
                    )
                )
            )
            data_mode_expression = func.upper(
                func.coalesce(TeacherRecord.data_mode, "UNKNOWN")
            )
            raw_total_expression = func.coalesce(TeacherRecord.total_score, 0)
            graduation_qualified_expression = or_(
                TeacherRecord.graduation_state == "GRADUATED",
                func.coalesce(
                    TeacherRecord.payload[
                        "graduation_qualified"
                    ].as_boolean(),
                    False,
                ),
            )
            gold_qualified_expression = or_(
                TeacherRecord.gold_qualified.is_(True),
                func.coalesce(
                    TeacherRecord.payload["gold_qualified"].as_boolean(),
                    False,
                ),
            )
            gold_criteria_met_expression = func.coalesce(
                TeacherRecord.payload["gold_criteria_met"].as_boolean(),
                gold_qualified_expression,
            )
            aggregate_rows = session.execute(
                select(
                    data_mode_expression.label("data_mode"),
                    employment_expression.label("employment_status"),
                    func.count().label("teacher_count"),
                    func.sum(
                        case(
                            (
                                and_(
                                    employment_expression == "on",
                                    TeacherRecord.graduation_state
                                    == "IN_CAMP",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("active_teacher_count"),
                    literal(0).label("settlement_pending_count"),
                    func.sum(
                        case(
                            (
                                raw_total_expression
                                >= graduation_threshold,
                                1,
                            ),
                            else_=0,
                        )
                    ).label("graduation_score_reached_count"),
                    func.sum(
                        case(
                            (graduation_qualified_expression, 1),
                            else_=0,
                        )
                    ).label("graduation_qualified_count"),
                    func.sum(
                        case(
                            (raw_total_expression >= gold_threshold, 1),
                            else_=0,
                        )
                    ).label("gold_score_reached_count"),
                    func.sum(
                        case((gold_qualified_expression, 1), else_=0)
                    ).label("gold_qualified_count"),
                    func.sum(
                        case((gold_criteria_met_expression, 1), else_=0)
                    ).label("gold_criteria_met_count"),
                )
                .select_from(TeacherRecord)
                .group_by(data_mode_expression, employment_expression)
            ).all()

            data_mode_counts: dict[str, int] = {}
            employment_status_counts: dict[str, int] = {}
            teacher_count = 0
            active_teacher_count = 0
            settlement_pending_count = 0
            graduation_score_reached_count = 0
            graduation_qualified_count = 0
            gold_score_reached_count = 0
            gold_qualified_count = 0
            gold_criteria_met_count = 0
            for row in aggregate_rows:
                count = int(row.teacher_count or 0)
                data_mode = str(row.data_mode or "UNKNOWN").upper()
                employment = str(
                    row.employment_status or "UNKNOWN"
                ).strip().casefold()
                teacher_count += count
                active_teacher_count += int(row.active_teacher_count or 0)
                settlement_pending_count += int(
                    row.settlement_pending_count or 0
                )
                graduation_score_reached_count += int(
                    row.graduation_score_reached_count or 0
                )
                graduation_qualified_count += int(
                    row.graduation_qualified_count or 0
                )
                gold_score_reached_count += int(
                    row.gold_score_reached_count or 0
                )
                gold_qualified_count += int(row.gold_qualified_count or 0)
                gold_criteria_met_count += int(
                    row.gold_criteria_met_count or 0
                )
                data_mode_counts[data_mode] = (
                    data_mode_counts.get(data_mode, 0) + count
                )
                employment_status_counts[employment] = (
                    employment_status_counts.get(employment, 0) + count
                )
                bucket = funnel.get(employment)
                if bucket is None:
                    continue
                bucket["teacher_count"] += count
                bucket["graduation_score_reached_count"] += int(
                    row.graduation_score_reached_count or 0
                )
                bucket["graduation_criteria_met_count"] += int(
                    row.graduation_qualified_count or 0
                )
                bucket["graduation_qualified_count"] += int(
                    row.graduation_qualified_count or 0
                )
                bucket["gold_eligible_count"] += int(
                    row.gold_qualified_count or 0
                )
                bucket["gold_qualified_count"] += int(
                    row.gold_qualified_count or 0
                )

            task_counts = session.execute(
                select(
                    func.count(TaskAssignmentRecord.assignment_id),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    TaskAssignmentRecord.status.in_(
                                        ACTIVE_TASK_STATUSES
                                    ),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    TaskAssignmentRecord.status
                                    == "COMPLETED",
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    select(func.count())
                    .select_from(OpsCaseRecord)
                    .where(OpsCaseRecord.status == "OPEN")
                    .scalar_subquery(),
                    select(func.count())
                    .select_from(NotificationRecord)
                    .where(
                        NotificationRecord.status
                        == "INTEGRATION_FAILED"
                    )
                    .scalar_subquery(),
                )
            ).one()
            task_total = int(task_counts[0] or 0)
            active_tasks = int(task_counts[1] or 0)
            completed_tasks = int(task_counts[2] or 0)
            open_cases = int(task_counts[3] or 0)
            notification_failures = int(task_counts[4] or 0)
            dimension_averages = [
                {
                    "code": dimension,
                    "label": DIMENSION_LABELS.get(dimension, dimension),
                    "average": round(float(average or 0), 1),
                }
                for dimension, average in session.execute(
                    select(
                        ScoreAccountRecord.dimension,
                        func.avg(ScoreAccountRecord.current_score),
                    )
                    .group_by(ScoreAccountRecord.dimension)
                    .order_by(
                        case(
                            *[
                                (
                                    ScoreAccountRecord.dimension == code,
                                    rank,
                                )
                                for code, rank in DIMENSION_ORDER.items()
                            ],
                            else_=999,
                        )
                    )
                ).all()
            ]

        return {
            "as_of": _iso(datetime.now(timezone.utc)),
            "graduation_threshold": graduation_threshold,
            "gold_threshold": gold_threshold,
            "graduation_external_score": (
                policy.thresholds.graduation_external_score
            ),
            "gold_external_score": policy.thresholds.gold_external_score,
            "graduation_effect": policy.graduation_effect,
            "score_policy_version": policy.policy_version,
            "score_policy_sha256": policy_sha256,
            "score_projection_scope": "PERSISTED_CURRENT",
            "score_policy_source": policy_source,
            "teacher_count": teacher_count,
            "active_teacher_count": active_teacher_count,
            "settlement_pending_count": settlement_pending_count,
            "graduation_score_reached_count": (
                graduation_score_reached_count
            ),
            "graduation_criteria_met_count": graduation_qualified_count,
            "gold_score_reached_count": gold_score_reached_count,
            "gold_eligible_count": gold_qualified_count,
            "gold_criteria_met_count": gold_criteria_met_count,
            "graduation_qualified_count": graduation_qualified_count,
            "gold_qualified_count": gold_qualified_count,
            "data_mode_counts": data_mode_counts,
            "employment_status_counts": employment_status_counts,
            "funnel_by_employment_status": funnel,
            "issued_task_count": task_total,
            "active_shared_task_count": active_tasks,
            "unacknowledged_task_count": 0,
            "open_case_count": open_cases,
            "completed_execution_count": completed_tasks,
            "notification_integration_failure_count": notification_failures,
            "p0_confirmation_waiting_count": 0,
            "dimension_averages": dimension_averages,
        }
