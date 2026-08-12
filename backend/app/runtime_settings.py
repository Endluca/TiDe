from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

from .qualification_award_gate import (
    QualificationAwardGateConfigurationError,
    irreversible_qualification_grants_enabled,
)


PRODUCTION_ENVIRONMENTS = {"prod", "production"}
LOCAL_ALLOWED_ORIGINS = (
    "http://localhost:5174",
    "http://127.0.0.1:5174",
)
LOCAL_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "testserver")
PRODUCTION_INTEGER_SETTINGS = {
    "TIT_DB_POOL_SIZE": (1, 100),
    "TIT_DB_MAX_OVERFLOW": (0, 100),
    "TIT_DB_POOL_TIMEOUT_SECONDS": (1, 120),
    "TIT_DB_POOL_RECYCLE_SECONDS": (60, 86_400),
    "TIT_DB_CONNECT_TIMEOUT_SECONDS": (1, 60),
    "TIT_DB_LOCK_TIMEOUT_MS": (1, 300_000),
    "TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS": (1_000, 3_600_000),
    "TIT_DB_STATEMENT_TIMEOUT_MS": (0, 3_600_000),
    "TIT_ARGON2_MAX_CONCURRENCY": (1, 16),
    "TIT_LOGIN_RATE_LIMIT_ATTEMPTS": (1, 100),
    "TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS": (1, 3_600),
    "TIT_LOGIN_RATE_LIMIT_MAX_KEYS": (100, 100_000),
    "TIT_SLOW_REQUEST_MS": (100, 60_000),
    "TIT_API_WORKERS": (1, 8),
    "TIT_API_LIMIT_CONCURRENCY": (1, 256),
    "TIT_API_KEEPALIVE_SECONDS": (1, 120),
}


def app_environment() -> str:
    return os.getenv("APP_ENV", "local").strip().lower()


def is_production() -> bool:
    return app_environment() in PRODUCTION_ENVIRONMENTS


def is_production_migration() -> bool:
    return is_production() and os.getenv("TIT_MIGRATION_MODE", "").strip() == "true"


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
    try:
        irreversible_qualification_grants_enabled()
    except QualificationAwardGateConfigurationError as exc:
        errors.append(str(exc))
    hosts = allowed_hosts()
    if not hosts:
        errors.append("TIT_ALLOWED_HOSTS is required")
    healthcheck_host = os.getenv("TIT_HEALTHCHECK_HOST", "").strip()
    if not healthcheck_host:
        errors.append("TIT_HEALTHCHECK_HOST is required")
    elif healthcheck_host not in hosts:
        errors.append("TIT_HEALTHCHECK_HOST must be included in TIT_ALLOWED_HOSTS")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        errors.append("DATABASE_URL is required")
    elif not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        errors.append("DATABASE_URL must use PostgreSQL")
    else:
        parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
        ssl_modes = parse_qs(
            parsed.query,
            keep_blank_values=True,
        ).get("sslmode", [])
        if len(ssl_modes) != 1:
            errors.append("DATABASE_URL must contain exactly one sslmode")
        elif ssl_modes[0].lower() not in {"verify-ca", "verify-full"}:
            errors.append(
                "DATABASE_URL sslmode must be verify-ca or verify-full"
            )

    for name, (minimum, maximum) in PRODUCTION_INTEGER_SETTINGS.items():
        raw = os.getenv(name, "").strip()
        if not raw:
            errors.append(f"{name} is required")
            continue
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{name} must be an integer")
            continue
        if not minimum <= value <= maximum:
            errors.append(f"{name} must be between {minimum} and {maximum}")

    if errors:
        raise RuntimeError(
            "Unsafe production runtime configuration: " + "; ".join(errors)
        )


def validate_production_migration_runtime() -> None:
    """Keep a release-only Alembic process independent from API secrets."""

    if not is_production_migration():
        return

    errors: list[str] = []
    database_url = os.getenv("DATABASE_URL", "").strip()
    expected_database = os.getenv(
        "TIT_MIGRATION_EXPECTED_DATABASE",
        "",
    ).strip()
    if not database_url:
        errors.append("DATABASE_URL is required")
    elif not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        errors.append("DATABASE_URL must use PostgreSQL")
    else:
        parsed = urlparse(
            database_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )
        )
        ssl_modes = parse_qs(
            parsed.query,
            keep_blank_values=True,
        ).get("sslmode", [])
        if ssl_modes != ["verify-full"]:
            errors.append(
                "DATABASE_URL must contain exactly one sslmode=verify-full"
            )
        if "ssl" in parse_qs(parsed.query, keep_blank_values=True):
            errors.append("DATABASE_URL must not contain conflicting ssl")
        url_database = parsed.path.removeprefix("/")
        if expected_database and url_database != expected_database:
            errors.append(
                "DATABASE_URL database must match TIT_MIGRATION_EXPECTED_DATABASE"
            )

    if not expected_database:
        errors.append("TIT_MIGRATION_EXPECTED_DATABASE is required")

    if errors:
        raise RuntimeError(
            "Unsafe production migration configuration: " + "; ".join(errors)
        )


def validate_alembic_runtime() -> None:
    if is_production():
        if not is_production_migration():
            raise RuntimeError(
                "Production Alembic requires TIT_MIGRATION_MODE=true"
            )
        validate_production_migration_runtime()
        return
    validate_production_runtime()


def validate_production_migration_identity(
    *,
    role: str,
    database: str,
    is_superuser: bool,
) -> None:
    expected_database = os.environ["TIT_MIGRATION_EXPECTED_DATABASE"]
    if (
        role != "tide_sys_admin"
        or database != expected_database
        or is_superuser
    ):
        raise RuntimeError(
            "Production migration requires non-superuser "
            "tide_sys_admin on the explicitly selected database"
        )
