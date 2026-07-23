from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from .auth import OperatorIdentity, require_roles
from .auth_models import OperatorRole
from .task_models import (
    CreateTaskTemplateRequest,
    PublishTaskTemplateRequest,
    UpdateTaskTemplateRequest,
)
from .task_service import TaskService


# Both products read and write the shared task_assignments table directly.
router = APIRouter(prefix="/api", tags=["tasks"])
service = TaskService()


@router.get("/task-templates")
def list_task_templates(
    template_status: Optional[str] = Query(default=None, alias="status"),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.list_templates(template_status)


@router.post("/task-templates", status_code=status.HTTP_201_CREATED)
def create_task_template(
    request: CreateTaskTemplateRequest,
    operator: OperatorIdentity = Depends(require_roles(OperatorRole.CONFIG_PUBLISHER)),
) -> dict:
    return service.create_template(request, operator.operator_id)


@router.put("/task-templates/{template_id}")
def update_task_template(
    template_id: str,
    request: UpdateTaskTemplateRequest,
    operator: OperatorIdentity = Depends(require_roles(OperatorRole.CONFIG_PUBLISHER)),
) -> dict:
    return service.update_template(template_id, request, operator.operator_id)


@router.post("/task-templates/{template_id}/publish")
def publish_task_template(
    template_id: str,
    request: PublishTaskTemplateRequest,
    operator: OperatorIdentity = Depends(require_roles(OperatorRole.CONFIG_PUBLISHER)),
) -> dict:
    return service.publish_template(template_id, request, operator.operator_id)


@router.get("/task-assignments")
def list_task_assignments(
    teacher_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    assignment_status: Optional[str] = Query(default=None, alias="status"),
    task_kind: Optional[str] = Query(default=None),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.list_assignments(
        teacher_id=teacher_id,
        status=assignment_status,
        task_kind=task_kind,
    )


@router.get("/task-progress")
def list_task_progress(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.list_task_progress()


@router.get("/task-progress/assignments")
def list_task_progress_assignments(
    task_code: str = Query(min_length=1, max_length=64),
    title: str = Query(min_length=1, max_length=500),
    task_kind: str = Query(
        pattern="^(FIXED_GROWTH|PERSONALIZED_IMPROVEMENT)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.list_task_progress_assignments(
        task_code=task_code,
        title=title,
        task_kind=task_kind,
        page=page,
        page_size=page_size,
    )
