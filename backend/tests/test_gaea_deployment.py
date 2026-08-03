from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS_DOCKERFILE = ROOT / "gaea" / "operations" / "Dockerfile"
SCORE_WORKER_DOCKERFILE = (
    ROOT / "gaea" / "score-settlement" / "Dockerfile"
)
README = ROOT / "gaea" / "README.md"


def test_gaea_image_builds_frontend_into_the_python_package() -> None:
    dockerfile = OPERATIONS_DOCKERFILE.read_text(encoding="utf-8")

    assert "hub.51talk.biz/library/node:22-alpine AS frontend-build" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert (
        "--from=frontend-build /build/frontend/dist ./app/static"
        in dockerfile
    )
    assert "TIT_FRONTEND_REQUIRED=true" in dockerfile
    assert "nginx" not in dockerfile.lower()


def test_gaea_image_uses_internal_sources_and_non_root_runtime() -> None:
    dockerfile = OPERATIONS_DOCKERFILE.read_text(encoding="utf-8")

    from_lines = [
        line for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]
    assert from_lines
    assert all("hub.51talk.biz/" in line for line in from_lines)
    assert "https://repo.bjtest.51talk.biz/repository/npm/" in dockerfile
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert dockerfile.count("mirrors.ustc.edu.cn") == 3
    assert "addgroup -g 1001 gaea" in dockerfile
    assert "adduser -u 1001 -G gaea -D gaea" in dockerfile
    assert "USER gaea" in dockerfile
    assert "ENV TZ=Asia/Shanghai" in dockerfile


def test_gaea_image_runs_only_python_and_has_a_healthcheck() -> None:
    dockerfile = OPERATIONS_DOCKERFILE.read_text(encoding="utf-8")

    assert "EXPOSE 8010" in dockerfile
    assert "HEALTHCHECK --interval=10s --timeout=3s" in dockerfile
    assert "http://127.0.0.1:8010/api/health" in dockerfile
    assert 'CMD ["python", "scripts/run_api.py"]' in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile


def test_gaea_declares_a_singleton_score_settlement_module() -> None:
    configuration = yaml.safe_load(
        (ROOT / "gaea" / "gaea.yml").read_text(encoding="utf-8")
    )
    dockerfile = SCORE_WORKER_DOCKERFILE.read_text(encoding="utf-8")

    assert configuration == {
        "multmod": True,
        "gaeamod": {
            "enable": True,
            "name": ["operations", "score-settlement"],
        },
    }
    from_lines = [
        line for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]
    assert from_lines
    assert all("hub.51talk.biz/" in line for line in from_lines)
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert dockerfile.count("mirrors.ustc.edu.cn") == 2
    assert "addgroup -g 1001 gaea" in dockerfile
    assert "USER gaea" in dockerfile
    assert "TIT_SCORE_WORKER_HEARTBEAT=/tmp/tit-score-worker-heartbeat" in dockerfile
    assert '"--healthcheck", "--max-heartbeat-age-seconds", "90"' in dockerfile
    assert (
        'CMD ["python", "scripts/settle_shared_task_scores.py", '
        '"--watch", "--max-events", "25", "--interval-seconds", "3"]'
        in dockerfile
    )
    assert "EXPOSE" not in dockerfile
    assert "/api/health" not in dockerfile
    assert "frontend" not in dockerfile.lower()


def test_gaea_readme_preserves_release_and_worker_boundaries() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "tit_growth_migrator" in readme
    assert "settle_shared_task_scores.py --watch" in readme
    assert "单实例 Worker" in readme
    assert "TIT_SCORE_WORKER_HEARTBEAT" in readme
    assert "TIT_BOOTSTRAP_USERNAME" in readme
    assert "TIT_BOOTSTRAP_PASSWORD" in readme
    assert "不代表" in readme


def test_gaea_build_context_excludes_local_secrets_and_other_apps() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore
    assert "**/*.pem" in dockerignore
    assert "**/*.key" in dockerignore
    assert "teacher" in dockerignore.splitlines()
