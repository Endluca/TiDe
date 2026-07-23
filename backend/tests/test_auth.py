from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import (
    COOKIE_NAME,
    OperatorIdentity,
    auth_router,
    get_db_session,
    hash_password,
    require_roles,
    verify_password,
)
from app.auth_models import OperatorAccount, OperatorRole, OperatorRoleGrant, OperatorSession
from app.database import Base


@dataclass
class AuthContext:
    client: TestClient
    sessions: sessionmaker


@pytest.fixture
def auth_context() -> Iterator[AuthContext]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/api/test/auditor")
    def auditor_only(
        operator: OperatorIdentity = Depends(require_roles(OperatorRole.AUDITOR)),
    ) -> dict:
        return {"operator_id": operator.operator_id}

    def override_db() -> Iterator[Session]:
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app) as client:
        yield AuthContext(client=client, sessions=sessions)
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_account(context: AuthContext, *roles: OperatorRole) -> OperatorAccount:
    with context.sessions() as db:
        account = OperatorAccount(
            operator_id=str(uuid4()),
            username="ops.user",
            display_name="Ops User",
            password_hash=hash_password("valid-password-123"),
            is_active=True,
        )
        db.add(account)
        db.flush()
        for role in roles:
            db.add(
                OperatorRoleGrant(
                    grant_id=str(uuid4()),
                    operator_id=account.operator_id,
                    role=role.value,
                )
            )
        db.commit()
        return account


def test_argon2_password_hash_never_stores_plaintext() -> None:
    raw = "never-store-this-password"
    first = hash_password(raw)
    second = hash_password(raw)

    assert first.startswith("$argon2id$")
    assert first != raw
    assert second != first
    assert verify_password(raw, first)
    assert not verify_password("incorrect", first)


def test_login_sets_strict_httponly_cookie_and_stores_only_sha256_hash(
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    account = create_account(auth_context, OperatorRole.VIEWER)

    response = auth_context.client.post(
        "/api/auth/login",
        json={"username": "  OPS.USER ", "password": "valid-password-123"},
    )

    assert response.status_code == 200
    assert response.json()["operator_id"] == account.operator_id
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Secure" not in set_cookie

    raw_token = auth_context.client.cookies.get(COOKIE_NAME)
    assert raw_token
    with auth_context.sessions() as db:
        stored = db.scalar(select(OperatorSession))
        assert stored is not None
        assert stored.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        assert raw_token not in stored.token_hash

    me = auth_context.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["VIEWER"]


def test_unknown_username_and_wrong_password_have_identical_error(auth_context: AuthContext) -> None:
    create_account(auth_context, OperatorRole.VIEWER)

    unknown = auth_context.client.post(
        "/api/auth/login",
        json={"username": "unknown.user", "password": "wrong-password"},
    )
    wrong = auth_context.client.post(
        "/api/auth/login",
        json={"username": "ops.user", "password": "wrong-password"},
    )

    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_role_dependency_rejects_authenticated_operator_without_role(auth_context: AuthContext) -> None:
    create_account(auth_context, OperatorRole.VIEWER)
    logged_in = auth_context.client.post(
        "/api/auth/login",
        json={"username": "ops.user", "password": "valid-password-123"},
    )
    assert logged_in.status_code == 200

    denied = auth_context.client.get("/api/test/auditor")

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "ROLE_REQUIRED"


def test_logout_revokes_server_session_and_clears_cookie(auth_context: AuthContext) -> None:
    create_account(auth_context, OperatorRole.AUDITOR)
    logged_in = auth_context.client.post(
        "/api/auth/login",
        json={"username": "ops.user", "password": "valid-password-123"},
    )
    assert logged_in.status_code == 200

    logged_out = auth_context.client.post("/api/auth/logout")

    assert logged_out.status_code == 200
    assert logged_out.json() == {"status": "logged_out"}
    assert auth_context.client.cookies.get(COOKIE_NAME) is None
    with auth_context.sessions() as db:
        stored = db.scalar(select(OperatorSession))
        assert stored is not None
        assert stored.revoked_at is not None
    assert auth_context.client.get("/api/auth/me").status_code == 401


def test_production_cookie_is_secure(
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    create_account(auth_context, OperatorRole.VIEWER)

    response = auth_context.client.post(
        "/api/auth/login",
        json={"username": "ops.user", "password": "valid-password-123"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_unset_environment_cookie_fails_closed_as_secure(
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    create_account(auth_context, OperatorRole.VIEWER)

    response = auth_context.client.post(
        "/api/auth/login",
        json={"username": "ops.user", "password": "valid-password-123"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
