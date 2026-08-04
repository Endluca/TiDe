from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
ROOT_README = ROOT / "README.md"
GAEA_DIR = ROOT / "gaea"
DOCKERFILE = GAEA_DIR / "Dockerfile"
README = GAEA_DIR / "README.md"
HEALTHCHECK = GAEA_DIR / "bin" / "healthcheck.sh"
NGINX_CONF = GAEA_DIR / "nginx" / "nginx.conf"
TEACHER_CONF = GAEA_DIR / "nginx" / "teacher.conf"
RENDER_NGINX = GAEA_DIR / "bin" / "render-nginx-conf.sh"
RENDER_REAL_IP = GAEA_DIR / "bin" / "render-real-ip-conf.py"
S6_DIR = GAEA_DIR / "s6-rc.d"
TEACHER_COMPANY_TEST_MIGRATOR = (
    ROOT
    / "teacher"
    / "backend"
    / "database"
    / "scripts"
    / "apply-company-test.sh"
)
TEACHER_BACKEND_DOCKERFILE = ROOT / "teacher" / "backend" / "Dockerfile"
TEACHER_BUILD_TSCONFIG = ROOT / "teacher" / "backend" / "tsconfig.build.json"
TEACHER_MAIN = ROOT / "teacher" / "backend" / "src" / "main.ts"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
RUNTIME_SECURITY = ROOT / "project-context" / "RUNTIME_DATA_SECURITY.md"


def test_gaea_uses_one_project_and_one_image() -> None:
    assert DOCKERFILE.is_file()
    assert not (GAEA_DIR / "gaea.yml").exists()
    assert not (GAEA_DIR / "operations" / "Dockerfile").exists()
    assert not (GAEA_DIR / "score-settlement" / "Dockerfile").exists()
    assert list(GAEA_DIR.glob("*/Dockerfile")) == []


def test_company_test_migrator_reads_back_multi_replica_contract() -> None:
    script = TEACHER_COMPANY_TEST_MIGRATOR.read_text(encoding="utf-8")
    verification = script.split(
        'verification="$("${APP_PSQL[@]}" -Atqc "', 1
    )[1].split('if [[ "${verification}"', 1)[0]

    for relation in (
        "tide.job_leases",
        "tide.job_leases_expiry_idx",
        "tide.teacher_photo_runs_pending_claim_idx",
        "tide.analytics_task_event_semantics_v2",
        "tide.analytics_actor_task_journey_v2",
        "tide.analytics_task_assignment_funnel_v2",
        "tide.analytics_task_funnel_v2",
        "tide.analytics_task_step_funnel_v2",
        "tide.analytics_content_quality_v2",
    ):
        assert f"to_regclass('{relation}') is not null" in verification

    for column in (
        "processing_owner",
        "lease_expires_at",
        "attempt_count",
        "next_attempt_at",
    ):
        assert f"'{column}'" in verification

    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert (
            "to_regclass('tide.job_leases'),\n"
            f"        '{privilege}'"
        ) in verification

    for mapping in (
        "('G01:v1', 'G01', 'PUBLISHED', 'ACTIVE')",
        "('G02:v1', 'G04', 'PUBLISHED', 'ACTIVE')",
        "('G03:v1', 'G02', 'PUBLISHED', 'ACTIVE')",
        "('G04:v1', 'G03', 'PUBLISHED', 'ACTIVE')",
        "('G05:v1', 'G00', 'RETIRED', 'RETIRED')",
        "('G06:v1', 'G05', 'PUBLISHED', 'ACTIVE')",
        "('G07:v1', 'G06', 'PUBLISHED', 'ACTIVE')",
        "('G08:v1', 'G07', 'PUBLISHED', 'ACTIVE')",
        "('G09:v1', 'G08', 'PUBLISHED', 'ACTIVE')",
        "('G10:v1', 'G09', 'PUBLISHED', 'ACTIVE')",
    ):
        assert mapping in verification

    expected = script.split('if [[ "${verification}" != "', 1)[1].split(
        '" ]]; then', 1
    )[0]
    assert expected.endswith("|t")


def test_gaea_image_builds_both_frontends_and_both_backends() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "hub.51talk.biz/library/node:22-alpine AS operations-frontend-build"
        in dockerfile
    )
    assert "COPY frontend/package.json frontend/package-lock.json ./" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert "COPY --chown=gaea:gaea backend/app ./operations/app" in dockerfile
    assert "COPY --chown=gaea:gaea backend/scripts ./operations/scripts" in dockerfile
    assert "--from=operations-frontend-build /build/frontend/dist" in dockerfile
    assert "./operations/app/static" in dockerfile
    assert "TIT_FRONTEND_REQUIRED=true" in dockerfile

    assert (
        "hub.51talk.biz/library/node:22-alpine AS teacher-frontend-build"
        in dockerfile
    )
    assert (
        "COPY teacher/frontend/package.json teacher/frontend/pnpm-lock.yaml ./"
        in dockerfile
    )
    assert "ARG VITE_API_BASE_URL=https://tide.51talk.com" in dockerfile
    assert "tide-camp-teacher.test.51talk.biz" not in dockerfile
    assert "RUN pnpm run build:nginx" in dockerfile
    assert "--from=teacher-frontend-build /build/teacher-frontend/dist" in dockerfile
    assert "/usr/share/nginx/teacher" in dockerfile

    assert (
        "hub.51talk.biz/library/node:22-alpine AS teacher-backend-build"
        in dockerfile
    )
    assert (
        "COPY teacher/backend/package.json teacher/backend/pnpm-lock.yaml"
        in dockerfile
    )
    assert "RUN pnpm run build" in dockerfile
    assert "pnpm prune --prod" in dockerfile
    assert "--from=teacher-backend-build /usr/local/bin/node" in dockerfile
    assert "--from=teacher-backend-build /build/teacher-backend/dist" in dockerfile
    assert "require('/app/teacher/node_modules/sharp')" in dockerfile

    assert "hub.51talk.biz/library/python:3.12-alpine AS python-build" in dockerfile
    assert "--from=python-build /opt/venv /opt/venv" in dockerfile


def test_teacher_backend_build_output_matches_runtime_entrypoints() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    teacher_dockerfile = TEACHER_BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    teacher_run = (S6_DIR / "teacher-api" / "run").read_text(encoding="utf-8")
    build_config = json.loads(TEACHER_BUILD_TSCONFIG.read_text(encoding="utf-8"))

    # Docker stages only copy src/. Keep the compiler root explicit so clean
    # image builds and full local checkouts emit the same runtime entrypoint.
    assert build_config["compilerOptions"]["rootDir"] == "."
    assert "COPY teacher/backend/src ./src" in dockerfile
    assert "test -f dist/src/main.js" in dockerfile
    assert "test -f dist/src/main.js" in teacher_dockerfile
    assert "node /app/teacher/dist/src/main.js" in teacher_run


def test_gaea_image_uses_internal_sources_and_s6_supervision() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    from_lines = [
        line for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]
    assert from_lines
    assert all("hub.51talk.biz/" in line for line in from_lines)
    assert "https://repo.bjtest.51talk.biz/repository/npm/" in dockerfile
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert "mirrors.ustc.edu.cn" in dockerfile
    assert "https://flow.51talk.biz/pkg/ci/files/files" in dockerfile
    assert "addgroup -g 1001 gaea" in dockerfile
    assert "adduser -u 1001 -G gaea -D gaea" in dockerfile
    assert "ENV TZ=Asia/Shanghai" in dockerfile
    assert "S6_KEEP_ENV=1" in dockerfile
    assert "S6_CMD_RECEIVE_SIGNALS=1" in dockerfile
    assert 'ENTRYPOINT ["/init"]' in dockerfile
    assert 'CMD ["sleep", "infinity"]' in dockerfile
    assert "USER gaea" not in dockerfile
    assert "nginx -t" in dockerfile

    for service in ("operations", "teacher-api", "score-settlement"):
        run_script = (S6_DIR / service / "run").read_text(encoding="utf-8")
        assert "s6-setuidgid gaea" in run_script

    teacher_web_run = (S6_DIR / "teacher-web" / "run").read_text(
        encoding="utf-8"
    )
    assert "s6-setuidgid" not in teacher_web_run
    assert "exec nginx -g \"daemon off;\"" in teacher_web_run
    assert "user gaea;" in NGINX_CONF.read_text(encoding="utf-8")


def test_gaea_prepares_every_nginx_temp_path_before_build_check() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    nginx_conf = NGINX_CONF.read_text(encoding="utf-8")
    teacher_web_run = (S6_DIR / "teacher-web" / "run").read_text(
        encoding="utf-8"
    )
    build_setup = dockerfile.split(
        "RUN rm -f /etc/nginx/http.d/default.conf", 1
    )[1].split("&& nginx -t", 1)[0]
    build_mkdir = build_setup.split("&& mkdir -p", 1)[1].split(
        "&& ln -sf", 1
    )[0]
    build_chown = build_setup.split("&& chown -R gaea:gaea", 1)[1]

    temp_paths = (
        "/tmp/tide-nginx/client-body",
        "/tmp/tide-nginx/proxy",
        "/tmp/tide-nginx/fastcgi",
        "/tmp/tide-nginx/uwsgi",
        "/tmp/tide-nginx/scgi",
    )
    configured_temp_paths = {
        line.strip().split()[1].removesuffix(";")
        for line in nginx_conf.splitlines()
        if "_temp_path " in line
    }
    assert configured_temp_paths == set(temp_paths)
    for temp_path in configured_temp_paths:
        assert temp_path in build_mkdir
        assert temp_path in teacher_web_run

    assert "/tmp/tide-nginx" in build_chown


def test_gaea_renders_bounded_nginx_workers_at_runtime() -> None:
    nginx_conf = NGINX_CONF.read_text(encoding="utf-8")
    renderer = RENDER_NGINX.read_text(encoding="utf-8")
    teacher_web_run = (S6_DIR / "teacher-web" / "run").read_text(
        encoding="utf-8"
    )

    assert "worker_processes ${NGINX_WORKER_PROCESSES};" in nginx_conf
    assert "worker_processes auto;" not in nginx_conf
    assert "micro|small|medium|large" in renderer
    assert "4xlarge|8xlarge|16xlarge|32xlarge|64xlarge" in renderer
    assert "NGINX_WORKER_PROCESSES=16" in renderer
    assert "NGINX_WORKER_PROCESSES=2" in renderer
    assert "envsubst '${NGINX_WORKER_PROCESSES}'" in renderer
    assert "render-nginx-conf.sh" in teacher_web_run


def test_gaea_exposes_two_domains_on_two_ports() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    teacher_conf = TEACHER_CONF.read_text(encoding="utf-8")

    assert "EXPOSE 8010 8080" in dockerfile
    assert "listen 8080;" in teacher_conf
    assert "absolute_redirect off;" in teacher_conf
    assert "port_in_redirect off;" in teacher_conf
    assert "proxy_pass http://127.0.0.1:3000;" in teacher_conf
    assert "location /api/" in teacher_conf
    assert "location = /healthz" in teacher_conf
    assert "location = /health/ready" in teacher_conf
    assert "proxy_pass http://127.0.0.1:3000/health/ready;" in teacher_conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in teacher_conf
    assert teacher_conf.count("add_header X-Content-Type-Options") == 3
    assert teacher_conf.count("add_header X-Frame-Options") == 3
    assert teacher_conf.count("add_header Referrer-Policy") == 3
    assert "/app/operations/scripts/run_api.py" in (
        S6_DIR / "operations" / "run"
    ).read_text(encoding="utf-8")

    teacher_run = (S6_DIR / "teacher-api" / "run").read_text(encoding="utf-8")
    teacher_main = TEACHER_MAIN.read_text(encoding="utf-8")
    assert "BIND_HOST=127.0.0.1" in teacher_run
    assert "FILE_UPLOAD_MAX_BYTES=10485760" in teacher_run
    assert "app.listen(port, bindHost)" in teacher_main
    assert "app.listen(port, '0.0.0.0')" not in teacher_main


def test_gaea_only_trusts_explicit_ingress_networks() -> None:
    renderer = RENDER_REAL_IP.read_text(encoding="utf-8")
    teacher_conf = TEACHER_CONF.read_text(encoding="utf-8")
    teacher_web_run = (S6_DIR / "teacher-web" / "run").read_text(
        encoding="utf-8"
    )

    assert "TIDE_TRUSTED_PROXY_CIDRS or TIT_TRUSTED_PROXY_IPS is required" in renderer
    assert "ipaddress.ip_network" in renderer
    assert "network.prefixlen == 0" in renderer
    assert "set_real_ip_from" in renderer
    assert "render-real-ip-conf.py" in teacher_web_run
    assert "$http_x_forwarded_for" not in teacher_conf


def test_real_ip_renderer_rejects_full_network_and_writes_canonical_cidrs(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "real-ip.conf"
    environment = os.environ | {
        "TIDE_TRUSTED_PROXY_CIDRS": "10.20.30.40, 2001:db8::1/128"
    }
    subprocess.run(
        [sys.executable, str(RENDER_REAL_IP), str(destination)],
        check=True,
        env=environment,
    )
    assert destination.read_text(encoding="ascii") == (
        "set_real_ip_from 10.20.30.40/32;\n"
        "set_real_ip_from 2001:db8::1/128;\n"
        "real_ip_header X-Forwarded-For;\n"
        "real_ip_recursive on;\n"
    )

    rejected = subprocess.run(
        [sys.executable, str(RENDER_REAL_IP), str(destination)],
        check=False,
        capture_output=True,
        env=os.environ | {"TIDE_TRUSTED_PROXY_CIDRS": "0.0.0.0/0"},
        text=True,
    )
    assert rejected.returncode != 0
    assert "must not trust an entire address family" in rejected.stderr


def test_gaea_supervises_all_processes_and_checks_all_boundaries() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    healthcheck = HEALTHCHECK.read_text(encoding="utf-8")

    assert "HEALTHCHECK --interval=30s --timeout=20s" in dockerfile
    assert 'CMD ["/app/bin/healthcheck.sh"]' in dockerfile
    assert "http://127.0.0.1:8010/api/health" in healthcheck
    assert "http://127.0.0.1:8080/healthz" in healthcheck
    assert "http://127.0.0.1:8080/health/ready" in healthcheck
    assert "http://127.0.0.1:3000/health/ready" not in healthcheck
    assert "TIDE_TEACHER_HOST must be a hostname" in healthcheck
    assert "--healthcheck" in healthcheck
    assert "--max-heartbeat-age-seconds 90" in healthcheck

    expected_services = {
        "operations": "scripts/run_api.py",
        "teacher-api": "dist/src/main.js",
        "teacher-web": "nginx",
        "score-settlement": "settle_shared_task_scores.py",
    }
    for service, command in expected_services.items():
        service_type = (S6_DIR / service / "type").read_text(
            encoding="utf-8"
        )
        assert service_type.strip() == "longrun"
        assert command in (S6_DIR / service / "run").read_text(encoding="utf-8")
        assert (S6_DIR / service / "finish").is_file()
        assert (S6_DIR / "user" / "contents.d" / service).is_file()

    assert (S6_DIR / "teacher-web" / "dependencies.d" / "teacher-api").is_file()
    worker_run = (S6_DIR / "score-settlement" / "run").read_text(
        encoding="utf-8"
    )
    assert "--watch --max-events 25 --interval-seconds 3" in worker_run
    assert "--heartbeat-path /tmp/tit-score-worker-heartbeat" in worker_run
    assert "--heartbeat-path /tmp/tit-score-worker-heartbeat" in healthcheck
    assert "TIT_SCORE_WORKER_HEARTBEAT" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile


def test_gaea_readme_preserves_release_and_multi_replica_boundaries() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "单模块" in readme
    assert "单镜像" in readme
    assert "四个常驻进程" in readme
    assert "8010" in readme and "8080" in readme and "3000" in readme
    assert "设置为 `2` 或更高" in readme
    assert "RollingUpdate" in readme
    assert "不再要求 `Recreate`" in readme
    assert "不再为 Worker 新建 Gaea 项目" in readme
    assert "session advisory lock" in readme
    assert "standby" in readme
    assert "未持有积分 advisory lock" in readme
    assert "ReadWriteMany (RWX)" in readme
    assert "RWO 或每 Pod" in readme
    assert "heartbeat" in readme and "禁止放入共享卷" in readme
    assert "副本数必须固定为 `1`" not in readme
    assert "先缩容到 `0`" not in readme
    assert "同一 UID" in readme
    assert "tit_growth_migrator" in readme
    assert "tide_migrator" in readme
    assert "settle_shared_task_scores.py --watch" in readme
    assert "TIT_SCORE_WORKER_HEARTBEAT" in readme
    assert "TIT_BOOTSTRAP_USERNAME" in readme
    assert "TIT_BOOTSTRAP_PASSWORD" in readme
    assert "`https://tide.51talk.com`" in readme
    assert "tide-camp-teacher.test.51talk.biz" not in readme
    assert "不代表" in readme


def test_current_deployment_docs_do_not_restore_single_replica_mode() -> None:
    documents = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT_README, README, ARCHITECTURE, RUNTIME_SECURITY)
    }

    for path, document in documents.items():
        assert "整个 Pod 固定单副本" not in document, path
        assert "必须固定一个 Pod 副本" not in document, path
        assert "必须固定单副本" not in document, path
        assert "部署必须使用 Recreate" not in document, path

    assert "`2` 个或更多副本" in documents[ROOT_README]
    assert "ReadWriteMany (RWX)" in documents[ROOT_README]
    assert "整套 Pod 可以水平复制" in documents[ARCHITECTURE]
    assert "2 个或更多副本及 RollingUpdate" in documents[RUNTIME_SECURITY]


def test_gaea_build_context_includes_teacher_but_excludes_secrets() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore
    assert "**/*.pem" in dockerignore
    assert "**/*.key" in dockerignore
    assert "**/node_modules" in dockerignore
    assert "**/dist" in dockerignore
    assert "teacher/backend/storage" in dockerignore
    assert "teacher/outputs" in dockerignore
    assert "teacher/tmp" in dockerignore
    assert "teacher/frontend/.openai" in dockerignore
    assert "teacher/frontend/public/assets/tasks/**/*.mp4" in dockerignore
    assert "teacher" not in dockerignore.splitlines()
