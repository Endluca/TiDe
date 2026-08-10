#!/usr/bin/env python3
"""Fail closed before expanding the standalone production Compose."""

from __future__ import annotations

import os
import re
import sys
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlparse


PRIVATE_BIND_NETWORKS = (
    IPv4Network("127.0.0.0/8"),
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
PRIVATE_RUNTIME_NETWORKS = PRIVATE_BIND_NETWORKS[1:]
DATABASE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class PreflightError(RuntimeError):
    pass


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise PreflightError(f"{name} is required")
    return value


def _existing_file(environment: Mapping[str, str], name: str) -> Path:
    raw_path = _required(environment, name)
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PreflightError(f"{name} must point to an existing file") from exc
    if not path.is_file():
        raise PreflightError(f"{name} must point to a regular file")
    return path


def _ipv4_address(name: str, raw_value: str) -> IPv4Address:
    try:
        return IPv4Address(raw_value)
    except ValueError as exc:
        raise PreflightError(f"{name} must be an IPv4 address") from exc


def _environment_value(path: Path, variable_name: str) -> str:
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == variable_name:
            candidate = value.strip()
            if (
                len(candidate) >= 2
                and candidate[0] == candidate[-1]
                and candidate[0] in {"'", '"'}
            ):
                candidate = candidate[1:-1]
            values.append(candidate)
    if len(values) != 1 or not values[0]:
        raise PreflightError(
            f"{path.name} must contain exactly one non-empty {variable_name}"
        )
    return values[0]


def _validate_database_identity(
    path: Path,
    *,
    variable_name: str = "DATABASE_URL",
    expected_role: str,
    expected_database: str,
) -> None:
    database_url = _environment_value(path, variable_name)
    normalized_url = database_url.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    parsed = urlparse(normalized_url)
    role = unquote(parsed.username or "")
    database = unquote(parsed.path.removeprefix("/"))
    if parsed.scheme != "postgresql" or role != expected_role:
        raise PreflightError(
            f"{path.name} must use the {expected_role} PostgreSQL role"
        )
    if database != expected_database:
        raise PreflightError(
            f"{path.name} DATABASE_URL must select {expected_database}"
        )


def validate(environment: Mapping[str, str]) -> None:
    runtime_env = _existing_file(environment, "TIDE_RUNTIME_ENV_FILE")
    migration_env = _existing_file(environment, "TIDE_MIGRATION_ENV_FILE")
    source_worker_env = _existing_file(
        environment,
        "TIDE_SOURCE_WORKER_ENV_FILE",
    )
    protected_files = (runtime_env, migration_env, source_worker_env)
    if any(
        os.path.samefile(left, right)
        for index, left in enumerate(protected_files)
        for right in protected_files[index + 1 :]
    ):
        raise PreflightError(
            "runtime, migration and source-worker env files must differ"
        )

    expected_database = _required(
        environment,
        "TIDE_MIGRATION_EXPECTED_DATABASE",
    )
    if not DATABASE_NAME_PATTERN.fullmatch(expected_database):
        raise PreflightError(
            "TIDE_MIGRATION_EXPECTED_DATABASE must be a PostgreSQL identifier"
        )

    ops_host = _required(environment, "TIDE_OPS_HOST")
    if any(character in ops_host for character in "/:, \t\r\n"):
        raise PreflightError(
            "TIDE_OPS_HOST must be one hostname without scheme, port or path"
        )

    bind_address = _ipv4_address(
        "TIDE_GATEWAY_BIND_ADDRESS",
        environment.get("TIDE_GATEWAY_BIND_ADDRESS", "127.0.0.1").strip(),
    )
    if not any(bind_address in network for network in PRIVATE_BIND_NETWORKS):
        raise PreflightError(
            "TIDE_GATEWAY_BIND_ADDRESS must be loopback or RFC1918"
        )

    raw_subnet = _required(environment, "TIDE_RUNTIME_SUBNET")
    try:
        runtime_subnet = IPv4Network(raw_subnet, strict=True)
    except ValueError as exc:
        raise PreflightError(
            "TIDE_RUNTIME_SUBNET must be a canonical IPv4 CIDR"
        ) from exc
    if not any(
        runtime_subnet.subnet_of(network)
        for network in PRIVATE_RUNTIME_NETWORKS
    ):
        raise PreflightError("TIDE_RUNTIME_SUBNET must be inside RFC1918 space")

    web_proxy_ip = _ipv4_address(
        "TIDE_WEB_PROXY_IP",
        _required(environment, "TIDE_WEB_PROXY_IP"),
    )
    if (
        web_proxy_ip not in runtime_subnet
        or web_proxy_ip == runtime_subnet.network_address
        or web_proxy_ip == runtime_subnet.broadcast_address
    ):
        raise PreflightError(
            "TIDE_WEB_PROXY_IP must be a usable address in TIDE_RUNTIME_SUBNET"
        )

    _validate_database_identity(
        runtime_env,
        expected_role="tit_growth_app",
        expected_database=expected_database,
    )
    _validate_database_identity(
        migration_env,
        expected_role="tit_growth_migrator",
        expected_database=expected_database,
    )
    _validate_database_identity(
        source_worker_env,
        variable_name="TIT_SOURCE_WORKER_DATABASE_URL",
        expected_role="tit_source_worker_runtime",
        expected_database=expected_database,
    )
    worker_expected_database = _environment_value(
        source_worker_env,
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE",
    )
    if worker_expected_database != expected_database:
        raise PreflightError(
            f"{source_worker_env.name} source-worker expected database must "
            f"be {expected_database}"
        )


def main() -> int:
    try:
        validate(os.environ)
    except (OSError, UnicodeError, PreflightError) as exc:
        print(f"Production preflight failed: {exc}", file=sys.stderr)
        return 1
    print("Production preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
