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
        raise RuntimeError("{} is required when DATABASE_URL is not set".format(name))
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
        raise RuntimeError("database password is missing from macOS Keychain")
    return password


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured
    role = _required_env("TIT_TEST_DATABASE_ROLE")
    keychain_service = _required_env("TIT_TEST_DATABASE_KEYCHAIN_SERVICE")
    return URL.create(
        "postgresql+psycopg",
        username=role,
        password=_database_password(role, keychain_service),
        host=_required_env("TIT_TEST_DATABASE_HOST"),
        port=int(os.getenv("TIT_TEST_DATABASE_PORT", "5432")),
        database=_required_env("TIT_TEST_DATABASE_NAME"),
        query={"sslmode": os.getenv("TIT_TEST_DATABASE_SSLMODE", "disable"), "connect_timeout": "8"},
    ).render_as_string(hide_password=False)


def main() -> int:
    env = os.environ.copy()
    env["DATABASE_URL"] = _database_url()
    env.setdefault("APP_ENV", "test")
    command = [
        str(BACKEND_ROOT / ".venv/bin/uvicorn"),
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
        "--env-file",
        ".env.local",
    ]
    os.chdir(BACKEND_ROOT)
    os.execvpe(command[0], command, env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(
            "Unable to read the configured test-database credential.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
