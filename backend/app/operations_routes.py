from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import OperatorIdentity, require_roles
from .auth_models import OperatorRole
from .operations_service import OperationsService


router = APIRouter(prefix="/api", tags=["operations"])
service = OperationsService()


class OpsCaseDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(START_PROCESSING|RESOLVE)$")
    note: str = Field(default="", max_length=2000)


@router.get("/operations/overview")
def operations_overview(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.overview()


@router.get("/operations/interventions")
def operations_interventions(
    output_type: Optional[str] = Query(default=None, alias="type"),
    status: Optional[str] = Query(default=None),
    domain: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    open_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.interventions(
        output_type=output_type,
        status=status,
        domain=domain,
        teacher_id=teacher_id,
        open_only=open_only,
        page=page,
        page_size=page_size,
    )


@router.post("/operations/cases/{case_id}/decision")
def decide_operations_case(
    case_id: str,
    request: OpsCaseDecisionRequest,
    operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.CASE_OPERATOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> dict:
    try:
        return service.decide_case(
            case_id=case_id,
            decision=request.decision,
            note=request.note,
            actor_id=operator.operator_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="运营事项不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="运营事项已经结束，不能重复处理") from exc


@router.get("/lessons")
def lesson_evidence(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    teacher_id: Optional[str] = Query(default=None),
    lesson_id: Optional[str] = Query(default=None),
    risk_only: bool = Query(default=False),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.lessons(
        page=page,
        page_size=page_size,
        teacher_id=teacher_id,
        lesson_id=lesson_id,
        risk_only=risk_only,
    )
