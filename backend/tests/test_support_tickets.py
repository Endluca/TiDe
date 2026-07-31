from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from app.auth import OperatorIdentity, current_operator
from app.auth_models import OperatorRole
from app.main import app
from app import support_ticket_routes
from app.support_ticket_service import (
    SupportTicketService,
    _ticket_cte,
    support_ticket_workflow_state,
)


client = TestClient(app)
TICKET_ID = "8764b775-b12e-4dc3-b0fb-5c870fb7cb90"


def _ticket_payload(state: str = "WAITING_OPERATOR") -> dict:
    return {
        "ticket_id": TICKET_ID,
        "teacher_id": "4998291",
        "teacher_name": "Glenn Delle",
        "primary_category": "PRODUCT",
        "secondary_category": "SCORE_OR_REVIEW",
        "problem_location": "SCORE_DASHBOARD",
        "problem_context": {"lesson_id": "LESSON-1"},
        "source_status": "WAITING_OPERATOR",
        "workflow_state": state,
        "latest_message": {
            "message_id": "teacher-message-1",
            "sender": "TEACHER",
            "content": "Why is this score zero?",
            "images": [],
            "created_at": "2026-07-29T01:00:00Z",
        },
        "messages": [],
        "message_count": 1,
        "last_operator_reply_at": None,
        "teacher_reply_deadline_at": None,
        "close_reason": None,
        "closed_at": None,
        "image_cleanup_status": "NOT_REQUIRED",
        "images_deleted_at": None,
        "row_version": 1,
        "created_at": "2026-07-29T01:00:00Z",
        "updated_at": "2026-07-29T01:00:00Z",
    }


def test_operator_workflow_state_uses_latest_sender_without_teacher_refresh() -> None:
    assert support_ticket_workflow_state(
        "WAITING_OPERATOR",
        [{"sender": "TEACHER"}, {"sender": "OPERATOR"}],
    ) == "WAITING_TEACHER"
    assert support_ticket_workflow_state(
        "WAITING_TEACHER",
        [{"sender": "OPERATOR"}, {"sender": "TEACHER"}],
    ) == "WAITING_OPERATOR"
    assert support_ticket_workflow_state(
        "CLOSED",
        [{"sender": "TEACHER"}],
    ) == "CLOSED"


def test_ticket_list_projection_excludes_full_message_history() -> None:
    list_cte = _ticket_cte(include_messages=False)
    assert "\n        ticket.messages,\n" not in list_cte
    assert "ticket.messages -> -1 AS latest_message" in list_cte
    assert "jsonb_array_length" in list_cte

    row = {
        "ticket_id": TICKET_ID,
        "teacher_id": "4998291",
        "teacher_name": "Glenn Delle",
        "primary_category": "PRODUCT",
        "secondary_category": "SCORE_OR_REVIEW",
        "problem_location": "SCORE_DASHBOARD",
        "source_status": "WAITING_OPERATOR",
        "workflow_state": "WAITING_OPERATOR",
        "latest_message": {
            "message_id": "teacher-message-200",
            "sender": "TEACHER",
            "content": "Latest only",
            "images": [],
            "created_at": "2026-07-29T01:00:00Z",
        },
        "message_count": 200,
        "last_operator_reply_at": None,
        "teacher_reply_deadline_at": None,
        "close_reason": None,
        "closed_at": None,
        "image_cleanup_status": "NOT_REQUIRED",
        "images_deleted_at": None,
        "row_version": 200,
        "created_at": None,
        "updated_at": None,
    }
    payload = SupportTicketService._ticket_payload(
        row,
        include_messages=False,
    )
    assert payload["message_count"] == 200
    assert payload["latest_message"]["content"] == "Latest only"
    assert "messages" not in payload


def test_ticket_list_and_summary_are_readable_by_viewer(monkeypatch) -> None:
    monkeypatch.setattr(
        support_ticket_routes.service,
        "summary",
        lambda: {"waiting_operator": 1, "waiting_teacher": 2, "closed": 3, "total": 6},
    )
    monkeypatch.setattr(
        support_ticket_routes.service,
        "list_tickets",
        lambda **_: {
            "items": [_ticket_payload()],
            "total": 1,
            "page": 1,
            "page_size": 20,
        },
    )
    app.dependency_overrides[current_operator] = lambda: OperatorIdentity(
        operator_id="viewer",
        username="viewer",
        display_name="Viewer",
        roles=[OperatorRole.VIEWER],
    )

    summary_response = client.get("/api/support-tickets/summary")
    list_response = client.get(
        "/api/support-tickets",
        params={"workflow_state": "WAITING_OPERATOR", "page_size": 20},
    )

    assert summary_response.status_code == 200
    assert summary_response.json()["waiting_operator"] == 1
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["ticket_id"] == TICKET_ID


def test_reply_requires_case_operator_role(monkeypatch) -> None:
    called = False

    def _reply(**_):
        nonlocal called
        called = True
        return _ticket_payload("WAITING_TEACHER")

    monkeypatch.setattr(support_ticket_routes.service, "append_operator_reply", _reply)
    app.dependency_overrides[current_operator] = lambda: OperatorIdentity(
        operator_id="viewer",
        username="viewer",
        display_name="Viewer",
        roles=[OperatorRole.VIEWER],
    )

    response = client.post(
        f"/api/support-tickets/{TICKET_ID}/operator-replies",
        json={
            "message_id": "be99f012-379b-4fab-a1e2-a8d6655665ab",
            "expected_row_version": 1,
            "content": "Please refresh the score page.",
        },
    )

    assert response.status_code == 403
    assert called is False


def test_case_operator_reply_uses_shared_row_version(monkeypatch) -> None:
    captured: dict = {}

    def _reply(**kwargs):
        captured.update(kwargs)
        return _ticket_payload("WAITING_TEACHER")

    monkeypatch.setattr(support_ticket_routes.service, "append_operator_reply", _reply)

    response = client.post(
        f"/api/support-tickets/{TICKET_ID}/operator-replies",
        json={
            "message_id": "be99f012-379b-4fab-a1e2-a8d6655665ab",
            "expected_row_version": 7,
            "content": " Please refresh the score page. ",
        },
    )

    assert response.status_code == 200
    assert captured["ticket_id"] == UUID(TICKET_ID)
    assert captured["expected_row_version"] == 7
    assert captured["content"] == " Please refresh the score page. "
    assert captured["actor_id"] == "test-operator"


def test_reply_rejects_unreleased_image_payload() -> None:
    response = client.post(
        f"/api/support-tickets/{TICKET_ID}/operator-replies",
        json={
            "message_id": "be99f012-379b-4fab-a1e2-a8d6655665ab",
            "expected_row_version": 7,
            "content": "Please refresh the score page.",
            "images": [],
        },
    )

    assert response.status_code == 422
