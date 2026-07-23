from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Generator, List, Optional, Sequence
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth_models import OperatorAccount, OperatorRole, OperatorRoleGrant, OperatorSession
from .database import SessionLocal


COOKIE_NAME = "tit_operator_session"
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
# A real Argon2 verification is performed even when the username does not exist.
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: SecretStr


class OperatorIdentity(BaseModel):
    operator_id: str
    username: str
    display_name: Optional[str]
    roles: List[OperatorRole]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


def password_needs_rehash(encoded_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(encoded_hash)
    except (VerificationError, ValueError):
        return True


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_secure() -> bool:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return app_env not in {"local", "dev", "development", "test"}


def session_ttl_seconds() -> int:
    raw = os.getenv("TIT_SESSION_TTL_HOURS", "8")
    try:
        hours = int(raw)
    except ValueError:
        hours = 8
    return max(1, min(hours, 24 * 7)) * 60 * 60


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "INVALID_CREDENTIALS", "message": "账号或密码错误"},
    )


def _authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTHENTICATION_REQUIRED", "message": "请先登录"},
    )


def _identity_for(db: Session, account: OperatorAccount) -> OperatorIdentity:
    role_values = db.scalars(
        select(OperatorRoleGrant.role)
        .where(OperatorRoleGrant.operator_id == account.operator_id)
        .where(OperatorRoleGrant.revoked_at.is_(None))
        .order_by(OperatorRoleGrant.role)
    ).all()
    valid_values = {role.value for role in OperatorRole}
    roles = [OperatorRole(value) for value in role_values if value in valid_values]
    return OperatorIdentity(
        operator_id=account.operator_id,
        username=account.username,
        display_name=account.display_name,
        roles=roles,
    )


def current_operator(
    token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db_session),
) -> OperatorIdentity:
    if not token:
        raise _authentication_required()

    current_time = now_utc()
    session_record = db.scalar(
        select(OperatorSession)
        .where(OperatorSession.token_hash == hash_session_token(token))
        .where(OperatorSession.revoked_at.is_(None))
        .where(OperatorSession.expires_at > current_time)
    )
    if session_record is None:
        raise _authentication_required()

    account = db.get(OperatorAccount, session_record.operator_id)
    if account is None or not account.is_active:
        raise _authentication_required()
    return _identity_for(db, account)


def require_roles(*allowed_roles: OperatorRole) -> Callable[..., OperatorIdentity]:
    if not allowed_roles:
        raise ValueError("require_roles needs at least one role")
    normalized = {
        role if isinstance(role, OperatorRole) else OperatorRole(role)
        for role in allowed_roles
    }

    def dependency(operator: OperatorIdentity = Depends(current_operator)) -> OperatorIdentity:
        if normalized.isdisjoint(set(operator.roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ROLE_REQUIRED", "message": "当前账号没有执行此操作的权限"},
            )
        return operator

    return dependency


router = APIRouter(prefix="/api/auth", tags=["operator-auth"])


@router.post("/login", response_model=OperatorIdentity)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db_session)) -> OperatorIdentity:
    username = normalize_username(payload.username)
    account = db.scalar(select(OperatorAccount).where(OperatorAccount.username == username))
    password_hash = account.password_hash if account is not None and account.is_active else _DUMMY_PASSWORD_HASH
    password = payload.password.get_secret_value()
    valid_password = verify_password(password, password_hash)

    if account is None or not account.is_active or not valid_password:
        raise _invalid_credentials()

    if password_needs_rehash(account.password_hash):
        account.password_hash = hash_password(password)

    raw_token = secrets.token_urlsafe(48)
    current_time = now_utc()
    ttl_seconds = session_ttl_seconds()
    db.add(
        OperatorSession(
            session_id=str(uuid4()),
            operator_id=account.operator_id,
            token_hash=hash_session_token(raw_token),
            created_at=current_time,
            expires_at=current_time + timedelta(seconds=ttl_seconds),
            last_seen_at=current_time,
        )
    )
    db.commit()
    response.set_cookie(
        key=COOKIE_NAME,
        value=raw_token,
        max_age=ttl_seconds,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )
    return _identity_for(db, account)


@router.post("/logout")
def logout(
    response: Response,
    token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db_session),
) -> dict:
    if token:
        session_record = db.scalar(
            select(OperatorSession).where(OperatorSession.token_hash == hash_session_token(token))
        )
        if session_record is not None and session_record.revoked_at is None:
            session_record.revoked_at = now_utc()
            db.commit()
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )
    return {"status": "logged_out"}


@router.get("/me", response_model=OperatorIdentity)
def me(operator: OperatorIdentity = Depends(current_operator)) -> OperatorIdentity:
    return operator


auth_router = router
