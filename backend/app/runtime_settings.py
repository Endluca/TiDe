from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from .qualification_award_gate import (
    QualificationAwardGateConfigurationError,
    irreversible_qualification_grants_enabled,
)


PRODUCTION_ENVIRONMENTS = {"prod", "production"}
PRIVATE_LINE_DATABASE = "tide_system_test"
PRIVATE_LINE_HOST = "tide-system.rwlb.singapore.rds.aliyuncs.com"
PRIVATE_LINE_PORT = 5432
DATABASE_TRANSPORT_VERIFIED_TLS = "verified-tls"
DATABASE_TRANSPORT_PRE_PRIVATE_LINE_PLAINTEXT = "pre-private-line-plaintext"
LIBPQ_CONNECTION_IDENTITY_ENV = frozenset(
    {
        "PGDATABASE",
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGUSER",
    }
)
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


def _is_exact_private_line_url(
    parsed: ParseResult,
    *,
    role: str,
) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity_override_keys = {
        "database",
        "dbname",
        "host",
        "hostaddr",
        "options",
        "port",
        "service",
        "servicefile",
        "user",
    }
    return (
        unquote(parsed.username or "") == role
        and parsed.hostname == PRIVATE_LINE_HOST
        and port == PRIVATE_LINE_PORT
        and unquote(parsed.path.removeprefix("/")) == PRIVATE_LINE_DATABASE
        and not identity_override_keys.intersection(query)
    )


def reject_ambient_libpq_connection_identity(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Prevent libpq from silently replacing the approved PRE endpoint."""

    values = os.environ if environ is None else environ
    present = sorted(
        name for name in LIBPQ_CONNECTION_IDENTITY_ENV if name in values
    )
    if present:
        raise ValueError(
            "libpq connection identity environment variables must be unset: "
            + ", ".join(present)
        )


def _production_database_transport_mode(
    database_url: str,
    *,
    private_line_role: str,
    accepted_tls_modes: frozenset[str] = frozenset({"verify-full"}),
) -> str:
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("DATABASE_URL must use PostgreSQL")
    parsed = urlparse(
        database_url.replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "ssl" in query:
        raise ValueError("DATABASE_URL must not contain conflicting ssl")
    ssl_modes = query.get("sslmode", [])
    if len(ssl_modes) == 1 and ssl_modes[0] in accepted_tls_modes:
        return DATABASE_TRANSPORT_VERIFIED_TLS
    if ssl_modes == ["disable"] and _is_exact_private_line_url(
        parsed,
        role=private_line_role,
    ):
        reject_ambient_libpq_connection_identity()
        return DATABASE_TRANSPORT_PRE_PRIVATE_LINE_PLAINTEXT
    raise ValueError(
        "DATABASE_URL must contain exactly one sslmode=verify-full; "
        "sslmode=disable is limited to the approved tide_system_test endpoint"
    )


def operations_database_transport_mode(database_url: str) -> str:
    """Classify the operations/API URL without duplicating the PRE allowlist."""

    return _production_database_transport_mode(
        database_url,
        private_line_role="tit_growth_app",
        accepted_tls_modes=frozenset({"verify-ca", "verify-full"}),
    )


def source_worker_database_transport_mode(database_url: str) -> str:
    """Classify the SourceWide URL while preserving its verify-full rule."""

    return _production_database_transport_mode(
        database_url,
        private_line_role="tit_growth_app",
    )


def migration_database_transport_mode(database_url: str) -> str:
    """Classify the one-shot migration URL using its dedicated admin role."""

    return _production_database_transport_mode(
        database_url,
        private_line_role="tide_sys_admin",
    )


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
        try:
            operations_database_transport_mode(database_url)
        except ValueError as exc:
            errors.append(str(exc))

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
        try:
            migration_database_transport_mode(database_url)
        except ValueError as exc:
            errors.append(str(exc))
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


def validate_production_migration_transport(
    *,
    session_ssl: bool,
    server_ssl: str,
) -> None:
    database_url = os.environ["DATABASE_URL"]
    transport_mode = migration_database_transport_mode(database_url)

    normalized_server_ssl = server_ssl.strip().lower()
    if transport_mode == DATABASE_TRANSPORT_PRE_PRIVATE_LINE_PLAINTEXT:
        if session_ssl or normalized_server_ssl != "off":
            raise RuntimeError(
                "Private-line plaintext migration requires a "
                "non-TLS session and PostgreSQL server ssl=off"
            )
        return

    if not session_ssl or normalized_server_ssl != "on":
        raise RuntimeError(
            "Production migration requires an actual TLS session and "
            "PostgreSQL server ssl=on"
        )
