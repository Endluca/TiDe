from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest
import yaml
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "combined"
LIBPQ_CONNECTION_IDENTITY_ENV = (
    "PGDATABASE",
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGUSER",
)
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
    "0026_kuozhi_course_syncs",
    "0027_remove_local_quiz_runtime",
    "0028_retire_task_business_change_view",
    "0029_remove_unused_tide_objects",
    "0030_remove_unused_columns_and_orphan_function",
    "0031_g04_independent_sections",
    "0032_first_login_onboarding",
    "0033_g01_tesol_only",
    "0037_g04_remove_device_check",
    "0038_personalized_environment_photo",
    "0039_g02_policy_document",
    "0040_g02_document_read_status",
    "0041_crm_sso_hybrid",
    "0042_g09_set_kuozhi_course",
    "0043_p_rel_execution_catalog",
)
EXPECTED_FIXED_TASKS = (
    ("G01", "Profile & Credentials Completion", 3),
    ("G02", "Platform Policies", 2),
    ("G03", "How to handle different types of students", 2),
    ("G04", "Lesson Preparation", 3),
    ("G05", "TTP Orientation", 3),
    ("G06", "ME Culture & PARSNIP", 4),
    ("G07", "Reliability Training", 3),
    ("G08", "Global Communicator Training", 5),
    ("G09", "SET Teaching Fundamentals", 5),
)


def _environment_without_libpq_identity_overrides() -> dict[str, str]:
    environment = dict(os.environ)
    for name in LIBPQ_CONNECTION_IDENTITY_ENV:
        environment.pop(name, None)
    return environment


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
        "source-wide",
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
    assert services["source-wide"]["env_file"] == services["api"]["env_file"]
    assert services["source-wide"]["env_file"] != services["migrate"]["env_file"]
    assert services["contract-probe"]["env_file"] == services["migrate"]["env_file"]
    assert services["contract-probe"]["env_file"] != services["api"]["env_file"]
    assert services["contract-probe"]["environment"][
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL"
    ] == "${TIDE_CONTRACT_PROBE_REQUIRE_SSL:-true}"
    assert (
        services["teacher-migrate"]["env_file"]
        == services["migrate"]["env_file"]
    )
    assert (
        services["teacher-migrate"]["build"]["dockerfile"]
        == "Dockerfile.migrate"
    )
    assert services["teacher-migrate"]["environment"]["TIDE_MIGRATION_TARGET"] == (
        "${TIDE_TEACHER_MIGRATION_TARGET:-0043_p_rel_execution_catalog}"
    )
    combined_environment_example = (DEPLOY / ".env.example").read_text(
        encoding="utf-8"
    )
    assert (
        "TIDE_TEACHER_MIGRATION_TARGET=0043_p_rel_execution_catalog"
        in combined_environment_example
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
    assert "VITE_API_BASE_URL" not in services["teacher-web"]["build"]["args"]
    assert (
        services["teacher-web"]["build"]["args"][
            "VITE_PUBLIC_ASSET_BASE_URL"
        ]
        == "${TIDE_TEACHER_PUBLIC_ASSET_BASE_URL:?Set an HTTPS public asset base URL}"
    )
def test_combined_deployment_runs_source_worker_with_bounded_resources() -> None:
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
    assert source_worker["read_only"] is True
    assert source_worker["pids_limit"] == 128
    assert source_worker["cpus"] == 0.75
    assert source_worker["mem_limit"] == "768m"
    assert source_worker["networks"] == ["ops-internal"]
    assert source_worker["healthcheck"]["test"] == [
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


def test_combined_preflight_and_database_probe_fail_closed() -> None:
    preflight = (DEPLOY / "preflight.sh").read_text(encoding="utf-8")
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")
    runner = (DEPLOY / "run-contract-probe.sh").read_text(encoding="utf-8")

    assert "status --porcelain" in preflight
    assert "HIDDEN_FIXED_TASK_CODES" in preflight
    assert "code: 'G10'" in preflight
    assert "0017_task_assignment_teacher_response" in preflight
    assert "0024_support_ticket_cas_and_function_owner" in preflight
    assert "0025_fixed_task_semantic_alignment" in preflight
    assert "0028_retire_task_business_change_view" in preflight
    assert "0029_remove_unused_tide_objects" in preflight
    assert "0030_remove_unused_columns_and_orphan_function" in preflight
    assert 'TIDE_MIGRATION_TARGET:-0043_p_rel_execution_catalog' in preflight
    assert "product_analytics_recorded" in preflight
    assert "0031_g04_independent_sections" in preflight
    assert "0032_first_login_onboarding" in preflight
    assert "0037_g04_remove_device_check" in preflight
    assert "0033_g01_tesol_only" in preflight
    assert "0038_personalized_environment_photo" in preflight
    assert "0039_g02_policy_document" in preflight
    assert "0040_g02_document_read_status" in preflight
    assert "0041_crm_sso_hybrid" in preflight
    assert "0042_g09_set_kuozhi_course" in preflight
    assert "0043_p_rel_execution_catalog" in preflight
    assert "2026-08-11-tesol-only-v1" in preflight
    assert "TESOL 真实状态尚未通过。" in preflight
    assert "0038_personalized_environment_photo" in preflight
    assert "0041_crm_sso_hybrid" in preflight
    assert "p-fb-negative-environment-photo" in preflight
    assert "TEACHING_ENVIRONMENT_V1" in preflight
    assert "tide.account_onboarding_states" in preflight
    assert "g02-device-2026-08-05-browser-preflight-v1" in preflight
    assert "g02-courseware-2026-08-05-guidance-v1" in preflight
    assert "2026-08-05-g04-three-part-v1" in preflight
    assert "2026-08-11-g04-two-part" in preflight
    assert "p-fb-negative-environment-photo" in preflight
    assert "TEACHING_ENVIRONMENT_V1" in preflight
    assert "g02-policy-document" in preflight
    assert "step_type = 'DOCUMENT'" in preflight
    assert "task_step_progress_g02_assignment_completion_check" in preflight
    assert "CREATE TABLE tide.crm_sso_logins" in preflight
    assert "20260811_56_p_fb_negative_copy" in preflight
    assert "20260811_57_g02_document" in preflight
    assert "20260824_101_dts_single_pipeline_reset" in preflight
    assert "20260822_98_task_v2_refresh" in preflight
    assert "2026-07-24-lesson-memo-rules-v1" in preflight
    assert "2026-08-22-reliability-course-595-v1" in preflight
    assert "p-rel-memo-document" in preflight
    assert "public 101 / teacher 0043（38 条）" in preflight
    assert "DELETE FROM tide.task_step_definitions" in preflight
    assert (
        '"requiredStepKeys":\\["g02-environment-photo",'
        '"g02-courseware-confirmation"\\]'
    ) in preflight
    assert "Lesson Preparation" in preflight
    assert "is_loopback_or_rfc1918_ipv4" in preflight
    assert "TIDE_CONTRACT_PROBE_ENV_FILE" not in preflight
    assert "TIDE_SOURCE_WORKER_ENV_FILE" not in preflight
    assert "TIT_SOURCE_WORKER_DATABASE_URL" not in preflight
    assert "tit_growth_app" in preflight
    assert "tide_sys_admin" in preflight
    assert "tit_teacher_crud" in preflight
    assert "公司网关可信源" in preflight

    assert "current_user IS DISTINCT FROM 'tide_sys_admin'" in probe
    assert "session_user IS DISTINCT FROM 'tide_sys_admin'" in probe
    assert "transaction_read_only" in probe
    assert "pg_stat_ssl" in probe
    assert "has_database_privilege" in probe
    assert "contract probe role has write-capable privileges" in probe
    assert "20260824_101_dts_single_pipeline_reset" in probe
    assert "dts_source_snapshot_desired_rows" in probe
    assert "publish_source_snapshot_candidate_v3" in probe
    assert "guard_dom_lesson_student_privacy_v1" in probe
    assert "tit.dts_source_region" in probe
    assert "tit_dts_ingest_runtime" in probe
    assert "tgenabled IN ('O', 'A')" in probe
    assert "tgtype = 23" in probe
    assert "dom_student_json_is_safe_v1(jsonb)" in probe
    ledger_start = probe.index(
        "SELECT array_agg(migration_id ORDER BY migration_order)"
    )
    ledger_end = probe.index("]::text[] THEN", ledger_start)
    ledger_entries = tuple(
        line.strip().strip(",").strip("'")
        for line in probe[ledger_start:ledger_end].splitlines()
        if line.strip().startswith("'")
    )
    assert len(ledger_entries) == 38
    assert ledger_entries == EXPECTED_TEACHER_MIGRATIONS
    assert "chain ending at 0043" in probe
    assert "P-REL-MEMO is not the exact reviewed READY document execution" in probe
    assert (
        "P-REL-ATTENDANCE is not the exact reviewed READY Kuozhi execution"
        in probe
    )
    assert "43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51" in probe
    assert "2026-08-22-reliability-course-595-v1" in probe
    assert "tide.crm_sso_logins" in probe
    assert "tide.user_accounts" in probe
    assert "tide.auth_sessions" in probe
    assert "CRM SSO account or session columns are incomplete" in probe
    for relation in (
        "teacher_source_wide",
        "lesson_source_wide",
        "dts_source_partition_epochs",
        "dts_source_row_versions",
        "dts_ingest_checkpoints",
        "dts_ingest_events",
        "dts_source_rows",
        "dts_dirty_keys",
        "dts_dirty_key_inputs",
        "dts_dirty_key_dependencies",
        "dts_dirty_key_state_audits",
    ):
        assert relation in probe
    assert "tit_dts_ingest_runtime" in probe
    assert "DTS runtime table ACL is not the exact public 99 ingest matrix" in probe
    assert (
        "DTS runtime can mutate a relation outside its public 99 ingest boundary"
        in probe
    )
    for relation in (
        "public.teachers",
        "public.complaint_category_rules",
        "public.personalized_trigger_matches",
        "public.lesson_score_results",
        "public.teacher_qualifications",
        "public.score_accounts",
        "public.score_component_accounts",
        "public.score_entries",
        "public.task_templates",
        "public.task_assignments",
        "public.notifications",
        "public.ops_cases",
        "public.ops_decisions",
        "public.outbox_events",
        "public.audit_events",
        "public.idempotency_records",
        "public.config_versions",
        "public.config_publication_audits",
        "public.operator_accounts",
        "public.operator_role_grants",
        "public.operator_sessions",
        "public.teacher_support_tickets",
    ):
        assert relation in probe
    assert "TiDe runtime source-wide access is not read-only" in probe
    assert "TiDe runtime business-table CRUD is incomplete" in probe
    for relation in (
        "public.alembic_version",
        "public.task_templates",
        "public.teachers",
        "public.teacher_scorecard_current",
        "public.teacher_lesson_score_current",
        "public.teacher_g01_status_current",
        "public.task_assignments",
        "public.notifications",
        "public.notification_events",
        "public.teacher_support_tickets",
    ):
        assert relation in probe
    assert "teacher runtime public read-only ACL is invalid" in probe
    assert "teacher runtime public business-table CRUD is incomplete" in probe
    assert "teacher runtime tide-table CRUD is incomplete" in probe
    assert "support-ticket function owner table CRUD is incomplete" in probe
    assert "guard_audit_event_history" in probe
    assert "guard_runtime_append_only_fact" in probe
    assert "Complete the required TESOL status and learning evidence." in probe
    assert "Confirm TESOL, pass all 61 questions" in probe
    assert "TESOL is complete, the 61-question check reaches 80%" in probe
    assert "position('Self-intro' IN payload->>'completion_standard') = 0" in probe
    assert "2026-08-11-tesol-only-v1" in probe
    assert "TESOL 真实状态尚未通过。" in probe
    assert "G01 external-status rule set is not exactly" in probe
    assert "rule.rule_key = 'g01-external-status'" in probe
    assert "OR rule.rule_type = 'G01_EXTERNAL_STATUS'" in probe
    assert "row_id = 'P-FB-NEGATIVE:v1'" in probe
    assert (
        "Complete the configured improvement activity for the feedback issue "
        "shown in the task reason. Depending on the assigned activity, you may "
        "need to submit a teaching-environment photo for review or complete "
        "another guided action."
        in probe
    )
    assert (
        "The teacher app marks the task as completed after every requirement "
        "for the assigned improvement activity, including any required photo "
        "review, is satisfied."
        in probe
    )
    assert "payload->>'score_type' = 'ZERO'" in probe
    assert "(payload->>'score_value')::numeric = 0" in probe
    assert "stable P-FB-NEGATIVE:v1 row is not the reviewed zero-point" in probe
    assert "actual_titles text[]" in probe
    assert (
        "ARRAY['G01','G02','G03','G04','G05','G06','G07','G08','G09']"
        in probe
    )
    assert "Lesson Preparation" in probe
    assert "WHERE row_id = 'G02:v1'" in probe
    assert "payload->>'template_id' = 'G04'" in probe
    assert "payload->>'ops_name_zh' = '首课准备'" in probe
    assert (
        "Complete the teaching-environment photo review and prepare the "
        "courseware before your first lesson."
    ) in probe
    assert (
        "Complete two sections in any order: submit one teaching-environment "
        "photo for AI review and prepare the courseware for your first lesson."
    ) in probe
    assert "Each section keeps its own progress" in probe
    assert "G04 is completed only after both sections pass" in probe
    assert "camera angle, lighting, background and dressing" in probe
    assert "Your teaching environment and courseware are ready" in probe
    assert "2026-08-11-g04-two-part" in probe
    assert "independentModules" in probe
    assert "g02-courseware-2026-08-05-guidance-v1" in probe
    assert "2026-08-11-g04-two-part-v1" in probe
    assert "lesson-preparation-camera-view-2026-08-v7-background-veto" in probe
    assert "G04 photo rule is not the reviewed four-criterion AI check" in probe
    assert "G04 teacher execution does not have exactly the reviewed two steps" in probe
    assert "definition.step_key = 'g02-device-check'" in probe
    assert "G04 device step is still active" in probe
    assert "G04 completion rule does not require the two current steps" in probe
    assert "tide.task_step_definitions" in probe
    assert "tide.task_validation_rules" in probe
    assert "ARRAY[3,2,2,3,3,4,3,5,5]" in probe
    assert "count(assignment.assignment_id) <> 9" in probe
    assert "teacher_reply_deadline_at" in probe
    assert "interval ''48 hours''" in probe
    assert "0024_support_ticket_cas_and_function_owner" in probe
    assert "0025_fixed_task_semantic_alignment" in probe
    assert "0028_retire_task_business_change_view" in probe
    assert "0029_remove_unused_tide_objects" in probe
    assert "0030_remove_unused_columns_and_orphan_function" in probe
    assert "analytics_task_business_change_v1" in probe
    assert "teacher_metric_snapshots" in probe
    assert "teacher_photo_runs" in probe
    assert "complaint_category_rules', 'learning_title" in probe
    assert "complaint_category_rules', 'learning_url" in probe
    assert "operator_sessions', 'last_seen_at" in probe
    assert "file_objects', 'visibility" in probe
    assert "tide.enforce_outbox_target()" in probe
    assert "0031_g04_independent_sections" in probe
    assert "0032_first_login_onboarding" in probe
    assert "0033_g01_tesol_only" in probe
    assert "0037_g04_remove_device_check" in probe
    assert "0038_personalized_environment_photo" in probe
    assert "0041_crm_sso_hybrid" in probe
    assert "tide.crm_sso_logins" in probe
    assert "P-FB-NEGATIVE is not the exact pending personalized photo execution" in probe
    assert "p-fb-negative-environment-photo" in probe
    assert "TEACHING_ENVIRONMENT_V1" in probe
    assert "public.teacher_g01_status_current" in probe
    assert "ARRAY['tchr_id', 'is_cpl_tesol']" in probe
    assert "guard_teacher_notification_write" in probe
    assert "guard_notification_event_history" in probe
    assert "guard_simple_support_ticket_write" in probe
    assert "guard_dts_runtime_state_write" in probe
    assert "guard_runtime_schema_migration_write" in probe
    assert "guard_crm_sso_login_write" in probe
    assert "protect_system_notification_content" in probe
    assert "guard_outbox_event_update" in probe
    assert "guard_lesson_score_result_identity" in probe
    assert "guard_operator_account_runtime_update" in probe
    assert "runtime roles still have explicit column ACL" in probe
    assert "pg_default_acl" in probe
    assert "future tide tables will not inherit teacher runtime CRUD" in probe
    assert "tide.account_onboarding_states" in probe
    assert "account_onboarding_states_request_hash_check" in probe
    assert "confdeltype = 'c'" in probe
    assert "p_message IS NULL" in probe
    assert "tide_support_ticket_owner" in probe
    assert "sslmode=verify-full" in runner
    assert "sslmode=disable" in runner
    assert "tide-system.rwlb.singapore.rds.aliyuncs.com:5432" in runner
    assert "tide_system_test" in runner
    assert "tide_sys_admin" in runner
    assert "private-line contract probe requires session TLS off" in probe
    assert "and server ssl=off" in probe
    assert "--no-password" in runner
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL: "
        "${TIDE_CONTRACT_PROBE_REQUIRE_SSL:-true}"
    ) in compose


def test_contract_probe_casts_information_schema_identifier_arrays_to_text() -> None:
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")

    identifier_aggregates = re.findall(
        r"SELECT\s+array_agg\((?P<expression>[^)]*)\)\s+"
        r"FROM\s+information_schema\.",
        probe,
        flags=re.DOTALL,
    )

    assert identifier_aggregates == [
        "column_name::text ORDER BY ordinal_position"
    ]


def test_contract_probe_separates_owner_admin_from_runtime_membership() -> None:
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")

    assert "member_role.rolname = 'tide_sys_admin'" in probe
    assert "membership.set_option" in probe
    assert "membership.admin_option" in probe
    assert "membership.inherit_option" in probe
    assert "member_role.rolname <> 'tide_sys_admin'" in probe
    assert "ARRAY['tide_sys_admin']::text[]" not in probe
    assert (
        "support-ticket owner membership grants runtime access outside "
        "tide_sys_admin"
    ) in probe


def test_contract_probe_requires_the_exact_support_function_acl_matrix() -> None:
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")

    matrix = probe[
        probe.index("VALUES\n                ('public.create_teacher_support_ticket") :
        probe.index("support-ticket function execution ACL is not exact")
    ]
    assert matrix.count("::regprocedure") == 16
    assert matrix.count("'tit_growth_app'::text, true") == 1
    assert matrix.count("'tit_teacher_crud'::text, true") == 3
    assert matrix.count("'tit_dts_ingest_runtime'::text, true") == 0
    assert "privilege.grantee = 0" in matrix
    assert "privilege.privilege_type = 'EXECUTE'" in matrix


def test_contract_probe_preserves_the_crm_sso_acl_exception() -> None:
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")

    tide_crud_error = "teacher runtime tide-table CRUD is incomplete"
    tide_crud_start = probe.rfind("IF EXISTS (", 0, probe.index(tide_crud_error))
    tide_crud_contract = probe[tide_crud_start : probe.index(tide_crud_error)]
    assert "relation.relname <> 'crm_sso_logins'" in tide_crud_contract
    assert "ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]" in (
        tide_crud_contract
    )

    crm_acl_error = "teacher runtime CRM SSO ACL is invalid"
    crm_acl_start = probe.rfind("IF EXISTS (", 0, probe.index(crm_acl_error))
    crm_acl_contract = probe[crm_acl_start : probe.index(crm_acl_error)]
    assert "ARRAY['SELECT', 'INSERT', 'UPDATE']::text[]" in crm_acl_contract
    assert "'tide.crm_sso_logins',\n        'DELETE'" in crm_acl_contract

    assert "future ordinary Tide tables" in probe
    assert (
        "ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]" in probe
    )


def test_personalized_photo_gates_reject_missing_or_extra_config_fields() -> None:
    probe = (DEPLOY / "contract-probe.sql").read_text(encoding="utf-8")
    readiness = (
        ROOT
        / "teacher/backend/src/platform/database/database.service.ts"
    ).read_text(encoding="utf-8")
    verifier = (
        ROOT / "teacher/backend/database/scripts/verify.sh"
    ).read_text(encoding="utf-8")
    company_test = (
        ROOT / "teacher/backend/database/scripts/apply-company-test.sh"
    ).read_text(encoding="utf-8")
    migration_up = (
        ROOT
        / "teacher/backend/database/migrations"
        / "0038_personalized_environment_photo.up.sql"
    ).read_text(encoding="utf-8")
    migration_down = (
        ROOT
        / "teacher/backend/database/migrations"
        / "0038_personalized_environment_photo.down.sql"
    ).read_text(encoding="utf-8")

    personalized_execution_error = probe.index(
        "'P-FB-NEGATIVE is not the exact pending personalized photo execution'"
    )
    personalized_start = probe.rfind(
        "IF NOT EXISTS (",
        0,
        personalized_execution_error,
    )
    personalized_end = probe.index(
        "IF to_regrole('tit_teacher_crud')",
        personalized_start,
    )
    probe_contract = probe[personalized_start:personalized_end]

    step_match = re.search(
        r"definition\.config\s*=\s*'([^']+)'::jsonb",
        probe_contract,
    )
    rule_matches = re.findall(
        r"rule\.config\s*=\s*'([^']+)'::jsonb",
        probe_contract,
    )
    assert step_match is not None
    assert len(rule_matches) == 2

    expected_step = {
        "version": "2026-08-11-personalized-environment-photo-v1",
        "role": "ENVIRONMENT_PHOTO",
        "reviewProfile": "TEACHING_ENVIRONMENT_V1",
        "accept": ["image/jpeg"],
        "captureOnly": True,
        "maxFiles": 1,
    }
    expected_completion_rule = {
        "requiredStepKeys": ["p-fb-negative-environment-photo"],
    }
    expected_ai_rule = {
        "stepKey": "p-fb-negative-environment-photo",
        "criteriaVersion": "personalized-teaching-environment-2026-08-v1",
        "criteriaKeys": [
            "camera_angle",
            "lighting",
            "background",
            "dressing",
        ],
        "allowedMimeTypes": ["image/jpeg", "image/png", "image/webp"],
        "reviewProfile": "TEACHING_ENVIRONMENT_V1",
        "systemPrompt": (
            "You strictly review teacher-submitted evidence. Return JSON only "
            'with this exact shape: {"decision":"PASS|RETRY|ERROR",'
            '"teacherReason":"teacher-safe concise message",'
            '"confidenceSummary":{},"criteria":[{"criterionKey":"one '
            'configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":'
            '"teacher-safe message or null"}]}. Include every configured '
            "criterion exactly once. Never infer a pass from the mere "
            "presence of a person or object. Use UNKNOWN whenever the visual "
            "evidence is unclear. PASS only when every configured criterion "
            "is visibly and unambiguously PASS; any FAIL or UNKNOWN requires "
            "RETRY. Use ERROR only when the file cannot be assessed. Do not "
            "expose internal risk labels or private model reasoning."
        ),
        "userText": (
            "Review this current teaching-environment photo strictly against "
            "camera angle, lighting, background and dressing only."
        ),
    }

    actual_step = json.loads(step_match.group(1))
    actual_completion_rule = json.loads(rule_matches[0])
    actual_ai_rule = json.loads(rule_matches[1])
    assert actual_step == expected_step
    assert actual_completion_rule == expected_completion_rule
    assert actual_ai_rule == expected_ai_rule

    missing_field = dict(expected_ai_rule)
    missing_field.pop("systemPrompt")
    extra_field = expected_ai_rule | {"unexpectedPolicy": True}
    assert missing_field != actual_ai_rule
    assert extra_field != actual_ai_rule

    assert (
        "definition.title = 'Take a teaching-environment photo'"
        in probe_contract
    )
    assert "rule.rule_key = 'all-steps-complete'" in probe_contract
    assert "rule.rule_version = '2026-07-27-strict'" in probe_contract
    assert "rule.teacher_failure_copy =" in probe_contract
    assert "definition.config->" not in probe_contract
    assert "rule.config->" not in probe_contract

    for source in (readiness, verifier):
        contract_start = source.index(
            "shared_template_row_id = 'P-FB-NEGATIVE:v1'"
        )
        contract_end = source.index(
            "P-FB-NEGATIVE",
            contract_start + len("shared_template_row_id = 'P-FB-NEGATIVE:v1'"),
        )
        contract_end = source.find("AND NOT EXISTS", contract_end)
        if contract_end == -1:
            contract_end = source.find(
                'reliability_execution_ready=',
                contract_start,
            )
        if contract_end == -1:
            contract_end = source.find('faq_count=', contract_start)
        source_contract = source[contract_start:contract_end]
        assert (
            "definition.title = 'Take a teaching-environment photo'"
            in source_contract
        )
        assert "criteriaVersion" in source_contract
        assert "allowedMimeTypes" in source_contract
        assert "systemPrompt" in source_contract
        assert "userText" in source_contract
        assert "teacher_failure_copy" in source_contract
        assert "definition.config->" not in source_contract
        assert "rule.config->" not in source_contract

    exact_contract_sources = {
        "migration up": migration_up,
        "migration down": migration_down,
        "company-test gate": company_test,
    }
    for label, source in exact_contract_sources.items():
        exact_configs = [
            json.loads(value)
            for value in re.findall(
                r"(?:(?:definition|rule)\.)?config\s*=\s*'([^']+)'::jsonb",
                source,
            )
        ]
        assert expected_step in exact_configs, label
        assert expected_completion_rule in exact_configs, label
        assert expected_ai_rule in exact_configs, label
        assert missing_field not in exact_configs, label
        assert extra_field not in exact_configs, label
        assert "Take a teaching-environment photo" in source, label
        assert "请拍摄并提交一张当前授课环境照片。" in source, label
        assert "已保留你完成的内容，请根据提示更新这份材料。" in source, label

    assert "config->" not in migration_up
    assert "config->" not in migration_down
    assert company_test.count(
        "definition.title = 'Take a teaching-environment photo'"
    ) >= 2
    assert company_test.count("rule.teacher_failure_copy =") >= 4


def test_tide_0030_accepts_only_its_own_postgresql_18_not_null_dependency() -> None:
    migration = (
        ROOT
        / "teacher/backend/database/migrations"
        / "0030_remove_unused_columns_and_orphan_function.up.sql"
    ).read_text(encoding="utf-8")

    assert "attribute.attnotnull" in migration
    assert "visibility_is_not_null IS DISTINCT FROM true" in migration
    assert "file_objects_visibility_not_null" in migration
    assert "constraint_row.contype = 'n'" in migration
    assert "dependency.objid = visibility_not_null_constraint_oid" in migration
    assert "DROP COLUMN visibility" in migration
    assert "CASCADE" not in migration.upper()


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
    *,
    include_cross_chain_gate: bool = True,
) -> str:
    migration_lines = "\n".join(
        f"  {migration_id}" for migration_id in migrations
    )
    cross_chain_gate = (
        "required_public_56=20260811_56_p_fb_negative_copy\n"
        "required_public_57=20260811_57_g02_document\n"
        "required_public_65=20260819_65_g09_set_course\n"
        "reliability_public_ready=true\n"
        "product_analytics_recorded=true\n"
        if include_cross_chain_gate
        else ""
    )
    return (
        "#!/usr/bin/env bash\n"
        f"{cross_chain_gate}"
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
        "backend/database/migrations/"
        "0028_retire_task_business_change_view.up.sql": "BEGIN;\nCOMMIT;\n",
        "backend/database/migrations/"
        "0029_remove_unused_tide_objects.up.sql": "BEGIN;\nCOMMIT;\n",
        "backend/database/migrations/"
        "0030_remove_unused_columns_and_orphan_function.up.sql": (
            "BEGIN;\nCOMMIT;\n"
        ),
        "backend/database/migrations/"
        "0031_g04_independent_sections.up.sql": (
            "BEGIN;\n"
            "SELECT '2026-08-05-g04-three-part';\n"
            "SELECT 'g02-device-2026-08-05-browser-preflight-v1';\n"
            "SELECT 'g02-courseware-2026-08-05-guidance-v1';\n"
            "SELECT '2026-08-05-g04-three-part-v1';\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0032_first_login_onboarding.up.sql": (
            "BEGIN;\n"
            "CREATE TABLE tide.account_onboarding_states (id integer);\n"
            "SELECT security_event.event_type = 'LOGIN';\n"
            "SELECT security_event.outcome = 'SUCCESS';\n"
            "SELECT 'MIGRATED_EXISTING';\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0033_g01_tesol_only.up.sql": (
            "BEGIN;\n"
            "SELECT '2026-08-11-tesol-only-v1';\n"
            "SELECT \"teacher_failure_copy = 'TESOL 真实状态尚未通过。'\";\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0037_g04_remove_device_check.up.sql": (
            "BEGIN;\n"
            "SELECT '2026-08-11-g04-two-part';\n"
            "DELETE FROM tide.task_step_definitions "
            "WHERE step_key = 'g02-device-check';\n"
            "SELECT '{\"requiredStepKeys\":[\"g02-environment-photo\","
            "\"g02-courseware-confirmation\"]}';\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0038_personalized_environment_photo.up.sql": (
            "BEGIN;\n"
            "SELECT 'p-fb-negative-environment-photo';\n"
            "SELECT 'TEACHING_ENVIRONMENT_V1';\n"
            "SELECT '{\"contentStatus\":\"PENDING\"}';\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0039_g02_policy_document.up.sql": (
            "BEGIN;\n"
            "SELECT step_key = 'g02-policy-document';\n"
            "SELECT step_type = 'DOCUMENT';\n"
            "SELECT '{\"contentHash\":\"fixture\"}';\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0040_g02_document_read_status.up.sql": (
            "BEGIN;\n"
            "CREATE CONSTRAINT TRIGGER "
            "task_step_progress_g02_assignment_completion_check;\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0041_crm_sso_hybrid.up.sql": (
            "BEGIN;\n"
            "ALTER TABLE tide.user_accounts "
            "ALTER COLUMN password_hash DROP NOT NULL;\n"
            "ALTER TABLE tide.auth_sessions ADD COLUMN auth_method text;\n"
            "CREATE TABLE tide.crm_sso_logins (id integer);\n"
            "COMMIT;\n"
        ),
        "backend/database/migrations/"
        "0042_g09_set_kuozhi_course.up.sql": "BEGIN;\nCOMMIT;\n",
        "backend/database/migrations/"
        "0043_p_rel_execution_catalog.up.sql": (
            "BEGIN;\n"
            "SELECT shared_template_row_id = 'P-REL-MEMO:v1';\n"
            "SELECT shared_template_row_id = 'P-REL-ATTENDANCE:v1';\n"
            "SELECT '2026-07-24-lesson-memo-rules-v1';\n"
            "SELECT '43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51';\n"
            "SELECT 'p-rel-memo-document';\n"
            "SELECT '2026-08-22-reliability-course-595-v1';\n"
            "SELECT 'P-REL-ATTENDANCE contains unreviewed local steps or rules';\n"
            "COMMIT;\n"
        ),
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
        tmp_path / "teacher.env",
    ]
    environment_files[0].write_text(
        "DATABASE_URL="
        "postgresql+psycopg://tit_growth_app:secret@db.example/"
        "tit_growth?sslmode=verify-full\n"
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE=tit_growth\n",
        encoding="utf-8",
    )
    environment_files[1].write_text(
        "DATABASE_URL="
        "postgresql+psycopg://tide_sys_admin:secret@db.example/"
        "tit_growth?sslmode=verify-full\n",
        encoding="utf-8",
    )
    environment_files[2].write_text(
        "TIDE_DATABASE_URL="
        "postgresql://tit_teacher_crud:secret@db.example/"
        "tit_growth?sslmode=verify-full\n"
        "SHIWEN_READ_DATABASE_URL="
        "postgresql://tit_teacher_crud:secret@db.example/"
        "tit_growth?sslmode=verify-full\n",
        encoding="utf-8",
    )

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
        "TIDE_TEACHER_ENV_FILE": str(environment_files[2]),
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


def test_combined_preflight_rejects_teacher_chain_ending_before_0043(
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
    assert "教师端迁移器默认目标不是 0043" in result.stderr


def test_combined_preflight_rejects_missing_cross_chain_stage_gate(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment = _commit_teacher_fixture_change(
        environment,
        "backend/database/scripts/apply-production.sh",
        _teacher_migrator_fixture(include_cross_chain_gate=False),
    )

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "教师端迁移器未固定 release public 56 切换点" in result.stderr


def test_combined_preflight_accepts_teacher_source_inside_one_clean_repository(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(
        tmp_path,
        combined_repository=True,
    )

    result = _run_preflight(environment)

    assert result.returncode == 0, result.stderr


def test_combined_preflight_accepts_fixed_pre_private_line_database_urls(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    private_line_base = (
        "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
        "tide_system_test?sslmode=disable"
    )
    Path(environment["TIDE_OPS_ENV_FILE"]).write_text(
        "DATABASE_URL=postgresql+psycopg://tit_growth_app:secret@"
        f"{private_line_base}\n"
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE=tide_system_test\n",
        encoding="utf-8",
    )
    Path(environment["TIDE_OPS_MIGRATION_ENV_FILE"]).write_text(
        "DATABASE_URL=postgresql+psycopg://tide_sys_admin:secret@"
        f"{private_line_base}\n",
        encoding="utf-8",
    )
    Path(environment["TIDE_TEACHER_ENV_FILE"]).write_text(
        "TIDE_DATABASE_URL=postgresql://tit_teacher_crud:secret@"
        f"{private_line_base}\n"
        "SHIWEN_READ_DATABASE_URL=postgresql://tit_teacher_crud:secret@"
        f"{private_line_base}\n",
        encoding="utf-8",
    )
    environment["TIDE_DATABASE_NAME"] = "tide_system_test"

    result = _run_preflight(environment)

    assert result.returncode == 0, result.stderr


def test_combined_preflight_rejects_private_line_query_identity_override(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    Path(environment["TIDE_OPS_ENV_FILE"]).write_text(
        "DATABASE_URL=postgresql+psycopg://tit_growth_app:secret@"
        "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
        "tide_system_test?sslmode=disable&host=other.example\n"
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE=tide_system_test\n",
        encoding="utf-8",
    )
    environment["TIDE_DATABASE_NAME"] = "tide_system_test"

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "fixed tide_system_test PRE private-line endpoint" in result.stderr


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


def test_combined_preflight_rejects_reused_runtime_and_migration_credentials(
    tmp_path: Path,
) -> None:
    environment = _make_preflight_environment(tmp_path)
    environment["TIDE_OPS_MIGRATION_ENV_FILE"] = environment["TIDE_OPS_ENV_FILE"]

    result = _run_preflight(environment)

    assert result.returncode != 0
    assert "运营运行与迁移环境文件不得复用" in result.stderr


def test_contract_probe_runner_requires_strict_production_tls() -> None:
    environment = {
        **_environment_without_libpq_identity_overrides(),
        "DATABASE_URL": (
            "postgresql://tide_sys_admin:secret@db.example/tit_growth"
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


@pytest.mark.parametrize(
    ("database_url", "expected_database", "error"),
    [
        (
            "postgresql://other:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable",
            "tide_system_test",
            "must use tide_sys_admin",
        ),
        (
            "postgresql://tide_sys_admin:secret@db.example:5432/"
            "tide_system_test?sslmode=disable",
            "tide_system_test",
            "endpoint is not allowlisted",
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "other?sslmode=disable",
            "other",
            "must select tide_system_test",
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&ssl=false",
            "tide_system_test",
            "conflicting ssl",
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&host=db.example",
            "tide_system_test",
            "must not override connection identity",
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable&%68ost=db.example",
            "tide_system_test",
            "query parameter name is not canonical",
        ),
        (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=verify-full",
            "tide_system_test",
            "exactly one sslmode=disable",
        ),
    ],
)
def test_contract_probe_plaintext_is_limited_to_exact_pre_endpoint(
    database_url: str,
    expected_database: str,
    error: str,
) -> None:
    environment = {
        **_environment_without_libpq_identity_overrides(),
        "DATABASE_URL": database_url,
        "TIDE_CONTRACT_PROBE_EXPECTED_DATABASE": expected_database,
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL": "false",
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
    assert error in result.stderr


def test_contract_probe_runner_accepts_exact_pre_private_line_before_psql(
    tmp_path: Path,
) -> None:
    fake_psql = tmp_path / "psql"
    fake_psql.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_psql.chmod(0o755)
    environment = {
        **_environment_without_libpq_identity_overrides(),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DATABASE_URL": (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
        "TIDE_CONTRACT_PROBE_EXPECTED_DATABASE": "tide_system_test",
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL": "false",
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

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
def test_contract_probe_rejects_ambient_libpq_identity_override(
    tmp_path: Path,
    name: str,
) -> None:
    fake_psql = tmp_path / "psql"
    fake_psql.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_psql.chmod(0o755)
    environment = {
        **_environment_without_libpq_identity_overrides(),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DATABASE_URL": (
            "postgresql://tide_sys_admin:secret@"
            "tide-system.rwlb.singapore.rds.aliyuncs.com:5432/"
            "tide_system_test?sslmode=disable"
        ),
        "TIDE_CONTRACT_PROBE_EXPECTED_DATABASE": "tide_system_test",
        "TIDE_CONTRACT_PROBE_REQUIRE_SSL": "false",
        "TIDE_CONTRACT_PROBE_SQL_FILE": str(DEPLOY / "contract-probe.sql"),
        name: "must-not-override-url",
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
    assert "ambient libpq connection identity variables" in result.stderr


def test_edge_canonicalizes_forwarded_headers_before_one_hop_backends() -> None:
    nginx = (DEPLOY / "edge-nginx.conf.template").read_text(encoding="utf-8")

    assert "set_real_ip_from ${TIDE_COMPANY_GATEWAY_CIDR};" in nginx
    assert "real_ip_header X-Forwarded-For;" in nginx
    assert "geo $realip_remote_addr $request_from_company_gateway" in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == 3
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
