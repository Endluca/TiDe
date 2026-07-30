from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OperatorRole(str, Enum):
    VIEWER = "VIEWER"
    CASE_OPERATOR = "CASE_OPERATOR"
    SENIOR_REVIEWER = "SENIOR_REVIEWER"
    CONFIG_PUBLISHER = "CONFIG_PUBLISHER"
    EXTERNAL_ACTION_APPROVER = "EXTERNAL_ACTION_APPROVER"
    AUDITOR = "AUDITOR"


ROLE_VALUES = tuple(role.value for role in OperatorRole)
ROLE_CHECK_SQL = "role IN ({})".format(", ".join("'{}'".format(role) for role in ROLE_VALUES))


class OperatorAccount(Base):
    __tablename__ = "operator_accounts"

    operator_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class OperatorRoleGrant(Base):
    __tablename__ = "operator_role_grants"
    __table_args__ = (
        UniqueConstraint("operator_id", "role", name="uq_operator_role_grant"),
        CheckConstraint(ROLE_CHECK_SQL, name="ck_operator_role_valid"),
    )

    grant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operator_id: Mapped[str] = mapped_column(
        ForeignKey("operator_accounts.operator_id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OperatorSession(Base):
    __tablename__ = "operator_sessions"
    __table_args__ = (
        Index("ix_operator_sessions_operator_active", "operator_id", "revoked_at", "expires_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operator_id: Mapped[str] = mapped_column(
        ForeignKey("operator_accounts.operator_id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
