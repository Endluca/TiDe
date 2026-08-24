from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import OperatorIdentity, require_roles
from .auth_models import OperatorRole
from .operations_service import OperationsService
from .dts_v2_completion_correction import DtsV2CompletionCorrectionError


router = APIRouter(prefix="/api", tags=["operations"])
service = OperationsService()


class OpsCaseDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(START_PROCESSING|RESOLVE)$")
    note: str = Field(default="", max_length=2000)


class CompletionSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teacher_id: str = Field(min_length=1, max_length=64)
    teacher_id_type: Literal["NUMERIC", "TEXT"]
    status: Literal["end"]
    end_time: Optional[str] = None
    student_token: Optional[str] = Field(default=None, max_length=128)
    lesson_local_date: Optional[str] = None
    lesson_local_time: Optional[str] = None
    is_peak: Optional[bool] = None


class CompletionCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$",
    )
    decision: Literal[
        "KEEP_FROZEN_COMPLETION",
        "UPDATE_COMPLETION_SNAPSHOT",
        "TRANSFER_COMPLETION",
        "VOID_COMPLETION",
    ]
    reason: str = Field(min_length=1, max_length=2000)
    expected_case_revision: int = Field(ge=1)
    expected_conflict_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    expected_source_revision: int = Field(ge=1)
    expected_source_position: dict[str, Any]
    target_participation_seq: Optional[int] = Field(default=None, ge=1)
    completion_snapshot: Optional[CompletionSnapshotRequest] = None


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


@router.post("/operations/cases/{case_id}/completion-correction")
def apply_completion_correction(
    case_id: str,
    request: CompletionCorrectionRequest,
    operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.CASE_OPERATOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> dict:
    try:
        return service.apply_completion_correction(
            decision_id=request.decision_id,
            case_id=case_id,
            decision=request.decision,
            actor_id=operator.operator_id,
            reason=request.reason,
            expected_case_revision=request.expected_case_revision,
            expected_conflict_fingerprint=(
                request.expected_conflict_fingerprint
            ),
            expected_source_revision=request.expected_source_revision,
            expected_source_position=request.expected_source_position,
            target_participation_seq=request.target_participation_seq,
            completion_snapshot=(
                None
                if request.completion_snapshot is None
                else request.completion_snapshot.model_dump()
            ),
        )
    except DtsV2CompletionCorrectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/lessons")
def lesson_evidence(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    teacher_id: Optional[str] = Query(default=None),
    source_region: Optional[str] = Query(default=None),
    lesson_id: Optional[str] = Query(default=None),
    risk_only: bool = Query(default=False),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    try:
        return service.lessons(
            page=page,
            page_size=page_size,
            teacher_id=teacher_id,
            source_region=source_region,
            lesson_id=lesson_id,
            risk_only=risk_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
