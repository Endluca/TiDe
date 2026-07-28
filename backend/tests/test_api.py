from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth import OperatorIdentity, current_operator
from app.auth_models import OperatorRole
from app.database import engine, session_scope
from app.db_models import AuditEventRecord
from app.main import app


client = TestClient(app)


def _identity(*roles: OperatorRole) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="api-test-operator",
        username="api.test",
        display_name="API Test",
        roles=list(roles),
    )


def test_health_is_public_and_reports_persistent_database() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "ok"
    assert body["runtime"] == {"single_process_required": False}


def test_current_read_routes_require_viewer_role() -> None:
    app.dependency_overrides[current_operator] = lambda: _identity(
        OperatorRole.CONFIG_PUBLISHER
    )

    for path in (
        "/api/dashboard",
        "/api/teachers",
        "/api/teachers/T-1001/scorecard",
        "/api/task-templates",
        "/api/task-assignments",
        "/api/outputs",
    ):
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.json()["detail"]["code"] == "ROLE_REQUIRED"


def test_retired_task_transport_and_legacy_runtime_routes_are_absent() -> None:
    retired_requests = (
        ("GET", "/api/tasks"),
        ("POST", "/api/tasks/manual-issue"),
        ("GET", "/api/templates"),
        ("POST", "/api/triggers/evaluate/T-1001"),
        ("GET", "/api/v2/task-templates"),
        ("GET", "/api/v2/trigger-policies"),
        ("GET", "/api/trigger-policies"),
        ("PUT", "/api/task-templates/G01/versions/1"),
        ("POST", "/api/task-templates/G01/versions/1/publish"),
        ("POST", "/api/v2/task-publications"),
        ("POST", "/api/v2/task-publications/preview"),
        ("GET", "/api/v2/fixed-task-instances"),
        ("POST", "/api/v2/fixed-task-status-events"),
        ("POST", "/api/task-assignments/ASSIGNMENT-OLD/status-events"),
        ("GET", "/api/ops/action-queue"),
        ("GET", "/api/ops/cases"),
    )

    for method, path in retired_requests:
        response = client.request(method, path, json={})
        assert response.status_code == 404, (method, path, response.text)


def test_openapi_exposes_one_current_task_surface_only() -> None:
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {
        "/api/task-templates",
        "/api/task-templates/{template_id}",
        "/api/task-templates/{template_id}/publish",
        "/api/task-assignments",
    } <= paths
    assert not any(path.startswith("/api/v2/") for path in paths)
    assert "/api/trigger-policies" not in paths
    assert not any("/versions/" in path for path in paths)
    assert "/api/tasks" not in paths
    assert "/api/templates" not in paths
    assert "/api/triggers/evaluate/{teacher_id}" not in paths
    assert "/api/teachers/{teacher_id}/scorecard" in paths


def test_audit_events_are_server_paginated_and_filterable() -> None:
    with session_scope(engine) as session:
        for index in range(1, 26):
            payload = {
            "event_id": f"EVT-{index}",
            "event_type": "TASK_STATUS_CHANGED",
            "occurred_at": f"2026-07-28T00:00:{index:02d}Z",
            "teacher_id": "T-1" if index % 2 else "T-2",
            "payload": {"note": "needle" if index == 3 else "other"},
        }
            session.add(
                AuditEventRecord(
                    event_id=payload["event_id"],
                    event_type=payload["event_type"],
                    teacher_id=payload["teacher_id"],
                    task_id=None,
                    case_id=None,
                    occurred_at=datetime(
                        2026, 7, 28, 0, 0, index, tzinfo=timezone.utc
                    ),
                    actor_type="SYSTEM",
                    payload_hash=hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    payload=payload,
                )
            )

    first_page = client.get("/api/events?page=1&page_size=10")
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 25
    assert len(first_page.json()["items"]) == 10
    assert first_page.json()["items"][0]["event_id"] == "EVT-25"

    filtered = client.get(
        "/api/events?page=1&page_size=10&teacher_id=T-1&keyword=needle"
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["event_id"] == "EVT-3"
