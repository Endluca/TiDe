from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import OperatorIdentity, current_operator
from app.auth_models import OperatorRole
from app.database import engine, session_scope
from app.db_models import (
    TaskAssignmentRecord,
    TaskTemplateRecord,
)
from app.main import app
from app.task_catalog import (
    MANDATORY_TASK_CODE_SET,
    task_template_seed_payloads,
)
from app.task_seed import seed_task_catalog
from app.task_service import TaskService


client = TestClient(app)
EXPECTED_G_CODES = set(MANDATORY_TASK_CODE_SET)
EXPECTED_PERSONALIZED_CODES = {
    "P-REL-MEMO",
    "P-REL-ATTENDANCE",
    "P-FB-NEGATIVE",
    "P-FB-COMPLAINT",
    "P-FB-BLACKLIST",
}
EXPECTED_MANDATORY_CATALOG = {
    "G01": ("Profile & Credentials Completion", 3, "DAY_1_7"),
    "G02": ("Platform Policies", 2, "DAY_1_7"),
    "G03": ("How to handle different types of students", 2, "DAY_1_7"),
    "G04": ("Lesson Preparation", 3, "DAY_1_7"),
    "G05": ("TTP Orientation", 3, "DAY_8_14"),
    "G06": ("ME Culture & PARSNIP", 4, "DAY_8_14"),
    "G07": ("Reliability Training", 3, "DAY_8_14"),
    "G08": ("Cocos Course Training", 5, "DAY_15_30"),
    "G09": ("SET Teaching Fundamentals", 5, "DAY_15_30"),
}

EXPECTED_MANDATORY_COPY = {
    "G01": (
        "Complete the required TESOL status and learning evidence.",
        "Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.",
        "TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.",
        "Your profile and required TESOL learning evidence are complete.",
        "READY",
    ),
    "G02": (
        "Learn the essential classroom and account-safety rules.",
        "Read the in-platform policy guide and complete its quiz.",
        "The policy guide is confirmed and the quiz requirements pass.",
        "You can apply the core platform policies in class.",
        "READY",
    ),
    "G03": (
        "Build practical responses for different learner needs.",
        "Complete the learning content configured by Jiahe.",
        "Meet every requirement in the published Student Types configuration.",
        "You can adapt your teaching to different learner types.",
        "PENDING_JIAHE",
    ),
    "G04": (
        "Complete the teaching-environment photo review and prepare the courseware before your first lesson.",
        "Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.",
        "G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.",
        "Your teaching environment and courseware are ready for your first lesson.",
        "READY",
    ),
    "G05": (
        "Understand TTP and its key business scenarios.",
        "Watch the in-platform TTP video and confirm every item in the learning checklist.",
        "The TTP video is watched in full and every published checklist item is confirmed.",
        "You understand the key TTP workflow and commitments.",
        "READY",
    ),
    "G06": (
        "Learn cross-cultural classroom guidance.",
        "Complete the configured videos and quiz.",
        "All configured videos and quiz requirements pass.",
        "You can apply the culture guidance appropriately.",
        "READY",
    ),
    "G07": (
        "Strengthen dependable attendance habits.",
        "Complete the configured training and quiz.",
        "All configured training and quiz requirements pass.",
        "You have a clear reliability routine.",
        "READY",
    ),
    "G08": (
        "Learn the core Cocos teaching flow.",
        "Complete the configured in-platform videos and quiz.",
        "All configured videos and quiz requirements pass.",
        "You can prepare for a Cocos class.",
        "READY",
    ),
    "G09": (
        "Learn the fundamentals of SET teaching.",
        "Watch the in-platform Mock video slot and complete the five-question Mock check.",
        "The Mock video is watched in full and the five-question check reaches 80%.",
        "You understand the SET teaching foundation.",
        "READY",
    ),
}


def _personalized_template_request() -> dict:
    source = dict(task_template_seed_payloads()[0])
    source.update(
        template_id="TEST-CURRENT-01",
        integration_mode="OUTBOUND_MANAGED",
        category="PERSONALIZED_IMPROVEMENT",
        dimension="RELIABILITY",
        stage="TEST_ONLY",
        ops_name_zh="当前模型测试任务",
        title="Current model test task",
        external_task_template_code="TIT.TEST.CURRENT.01",
        score_type="ZERO",
        score_value=0,
        idempotency_key="test-current-template-create-01",
    )
    return source


def _insert_fixed_assignment(
    *,
    assignment_id: str = "ASSIGNMENT-CURRENT-G01",
    teacher_id: str = "T-1001",
    task_code: str = "G01",
    creator_system: str = "TRIGGER_CENTER",
    status: str = "ASSIGNED",
    source_mode: str = "MOCK",
) -> None:
    now = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            TaskAssignmentRecord(
                assignment_id=assignment_id,
                teacher_id=teacher_id,
                task_code=task_code,
                template_version_id=f"{task_code}:v1",
                task_kind="FIXED_GROWTH",
                creator_system=creator_system,
                status=status,
                priority="P1",
                why="This fixed growth task is part of the approved trial-camp path.",
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=(
                    "TEST_TERMINAL_STATUS"
                    if status in {"FAILED", "EXPIRED", "WAIVED", "CANCELLED"}
                    else None
                ),
                source_mode=source_mode,
                dedupe_key=f"fixed:{teacher_id}:{task_code}",
                created_by="tit_teacher_test",
                updated_by="tit_teacher_test",
                row_version=1,
                assigned_at=now,
                status_changed_at=now,
                completed_at=now if status == "COMPLETED" else None,
                created_at=now,
                updated_at=now,
            )
        )


def _insert_personalized_assignment(
    *,
    assignment_id: str,
    teacher_id: str,
    title: str,
    status: str = "ASSIGNED",
) -> None:
    now = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            TaskAssignmentRecord(
                assignment_id=assignment_id,
                teacher_id=teacher_id,
                task_code="P-FB-COMPLAINT",
                template_version_id="P-FB-COMPLAINT:v1",
                task_kind="PERSONALIZED_IMPROVEMENT",
                creator_system="TRIGGER_CENTER",
                status=status,
                priority="P1",
                why=(
                    "Complaint evidence triggered this learning task. "
                    f"Evidence: Lesson IDs: LESSON-{assignment_id}; "
                    "complaint category recorded."
                ),
                display_title=title,
                evidence_snapshot={
                    "lesson_ids": [f"LESSON-{assignment_id}"],
                    "complaint_level3": title,
                },
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=(
                    "TEST_TERMINAL_STATUS"
                    if status in {"FAILED", "EXPIRED", "WAIVED", "CANCELLED"}
                    else None
                ),
                source_mode="DERIVED_REAL",
                dedupe_key=f"personalized:{assignment_id}",
                created_by="trigger-center-test",
                updated_by="trigger-center-test",
                row_version=1,
                assigned_at=now,
                status_changed_at=now,
                completed_at=now if status == "COMPLETED" else None,
                created_at=now,
                updated_at=now,
            )
        )


def test_seed_is_idempotent_and_contains_current_catalog() -> None:
    result = seed_task_catalog(engine)
    repeated = seed_task_catalog(engine)

    assert result["template_catalog_size"] == 14
    assert result["templates_created"] == 0
    assert repeated["templates_created"] == 0

    with session_scope(engine) as session:
        templates = session.scalars(select(TaskTemplateRecord)).all()

    assert {item.template_id for item in templates} == (
        EXPECTED_G_CODES | EXPECTED_PERSONALIZED_CODES
    )
    assert all(item.status == "PUBLISHED" for item in templates)
    assert all(item.source_mode == "REAL" for item in templates)
    assert all(item.payload["source_mode"] == "REAL" for item in templates)
    assert all(
        item.integration_mode == "INBOUND_STATUS_ONLY"
        for item in templates
        if item.template_id in EXPECTED_G_CODES
    )
    assert all(
        item.integration_mode == "OUTBOUND_MANAGED"
        for item in templates
        if item.template_id in EXPECTED_PERSONALIZED_CODES
    )
    points = {
        item.template_id: item.payload["score_value"]
        for item in templates
        if item.template_id in EXPECTED_G_CODES
    }
    assert sum(points.values()) == 30
    assert points["G08"] == 5
    actual_catalog = {
        item.template_id: (
            item.payload["title"],
            item.payload["score_value"],
            item.payload["stage"],
        )
        for item in templates
        if item.template_id in EXPECTED_G_CODES
    }
    assert actual_catalog == EXPECTED_MANDATORY_CATALOG
    g04_payload = next(
        item.payload for item in templates if item.template_id == "G04"
    )
    assert g04_payload["ops_name_zh"] == "首课准备"
    g04_teacher_copy = " ".join(
        str(g04_payload[field])
        for field in (
            "why_template",
            "how_summary",
            "completion_standard",
            "benefit",
        )
    ).lower()
    assert "courseware" in g04_teacher_copy
    assert "photo" in g04_teacher_copy
    assert g04_payload["how_summary"].index("photo") < g04_payload[
        "how_summary"
    ].index("courseware")
    assert g04_payload["completion_standard"].index("photo") < g04_payload[
        "completion_standard"
    ].index("courseware")
    assert all(
        removed_term not in g04_teacher_copy
        for removed_term in ("device", "network", "microphone")
    )
    actual_copy = {
        item.template_id: (
            item.payload["why_template"],
            item.payload["how_summary"],
            item.payload["completion_standard"],
            item.payload["benefit"],
            item.payload["content_status"],
        )
        for item in templates
        if item.template_id in EXPECTED_G_CODES
    }
    assert actual_copy == EXPECTED_MANDATORY_COPY
    assert all(
        "Free Trial" not in str(item.payload)
        for item in templates
        if item.template_id in EXPECTED_G_CODES
    )


def test_current_catalog_api_returns_current_templates_without_versions() -> None:
    templates = client.get("/api/task-templates")

    assert templates.status_code == 200
    assert {item["template_id"] for item in templates.json()} == (
        EXPECTED_G_CODES | EXPECTED_PERSONALIZED_CODES
    )
    assert all("template_version" not in item for item in templates.json())


def test_current_template_create_update_publish_lifecycle_and_role_boundary() -> None:
    request = _personalized_template_request()

    app.dependency_overrides[current_operator] = lambda: OperatorIdentity(
        operator_id="viewer-only",
        username="viewer.only",
        display_name="Viewer",
        roles=[OperatorRole.VIEWER],
    )
    denied = client.post("/api/task-templates", json=request)
    assert denied.status_code == 403

    app.dependency_overrides[current_operator] = lambda: OperatorIdentity(
        operator_id="config-publisher",
        username="config.publisher",
        display_name="Config Publisher",
        roles=[OperatorRole.VIEWER, OperatorRole.CONFIG_PUBLISHER],
    )
    created = client.post("/api/task-templates", json=request)
    assert created.status_code == 201
    assert created.json()["status"] == "DRAFT"
    assert created.json()["revision"] == 1

    update_request = {
        key: value
        for key, value in request.items()
        if key not in {"template_id", "idempotency_key"}
    }
    update_request.update(
        expected_revision=1,
        title="Updated current model test task",
    )
    updated = client.put(
        "/api/task-templates/TEST-CURRENT-01",
        json=update_request,
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["title"] == "Updated current model test task"

    published = client.post(
        "/api/task-templates/TEST-CURRENT-01/publish",
        json={"expected_revision": 2},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    assert published.json()["revision"] == 3


def test_template_update_compare_and_swap_rejects_a_stale_second_session() -> None:
    request = _personalized_template_request()
    created = client.post("/api/task-templates", json=request)
    assert created.status_code == 201

    with Session(engine) as first_session, Session(engine) as second_session:
        first_revision = first_session.scalar(
            select(TaskTemplateRecord.revision).where(
                TaskTemplateRecord.template_id == "TEST-CURRENT-01"
            )
        )
        stale_revision = second_session.scalar(
            select(TaskTemplateRecord.revision).where(
                TaskTemplateRecord.template_id == "TEST-CURRENT-01"
            )
        )
        second_session.rollback()
        assert first_revision == stale_revision == 1

        assert TaskService._cas_update_template(
            first_session,
            row_id="TEST-CURRENT-01:v1",
            expected_revision=first_revision,
            values={"revision": 2},
        )
        first_session.commit()

        assert not TaskService._cas_update_template(
            second_session,
            row_id="TEST-CURRENT-01:v1",
            expected_revision=stale_revision,
            values={"revision": 2},
        )
        second_session.rollback()

    with session_scope(engine) as session:
        assert session.scalar(
            select(TaskTemplateRecord.revision).where(
                TaskTemplateRecord.template_id == "TEST-CURRENT-01"
            )
        ) == 2


def test_shared_assignment_list_joins_current_template_and_supports_filters() -> None:
    _insert_fixed_assignment()

    response = client.get("/api/task-assignments")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20
    assert response.json()["total_pages"] == 1
    assignment = response.json()["items"][0]
    assert assignment["assignment_id"] == "ASSIGNMENT-CURRENT-G01"
    assert assignment["teacher_id"] == "T-1001"
    assert assignment["teacher_name"] == "Maria Santos"
    assert assignment["task_code"] == "G01"
    assert "template_version_id" not in assignment
    assert assignment["task_kind"] == "FIXED_GROWTH"
    assert assignment["creator_system"] == "TRIGGER_CENTER"
    assert assignment["title"] == "Profile & Credentials Completion"
    assert assignment["status"] == "ASSIGNED"
    assert assignment["row_version"] == 1

    assert client.get(
        "/api/task-assignments?teacher_id=T-1001"
    ).json()["total"] == 1
    assert client.get(
        "/api/task-assignments?teacher_id=T-UNKNOWN"
    ).json()["items"] == []
    assert client.get(
        "/api/task-assignments?status=COMPLETED"
    ).json()["items"] == []
    assert client.get(
        "/api/task-assignments?task_kind=PERSONALIZED_IMPROVEMENT"
    ).json()["items"] == []


def test_shared_assignment_list_uses_server_side_pagination() -> None:
    _insert_fixed_assignment(
        assignment_id="ASSIGNMENT-CURRENT-G01-PAGE-1",
        teacher_id="T-1001",
    )
    _insert_fixed_assignment(
        assignment_id="ASSIGNMENT-CURRENT-G01-PAGE-2",
        teacher_id="T-1002",
    )

    first_page = client.get("/api/task-assignments?page=1&page_size=1")
    second_page = client.get("/api/task-assignments?page=2&page_size=1")

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert first_page.json()["total_pages"] == 2
    assert len(first_page.json()["items"]) == 1
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 2
    assert second_page.json()["page"] == 2
    assert len(second_page.json()["items"]) == 1
    assert (
        first_page.json()["items"][0]["assignment_id"]
        != second_page.json()["items"][0]["assignment_id"]
    )

    operational = client.get(
        "/api/task-assignments?include_mock=false&page=1&page_size=10"
    )
    assert operational.status_code == 200
    assert operational.json()["total"] == 0
    assert operational.json()["items"] == []


def test_task_progress_aggregates_operational_assignments_and_pages_details() -> None:
    _insert_fixed_assignment(
        assignment_id="FIXED-G01-T1",
        teacher_id="T-1001",
        source_mode="REAL",
    )
    _insert_fixed_assignment(
        assignment_id="FIXED-G01-T2",
        teacher_id="T-1002",
        status="COMPLETED",
        source_mode="REAL",
    )
    _insert_fixed_assignment(
        assignment_id="MOCK-G02-T3",
        teacher_id="T-1003",
        task_code="G02",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-1",
        teacher_id="T-1001",
        title="General Complaint - A",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-2",
        teacher_id="T-1002",
        title="General Complaint - A",
        status="COMPLETED",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-3",
        teacher_id="T-1003",
        title="General Complaint - A",
        status="UNDER_REVIEW",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-4",
        teacher_id="T-1004",
        title="General Complaint - B",
        status="EXPIRED",
    )

    response = client.get("/api/task-progress")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert all(item["task_code"] != "G02" for item in body["items"])

    fixed = next(item for item in body["items"] if item["task_code"] == "G01")
    assert fixed == {
        "task_code": "G01",
        "title": "Profile & Credentials Completion",
        "task_kind": "FIXED_GROWTH",
        "assigned_teacher_count": 2,
        "assignment_count": 2,
        "not_started": 1,
        "in_progress": 0,
        "completed": 1,
        "other": 0,
        "completion_rate": 0.5,
    }

    personalized = next(
        item
        for item in body["items"]
        if item["title"] == "General Complaint - A"
    )
    assert personalized["assigned_teacher_count"] == 3
    assert personalized["assignment_count"] == 3
    assert personalized["not_started"] == 1
    assert personalized["in_progress"] == 1
    assert personalized["completed"] == 1
    assert personalized["other"] == 0
    assert personalized["completion_rate"] == pytest.approx(1 / 3)

    detail = client.get(
        "/api/task-progress/assignments",
        params={
            "task_code": "P-FB-COMPLAINT",
            "title": "General Complaint - A",
            "task_kind": "PERSONALIZED_IMPROVEMENT",
            "page": 1,
            "page_size": 2,
        },
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["total"] == 3
    assert detail_body["page"] == 1
    assert detail_body["page_size"] == 2
    assert detail_body["total_pages"] == 2
    assert len(detail_body["items"]) == 2
    assert {
        item["title"] for item in detail_body["items"]
    } == {"General Complaint - A"}

    second_page = client.get(
        "/api/task-progress/assignments",
        params={
            "task_code": "P-FB-COMPLAINT",
            "title": "General Complaint - A",
            "task_kind": "PERSONALIZED_IMPROVEMENT",
            "page": 2,
            "page_size": 2,
        },
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1

    teacher_search = client.get(
        "/api/task-progress",
        params={"keyword": "T-1002"},
    )
    assert teacher_search.status_code == 200
    assert {
        (item["task_code"], item["title"])
        for item in teacher_search.json()["items"]
    } == {
        ("G01", "Profile & Credentials Completion"),
        ("P-FB-COMPLAINT", "General Complaint - A"),
    }

    teacher_detail = client.get(
        "/api/task-progress/assignments",
        params={
            "task_code": "P-FB-COMPLAINT",
            "title": "General Complaint - A",
            "task_kind": "PERSONALIZED_IMPROVEMENT",
            "keyword": "T-1002",
            "page": 1,
            "page_size": 10,
        },
    )
    assert teacher_detail.status_code == 200
    assert teacher_detail.json()["total"] == 1
    assert teacher_detail.json()["items"][0]["teacher_id"] == "T-1002"
    with session_scope(engine) as session:
        stored = session.get(TaskAssignmentRecord, "P-COMPLAINT-2")
        assert stored is not None
        assert teacher_detail.json()["items"][0]["why"] == stored.why
        assert "Evidence:" in stored.why
    assert "Evidence:" in teacher_detail.json()["items"][0]["why"]


def test_task_progress_aggregates_and_pages_in_sql() -> None:
    _insert_fixed_assignment(
        assignment_id="FIXED-G01-SQL-1",
        teacher_id="T-1001",
        source_mode="REAL",
    )
    _insert_fixed_assignment(
        assignment_id="FIXED-G01-SQL-2",
        teacher_id="T-1002",
        source_mode="REAL",
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
        progress = TaskService(engine).list_task_progress()
        progress_statements = list(statements)
        statements.clear()
        detail = TaskService(engine).list_task_progress_assignments(
            task_code="G01",
            title="Profile & Credentials Completion",
            task_kind="FIXED_GROWTH",
            page=1,
            page_size=1,
        )
        detail_statements = list(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert progress["total"] == 1
    assert len(progress_statements) == 1
    assert "GROUP BY" in progress_statements[0].upper()
    assert detail["total"] == 2
    assert len(detail["items"]) == 1
    assert len(detail_statements) == 2
    assert "LIMIT" in detail_statements[-1].upper()


def test_shared_assignment_schema_excludes_retired_transport_fields() -> None:
    columns = {column.name for column in TaskAssignmentRecord.__table__.columns}

    assert {
        "assignment_id",
        "teacher_id",
        "template_version_id",
        "task_code",
        "task_kind",
        "creator_system",
        "status",
        "why",
        "row_version",
    } <= columns
    assert {
        "external_task_id",
        "provider_event_id",
        "latest_sequence",
        "dispatched_at",
        "accepted_at",
        "request_hash",
        "trigger_evaluation_id",
        "result_code",
    }.isdisjoint(columns)


def test_shared_assignment_database_constraint_rejects_wrong_fixed_task_owner() -> None:
    with pytest.raises(IntegrityError):
        _insert_fixed_assignment(
            assignment_id="ASSIGNMENT-WRONG-OWNER",
            creator_system="TEACHER_APP",
        )


def test_migration_grants_teacher_service_only_necessary_shared_table_access() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260722_13_single_task_schema.py"
    ).read_text(encoding="utf-8")
    upper = migration.upper()

    assert "CREATE ROLE" not in upper
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM tit_teacher_crud"
        in migration
    )
    assert "GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud" in migration
    assert "GRANT SELECT ON TABLE public.{template_table} TO tit_teacher_crud" in migration
    assert '_restrict_teacher_role(bind, template_table="task_templates")' in migration
    assert "GRANT INSERT (" in migration
    assert "GRANT UPDATE (" in migration
    assert "DELETE ON TABLE public.task_assignments TO tit_teacher_crud" not in migration
