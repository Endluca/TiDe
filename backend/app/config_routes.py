from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .auth import OperatorIdentity, require_roles
from .auth_models import OperatorRole
from .config_models import ConfigKey, ConfigStatus
from .config_service import ConfigDomainError, ConfigService


router = APIRouter(prefix="/api/configs", tags=["configuration"])


class CreateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_key: ConfigKey
    payload: Optional[dict[str, Any]] = None
    from_version_id: Optional[str] = Field(default=None, max_length=128)


class UpdateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


def get_config_service() -> ConfigService:
    return ConfigService()


def resolve_config_actor(
    operator: OperatorIdentity = Depends(require_roles(OperatorRole.CONFIG_PUBLISHER)),
) -> str:
    """操作者只来自服务端校验过的会话，任何请求体 actor 字段都会被拒绝。"""

    return operator.operator_id


def _translate(exc: ConfigDomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.response())


@router.get("")
def list_config_versions(
    config_key: Optional[ConfigKey] = Query(default=None, alias="key"),
    status: Optional[ConfigStatus] = Query(default=None),
    service: ConfigService = Depends(get_config_service),
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.VIEWER, OperatorRole.CONFIG_PUBLISHER)
    ),
) -> list[dict[str, Any]]:
    return service.list_versions(config_key=config_key, status=status)


@router.get("/published/{config_key}")
def get_published_config(
    config_key: ConfigKey,
    service: ConfigService = Depends(get_config_service),
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.VIEWER, OperatorRole.CONFIG_PUBLISHER)
    ),
) -> dict[str, Any]:
    payload = service.get_published_payload(config_key)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "PUBLISHED_CONFIG_NOT_FOUND", "message": "该配置域尚无已发布版本"},
        )
    return {"config_key": config_key.value, "payload": payload}


@router.post("/drafts", status_code=201)
def create_config_draft(
    request: CreateDraftRequest,
    actor_id: str = Depends(resolve_config_actor),
    service: ConfigService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        return service.create_draft(
            request.config_key,
            actor_id=actor_id,
            payload=request.payload,
            from_version_id=request.from_version_id,
        )
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.get("/{version_id}")
def get_config_version(
    version_id: str,
    service: ConfigService = Depends(get_config_service),
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.VIEWER, OperatorRole.CONFIG_PUBLISHER)
    ),
) -> dict[str, Any]:
    try:
        return service.get_version(version_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.get("/{version_id}/audits")
def get_config_audits(
    version_id: str,
    service: ConfigService = Depends(get_config_service),
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.AUDITOR, OperatorRole.CONFIG_PUBLISHER)
    ),
) -> list[dict[str, Any]]:
    try:
        return service.get_audits(version_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.patch("/{version_id}")
def update_config_draft(
    version_id: str,
    request: UpdateDraftRequest,
    actor_id: str = Depends(resolve_config_actor),
    service: ConfigService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        return service.update_draft(version_id, request.payload, actor_id=actor_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.post("/{version_id}/validate")
def validate_config_version(
    version_id: str,
    actor_id: str = Depends(resolve_config_actor),
    service: ConfigService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        return service.validate_version(version_id, actor_id=actor_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.post("/{version_id}/publish")
def publish_config_version(
    version_id: str,
    actor_id: str = Depends(resolve_config_actor),
    service: ConfigService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        return service.publish_version(version_id, actor_id=actor_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc


@router.post("/{version_id}/retire")
def retire_config_version(
    version_id: str,
    actor_id: str = Depends(resolve_config_actor),
    service: ConfigService = Depends(get_config_service),
) -> dict[str, Any]:
    try:
        return service.retire_version(version_id, actor_id=actor_id)
    except ConfigDomainError as exc:
        raise _translate(exc) from exc
