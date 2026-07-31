from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .auth import OperatorIdentity, require_roles
from .auth_models import OperatorRole
from .support_ticket_service import SupportTicketService


router = APIRouter(prefix="/api/support-tickets", tags=["support-tickets"])
service = SupportTicketService()


class SupportTicketReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    expected_row_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=5000)


@router.get("/summary")
def support_ticket_summary(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict[str, int]:
    return service.summary()


@router.get("")
def list_support_tickets(
    workflow_state: Optional[str] = Query(default=None),
    secondary_category: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict[str, Any]:
    return service.list_tickets(
        workflow_state=workflow_state,
        secondary_category=secondary_category,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/{ticket_id}")
def get_support_ticket(
    ticket_id: UUID,
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict[str, Any]:
    return service.get_ticket(ticket_id)


@router.post("/{ticket_id}/operator-replies")
def append_support_ticket_operator_reply(
    ticket_id: UUID,
    request: SupportTicketReplyRequest,
    operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.CASE_OPERATOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> dict[str, Any]:
    return service.append_operator_reply(
        ticket_id=ticket_id,
        expected_row_version=request.expected_row_version,
        message_id=request.message_id,
        content=request.content,
        actor_id=operator.operator_id,
    )
