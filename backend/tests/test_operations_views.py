from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from app.database import engine, session_scope
from app.db_models import (
    ComplaintCategoryRuleRecord,
    ComplaintRuleImportRecord,
    LessonSourceWideRecord,
    NotificationRecord,
    OpsCaseRecord,
    PersonalizedTriggerMatchRecord,
    TaskAssignmentRecord,
)
from app.main import app
from app.operations_service import OperationsService


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_source_lessons() -> None:
    with session_scope(engine) as session:
        session.execute(delete(LessonSourceWideRecord))
    yield
    with session_scope(engine) as session:
        session.execute(delete(LessonSourceWideRecord))


def _seed_operations_evidence() -> None:
    now = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    source_sha256 = "a" * 64
    lesson_id = "LESSON-REAL-1"
    teacher_id = "T-1001"
    task_id = "TASK-PERSONALIZED-1"
    case_id = "CASE-SEVERE-1"
    with session_scope(engine) as session:
        session.add(
            ComplaintRuleImportRecord(
                source_sha256=source_sha256,
                source_filename="lesson.xlsx",
                raw_rows=[
                    {
                        "source_row_number": 2,
                        "一级分类": "关于老师",
                        "二级分类": "测试问题",
                        "三级分类": "测试",
                        "P级": "P0",
                        "Course Title in the Learning Hub": None,
                        "link": None,
                    }
                ],
                imported_at=now,
            )
        )
        session.add_all(
            [
                LessonSourceWideRecord(
                    source_region="ovs",
                    course_id=lesson_id,
                    teacher_id=teacher_id,
                    lesson_status="end",
                    lesson_date=date(2026, 5, 1),
                    lesson_time=time(10, 0),
                    is_late=True,
                    complaint_category_l3="测试",
                ),
                ComplaintCategoryRuleRecord(
                    rule_id="COMPLAINT-RULE-OPERATIONS-1",
                    source_sha256=source_sha256,
                    source_row_number=2,
                    category_l1="关于老师",
                    category_l2="测试问题",
                    category_l3="测试",
                    category_l3_normalized="测试",
                    source_level="P0",
                    severity_rank=0,
                    default_route="OPS_CASE",
                    created_at=now,
                ),
            ]
        )
        session.add(
            TaskAssignmentRecord(
                assignment_id=task_id,
                teacher_id=teacher_id,
                task_code="P-REL-ATTENDANCE",
                template_version_id="P-REL-ATTENDANCE:v1",
                task_kind="PERSONALIZED_IMPROVEMENT",
                creator_system="TRIGGER_CENTER",
                status="ASSIGNED",
                priority="P1",
                why=(
                    "This lesson recorded a late arrival. "
                    "Complete the attendance training and quiz. "
                    f"Evidence: Lesson IDs: LESSON-{teacher_id}; "
                    "late arrival recorded."
                ),
                display_title="Attendance Improvement",
                evidence_snapshot={"lesson_id": lesson_id, "is_late": True},
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=None,
                source_mode="REAL",
                dedupe_key=f"personalized:{teacher_id}:{lesson_id}:attendance",
                created_by="TRIGGER_CENTER",
                updated_by="TRIGGER_CENTER",
                row_version=1,
                assigned_at=now,
                status_changed_at=now,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            OpsCaseRecord(
                case_id=case_id,
                case_type="SEVERE_COMPLAINT",
                teacher_id=teacher_id,
                task_id=None,
                priority="P0",
                status="ACTION_PENDING",
                source_reason="TR-FB-SEVERE-COMPLAINT",
                external_action_status="NOT_REQUIRED",
                created_at=now,
                payload={"title": "严重投诉-测试问题", "lesson_id": lesson_id},
            )
        )
        session.add_all(
            [
                PersonalizedTriggerMatchRecord(
                    trigger_match_id="MATCH-TASK-1",
                    trigger_code="TR-REL-ATTENDANCE",
                    rule_version="2026-07-22",
                    teacher_id=teacher_id,
                    lesson_source_region="ovs",
                    lesson_id=lesson_id,
                    complaint_rule_id=None,
                    dedupe_key="match:attendance:1",
                    output_type="TEACHER_TASK",
                    output_title="出席问题",
                    output_id=task_id,
                    match_status="MATERIALIZED",
                    evidence_snapshot={
                        "domain": "RELIABILITY",
                        "priority": "P1",
                        "why": "该课程迟到，请完成出席培训。",
                        "evidence": {"lesson_id": lesson_id, "is_late": True},
                    },
                    matched_at=now,
                    materialized_at=now,
                ),
                PersonalizedTriggerMatchRecord(
                    trigger_match_id="MATCH-CASE-1",
                    trigger_code="TR-FB-SEVERE-COMPLAINT",
                    rule_version="2026-07-22",
                    teacher_id=teacher_id,
                    lesson_source_region="ovs",
                    lesson_id=lesson_id,
                    complaint_rule_id=None,
                    dedupe_key="match:case:1",
                    output_type="OPS_CASE",
                    output_title="严重投诉-测试问题",
                    output_id=case_id,
                    match_status="MATERIALIZED",
                    evidence_snapshot={
                        "domain": "USER_FEEDBACK",
                        "priority": "P0",
                        "why": "P0 投诉需运营处理。",
                        "evidence": {
                            "lesson_id": lesson_id,
                            "complaint_level3": "测试",
                            "source_level_code": "P0",
                        },
                    },
                    matched_at=now,
                    materialized_at=now,
                ),
                PersonalizedTriggerMatchRecord(
                    trigger_match_id="MATCH-PENDING-1",
                    trigger_code="TR-FB-NEGATIVE-LABEL-MISSING",
                    rule_version="2026-07-22",
                    teacher_id=teacher_id,
                    lesson_id=None,
                    complaint_rule_id=None,
                    dedupe_key="match:pending:1",
                    output_type="PENDING_DATA",
                    output_title="差评标签名称待补齐",
                    output_id=None,
                    match_status="PENDING_DATA",
                    evidence_snapshot={
                        "domain": "USER_FEEDBACK",
                        "priority": "P1",
                        "why": "源字段只有 0/1，未发布任务。",
                        "evidence": {"data_issue": "NEGATIVE_LABEL_NAME_MISSING"},
                    },
                    matched_at=now,
                    materialized_at=None,
                ),
            ]
        )


def test_severe_complaint_case_precedes_same_priority_teacher_task() -> None:
    _seed_operations_evidence()

    # P1 投诉仍属于需要运营直接介入的严重投诉，不能被普通 P1 改善任务淹没。
    with session_scope(engine) as session:
        case = session.get(OpsCaseRecord, "CASE-SEVERE-1")
        assert case is not None
        case.priority = "P1"

    response = client.get("/api/operations/interventions?page=1&page_size=3")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["output_type"] == "OPS_CASE"
    assert items[0]["title"] == "严重投诉-测试问题"


def test_operations_overview_and_intervention_drilldown() -> None:
    _seed_operations_evidence()

    overview = client.get("/api/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["lesson_total"] == 1
    assert overview.json()["teacher_total"] == 1
    assert overview.json()["affected_teacher_total"] == 1
    assert overview.json()["open_personalized_tasks"] == 1
    assert overview.json()["severe_complaint_cases"] == 1
    assert overview.json()["current_ops_todo_count"] == 1
    assert overview.json()["pending_data_issues"] == 1

    interventions = client.get("/api/operations/interventions?domain=RELIABILITY")
    assert interventions.status_code == 200
    assert interventions.json()["total"] == 1
    assert interventions.json()["counts_by_type"] == {"TEACHER_TASK": 1}
    item = interventions.json()["items"][0]
    assert item["title"] == "Attendance Improvement"
    assert item["source_lesson_id"] == "LESSON-REAL-1"
    assert item["status"] == "ASSIGNED"

    combined_outputs = client.get(
        "/api/operations/interventions",
        params={
            "type": "NOTIFICATION,OPS_CASE,PENDING_DATA",
            "page": 1,
            "page_size": 2,
        },
    )
    assert combined_outputs.status_code == 200
    assert combined_outputs.json()["total"] == 2
    assert combined_outputs.json()["counts_by_type"] == {
        "OPS_CASE": 1,
        "PENDING_DATA": 1,
    }
    assert len(combined_outputs.json()["items"]) == 2


def test_operations_reads_keep_query_count_bounded_by_page_not_match_count() -> None:
    _seed_operations_evidence()
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        OperationsService(engine).overview()
        overview_query_count = len(statements)
        statements.clear()
        result = OperationsService(engine).interventions(
            page=1,
            page_size=2,
        )
        intervention_query_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert overview_query_count == 2
    assert intervention_query_count == 3
    assert len(result["items"]) == 2
    assert result["total"] == 3


def test_intervention_evidence_is_bounded_per_output() -> None:
    _seed_operations_evidence()
    now = datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add_all(
            [
                PersonalizedTriggerMatchRecord(
                    trigger_match_id=f"MATCH-TASK-SAMPLE-{index:02d}",
                    trigger_code="TR-REL-ATTENDANCE",
                    rule_version="2026-07-22",
                    teacher_id="T-1001",
                    lesson_source_region="ovs",
                    lesson_id="LESSON-REAL-1",
                    complaint_rule_id=None,
                    dedupe_key=f"match:attendance:sample:{index}",
                    output_type="TEACHER_TASK",
                    output_title="Attendance Improvement",
                    output_id="TASK-PERSONALIZED-1",
                    match_status="MATERIALIZED",
                    evidence_snapshot={
                        "domain": "RELIABILITY",
                        "priority": "P1",
                        "why": "Repeated attendance signal.",
                        "evidence": {
                            "lesson_id": "LESSON-REAL-1",
                            "sample": index,
                        },
                    },
                    matched_at=now + timedelta(seconds=index),
                    materialized_at=now,
                )
                for index in range(25)
            ]
        )

    response = client.get(
        "/api/operations/interventions?domain=RELIABILITY"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["signal_count"] == 26
    assert item["evidence_sampled"] is True


def test_notification_copy_is_read_directly_from_database() -> None:
    _seed_operations_evidence()
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    notification_id = "NOTIFICATION-DIRECT-DATABASE-COPY"
    stored_title = "In-Class Quality Alert"
    stored_body = (
        "This lesson had an in-class quality issue: high network delay was "
        "detected. Check and improve the class setup. "
        "Evidence: Lesson IDs: LESSON-REAL-1; "
        "quality anomalies: high network delay."
    )
    with session_scope(engine) as session:
        session.add(
            NotificationRecord(
                notification_id=notification_id,
                task_id=None,
                source_ref="legacy-quality-alert:LESSON-REAL-1",
                teacher_id="T-1001",
                channel="WEBAPP_INBOX",
                priority="P1",
                status="STORED",
                requested_at=now,
                stored_at=now,
                read_at=None,
                clicked_at=None,
                response_due_at=None,
                failure_reason=None,
                payload={
                    "title": stored_title,
                    "body": stored_body,
                    "evidence": {
                        "lesson_id": "LESSON-REAL-1",
                        "anomalies": ["网络延迟过高"],
                    },
                },
            )
        )
        session.add(
            PersonalizedTriggerMatchRecord(
                trigger_match_id="MATCH-NOTIFICATION-LEGACY",
                trigger_code="TR-QUALITY-IN-CLASS",
                rule_version="2026-07-22",
                teacher_id="T-1001",
                lesson_source_region="ovs",
                lesson_id="LESSON-REAL-1",
                complaint_rule_id=None,
                dedupe_key="match:notification:legacy",
                output_type="NOTIFICATION",
                output_title="课中质量问题",
                output_id=notification_id,
                match_status="MATERIALIZED",
                evidence_snapshot={
                    "domain": "CLASS_QUALITY",
                    "priority": "P1",
                    "why": "该课程检测到课堂质量问题。",
                    "evidence": {
                        "lesson_id": "LESSON-REAL-1",
                        "anomalies": ["网络延迟过高"],
                    },
                },
                matched_at=now,
                materialized_at=now,
            )
        )

    response = client.get(
        "/api/operations/interventions?type=NOTIFICATION&page=1&page_size=10"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["title"] == stored_title
    assert item["why"] == stored_body


def test_missing_materialized_output_keeps_trigger_copy_and_is_not_open() -> None:
    _seed_operations_evidence()
    now = datetime(2026, 7, 22, 12, 30, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            PersonalizedTriggerMatchRecord(
                trigger_match_id="MATCH-MISSING-TASK",
                trigger_code="TR-REL-MISSING-TASK",
                rule_version="2026-07-22",
                teacher_id="T-1001",
                lesson_source_region="ovs",
                lesson_id="LESSON-REAL-1",
                complaint_rule_id=None,
                dedupe_key="match:missing-task",
                output_type="TEACHER_TASK",
                output_title="Unmaterialized reliability task",
                output_id="TASK-DOES-NOT-EXIST",
                match_status="FAILED",
                evidence_snapshot={
                    "domain": "RELIABILITY",
                    "priority": "P2",
                    "why": "Materialization failed after the trigger matched.",
                },
                matched_at=now,
                materialized_at=None,
            )
        )

    response = client.get(
        "/api/operations/interventions",
        params={"status": "OUTPUT_MISSING"},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Unmaterialized reliability task"
    assert item["why"] == "Materialization failed after the trigger matched."
    assert item["priority"] == "P2"

    open_response = client.get(
        "/api/operations/interventions",
        params={
            "status": "OUTPUT_MISSING",
            "open_only": "true",
        },
    )
    assert open_response.status_code == 200
    assert open_response.json()["items"] == []
    assert open_response.json()["total"] == 0


def test_current_ops_todo_excludes_terminal_case_statuses() -> None:
    _seed_operations_evidence()
    now = datetime(2026, 7, 22, 11, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            OpsCaseRecord(
                case_id="CASE-OTHER-OPEN",
                case_type="QUALITY_REVIEW",
                teacher_id="T-1001",
                task_id=None,
                priority="P1",
                status="IN_REVIEW",
                source_reason="TR-QUALITY-OTHER",
                external_action_status="NOT_REQUIRED",
                created_at=now,
                payload={"title": "其他类型待办"},
            )
        )
        session.add(
            PersonalizedTriggerMatchRecord(
                trigger_match_id="MATCH-OTHER-OPEN",
                trigger_code="TR-QUALITY-OTHER",
                rule_version="2026-07-22",
                teacher_id="T-1001",
                lesson_source_region="ovs",
                lesson_id="LESSON-REAL-1",
                complaint_rule_id=None,
                dedupe_key="match:other-open",
                output_type="OPS_CASE",
                output_title="其他类型待办",
                output_id="CASE-OTHER-OPEN",
                match_status="MATERIALIZED",
                evidence_snapshot={
                    "domain": "CLASS_QUALITY",
                    "priority": "P1",
                    "why": "用于验证当前待办不限严重投诉类型。",
                },
                matched_at=now,
                materialized_at=now,
            )
        )
        for index, status in enumerate(("RESOLVED", "CLOSED", "CANCELLED"), start=1):
            case_id = f"CASE-TERMINAL-{index}"
            session.add(
                OpsCaseRecord(
                    case_id=case_id,
                    case_type="SEVERE_COMPLAINT",
                    teacher_id="T-1001",
                    task_id=None,
                    priority="P0",
                    status=status,
                    source_reason="TR-FB-SEVERE-COMPLAINT",
                    external_action_status="NOT_REQUIRED",
                    created_at=now,
                    payload={"title": f"已结束运营事项 {index}"},
                )
            )
            session.add(
                PersonalizedTriggerMatchRecord(
                    trigger_match_id=f"MATCH-TERMINAL-{index}",
                    trigger_code="TR-FB-SEVERE-COMPLAINT",
                    rule_version="2026-07-22",
                    teacher_id="T-1001",
                    lesson_source_region="ovs",
                    lesson_id="LESSON-REAL-1",
                    complaint_rule_id=None,
                    dedupe_key=f"match:terminal:{index}",
                    output_type="OPS_CASE",
                    output_title=f"已结束运营事项 {index}",
                    output_id=case_id,
                    match_status="MATERIALIZED",
                    evidence_snapshot={
                        "domain": "USER_FEEDBACK",
                        "priority": "P0",
                        "why": "用于验证终态运营事项不会进入当前待办。",
                    },
                    matched_at=now,
                    materialized_at=now,
                )
            )

    overview = client.get("/api/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["current_ops_todo_count"] == 2
    assert overview.json()["severe_complaint_cases"] == 1

    compatible_default = client.get("/api/operations/interventions?type=OPS_CASE")
    assert compatible_default.status_code == 200
    assert compatible_default.json()["total"] == 5

    current_todos = client.get(
        "/api/operations/interventions?type=OPS_CASE&open_only=true"
    )
    assert current_todos.status_code == 200
    assert current_todos.json()["total"] == 2
    assert {
        (item["output_id"], item["status"])
        for item in current_todos.json()["items"]
    } == {
        ("CASE-SEVERE-1", "ACTION_PENDING"),
        ("CASE-OTHER-OPEN", "IN_REVIEW"),
    }


def test_lesson_evidence_is_safe_and_filterable() -> None:
    _seed_operations_evidence()

    response = client.get("/api/lessons?risk_only=true&lesson_id=LESSON-REAL-1")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["complaint_level"] == "P0"
    assert item["risk_domains"] == ["RELIABILITY", "USER_FEEDBACK"]
    assert "student_id" not in item
    assert "raw_payload" not in item


def test_lesson_reads_use_current_source_and_latest_complaint_rule() -> None:
    _seed_operations_evidence()
    newer_at = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            ComplaintRuleImportRecord(
                source_sha256="d" * 64,
                source_filename="complaint-newer.xlsx",
                raw_rows=[
                    {
                        "source_row_number": 2,
                        "一级分类": "关于老师",
                        "二级分类": "测试问题",
                        "三级分类": "测试",
                        "P级": "P2",
                        "Course Title in the Learning Hub": None,
                        "link": None,
                    }
                ],
                imported_at=newer_at,
            )
        )
        session.add(
            ComplaintCategoryRuleRecord(
                rule_id="COMPLAINT-RULE-OPERATIONS-NEWER",
                source_sha256="d" * 64,
                source_row_number=2,
                category_l1="关于老师",
                category_l2="测试问题",
                category_l3="测试",
                category_l3_normalized="测试",
                source_level="P2",
                severity_rank=2,
                default_route="TEACHER_TASK",
                created_at=newer_at,
            )
        )

    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        overview = OperationsService(engine).overview()
        lessons = OperationsService(engine).lessons(
            lesson_id="LESSON-REAL-1"
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    sql = "\n".join(statements).lower()
    assert "lesson_source_wide" in sql
    assert "lesson_facts" not in sql
    assert overview["lesson_total"] == 1
    assert overview["teacher_total"] == 1
    assert lessons["items"][0]["complaint_level"] == "P2"


def test_lesson_evidence_excludes_suppressed_matches() -> None:
    _seed_operations_evidence()
    with session_scope(engine) as session:
        for match in session.scalars(
            select(PersonalizedTriggerMatchRecord).where(
                PersonalizedTriggerMatchRecord.lesson_id
                == "LESSON-REAL-1"
            )
        ).all():
            match.match_status = "SUPPRESSED"

    response = client.get(
        "/api/lessons?risk_only=true&lesson_id=LESSON-REAL-1"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0

    all_lessons = client.get(
        "/api/lessons?risk_only=false&lesson_id=LESSON-REAL-1"
    )
    assert all_lessons.status_code == 200
    assert all_lessons.json()["items"][0]["risk_domains"] == []
    assert all_lessons.json()["items"][0]["signals"] == []


def test_unqualified_duplicate_lesson_id_is_rejected_as_ambiguous() -> None:
    with session_scope(engine) as session:
        session.add_all(
            [
                LessonSourceWideRecord(
                    source_region="dom",
                    course_id="LESSON-CROSS-REGION",
                    teacher_id=None,
                ),
                LessonSourceWideRecord(
                    source_region="ovs",
                    course_id="LESSON-CROSS-REGION",
                    teacher_id=None,
                ),
            ]
        )

    ambiguous = client.get(
        "/api/lessons",
        params={"lesson_id": "LESSON-CROSS-REGION"},
    )
    assert ambiguous.status_code == 409
    assert "LESSON_IDENTITY_AMBIGUOUS" in ambiguous.json()["detail"]

    qualified = client.get(
        "/api/lessons",
        params={
            "source_region": "dom",
            "lesson_id": "LESSON-CROSS-REGION",
        },
    )
    assert qualified.status_code == 200
    assert qualified.json()["total"] == 1
    assert qualified.json()["items"][0]["source_region"] == "dom"


def test_severe_complaint_case_can_be_processed_and_resolved() -> None:
    _seed_operations_evidence()

    started = client.post(
        "/api/operations/cases/CASE-SEVERE-1/decision",
        json={"decision": "START_PROCESSING", "note": ""},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "IN_REVIEW"

    resolved = client.post(
        "/api/operations/cases/CASE-SEVERE-1/decision",
        json={"decision": "RESOLVE", "note": "已完成运营核查并记录后续动作。"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"

    repeated = client.post(
        "/api/operations/cases/CASE-SEVERE-1/decision",
        json={"decision": "RESOLVE", "note": "重复处理"},
    )
    assert repeated.status_code == 409
