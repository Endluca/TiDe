#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.pool import NullPool


BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATABASE_ENV = "TIT_MIGRATION_EXPECTED_DATABASE"
APPROVED_TEST_HOST = (
    "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
)
APPROVED_TEST_PORT = 5432
APPROVED_TEST_DATABASE = "tit_growth_test_v2"
APPROVED_OWNER_ROLE = "postgres"
APPROVED_DRIVER = "postgresql+psycopg"
APPROVED_QUERY_KEYS = frozenset({"sslmode", "connect_timeout"})
DISALLOWED_LIBPQ_ENV = frozenset(
    {
        "PGAPPNAME",
        "PGCHANNELBINDING",
        "PGCLIENTENCODING",
        "PGCONNECT_TIMEOUT",
        "PGDATABASE",
        "PGFALLBACKAPPLICATIONNAME",
        "PGGSSENCMODE",
        "PGGSSLIB",
        "PGHOST",
        "PGHOSTADDR",
        "PGKRBSRVNAME",
        "PGLOADBALANCEHOSTS",
        "PGOPTIONS",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGREQUIREPEER",
        "PGREQUIRESSL",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSSLCERT",
        "PGSSLCRL",
        "PGSSLCRLDIR",
        "PGSSLCOMPRESSION",
        "PGSSLKEY",
        "PGSSLMODE",
        "PGSSLNEGOTIATION",
        "PGSSLROOTCERT",
        "PGTARGETSESSIONATTRS",
        "PGUSER",
    }
)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _reject_ambient_libpq_environment() -> None:
    present = sorted(name for name in DISALLOWED_LIBPQ_ENV if name in os.environ)
    if present:
        raise RuntimeError(
            "Database guard failed: unset libpq environment variables: "
            + ", ".join(present)
        )


def _database_password(role: str, keychain_service: str) -> str:
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            role,
            "-s",
            keychain_service,
            "-w",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    password = result.stdout.strip()
    if not password:
        raise RuntimeError("database owner password is missing from macOS Keychain")
    return password


def _database_url() -> str:
    configured = os.getenv("TIT_DATABASE_OWNER_URL", "").strip()
    if configured:
        raise RuntimeError(
            "TIT_DATABASE_OWNER_URL is not accepted; use the macOS Keychain "
            "credential configuration"
        )
    role = _required_env("TIT_DATABASE_OWNER_ROLE")
    keychain_service = _required_env("TIT_DATABASE_OWNER_KEYCHAIN_SERVICE")
    return URL.create(
        "postgresql+psycopg",
        username=role,
        password=_database_password(role, keychain_service),
        host=_required_env("TIT_DATABASE_OWNER_HOST"),
        port=int(os.getenv("TIT_DATABASE_OWNER_PORT", "5432")),
        database=_required_env("TIT_DATABASE_OWNER_DATABASE"),
        query={"sslmode": os.getenv("TIT_DATABASE_OWNER_SSLMODE", "disable"), "connect_timeout": "8"},
    ).render_as_string(hide_password=False)


def _expected_database() -> str:
    expected = os.getenv(EXPECTED_DATABASE_ENV, "").strip()
    if not expected:
        raise RuntimeError(f"{EXPECTED_DATABASE_ENV} is required")
    if expected != APPROVED_TEST_DATABASE:
        raise RuntimeError(
            f"{EXPECTED_DATABASE_ENV} must be {APPROVED_TEST_DATABASE}"
        )
    return expected


def _database_name_from_url(database_url: str) -> str:
    try:
        database_name = make_url(database_url).database
    except ArgumentError:
        # SQLAlchemy's parse error may echo its input.  Suppress the cause so a
        # malformed URL cannot leak embedded credentials through a traceback.
        raise RuntimeError("TIT database owner URL is invalid") from None
    if not database_name:
        raise RuntimeError("TIT database owner URL must include a database name")
    return database_name


def _validate_approved_test_identity(database_url: str) -> None:
    try:
        target = make_url(database_url)
    except ArgumentError:
        raise RuntimeError("TIT database owner URL is invalid") from None

    if target.drivername != APPROVED_DRIVER:
        raise RuntimeError("Database guard failed: unapproved database driver")

    unapproved_query_keys = sorted(set(target.query) - APPROVED_QUERY_KEYS)
    if unapproved_query_keys:
        raise RuntimeError(
            "Database guard failed: unapproved connection query option"
        )
    if target.query.get("sslmode", "disable") != "disable":
        raise RuntimeError("Database guard failed: unapproved test sslmode")
    connect_timeout = target.query.get("connect_timeout", "8")
    try:
        timeout_seconds = int(connect_timeout)
    except (TypeError, ValueError):
        raise RuntimeError("Database guard failed: invalid connect timeout") from None
    if not 1 <= timeout_seconds <= 30:
        raise RuntimeError("Database guard failed: invalid connect timeout")

    if target.host != APPROVED_TEST_HOST:
        raise RuntimeError("Database guard failed: unapproved test host")
    try:
        port = target.port
    except ValueError:
        raise RuntimeError("Database guard failed: invalid test port") from None
    if port != APPROVED_TEST_PORT:
        raise RuntimeError("Database guard failed: unapproved test port")
    if target.username != APPROVED_OWNER_ROLE:
        raise RuntimeError("Database guard failed: unapproved owner role")
    if target.database != APPROVED_TEST_DATABASE:
        raise RuntimeError("Database guard failed: unapproved test database")


def _current_identity(database_url: str) -> tuple[str, str]:
    try:
        engine = create_engine(database_url, poolclass=NullPool)
    except SQLAlchemyError:
        raise RuntimeError("Unable to initialize the database target guard") from None
    try:
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT current_database(), current_user")
                ).one()
                return str(row[0]), str(row[1])
        except SQLAlchemyError:
            raise RuntimeError("Unable to verify the live database target") from None
    finally:
        engine.dispose()


def _validate_database_target(database_url: str, expected_database: str) -> None:
    url_database = _database_name_from_url(database_url)
    if url_database != expected_database:
        raise RuntimeError(
            "Database guard failed before connection: "
            f"expected={expected_database}, url_database={url_database}"
        )

    actual_database, actual_user = _current_identity(database_url)
    if actual_database != expected_database:
        raise RuntimeError(
            "Database guard failed after connection: "
            f"expected={expected_database}, actual={actual_database}"
        )
    if actual_user != APPROVED_OWNER_ROLE:
        raise RuntimeError(
            "Database guard failed after connection: "
            f"expected_user={APPROVED_OWNER_ROLE}, actual_user={actual_user}"
        )


def main() -> int:
    expected_database = _expected_database()
    _reject_ambient_libpq_environment()
    database_url = _database_url()
    _validate_approved_test_identity(database_url)
    _validate_database_target(database_url, expected_database)

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env.setdefault("APP_ENV", "test")
    subprocess.run(
        [str(BACKEND_ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
    )
    subprocess.run(
        [str(BACKEND_ROOT / ".venv/bin/alembic"), "current"],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        print(
            "Configured test-database migration failed; no credential was printed.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
