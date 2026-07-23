#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import URL


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError("{} is required when TIT_DATABASE_OWNER_URL is not set".format(name))
    return value


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
        return configured
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


def main() -> int:
    env = os.environ.copy()
    env["DATABASE_URL"] = _database_url()
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
    except subprocess.CalledProcessError as exc:
        print(
            "Configured test-database migration failed; no credential was printed.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
