from __future__ import annotations

from datetime import date, datetime, time, timezone

from fastapi.testclient import TestClient

from app.database import engine, session_scope
from app.db_models import (
    DataImportBatchRecord,
    LessonFactRecord,
    OpsCaseRecord,
    PersonalizedTriggerMatchRecord,
    SourceRecord,
    TaskAssignmentRecord,
)
from app.main import app


client = TestClient(app)


def _seed_operations_evidence() -> None:
    now = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    batch_id = "BATCH-LESSON-TEST"
    source_record_id = "SOURCE-LESSON-TEST"
    lesson_id = "LESSON-REAL-1"
    teacher_id = "T-1001"
    task_id = "TASK-PERSONALIZED-1"
    case_id = "CASE-SEVERE-1"
    with session_scope(engine) as session:
        session.add(
            DataImportBatchRecord(
                batch_id=batch_id,
                source_kind="LESSON_FACT",
                sync_mode="MANUAL_BASELINE",
                source_system="TEST_WORKBOOK",
                source_filename="lesson.xlsx",
                source_uri="test://lesson.xlsx",
                source_sha256="a" * 64,
                source_sheet="Sheet1",
                snapshot_label="TEST_LESSONS",
                data_mode="REAL",
                column_count=22,
                row_count=1,
                header=["课程id"],
                status="COMPLETED",
                imported_at=now,
                payload={"test": True},
            )
        )
        session.add(
            SourceRecord(
                source_record_id=source_record_id,
                batch_id=batch_id,
                source_sheet="Sheet1",
                source_row_number=2,
                business_key=lesson_id,
                teacher_id=teacher_id,
                lesson_id=lesson_id,
                occurred_at=None,
                row_sha256="b" * 64,
                raw_payload={"课程id": lesson_id},
            )
        )
        session.add(
            LessonFactRecord(
                lesson_id=lesson_id,
                source_appoint_id=lesson_id,
                camp_enrollment_id="CAMP-T-1001",
                teacher_id=teacher_id,
                scheduled_start_at=None,
                scheduled_end_at=None,
                lesson_lifecycle_status="end",
                lesson_local_date=date(2026, 5, 1),
                lesson_local_time=time(10, 0),
                student_id_hash="c" * 64,
                is_late=True,
                complaint_source_level="P0",
                complaint_level_rank=0,
                source_batch_id=batch_id,
                source_record_id=source_record_id,
                valid_for_scoring=True,
                evidence_status="CONFIRMED",
                data_mode="REAL",
                payload={"课程id": lesson_id},
            )
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
                why="该课程迟到，请完成出席培训。",
                display_title="出席问题",
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
                    lesson_id=lesson_id,
                    source_record_id=source_record_id,
                    complaint_rule_id=None,
                    scope_key=lesson_id,
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
                    lesson_id=lesson_id,
                    source_record_id=source_record_id,
                    complaint_rule_id=None,
                    scope_key=lesson_id,
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
                    source_record_id=None,
                    complaint_rule_id=None,
                    scope_key=teacher_id,
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
    item = interventions.json()["items"][0]
    assert item["title"] == "出席问题"
    assert item["source_lesson_id"] == "LESSON-REAL-1"
    assert item["status"] == "ASSIGNED"


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
                lesson_id="LESSON-REAL-1",
                source_record_id="SOURCE-LESSON-TEST",
                complaint_rule_id=None,
                scope_key="other-open",
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
                    lesson_id="LESSON-REAL-1",
                    source_record_id="SOURCE-LESSON-TEST",
                    complaint_rule_id=None,
                    scope_key=f"terminal-{index}",
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
