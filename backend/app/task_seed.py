from __future__ import annotations

from sqlalchemy import Engine

from .database import engine as default_engine
from .task_catalog import task_template_seed_payloads
from .task_models import (
    CreateTaskTemplateRequest,
    PublishTaskTemplateRequest,
)
from .task_service import TaskService


def seed_task_catalog(
    bind: Engine | None = None,
    *,
    actor_id: str = "SYSTEM_SEED",
) -> dict[str, int]:
    """Idempotently seed the current mandatory and personalized task catalog."""

    service = TaskService(bind or default_engine)
    existing_templates = {item["template_id"] for item in service.list_templates()}
    created_templates = 0
    published_templates = 0
    for payload in task_template_seed_payloads():
        template_id = payload["template_id"]
        if template_id in existing_templates:
            continue
        request = CreateTaskTemplateRequest.model_validate(
            {
                **payload,
                "idempotency_key": f"seed-task-template-{template_id.lower()}",
            }
        )
        created = service.create_template(request, actor_id)
        created_templates += 1
        service.publish_template(
            template_id,
            PublishTaskTemplateRequest(expected_revision=int(created["revision"])),
            actor_id,
        )
        published_templates += 1
    expected_payloads = task_template_seed_payloads()
    expected_codes = {payload["template_id"] for payload in expected_payloads}
    approved = {
        item["template_id"]: item
        for item in service.list_templates()
        if item["template_id"] in expected_codes
    }
    if set(approved) != expected_codes:
        raise RuntimeError("G01-G10 task-template catalog is incomplete")
    mandatory = {code: item for code, item in approved.items() if code.startswith("G")}
    personalized = {code: item for code, item in approved.items() if code.startswith("P-")}
    if any(
        item["status"] != "PUBLISHED"
        or item["integration_mode"] != "INBOUND_STATUS_ONLY"
        or item["source_mode"] != "REAL"
        for item in mandatory.values()
    ):
        raise RuntimeError("G01-G10 templates must be published, inbound-only and REAL")
    if any(
        item["status"] != "PUBLISHED"
        or item["integration_mode"] != "OUTBOUND_MANAGED"
        or item["source_mode"] != "REAL"
        or item["score_type"] != "ZERO"
        or float(item["score_value"]) != 0
        for item in personalized.values()
    ):
        raise RuntimeError(
            "personalized templates must be published, outbound-managed, REAL and zero-point"
        )
    points = {code: float(item["score_value"]) for code, item in mandatory.items()}
    if sum(points.values()) != 30 or points["G09"] != 10:
        raise RuntimeError("G01-G10 fixed points must total 30 and G09 must equal 10")
    return {
        "template_catalog_size": len(task_template_seed_payloads()),
        "templates_created": created_templates,
        "templates_published": published_templates,
    }
