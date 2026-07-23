from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .config_models import (
    CONFIG_SCHEMA_BY_KEY,
    DEFAULT_CONFIG_PAYLOADS,
    HIGH_IMPACT_CONFIG_KEYS,
    AgentPolicyConfig,
    ConfigKey,
    ConfigPublicationAuditRecord,
    ConfigStatus,
    ConfigVersionRecord,
)
from .database import SessionLocal


SessionFactory = Callable[[], Session]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": ".".join(str(part) for part in item["loc"]),
            "type": item["type"],
            "message": item["msg"],
        }
        for item in exc.errors()
    ]


def validate_config_payload(config_key: ConfigKey | str, payload: dict[str, Any]) -> dict[str, Any]:
    key = ConfigKey(config_key)
    schema = CONFIG_SCHEMA_BY_KEY[key]
    return schema.model_validate(payload).model_dump(mode="json")


def is_agent_effectively_enabled(payload: dict[str, Any]) -> bool:
    policy = AgentPolicyConfig.model_validate(payload)
    return policy.effective_enabled


class ConfigDomainError(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or []

    def response(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ConfigService:
    def __init__(self, session_factory: SessionFactory = SessionLocal) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _version_dict(record: ConfigVersionRecord) -> dict[str, Any]:
        return {
            "version_id": record.version_id,
            "config_key": record.config_key,
            "version_number": record.version_number,
            "status": record.status,
            "high_impact": record.high_impact,
            "payload": deepcopy(record.payload),
            "validation_errors": deepcopy(record.validation_errors),
            "source_version_id": record.source_version_id,
            "created_by": record.created_by,
            "updated_by": record.updated_by,
            "validated_by": record.validated_by,
            "published_by": record.published_by,
            "retired_by": record.retired_by,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "validated_at": record.validated_at.isoformat() if record.validated_at else None,
            "published_at": record.published_at.isoformat() if record.published_at else None,
            "retired_at": record.retired_at.isoformat() if record.retired_at else None,
        }

    @staticmethod
    def _audit_dict(record: ConfigPublicationAuditRecord) -> dict[str, Any]:
        return {
            "audit_id": record.audit_id,
            "version_id": record.version_id,
            "config_key": record.config_key,
            "action": record.action,
            "actor_id": record.actor_id,
            "from_status": record.from_status,
            "to_status": record.to_status,
            "payload_hash": record.payload_hash,
            "detail": record.detail,
            "occurred_at": record.occurred_at.isoformat(),
        }

    @staticmethod
    def _audit(
        session: Session,
        record: ConfigVersionRecord,
        *,
        action: str,
        actor_id: str,
        from_status: str | None,
        to_status: str,
        detail: str = "",
    ) -> None:
        session.add(
            ConfigPublicationAuditRecord(
                audit_id=f"CFGAUD-{uuid4().hex}",
                version_id=record.version_id,
                config_key=record.config_key,
                action=action,
                actor_id=actor_id,
                from_status=from_status,
                to_status=to_status,
                payload_hash=_payload_hash(record.payload),
                detail=detail,
                occurred_at=utcnow(),
            )
        )

    @staticmethod
    def _get_locked(session: Session, version_id: str) -> ConfigVersionRecord:
        record = session.scalar(
            select(ConfigVersionRecord)
            .where(ConfigVersionRecord.version_id == version_id)
            .with_for_update()
        )
        if record is None:
            raise ConfigDomainError(404, "CONFIG_VERSION_NOT_FOUND", "配置版本不存在")
        return record

    def list_versions(
        self,
        config_key: ConfigKey | str | None = None,
        status: ConfigStatus | str | None = None,
    ) -> list[dict[str, Any]]:
        query: Select[tuple[ConfigVersionRecord]] = select(ConfigVersionRecord)
        if config_key is not None:
            query = query.where(ConfigVersionRecord.config_key == ConfigKey(config_key).value)
        if status is not None:
            query = query.where(ConfigVersionRecord.status == ConfigStatus(status).value)
        query = query.order_by(ConfigVersionRecord.config_key, ConfigVersionRecord.version_number.desc())
        with self.session_factory() as session:
            return [self._version_dict(item) for item in session.scalars(query).all()]

    def get_version(self, version_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            record = session.get(ConfigVersionRecord, version_id)
            if record is None:
                raise ConfigDomainError(404, "CONFIG_VERSION_NOT_FOUND", "配置版本不存在")
            return self._version_dict(record)

    def get_audits(self, version_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            if session.get(ConfigVersionRecord, version_id) is None:
                raise ConfigDomainError(404, "CONFIG_VERSION_NOT_FOUND", "配置版本不存在")
            records = session.scalars(
                select(ConfigPublicationAuditRecord)
                .where(ConfigPublicationAuditRecord.version_id == version_id)
                .order_by(ConfigPublicationAuditRecord.occurred_at)
            ).all()
            return [self._audit_dict(item) for item in records]

    def create_draft(
        self,
        config_key: ConfigKey | str,
        *,
        actor_id: str,
        payload: dict[str, Any] | None = None,
        from_version_id: str | None = None,
    ) -> dict[str, Any]:
        key = ConfigKey(config_key)
        with self.session_factory() as session, session.begin():
            source: ConfigVersionRecord | None = None
            if from_version_id:
                source = session.get(ConfigVersionRecord, from_version_id)
                if source is None or source.config_key != key.value:
                    raise ConfigDomainError(400, "INVALID_SOURCE_VERSION", "来源版本不存在或配置域不一致")
            elif payload is None:
                source = session.scalar(
                    select(ConfigVersionRecord)
                    .where(
                        ConfigVersionRecord.config_key == key.value,
                        ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
                    )
                    .order_by(ConfigVersionRecord.version_number.desc())
                )
            resolved_payload = deepcopy(payload if payload is not None else source.payload if source else None)
            if resolved_payload is None:
                raise ConfigDomainError(
                    409,
                    "CONFIG_NOT_SEEDED",
                    "该配置域尚无已发布版本；请先显式执行默认配置 seed，或提交初始 payload",
                )
            try:
                resolved_payload = validate_config_payload(key, resolved_payload)
            except ValidationError as exc:
                raise ConfigDomainError(
                    422,
                    "CONFIG_PAYLOAD_REJECTED",
                    "配置内容不符合受控字段和类型，未写入数据库",
                    details=_safe_validation_errors(exc),
                ) from exc
            latest = session.scalar(
                select(ConfigVersionRecord)
                .where(ConfigVersionRecord.config_key == key.value)
                .order_by(ConfigVersionRecord.version_number.desc())
                .limit(1)
                .with_for_update()
            )
            version_number = (latest.version_number if latest else 0) + 1
            now = utcnow()
            record = ConfigVersionRecord(
                version_id=f"CFG-{key.value}-{version_number:04d}-{uuid4().hex[:8]}",
                config_key=key.value,
                version_number=version_number,
                status=ConfigStatus.DRAFT.value,
                high_impact=key in HIGH_IMPACT_CONFIG_KEYS,
                payload=resolved_payload,
                validation_errors=[],
                source_version_id=source.version_id if source else None,
                created_by=actor_id,
                updated_by=actor_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            self._audit(
                session,
                record,
                action="CREATE_DRAFT",
                actor_id=actor_id,
                from_status=None,
                to_status=ConfigStatus.DRAFT.value,
            )
            return self._version_dict(record)

    def update_draft(
        self,
        version_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = self._get_locked(session, version_id)
            if record.status != ConfigStatus.DRAFT.value:
                raise ConfigDomainError(409, "CONFIG_NOT_EDITABLE", "只有 DRAFT 状态可以编辑")
            try:
                normalized_payload = validate_config_payload(record.config_key, payload)
            except ValidationError as exc:
                raise ConfigDomainError(
                    422,
                    "CONFIG_PAYLOAD_REJECTED",
                    "配置内容不符合受控字段和类型，未写入数据库",
                    details=_safe_validation_errors(exc),
                ) from exc
            record.payload = normalized_payload
            record.validation_errors = []
            record.updated_by = actor_id
            record.updated_at = utcnow()
            self._audit(
                session,
                record,
                action="UPDATE_DRAFT",
                actor_id=actor_id,
                from_status=ConfigStatus.DRAFT.value,
                to_status=ConfigStatus.DRAFT.value,
            )
            session.flush()
            return self._version_dict(record)

    def validate_version(self, version_id: str, *, actor_id: str) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = self._get_locked(session, version_id)
            if record.status != ConfigStatus.DRAFT.value:
                raise ConfigDomainError(409, "INVALID_CONFIG_TRANSITION", "只有 DRAFT 可以进入校验")
            try:
                normalized = validate_config_payload(record.config_key, record.payload)
            except ValidationError as exc:
                errors = _safe_validation_errors(exc)
                record.validation_errors = errors
                record.updated_by = actor_id
                record.updated_at = utcnow()
                self._audit(
                    session,
                    record,
                    action="VALIDATION_FAILED",
                    actor_id=actor_id,
                    from_status=ConfigStatus.DRAFT.value,
                    to_status=ConfigStatus.DRAFT.value,
                    detail="; ".join(item["message"] for item in errors),
                )
                session.flush()
                return {"valid": False, "errors": errors, "version": self._version_dict(record)}
            now = utcnow()
            record.payload = normalized
            record.validation_errors = []
            record.status = ConfigStatus.VALIDATED.value
            record.validated_by = actor_id
            record.validated_at = now
            record.updated_by = actor_id
            record.updated_at = now
            self._audit(
                session,
                record,
                action="VALIDATE",
                actor_id=actor_id,
                from_status=ConfigStatus.DRAFT.value,
                to_status=ConfigStatus.VALIDATED.value,
            )
            session.flush()
            return {"valid": True, "errors": [], "version": self._version_dict(record)}

    def publish_version(self, version_id: str, *, actor_id: str) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = self._get_locked(session, version_id)
            if record.status == ConfigStatus.PUBLISHED.value:
                return self._version_dict(record)
            if record.status != ConfigStatus.VALIDATED.value:
                raise ConfigDomainError(409, "INVALID_CONFIG_TRANSITION", "只有 VALIDATED 可以发布")
            if record.high_impact and record.created_by == actor_id:
                raise ConfigDomainError(
                    409,
                    "FOUR_EYES_REQUIRED",
                    "高影响配置必须由不同于草稿创建人的账号发布",
                )
            try:
                record.payload = validate_config_payload(record.config_key, record.payload)
            except ValidationError as exc:
                raise ConfigDomainError(
                    422,
                    "CONFIG_VALIDATION_FAILED",
                    "配置已不符合当前校验规则",
                    details=_safe_validation_errors(exc),
                ) from exc

            now = utcnow()
            current = session.scalar(
                select(ConfigVersionRecord)
                .where(
                    ConfigVersionRecord.config_key == record.config_key,
                    ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
                    ConfigVersionRecord.version_id != record.version_id,
                )
                .with_for_update()
            )
            if (
                current is not None
                and record.config_key == ConfigKey.SCORE_GRADUATION.value
                and isinstance(current.payload, dict)
                and isinstance(record.payload, dict)
            ):
                policy_rank = {"v2": 2, "v3": 3, "v4": 4, "v5": 5, "v6": 6}
                current_rank = policy_rank.get(str(current.payload.get("policy_version")), 0)
                candidate_rank = policy_rank.get(str(record.payload.get("policy_version")), 0)
                if current_rank and candidate_rank < current_rank:
                    raise ConfigDomainError(
                        409,
                        "SCORE_POLICY_DOWNGRADE_FORBIDDEN",
                        "积分语义版本只能向前发布，不能重新发布已退休的历史版本",
                    )
            if current is not None:
                current.status = ConfigStatus.RETIRED.value
                current.retired_by = actor_id
                current.retired_at = now
                current.updated_by = actor_id
                current.updated_at = now
                self._audit(
                    session,
                    current,
                    action="RETIRE_SUPERSEDED",
                    actor_id=actor_id,
                    from_status=ConfigStatus.PUBLISHED.value,
                    to_status=ConfigStatus.RETIRED.value,
                    detail=f"被 {record.version_id} 替代",
                )
                # Partial unique index permits only one PUBLISHED row. Flush the retirement
                # before promoting the replacement so PostgreSQL never observes two rows.
                session.flush()

            record.status = ConfigStatus.PUBLISHED.value
            record.published_by = actor_id
            record.published_at = now
            record.updated_by = actor_id
            record.updated_at = now
            self._audit(
                session,
                record,
                action="PUBLISH",
                actor_id=actor_id,
                from_status=ConfigStatus.VALIDATED.value,
                to_status=ConfigStatus.PUBLISHED.value,
            )
            session.flush()
            return self._version_dict(record)

    def retire_version(self, version_id: str, *, actor_id: str) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = self._get_locked(session, version_id)
            if record.status != ConfigStatus.PUBLISHED.value:
                raise ConfigDomainError(409, "INVALID_CONFIG_TRANSITION", "只有 PUBLISHED 可以退役")
            raise ConfigDomainError(
                409,
                "PUBLISHED_CONFIG_REPLACEMENT_REQUIRED",
                "已发布运行配置不能被单独移除；请创建、校验并双人发布替代版本",
            )

    def get_published_payload(self, config_key: ConfigKey | str) -> dict[str, Any] | None:
        key = ConfigKey(config_key)
        with self.session_factory() as session:
            record = session.scalar(
                select(ConfigVersionRecord)
                .where(
                    ConfigVersionRecord.config_key == key.value,
                    ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
                )
                .order_by(ConfigVersionRecord.version_number.desc())
            )
            return deepcopy(record.payload) if record else None


def get_published_payload(
    config_key: ConfigKey | str,
    *,
    session_factory: SessionFactory = SessionLocal,
) -> dict[str, Any] | None:
    """运行时读取唯一已发布配置；空库明确返回 None，不隐式创建默认值。"""

    return ConfigService(session_factory).get_published_payload(config_key)


def seed_default_configs(
    *,
    session_factory: SessionFactory = SessionLocal,
    creator_actor_id: str = "system:config-seed-creator",
    publisher_actor_id: str = "system:config-seed-publisher",
) -> dict[str, list[str]]:
    """显式写入并发布默认配置。API 读取空库时不会调用此函数。"""

    if creator_actor_id == publisher_actor_id:
        raise ValueError("默认配置 seed 也必须使用不同的创建人与发布人")
    service = ConfigService(session_factory)
    created: list[str] = []
    skipped: list[str] = []
    for key, payload in DEFAULT_CONFIG_PAYLOADS.items():
        if service.get_published_payload(key) is not None:
            skipped.append(key.value)
            continue
        draft = service.create_draft(
            key,
            actor_id=creator_actor_id,
            payload=deepcopy(payload),
        )
        validation = service.validate_version(draft["version_id"], actor_id=creator_actor_id)
        if not validation["valid"]:
            raise RuntimeError(f"默认配置 {key.value} 未通过内置校验: {validation['errors']}")
        published = service.publish_version(draft["version_id"], actor_id=publisher_actor_id)
        created.append(published["version_id"])
    return {"created": created, "skipped": skipped}
