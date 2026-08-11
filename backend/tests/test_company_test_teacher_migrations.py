from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = (
    ROOT
    / "teacher"
    / "backend"
    / "database"
    / "scripts"
    / "apply-company-test-migrations.sh"
)
PRODUCTION_MIGRATOR = WRAPPER.with_name("apply-production.sh")
MIGRATIONS = WRAPPER.parent.parent / "migrations"
MIGRATION_DOCKERFILE = ROOT / "teacher" / "backend" / "Dockerfile.migrate"


def _bash_array(script: str, name: str) -> tuple[str, ...]:
    body = script.split(f"{name}=(", 1)[1].split("\n)", 1)[0]
    return tuple(
        line.strip().strip('"')
        for line in body.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and "${" not in line
    )


def _approved_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    script = WRAPPER.read_text(encoding="utf-8")
    baseline = _bash_array(script, "BASELINE_TIDE_MIGRATIONS")
    return baseline, (
        *baseline,
        "0039_g02_policy_document",
        "0040_g02_document_read_status",
    )


def _ledger_manifest(ids: tuple[str, ...], migration_dir: Path) -> str:
    return "\n".join(
        "|".join(
            (
                str(order),
                migration_id,
                f"{migration_id}.up.sql",
                hashlib.sha256(
                    (migration_dir / f"{migration_id}.up.sql").read_bytes()
                ).hexdigest(),
            )
        )
        for order, migration_id in enumerate(ids, start=1)
    )


def test_company_test_increment_is_narrow_and_packaged() -> None:
    wrapper = WRAPPER.read_text(encoding="utf-8")
    production = PRODUCTION_MIGRATOR.read_text(encoding="utf-8")
    dockerfile = MIGRATION_DOCKERFILE.read_text(encoding="utf-8")
    baseline, approved = _approved_ids()

    assert baseline[-1] == "0038_personalized_environment_photo"
    assert approved[-2:] == (
        "0039_g02_policy_document",
        "0040_g02_document_read_status",
    )
    assert len(baseline) == 33
    assert "TIDE_MIGRATION_TEST_MODE=true" not in wrapper
    assert "TIDE_MIGRATION_TEST_MODE=false" in wrapper
    assert 'EXPECTED_PUBLIC_HEAD="20260811_57_g02_document"' in wrapper
    assert 'APPROVED_TEST_DB_SSLMODE="disable"' in wrapper
    assert "actual_baseline_manifest" in wrapper
    assert "expected_baseline_manifest" in wrapper
    assert 'bash "${CANONICAL_MIGRATOR}"' in wrapper
    assert "TIDE_COMPANY_TEST_MIGRATION_MODE" in production
    assert "APPROVED_COMPANY_TEST_DB_HOST" in production
    assert "通用 TIDE_MIGRATION_TEST_MODE 绕过" in production
    assert "apply-company-test-migrations.sh" in dockerfile


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_sandbox(tmp_path: Path) -> dict[str, Path | str]:
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    database = source / "teacher" / "backend" / "database"
    scripts = database / "scripts"
    migrations = database / "migrations"
    scripts.mkdir(parents=True)
    migrations.mkdir()

    sandbox_wrapper = scripts / WRAPPER.name
    shutil.copy2(WRAPPER, sandbox_wrapper)
    sandbox_wrapper.chmod(0o755)

    baseline, approved = _approved_ids()
    for migration_id in approved:
        shutil.copy2(
            MIGRATIONS / f"{migration_id}.up.sql",
            migrations / f"{migration_id}.up.sql",
        )

    marker = tmp_path / "canonical-called"
    captured = tmp_path / "canonical-env"
    migration_lines = "\n".join(f"  {migration_id}" for migration_id in approved)
    _write_executable(
        scripts / "apply-production.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail
PRODUCTION_MIGRATIONS=(
{migration_lines}
)
[[ "${{TIDE_COMPANY_TEST_MIGRATION_MODE:-}}" == "true" ]]
[[ "${{TIDE_MIGRATION_TEST_MODE:-}}" == "false" ]]
[[ "${{TIDE_MIGRATION_TARGET:-}}" == "0040_g02_document_read_status" ]]
[[ "${{TIDE_MIGRATION_EXPECTED_DATABASE:-}}" == "tit_growth_test_v2" ]]
[[ "${{TIDE_COMPANY_TEST_DB_HOST:-}}" == "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz" ]]
[[ "${{TIDE_COMPANY_TEST_DB_PORT:-}}" == "5432" ]]
[[ "${{TIDE_COMPANY_TEST_DB_USER:-}}" == "postgres" ]]
[[ "${{TIDE_COMPANY_TEST_DB_NAME:-}}" == "tit_growth_test_v2" ]]
[[ "${{TIDE_COMPANY_TEST_DB_SSLMODE:-}}" == "disable" ]]
[[ "${{PGPASSWORD:-}}" == "${{FAKE_EXPECTED_PASSWORD}}" ]]
printf '%s' "${{TIDE_COMPANY_TEST_MIGRATION_MODE}}|${{TIDE_MIGRATION_TEST_MODE}}|${{TIDE_MIGRATION_TARGET}}" >"${{FAKE_CAPTURED_ENV}}"
printf called >"${{FAKE_CANONICAL_MARKER}}"
""",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    query_log = tmp_path / "psql.log"
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
set -euo pipefail
stdin_payload="$(cat || true)"
request="$*"$'\n'"${stdin_payload}"
printf '%s\n<END>\n' "${request}" >>"${FAKE_QUERY_LOG}"
if [[ "${request}" == *"current_database()"* && "${request}" == *"server_version_num"* ]]; then
  printf 'tit_growth_test_v2|postgres|postgres|t|f|t\n'
elif [[ "${request}" == *"public.alembic_version"* ]]; then
  if [[ "${FAKE_SCENARIO}" == "bad-public" ]]; then
    printf '20260810_50_g04_sections\n'
  else
    printf '20260811_57_g02_document\n'
  fi
elif [[ "${request}" == *"to_regclass('tide.schema_migrations')"* ]]; then
  printf 't\n'
elif [[ "${request}" == *"ORDER BY migration_order DESC LIMIT 1"* ]]; then
  if [[ -f "${FAKE_CANONICAL_MARKER}" ]]; then
    printf '0040_g02_document_read_status\n'
  else
    printf '0038_personalized_environment_photo\n'
  fi
elif [[ "${request}" == *"SELECT migration_order, migration_id, filename, sha256"* ]]; then
  if [[ -f "${FAKE_CANONICAL_MARKER}" ]]; then
    cat "${FAKE_TARGET_MANIFEST}"
  elif [[ "${FAKE_SCENARIO}" == "bad-ledger" ]]; then
    sed '1s/[0-9a-f]$/0/' "${FAKE_BASELINE_MANIFEST}"
  else
    cat "${FAKE_BASELINE_MANIFEST}"
  fi
elif [[ "${request}" == *"task_step_progress_g02_read_status_check"* ]]; then
  [[ -f "${FAKE_CANONICAL_MARKER}" ]]
  printf 't\n'
else
  printf 'unexpected psql request: %s\n' "${request}" >&2
  exit 91
fi
""",
    )

    baseline_manifest = tmp_path / "baseline.manifest"
    baseline_manifest.write_text(
        _ledger_manifest(baseline, migrations) + "\n",
        encoding="utf-8",
    )
    target_manifest = tmp_path / "target.manifest"
    target_manifest.write_text(
        _ledger_manifest(approved, migrations) + "\n",
        encoding="utf-8",
    )

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    env_file = secrets / "company-test.env"
    password = "do-not-print=this-secret"
    env_file.write_text(
        "\n".join(
            (
                "TIDE_ADMIN_DB_HOST=ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz",
                "TIDE_ADMIN_DB_PORT=5432",
                "TIDE_ADMIN_DB_USER=postgres",
                "TIDE_ADMIN_DB_NAME=tit_growth_test_v2",
                f"TIDE_ADMIN_DB_PASSWORD={password}",
                "TIDE_ADMIN_DB_SSLMODE=disable",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    return {
        "source": source,
        "wrapper": sandbox_wrapper,
        "fake_bin": fake_bin,
        "marker": marker,
        "captured": captured,
        "query_log": query_log,
        "baseline_manifest": baseline_manifest,
        "target_manifest": target_manifest,
        "env_file": env_file,
        "password": password,
    }


def _run_sandbox(
    sandbox: dict[str, Path | str],
    *,
    scenario: str = "success",
    extra_env: dict[str, str] | None = None,
    env_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "PATH": f"{sandbox['fake_bin']}:{os.environ['PATH']}",
        "FAKE_SCENARIO": scenario,
        "FAKE_CANONICAL_MARKER": str(sandbox["marker"]),
        "FAKE_CAPTURED_ENV": str(sandbox["captured"]),
        "FAKE_QUERY_LOG": str(sandbox["query_log"]),
        "FAKE_BASELINE_MANIFEST": str(sandbox["baseline_manifest"]),
        "FAKE_TARGET_MANIFEST": str(sandbox["target_manifest"]),
        "FAKE_EXPECTED_PASSWORD": str(sandbox["password"]),
    }
    if extra_env:
        environment.update(extra_env)
    selected_env_file = env_file or Path(sandbox["env_file"])
    return subprocess.run(
        [str(sandbox["wrapper"]), str(selected_env_file)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_company_test_increment_runs_only_the_approved_canonical_target(
    tmp_path: Path,
) -> None:
    sandbox = _prepare_sandbox(tmp_path)

    result = _run_sandbox(sandbox)

    assert result.returncode == 0, result.stderr
    assert Path(sandbox["marker"]).is_file()
    assert Path(sandbox["captured"]).read_text(encoding="utf-8") == (
        "true|false|0040_g02_document_read_status"
    )
    assert "0038 -> 0039 -> 0040" in result.stdout
    assert str(sandbox["password"]) not in result.stdout
    assert str(sandbox["password"]) not in result.stderr
    assert str(sandbox["password"]) not in Path(sandbox["query_log"]).read_text(
        encoding="utf-8"
    )


def test_company_test_increment_rejects_generic_test_mode_before_connecting(
    tmp_path: Path,
) -> None:
    sandbox = _prepare_sandbox(tmp_path)

    result = _run_sandbox(
        sandbox,
        extra_env={"TIDE_MIGRATION_TEST_MODE": "true"},
    )

    assert result.returncode != 0
    assert "通用 TIDE_MIGRATION_TEST_MODE 绕过" in result.stderr
    assert not Path(sandbox["query_log"]).exists()
    assert not Path(sandbox["marker"]).exists()


def test_company_test_increment_rejects_config_inside_git_or_wrong_mode(
    tmp_path: Path,
) -> None:
    sandbox = _prepare_sandbox(tmp_path)
    inside_git = Path(sandbox["source"]) / "company-test.env"
    shutil.copy2(Path(sandbox["env_file"]), inside_git)
    inside_git.chmod(0o600)

    git_result = _run_sandbox(sandbox, env_file=inside_git)

    assert git_result.returncode != 0
    assert "Git/镜像工作区之外" in git_result.stderr
    assert not Path(sandbox["query_log"]).exists()

    Path(sandbox["env_file"]).chmod(0o640)
    mode_result = _run_sandbox(sandbox)

    assert mode_result.returncode != 0
    assert "权限必须精确为 600" in mode_result.stderr
    assert not Path(sandbox["query_log"]).exists()
    assert not Path(sandbox["marker"]).exists()


def test_company_test_increment_fails_closed_on_public_or_teacher_drift(
    tmp_path: Path,
) -> None:
    public_sandbox = _prepare_sandbox(tmp_path / "public")

    public_result = _run_sandbox(public_sandbox, scenario="bad-public")

    assert public_result.returncode != 0
    assert "public Head 必须精确" in public_result.stderr
    assert not Path(public_sandbox["marker"]).exists()
    assert str(public_sandbox["password"]) not in public_result.stderr

    ledger_sandbox = _prepare_sandbox(tmp_path / "ledger")
    ledger_result = _run_sandbox(ledger_sandbox, scenario="bad-ledger")

    assert ledger_result.returncode != 0
    assert "顺序、文件名或 SHA-256" in ledger_result.stderr
    assert not Path(ledger_sandbox["marker"]).exists()
    assert str(ledger_sandbox["password"]) not in ledger_result.stderr
