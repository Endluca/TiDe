from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.database import build_engine
from app.runtime_settings import (
    PRODUCTION_INTEGER_SETTINGS,
    allowed_hosts,
    allowed_origins,
    operations_database_transport_mode,
    source_worker_database_transport_mode,
    validate_alembic_runtime,
    validate_production_migration_runtime,
    validate_production_migration_identity,
    validate_production_migration_transport,
    validate_production_runtime,
)


def set_safe_database_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "TIT_DB_POOL_SIZE": "5",
        "TIT_DB_MAX_OVERFLOW": "2",
        "TIT_DB_POOL_TIMEOUT_SECONDS": "5",
        "TIT_DB_POOL_RECYCLE_SECONDS": "1800",
        "TIT_DB_CONNECT_TIMEOUT_SECONDS": "8",
        "TIT_DB_LOCK_TIMEOUT_MS": "5000",
        "TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS": "60000",
        "TIT_DB_STATEMENT_TIMEOUT_MS": "30000",
        "TIT_ARGON2_MAX_CONCURRENCY": "2",
        "TIT_LOGIN_RATE_LIMIT_ATTEMPTS": "10",
        "TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS": "60",
        "TIT_LOGIN_RATE_LIMIT_MAX_KEYS": "10000",
        "TIT_SLOW_REQUEST_MS": "1000",
        "TIT_API_WORKERS": "2",
        "TIT_API_LIMIT_CONCURRENCY": "8",
        "TIT_API_KEEPALIVE_SECONDS": "5",
    }
    assert set(values) == set(PRODUCTION_INTEGER_SETTINGS)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TIT_HEALTHCHECK_HOST", "tit-growth.example.com")


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
    set_safe_database_runtime(monkeypatch)

    validate_production_runtime()


def test_production_runtime_preserves_verify_ca_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=verify-ca",
    )
    set_safe_database_runtime(monkeypatch)

    validate_production_runtime()


def test_production_runtime_accepts_exact_pre_private_line_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql+psycopg://tit_growth_app:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
    )
    set_safe_database_runtime(monkeypatch)

    validate_production_runtime()

    assert operations_database_transport_mode(
        os.environ["DATABASE_URL"]
    ) == "pre-private-line-plaintext"
    assert source_worker_database_transport_mode(
        os.environ["DATABASE_URL"]
    ) == "pre-private-line-plaintext"


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
def test_pre_private_line_rejects_ambient_libpq_identity_overrides(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, "must-not-override-url")
    database_url = (
        "postgresql://tit_growth_app:secret@"
        "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
        "tide_system_test?sslmode=disable"
    )

    with pytest.raises(ValueError, match=name):
        operations_database_transport_mode(database_url)


def test_source_worker_transport_preserves_verify_full_only() -> None:
    assert source_worker_database_transport_mode(
        "postgresql://tit_growth_app:secret@db.example/tit?sslmode=verify-full"
    ) == "verified-tls"
    with pytest.raises(ValueError, match="sslmode=verify-full"):
        source_worker_database_transport_mode(
            "postgresql://tit_growth_app:secret@db.example/tit?sslmode=verify-ca"
        )


@pytest.mark.parametrize(
    "database_url",
    [
        (
            "postgresql://other:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tit_growth_app:secret@db.example:5432/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tit_growth_app:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "other?sslmode=disable"
        ),
        (
            "postgresql://tit_growth_app:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5433/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tit_growth_app:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&ssl=false"
        ),
        (
            "postgresql://tit_growth_app:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&host=db.example"
        ),
    ],
)
def test_production_runtime_rejects_plaintext_outside_exact_pre_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv("DATABASE_URL", database_url)
    set_safe_database_runtime(monkeypatch)

    with pytest.raises(RuntimeError, match="Unsafe production runtime"):
        validate_production_runtime()


def test_production_runtime_requires_explicit_pool_and_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=verify-full",
    )
    for name in PRODUCTION_INTEGER_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TIT_DB_POOL_SIZE is required"):
        validate_production_runtime()


def test_production_runtime_rejects_duplicate_sslmode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql+psycopg://app:secret@db.example/tit"
            "?sslmode=verify-full&sslmode=disable"
        ),
    )
    set_safe_database_runtime(monkeypatch)

    with pytest.raises(
        RuntimeError,
        match="exactly one sslmode",
    ):
        validate_production_runtime()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TIT_DB_STATEMENT_TIMEOUT_MS", "not-an-int"),
        ("TIT_API_WORKERS", "10000"),
        ("TIT_API_LIMIT_CONCURRENCY", "0"),
        ("TIT_API_KEEPALIVE_SECONDS", "999"),
    ],
)
def test_production_runtime_rejects_unsafe_process_budgets(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=verify-full",
    )
    set_safe_database_runtime(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
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
    assert environment["TIT_HEALTHCHECK_HOST"] == "tit-growth.example.com"
    assert environment["TIT_ALLOWED_ORIGINS"] == ""
    assert {
        "TIT_DB_POOL_SIZE",
        "TIT_DB_MAX_OVERFLOW",
        "TIT_DB_POOL_TIMEOUT_SECONDS",
        "TIT_DB_POOL_RECYCLE_SECONDS",
        "TIT_DB_CONNECT_TIMEOUT_SECONDS",
        "TIT_DB_LOCK_TIMEOUT_MS",
        "TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS",
        "TIT_DB_STATEMENT_TIMEOUT_MS",
        "TIT_ARGON2_MAX_CONCURRENCY",
        "TIT_LOGIN_RATE_LIMIT_ATTEMPTS",
        "TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        "TIT_LOGIN_RATE_LIMIT_MAX_KEYS",
        "TIT_SLOW_REQUEST_MS",
        "TIT_API_WORKERS",
        "TIT_API_LIMIT_CONCURRENCY",
        "TIT_API_KEEPALIVE_SECONDS",
    } <= set(environment)


def test_production_runtime_rejects_health_host_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_ALLOWED_HOSTS", "tit-growth.example.com")
    monkeypatch.setenv("TIT_HEALTHCHECK_HOST", "127.0.0.1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@db.example/tit?sslmode=verify-full",
    )
    set_safe_database_runtime(monkeypatch)
    monkeypatch.setenv("TIT_HEALTHCHECK_HOST", "127.0.0.1")

    with pytest.raises(RuntimeError, match="must be included"):
        validate_production_runtime()


def test_production_migration_requires_exact_tls_database_without_api_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_MIGRATION_MODE", "true")
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql+psycopg://tide_sys_admin:secret@db.example/"
            "tit_growth?sslmode=verify-full"
        ),
    )
    for name in PRODUCTION_INTEGER_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    validate_production_migration_runtime()
    validate_alembic_runtime()


def test_production_migration_accepts_exact_pre_private_line_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_MIGRATION_MODE", "true")
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tide_system_test")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql+psycopg://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
    )

    validate_production_migration_runtime()
    validate_alembic_runtime()


@pytest.mark.parametrize(
    "database_url",
    [
        (
            "postgresql://other:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tide_sys_admin:secret@db.example:5432/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5433/"
            "tide_system_test?sslmode=disable"
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "other?sslmode=disable"
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&ssl=false"
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&dbname=other"
        ),
    ],
)
def test_production_migration_rejects_plaintext_outside_exact_pre_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_MIGRATION_MODE", "true")
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tide_system_test")
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(RuntimeError, match="Unsafe production migration"):
        validate_production_migration_runtime()


def test_production_alembic_requires_explicit_migration_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TIT_MIGRATION_MODE", raising=False)

    with pytest.raises(RuntimeError, match="TIT_MIGRATION_MODE=true"):
        validate_alembic_runtime()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://tide_sys_admin:secret@db.example/other?sslmode=verify-full",
        "postgresql://tide_sys_admin:secret@db.example/tit_growth?sslmode=require",
        (
            "postgresql://tide_sys_admin:secret@db.example/tit_growth"
            "?sslmode=verify-full&sslmode=disable"
        ),
        (
            "postgresql://tide_sys_admin:secret@db.example/tit_growth"
            "?sslmode=verify-full&ssl=false"
        ),
    ],
)
def test_production_migration_rejects_wrong_target_or_tls(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_MIGRATION_MODE", "true")
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(RuntimeError, match="Unsafe production migration"):
        validate_production_migration_runtime()


@pytest.mark.parametrize(
    ("role", "database", "is_superuser"),
    [
        ("postgres", "tit_growth", True),
        ("tide_sys_admin", "other", False),
        ("unexpected_migrator", "tit_growth", False),
    ],
)
def test_production_migration_rejects_wrong_live_identity(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    database: str,
    is_superuser: bool,
) -> None:
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tit_growth")

    with pytest.raises(RuntimeError, match="non-superuser"):
        validate_production_migration_identity(
            role=role,
            database=database,
            is_superuser=is_superuser,
        )


def test_production_migration_accepts_exact_live_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIT_MIGRATION_EXPECTED_DATABASE", "tit_growth")

    validate_production_migration_identity(
        role="tide_sys_admin",
        database="tit_growth",
        is_superuser=False,
    )


def test_production_migration_accepts_matching_live_private_line_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
    )

    validate_production_migration_transport(
        session_ssl=False,
        server_ssl="off",
    )


@pytest.mark.parametrize(
    ("database_url", "session_ssl", "server_ssl"),
    [
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable",
            True,
            "on",
        ),
        (
            "postgresql://tide_sys_admin:secret@db.example:5432/"
            "tit_growth?sslmode=verify-full",
            False,
            "off",
        ),
    ],
)
def test_production_migration_rejects_mismatched_live_transport(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    session_ssl: bool,
    server_ssl: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(RuntimeError, match="migration requires"):
        validate_production_migration_transport(
            session_ssl=session_ssl,
            server_ssl=server_ssl,
        )


def test_production_migration_accepts_matching_live_tls_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql://tide_sys_admin:secret@db.example:5432/"
            "tit_growth?sslmode=verify-full"
        ),
    )

    validate_production_migration_transport(
        session_ssl=True,
        server_ssl="on",
    )


def test_private_line_engine_installs_live_transport_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    selected = build_engine(
        "postgresql+psycopg://tit_growth_app:secret@"
        "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
        "tide_system_test?sslmode=disable"
    )
    try:
        assert getattr(
            selected,
            "_tit_pre_private_line_transport_guard",
            False,
        ) is True
    finally:
        selected.dispose()


def test_private_line_migration_engine_uses_admin_transport_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TIT_MIGRATION_MODE", "true")
    selected = build_engine(
        "postgresql+psycopg://tide_sys_admin:secret@"
        "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
        "tide_system_test?sslmode=disable"
    )
    try:
        assert getattr(
            selected,
            "_tit_pre_private_line_transport_guard",
            False,
        ) is True
    finally:
        selected.dispose()


def test_verified_tls_engine_does_not_install_private_line_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    selected = build_engine(
        "postgresql+psycopg://tit_growth_app:secret@db.example:5432/"
        "tide_system_test?sslmode=verify-full"
    )
    try:
        assert getattr(
            selected,
            "_tit_pre_private_line_transport_guard",
            False,
        ) is False
    finally:
        selected.dispose()


def test_postgresql_engine_uses_explicit_bounded_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIT_DB_POOL_SIZE", "3")
    monkeypatch.setenv("TIT_DB_MAX_OVERFLOW", "1")
    monkeypatch.setenv("TIT_DB_POOL_TIMEOUT_SECONDS", "7")
    selected = build_engine(
        "postgresql+psycopg://app@localhost/tit?sslmode=disable"
    )
    try:
        assert selected.pool.size() == 3
        assert selected.pool._max_overflow == 1
        assert selected.pool._timeout == 7
    finally:
        selected.dispose()
