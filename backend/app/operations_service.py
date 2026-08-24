from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Engine,
    and_,
    case,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    tuple_,
)
from sqlalchemy.orm import Session

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    ComplaintCategoryRuleRecord,
    LessonSourceWideRecord,
    NotificationRecord,
    OpsCaseRecord,
    OpsDecisionRecord,
    PersonalizedTriggerMatchRecord,
    TaskAssignmentRecord,
    TeacherRecord,
)
from .dts_v2_completion_correction import (
    CompletionCorrectionRequestV2,
    PostgresDtsV2CompletionCorrectionStore,
)
from .personalized_rules import normalize_text


TERMINAL_TASK_STATUSES = {
    "COMPLETED",
    "FAILED",
    "EXPIRED",
    "WAIVED",
    "CANCELLED",
}
TERMINAL_CASE_STATUSES = {"CLOSED", "RESOLVED", "CANCELLED"}
TERMINAL_NOTIFICATION_STATUSES = {"READ", "CLICKED", "CANCELLED", "FAILED"}
DOMAIN_META = {
    "RELIABILITY": "可靠性",
    "USER_FEEDBACK": "用户反馈",
    "CLASS_QUALITY": "课堂质量",
}
EVIDENCE_MATCH_SAMPLE_LIMIT = 20


def _active_match_expression() -> Any:
    # Keep the constant literal so PostgreSQL can use the partial active-output
    # index even after psycopg switches this query to a generic prepared plan.
    return (
        PersonalizedTriggerMatchRecord.match_status
        != literal_column("'SUPPRESSED'")
    )


def _domain_expression() -> Any:
    explicit = func.upper(
        func.coalesce(
            PersonalizedTriggerMatchRecord.evidence_snapshot[
                "domain"
            ].as_string(),
            "",
        )
    )
    return case(
        (explicit.in_(tuple(DOMAIN_META)), explicit),
        (
            PersonalizedTriggerMatchRecord.trigger_code.like("TR-REL%"),
            "RELIABILITY",
        ),
        (
            PersonalizedTriggerMatchRecord.trigger_code.like("TR-QUALITY%"),
            "CLASS_QUALITY",
        ),
        else_="USER_FEEDBACK",
    )


def _output_key_expression() -> Any:
    return (
        PersonalizedTriggerMatchRecord.output_type
        + literal(":")
        + func.coalesce(
            PersonalizedTriggerMatchRecord.output_id,
            PersonalizedTriggerMatchRecord.trigger_match_id,
        )
    )


def _output_status_expression() -> Any:
    return case(
        (
            PersonalizedTriggerMatchRecord.output_type == "TEACHER_TASK",
            func.coalesce(TaskAssignmentRecord.status, "OUTPUT_MISSING"),
        ),
        (
            PersonalizedTriggerMatchRecord.output_type == "OPS_CASE",
            func.coalesce(OpsCaseRecord.status, "OUTPUT_MISSING"),
        ),
        (
            PersonalizedTriggerMatchRecord.output_type == "NOTIFICATION",
            func.coalesce(NotificationRecord.status, "OUTPUT_MISSING"),
        ),
        (
            PersonalizedTriggerMatchRecord.output_type == "PENDING_DATA",
            "PENDING_DATA",
        ),
        else_=PersonalizedTriggerMatchRecord.match_status,
    )


def _open_output_expression() -> Any:
    return or_(
        and_(
            PersonalizedTriggerMatchRecord.output_type == "TEACHER_TASK",
            TaskAssignmentRecord.assignment_id.is_not(None),
            TaskAssignmentRecord.status.not_in(TERMINAL_TASK_STATUSES),
        ),
        and_(
            PersonalizedTriggerMatchRecord.output_type == "OPS_CASE",
            OpsCaseRecord.case_id.is_not(None),
            OpsCaseRecord.status.not_in(TERMINAL_CASE_STATUSES),
        ),
        and_(
            PersonalizedTriggerMatchRecord.output_type == "NOTIFICATION",
            NotificationRecord.notification_id.is_not(None),
            NotificationRecord.status.not_in(TERMINAL_NOTIFICATION_STATUSES),
        ),
        PersonalizedTriggerMatchRecord.output_type == "PENDING_DATA",
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _domain(trigger_code: str, evidence: dict[str, Any]) -> str:
    explicit = str(evidence.get("domain") or "").upper()
    if explicit in DOMAIN_META:
        return explicit
    if trigger_code.startswith("TR-REL"):
        return "RELIABILITY"
    if trigger_code.startswith("TR-QUALITY"):
        return "CLASS_QUALITY"
    return "USER_FEEDBACK"


def _evidence_payload(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, dict) else {}
    nested = source.get("evidence")
    return nested if isinstance(nested, dict) else source


def _evidence_summary(snapshot: dict[str, Any] | None) -> str:
    evidence = _evidence_payload(snapshot)
    parts: list[str] = []
    lesson_id = evidence.get("lesson_id")
    if lesson_id:
        parts.append(f"课程 {lesson_id}")
    if evidence.get("absence_reason_detail"):
        parts.append(f"缺席原因：{evidence['absence_reason_detail']}")
    complaint = evidence.get("complaint_level3") or evidence.get("complaint_category_l3")
    if complaint:
        level = evidence.get("source_level_code") or evidence.get("source_level")
        parts.append(f"投诉：{complaint}" + (f"（{level}）" if level else ""))
    negative_label = evidence.get("negative_feedback_label")
    if negative_label:
        hit_count = evidence.get("aggregate_hit_count") or evidence.get("hit_count")
        parts.append(
            f"重复差评：{negative_label}"
            + (f"（{hit_count} 节课）" if hit_count else "")
        )
    anomalies = evidence.get("anomalies")
    if isinstance(anomalies, list) and anomalies:
        parts.append("异常：" + "、".join(str(item) for item in anomalies))
    if evidence.get("distinct_student_count"):
        parts.append(f"不同学员拉黑 {evidence['distinct_student_count']} 人")
    issue = (
        evidence.get("data_issue")
        or evidence.get("reason_code")
        or evidence.get("missing_field")
    )
    if issue:
        parts.append(f"待补数据：{issue}")
    return "；".join(parts) or "触发证据已记录"


def _current_complaint_levels(
    session: Session,
    lessons: list[LessonSourceWideRecord],
) -> dict[str, str]:
    """Return the newest exact level-3 mapping for the paged lessons."""

    categories = {
        normalize_text(lesson.complaint_category_l3)
        for lesson in lessons
        if normalize_text(lesson.complaint_category_l3)
    }
    if not categories:
        return {}
    rules = session.scalars(
        select(ComplaintCategoryRuleRecord)
        .where(
            ComplaintCategoryRuleRecord.category_l3_normalized.in_(
                categories
            )
        )
        .order_by(
            ComplaintCategoryRuleRecord.created_at.desc(),
            ComplaintCategoryRuleRecord.rule_id.desc(),
        )
    ).all()
    levels: dict[str, str] = {}
    for rule in rules:
        key = normalize_text(rule.category_l3_normalized)
        if key:
            levels.setdefault(key, rule.source_level)
    return levels


class OperationsService:
    """Read model for an operator's macro-to-micro intervention workflow."""

    def __init__(
        self,
        bind: Engine | None = None,
        *,
        completion_correction_store: (
            PostgresDtsV2CompletionCorrectionStore | None
        ) = None,
    ) -> None:
        self.engine = bind or default_engine
        self.completion_correction_store = (
            completion_correction_store
            or PostgresDtsV2CompletionCorrectionStore()
        )

    def overview(self) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            active_match = _active_match_expression()
            summary = session.execute(
                select(
                    select(func.count())
                    .select_from(LessonSourceWideRecord)
                    .scalar_subquery()
                    .label("lesson_total"),
                    select(
                        func.count(
                            func.distinct(LessonSourceWideRecord.teacher_id)
                        )
                    )
                    .scalar_subquery()
                    .label("teacher_total"),
                    select(
                        func.count(
                            func.distinct(
                                PersonalizedTriggerMatchRecord.teacher_id
                            )
                        )
                    )
                    .where(active_match)
                    .scalar_subquery()
                    .label("affected_teacher_total"),
                    select(func.count())
                    .select_from(PersonalizedTriggerMatchRecord)
                    .where(
                        active_match,
                        PersonalizedTriggerMatchRecord.output_type
                        == "PENDING_DATA",
                    )
                    .scalar_subquery()
                    .label("pending_data_issues"),
                    select(func.count())
                    .select_from(TaskAssignmentRecord)
                    .where(
                        TaskAssignmentRecord.task_kind
                        == "PERSONALIZED_IMPROVEMENT",
                        TaskAssignmentRecord.status.not_in(
                            TERMINAL_TASK_STATUSES
                        ),
                    )
                    .scalar_subquery()
                    .label("open_personalized_tasks"),
                    select(func.count())
                    .select_from(OpsCaseRecord)
                    .where(
                        OpsCaseRecord.case_type == "SEVERE_COMPLAINT",
                        OpsCaseRecord.status.not_in(TERMINAL_CASE_STATUSES),
                    )
                    .scalar_subquery()
                    .label("severe_complaint_cases"),
                    select(
                        func.count(func.distinct(OpsCaseRecord.case_id))
                    )
                    .select_from(PersonalizedTriggerMatchRecord)
                    .join(
                        OpsCaseRecord,
                        and_(
                            PersonalizedTriggerMatchRecord.output_type
                            == "OPS_CASE",
                            PersonalizedTriggerMatchRecord.output_id
                            == OpsCaseRecord.case_id,
                        ),
                    )
                    .where(
                        active_match,
                        OpsCaseRecord.status.not_in(
                            TERMINAL_CASE_STATUSES
                        ),
                    )
                    .scalar_subquery()
                    .label("current_ops_todo_count"),
                    select(
                        func.max(
                            PersonalizedTriggerMatchRecord.matched_at
                        )
                    )
                    .scalar_subquery()
                    .label("latest_match"),
                )
            ).one()

            domain_expression = _domain_expression()
            output_key = _output_key_expression()
            is_open = _open_output_expression()
            risk_rows = session.execute(
                select(
                    domain_expression.label("domain"),
                    func.count().label("signal_count"),
                    func.count(
                        func.distinct(
                            PersonalizedTriggerMatchRecord.teacher_id
                        )
                    ).label("teacher_count"),
                    func.count(
                        func.distinct(
                            case((is_open, output_key), else_=None)
                        )
                    ).label("open_output_count"),
                )
                .select_from(PersonalizedTriggerMatchRecord)
                .outerjoin(
                    TaskAssignmentRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "TEACHER_TASK",
                        PersonalizedTriggerMatchRecord.output_id
                        == TaskAssignmentRecord.assignment_id,
                    ),
                )
                .outerjoin(
                    OpsCaseRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "OPS_CASE",
                        PersonalizedTriggerMatchRecord.output_id
                        == OpsCaseRecord.case_id,
                    ),
                )
                .outerjoin(
                    NotificationRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "NOTIFICATION",
                        PersonalizedTriggerMatchRecord.output_id
                        == NotificationRecord.notification_id,
                    ),
                )
                .where(active_match)
                .group_by(domain_expression)
            ).all()
            risk_by_domain = {
                str(row.domain): {
                    "signal_count": int(row.signal_count or 0),
                    "teacher_count": int(row.teacher_count or 0),
                    "open_output_count": int(row.open_output_count or 0),
                }
                for row in risk_rows
            }
            risk_breakdown = []
            for code, label in DOMAIN_META.items():
                counts = risk_by_domain.get(code, {})
                risk_breakdown.append(
                    {
                        "domain": code,
                        "label": label,
                        "signal_count": int(
                            counts.get("signal_count", 0)
                        ),
                        "teacher_count": int(
                            counts.get("teacher_count", 0)
                        ),
                        "open_output_count": int(
                            counts.get("open_output_count", 0)
                        ),
                    }
                )
            return {
                "as_of": _iso(summary.latest_match),
                "teacher_total": int(summary.teacher_total or 0),
                "lesson_total": int(summary.lesson_total or 0),
                "affected_teacher_total": int(
                    summary.affected_teacher_total or 0
                ),
                "open_personalized_tasks": int(
                    summary.open_personalized_tasks or 0
                ),
                "severe_complaint_cases": int(
                    summary.severe_complaint_cases or 0
                ),
                "current_ops_todo_count": int(
                    summary.current_ops_todo_count or 0
                ),
                "pending_data_issues": int(
                    summary.pending_data_issues or 0
                ),
                "risk_breakdown": risk_breakdown,
            }

    def interventions(
        self,
        *,
        output_type: str | None = None,
        status: str | None = None,
        domain: str | None = None,
        teacher_id: str | None = None,
        open_only: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            domain_expression = _domain_expression()
            output_key = _output_key_expression()
            output_status = _output_status_expression()
            is_open = _open_output_expression()
            title = case(
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "TEACHER_TASK",
                        TaskAssignmentRecord.assignment_id.is_not(None),
                    ),
                    func.coalesce(
                        TaskAssignmentRecord.display_title,
                        "",
                    ),
                ),
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "NOTIFICATION",
                        NotificationRecord.notification_id.is_not(None),
                    ),
                    func.coalesce(
                        NotificationRecord.payload["title"].as_string(),
                        "",
                    ),
                ),
                else_=PersonalizedTriggerMatchRecord.output_title,
            )
            why = case(
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "TEACHER_TASK",
                        TaskAssignmentRecord.assignment_id.is_not(None),
                    ),
                    func.coalesce(TaskAssignmentRecord.why, ""),
                ),
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "NOTIFICATION",
                        NotificationRecord.notification_id.is_not(None),
                    ),
                    func.coalesce(
                        NotificationRecord.payload["body"].as_string(),
                        "",
                    ),
                ),
                else_=func.coalesce(
                    PersonalizedTriggerMatchRecord.evidence_snapshot[
                        "why"
                    ].as_string(),
                    PersonalizedTriggerMatchRecord.output_title,
                ),
            )
            priority = case(
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "TEACHER_TASK",
                        TaskAssignmentRecord.assignment_id.is_not(None),
                    ),
                    TaskAssignmentRecord.priority,
                ),
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "OPS_CASE",
                        OpsCaseRecord.case_id.is_not(None),
                    ),
                    OpsCaseRecord.priority,
                ),
                (
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "NOTIFICATION",
                        NotificationRecord.notification_id.is_not(None),
                    ),
                    NotificationRecord.priority,
                ),
                else_=PersonalizedTriggerMatchRecord.evidence_snapshot[
                    "priority"
                ].as_string(),
            )
            action_label = case(
                (
                    PersonalizedTriggerMatchRecord.output_type
                    == "TEACHER_TASK",
                    "查看任务",
                ),
                (
                    PersonalizedTriggerMatchRecord.output_type == "OPS_CASE",
                    "处理投诉",
                ),
                (
                    PersonalizedTriggerMatchRecord.output_type
                    == "NOTIFICATION",
                    "查看提醒",
                ),
                (
                    PersonalizedTriggerMatchRecord.output_type
                    == "PENDING_DATA",
                    "补齐数据",
                ),
                else_="查看证据",
            )
            statement = (
                select(
                    output_key.label("output_key"),
                    PersonalizedTriggerMatchRecord.output_type.label(
                        "output_type"
                    ),
                    PersonalizedTriggerMatchRecord.output_id.label(
                        "materialized_output_id"
                    ),
                    PersonalizedTriggerMatchRecord.trigger_match_id.label(
                        "trigger_match_id"
                    ),
                    PersonalizedTriggerMatchRecord.teacher_id.label(
                        "teacher_id"
                    ),
                    TeacherRecord.name.label("teacher_name"),
                    domain_expression.label("domain"),
                    output_status.label("status"),
                    func.coalesce(priority, "P1").label("priority"),
                    title.label("title"),
                    why.label("why"),
                    action_label.label("action_label"),
                    PersonalizedTriggerMatchRecord.matched_at.label(
                        "matched_at"
                    ),
                )
                .select_from(PersonalizedTriggerMatchRecord)
                .join(
                    TeacherRecord,
                    TeacherRecord.teacher_id
                    == PersonalizedTriggerMatchRecord.teacher_id,
                )
                .outerjoin(
                    TaskAssignmentRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "TEACHER_TASK",
                        PersonalizedTriggerMatchRecord.output_id
                        == TaskAssignmentRecord.assignment_id,
                    ),
                )
                .outerjoin(
                    OpsCaseRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "OPS_CASE",
                        PersonalizedTriggerMatchRecord.output_id
                        == OpsCaseRecord.case_id,
                    ),
                )
                .outerjoin(
                    NotificationRecord,
                    and_(
                        PersonalizedTriggerMatchRecord.output_type
                        == "NOTIFICATION",
                        PersonalizedTriggerMatchRecord.output_id
                        == NotificationRecord.notification_id,
                    ),
                )
                .where(
                    _active_match_expression()
                )
            )
            if output_type:
                output_types = {
                    item.strip()
                    for item in output_type.split(",")
                    if item.strip()
                }
                statement = statement.where(
                    PersonalizedTriggerMatchRecord.output_type.in_(output_types)
                )
            if teacher_id:
                statement = statement.where(
                    PersonalizedTriggerMatchRecord.teacher_id == teacher_id
                )
            if domain:
                statement = statement.where(
                    domain_expression == domain
                )
            if status:
                statement = statement.where(output_status == status)
            if open_only:
                statement = statement.where(is_open)

            source = statement.subquery("intervention_signals")
            grouped = (
                select(
                    source.c.output_key,
                    source.c.output_type,
                    func.max(source.c.materialized_output_id).label(
                        "materialized_output_id"
                    ),
                    func.max(source.c.trigger_match_id).label(
                        "trigger_match_id"
                    ),
                    func.max(source.c.teacher_id).label("teacher_id"),
                    func.max(source.c.teacher_name).label("teacher_name"),
                    func.max(source.c.domain).label("domain"),
                    func.max(source.c.status).label("status"),
                    func.max(source.c.priority).label("priority"),
                    func.max(source.c.title).label("title"),
                    func.max(source.c.why).label("why"),
                    func.max(source.c.action_label).label("action_label"),
                    func.min(source.c.matched_at).label("triggered_at"),
                    func.count().label("signal_count"),
                )
                .group_by(source.c.output_key, source.c.output_type)
                .subquery("intervention_outputs")
            )
            count_rows = session.execute(
                select(
                    grouped.c.output_type,
                    func.count().label("output_count"),
                ).group_by(grouped.c.output_type)
            ).all()
            counts_by_type = {
                str(row.output_type): int(row.output_count)
                for row in count_rows
            }
            total = sum(counts_by_type.values())
            priority_order = case(
                (grouped.c.priority == "P0", 0),
                (grouped.c.priority == "P1", 1),
                (grouped.c.priority == "P2", 2),
                (grouped.c.priority == "P3", 3),
                else_=9,
            )
            page_rows = session.execute(
                select(grouped)
                .order_by(
                    case(
                        (grouped.c.output_type == "OPS_CASE", 0),
                        else_=1,
                    ),
                    case(
                        (grouped.c.output_type == "PENDING_DATA", 1),
                        else_=0,
                    ),
                    priority_order,
                    grouped.c.triggered_at,
                    grouped.c.output_key,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            page_keys = [str(row.output_key) for row in page_rows]
            evidence_by_key: dict[str, list[Any]] = defaultdict(list)
            if page_keys:
                evidence_ranked = (
                    select(
                        _output_key_expression().label("output_key"),
                        PersonalizedTriggerMatchRecord.evidence_snapshot,
                        PersonalizedTriggerMatchRecord.lesson_source_region,
                        PersonalizedTriggerMatchRecord.lesson_id,
                        func.row_number()
                        .over(
                            partition_by=_output_key_expression(),
                            order_by=(
                                PersonalizedTriggerMatchRecord.matched_at.desc(),
                                PersonalizedTriggerMatchRecord.trigger_match_id.desc(),
                            ),
                        )
                        .label("evidence_rank"),
                    )
                    .where(
                        _active_match_expression(),
                        _output_key_expression().in_(page_keys),
                    )
                )
                if domain:
                    evidence_ranked = evidence_ranked.where(
                        _domain_expression() == domain
                    )
                evidence_source = evidence_ranked.subquery(
                    "ranked_intervention_evidence"
                )
                evidence_statement = (
                    select(
                        evidence_source.c.output_key,
                        evidence_source.c.evidence_snapshot,
                        evidence_source.c.lesson_source_region,
                        evidence_source.c.lesson_id,
                    )
                    .where(
                        evidence_source.c.evidence_rank
                        <= EVIDENCE_MATCH_SAMPLE_LIMIT
                    )
                    .order_by(
                        evidence_source.c.output_key,
                        evidence_source.c.evidence_rank,
                    )
                )
                for evidence_row in session.execute(
                    evidence_statement
                ).all():
                    evidence_by_key[str(evidence_row.output_key)].append(
                        evidence_row
                    )

            rows: list[dict[str, Any]] = []
            for page_row in page_rows:
                evidence_summaries: list[str] = []
                lesson_ids: list[str] = []
                source_lessons: list[dict[str, str]] = []
                for evidence_row in evidence_by_key.get(
                    str(page_row.output_key), []
                ):
                    snapshot = (
                        evidence_row.evidence_snapshot
                        if isinstance(
                            evidence_row.evidence_snapshot, dict
                        )
                        else {}
                    )
                    evidence_summary = _evidence_summary(snapshot)
                    if evidence_summary not in evidence_summaries:
                        evidence_summaries.append(evidence_summary)
                    if (
                        evidence_row.lesson_source_region
                        and
                        evidence_row.lesson_id
                        and {
                            "source_region": evidence_row.lesson_source_region,
                            "source_appoint_id": evidence_row.lesson_id,
                        }
                        not in source_lessons
                    ):
                        source_lessons.append(
                            {
                                "source_region": evidence_row.lesson_source_region,
                                "source_appoint_id": evidence_row.lesson_id,
                            }
                        )
                        if evidence_row.lesson_id not in lesson_ids:
                            lesson_ids.append(evidence_row.lesson_id)
                signal_count = int(page_row.signal_count or 0)
                prefix = (
                    f"共 {signal_count} 次命中；"
                    if signal_count > 1
                    else ""
                )
                rows.append(
                    {
                        "output_id": (
                            page_row.materialized_output_id
                            or page_row.trigger_match_id
                        ),
                        "output_type": page_row.output_type,
                        "title": page_row.title,
                        "teacher_id": page_row.teacher_id,
                        "teacher_name": (
                            page_row.teacher_name or page_row.teacher_id
                        ),
                        "domain": page_row.domain,
                        "priority": page_row.priority,
                        "status": page_row.status,
                        "triggered_at": _iso(page_row.triggered_at),
                        "why": page_row.why,
                        "source_lesson_ids": lesson_ids,
                        "source_lessons": source_lessons,
                        "source_lesson_id": (
                            lesson_ids[0] if lesson_ids else None
                        ),
                        "action_label": page_row.action_label,
                        "signal_count": signal_count,
                        "evidence_sampled": (
                            signal_count
                            > len(
                                evidence_by_key.get(
                                    str(page_row.output_key),
                                    [],
                                )
                            )
                        ),
                        "evidence_summary": prefix
                        + "；".join(evidence_summaries[:3]),
                    }
                )
            return {
                "items": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "counts_by_type": counts_by_type,
            }

    def decide_case(
        self,
        *,
        case_id: str,
        decision: str,
        note: str,
        actor_id: str,
    ) -> dict[str, Any]:
        normalized_decision = decision.strip().upper()
        if normalized_decision not in {"START_PROCESSING", "RESOLVE"}:
            raise ValueError("unsupported case decision")
        if normalized_decision == "RESOLVE" and not note.strip():
            raise ValueError("resolution note is required")
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as session:
            case = session.scalar(
                select(OpsCaseRecord)
                .where(OpsCaseRecord.case_id == case_id)
                .with_for_update()
            )
            if case is None:
                raise LookupError("case not found")
            if case.case_type == "COURSE_COMPLETION_CORRECTION":
                raise ValueError(
                    "COURSE_COMPLETION_CORRECTION_REQUIRES_DEDICATED_DECISION"
                )
            if case.status in TERMINAL_CASE_STATUSES:
                raise RuntimeError("case is already terminal")
            next_status = "IN_REVIEW" if normalized_decision == "START_PROCESSING" else "RESOLVED"
            decision_id = f"OPS-DECISION-{uuid4().hex}"
            decision_payload = {
                "decision_id": decision_id,
                "case_id": case_id,
                "decision": normalized_decision,
                "note": note.strip(),
                "decided_at": _iso(now),
                "decided_by": actor_id,
                "previous_status": case.status,
                "new_status": next_status,
            }
            session.add(
                OpsDecisionRecord(
                    decision_id=decision_id,
                    case_id=case_id,
                    decision=normalized_decision,
                    note=note.strip(),
                    decided_at=now,
                    actor_type="OPS_USER",
                    payload=decision_payload,
                )
            )
            case.status = next_status
            case.updated_at = now
            case.payload = {
                **deepcopy(case.payload or {}),
                "latest_decision": decision_payload,
            }
            return {
                "case_id": case_id,
                "status": next_status,
                "decision_id": decision_id,
                "updated_at": _iso(now),
            }

    def apply_completion_correction(
        self,
        *,
        decision_id: str,
        case_id: str,
        decision: str,
        actor_id: str,
        reason: str,
        expected_case_revision: int,
        expected_conflict_fingerprint: str,
        expected_source_revision: int,
        expected_source_position: dict[str, Any],
        target_participation_seq: int | None,
        completion_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request = CompletionCorrectionRequestV2(
            decision_id=decision_id,
            case_id=case_id,
            decision=decision,
            actor_id=actor_id,
            reason=reason,
            expected_case_revision=expected_case_revision,
            expected_conflict_fingerprint=expected_conflict_fingerprint,
            expected_source_revision=expected_source_revision,
            expected_source_position=expected_source_position,
            target_participation_seq=target_participation_seq,
            completion_snapshot=completion_snapshot,
        )
        with self.engine.begin() as connection:
            result = self.completion_correction_store.apply(
                connection,
                request,
            )
        return {
            "outcome": result.outcome,
            "decision_id": result.decision_id,
            "case_id": result.case_id,
            "case_status": result.case_status,
            "completion_conflict_status": (
                result.completion_conflict_status
            ),
            "projection_event_ids": list(result.projection_event_ids),
        }

    def lessons(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        teacher_id: str | None = None,
        source_region: str | None = None,
        lesson_id: str | None = None,
        risk_only: bool = False,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            statement = select(LessonSourceWideRecord)
            if source_region is not None:
                if source_region not in {"dom", "ovs"}:
                    raise ValueError("source_region must be dom or ovs")
                statement = statement.where(
                    LessonSourceWideRecord.source_region == source_region
                )
            if teacher_id:
                statement = statement.where(
                    LessonSourceWideRecord.teacher_id == teacher_id
                )
            if lesson_id:
                if source_region is None:
                    matching_regions = set(
                        session.scalars(
                            select(LessonSourceWideRecord.source_region).where(
                                LessonSourceWideRecord.course_id == lesson_id
                            )
                        ).all()
                    )
                    if len(matching_regions) > 1:
                        raise ValueError(
                            "LESSON_IDENTITY_AMBIGUOUS:source_region is required"
                        )
                statement = statement.where(
                    LessonSourceWideRecord.course_id == lesson_id
                )
            if risk_only:
                statement = statement.where(
                    exists(
                        select(1).where(
                            PersonalizedTriggerMatchRecord.lesson_source_region
                            == LessonSourceWideRecord.source_region,
                            PersonalizedTriggerMatchRecord.lesson_id
                            == LessonSourceWideRecord.course_id,
                            PersonalizedTriggerMatchRecord.lesson_id.is_not(None),
                            _active_match_expression(),
                        )
                    )
                )
            count_statement = select(func.count()).select_from(statement.subquery())
            total = int(session.scalar(count_statement) or 0)
            records = session.scalars(
                statement.order_by(
                    LessonSourceWideRecord.lesson_date.desc(),
                    LessonSourceWideRecord.lesson_time.desc(),
                    LessonSourceWideRecord.source_region,
                    LessonSourceWideRecord.course_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            lesson_identities = [
                (item.source_region, item.course_id) for item in records
            ]
            matches_by_lesson: dict[
                tuple[str, str], list[PersonalizedTriggerMatchRecord]
            ] = defaultdict(list)
            if lesson_identities:
                for match in session.scalars(
                    select(PersonalizedTriggerMatchRecord).where(
                        tuple_(
                            PersonalizedTriggerMatchRecord.lesson_source_region,
                            PersonalizedTriggerMatchRecord.lesson_id,
                        ).in_(lesson_identities),
                        _active_match_expression(),
                    )
                ).all():
                    if match.lesson_source_region and match.lesson_id:
                        matches_by_lesson[
                            (match.lesson_source_region, match.lesson_id)
                        ].append(match)
            teacher_ids = {
                item.teacher_id for item in records if item.teacher_id is not None
            }
            teachers = {
                item.teacher_id: item.name
                for item in session.scalars(
                    select(TeacherRecord).where(TeacherRecord.teacher_id.in_(teacher_ids))
                ).all()
            }
            complaint_levels = _current_complaint_levels(session, records)
            items: list[dict[str, Any]] = []
            for lesson in records:
                matches = matches_by_lesson.get(
                    (lesson.source_region, lesson.course_id), []
                )
                domains = sorted(
                    {
                        _domain(
                            item.trigger_code,
                            item.evidence_snapshot if isinstance(item.evidence_snapshot, dict) else {},
                        )
                        for item in matches
                    }
                )
                items.append(
                    {
                        "lesson_id": lesson.course_id,
                        "source_region": lesson.source_region,
                        "teacher_id": lesson.teacher_id,
                        "teacher_name": teachers.get(lesson.teacher_id, lesson.teacher_id),
                        "lesson_date": lesson.lesson_date.isoformat() if lesson.lesson_date else None,
                        "lesson_time": lesson.lesson_time.strftime("%H:%M") if lesson.lesson_time else None,
                        "lesson_status": lesson.lesson_status,
                        "risk_domains": domains,
                        "signals": [item.output_title for item in matches],
                        "complaint_level": complaint_levels.get(
                            normalize_text(lesson.complaint_category_l3)
                        ),
                    }
                )
            return {"items": items, "total": total, "page": page, "page_size": page_size}
