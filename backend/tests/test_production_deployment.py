from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load(
        (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )


def test_production_compose_separates_migration_and_runtime_credentials() -> None:
    services = _compose()["services"]

    assert services["migrate"]["env_file"] == [
        "${TIDE_MIGRATION_ENV_FILE:?Set TIDE_MIGRATION_ENV_FILE to a protected migration-only env file}"
    ]
    assert services["api"]["env_file"] == [
        "${TIDE_RUNTIME_ENV_FILE:?Set TIDE_RUNTIME_ENV_FILE to a protected runtime-only env file}"
    ]
    assert services["score-settlement"]["env_file"] == services["api"]["env_file"]
    assert services["source-wide"]["env_file"] == services["api"]["env_file"]
    assert services["source-wide"]["env_file"] != services["migrate"]["env_file"]

    migrate_environment = services["migrate"]["environment"]
    assert migrate_environment["TIT_MIGRATION_MODE"] == "true"
    assert migrate_environment["TIT_MIGRATION_EXPECTED_DATABASE"] == (
        "${TIDE_MIGRATION_EXPECTED_DATABASE:?Set TIDE_MIGRATION_EXPECTED_DATABASE}"
    )
    assert services["api"]["environment"]["TIT_MIGRATION_MODE"] == "false"
    assert (
        services["score-settlement"]["environment"]["TIT_MIGRATION_MODE"]
        == "false"
    )
    assert (
        services["source-wide"]["environment"]["TIT_MIGRATION_MODE"]
        == "false"
    )


def test_production_compose_supervises_source_worker_and_both_health_files() -> None:
    source_worker = _compose()["services"]["source-wide"]

    assert source_worker["command"] == [
        "python",
        "scripts/run_source_wide_worker.py",
        "--watch",
        "--max-events",
        "25",
        "--interval-seconds",
        "3",
        "--heartbeat-path",
        "/tmp/tit-source-worker-heartbeat",
        "--readiness-path",
        "/tmp/tit-source-worker-readiness",
    ]
    assert source_worker["environment"]["TIT_DB_POOL_SIZE"] == (
        "${TIT_SOURCE_WORKER_DB_POOL_SIZE:-1}"
    )
    assert source_worker["environment"]["TIT_DB_MAX_OVERFLOW"] == (
        "${TIT_SOURCE_WORKER_DB_MAX_OVERFLOW:-0}"
    )
    assert source_worker["environment"]["TIT_DB_APPLICATION_NAME"] == (
        "tit-growth-source-worker"
    )
    assert source_worker["restart"] == "unless-stopped"
    assert source_worker["read_only"] is True
    healthcheck = source_worker["healthcheck"]
    assert healthcheck["test"] == [
        "CMD",
        "python",
        "scripts/run_source_wide_worker.py",
        "--healthcheck",
        "--heartbeat-path",
        "/tmp/tit-source-worker-heartbeat",
        "--readiness-path",
        "/tmp/tit-source-worker-readiness",
        "--max-heartbeat-age-seconds",
        "90",
        "--max-readiness-age-seconds",
        "90",
    ]


def test_local_start_supervises_source_worker_process() -> None:
    start_script = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")

    assert "scripts/run_source_wide_worker.py" in start_script
    assert (
        '--heartbeat-path "$RUNTIME_DIR/source-worker-heartbeat"'
        in start_script
    )
    assert (
        '--readiness-path "$RUNTIME_DIR/source-worker-readiness"'
        in start_script
    )
    assert '>"$RUNTIME_DIR/source-worker.log" 2>&1 &' in start_script
    assert "SOURCE_WORKER_PID=$!" in start_script
    assert start_script.count('"${SOURCE_WORKER_PID:-}"') == 1
    assert start_script.count('"$SOURCE_WORKER_PID"') == 1


def test_production_compose_trusts_only_the_fixed_web_proxy() -> None:
    compose = _compose()
    services = compose["services"]

    assert services["api"]["environment"]["TIT_TRUSTED_PROXY_IPS"] == (
        "${TIDE_WEB_PROXY_IP:?Set the exact Web proxy container IP}"
    )
    assert services["web"]["networks"]["runtime"]["ipv4_address"] == (
        "${TIDE_WEB_PROXY_IP:?Set TIDE_WEB_PROXY_IP}"
    )
    assert compose["networks"]["runtime"]["ipam"]["config"] == [
        {"subnet": "${TIDE_RUNTIME_SUBNET:?Set a non-overlapping runtime subnet}"}
    ]
    assert "172.16.0.0/12" not in (
        ROOT / "docker-compose.production.yml"
    ).read_text(encoding="utf-8")


def test_production_compose_closes_health_and_published_port_boundaries() -> None:
    services = _compose()["services"]

    assert services["api"]["environment"]["TIT_HEALTHCHECK_HOST"] == (
        "${TIDE_OPS_HOST:?Set TIDE_OPS_HOST}"
    )
    health_command = services["api"]["healthcheck"]["test"][-1]
    assert "urllib.request.Request" in health_command
    assert "'Host': os.environ['TIT_HEALTHCHECK_HOST']" in health_command
    assert services["web"]["ports"] == [
        "${TIDE_GATEWAY_BIND_ADDRESS:-127.0.0.1}:${TIDE_HTTPS_TERMINATION_PORT:-8080}:8080"
    ]
    assert {
        name: service["ports"]
        for name, service in services.items()
        if service.get("ports")
    } == {"web": services["web"]["ports"]}


def test_production_nginx_does_not_trust_forged_forwarding_headers() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "proxy_set_header X-Real-IP $remote_addr;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "proxy_set_header X-Forwarded-Proto https;" in nginx
    assert "$http_x_real_ip" not in nginx
    assert "$http_x_forwarded_proto" not in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_production_env_examples_use_lean_database_roles() -> None:
    runtime_example = (
        ROOT / "backend" / ".env.production.example"
    ).read_text(encoding="utf-8")
    migration_example = (
        ROOT / "backend" / ".env.migration.production.example"
    ).read_text(encoding="utf-8")
    assert "tit_growth_app:" in runtime_example
    assert "tide_sys_admin:" not in runtime_example
    assert "TIT_MIGRATION_MODE=false" in runtime_example
    assert "TIT_SOURCE_WORKER_EXPECTED_DATABASE=tit_growth" in runtime_example
    assert "tide_sys_admin:" in migration_example
    assert "tit_growth_app:" not in migration_example
    assert "TIT_MIGRATION_MODE=true" not in migration_example


def _preflight_environment(tmp_path: Path) -> dict[str, str]:
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text(
        "DATABASE_URL="
        "postgresql+psycopg://tit_growth_app:secret@db.example/"
        "tit_growth?sslmode=verify-full\n"
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE=tit_growth\n",
        encoding="utf-8",
    )
    migration_env = tmp_path / "migration.env"
    migration_env.write_text(
        "DATABASE_URL="
        "postgresql+psycopg://tide_sys_admin:secret@db.example/"
        "tit_growth?sslmode=verify-full\n",
        encoding="utf-8",
    )
    return {
        **os.environ,
        "TIDE_RUNTIME_ENV_FILE": str(runtime_env),
        "TIDE_MIGRATION_ENV_FILE": str(migration_env),
        "TIDE_MIGRATION_EXPECTED_DATABASE": "tit_growth",
        "TIDE_OPS_HOST": "tit-growth.example.com",
        "TIDE_GATEWAY_BIND_ADDRESS": "127.0.0.1",
        "TIDE_RUNTIME_SUBNET": "172.31.254.0/24",
        "TIDE_WEB_PROXY_IP": "172.31.254.10",
    }


def _run_preflight(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight_production.py")],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_production_preflight_accepts_closed_private_topology(
    tmp_path: Path,
) -> None:
    result = _run_preflight(_preflight_environment(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Production preflight passed.\n"


def test_production_preflight_rejects_public_bind(
    tmp_path: Path,
) -> None:
    environment = _preflight_environment(tmp_path)
    environment["TIDE_GATEWAY_BIND_ADDRESS"] = "203.0.113.10"

    result = _run_preflight(environment)

    assert result.returncode == 1
    assert "must be loopback or RFC1918" in result.stderr


def test_production_preflight_rejects_invalid_internal_network(
    tmp_path: Path,
) -> None:
    environment = _preflight_environment(tmp_path)
    environment["TIDE_RUNTIME_SUBNET"] = "203.0.113.0/24"

    result = _run_preflight(environment)

    assert result.returncode == 1
    assert "must be inside RFC1918 space" in result.stderr


def test_production_preflight_rejects_proxy_outside_runtime_subnet(
    tmp_path: Path,
) -> None:
    environment = _preflight_environment(tmp_path)
    environment["TIDE_WEB_PROXY_IP"] = "172.31.253.10"

    result = _run_preflight(environment)

    assert result.returncode == 1
    assert "must be a usable address" in result.stderr


def test_production_preflight_rejects_reused_env_file(
    tmp_path: Path,
) -> None:
    environment = _preflight_environment(tmp_path)
    environment["TIDE_MIGRATION_ENV_FILE"] = environment["TIDE_RUNTIME_ENV_FILE"]

    result = _run_preflight(environment)

    assert result.returncode == 1
    assert "must differ" in result.stderr


def test_production_preflight_rejects_worker_database_mismatch(
    tmp_path: Path,
) -> None:
    environment = _preflight_environment(tmp_path)
    runtime_env = Path(environment["TIDE_RUNTIME_ENV_FILE"])
    runtime_env.write_text(
        "DATABASE_URL="
        "postgresql+psycopg://tit_growth_app:secret@db.example/"
        "tit_growth?sslmode=verify-full\n"
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE=other\n",
        encoding="utf-8",
    )

    result = _run_preflight(environment)

    assert result.returncode == 1
    assert "source-worker expected database must be tit_growth" in result.stderr


def test_readme_runs_preflight_before_migration() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    preflight = readme.index("python3 scripts/preflight_production.py")
    migration = readme.index(
        "docker compose -f docker-compose.production.yml "
        "--profile migration run --rm migrate"
    )
    assert preflight < migration
    assert "20260812_59_simple_acl" in readme
    assert "0041_crm_sso_hybrid" in readme
    assert "20260811_51_g01_tesol_only" in readme
    assert "0033_g01_tesol_only" in readme
    assert "20260810_50_g04_sections" in readme
    assert "0032_first_login_onboarding" in readme
    assert "20260811_56_p_fb_negative_copy" in readme
    assert "0038_personalized_environment_photo" in readme
    assert "0041_crm_sso_hybrid" in readme
    assert (
        "public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → "
        "teacher 0037 → public 55 → release public 56 → teacher 0038 → "
        "release public 57 → teacher 0040 → teacher 0041"
    ) in readme
