from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    LessonFactRecord,
    NotificationRecord,
    OpsCaseRecord,
    OpsDecisionRecord,
    PersonalizedTriggerMatchRecord,
    TaskAssignmentRecord,
    TeacherRecord,
)


TERMINAL_TASK_STATUSES = {"COMPLETED", "EXPIRED", "WAIVED", "CANCELLED"}
TERMINAL_CASE_STATUSES = {"CLOSED", "RESOLVED", "CANCELLED"}
TERMINAL_NOTIFICATION_STATUSES = {"READ", "CLICKED", "CANCELLED", "FAILED"}
DOMAIN_META = {
    "RELIABILITY": "可靠性",
    "USER_FEEDBACK": "用户反馈",
    "CLASS_QUALITY": "课堂质量",
}


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


class OperationsService:
    """Read model for an operator's macro-to-micro intervention workflow."""

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    def overview(self) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            lesson_total = int(
                session.scalar(
                    select(func.count()).select_from(LessonFactRecord).where(
                        LessonFactRecord.source_batch_id.is_not(None)
                    )
                )
                or 0
            )
            teacher_total = int(
                session.scalar(
                    select(func.count(func.distinct(LessonFactRecord.teacher_id))).where(
                        LessonFactRecord.source_batch_id.is_not(None)
                    )
                )
                or 0
            )
            matches = session.scalars(
                select(PersonalizedTriggerMatchRecord).where(
                    PersonalizedTriggerMatchRecord.match_status != "SUPPRESSED"
                )
            ).all()
            affected_teacher_total = len({item.teacher_id for item in matches})
            pending_data_issues = sum(item.output_type == "PENDING_DATA" for item in matches)
            open_personalized_tasks = int(
                session.scalar(
                    select(func.count()).select_from(TaskAssignmentRecord).where(
                        TaskAssignmentRecord.task_kind == "PERSONALIZED_IMPROVEMENT",
                        TaskAssignmentRecord.status.not_in(TERMINAL_TASK_STATUSES),
                    )
                )
                or 0
            )
            severe_complaint_cases = int(
                session.scalar(
                    select(func.count()).select_from(OpsCaseRecord).where(
                        OpsCaseRecord.case_type == "SEVERE_COMPLAINT",
                        OpsCaseRecord.status.not_in(TERMINAL_CASE_STATUSES),
                    )
                )
                or 0
            )

            grouped: dict[str, dict[str, Any]] = {
                code: {
                    "domain": code,
                    "label": label,
                    "signal_count": 0,
                    "teacher_ids": set(),
                    "open_output_ids": set(),
                }
                for code, label in DOMAIN_META.items()
            }
            task_status = {
                item.assignment_id: item.status
                for item in session.scalars(
                    select(TaskAssignmentRecord).where(
                        TaskAssignmentRecord.assignment_id.in_(
                            [item.output_id for item in matches if item.output_type == "TEACHER_TASK" and item.output_id]
                        )
                    )
                ).all()
            }
            case_status = {
                item.case_id: item.status
                for item in session.scalars(
                    select(OpsCaseRecord).where(
                        OpsCaseRecord.case_id.in_(
                            [item.output_id for item in matches if item.output_type == "OPS_CASE" and item.output_id]
                        )
                    )
                ).all()
            }
            current_ops_todo_count = sum(
                status not in TERMINAL_CASE_STATUSES
                for status in case_status.values()
            )
            notification_status = {
                item.notification_id: item.status
                for item in session.scalars(
                    select(NotificationRecord).where(
                        NotificationRecord.notification_id.in_(
                            [item.output_id for item in matches if item.output_type == "NOTIFICATION" and item.output_id]
                        )
                    )
                ).all()
            }
            for item in matches:
                snapshot = item.evidence_snapshot if isinstance(item.evidence_snapshot, dict) else {}
                domain = _domain(item.trigger_code, snapshot)
                group = grouped[domain]
                group["signal_count"] += 1
                group["teacher_ids"].add(item.teacher_id)
                is_open = False
                if item.output_type == "TEACHER_TASK":
                    is_open = task_status.get(item.output_id) not in TERMINAL_TASK_STATUSES
                elif item.output_type == "OPS_CASE":
                    is_open = case_status.get(item.output_id) not in TERMINAL_CASE_STATUSES
                elif item.output_type == "NOTIFICATION":
                    is_open = notification_status.get(item.output_id) not in TERMINAL_NOTIFICATION_STATUSES
                elif item.output_type == "PENDING_DATA":
                    is_open = True
                if is_open:
                    group["open_output_ids"].add(
                        f"{item.output_type}:{item.output_id or item.trigger_match_id}"
                    )

            latest_match = session.scalar(select(func.max(PersonalizedTriggerMatchRecord.matched_at)))
            risk_breakdown = [
                {
                    "domain": item["domain"],
                    "label": item["label"],
                    "signal_count": item["signal_count"],
                    "teacher_count": len(item["teacher_ids"]),
                    "open_output_count": len(item["open_output_ids"]),
                }
                for item in grouped.values()
            ]
            return {
                "as_of": _iso(latest_match),
                "teacher_total": teacher_total,
                "lesson_total": lesson_total,
                "affected_teacher_total": affected_teacher_total,
                "open_personalized_tasks": open_personalized_tasks,
                "severe_complaint_cases": severe_complaint_cases,
                "current_ops_todo_count": current_ops_todo_count,
                "pending_data_issues": pending_data_issues,
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
            statement = (
                select(PersonalizedTriggerMatchRecord)
                .where(PersonalizedTriggerMatchRecord.match_status != "SUPPRESSED")
                .order_by(
                    PersonalizedTriggerMatchRecord.matched_at.desc(),
                    PersonalizedTriggerMatchRecord.trigger_match_id.desc(),
                )
            )
            if output_type:
                statement = statement.where(PersonalizedTriggerMatchRecord.output_type == output_type)
            if teacher_id:
                statement = statement.where(PersonalizedTriggerMatchRecord.teacher_id == teacher_id)
            matches = session.scalars(statement).all()
            teacher_ids = {item.teacher_id for item in matches}
            teachers = {
                item.teacher_id: item.name
                for item in session.scalars(
                    select(TeacherRecord).where(TeacherRecord.teacher_id.in_(teacher_ids))
                ).all()
            }
            task_ids = [item.output_id for item in matches if item.output_type == "TEACHER_TASK" and item.output_id]
            case_ids = [item.output_id for item in matches if item.output_type == "OPS_CASE" and item.output_id]
            notification_ids = [item.output_id for item in matches if item.output_type == "NOTIFICATION" and item.output_id]
            tasks = {
                item.assignment_id: item
                for item in session.scalars(
                    select(TaskAssignmentRecord).where(TaskAssignmentRecord.assignment_id.in_(task_ids))
                ).all()
            }
            cases = {
                item.case_id: item
                for item in session.scalars(select(OpsCaseRecord).where(OpsCaseRecord.case_id.in_(case_ids))).all()
            }
            notifications = {
                item.notification_id: item
                for item in session.scalars(
                    select(NotificationRecord).where(NotificationRecord.notification_id.in_(notification_ids))
                ).all()
            }

            grouped_rows: dict[str, dict[str, Any]] = {}
            for item in matches:
                snapshot = item.evidence_snapshot if isinstance(item.evidence_snapshot, dict) else {}
                item_domain = _domain(item.trigger_code, snapshot)
                if domain and item_domain != domain:
                    continue
                output_status = item.match_status
                action_label = "查看证据"
                if item.output_type == "TEACHER_TASK":
                    task = tasks.get(item.output_id)
                    output_status = task.status if task else "OUTPUT_MISSING"
                    action_label = "查看任务"
                elif item.output_type == "OPS_CASE":
                    case = cases.get(item.output_id)
                    output_status = case.status if case else "OUTPUT_MISSING"
                    action_label = "处理投诉"
                elif item.output_type == "NOTIFICATION":
                    notification = notifications.get(item.output_id)
                    output_status = notification.status if notification else "OUTPUT_MISSING"
                    action_label = "查看提醒"
                elif item.output_type == "PENDING_DATA":
                    output_status = "PENDING_DATA"
                    action_label = "补齐数据"
                if status and output_status != status:
                    continue
                if open_only:
                    if item.output_type == "TEACHER_TASK":
                        task = tasks.get(item.output_id)
                        is_open = (
                            task is not None
                            and task.status not in TERMINAL_TASK_STATUSES
                        )
                    elif item.output_type == "OPS_CASE":
                        case = cases.get(item.output_id)
                        is_open = (
                            case is not None
                            and case.status not in TERMINAL_CASE_STATUSES
                        )
                    elif item.output_type == "NOTIFICATION":
                        notification = notifications.get(item.output_id)
                        is_open = (
                            notification is not None
                            and notification.status
                            not in TERMINAL_NOTIFICATION_STATUSES
                        )
                    else:
                        is_open = item.output_type == "PENDING_DATA"
                    if not is_open:
                        continue
                output_key = f"{item.output_type}:{item.output_id or item.trigger_match_id}"
                row = grouped_rows.get(output_key)
                if row is None:
                    title = item.output_title
                    why = str(snapshot.get("why") or item.output_title)
                    priority = str(snapshot.get("priority") or "P1")
                    if item.output_type == "TEACHER_TASK" and (task := tasks.get(item.output_id)):
                        title = task.display_title or title
                        why = task.why
                        priority = task.priority
                    elif item.output_type == "OPS_CASE" and (case := cases.get(item.output_id)):
                        priority = case.priority
                    elif item.output_type == "NOTIFICATION" and (
                        notification := notifications.get(item.output_id)
                    ):
                        priority = notification.priority
                    row = {
                        "output_id": item.output_id or item.trigger_match_id,
                        "output_type": item.output_type,
                        "title": title,
                        "teacher_id": item.teacher_id,
                        "teacher_name": teachers.get(item.teacher_id, item.teacher_id),
                        "domain": item_domain,
                        "priority": priority,
                        "status": output_status,
                        "triggered_at": _iso(item.matched_at),
                        "why": why,
                        "evidence_summaries": [],
                        "source_lesson_ids": [],
                        "action_label": action_label,
                        "signal_count": 0,
                    }
                    grouped_rows[output_key] = row
                row["signal_count"] += 1
                summary = _evidence_summary(snapshot)
                if summary not in row["evidence_summaries"]:
                    row["evidence_summaries"].append(summary)
                if item.lesson_id and item.lesson_id not in row["source_lesson_ids"]:
                    row["source_lesson_ids"].append(item.lesson_id)
                matched_at = _iso(item.matched_at)
                if matched_at and (not row["triggered_at"] or matched_at < row["triggered_at"]):
                    row["triggered_at"] = matched_at

            rows: list[dict[str, Any]] = []
            for row in grouped_rows.values():
                evidence_summaries = row.pop("evidence_summaries")
                lesson_ids = row["source_lesson_ids"]
                row["source_lesson_id"] = lesson_ids[0] if lesson_ids else None
                prefix = f"共 {row['signal_count']} 次命中；" if row["signal_count"] > 1 else ""
                row["evidence_summary"] = prefix + "；".join(evidence_summaries[:3])
                rows.append(row)
            rows.sort(
                key=lambda row: (
                    # 严重投诉需要运营本人介入。即使来源处罚表是 P1，
                    # 也必须排在普通教师改善任务和课中提醒之前。
                    0 if row["output_type"] == "OPS_CASE" else 1,
                    # 数据缺口用于内部治理，不应挤占一线处置队列顶部。
                    1 if row["output_type"] == "PENDING_DATA" else 0,
                    {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(row["priority"], 9),
                    row["triggered_at"] or "",
                )
            )
            total = len(rows)
            start = (page - 1) * page_size
            return {
                "items": rows[start : start + page_size],
                "total": total,
                "page": page,
                "page_size": page_size,
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

    def lessons(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        teacher_id: str | None = None,
        lesson_id: str | None = None,
        risk_only: bool = False,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            statement = select(LessonFactRecord).where(
                LessonFactRecord.source_batch_id.is_not(None)
            )
            if teacher_id:
                statement = statement.where(LessonFactRecord.teacher_id == teacher_id)
            if lesson_id:
                statement = statement.where(LessonFactRecord.lesson_id == lesson_id)
            if risk_only:
                statement = statement.where(
                    LessonFactRecord.lesson_id.in_(
                        select(PersonalizedTriggerMatchRecord.lesson_id).where(
                            PersonalizedTriggerMatchRecord.lesson_id.is_not(None)
                        )
                    )
                )
            count_statement = select(func.count()).select_from(statement.subquery())
            total = int(session.scalar(count_statement) or 0)
            records = session.scalars(
                statement.order_by(
                    LessonFactRecord.lesson_local_date.desc(),
                    LessonFactRecord.lesson_local_time.desc(),
                    LessonFactRecord.lesson_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            lesson_ids = [item.lesson_id for item in records]
            matches_by_lesson: dict[str, list[PersonalizedTriggerMatchRecord]] = defaultdict(list)
            if lesson_ids:
                for match in session.scalars(
                    select(PersonalizedTriggerMatchRecord).where(
                        PersonalizedTriggerMatchRecord.lesson_id.in_(lesson_ids)
                    )
                ).all():
                    if match.lesson_id:
                        matches_by_lesson[match.lesson_id].append(match)
            teacher_ids = {item.teacher_id for item in records}
            teachers = {
                item.teacher_id: item.name
                for item in session.scalars(
                    select(TeacherRecord).where(TeacherRecord.teacher_id.in_(teacher_ids))
                ).all()
            }
            items: list[dict[str, Any]] = []
            for lesson in records:
                matches = matches_by_lesson.get(lesson.lesson_id, [])
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
                        "lesson_id": lesson.lesson_id,
                        "teacher_id": lesson.teacher_id,
                        "teacher_name": teachers.get(lesson.teacher_id, lesson.teacher_id),
                        "lesson_date": lesson.lesson_local_date.isoformat() if lesson.lesson_local_date else None,
                        "lesson_time": lesson.lesson_local_time.strftime("%H:%M") if lesson.lesson_local_time else None,
                        "lesson_status": lesson.lesson_lifecycle_status,
                        "risk_domains": domains,
                        "signals": [item.output_title for item in matches],
                        "complaint_level": lesson.complaint_source_level,
                    }
                )
            return {"items": items, "total": total, "page": page, "page_size": page_size}
