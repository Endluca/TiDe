from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest
import yaml
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "combined"
EXPECTED_TEACHER_MIGRATIONS = (
    "0001_initial",
    "0002_shared_database_exchange",
    "0003_file_upload_intents",
    "0004_task_command_receipts",
    "0005_faq_message_commands",
    "0006_teacher_profile_g01_support",
    "0007_shared_task_assignment_links",
    "0008_remove_legacy_task_exchange",
    "0009_task_view_command",
    "0010_current_task_execution",
    "0011_system_notification_delivery",
    "0012_system_notification_publication_guards",
    "0013_system_notification_owner_maintenance",
    "0014_teacher_photo_processing",
    "0015_teacher_photo_filter_strength",
    "0016_database_quiz_banks",
    "0019_growth_stage_notification_state",
    "0020_product_analytics",
    "0021_teacher_support_tickets",
    "0022_performance_job_leases",
    "0023_teacher_support_operator_atomicity",
    "0024_support_ticket_cas_and_function_owner",
    "0025_fixed_task_semantic_alignment",
)
EXPECTED_FIXED_TASKS = (
    ("G01", "Profile & Credentials Completion", 3),
    ("G02", "Platform Policies", 2),
    ("G03", "How to handle different types of students", 2),
    ("G04", "Lesson Preparation&Device Network Check", 3),
    ("G05", "TTP Orientation", 3),
    ("G06", "ME Culture & PARSNIP", 4),
    ("G07", "Reliability Training", 3),
    ("G08", "Cocos Course Training", 5),
    ("G09", "SET Teaching Fundamentals", 5),
)


def _compose() -> dict:
    return yaml.safe_load(
        (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    )


def test_combined_deployment_exposes_only_the_edge() -> None:
    compose = _compose()
    services = compose["services"]

    assert set(services) == {
        "migrate",
        "teacher-migrate",
        "api",
        "score-settlement",
        "web",
        "teacher-api",
        "teacher-web",
        "edge",
        "contract-probe",
    }
    published = {
        name: service["ports"]
        for name, service in services.items()
        if service.get("ports")
    }
    assert published == {
        "edge": [
            "${TIDE_EDGE_BIND_ADDRESS:?Set a loopback or private gateway bind address}:${TIDE_EDGE_HTTP_PORT:-8080}:8080"
        ]
    }
    assert services["teacher-api"]["deploy"]["replicas"] == 1
    assert (
        services["teacher-api"]["environment"]["BACKGROUND_JOBS_ENABLED"]
        == "true"
    )
    assert services["teacher-api"]["environment"][
        "MULTIPART_UPLOAD_MAX_CONCURRENCY"
    ] == "${TIT_TEACHER_MULTIPART_UPLOAD_MAX_CONCURRENCY:-4}"
    assert services["teacher-api"]["environment"]["TRUST_PROXY_HOPS"] == "1"
    assert (
        services["teacher-api"]["environment"]["SHIWEN_READ_MODE"]
        == "DIRECT_TABLES"
    )
    assert services["api"]["environment"]["TIT_HEALTHCHECK_HOST"] == (
        "${TIDE_OPS_HOST:?Set TIDE_OPS_HOST}"
    )
    assert services["api"]["environment"]["TIT_TRUSTED_PROXY_IPS"] == (
        "${TIDE_EDGE_PROXY_IP:?Set the exact Edge container IP}"
    )
    assert services["edge"]["networks"]["edge"]["ipv4_address"] == (
        "${TIDE_EDGE_PROXY_IP:?Set TIDE_EDGE_PROXY_IP}"
    )
    assert services["edge"]["environment"]["TIDE_COMPANY_GATEWAY_CIDR"] == (
        "${TIDE_COMPANY_GATEWAY_CIDR:?Set the exact trusted company gateway IP/CIDR}"
    )
    assert services["contract-probe"]["profiles"] == ["migration"]


def test_combined_deployment_keeps_runtime_roles_and_origins_separate() -> None:
    compose = _compose()
    services = compose["services"]

    assert services["api"]["env_file"] != services["teacher-api"]["env_file"]
    assert services["migrate"]["env_file"] != services["api"]["env_file"]
    assert services["contract-probe"]["env_file"] != services["migrate"]["env_file"]
    assert services["contract-probe"]["env_file"] != services["api"]["env_file"]
    assert services["contract-probe"]["env_file"] == [
        "${TIDE_CONTRACT_PROBE_ENV_FILE:?Set TIDE_CONTRACT_PROBE_ENV_FILE to a protected read-only probe env file}"
    ]
    assert services["contract-probe"]["environment"][
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL"
    ] == "true"
    assert (
        services["teacher-migrate"]["env_file"]
        != services["teacher-api"]["env_file"]
    )
    assert (
        services["teacher-migrate"]["build"]["dockerfile"]
        == "Dockerfile.migrate"
    )
    assert (
        services["teacher-api"]["environment"]["TASK_CATALOG_PUBLIC_WRITE"]
        == "false"
    )
    assert (
        services["teacher-api"]["environment"][
            "SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK"
        ]
        == "false"
    )
    assert (
        services["teacher-web"]["build"]["args"]["VITE_API_BASE_URL"]
        == "https://${TIDE_TEACHER_HOST:?Set TIDE_TEACHER_HOST}"
    )
    probe_environment_example = (
        DEPLOY / "contract-probe.env.example"
    ).read_text(encoding="utf-8")
    configured_lines = [
        line
        for line in probe_environment_example.splitlines()
        if line and not line.startswith("#")
    ]
    assert configured_lines == [
        "DATABASE_URL=postgresql://tit_contract_probe:REPLACE_ME@"
        "postgres.example.internal:5432/tit_growth?sslmode=verify-full"
    ]


def test_combined_preflight_and_database_probe_fail_closed() -> None:
    preflight = (DEPLOY / "preflight.sh").read_text(encoding="utf-8")
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")
    grants = (DEPLOY / "grant-contract-probe.sql").read_text(encoding="utf-8")
    runner = (DEPLOY / "run-contract-probe.sh").read_text(encoding="utf-8")

    assert "status --porcelain" in preflight
    assert "HIDDEN_FIXED_TASK_CODES" in preflight
    assert "code: 'G10'" in preflight
    assert "0017_task_assignment_teacher_response" in preflight
    assert "0024_support_ticket_cas_and_function_owner" in preflight
    assert "0025_fixed_task_semantic_alignment" in preflight
    assert "Lesson Preparation&Device Network Check" in preflight
    assert "is_loopback_or_rfc1918_ipv4" in preflight
    assert "TIDE_CONTRACT_PROBE_ENV_FILE" in preflight
    assert "公司网关可信源" in preflight

    assert "current_user IS DISTINCT FROM 'tit_contract_probe'" in probe
    assert "session_user IS DISTINCT FROM 'tit_contract_probe'" in probe
    assert "transaction_read_only" in probe
    assert "pg_stat_ssl" in probe
    assert "has_database_privilege" in probe
    assert "contract probe role has write-capable privileges" in probe
    assert "20260729_38_catalog_scores" in probe
    assert "actual_titles text[]" in probe
    assert (
        "ARRAY['G01','G02','G03','G04','G05','G06','G07','G08','G09']"
        in probe
    )
    assert "Lesson Preparation&Device Network Check" in probe
    assert "ARRAY[3,2,2,3,3,4,3,5,5]" in probe
    assert "count(assignment.assignment_id) <> 9" in probe
    assert "teacher_reply_deadline_at" in probe
    assert "interval ''48 hours''" in probe
    assert "0024_support_ticket_cas_and_function_owner" in probe
    assert "0025_fixed_task_semantic_alignment" in probe
    assert "p_message IS NULL" in probe
    assert "tide_support_ticket_owner" in probe
    assert "ALTER ROLE tit_contract_probe SET default_transaction_read_only" in grants
    assert "GRANT SELECT ON" in grants
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES" in grants
    assert "sslmode=verify-full" in runner
    assert "--no-password" in runner


def _teacher_catalog_fixture(
    tasks: tuple[tuple[str, str, int], ...] = EXPECTED_FIXED_TASKS,
) -> str:
    task_blocks = [
        (
            "  {\n"
            f"    code: '{code}',\n"
            f"    title: '{title}',\n"
            f"    score: {score},\n"
            "  },"
        )
        for code, title, score in tasks
    ]
    return "const catalog = [\n" + "\n".join(task_blocks) + "\n];\n"


def _teacher_migrator_fixture(
    migrations: tuple[str, ...] = EXPECTED_TEACHER_MIGRATIONS,
) -> str:
    migration_lines = "\n".join(
        f"  {migration_id}" for migration_id in migrations
    )
    return (
        "#!/usr/bin/env bash\n"
        "TARGET_MIGRATION="
        f'"${{TIDE_MIGRATION_TARGET:-{migrations[-1]}}}"\n'
        "PRODUCTION_MIGRATIONS=(\n"
        f"{migration_lines}\n"
        ")\n"
    )


def _make_preflight_environment(
    tmp_path: Path,
    *,
    combined_repository: bool = False,
) -> dict[str, str]:
    source_repo = tmp_path / ("system" if combined_repository else "teacher")
    teacher_repo = source_repo / "teacher" if combined_repository else source_repo
    files = {
        "backend/src/tide/tide.service.ts": (
            "const HIDDEN_FIXED_TASK_CODES = new Set<string>();\n"
        ),
        "backend/scripts/sync-current-task-catalog.ts": _teacher_catalog_fixture(),
        "backend/src/notifications/growth-stage-notification.repository.ts": (
            "const currentTasks = ["
            + ", ".join(f"'{code}'" for code, _, _ in EXPECTED_FIXED_TASKS)
            + "];\n"
        ),
        "backend/database/scripts/apply-production.sh": _teacher_migrator_fixture(),
        "backend/database/migrations/"
        "0025_fixed_task_semantic_alignment.up.sql": "BEGIN;\nCOMMIT;\n",
        "backend/Dockerfile": "FROM scratch\n",
        "backend/Dockerfile.migrate": "FROM scratch\n",
        "frontend/Dockerfile": "FROM scratch\n",
    }
    for relative_path, content in files.items():
        target = teacher_repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    subprocess.run(
        ["git", "init", "-q", str(source_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "config", "user.name", "Contract Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "add", "."],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-qm", "fixture"],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    environment_files = [
        tmp_path / "ops.env",
        tmp_path / "ops-migration.env",
        tmp_path / "contract-probe.env",
        tmp_path / "teacher.env",
        tmp_path / "teacher-migration.env",
    ]
    for environment_file in environment_files:
        environment_file.write_text("PLACEHOLDER=true\n", encoding="utf-8")

    return {
        **os.environ,
        "TIDE_OPS_HOST": "ops.example.com",
        "TIDE_TEACHER_HOST": "teacher.example.com",
        "TIDE_EDGE_BIND_ADDRESS": "127.0.0.1",
        "TIDE_EDGE_NETWORK_SUBNET": "172.29.0.0/24",
        "TIDE_EDGE_PROXY_IP": "172.29.0.2",
        "TIDE_COMPANY_GATEWAY_CIDR": "127.0.0.1/32",
        "TIDE_DATABASE_NAME": "tit_growth",
        "TIDE_OPS_ENV_FILE": str(environment_files[0]),
        "TIDE_OPS_MIGRATION_ENV_FILE": str(environment_files[1]),
        "TIDE_CONTRACT_PROBE_ENV_FILE": str(environment_files[2]),
        "TIDE_TEACHER_ENV_FILE": str(environment_files[3]),
        "TIDE_TEACHER_MIGRATION_ENV_FILE": str(environment_files[4]),
        "TIDE_TEACHER_REPO_PATH": str(teacher_repo),
        "TIDE_TEACHER_EXPECTED_COMMIT": commit,
        "TIDE_TEACHER_PUBLIC_ASSET_BASE_URL": "https://media.example.com",
    }


def _commit_teacher_fixture_change(
    environment: dict[str, str],
    relative_path: str,
    content: str,
) -> dict[str, str]:
    teacher_repo = Path(environment["TIDE_TEACHER_REPO_PATH"])
    target = teacher_repo / relative_path
    target.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(teacher_repo), "add", relative_path],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(teacher_repo), "commit", "-qm", "change fixture"],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(teacher_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {**environment, "TIDE_TEACHER_EXPECTED_COMMIT": commit}


def _run_preflight(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY / "preflight.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_combined_preflight_rejects_teacher_chain_ending_at_only_0024(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment = _commit_teacher_fixture_change(
        environment,
        "backend/database/scripts/apply-production.sh",
        _teacher_migrator_fixture(EXPECTED_TEACHER_MIGRATIONS[:-1]),
    )

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "教师端生产迁移器不是以 0025 结尾的完整有序生产链" in result.stderr


def test_combined_preflight_accepts_teacher_source_inside_one_clean_repository(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(
        tmp_path,
        combined_repository=True,
    )

    result = _run_preflight(environment)

    assert result.returncode == 0, result.stderr


def test_combined_preflight_rejects_dirty_combined_repository(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(
        tmp_path,
        combined_repository=True,
    )
    source_root = Path(environment["TIDE_TEACHER_REPO_PATH"]).parent
    (source_root / "UNCOMMITTED.md").write_text("dirty\n", encoding="utf-8")

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "部署源码所在 Git 工作副本存在未提交改动" in result.stderr


def test_combined_preflight_rejects_missing_fixed_task(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment = _commit_teacher_fixture_change(
        environment,
        "backend/scripts/sync-current-task-catalog.ts",
        _teacher_catalog_fixture(EXPECTED_FIXED_TASKS[:-1]),
    )

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "教师端执行目录不是精确的新 G01-G09 标题与分值" in result.stderr


@pytest.mark.parametrize("wrong_field", ["title", "score"])
def test_combined_preflight_rejects_wrong_fixed_task_semantics(
    tmp_path: Path,
    wrong_field: str,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    wrong_tasks = tuple(
        (
            code,
            (
                "Lesson Preparation & Device Network Check"
                if code == "G04" and wrong_field == "title"
                else title
            ),
            10 if code == "G04" and wrong_field == "score" else score,
        )
        for code, title, score in EXPECTED_FIXED_TASKS
    )
    environment = _commit_teacher_fixture_change(
        environment,
        "backend/scripts/sync-current-task-catalog.ts",
        _teacher_catalog_fixture(wrong_tasks),
    )

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "教师端执行目录不是精确的新 G01-G09 标题与分值" in result.stderr


def test_combined_preflight_accepts_only_loopback_or_rfc1918_bindings(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    for address in ("127.0.0.1", "10.2.3.4", "172.16.9.8", "192.168.4.5"):
        result = _run_preflight(
            {**environment, "TIDE_EDGE_BIND_ADDRESS": address}
        )
        assert result.returncode == 0, result.stderr

    for address in (
        "0.0.0.0",
        "8.8.8.8",
        "172.15.9.8",
        "169.254.1.2",
        "203.0.113.7",
        "::1",
        "010.2.3.4",
    ):
        result = _run_preflight(
            {**environment, "TIDE_EDGE_BIND_ADDRESS": address}
        )
        assert result.returncode != 0, address
        assert "Edge 监听地址" in result.stderr


@pytest.mark.parametrize(
    ("gateway_cidr", "accepted"),
    [
        ("127.0.0.1/32", True),
        ("10.20.30.40/32", True),
        ("10.20.30.0/24", True),
        ("10.20.30.40/24", False),
        ("10.20.0.0/16", False),
        ("203.0.113.40/32", False),
        ("::1/128", False),
    ],
)
def test_combined_preflight_limits_the_trusted_gateway_source(
    tmp_path: Path,
    gateway_cidr: str,
    accepted: bool,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment["TIDE_COMPANY_GATEWAY_CIDR"] = gateway_cidr
    result = _run_preflight(environment)

    assert (result.returncode == 0) is accepted, result.stderr


def test_combined_preflight_rejects_reused_probe_credentials(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment["TIDE_CONTRACT_PROBE_ENV_FILE"] = environment[
        "TIDE_OPS_MIGRATION_ENV_FILE"
    ]

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "契约探针必须使用独立只读账号环境文件" in result.stderr


def test_contract_probe_runner_requires_strict_production_tls() -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": (
            "postgresql://tit_contract_probe:secret@db.example/tit_growth"
            "?sslmode=require"
        ),
        "TIDE_CONTRACT_PROBE_EXPECTED_DATABASE": "tit_growth",
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL": "true",
        "TIDE_CONTRACT_PROBE_SQL_FILE": str(DEPLOY / "contract-probe.sql"),
    }

    result = subprocess.run(
        ["sh", str(DEPLOY / "run-contract-probe.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "exactly one sslmode=verify-full" in result.stderr


def test_edge_canonicalizes_forwarded_headers_before_one_hop_backends() -> None:
    nginx = (DEPLOY / "edge-nginx.conf.template").read_text(encoding="utf-8")

    assert "set_real_ip_from ${TIDE_COMPANY_GATEWAY_CIDR};" in nginx
    assert "real_ip_header X-Forwarded-For;" in nginx
    assert "geo $realip_remote_addr $request_from_company_gateway" in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert '"1:https" https;' in nginx


def test_ops_proxy_middleware_uses_distinct_sanitized_client_ips_and_ignores_direct_spoof() -> None:
    observed: list[str] = []

    async def capture_client(
        scope: dict[str, Any],
        _receive: Any,
        _send: Any,
    ) -> None:
        observed.append(scope["client"][0])

    middleware = ProxyHeadersMiddleware(
        capture_client,
        trusted_hosts=["172.29.0.2"],
    )

    async def invoke(peer: str, forwarded_for: str) -> None:
        scope: dict[str, Any] = {
            "type": "http",
            "client": (peer, 12345),
            "scheme": "http",
            "headers": [
                (b"x-forwarded-for", forwarded_for.encode("ascii")),
                (b"x-forwarded-proto", b"https"),
            ],
        }

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(_message: dict[str, Any]) -> None:
            return None

        await middleware(scope, receive, send)

    asyncio.run(invoke("172.29.0.2", "203.0.113.17"))
    asyncio.run(invoke("172.29.0.2", "198.51.100.23"))
    asyncio.run(invoke("172.29.0.99", "192.0.2.88"))

    assert observed == [
        "203.0.113.17",
        "198.51.100.23",
        "172.29.0.99",
    ]
