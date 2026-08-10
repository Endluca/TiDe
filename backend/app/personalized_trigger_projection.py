"""Build and persist personalized outputs from normalized lesson signals.

This module consumes normalized current lesson signals and is independent from
the retired XLSX/``lesson_facts`` path. SourceWide Worker is the only runtime
projection caller; compatibility modules may import its pure helpers but cannot
persist a legacy lesson batch.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import (
    NotificationRecord,
    OpsCaseRecord,
    PersonalizedTriggerMatchRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from .personalized_rules import (
    ComplaintRule,
    TriggerDecision,
    evaluate_lesson,
    normalize_text,
)
from .teacher_copy import (
    personalized_task_title,
    require_english_teacher_copy,
    with_teacher_evidence,
)


TRIGGER_RULE_VERSION = "personalized_rules_20260724_v2"

PERSONALIZED_TEMPLATE_CODES = {
    "P-REL-MEMO",
    "P-REL-ATTENDANCE",
    "P-FB-NEGATIVE",
    "P-FB-COMPLAINT",
    "P-FB-BLACKLIST",
}


class LessonTriggerProjectionError(ValueError):
    """A normalized lesson cannot be evaluated or materialized safely."""


@dataclass(frozen=True)
class LessonTriggerRow:
    row_number: int
    raw_payload: dict[str, Any]
    lesson_id: str
    teacher_id: str
    student_id: str
    local_date: date
    local_time: time
    local_start_at: datetime
    lifecycle_status: str
    is_peak: bool | None
    is_late: bool | None
    is_early: bool | None
    is_false_early_leave: bool | None
    negative_score: float | None
    has_negative_tag: bool | None
    feedback_detail: str | None
    negative_tags: tuple[str, ...]
    absence_reason_detail: str | None
    complaint_l1: str | None
    complaint_l2: str | None
    complaint_l3: str | None
    is_blocked: bool | None
    is_favorited: bool | None
    has_positive_tag: bool | None
    is_rebooked: bool | None
    is_camera_off: bool | None
    is_cpu_usage_high: bool | None
    is_network_delay_high: bool | None


@dataclass(frozen=True)
class PersonalizedOutputSpec:
    rule_code: str
    domain: str
    output_type: str
    title: str
    priority: str
    why: str
    evidence: dict[str, Any]
    teacher_id: str
    lesson_id: str | None
    complaint_rule_id: str | None
    dedupe_key: str
    output_dedupe_key: str
    task_code: str | None = None


def stable_output_id(prefix: str, value: str, *, length: int = 32) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def chunks(values: Sequence[Any], size: int = 900) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def feedback_labels(value: Any) -> tuple[str, ...]:
    """Split the reviewed source's ASCII-comma label list, preserving order."""

    labels = [normalize_text(item) for item in str(value or "").split(",")]
    return tuple(dict.fromkeys(item for item in labels if item))


def published_template_map(session: Session) -> dict[str, TaskTemplateRecord]:
    templates = {
        item.template_id: item
        for item in session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.template_id.in_(
                    sorted(PERSONALIZED_TEMPLATE_CODES)
                ),
                TaskTemplateRecord.status == "PUBLISHED",
            )
        ).all()
    }
    missing = sorted(PERSONALIZED_TEMPLATE_CODES - set(templates))
    if missing:
        raise LessonTriggerProjectionError(
            "personalized task templates must be seeded and published first: "
            f"{missing!r}"
        )
    return templates


def _priority_min(values: Iterable[str]) -> str:
    return min(values, key=lambda item: int(item[1:]))


def _merge_lesson_decisions(
    row: LessonTriggerRow,
    decisions: Sequence[TriggerDecision],
    *,
    complaint_rule_id: str | None,
) -> list[PersonalizedOutputSpec]:
    grouped: dict[tuple[str, str], list[TriggerDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.output_type == "TEACHER_TASK":
            key = (decision.output_type, decision.task_code or decision.rule_code)
        elif (
            decision.output_type == "NOTIFICATION"
            and decision.domain == "CLASS_QUALITY"
        ):
            key = (decision.output_type, "CLASS_QUALITY")
        else:
            key = (decision.output_type, decision.rule_code)
        grouped[key].append(decision)

    result: list[PersonalizedOutputSpec] = []
    for (output_type, key), members in grouped.items():
        task_code = members[0].task_code
        if output_type == "TEACHER_TASK" and task_code:
            canonical_rule = {
                "P-REL-MEMO": "TR-REL-LESSON-MEMO",
                "P-REL-ATTENDANCE": "TR-REL-ATTENDANCE",
                "P-FB-COMPLAINT": "TR-FB-GENERAL-COMPLAINT",
            }.get(task_code, members[0].rule_code)
        elif output_type == "NOTIFICATION" and key == "CLASS_QUALITY":
            canonical_rule = "TR-QUALITY-LESSON"
        else:
            canonical_rule = members[0].rule_code

        signals = [
            {
                "rule_code": item.rule_code,
                "why": item.why,
                "evidence": item.evidence,
            }
            for item in members
        ]
        evidence: dict[str, Any] = {
            "lesson_id": row.lesson_id,
            "source_row_number": row.row_number,
            "matched_rule_codes": list(
                dict.fromkeys(item.rule_code for item in members)
            ),
            "signals": signals,
        }
        anomalies: list[str] = []
        for item in members:
            anomalies.extend(item.evidence.get("anomalies") or [])
            for field in (
                "complaint_level2",
                "complaint_level3",
                "source_level_code",
                "severity_rank",
                "absence_reason_detail",
                "is_late",
                "is_early",
                "is_fake_early",
            ):
                if field in item.evidence and field not in evidence:
                    evidence[field] = item.evidence[field]
        if anomalies:
            evidence["anomalies"] = list(dict.fromkeys(anomalies))
        why = " ".join(dict.fromkeys(item.why for item in members))
        dedupe_key = f"{canonical_rule}:{row.teacher_id}:{row.lesson_id}"
        if output_type == "TEACHER_TASK":
            if task_code == "P-FB-COMPLAINT":
                complaint_key = hashlib.sha256(
                    normalize_text(row.complaint_l3).encode("utf-8")
                ).hexdigest()[:16]
                output_dedupe_key = (
                    f"{canonical_rule}:{row.teacher_id}:complaint:{complaint_key}"
                )
            else:
                output_dedupe_key = f"{canonical_rule}:{row.teacher_id}"
        else:
            output_dedupe_key = dedupe_key
        result.append(
            PersonalizedOutputSpec(
                rule_code=canonical_rule,
                domain=members[0].domain,
                output_type=output_type,
                title=members[0].title,
                priority=_priority_min(item.priority for item in members),
                why=why,
                evidence=evidence,
                teacher_id=row.teacher_id,
                lesson_id=row.lesson_id,
                complaint_rule_id=complaint_rule_id,
                dedupe_key=dedupe_key,
                output_dedupe_key=output_dedupe_key,
                task_code=task_code,
            )
        )
    return result


def build_output_specs(
    lessons: Sequence[LessonTriggerRow],
    *,
    complaint_rules: Mapping[str, ComplaintRule],
    complaint_rule_ids: Mapping[str, str],
) -> tuple[list[PersonalizedOutputSpec], int, int, int]:
    result: list[PersonalizedOutputSpec] = []
    blacklists: dict[str, dict[str, LessonTriggerRow]] = defaultdict(dict)
    negative_tags: dict[str, dict[str, list[LessonTriggerRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    negative_rows_without_labels: dict[str, list[LessonTriggerRow]] = defaultdict(
        list
    )
    unmatched_complaints = 0

    for row in lessons:
        complaint_rule_id = complaint_rule_ids.get(normalize_text(row.complaint_l3))
        decisions = evaluate_lesson(
            row.raw_payload,
            complaint_rules=complaint_rules,
        )
        unmatched_complaints += sum(
            item.output_type == "PENDING_DATA" for item in decisions
        )
        result.extend(
            _merge_lesson_decisions(
                row,
                decisions,
                complaint_rule_id=complaint_rule_id,
            )
        )
        if row.is_blocked:
            blacklists[row.teacher_id].setdefault(row.student_id, row)
        if row.has_negative_tag:
            if row.negative_tags:
                for label in row.negative_tags:
                    negative_tags[row.teacher_id][label].append(row)
            else:
                negative_rows_without_labels[row.teacher_id].append(row)

    blacklist_count = 0
    for teacher_id, student_rows in blacklists.items():
        if len(student_rows) < 2:
            continue
        blacklist_count += 1
        ordered = sorted(
            student_rows.values(),
            key=lambda item: (item.local_start_at, item.row_number),
        )
        threshold_row = ordered[1]
        result.append(
            PersonalizedOutputSpec(
                rule_code="TR-FB-BLACKLIST",
                domain="USER_FEEDBACK",
                output_type="TEACHER_TASK",
                title=personalized_task_title("P-FB-BLACKLIST"),
                priority="P1",
                why=(
                    f"{len(student_rows)} different students blacklisted this teacher. "
                    "Complete the assigned blacklist-prevention learning activity."
                ),
                evidence={
                    "distinct_student_count": len(student_rows),
                    "threshold": 2,
                    "threshold_crossing_lesson_id": threshold_row.lesson_id,
                    "lesson_ids": [item.lesson_id for item in ordered],
                },
                teacher_id=teacher_id,
                lesson_id=threshold_row.lesson_id,
                complaint_rule_id=None,
                dedupe_key=f"TR-FB-BLACKLIST:{teacher_id}",
                output_dedupe_key=f"TR-FB-BLACKLIST:{teacher_id}",
                task_code="P-FB-BLACKLIST",
            )
        )

    for teacher_id, labels in negative_tags.items():
        for label, rows in labels.items():
            if len(rows) <= 1:
                continue
            ordered = sorted(
                rows,
                key=lambda item: (item.local_start_at, item.row_number),
            )
            threshold_row = ordered[1]
            label_key = hashlib.sha256(
                normalize_text(label).encode("utf-8")
            ).hexdigest()[:16]
            result.append(
                PersonalizedOutputSpec(
                    rule_code="TR-FB-NEGATIVE-REPEAT",
                    domain="USER_FEEDBACK",
                    output_type="TEACHER_TASK",
                    title=personalized_task_title("P-FB-NEGATIVE", label),
                    priority="P1",
                    why=(
                        "The same negative-feedback tag appeared in "
                        f"{len(rows)} different lessons for this teacher. "
                        "Complete the corresponding learning activity."
                    ),
                    evidence={
                        "negative_feedback_label": label,
                        "negative_review_lesson_count": len(rows),
                        "aggregate_hit_count": len(rows),
                        "threshold": 2,
                        "threshold_crossing_lesson_id": threshold_row.lesson_id,
                        "lesson_ids": [item.lesson_id for item in ordered],
                    },
                    teacher_id=teacher_id,
                    lesson_id=threshold_row.lesson_id,
                    complaint_rule_id=None,
                    dedupe_key=f"TR-FB-NEGATIVE-REPEAT:{teacher_id}:{label_key}",
                    output_dedupe_key=(
                        f"TR-FB-NEGATIVE-REPEAT:{teacher_id}:{label_key}"
                    ),
                    task_code="P-FB-NEGATIVE",
                )
            )

    negative_pending_count = 0
    for teacher_id, rows in negative_rows_without_labels.items():
        if len(rows) <= 1:
            continue
        negative_pending_count += 1
        ordered = sorted(
            rows,
            key=lambda item: (item.local_start_at, item.row_number),
        )
        threshold_row = ordered[1]
        result.append(
            PersonalizedOutputSpec(
                rule_code="TR-FB-NEGATIVE-TAG-MISSING",
                domain="USER_FEEDBACK",
                output_type="PENDING_DATA",
                title="差评任务待补标签名称",
                priority="P1",
                why=(
                    "Negative feedback was recorded repeatedly, but the feedback detail "
                    "did not contain a usable tag. No teacher task was created."
                ),
                evidence={
                    "negative_tag_flag_count": len(rows),
                    "threshold": 2,
                    "missing_field": "评价详情.评价标签",
                    "lesson_ids": [item.lesson_id for item in ordered],
                },
                teacher_id=teacher_id,
                lesson_id=threshold_row.lesson_id,
                complaint_rule_id=None,
                dedupe_key=f"TR-FB-NEGATIVE-TAG-MISSING:{teacher_id}",
                output_dedupe_key=f"TR-FB-NEGATIVE-TAG-MISSING:{teacher_id}",
                task_code=None,
            )
        )
    return result, blacklist_count, negative_pending_count, unmatched_complaints


def materialize_outputs(
    session: Session,
    *,
    specs: Sequence[PersonalizedOutputSpec],
    templates: Mapping[str, TaskTemplateRecord],
    teachers: Mapping[str, TeacherRecord],
    materialized_at: datetime,
    reconcile_existing: bool = False,
    reconcile_teacher_ids: Sequence[str] | None = None,
    source_mode: str = "DERIVED_REAL",
    evidence_context: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    counts = Counter(
        task_assignments_created=0,
        ops_cases_created=0,
        notifications_created=0,
        pending_data_matches_created=0,
        trigger_matches_created=0,
    )
    desired_match_keys = sorted({item.dedupe_key for item in specs})
    normalized_reconcile_teacher_ids = sorted(
        {str(item) for item in (reconcile_teacher_ids or []) if str(item)}
    )
    if normalized_reconcile_teacher_ids and not reconcile_existing:
        raise ValueError("reconcile_teacher_ids requires reconcile_existing=True")
    existing_matches = {
        item.dedupe_key: item
        for chunk in (
            chunks(desired_match_keys)
            if not reconcile_existing
            else [desired_match_keys]
        )
        for item in session.scalars(
            (
                select(PersonalizedTriggerMatchRecord).where(
                    PersonalizedTriggerMatchRecord.dedupe_key.in_(chunk)
                )
                if not reconcile_existing
                else select(PersonalizedTriggerMatchRecord).where(
                    PersonalizedTriggerMatchRecord.teacher_id.in_(
                        normalized_reconcile_teacher_ids
                    )
                )
                if normalized_reconcile_teacher_ids
                else select(PersonalizedTriggerMatchRecord)
            )
        ).all()
    }
    task_specs = [item for item in specs if item.output_type == "TEACHER_TASK"]
    task_groups: dict[str, list[PersonalizedOutputSpec]] = defaultdict(list)
    for item in task_specs:
        task_groups[item.output_dedupe_key].append(item)
    existing_tasks = {
        item.dedupe_key: item
        for chunk in chunks(sorted(task_groups))
        for item in session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.dedupe_key.in_(chunk)
            )
        ).all()
    }
    desired_case_ids = sorted(
        {
            stable_output_id("CASE", item.output_dedupe_key)
            for item in specs
            if item.output_type == "OPS_CASE"
        }
    )
    existing_cases = {
        item.case_id: item
        for chunk in chunks(desired_case_ids)
        for item in session.scalars(
            select(OpsCaseRecord).where(OpsCaseRecord.case_id.in_(chunk))
        ).all()
    }
    desired_notification_ids = sorted(
        {
            stable_output_id("NOTIF", item.output_dedupe_key)
            for item in specs
            if item.output_type == "NOTIFICATION"
        }
    )
    existing_notifications = {
        item.notification_id: item
        for chunk in chunks(desired_notification_ids)
        for item in session.scalars(
            select(NotificationRecord).where(
                NotificationRecord.notification_id.in_(chunk)
            )
        ).all()
    }

    task_outputs: dict[str, TaskAssignmentRecord] = {}
    for output_dedupe_key, members in task_groups.items():
        assignment = existing_tasks.get(output_dedupe_key)
        first = members[0]
        if not first.task_code:
            raise LessonTriggerProjectionError(
                f"{first.rule_code}: teacher task has no task_code"
            )
        if any(
            item.task_code != first.task_code or item.title != first.title
            for item in members
        ):
            raise LessonTriggerProjectionError(
                f"task aggregation conflict for {output_dedupe_key}"
            )
        lesson_ids = list(
            dict.fromkeys(
                lesson_id
                for item in members
                for lesson_id in (
                    item.evidence.get("lesson_ids")
                    if isinstance(item.evidence.get("lesson_ids"), list)
                    else [item.lesson_id]
                )
                if lesson_id
            )
        )
        hit_count = sum(
            int(item.evidence.get("aggregate_hit_count") or 1)
            for item in members
        )
        evidence = {
            "hit_count": hit_count,
            "lesson_ids": lesson_ids,
            "matched_rule_codes": list(
                dict.fromkeys(item.rule_code for item in members)
            ),
            "signal_samples": [
                {
                    "lesson_id": item.lesson_id,
                    "why": item.why,
                    "evidence": item.evidence,
                }
                for item in members[:20]
            ],
            "sample_limit": 20,
            "evidence_is_complete_for_lesson_ids": True,
            "source_mode": source_mode,
            **dict(evidence_context or {}),
        }
        lesson_sample = ", ".join(lesson_ids[:10])
        sample_suffix = ", ..." if len(lesson_ids) > 10 else ""
        lesson_count = len(lesson_ids)
        why = with_teacher_evidence(
            require_english_teacher_copy(
                (
                    f"This task was triggered by {lesson_count} lesson record(s) "
                    f"(Lesson IDs: {lesson_sample}{sample_suffix}). {first.why}"
                ),
                field_name="task_assignments.why",
            ),
            evidence,
        )
        if assignment is None:
            template = templates[first.task_code]
            template_payload = (
                template.payload if isinstance(template.payload, dict) else {}
            )
            display_title = require_english_teacher_copy(
                first.title,
                field_name="task_assignments.display_title",
            )
            due_hours = int((template_payload.get("due_rule") or {}).get("hours") or 72)
            output_id = stable_output_id("TASK", output_dedupe_key)
            teacher = teachers[first.teacher_id]
            assignment = TaskAssignmentRecord(
                assignment_id=output_id,
                teacher_id=first.teacher_id,
                task_code=first.task_code,
                template_version_id=template.row_id,
                task_kind="PERSONALIZED_IMPROVEMENT",
                creator_system="TRIGGER_CENTER",
                status="ASSIGNED",
                priority=_priority_min(item.priority for item in members),
                why=why,
                display_title=display_title,
                evidence_snapshot=evidence,
                due_at=materialized_at + timedelta(hours=due_hours),
                timezone_used=teacher.timezone,
                timezone_source="TEACHER_PROFILE",
                timezone_verified_at=materialized_at,
                status_reason_code=None,
                source_mode=source_mode,
                dedupe_key=output_dedupe_key,
                created_by="TRIGGER_CENTER",
                updated_by="TRIGGER_CENTER",
                row_version=1,
                assigned_at=materialized_at,
                status_changed_at=materialized_at,
                completed_at=None,
                created_at=materialized_at,
                updated_at=materialized_at,
            )
            session.add(assignment)
            existing_tasks[output_dedupe_key] = assignment
            counts["task_assignments_created"] += 1
        task_outputs[output_dedupe_key] = assignment

    for spec in specs:
        output_id: str | None = None
        teacher_facing_reason = (
            with_teacher_evidence(spec.why, spec.evidence)
            if spec.output_type in {"TEACHER_TASK", "NOTIFICATION"}
            else spec.why
        )
        match_status = (
            "PENDING_DATA" if spec.output_type == "PENDING_DATA" else "MATERIALIZED"
        )
        if spec.output_type == "TEACHER_TASK":
            assignment = task_outputs[spec.output_dedupe_key]
            output_id = assignment.assignment_id
        elif spec.output_type == "OPS_CASE":
            output_id = stable_output_id("CASE", spec.output_dedupe_key)
            ops_case = existing_cases.get(output_id)
            if ops_case is None:
                ops_case = OpsCaseRecord(
                    case_id=output_id,
                    case_type="SEVERE_COMPLAINT",
                    teacher_id=spec.teacher_id,
                    task_id=None,
                    priority=spec.priority,
                    status="OPEN",
                    source_reason=spec.rule_code,
                    external_action_status="OPS_REVIEW_REQUIRED",
                    created_at=materialized_at,
                    payload={
                        "title": spec.title,
                        "summary": spec.why,
                        "recommended_action": "核实投诉事实并按现行处罚规则处理。",
                        "evidence": spec.evidence,
                        "source_mode": source_mode,
                        **dict(evidence_context or {}),
                        "trigger_rule_version": TRIGGER_RULE_VERSION,
                    },
                    updated_at=materialized_at,
                )
                session.add(ops_case)
                existing_cases[output_id] = ops_case
                counts["ops_cases_created"] += 1
        elif spec.output_type == "NOTIFICATION":
            output_id = stable_output_id("NOTIF", spec.output_dedupe_key)
            notification_title = require_english_teacher_copy(
                spec.title,
                field_name="notifications.payload.title",
            )
            notification_body = require_english_teacher_copy(
                teacher_facing_reason,
                field_name="notifications.payload.body",
            )
            if "Evidence:" not in notification_body:
                raise ValueError("notifications.payload.body must contain Evidence:")
            notification_payload = {
                "title": notification_title,
                "body": notification_body,
                "evidence": spec.evidence,
                "source_mode": source_mode,
                **dict(evidence_context or {}),
                "trigger_rule_version": TRIGGER_RULE_VERSION,
            }
            notification = existing_notifications.get(output_id)
            if notification is None:
                notification = NotificationRecord(
                    notification_id=output_id,
                    task_id=None,
                    source_ref=spec.output_dedupe_key,
                    teacher_id=spec.teacher_id,
                    channel="WEBAPP_INBOX",
                    priority=spec.priority,
                    status="STORED",
                    requested_at=materialized_at,
                    stored_at=materialized_at,
                    read_at=None,
                    clicked_at=None,
                    response_due_at=None,
                    failure_reason=None,
                    payload=notification_payload,
                )
                session.add(notification)
                existing_notifications[output_id] = notification
                counts["notifications_created"] += 1
            else:
                notification.payload = notification_payload
        elif spec.output_type == "PENDING_DATA":
            if spec.dedupe_key not in existing_matches:
                counts["pending_data_matches_created"] += 1
        else:
            raise LessonTriggerProjectionError(
                f"unsupported personalized output type: {spec.output_type}"
            )

        existing_match = existing_matches.get(spec.dedupe_key)
        match_values = {
            "trigger_code": spec.rule_code,
            "rule_version": TRIGGER_RULE_VERSION,
            "teacher_id": spec.teacher_id,
            "lesson_id": spec.lesson_id,
            "complaint_rule_id": spec.complaint_rule_id,
            "output_type": spec.output_type,
            "output_title": spec.title,
            "output_id": output_id,
            "match_status": match_status,
            "evidence_snapshot": {
                **spec.evidence,
                "why": teacher_facing_reason,
                "source_mode": source_mode,
                **dict(evidence_context or {}),
            },
            "materialized_at": (
                materialized_at if match_status == "MATERIALIZED" else None
            ),
            "updated_at": materialized_at,
        }
        if existing_match is not None:
            for field, value in match_values.items():
                setattr(existing_match, field, value)
        else:
            session.add(
                PersonalizedTriggerMatchRecord(
                    trigger_match_id=stable_output_id("TM", spec.dedupe_key),
                    dedupe_key=spec.dedupe_key,
                    **match_values,
                    matched_at=materialized_at,
                )
            )
            counts["trigger_matches_created"] += 1

    if reconcile_existing:
        desired_match_keys_set = {item.dedupe_key for item in specs}
        for dedupe_key, match in existing_matches.items():
            if dedupe_key in desired_match_keys_set:
                continue
            match.match_status = "SUPPRESSED"
            match.evidence_snapshot = {
                **(
                    match.evidence_snapshot
                    if isinstance(match.evidence_snapshot, dict)
                    else {}
                ),
                "superseded_by_rule_version": TRIGGER_RULE_VERSION,
                "superseded_at": materialized_at.isoformat(),
            }
            match.updated_at = materialized_at

        session.flush()
        active_match_query = select(
            PersonalizedTriggerMatchRecord.output_id
        ).where(
            PersonalizedTriggerMatchRecord.match_status != "SUPPRESSED",
            PersonalizedTriggerMatchRecord.output_id.is_not(None),
        )
        if normalized_reconcile_teacher_ids:
            active_match_query = active_match_query.where(
                PersonalizedTriggerMatchRecord.teacher_id.in_(
                    normalized_reconcile_teacher_ids
                )
            )
        active_output_ids = set(session.scalars(active_match_query).all())
        assignment_query = select(TaskAssignmentRecord).where(
            TaskAssignmentRecord.task_kind == "PERSONALIZED_IMPROVEMENT",
            TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
        )
        if normalized_reconcile_teacher_ids:
            assignment_query = assignment_query.where(
                TaskAssignmentRecord.teacher_id.in_(
                    normalized_reconcile_teacher_ids
                )
            )
        for assignment in session.scalars(assignment_query).all():
            if (
                assignment.assignment_id not in active_output_ids
                and assignment.status == "ASSIGNED"
            ):
                assignment.status = "CANCELLED"
                assignment.status_reason_code = "SOURCE_EVIDENCE_SUPERSEDED"
                assignment.status_changed_at = materialized_at
                assignment.updated_by = "TRIGGER_CENTER_BASELINE_REPLACEMENT"
        notification_query = select(NotificationRecord)
        if normalized_reconcile_teacher_ids:
            notification_query = notification_query.where(
                NotificationRecord.teacher_id.in_(normalized_reconcile_teacher_ids)
            )
        for notification in session.scalars(notification_query).all():
            if (
                notification.notification_id not in active_output_ids
                and notification.status == "STORED"
            ):
                notification.status = "CANCELLED"
        case_query = select(OpsCaseRecord).where(
            OpsCaseRecord.source_reason.is_not(None)
        )
        if normalized_reconcile_teacher_ids:
            case_query = case_query.where(
                OpsCaseRecord.teacher_id.in_(normalized_reconcile_teacher_ids)
            )
        for case in session.scalars(case_query).all():
            if case.case_id not in active_output_ids and case.status == "OPEN":
                case.status = "CANCELLED"
                case.external_action_status = "SOURCE_EVIDENCE_SUPERSEDED"
                case.updated_at = materialized_at
    return dict(counts)
