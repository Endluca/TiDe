from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse


PRODUCTION_ENVIRONMENTS = {"prod", "production"}
LOCAL_ALLOWED_ORIGINS = (
    "http://localhost:5174",
    "http://127.0.0.1:5174",
)
LOCAL_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "testserver")


def app_environment() -> str:
    return os.getenv("APP_ENV", "local").strip().lower()


def is_production() -> bool:
    return app_environment() in PRODUCTION_ENVIRONMENTS


def _csv_environment(name: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    )


def allowed_origins() -> tuple[str, ...]:
    configured = _csv_environment("TIT_ALLOWED_ORIGINS")
    if configured:
        return configured
    return () if is_production() else LOCAL_ALLOWED_ORIGINS


def allowed_hosts() -> tuple[str, ...]:
    configured = _csv_environment("TIT_ALLOWED_HOSTS")
    if configured:
        return configured
    return () if is_production() else LOCAL_ALLOWED_HOSTS


def validate_production_runtime() -> None:
    """Fail closed when a production process is missing minimum safeguards."""

    if not is_production():
        return

    errors: list[str] = []
    hosts = allowed_hosts()
    if not hosts:
        errors.append("TIT_ALLOWED_HOSTS is required")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        errors.append("DATABASE_URL is required")
    elif not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        errors.append("DATABASE_URL must use PostgreSQL")
    else:
        parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
        ssl_mode = (parse_qs(parsed.query).get("sslmode") or [""])[0].lower()
        if ssl_mode not in {"verify-ca", "verify-full"}:
            errors.append(
                "DATABASE_URL sslmode must be verify-ca or verify-full"
            )

    if errors:
        raise RuntimeError(
            "Unsafe production runtime configuration: " + "; ".join(errors)
        )
