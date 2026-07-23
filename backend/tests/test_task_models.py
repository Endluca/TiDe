from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.task_catalog import task_template_seed_payloads
from app.task_models import CreateTaskTemplateRequest


def _request(**overrides: object) -> dict:
    payload = {
        **task_template_seed_payloads()[0],
        "idempotency_key": "task-model-validation-test",
    }
    payload.update(overrides)
    return payload


def test_current_mandatory_template_contract_is_valid() -> None:
    request = CreateTaskTemplateRequest.model_validate(_request())

    assert request.template_id == "G01"
    assert request.integration_mode == "INBOUND_STATUS_ONLY"
    assert request.source_mode == "REAL"
    assert "accepted_callback_statuses" not in request.model_dump()


def test_current_template_requires_an_explicit_source_mode() -> None:
    request = _request()
    request.pop("source_mode")

    with pytest.raises(ValidationError):
        CreateTaskTemplateRequest.model_validate(request)


def test_current_template_rejects_retired_callback_contract_fields() -> None:
    with pytest.raises(ValidationError):
        CreateTaskTemplateRequest.model_validate(
            _request(accepted_callback_statuses=["COMPLETED"])
        )


def test_personalized_template_cannot_award_fixed_points() -> None:
    with pytest.raises(ValidationError):
        CreateTaskTemplateRequest.model_validate(
            _request(
                template_id="PERSONALIZED-TEST",
                category="PERSONALIZED_IMPROVEMENT",
                integration_mode="OUTBOUND_MANAGED",
                score_type="FIXED",
                score_value=1,
            )
        )
