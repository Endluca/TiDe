from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime_settings import (
    allowed_hosts,
    allowed_origins,
    validate_production_runtime,
)


def test_local_runtime_uses_loopback_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("TIT_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("TIT_ALLOWED_ORIGINS", raising=False)

    assert "127.0.0.1" in allowed_hosts()
    assert "http://127.0.0.1:5174" in allowed_origins()
    validate_production_runtime()


def test_production_runtime_fails_closed_without_tls_and_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TIT_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=disable",
    )

    with pytest.raises(RuntimeError, match="TIT_ALLOWED_HOSTS"):
        validate_production_runtime()


def test_production_runtime_accepts_verified_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=verify-full",
    )

    validate_production_runtime()


def test_production_example_matches_same_origin_proxy_contract() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for line in (backend_root / ".env.production.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    nginx = (backend_root.parent / "frontend" / "nginx.conf").read_text()

    assert "proxy_set_header Host $host;" in nginx
    assert environment["TIT_ALLOWED_HOSTS"] == "tit-growth.example.com"
    assert environment["TIT_ALLOWED_ORIGINS"] == ""
