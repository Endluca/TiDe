from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Callable, Generator, List, Optional, Sequence
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import and_, select
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


def _argon2_concurrency_limit() -> int:
    raw = os.getenv("TIT_ARGON2_MAX_CONCURRENCY", "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(1, min(value, 16))


_PASSWORD_VERIFY_SLOTS = threading.BoundedSemaphore(_argon2_concurrency_limit())


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


class _LoginRateLimiter:
    """Bounded per-process guard for the expensive password-hash path."""

    def __init__(
        self,
        *,
        attempts: int,
        window_seconds: int,
        max_keys: int,
    ) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, key: str, *, now: float | None = None) -> int | None:
        current_time = time.monotonic() if now is None else now
        cutoff = current_time - self.window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.attempts:
                self._buckets.move_to_end(key)
                return max(1, ceil(bucket[0] + self.window_seconds - current_time))
            bucket.append(current_time)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        return None


_LOGIN_RATE_LIMITER = _LoginRateLimiter(
    attempts=_bounded_int("TIT_LOGIN_RATE_LIMIT_ATTEMPTS", 10, 1, 100),
    window_seconds=_bounded_int(
        "TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        60,
        1,
        3_600,
    ),
    max_keys=_bounded_int("TIT_LOGIN_RATE_LIMIT_MAX_KEYS", 10_000, 100, 100_000),
)


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


def _authentication_capacity_exceeded() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "AUTHENTICATION_CAPACITY_EXCEEDED",
            "message": "登录请求过多，请稍后重试",
        },
        headers={"Retry-After": "1"},
    )


def _authentication_rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "AUTHENTICATION_RATE_LIMITED",
            "message": "登录尝试过于频繁，请稍后重试",
        },
        headers={"Retry-After": str(retry_after)},
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


def _authenticated_identity(
    db: Session,
    *,
    token_hash: str,
    current_time: datetime,
) -> OperatorIdentity | None:
    rows = db.execute(
        select(
            OperatorAccount.operator_id,
            OperatorAccount.username,
            OperatorAccount.display_name,
            OperatorRoleGrant.role,
        )
        .join(
            OperatorSession,
            OperatorSession.operator_id == OperatorAccount.operator_id,
        )
        .outerjoin(
            OperatorRoleGrant,
            and_(
                OperatorRoleGrant.operator_id == OperatorAccount.operator_id,
                OperatorRoleGrant.revoked_at.is_(None),
            ),
        )
        .where(
            OperatorSession.token_hash == token_hash,
            OperatorSession.revoked_at.is_(None),
            OperatorSession.expires_at > current_time,
            OperatorAccount.is_active.is_(True),
        )
        .order_by(OperatorRoleGrant.role)
    ).all()
    if not rows:
        return None
    valid_values = {role.value for role in OperatorRole}
    role_values = {
        str(row.role)
        for row in rows
        if row.role is not None and str(row.role) in valid_values
    }
    first = rows[0]
    return OperatorIdentity(
        operator_id=str(first.operator_id),
        username=str(first.username),
        display_name=first.display_name,
        roles=[OperatorRole(value) for value in sorted(role_values)],
    )


def current_operator(
    token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db_session),
) -> OperatorIdentity:
    if not token:
        raise _authentication_required()

    try:
        identity = _authenticated_identity(
            db,
            token_hash=hash_session_token(token),
            current_time=now_utc(),
        )
    finally:
        # The dependency object stays alive until the response is complete. End
        # the read transaction now so the authenticated request does not hold one
        # pool connection while its business service checks out another.
        db.rollback()
    if identity is None:
        raise _authentication_required()
    return identity


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
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
) -> OperatorIdentity:
    username = normalize_username(payload.username)
    client_host = request.client.host if request.client is not None else "unknown"
    limiter_key = hashlib.sha256(
        f"{client_host}\0{username}".encode("utf-8")
    ).hexdigest()
    retry_after = _LOGIN_RATE_LIMITER.retry_after(limiter_key)
    if retry_after is not None:
        raise _authentication_rate_limited(retry_after)
    account = db.scalar(select(OperatorAccount).where(OperatorAccount.username == username))
    password_hash = account.password_hash if account is not None and account.is_active else _DUMMY_PASSWORD_HASH
    password = payload.password.get_secret_value()
    if not _PASSWORD_VERIFY_SLOTS.acquire(blocking=False):
        raise _authentication_capacity_exceeded()
    try:
        valid_password = verify_password(password, password_hash)
        if (
            account is not None
            and account.is_active
            and valid_password
            and password_needs_rehash(account.password_hash)
        ):
            account.password_hash = hash_password(password)
    finally:
        _PASSWORD_VERIFY_SLOTS.release()

    if account is None or not account.is_active or not valid_password:
        raise _invalid_credentials()

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
