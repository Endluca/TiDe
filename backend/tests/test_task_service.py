from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorIdentity, current_operator
from app.auth_models import OperatorRole
from app.database import engine, session_scope
from app.db_models import (
    TaskAssignmentRecord,
    TaskTemplateRecord,
)
from app.main import app
from app.task_catalog import (
    task_template_seed_payloads,
)
from app.task_seed import seed_task_catalog


client = TestClient(app)
EXPECTED_G_CODES = {f"G{index:02d}" for index in range(1, 11)}
EXPECTED_PERSONALIZED_CODES = {
    "P-REL-MEMO",
    "P-REL-ATTENDANCE",
    "P-FB-NEGATIVE",
    "P-FB-COMPLAINT",
    "P-FB-BLACKLIST",
}
EXPECTED_MANDATORY_CATALOG = {
    "G01": ("Profile & Credentials Completion", 4, "DAY_1_7"),
    "G02": ("Device & Network Check", 3, "DAY_1_7"),
    "G03": ("Platform Policies", 3, "DAY_1_7"),
    "G04": ("How to handle different types of students", 1, "DAY_1_7"),
    "G05": ("Lesson Preparation", 3, "DAY_1_7"),
    "G06": ("TTP Orientation", 1, "DAY_8_14"),
    "G07": ("ME Culture & PARSNIP", 2, "DAY_8_14"),
    "G08": ("Reliability Training", 2, "DAY_8_14"),
    "G09": ("Cocos Course Training", 10, "DAY_15_30"),
    "G10": ("SET Teaching Fundamentals", 1, "DAY_15_30"),
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
                why=f"Complaint evidence triggered {title}.",
                display_title=title,
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

    assert result["template_catalog_size"] == 15
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
    assert points["G09"] == 10
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


def test_shared_assignment_list_joins_current_template_and_supports_filters() -> None:
    _insert_fixed_assignment()

    response = client.get("/api/task-assignments")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assignment = response.json()[0]
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

    assert len(client.get("/api/task-assignments?teacher_id=T-1001").json()) == 1
    assert client.get("/api/task-assignments?teacher_id=T-UNKNOWN").json() == []
    assert client.get("/api/task-assignments?status=COMPLETED").json() == []
    assert client.get(
        "/api/task-assignments?task_kind=PERSONALIZED_IMPROVEMENT"
    ).json() == []


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
        title="一般投诉-A问题",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-2",
        teacher_id="T-1002",
        title="一般投诉-A问题",
        status="COMPLETED",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-3",
        teacher_id="T-1003",
        title="一般投诉-A问题",
        status="UNDER_REVIEW",
    )
    _insert_personalized_assignment(
        assignment_id="P-COMPLAINT-4",
        teacher_id="T-1004",
        title="一般投诉-B问题",
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
        if item["title"] == "一般投诉-A问题"
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
            "title": "一般投诉-A问题",
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
    } == {"一般投诉-A问题"}

    second_page = client.get(
        "/api/task-progress/assignments",
        params={
            "task_code": "P-FB-COMPLAINT",
            "title": "一般投诉-A问题",
            "task_kind": "PERSONALIZED_IMPROVEMENT",
            "page": 2,
            "page_size": 2,
        },
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1


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
