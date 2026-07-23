from __future__ import annotations

from app.agent_provider import BoundedAgentProvider, BoundedPlan


def signals() -> list[dict]:
    return [
        {
            "signal_id": "SIG-1",
            "code": "LESSON_PREP_RISK",
            "severity": "P1",
            "status": "VALID",
            "raw_student_comment": "must not leave the process",
        }
    ]


def test_deterministic_provider_cannot_create_work_without_current_candidates(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")
    result = BoundedAgentProvider().plan("T-1001", signals(), [])

    assert result.mode == "DETERMINISTIC"
    assert result.plan.selected_action_ids == ["NO_ACTION:NO_PUBLISHED_CANDIDATE"]


def test_openai_without_key_fails_closed_to_policy(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = BoundedAgentProvider().plan("T-1001", signals(), [])

    assert result.mode == "DETERMINISTIC_FALLBACK"
    assert result.fallback_reason == "PROVIDER_NOT_CONFIGURED"
    assert result.model == "gpt-5.6-terra"


def test_safe_payload_is_minimal() -> None:
    actions = BoundedAgentProvider._legacy_create_actions([])
    payload = BoundedAgentProvider._safe_payload("T-1001", signals(), [], actions)
    serialized = str(payload)

    assert "raw_student_comment" not in serialized
    assert "must not leave the process" not in serialized
    assert payload["candidate_templates"] == []
    assert payload["action_candidates"] == [
        {
            "action_id": "NO_ACTION:NO_PUBLISHED_CANDIDATE",
            "action_type": "NO_ACTION",
            "template_id": None,
            "task_id": None,
            "recommended": True,
            "reason_code": "NO_PUBLISHED_CANDIDATE",
        }
    ]
    assert payload["constraints"]["server_generated_action_ids_only"] is True


def test_unknown_action_is_rejected() -> None:
    plan = BoundedPlan(selected_action_ids=["MADE-UP"])
    actions = BoundedAgentProvider._legacy_create_actions([])

    try:
        BoundedAgentProvider._validate_action_subset(plan, actions)
    except ValueError as exc:
        assert str(exc) == "MODEL_SELECTED_UNKNOWN_ACTION"
    else:
        raise AssertionError("unknown action must be rejected")
