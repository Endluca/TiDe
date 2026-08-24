from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
ROOT_README = ROOT / "README.md"
GAEA_DIR = ROOT / "gaea"
DOCKERFILE = GAEA_DIR / "Dockerfile"
APPLICATION_DOCKERFILE = GAEA_DIR / "application" / "Dockerfile"
DTS_DOCKERFILE = GAEA_DIR / "dts-ingest" / "Dockerfile"
DTS_JAVA_DIR = GAEA_DIR / "dts-ingest" / "java"
DTS_TRANSPORT_LOGGING = (
    GAEA_DIR / "dts-ingest" / "log4j-bridge.properties"
)
DTS_DIAG_DIR = GAEA_DIR / "dts-diagnose"
DTS_DIAG_DOCKERFILE = DTS_DIAG_DIR / "Dockerfile"
DTS_DIAG_RUN = DTS_DIAG_DIR / "run.sh"
DTS_DIAG_LOGGING = DTS_DIAG_DIR / "log4j-diagnose.properties"
DTS_DIAG_SOURCE = DTS_DIAG_DIR / "SOURCE.md"
DTS_DIAG_JAR = (
    DTS_DIAG_DIR
    / "dts_subscribe_sdk_dep_demo-1.0-SNAPSHOT-jar-with-dependencies.jar"
)
GAEA_MODULES = GAEA_DIR / "gaea.yml"
README = GAEA_DIR / "README.md"
HEALTHCHECK = GAEA_DIR / "bin" / "healthcheck.sh"
RUNTIME_ENV_LOADER = GAEA_DIR / "bin" / "runtime-env.py"
APPLICATION_RUNTIME_ENV = (
    GAEA_DIR / "application" / "application.runtime.env.example"
)
SOURCE_WIDE_ENABLED = GAEA_DIR / "bin" / "source-wide-enabled.sh"
NGINX_CONF = GAEA_DIR / "nginx" / "nginx.conf"
TEACHER_CONF = GAEA_DIR / "nginx" / "teacher.conf"
RENDER_NGINX = GAEA_DIR / "bin" / "render-nginx-conf.sh"
RENDER_REAL_IP = GAEA_DIR / "bin" / "render-real-ip-conf.py"
S6_DIR = GAEA_DIR / "s6-rc.d"
DTS_OVS_ENV = ROOT / "backend" / ".env.dts-ingest.ovs.production.example"
DTS_DOM_ENV = ROOT / "backend" / ".env.dts-ingest.dom.production.example"
DTS_GENERIC_ENV = ROOT / "backend" / ".env.dts-ingest.production.example"
DTS_PRE_SSL_OFF_ENV = (
    ROOT / "backend" / "dts-ingest.pre-ssl-off.env.example"
)
DTS_REQUIREMENTS = ROOT / "backend" / "requirements-dts-ingest.txt"
APPLICATION_ENV = ROOT / "backend" / ".env.production.example"
COMBINED_ENV = ROOT / "deploy" / "combined" / ".env.example"
TEACHER_COMPANY_TEST_MIGRATOR = (
    ROOT
    / "teacher"
    / "backend"
    / "database"
    / "scripts"
    / "apply-company-test.sh"
)
TEACHER_PRODUCTION_MIGRATOR = (
    ROOT
    / "teacher"
    / "backend"
    / "database"
    / "scripts"
    / "apply-production.sh"
)
TEACHER_TASK_CATALOG_SYNC = (
    ROOT / "teacher" / "backend" / "scripts" / "sync-current-task-catalog.ts"
)
TEACHER_CRUD_GRANT = (
    ROOT
    / "teacher"
    / "backend"
    / "database"
    / "scripts"
    / "grant-tit-teacher-crud.sql"
)
TEACHER_BACKEND_DOCKERFILE = ROOT / "teacher" / "backend" / "Dockerfile"
TEACHER_BUILD_TSCONFIG = ROOT / "teacher" / "backend" / "tsconfig.build.json"
TEACHER_MAIN = ROOT / "teacher" / "backend" / "src" / "main.ts"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
RUNTIME_SECURITY = ROOT / "project-context" / "RUNTIME_DATA_SECURITY.md"


def test_gaea_keeps_the_application_root_and_routes_dts_modules() -> None:
    assert DOCKERFILE.is_file()
    assert GAEA_MODULES.read_text(encoding="utf-8") == (
        "multmod: true\n"
        "gaeamod:\n"
        "  enable: true\n"
        "  name:\n"
        "    - application\n"
        "    - dts-ingest\n"
        "    - dts-diagnose\n"
    )
    assert APPLICATION_DOCKERFILE.read_bytes() == DOCKERFILE.read_bytes()
    assert DTS_DOCKERFILE.is_file()
    assert not (GAEA_DIR / "operations" / "Dockerfile").exists()
    assert not (GAEA_DIR / "score-settlement" / "Dockerfile").exists()
    assert not (GAEA_DIR / "source-wide" / "Dockerfile").exists()
    assert set(GAEA_DIR.glob("*/Dockerfile")) == {
        APPLICATION_DOCKERFILE,
        DTS_DOCKERFILE,
        DTS_DIAG_DOCKERFILE,
    }


def test_gaea_official_dts_diagnostic_is_pinned_and_verbose() -> None:
    expected_sha256 = (
        "8a1c484a7c01fc5e684b57eb720a4757"
        "f3652027c6d1fea41bc5f69451556ef0"
    )
    jar_bytes = DTS_DIAG_JAR.read_bytes()
    dockerfile = DTS_DIAG_DOCKERFILE.read_text(encoding="utf-8")
    runner = DTS_DIAG_RUN.read_text(encoding="utf-8")
    logging_config = DTS_DIAG_LOGGING.read_text(encoding="utf-8")
    source = DTS_DIAG_SOURCE.read_text(encoding="utf-8")

    assert len(jar_bytes) == 13_917_673
    assert hashlib.sha256(jar_bytes).hexdigest() == expected_sha256
    with zipfile.ZipFile(DTS_DIAG_JAR) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8")
        kafka_version = archive.read("kafka/kafka-version.properties").decode(
            "utf-8"
        )
    assert "Main-Class: com.aliyun.dts.subscribe.clients.DTSConsumerDemo" in manifest
    assert "version=1.0.0" in kafka_version

    assert "hub.51talk.biz/runtime/oraclejdk:1.8-debian11" in dockerfile
    assert "useradd --uid 1001" in dockerfile
    assert "USER gaea" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert 'CMD ["/deployments/bin/run.sh"]' in dockerfile
    assert expected_sha256 in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "curl " not in dockerfile
    assert "wget " not in dockerfile

    syntax = subprocess.run(
        ["sh", "-n", str(DTS_DIAG_RUN)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    for mapping in (
        "brokerUrl=%s",
        "topic=%s",
        "sid=%s",
        "userName=%s",
        "password=%s",
        "initCheckpoint=%s",
        "subscribeMode=ASSIGN",
        "isForceUseInitCheckpoint=true",
    ):
        assert mapping in runner
    assert "kafka.request.timeout.ms=" not in runner
    assert "api.version.auto.timeout.ms=" not in runner
    assert 'chmod 0600 "$CONFIG_PATH"' in runner
    assert "set -x" not in runner
    assert '-cp "${LOG_CONFIG_DIR}:${JAR_PATH}"' in runner
    assert "com.aliyun.dts.subscribe.clients.DTSConsumerDemo" in runner
    assert "java_exited" in runner

    for logger in (
        "com.aliyun.dts.subscribe",
        "org.apache.kafka=DEBUG",
        "org.apache.kafka.clients.NetworkClient=TRACE",
        "org.apache.kafka.clients.Metadata=DEBUG",
        "org.apache.kafka.common.network.Selector=TRACE",
        "org.apache.kafka.common.security.authenticator.SaslClientAuthenticator=DEBUG",
        "org.apache.kafka.clients.consumer.internals.ConsumerNetworkClient=DEBUG",
        "org.apache.kafka.clients.consumer.internals.Fetcher=DEBUG",
        "org.apache.kafka.clients.consumer.internals.SubscriptionState=DEBUG",
    ):
        assert logger in logging_config
    assert "dts-new-subscribe.log" in logging_config
    assert "MaxFileSize=100MB" in logging_config
    assert "MaxBackupIndex=5" in logging_config
    assert expected_sha256 in source
    assert "48596de62f01ea7d4b84082b562c33e9e5350287" in source


def test_gaea_dts_module_omits_the_application_build_graph() -> None:
    dockerfile = DTS_DOCKERFILE.read_text(encoding="utf-8")
    full_requirements = {
        line.strip()
        for line in (ROOT / "backend" / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    dts_requirements = {
        line.strip()
        for line in DTS_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert dts_requirements == {
        "SQLAlchemy==2.0.36",
        "psycopg[binary]==3.2.4",
        "fastavro==1.12.2",
        "kafka-python==2.2.20",
        "lz4==4.4.5",
    }
    assert dts_requirements < full_requirements

    java_build = dockerfile.split(
        "FROM hub.51talk.biz/ci/maven:oraclejdk1.8-maven3 AS java-build",
        1,
    )[1]
    dts_build = java_build.split(
        "FROM hub.51talk.biz/library/python:3.12-alpine AS python-build",
        1,
    )[1]
    dts_runtime = dts_build.split(
        "FROM hub.51talk.biz/library/python:3.12-alpine AS runtime",
        1,
    )[1]
    assert "COPY gaea/dts-ingest/java /build/src" in java_build
    assert "javac -encoding UTF-8 -source 1.8 -target 1.8" in java_build
    assert (
        "com.aliyun.dts.subscribe.clients."
        "TitDtsTransportBridgeAvroSelfTest"
    ) in java_build
    assert "dts-diagnose.jar" in java_build
    assert "sha256sum -c -" in java_build
    assert "COPY backend/requirements-dts-ingest.txt" in dts_build
    assert "COPY backend/requirements.txt" not in dts_build
    assert "python -m venv /opt/venv" in dts_build
    assert "COPY --chown=gaea:gaea backend/app ./app" in dts_runtime
    assert "run_dts_ingest.py" in dts_runtime
    assert "TIT_PROCESS_PROFILE=dts-ingest" in dts_runtime
    assert "TIT_DTS_TRANSPORT=official_java" in dts_runtime
    assert "TIT_DTS_PIPELINE_MODE=SINGLE_PIPELINE" in dts_runtime
    assert "TIT_DTS_STARTUP_RETRY_SECONDS=15" in dts_runtime
    assert "/deployments/dts-transport.jar" in dts_runtime
    assert "/deployments/dts-diagnose.jar" in dts_runtime
    assert "/deployments/config/log4j.properties" in dts_runtime
    assert "USER gaea" in dts_runtime
    assert "HEALTHCHECK" in dts_runtime
    assert "STOPSIGNAL SIGTERM" in dts_runtime
    assert "node_modules" not in dts_runtime
    assert "nginx" not in dts_runtime.lower()
    assert "s6-overlay" not in dts_runtime
    assert "frontend" not in dts_runtime.lower()
    assert dockerfile.count("FROM ") == 3


def test_gaea_dts_module_uses_internal_sources_and_non_root_runtime() -> None:
    dockerfile = DTS_DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [
        line for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]

    assert len(from_lines) == 3
    assert all("hub.51talk.biz/" in line for line in from_lines)
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert "mirrors.ustc.edu.cn" in dockerfile
    assert "addgroup -g 1001 gaea" in dockerfile
    assert "adduser -u 1001 -G gaea -D gaea" in dockerfile
    assert "openjdk8-jre-base" in dockerfile
    assert "gcompat" in dockerfile
    assert "--start-period=360s" in dockerfile
    assert "ENV TZ=Asia/Shanghai" in dockerfile
    assert "USER gaea" in dockerfile
    assert 'ENTRYPOINT ["/init"]' not in dockerfile
    assert "HEALTHCHECK --interval=30s --timeout=20s" in dockerfile
    assert 'CMD ["/opt/venv/bin/python"' in dockerfile
    assert '"--max-messages", "2000"' in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    for excluded in (
        "repo.bjtest.51talk.biz/repository/npm/",
        "pnpm",
        "npm ci",
        "nginx",
        "s6-overlay",
        "teacher/frontend",
        "teacher/backend",
        "frontend/package.json",
        "backend/migrations",
    ):
        assert excluded not in dockerfile


def test_gaea_dts_official_transport_keeps_protocol_stdout_clean() -> None:
    dockerfile = DTS_DOCKERFILE.read_text(encoding="utf-8")
    logging_config = DTS_TRANSPORT_LOGGING.read_text(encoding="utf-8")
    java_sources = tuple(DTS_JAVA_DIR.rglob("*.java"))

    assert java_sources
    assert (
        "-Dlog4j.configuration=file:/deployments/config/log4j.properties"
        in dockerfile
    )
    assert (
        "COPY --chown=gaea:gaea gaea/dts-ingest/log4j-bridge.properties "
        "/deployments/config/log4j.properties"
    ) in dockerfile
    assert "log4j.appender.STDERR.Target=System.err" in logging_config
    assert "System.out" not in logging_config


def _bash_array(script: str, name: str) -> tuple[str, ...]:
    body = script.split(f"{name}=(", 1)[1].split("\n)", 1)[0]
    return tuple(
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_company_test_initializer_reads_back_multi_replica_contract() -> None:
    script = TEACHER_COMPANY_TEST_MIGRATOR.read_text(encoding="utf-8")
    verification = script.split(
        'verification="$("${APP_PSQL[@]}" -Atq <<\'SQL\'\n', 1
    )[1].split('\nSQL\n)"\nif [[ "${verification}"', 1)[0]

    for relation in (
        "tide.job_leases",
        "tide.job_leases_expiry_idx",
        "tide.analytics_task_event_semantics_v2",
        "tide.analytics_actor_task_journey_v2",
        "tide.analytics_task_assignment_funnel_v2",
        "tide.analytics_task_funnel_v2",
        "tide.analytics_task_step_funnel_v2",
        "tide.analytics_content_quality_v2",
    ):
        assert f"to_regclass('{relation}') is not null" in verification

    for retired_relation in (
        "tide.outcome_projections",
        "tide.camp_enrollment_projections",
        "tide.audit_events",
        "tide.task_template_files",
        "tide.file_migrations",
        "tide.teacher_photo_runs",
        "tide.analytics_actor_task_journey_v1",
        "tide.analytics_task_assignment_funnel_v1",
        "tide.analytics_task_funnel_v1",
        "tide.analytics_task_step_funnel_v1",
        "tide.analytics_content_quality_v1",
    ):
        assert f"to_regclass('{retired_relation}') is null" in verification

    assert "column_name = 'visibility'" in verification
    assert "to_regprocedure('tide.enforce_outbox_target()') is null" in verification

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
        "('G06:v1', 'G05', 'PUBLISHED', 'ACTIVE')",
        "('G07:v1', 'G06', 'PUBLISHED', 'ACTIVE')",
        "('G08:v1', 'G07', 'PUBLISHED', 'ACTIVE')",
        "('G09:v1', 'G08', 'PUBLISHED', 'ACTIVE')",
        "('G10:v1', 'G09', 'PUBLISHED', 'ACTIVE')",
    ):
        assert mapping in verification
    assert "execution.task_code = 'G00'" in verification
    assert "execution.shared_template_row_id = 'G05:v1'" in verification
    assert "execution.status <> 'RETIRED'" in verification

    expected = script.split('if [[ "${verification}" != "', 1)[1].split(
        '" ]]; then', 1
    )[0]
    assert expected.endswith("|t")


def test_company_test_initializer_exactly_gates_personalized_photo_contract() -> None:
    script = TEACHER_COMPANY_TEST_MIGRATOR.read_text(encoding="utf-8")
    pre_gate = script.split(
        'canonical_schema_ready="$("${ADMIN_PSQL[@]}" -Atq <<\'SQL\'\n', 1
    )[1].split('\nSQL\n)"\nif [[ "${canonical_schema_ready}"', 1)[0]
    verification = script.split(
        'verification="$("${APP_PSQL[@]}" -Atq <<\'SQL\'\n', 1
    )[1].split('\nSQL\n)"\nif [[ "${verification}"', 1)[0]

    pre_start = pre_gate.index("shared_template_row_id = 'P-FB-NEGATIVE:v1'")
    verification_start = verification.index(
        "shared_template_row_id = 'P-FB-NEGATIVE:v1'"
    )
    verification_end = verification.index(
        "to_regclass('tide.analytics_task_event_semantics_v2')",
        verification_start,
    )
    personalized_blocks = (
        pre_gate[pre_start:],
        verification[verification_start:verification_end],
    )

    expected_step = {
        "version": "2026-08-11-personalized-environment-photo-v1",
        "role": "ENVIRONMENT_PHOTO",
        "reviewProfile": "TEACHING_ENVIRONMENT_V1",
        "accept": ["image/jpeg"],
        "captureOnly": True,
        "maxFiles": 1,
    }
    expected_completion = {
        "requiredStepKeys": ["p-fb-negative-environment-photo"]
    }
    expected_ai = {
        "stepKey": "p-fb-negative-environment-photo",
        "criteriaVersion": "personalized-teaching-environment-2026-08-v1",
        "criteriaKeys": ["camera_angle", "lighting", "background", "dressing"],
        "allowedMimeTypes": ["image/jpeg", "image/png", "image/webp"],
        "reviewProfile": "TEACHING_ENVIRONMENT_V1",
        "systemPrompt": (
            "You strictly review teacher-submitted evidence. Return JSON only "
            'with this exact shape: {"decision":"PASS|RETRY|ERROR",'
            '"teacherReason":"teacher-safe concise message",'
            '"confidenceSummary":{},"criteria":[{"criterionKey":"one '
            'configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":'
            '"teacher-safe message or null"}]}. Include every configured '
            "criterion exactly once. Never infer a pass from the mere presence "
            "of a person or object. Use UNKNOWN whenever the visual evidence is "
            "unclear. PASS only when every configured criterion is visibly and "
            "unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR "
            "only when the file cannot be assessed. Do not expose internal risk "
            "labels or private model reasoning."
        ),
        "userText": (
            "Review this current teaching-environment photo strictly against "
            "camera angle, lighting, background and dressing only."
        ),
    }

    for block in personalized_blocks:
        configs = [
            json.loads(value)
            for value in re.findall(
                r"(?:definition|rule)\.config\s*=\s*'([^']+)'::jsonb",
                block,
            )
        ]
        assert expected_step in configs
        assert expected_completion in configs
        assert expected_ai in configs
        assert "definition.title = 'Take a teaching-environment photo'" in block
        assert "请拍摄并提交一张当前授课环境照片。" in block
        assert "已保留你完成的内容，请根据提示更新这份材料。" in block
        assert "definition.config->" not in block
        assert "rule.config->" not in block


def test_company_test_initializer_never_executes_schema_migrations() -> None:
    script = TEACHER_COMPANY_TEST_MIGRATOR.read_text(encoding="utf-8")
    grant_script = TEACHER_CRUD_GRANT.read_text(encoding="utf-8")

    assert "migration_files=(" not in script
    assert ' -f "${DB_DIR}/migrations/' not in script
    assert 'awk \'$0 != "BEGIN;"' not in script
    assert "apply-production.sh" not in script
    assert 'APPROVED_TEST_DB_NAME="tit_growth_test_v2"' in script
    assert (
        'APPROVED_TEST_DB_HOST="ai-efficiency-postgresql-20260722194941.'
        'pods.test.51talk.biz"'
    ) in script
    assert '-v app_password="${TIDE_APP_DB_PASSWORD}"' not in script
    assert r"\getenv app_password TIDE_APP_DB_PASSWORD" in script
    assert "DROP " not in grant_script.upper()
    assert (
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM tit_teacher_crud;"
        in grant_script
    )
    assert "public.teachers," in grant_script
    assert "public.teacher_g01_status_current" in grant_script
    for signature in (
        "public.create_teacher_support_ticket(",
        "public.append_teacher_support_ticket_teacher_message(",
        "public.append_teacher_support_ticket_operator_message(",
        "public.mark_teacher_support_ticket_images_deleted(",
    ):
        assert signature in grant_script
    assert (
        "FROM PUBLIC, tit_growth_app, tit_teacher_crud, "
        "tit_dts_ingest_runtime;"
    ) in grant_script
    owner_switch = grant_script.index(
        "SET LOCAL ROLE tide_support_ticket_owner;"
    )
    function_revoke = grant_script.index(
        "REVOKE ALL ON FUNCTION public.create_teacher_support_ticket("
    )
    owner_reset = grant_script.index("RESET ROLE;", function_revoke)
    tide_table_acl = grant_script.index(
        "REVOKE ALL ON ALL TABLES IN SCHEMA tide FROM tit_teacher_crud;"
    )
    assert owner_switch < function_revoke < owner_reset < tide_table_acl
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.append_teacher_support_ticket_operator_message("
    ) in grant_script
    assert ") TO tit_growth_app;" in grant_script
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tide "
        "TO tit_teacher_crud;"
    ) in grant_script
    assert (
        "REVOKE DELETE ON tide.crm_sso_logins FROM tit_teacher_crud;"
        in grant_script
    )
    assert (
        "GRANT SELECT, INSERT, UPDATE ON tide.crm_sso_logins "
        "TO tit_teacher_crud;"
    ) in grant_script
    assert "relation.relname <> 'crm_sso_logins'" in script
    assert (
        "'tit_teacher_crud', 'tide.crm_sso_logins', 'DELETE'" in script
    )
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin IN SCHEMA tide"
        in grant_script
    )
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" not in grant_script
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public"
        not in grant_script
    )
    assert "not has_table_privilege(current_user, 'public.teachers', 'INSERT')" in script
    assert "not has_table_privilege(current_user, 'public.teachers', 'UPDATE')" in script
    assert "not has_table_privilege(current_user, 'public.teachers', 'DELETE')" in script

    first_write = script.index('pnpm --dir "${DB_DIR}/.." exec ts-node')
    for guard in (
        'EXPECTED_PUBLIC_HEAD="20260824_101_dts_single_pipeline_reset"',
        'CANONICAL_TIDE_MIGRATIONS=(',
        'actual_tide_ledger_manifest=',
        'canonical_schema_ready=',
        'required_current_catalog_ready=',
    ):
        assert script.index(guard) < first_write
    for required_object in (
        "to_regclass('tide.file_objects') is not null",
        "to_regclass('tide.task_validation_rules') is not null",
        "to_regclass('tide.system_notifications') is not null",
        "to_regclass('tide.job_leases_expiry_idx') is not null",
        "to_regclass('tide.account_onboarding_states') is not null",
        "column_name = 'reached_end'",
        "task_step_progress_g02_read_status_check",
        "task_step_progress_g02_assignment_completion_check",
        "to_regclass('public.teacher_metric_snapshots') is null",
        "to_regclass('public.tide_score_policy_versions_v1') is null",
    ):
        assert required_object in script[:first_write]

    prewrite_guards = script[:first_write]
    assert '"contentVersion":"2026-08-11-g04-two-part"' in prewrite_guards
    assert (
        '"stepKeys":["g02-environment-photo","g02-courseware-confirmation"]'
        in prewrite_guards
    )
    device_guard = prewrite_guards.index(
        "definition.step_key = 'g02-device-check'"
    )
    device_guard_start = prewrite_guards.rfind(
        "not exists (",
        0,
        device_guard,
    )
    assert device_guard_start >= 0
    assert "definition.execution_version_id = execution.id" in prewrite_guards[
        device_guard_start:device_guard
    ]
    active_g04_guard = prewrite_guards.rfind(
        "from tide.task_execution_versions execution",
        0,
        device_guard_start,
    )
    assert active_g04_guard >= 0
    assert "execution.status = 'ACTIVE'" in prewrite_guards[
        active_g04_guard:device_guard
    ]


def test_company_test_initializer_requires_the_production_canonical_ledger() -> None:
    initializer = TEACHER_COMPANY_TEST_MIGRATOR.read_text(encoding="utf-8")
    production = TEACHER_PRODUCTION_MIGRATOR.read_text(encoding="utf-8")

    assert _bash_array(initializer, "CANONICAL_TIDE_MIGRATIONS") == _bash_array(
        production,
        "PRODUCTION_MIGRATIONS",
    )
    for contract in (
        "count(*) = 38",
        "min(migration_order) = 1",
        "max(migration_order) = 38",
        "count(distinct migration_order) = 38",
        "filename = migration_id || '.up.sql'",
        "select migration_order, migration_id, filename, sha256",
        '0032_first_login_onboarding',
        '0033_g01_tesol_only',
        '0037_g04_remove_device_check',
        '0038_personalized_environment_photo',
        '0039_g02_policy_document',
        '0040_g02_document_read_status',
        '0041_crm_sso_hybrid',
        '0042_g09_set_kuozhi_course',
        '0043_p_rel_execution_catalog',
    ):
        assert contract in initializer
    assert (
        "public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 "
        "-> public head 54 -> teacher 0037 -> public head 55 -> public head 56 "
        "-> teacher 0038 -> public head 57 -> teacher 0040 -> teacher 0041 "
        "-> public head 63 -> public head 64 -> public head 65 -> teacher 0042 "
            "-> public head 99/100 -> teacher 0043，并最终迁移到 public head 100"
        ) in initializer


def test_company_test_catalog_sync_keeps_unchanged_executions_stable() -> None:
    script = TEACHER_TASK_CATALOG_SYNC.read_text(encoding="utf-8")
    execution_upsert = script.split(
        "INSERT INTO tide.task_execution_versions",
        1,
    )[1].split("const actualExecution", 1)[0]

    assert "updated_at = now()" in execution_upsert
    assert ") IS DISTINCT FROM (" in execution_upsert
    assert "code: 'G04'" in script
    assert "title: 'Lesson Preparation'" in script
    assert "Lesson Preparation&Device Network Check" not in script
    for field in (
        "task_code",
        "execution_contract_version",
        "config",
        "status",
    ):
        assert f"tide.task_execution_versions.{field}" in execution_upsert
        assert f"EXCLUDED.{field}" in execution_upsert


def _run_rejected_company_initializer(
    tmp_path: Path,
    *,
    scenario: str,
) -> tuple[subprocess.CompletedProcess[str], str, bool, int]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "psql-count"
    query_log = tmp_path / "psql.log"
    pnpm_marker = tmp_path / "pnpm-called"
    old_ids = tmp_path / "old-ledger-ids"
    canonical_ids_file = tmp_path / "canonical-ledger-ids"
    production = TEACHER_PRODUCTION_MIGRATOR.read_text(encoding="utf-8")
    canonical_ids = _bash_array(production, "PRODUCTION_MIGRATIONS")
    canonical_ids_file.write_text(
        "\n".join(canonical_ids) + "\n",
        encoding="utf-8",
    )
    old_ids.write_text(
        "\n".join(
            (
                *canonical_ids[:-4],
                "0027_retire_task_business_change_view",
                "0028_remove_unused_tide_objects",
                "0029_remove_unused_columns_and_orphan_function",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count=0
if [[ -f \"${FAKE_PSQL_STATE}\" ]]; then
  count=\"$(cat \"${FAKE_PSQL_STATE}\")\"
fi
count=$((count + 1))
printf '%s' \"${count}\" >\"${FAKE_PSQL_STATE}\"
printf '%s\\n' \"$*\" >>\"${FAKE_PSQL_LOG}\"
case \"${count}\" in
  1) printf 'tit_growth_test_v2|postgres\\n' ;;
  2) printf '180004\\n' ;;
  3) printf 't\\n' ;;
  4) printf 't\\n' ;;
  5) printf 't\\n' ;;
  6) printf '20260824_101_dts_single_pipeline_reset\\n' ;;
  7)
    if [[ \"${FAKE_SCENARIO}\" == 'missing' ]]; then
      printf 'f\\n'
    else
      printf 't\\n'
    fi
    ;;
  8)
    if [[ \"${FAKE_SCENARIO}\" == 'old-ledger' ]]; then
      cat \"${FAKE_OLD_IDS}\"
    else
      cat \"${FAKE_CANONICAL_IDS}\"
    fi
    ;;
  9) printf 't\\n' ;;
  10)
    if [[ \"${FAKE_SCENARIO}\" == 'old-ledger' ]]; then
      printf '0029_remove_unused_columns_and_orphan_function\\n'
    else
      printf 'invalid-ledger-manifest\\n'
    fi
    ;;
  *) printf 'unexpected psql call %s\\n' \"${count}\" >&2; exit 91 ;;
esac
""",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    fake_pnpm = fake_bin / "pnpm"
    fake_pnpm.write_text(
        "#!/usr/bin/env bash\nprintf called >\"${FAKE_PNPM_MARKER}\"\n",
        encoding="utf-8",
    )
    fake_pnpm.chmod(0o755)

    env_file = tmp_path / "company-test.env"
    env_file.write_text(
        "\n".join(
            (
                "TIDE_ADMIN_DB_HOST="
                + (
                    "db.example"
                    if scenario == "unapproved-target"
                    else "ai-efficiency-postgresql-20260722194941."
                    "pods.test.51talk.biz"
                ),
                "TIDE_ADMIN_DB_PORT=5432",
                "TIDE_ADMIN_DB_USER=postgres",
                "TIDE_ADMIN_DB_NAME=tit_growth_test_v2",
                "TIDE_ADMIN_DB_PASSWORD=secret",
                "TIDE_APP_DB_USER=tit_teacher_crud",
                "TIDE_APP_DB_PASSWORD=app-secret",
                "TIDE_ADMIN_DB_SSLMODE=disable",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_PSQL_STATE": str(state),
        "FAKE_PSQL_LOG": str(query_log),
        "FAKE_PNPM_MARKER": str(pnpm_marker),
        "FAKE_OLD_IDS": str(old_ids),
        "FAKE_CANONICAL_IDS": str(canonical_ids_file),
        "FAKE_SCENARIO": scenario,
    }
    result = subprocess.run(
        [str(TEACHER_COMPANY_TEST_MIGRATOR), str(env_file)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    return (
        result,
        query_log.read_text(encoding="utf-8") if query_log.exists() else "",
        pnpm_marker.exists(),
        int(state.read_text(encoding="utf-8")) if state.exists() else 0,
    )


def test_company_test_initializer_rejects_an_unapproved_target_before_connecting(
    tmp_path: Path,
) -> None:
    result, queries, pnpm_called, psql_calls = _run_rejected_company_initializer(
        tmp_path,
        scenario="unapproved-target",
    )

    assert result.returncode != 0
    assert "只允许连接已批准的 tit_growth_test_v2" in result.stderr
    assert psql_calls == 0
    assert not queries
    assert not pnpm_called


def test_company_test_initializer_rejects_a_missing_ledger_before_writes(
    tmp_path: Path,
) -> None:
    result, queries, pnpm_called, psql_calls = _run_rejected_company_initializer(
        tmp_path,
        scenario="missing",
    )

    assert result.returncode != 0
    assert "缺少 canonical Tide 迁移账本" in result.stderr
    assert psql_calls == 7
    assert not pnpm_called
    assert not any(
        statement in queries.upper()
        for statement in ("ALTER ", "INSERT ", "UPDATE ", "DELETE ", "GRANT ")
    )


def test_company_test_initializer_rejects_the_precanonical_ledger_before_writes(
    tmp_path: Path,
) -> None:
    result, queries, pnpm_called, psql_calls = _run_rejected_company_initializer(
        tmp_path,
        scenario="old-ledger",
    )

    assert result.returncode != 0
    assert "不是精确 canonical 0043" in result.stderr
    assert psql_calls == 10
    assert not pnpm_called
    assert not any(
        statement in queries.upper()
        for statement in ("ALTER ", "INSERT ", "UPDATE ", "DELETE ", "GRANT ")
    )


def test_company_test_initializer_rejects_checksum_drift_before_writes(
    tmp_path: Path,
) -> None:
    result, queries, pnpm_called, psql_calls = _run_rejected_company_initializer(
        tmp_path,
        scenario="checksum",
    )

    assert result.returncode != 0
    assert "顺序、文件名或 SHA-256" in result.stderr
    assert psql_calls == 10
    assert not pnpm_called
    assert not any(
        statement in queries.upper()
        for statement in ("ALTER ", "INSERT ", "UPDATE ", "DELETE ", "GRANT ")
    )


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
    assert "ARG VITE_API_BASE_URL" not in dockerfile
    assert "ENV VITE_API_BASE_URL" not in dockerfile
    assert "tide-camp-teacher.test.51talk.biz" not in dockerfile
    assert "RUN pnpm run build:nginx" in dockerfile
    assert "--from=teacher-frontend-build /build/teacher-frontend/dist" in dockerfile
    assert "/usr/share/nginx/teacher" in dockerfile

    teacher_nginx = TEACHER_CONF.read_text(encoding="utf-8")
    assert "location /api/" in teacher_nginx
    assert "proxy_pass http://127.0.0.1:3000" in teacher_nginx
    assert (
        "sub_filter '__SITE_ORIGIN__' '$tide_forwarded_proto://$host';"
        in teacher_nginx
    )

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
    assert "teacher/backend/content ./teacher/content" in dockerfile
    assert "new TaskDocumentContentService().getContent" in dockerfile
    assert "2026-07-24-overseas-nt-policies-v1" in dockerfile
    assert "SCROLL_TO_END" in dockerfile
    assert "content.title !== 'Overseas NT Policies'" in dockerfile
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
    assert "S6_CMD_RECEIVE_SIGNALS=" not in dockerfile
    assert "SIGTERM must reach pid 1" in dockerfile
    assert (
        'ENTRYPOINT ["/opt/venv/bin/python", "/app/bin/runtime-env.py", '
        '"start", "--fingerprint-file", "/run/tit-runtime-env/fingerprint", '
        '"--", "/init"]'
    ) in dockerfile
    assert 'CMD ["sleep", "infinity"]' in dockerfile
    assert "USER gaea" not in dockerfile
    assert "nginx -t" in dockerfile

    for service in (
        "operations",
        "teacher-api",
        "score-settlement",
        "source-wide",
    ):
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

    rendered = subprocess.run(
        [str(RENDER_NGINX), str(NGINX_CONF), "/dev/stdout"],
        check=True,
        capture_output=True,
        env=os.environ | {"MEMORY_SIZE": "--"},
        text=True,
    )
    assert "worker_processes 2;" in rendered.stdout
    assert "invalid number" not in rendered.stderr


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
    assert teacher_conf.count("add_header Referrer-Policy") == 5
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
    assert (
        'CMD ["/opt/venv/bin/python", "/app/bin/runtime-env.py", '
        '"healthcheck", "--fingerprint-file", '
        '"/run/tit-runtime-env/fingerprint", "--", '
        '"/app/bin/healthcheck.sh"]'
    ) in dockerfile
    assert "http://127.0.0.1:8010/api/health" in healthcheck
    assert "http://127.0.0.1:8080/healthz" in healthcheck
    assert "http://127.0.0.1:8080/health/ready" in healthcheck
    assert "http://127.0.0.1:3000/health/ready" not in healthcheck
    assert "TIDE_TEACHER_HOST must be a hostname" in healthcheck
    # Three fixed probes plus one shell-loop invocation covering all three
    # DTS v2 runtime components.
    assert healthcheck.count("--healthcheck") == 4
    assert "--max-heartbeat-age-seconds 90" in healthcheck
    assert "--max-readiness-age-seconds 90" in healthcheck

    expected_services = {
        "operations": "scripts/run_api.py",
        "teacher-api": "dist/src/main.js",
        "teacher-web": "nginx",
        "score-settlement": "settle_shared_task_scores.py",
        "source-wide": "run_source_wide_worker.py",
        "dts-ingest": "run_dts_ingest.py",
        "dts-v2-domain": "run_dts_v2_runtime.py",
        "dts-v2-outbox": "run_dts_v2_runtime.py",
        "dts-v2-favorite": "run_dts_v2_runtime.py",
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
    source_worker_run = (S6_DIR / "source-wide" / "run").read_text(
        encoding="utf-8"
    )
    assert (S6_DIR / "source-wide" / "timeout-kill").read_text(
        encoding="utf-8"
    ).strip() == "25000"
    assert "DATABASE_URL is required" in source_worker_run
    assert "TIT_SOURCE_WORKER_DATABASE_URL" not in source_worker_run
    assert "/app/bin/source-wide-enabled.sh" in source_worker_run
    assert "TIT_SOURCE_WIDE_ENABLED=false" in source_worker_run
    assert source_worker_run.count("exec sleep infinity") == 2
    assert source_worker_run.index("TIT_PROCESS_PROFILE") < source_worker_run.index(
        "/app/bin/source-wide-enabled.sh"
    )
    assert source_worker_run.index(
        "/app/bin/source-wide-enabled.sh"
    ) < source_worker_run.index("DATABASE_URL is required")
    assert "--watch --max-events 25 --interval-seconds 3" in source_worker_run
    assert (
        "--heartbeat-path /tmp/tit-source-worker-heartbeat"
        in source_worker_run
    )
    assert (
        "--readiness-path /tmp/tit-source-worker-readiness"
        in source_worker_run
    )
    assert "--heartbeat-path /tmp/tit-source-worker-heartbeat" in healthcheck
    assert "--readiness-path /tmp/tit-source-worker-readiness" in healthcheck
    assert "/app/bin/source-wide-enabled.sh" in healthcheck
    assert "SourceWide healthcheck intentionally skipped" in healthcheck
    assert healthcheck.index("TIT_PROCESS_PROFILE") < healthcheck.index(
        "/app/bin/source-wide-enabled.sh"
    )
    assert "TIT_SCORE_WORKER_HEARTBEAT" in dockerfile
    assert "TIT_SOURCE_WIDE_ENABLED=false" in dockerfile
    assert "TIT_V2_RUNTIME_ENABLED=true" in dockerfile
    assert "TIT_SOURCE_WORKER_HEARTBEAT" in dockerfile
    assert "TIT_SOURCE_WORKER_READINESS" in dockerfile
    assert "TIT_DTS_INGEST_HEARTBEAT" in dockerfile
    assert "TIT_DTS_INGEST_READINESS" in dockerfile
    for component in ("DOMAIN", "OUTBOX", "FAVORITE"):
        assert f"TIT_V2_{component}_HEARTBEAT" in dockerfile
        assert f"TIT_V2_{component}_READINESS" in dockerfile
    assert "/app/bin/dts-v2-runtime-enabled.sh" in healthcheck
    assert "for component in domain outbox favorite" in healthcheck
    for component in ("domain", "outbox", "favorite"):
        v2_run = (S6_DIR / f"dts-v2-{component}" / "run").read_text(
            encoding="utf-8"
        )
        assert "/app/bin/dts-v2-runtime-enabled.sh" in v2_run
        assert f"--component {component} --watch" in v2_run
        assert f"/tmp/tit-v2-{component}-heartbeat" in v2_run
        assert f"/tmp/tit-v2-{component}-readiness" in v2_run
        assert (S6_DIR / f"dts-v2-{component}" / "timeout-kill").read_text(
            encoding="utf-8"
        ).strip() == "25000"
    dts_run = (S6_DIR / "dts-ingest" / "run").read_text(encoding="utf-8")
    assert "TIT_PROCESS_PROFILE" in dts_run
    assert "TIT_DTS_PASSWORD is required" in dts_run
    assert "TIT_DTS_INGEST_DB_PASSWORD is required" in dts_run
    assert "TIT_DTS_INGEST_DB_SSLMODE is required" not in dts_run
    assert "--watch --max-messages 2000" in dts_run
    assert "--max-projection-keys" not in dts_run
    assert "--projection-time-budget-seconds" not in dts_run
    assert (S6_DIR / "dts-ingest" / "timeout-kill").read_text(
        encoding="utf-8"
    ).strip() == "25000"
    for service in (
        "operations",
        "teacher-api",
        "teacher-web",
        "score-settlement",
        "source-wide",
    ):
        assert "TIT_PROCESS_PROFILE" in (S6_DIR / service / "run").read_text(
            encoding="utf-8"
        )
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert "S6_CMD_RECEIVE_SIGNALS=" not in dockerfile
    assert "SIGTERM must reach pid 1" in dockerfile


def _runtime_env_base() -> str:
    return "\n".join(
        (
            "APP_ENV=production",
            "TIT_MIGRATION_MODE=false",
            "TASK_CATALOG_PUBLIC_WRITE=false",
            "TIT_SOURCE_WIDE_ENABLED=false",
            "TIT_V2_RUNTIME_ENABLED=false",
            "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED=false",
            "LOG_LEVEL=debug",
            "",
        )
    )


def _run_runtime_env(
    tmp_path: Path,
    *,
    content: bytes | None,
    mode: str = "start",
    extra_env: dict[str, str] | None = None,
    command: list[str] | None = None,
    config_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    path = config_path or (tmp_path / "application.env")
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith(
            ("TII_", "TIT_DTS_", "TIDE_ADMIN_DB_", "TIDE_APP_DB_")
        ):
            environment.pop(key)
    environment.pop("COMPANY_TEST_DATABASE_ENABLED", None)
    environment.pop("TIT_SOURCE_WORKER_DATABASE_URL", None)
    if content is None:
        environment.pop("TIT_RUNTIME_ENV_FILE", None)
    else:
        path.write_bytes(content)
        for raw_line in content.decode("utf-8", errors="ignore").splitlines():
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                environment.pop(stripped.split("=", 1)[0].strip(), None)
        environment["TIT_RUNTIME_ENV_FILE"] = str(path)
        environment["TIT_PROCESS_PROFILE"] = "application"
    if extra_env:
        environment.update(extra_env)
    child = command or [sys.executable, "-c", "raise SystemExit(0)"]
    return subprocess.run(
        [
            sys.executable,
            str(RUNTIME_ENV_LOADER),
            mode,
            "--fingerprint-file",
            str(tmp_path / "fingerprints" / "runtime.sha256"),
            "--",
            *child,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_gaea_runtime_env_loads_literals_with_platform_precedence(
    tmp_path: Path,
) -> None:
    untouched = tmp_path / "must-not-exist"
    content = (
        _runtime_env_base()
        + "CRM_ENTRY_URL=\"https://crm.example.test/entry#fragment\"\n"
        + f"ALLOWED_EMAIL_DOMAINS=$(touch {untouched})\n"
    ).encode()
    child = [
        sys.executable,
        "-c",
        (
            "import json, os; "
            "print(json.dumps({key: os.environ.get(key) for key in "
            "('LOG_LEVEL','CRM_ENTRY_URL','ALLOWED_EMAIL_DOMAINS')}))"
        ),
    ]
    result = _run_runtime_env(
        tmp_path,
        content=content,
        extra_env={
            "LOG_LEVEL": "warn",
            "DATABASE_URL": "postgresql://runtime-secret@database.test/app",
            "TIT_DTS_INGEST_HEARTBEAT": "/tmp/tit-dts-ingest-heartbeat",
            "TIT_DTS_INGEST_READINESS": "/tmp/tit-dts-ingest-readiness",
        },
        command=child,
    )

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded == {
        "LOG_LEVEL": "warn",
        "CRM_ENTRY_URL": "https://crm.example.test/entry#fragment",
        "ALLOWED_EMAIL_DOMAINS": f"$(touch {untouched})",
    }
    assert not untouched.exists()
    assert "file_keys=9" in result.stderr
    assert "platform_overrides=1" in result.stderr
    assert "runtime-secret" not in result.stderr


def test_gaea_runtime_env_accepts_projected_symlink_and_freezes_health_version(
    tmp_path: Path,
) -> None:
    first_version = tmp_path / "..2026_08_17_01"
    first_version.mkdir()
    first_target = first_version / "application.env"
    first_target.write_text(_runtime_env_base(), encoding="utf-8")
    data_link = tmp_path / "..data"
    data_link.symlink_to(first_version.name, target_is_directory=True)
    mounted = tmp_path / "application.env"
    mounted.symlink_to(Path("..data") / "application.env")
    environment = {"TIT_RUNTIME_ENV_FILE": str(mounted)}

    started = _run_runtime_env(
        tmp_path,
        content=first_target.read_bytes(),
        extra_env=environment,
        config_path=first_target,
    )
    assert started.returncode == 0, started.stderr
    fingerprint_dir = tmp_path / "fingerprints"
    fingerprint = fingerprint_dir / "runtime.sha256"
    assert stat.S_IMODE(fingerprint_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(fingerprint.stat().st_mode) == 0o600

    healthy = _run_runtime_env(
        tmp_path,
        content=first_target.read_bytes(),
        mode="healthcheck",
        extra_env=environment,
        config_path=first_target,
    )
    assert healthy.returncode == 0, healthy.stderr

    second_version = tmp_path / "..2026_08_17_02"
    second_version.mkdir()
    second_target = second_version / "application.env"
    second_target.write_text(
        _runtime_env_base().replace("LOG_LEVEL=debug", "LOG_LEVEL=info"),
        encoding="utf-8",
    )
    replacement_link = tmp_path / "..data-replacement"
    replacement_link.symlink_to(second_version.name, target_is_directory=True)
    os.replace(replacement_link, data_link)
    changed = _run_runtime_env(
        tmp_path,
        content=second_target.read_bytes(),
        mode="healthcheck",
        extra_env=environment,
        config_path=second_target,
    )
    assert changed.returncode == 78
    assert "RollingUpdate is required" in changed.stderr


def test_gaea_runtime_env_fails_closed_for_invalid_or_missing_fingerprint(
    tmp_path: Path,
) -> None:
    missing = _run_runtime_env(
        tmp_path,
        content=_runtime_env_base().encode(),
        mode="healthcheck",
    )
    assert missing.returncode == 78
    assert "fingerprint is missing or unreadable" in missing.stderr

    broad = tmp_path / "broad"
    broad.mkdir()
    fingerprint_dir = broad / "fingerprints"
    fingerprint_dir.mkdir(mode=0o755)
    os.chmod(fingerprint_dir, 0o755)
    invalid_permissions = _run_runtime_env(
        broad,
        content=_runtime_env_base().encode(),
    )
    assert invalid_permissions.returncode == 78
    assert "permissions are too broad" in invalid_permissions.stderr
    assert "Traceback" not in invalid_permissions.stderr


def test_gaea_runtime_env_rejects_unsafe_or_malformed_files_without_values(
    tmp_path: Path,
) -> None:
    sentinel = "secret-value-must-not-appear"
    invalid_cases = (
        _runtime_env_base() + "LOG_LEVEL=info\n",
        _runtime_env_base() + "TII_API_WORKERS=2\n",
        _runtime_env_base() + f"DATABASE_URL={sentinel}\n",
        _runtime_env_base() + "TIT_DTS_TOPIC=forbidden\n",
        _runtime_env_base() + "NOT_A_SETTING\n",
        _runtime_env_base() + "UNKNOWN_RUNTIME_KEY=value\n",
        _runtime_env_base() + "PUBLIC_APP_URL=http://[\n",
    )
    for index, content in enumerate(invalid_cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        result = _run_runtime_env(case_dir, content=content.encode())
        assert result.returncode == 78
        assert sentinel not in result.stderr

    invalid_utf8 = tmp_path / "invalid-utf8"
    invalid_utf8.mkdir()
    result = _run_runtime_env(invalid_utf8, content=b"APP_ENV=\xff\n")
    assert result.returncode == 78
    assert "valid UTF-8" in result.stderr

    nul = tmp_path / "nul"
    nul.mkdir()
    result = _run_runtime_env(nul, content=b"APP_ENV=production\x00\n")
    assert result.returncode == 78
    assert "NUL" in result.stderr

    oversized = tmp_path / "oversized"
    oversized.mkdir()
    result = _run_runtime_env(
        oversized,
        content=b"#" * (128 * 1024 + 1),
    )
    assert result.returncode == 78
    assert "128 KiB" in result.stderr

    blank_path = tmp_path / "blank-path"
    blank_path.mkdir()
    result = _run_runtime_env(
        blank_path,
        content=None,
        extra_env={"TIT_RUNTIME_ENV_FILE": "   "},
    )
    assert result.returncode == 78
    assert "must not be empty" in result.stderr

    missing_profile = tmp_path / "missing-profile"
    missing_profile.mkdir()
    result = _run_runtime_env(
        missing_profile,
        content=_runtime_env_base().encode(),
        extra_env={"TIT_PROCESS_PROFILE": ""},
    )
    assert result.returncode == 78
    assert "TIT_PROCESS_PROFILE=application" in result.stderr


def test_gaea_runtime_env_rejects_legacy_company_test_database_variables(
    tmp_path: Path,
) -> None:
    for index, key in enumerate(
        ("COMPANY_TEST_DATABASE_ENABLED", "TIDE_ADMIN_DB_HOST", "TIDE_APP_DB_USER")
    ):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        result = _run_runtime_env(
            case_dir,
            content=_runtime_env_base().encode(),
            extra_env={key: "legacy-value"},
        )
        assert result.returncode == 78
        assert "legacy company-test database variables" in result.stderr
        assert "legacy-value" not in result.stderr


def test_gaea_runtime_env_keeps_platform_only_mode_backward_compatible(
    tmp_path: Path,
) -> None:
    started = _run_runtime_env(tmp_path, content=None)
    assert started.returncode == 0, started.stderr
    assert "mode=platform" in started.stderr

    healthy = _run_runtime_env(tmp_path, content=None, mode="healthcheck")
    assert healthy.returncode == 0, healthy.stderr


def test_gaea_application_runtime_env_template_is_non_secret_and_current(
    tmp_path: Path,
) -> None:
    content = APPLICATION_RUNTIME_ENV.read_text(encoding="utf-8")
    assignments: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "=" in stripped
        key, value = stripped.split("=", 1)
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
        assert key not in assignments
        assignments[key] = value

    forbidden = {
        "ARK_API_KEY",
        "AUTH_JWT_SECRET",
        "CDN_ACCESS_KEY_ID",
        "CDN_ACCESS_KEY_SECRET",
        "CRM_SSO_JWT_SECRET_CURRENT",
        "CRM_SSO_JWT_SECRET_PREVIOUS",
        "DATABASE_URL",
        "DATA_HASH_SECRET",
        "KUOZHI_APP_KEY",
        "KUOZHI_SECRET_KEY",
        "MAIL_API_ACCESS_KEY",
        "OPENAI_API_KEY",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
        "SHIWEN_READ_DATABASE_URL",
        "TIDE_DATABASE_URL",
    }
    assert forbidden.isdisjoint(assignments)
    assert not any(key.startswith(("TII_", "TIT_DTS_")) for key in assignments)
    assert assignments["APP_ENV"] == "production"
    assert assignments["TIT_MIGRATION_MODE"] == "false"
    assert assignments["TIT_SOURCE_WIDE_ENABLED"] == "false"
    assert assignments["TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED"] == "false"
    assert assignments["TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS"] == "900"
    assert assignments["TIT_V2_RUNTIME_ENABLED"] == "true"
    assert assignments["TIT_V2_EXPECTED_DATABASE"] == "tide_system_test"
    assert assignments["TIT_V2_RUNTIME_BATCH_SIZE"] == "25"
    assert assignments["TIT_V2_DOMAIN_LEASE_SECONDS"] == "120"
    assert assignments["TIT_V2_TIME_RECHECK_LEASE_SECONDS"] == "120"
    assert "TIT_V2_DOMAIN_DATABASE_URL" not in assignments
    assert "TIT_V2_OUTBOX_DATABASE_URL" not in assignments
    assert "TIT_V2_FAVORITE_DATABASE_URL" not in assignments
    assert assignments["TASK_CATALOG_PUBLIC_WRITE"] == "false"
    assert assignments["TIT_ALLOWED_HOSTS"] == "tide-camp-ops.test.51talk.biz"
    assert assignments["TIT_HEALTHCHECK_HOST"] == "tide-camp-ops.test.51talk.biz"
    assert "TIT_SOURCE_WORKER_DATABASE_URL" not in assignments
    assert "COMPANY_TEST_DATABASE_ENABLED" not in assignments
    assert "CDN_API_ENDPOINT" not in assignments
    assert "VIDEO_PREFETCH_STATE_DIR" not in assignments
    assert not any(key.startswith("PUBLIC_ASSET_") for key in assignments)
    assert "TIT_RUNTIME_ENV_FILE=/deployments/config/application.env" in content

    runnable = content.replace(
        "REPLACE_WITH_INGRESS_IP_OR_CIDR",
        "10.0.0.8/32",
    )
    result = _run_runtime_env(tmp_path, content=runnable.encode())
    assert result.returncode == 0, result.stderr

    operations_contract = _run_runtime_env(
        tmp_path,
        content=runnable.encode(),
        extra_env={
            "DATABASE_URL": (
                "postgresql+psycopg://tit_growth_app:dummy@db.example.test/"
                "tide_system_test?sslmode=verify-full"
            ),
            "PYTHONPATH": str(ROOT / "backend"),
        },
        command=[
            str(ROOT / "backend" / ".venv" / "bin" / "python"),
            "-c",
            (
                "from app.runtime_settings import validate_production_runtime; "
                "validate_production_runtime()"
            ),
        ],
    )
    assert operations_contract.returncode == 0, operations_contract.stderr


def test_source_wide_enable_gate_defaults_true_and_rejects_invalid_values() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert SOURCE_WIDE_ENABLED.is_file()
    assert os.access(SOURCE_WIDE_ENABLED, os.X_OK)
    assert "COPY --chmod=755 gaea/bin /app/bin" in dockerfile

    base_env = os.environ.copy()
    base_env.pop("TIT_SOURCE_WIDE_ENABLED", None)
    valid_values = ((None, "true"), ("true", "true"), ("false", "false"))
    for value, expected in valid_values:
        env = base_env.copy()
        if value is not None:
            env["TIT_SOURCE_WIDE_ENABLED"] = value
        result = subprocess.run(
            [str(SOURCE_WIDE_ENABLED)],
            check=False,
            capture_output=True,
            env=env,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == expected
        assert result.stderr == ""

    for value in ("", "TRUE", "1", "yes"):
        result = subprocess.run(
            [str(SOURCE_WIDE_ENABLED)],
            check=False,
            capture_output=True,
            env=base_env | {"TIT_SOURCE_WIDE_ENABLED": value},
            text=True,
        )
        assert result.returncode == 64
        assert result.stdout == ""
        assert "must be exactly true or false" in result.stderr


def test_gaea_readme_preserves_release_and_multi_replica_boundaries() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "`tida-camp`" in readme
    assert "`pre-tida-camp`" in readme
    assert "tide-camp-api" not in readme
    assert "两种构建形态、三个运行项目" in readme
    assert "`dts-ingest`" in readme
    assert "`dts-diagnose`" in readme
    assert "轻量 DTS 镜像" in readme
    assert "分别构建、" in readme
    assert "推送和发布" in readme
    assert "模块选择不会自动创建 Gaea 项目" in readme
    assert "八个业务进程入口" in readme
    assert "application.runtime.env.example" in readme
    assert "TIT_RUNTIME_ENV_FILE=/deployments/config/application.env" in readme
    assert "TIT_PROCESS_PROFILE=application" in readme
    assert "平台环境变量优先于配置文件" in readme
    assert "TII_*" in readme
    assert "挂载文件不会\n热加载" in readme
    assert "RollingUpdate" in readme
    assert "非 root" in readme
    assert "multi_module" in readme
    assert "8010" in readme and "8080" in readme and "3000" in readme
    assert "设置为 `2` 或更高" in readme
    assert "RollingUpdate" in readme
    assert "不再要求 `Recreate`" in readme
    assert "不再为积分、SourceWide 或 DTS v2 Worker 新建" in readme
    assert "TIT_PROCESS_PROFILE=dts-ingest" in readme
    assert "TIT_DTS_INGEST_DB_PASSWORD" in readme
    assert "TIT_DTS_INGEST_DB_SSLMODE" in readme
    assert "TIT_DTS_ALLOW_INSECURE_DB" in readme
    assert "SHOW ssl=off" in readme
    assert "dts-ingest.pre-ssl-off.env.example" in readme
    assert "TIT_DTS_COHORT_START" not in readme
    assert "2026-08-13" in readme
    assert "TIT_DTS_PROJECTION_ENABLED=false" in readme
    assert "TIT_DTS_STARTUP_RETRY_SECONDS" in readme
    assert "TIT_DTS_DIAG_INIT_CHECKPOINT=1786523400" in readme
    assert "RecordType [HEARTBEAT]" in readme
    assert "dts-new-subscribe.log" in readme
    assert "305000ms" in readme
    assert "8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0" in readme
    assert "TIT_DTS_KAFKA_STARTUP_REQUEST_TIMEOUT_MS" in readme
    assert "TIT_DTS_KAFKA_STARTUP_API_VERSION_AUTO_TIMEOUT_MS" in readme
    assert "configured_startup_probe_budget_ms" in readme
    assert "remaining_probe_budget_ms" in readme
    assert "effective_request_timeout_ms" in readme
    assert "15/30/60" in readme
    assert "重试期间 readiness 与 heartbeat 都不存在" in readme
    assert "TIT_DTS_ACTIVATION_AT" in readme
    assert "TIT_DTS_REQUIRED_OVS_TOPIC" not in readme
    assert "探针不读取消息" in readme
    assert "不提交 offset" in readme
    assert "不能用 readiness 代替接入证据" in readme
    assert "DTS_BROKER_TCP_*" in readme
    assert "DTS_BROKER_KAFKA_REQUEST_TIMEOUT" in readme
    assert "每次容器进程启动/重启" in readme
    assert "不在镜像构建或周期 healthcheck" in readme
    assert "TIT_DTS_REQUIRED_DOM_TOPIC" not in readme
    assert "海外项目必须位于新加坡" in readme
    assert "国内项目必须位于中国大陆" in readme
    assert "TIT_DTS_EXECUTION_REGION=sg" in readme
    assert "TIT_DTS_EXECUTION_REGION=cn" in readme
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" in readme
    assert "dom:v1:<HMAC-SHA256>" in readme
    assert "国内、海外 DTS 的固定 PRE 专线目标" in readme
    assert "专线限制网络路径但" in readme
    assert "不加密 PostgreSQL 流量" in readme
    assert "正式环境仍固定 `verify-full`" in readme
    assert "旧 SourceWide 必须保持关闭" in readme
    assert "DOM/OVS 两个 DTS 项目都只做 V2 ingest" in readme
    assert "pg_try_advisory_lock" not in readme
    assert "session advisory" in readme
    assert "session advisory lock" in readme
    assert "standby" in readme
    assert "未持有积分 advisory lock" in readme
    assert "ReadWriteMany (RWX)" in readme
    assert "RWO 或每 Pod" in readme
    assert "heartbeat" in readme and "禁止放入共享卷" in readme
    assert "副本数必须固定为 `1`" not in readme
    assert "先缩容到 `0`" not in readme
    assert "同一 UID" in readme
    assert "tide_sys_admin" in readme
    assert "tit_growth_migrator" not in readme
    assert "tide_migrator" not in readme
    assert "20260819_65_g09_set_course" in readme
    assert "20260811_55_source_wide_v12" in readme
    assert "0037_g04_remove_device_check" in readme
    assert "20260811_51_g01_tesol_only" in readme
    assert "0033_g01_tesol_only" in readme
    assert "20260811_56_p_fb_negative_copy" in readme
    assert "0038_personalized_environment_photo" in readme
    assert "0041_crm_sso_hybrid" in readme
    assert "0042_g09_set_kuozhi_course" in readme
    assert (
        "public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → "
        "teacher 0037 → public 55 → release public 56 → teacher 0038 → "
        "release public 57 → teacher 0040 → teacher 0041 → public 59 → "
        "public 60 → public 61 → public 62 → public 63 → public 64 → "
        "public 65 → teacher 0042"
    ) in readme
    assert "TEACHING_ENVIRONMENT_V1" in readme
    assert "G01 TESOL-only" in readme
    assert "release 内容链到 public 57 / teacher 0041" in readme
    assert "settle_shared_task_scores.py --watch" in readme
    assert "TIT_SCORE_WORKER_HEARTBEAT" in readme
    assert "TIT_SOURCE_WIDE_ENABLED" in readme
    assert "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED" in readme
    assert "当前门槛" in readme
    assert "首次 DTS 投影排空" in readme
    assert "恢复为 `true`" in readme
    assert "TIT_BOOTSTRAP_USERNAME" in readme
    assert "TIT_BOOTSTRAP_PASSWORD" in readme
    assert "`https://tide.51talk.com`" in readme
    assert "tide-camp-teacher.test.51talk.biz" not in readme
    assert "不代表" in readme


def test_application_examples_enable_only_v2_source_processing() -> None:
    for path in (APPLICATION_ENV, COMBINED_ENV):
        content = path.read_text(encoding="utf-8")
        assert content.count("TIT_SOURCE_WIDE_ENABLED=false") == 1
        assert content.count(
            "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED=false"
        ) == 1

    application = APPLICATION_ENV.read_text(encoding="utf-8")
    assert application.count("TIT_V2_RUNTIME_ENABLED=true") == 1
    assert application.count("TIT_DB_STATEMENT_TIMEOUT_MS=30000") == 1
    assert "TIT_DB_STATEMENT_TIMEOUT_MS=0" not in application


def test_dts_region_examples_are_single_pipeline_without_legacy_activation() -> None:
    overseas = DTS_OVS_ENV.read_text(encoding="utf-8")
    domestic = DTS_DOM_ENV.read_text(encoding="utf-8")
    generic = DTS_GENERIC_ENV.read_text(encoding="utf-8")
    for content in (overseas, domestic):
        assert "TIT_DTS_PROJECTION_ENABLED" not in content
        assert "TIT_DTS_START_AT=\n" in content

    for content in (generic, overseas, domestic):
        assert content.count("TIT_DTS_PIPELINE_MODE=SINGLE_PIPELINE") == 1
        assert "TIT_DTS_PROJECTION_MODE" not in content
        assert content.count(
            "TIT_DTS_SOURCE_PARTITION_EPOCH_ID="
        ) == 1
        assert "TIT_DTS_V2_CONTROL_GROUP" not in content
        assert content.count("TIT_DTS_INGEST_DB_SSLMODE=verify-full") == 1
        assert content.count("TIT_DTS_ALLOW_INSECURE_DB=false") == 1
        assert content.count(
            "TIT_DTS_KAFKA_STARTUP_REQUEST_TIMEOUT_MS=15000"
        ) == 1
        assert content.count(
            "TIT_DTS_KAFKA_STARTUP_API_VERSION_AUTO_TIMEOUT_MS=15000"
        ) == 1
        assert "TIT_DTS_INGEST_DB_SSLMODE=disable" not in content
        assert "TIT_DTS_COHORT_START" not in content
        assert "TIT_DTS_COHORT_END_EXCLUSIVE" not in content
        assert "TIT_DTS_PROJECTION_MAX_ATTEMPTS" not in content
        assert "TIT_DTS_ACTIVATION_AT" not in content
        assert "TIT_DTS_REQUIRED_OVS_TOPIC" not in content
        assert "TIT_DTS_REQUIRED_DOM_TOPIC" not in content

    assert "TIT_DTS_EXECUTION_REGION=sg" in overseas
    assert "TIT_DTS_EXECUTION_REGION=cn" in domestic
    assert overseas.count("TIT_DTS_GROUP_ID=\n") == 1
    assert domestic.count("TIT_DTS_GROUP_ID=\n") == 1
    assert "consumer-group name" in overseas
    assert "consumer-group name" in domestic
    assert "tit-ovs-group" not in overseas
    assert "tit-dom-group" not in domestic
    assert "TIT_DTS_EXECUTION_REGION=" in generic
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" not in overseas
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in overseas
    assert domestic.count("TIT_DTS_DOM_STUDENT_HMAC_PASSWORD=") == 1
    assert generic.count("TIT_DTS_DOM_STUDENT_HMAC_PASSWORD=") == 1
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in domestic
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in generic
    assert "TIT_DTS_PROJECTION_ENABLED" not in domestic
    assert "dts-ingest.pre-ssl-off.env.example" in domestic

    pre_override = DTS_PRE_SSL_OFF_ENV.read_text(encoding="utf-8")
    assert pre_override.count("TIT_DTS_INGEST_DB_SSLMODE=disable") == 1
    assert pre_override.count("TIT_DTS_ALLOW_INSECURE_DB=true") == 1
    assert "tide-system.rwlb.singapore.rds.aliyuncs.com:5432" in pre_override
    assert "overseas or domestic DTS PRE project" in pre_override
    assert "private line limits network exposure but" in pre_override
    assert "does not encrypt PostgreSQL traffic" in pre_override
    assert "Production must remain verify-full" in pre_override
    assert "TIT_DTS_PASSWORD" not in pre_override
    assert "TIT_DTS_INGEST_DB_PASSWORD" not in pre_override
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" not in pre_override
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in pre_override
    assert "dts-ingest.pre-ssl-off.env.example" not in DTS_DOCKERFILE.read_text(
        encoding="utf-8"
    )

    assert "TIT_DTS_INGEST_DB_SSLMODE" not in APPLICATION_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_INGEST_DB_SSLMODE" not in COMBINED_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_INGEST_DB_SSLMODE" not in DOCKERFILE.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_ALLOW_INSECURE_DB" not in APPLICATION_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_ALLOW_INSECURE_DB" not in COMBINED_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_ALLOW_INSECURE_DB" not in DOCKERFILE.read_text(
        encoding="utf-8"
    )


def test_gaea_dts_service_accepts_only_single_pipeline() -> None:
    runtime = (S6_DIR / "dts-ingest" / "run").read_text(encoding="utf-8")

    assert '${TIT_DTS_PIPELINE_MODE:-SINGLE_PIPELINE}' in runtime
    assert '!= "SINGLE_PIPELINE"' in runtime
    assert "TIT_DTS_SOURCE_PARTITION_EPOCH_ID is required" in runtime
    assert "TIT_DTS_V2_CONTROL_GROUP" not in runtime
    assert "V1_COMPAT_DUAL_CAPTURE" not in runtime
    assert "ROLLED_BACK" not in runtime
    assert "TIT_DTS_START_AT is required" in runtime
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" not in APPLICATION_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in APPLICATION_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" not in COMBINED_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in COMBINED_ENV.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD" not in DOCKERFILE.read_text(
        encoding="utf-8"
    )
    assert "TIT_DTS_DOM_STUDENT_HMAC_KEY" not in DOCKERFILE.read_text(
        encoding="utf-8"
    )


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
    assert "application Pod 可以水平复制" in documents[ARCHITECTURE]
    assert "2 个或更多副本及 RollingUpdate" in documents[RUNTIME_SECURITY]
    assert "国内项目必须选择中国大陆数据中心" in documents[ROOT_README]
    assert "国内项目必须位于中国大陆" in documents[README]
    assert "国内 DTS 项目放在中国大陆" in documents[ARCHITECTURE]
    assert "国内项目必须位于中国大陆" in documents[RUNTIME_SECURITY]
    for path, document in documents.items():
        assert "dom:v1:<HMAC-SHA256>" in document, path

    assert "`DB → TCP → Kafka`" in documents[ARCHITECTURE]
    assert "TCP 探针的 5 秒连接预算不覆盖前置" in documents[ARCHITECTURE]
    assert "`DB → official Java Kafka`" in documents[RUNTIME_SECURITY]
    assert "`DB → TCP → 分阶段 kafka-python`" in documents[RUNTIME_SECURITY]


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
    assert "outputs/" in dockerignore
    assert ".tmp_*/" in dockerignore
    assert "teacher" not in dockerignore.splitlines()
