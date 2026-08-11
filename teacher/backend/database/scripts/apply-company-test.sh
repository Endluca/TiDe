#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-${DB_DIR}/.env.company-test}"
APPROVED_TEST_DB_HOST="ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
APPROVED_TEST_DB_PORT="5432"
APPROVED_TEST_DB_NAME="tit_growth_test_v2"
APPROVED_TEST_DB_OWNER="postgres"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少公司测试库配置：${ENV_FILE}" >&2
  exit 1
fi
if stat -f '%Lp' "${ENV_FILE}" >/dev/null 2>&1; then
  env_file_mode="$(stat -f '%Lp' "${ENV_FILE}")"
else
  env_file_mode="$(stat -c '%a' "${ENV_FILE}")"
fi
if [[ "${env_file_mode}" != "600" ]]; then
  echo "公司测试库配置权限必须为 600：${ENV_FILE}" >&2
  exit 1
fi

while IFS='=' read -r key value || [[ -n "${key}" ]]; do
  value="${value%$'\r'}"
  case "${key}" in
    TIDE_ADMIN_DB_HOST|TIDE_ADMIN_DB_PORT|TIDE_ADMIN_DB_USER|TIDE_ADMIN_DB_NAME|TIDE_ADMIN_DB_PASSWORD|TIDE_APP_DB_USER|TIDE_APP_DB_PASSWORD|TIDE_ADMIN_DB_SSLMODE)
      export "${key}=${value}"
      ;;
  esac
done < "${ENV_FILE}"

required_variables=(
  TIDE_ADMIN_DB_HOST
  TIDE_ADMIN_DB_PORT
  TIDE_ADMIN_DB_USER
  TIDE_ADMIN_DB_NAME
  TIDE_ADMIN_DB_PASSWORD
  TIDE_APP_DB_USER
  TIDE_APP_DB_PASSWORD
)
for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "配置项 ${variable_name} 不能为空。" >&2
    exit 1
  fi
done

if [[ "${TIDE_APP_DB_USER}" != "tit_teacher_crud" ]]; then
  echo "共享任务写入触发器要求应用账号为 tit_teacher_crud。" >&2
  exit 1
fi
if [[ "${TIDE_ADMIN_DB_HOST}" != "${APPROVED_TEST_DB_HOST}" \
      || "${TIDE_ADMIN_DB_PORT}" != "${APPROVED_TEST_DB_PORT}" \
      || "${TIDE_ADMIN_DB_NAME}" != "${APPROVED_TEST_DB_NAME}" \
      || "${TIDE_ADMIN_DB_USER}" != "${APPROVED_TEST_DB_OWNER}" ]]; then
  echo "公司测试库初始化器只允许连接已批准的 tit_growth_test_v2 测试目标。" >&2
  exit 1
fi

export PGPASSWORD="${TIDE_ADMIN_DB_PASSWORD}"
export PGCONNECT_TIMEOUT=8
export PGSSLMODE="${TIDE_ADMIN_DB_SSLMODE:-disable}"
ADMIN_PSQL=(
  psql -X -v ON_ERROR_STOP=1
  -h "${TIDE_ADMIN_DB_HOST}"
  -p "${TIDE_ADMIN_DB_PORT}"
  -U "${TIDE_ADMIN_DB_USER}"
  -d "${TIDE_ADMIN_DB_NAME}"
)

connected_identity="$("${ADMIN_PSQL[@]}" -AtF '|' -c "
  select current_database(), current_user
")"
if [[ "${connected_identity}" != "${TIDE_ADMIN_DB_NAME}|${TIDE_ADMIN_DB_USER}" ]]; then
  echo "公司测试库实际连接身份与显式配置不一致，初始化已停止。" >&2
  exit 1
fi

server_version_num="$("${ADMIN_PSQL[@]}" -Atqc "show server_version_num")"
if (( server_version_num < 160000 )); then
  echo "PostgreSQL 版本低于 16：${server_version_num}" >&2
  exit 1
fi

shared_tables_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    to_regclass('public.task_templates') is not null
    and to_regclass('public.task_assignments') is not null
    and to_regclass('public.teachers') is not null
    and to_regclass('public.teacher_source_wide') is not null
    and exists (
      select 1 from information_schema.columns
      where table_schema = 'public'
        and table_name = 'teacher_source_wide'
        and column_name = 'is_cpl_tesol'
    )
    and exists (
      select 1 from information_schema.columns
      where table_schema = 'public'
        and table_name = 'teacher_source_wide'
        and column_name = 'is_self_introduce'
    )
    and to_regclass('public.teacher_scorecard_current') is not null
    and to_regclass('public.teacher_lesson_score_current') is not null
")"
if [[ "${shared_tables_ready}" != "t" ]]; then
  echo "世文共享表尚未准备完成。" >&2
  exit 1
fi

authoritative_fixed_catalog_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    (
      select count(*)
      from (
        values
          ('G01:v1', 'G01', 'PUBLISHED'),
          ('G02:v1', 'G04', 'PUBLISHED'),
          ('G03:v1', 'G02', 'PUBLISHED'),
          ('G04:v1', 'G03', 'PUBLISHED'),
          ('G05:v1', 'G00', 'RETIRED'),
          ('G06:v1', 'G05', 'PUBLISHED'),
          ('G07:v1', 'G06', 'PUBLISHED'),
          ('G08:v1', 'G07', 'PUBLISHED'),
          ('G09:v1', 'G08', 'PUBLISHED'),
          ('G10:v1', 'G09', 'PUBLISHED')
      ) expected(row_id, task_code, expected_status)
      join public.task_templates template
        on template.row_id = expected.row_id
       and template.template_id = expected.task_code
       and template.status = expected.expected_status
       and template.execution_owner = 'TEACHER_APP'
       and template.payload->>'category' = 'MANDATORY_GROWTH'
    ) = 10
    and (
      select array_agg(template_id::text order by template_id)
      from public.task_templates
      where status = 'PUBLISHED'
        and payload->>'category' = 'MANDATORY_GROWTH'
    ) is not distinct from
      array[
        'G01', 'G02', 'G03', 'G04', 'G05',
        'G06', 'G07', 'G08', 'G09'
      ]::text[]
")"
if [[ "${authoritative_fixed_catalog_ready}" != "t" ]]; then
  echo "运营端权威固定任务目录不是 rev38 稳定映射，初始化已停止。" >&2
  exit 1
fi

EXPECTED_PUBLIC_HEAD="20260811_56_p_fb_negative_copy"
if [[ "$("${ADMIN_PSQL[@]}" -Atqc "select to_regclass('public.alembic_version') is not null")" != "t" ]]; then
  echo "公司测试库缺少 public Alembic 账本。请先执行受控分阶段迁移；初始化未执行任何写入。" >&2
  exit 1
fi
public_head="$("${ADMIN_PSQL[@]}" -Atqc "
  select case when count(*) = 1 then min(version_num) else '' end
  from public.alembic_version
")"
if [[ "${public_head}" != "${EXPECTED_PUBLIC_HEAD}" ]]; then
  echo "公司测试库 public Schema 必须先迁移到 ${EXPECTED_PUBLIC_HEAD}，当前为 ${public_head:-未记账}。初始化未执行任何写入。" >&2
  exit 1
fi

CANONICAL_TIDE_MIGRATIONS=(
  0001_initial
  0002_shared_database_exchange
  0003_file_upload_intents
  0004_task_command_receipts
  0005_faq_message_commands
  0006_teacher_profile_g01_support
  0007_shared_task_assignment_links
  0008_remove_legacy_task_exchange
  0009_task_view_command
  0010_current_task_execution
  0011_system_notification_delivery
  0012_system_notification_publication_guards
  0013_system_notification_owner_maintenance
  0014_teacher_photo_processing
  0015_teacher_photo_filter_strength
  0016_database_quiz_banks
  0019_growth_stage_notification_state
  0020_product_analytics
  0021_teacher_support_tickets
  0022_performance_job_leases
  0023_teacher_support_operator_atomicity
  0024_support_ticket_cas_and_function_owner
  0025_fixed_task_semantic_alignment
  0026_kuozhi_course_syncs
  0027_remove_local_quiz_runtime
  0028_retire_task_business_change_view
  0029_remove_unused_tide_objects
  0030_remove_unused_columns_and_orphan_function
  0031_g04_independent_sections
  0032_first_login_onboarding
  0033_g01_tesol_only
  0037_g04_remove_device_check
  0038_personalized_environment_photo
)

if [[ "$("${ADMIN_PSQL[@]}" -Atqc "select to_regclass('tide.schema_migrations') is not null")" != "t" ]]; then
  echo "公司测试库缺少 canonical Tide 迁移账本。请先按 public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 -> public head 54 -> teacher 0037 -> public head 55 -> public head 56 -> teacher 0038 执行正式分阶段迁移；本脚本不创建或补迁移。" >&2
  exit 1
fi

expected_tide_migration_ids="$(printf '%s\n' "${CANONICAL_TIDE_MIGRATIONS[@]}")"
actual_tide_migration_ids="$("${ADMIN_PSQL[@]}" -Atqc "
  select migration_id
  from tide.schema_migrations
  order by migration_order
")"
tide_ledger_shape_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    count(*) = 33
    and min(migration_order) = 1
    and max(migration_order) = 33
    and count(distinct migration_order) = 33
    and bool_and(filename = migration_id || '.up.sql')
  from tide.schema_migrations
")"
if [[ "${actual_tide_migration_ids}" != "${expected_tide_migration_ids}" \
      || "${tide_ledger_shape_ready}" != "t" ]]; then
  current_tide_head="$("${ADMIN_PSQL[@]}" -Atqc "
    select coalesce(
      (select migration_id from tide.schema_migrations order by migration_order desc limit 1),
      '未记账'
    )
  ")"
  echo "公司测试库 Tide 账本不是精确 canonical 0038（当前 Head：${current_tide_head}）。请使用正式分阶段迁移器处理；禁止由初始化脚本重放或认领迁移。" >&2
  exit 1
fi

sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
  else
    shasum -a 256 "${file_path}" | awk '{print $1}'
  fi
}

expected_tide_ledger_manifest=""
migration_order=0
for migration_id in "${CANONICAL_TIDE_MIGRATIONS[@]}"; do
  migration_order=$((migration_order + 1))
  migration_file="${DB_DIR}/migrations/${migration_id}.up.sql"
  if [[ ! -f "${migration_file}" ]]; then
    echo "缺少 canonical Tide 迁移文件：${migration_file}" >&2
    exit 1
  fi
  migration_sha256="$(sha256_file "${migration_file}")"
  if [[ -n "${expected_tide_ledger_manifest}" ]]; then
    expected_tide_ledger_manifest+=$'\n'
  fi
  expected_tide_ledger_manifest+="${migration_order}|${migration_id}|${migration_id}.up.sql|${migration_sha256}"
done
actual_tide_ledger_manifest="$("${ADMIN_PSQL[@]}" -AtF '|' -c "
  select migration_order, migration_id, filename, sha256
  from tide.schema_migrations
  order by migration_order
")"
if [[ "${actual_tide_ledger_manifest}" != "${expected_tide_ledger_manifest}" ]]; then
  echo "公司测试库 Tide 迁移账本的顺序、文件名或 SHA-256 与当前 canonical 文件不一致。初始化未执行任何写入。" >&2
  exit 1
fi

canonical_schema_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    exists (
      select 1 from pg_roles
      where rolname = current_user and rolsuper
    )
    and exists (
      select 1 from pg_roles where rolname = 'tit_teacher_crud'
    )
    and to_regclass('tide.user_accounts') is not null
    and to_regclass('tide.task_execution_versions') is not null
    and to_regclass('tide.task_step_definitions') is not null
    and to_regclass('tide.task_validation_rules') is not null
    and to_regclass('tide.file_objects') is not null
    and to_regclass('tide.app_events') is not null
    and to_regclass('tide.system_notifications') is not null
    and to_regclass('tide.system_notification_publications') is not null
    and to_regclass('tide.growth_stage_notification_states') is not null
    and to_regclass('tide.job_leases') is not null
    and to_regclass('tide.job_leases_expiry_idx') is not null
    and to_regclass('tide.kuozhi_course_syncs') is not null
    and to_regclass('tide.account_onboarding_states') is not null
    and exists (
      select 1
      from pg_constraint
      where conrelid = 'tide.account_onboarding_states'::regclass
        and conname = 'account_onboarding_states_idempotency_key_check'
        and contype = 'c'
    )
    and to_regclass('public.teacher_support_tickets') is not null
    and to_regclass('public.config_versions') is not null
    and to_regclass('public.score_entries') is not null
    and to_regclass('public.notifications') is not null
    and to_regclass('public.notification_events') is not null
    and to_regclass('tide.analytics_task_event_semantics_v2') is not null
    and to_regclass('tide.analytics_actor_task_journey_v2') is not null
    and to_regclass('tide.analytics_task_assignment_funnel_v2') is not null
    and to_regclass('tide.analytics_task_funnel_v2') is not null
    and to_regclass('tide.analytics_task_step_funnel_v2') is not null
    and to_regclass('tide.analytics_content_quality_v2') is not null
    and to_regclass('tide.analytics_technical_quality_v1') is not null
    and to_regclass('tide.analytics_help_usage_v1') is not null
    and to_regclass('tide.teacher_tasks') is null
    and to_regclass('tide.task_quiz_banks') is null
    and to_regclass('tide.quiz_attempts') is null
    and to_regclass('tide.quiz_answers') is null
    and not exists (
      select 1 from tide.task_step_definitions where step_type = 'QUIZ'
    )
    and not exists (
      select 1
      from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'analytics_content_quality_v2'
        and column_name in (
          'quiz_submissions', 'quiz_passes', 'quiz_failures', 'quiz_pass_rate'
        )
    )
    and to_regclass('tide.analytics_task_business_change_v1') is null
    and to_regclass('tide.outcome_projections') is null
    and to_regclass('tide.camp_enrollment_projections') is null
    and to_regclass('tide.audit_events') is null
    and to_regclass('tide.task_template_files') is null
    and to_regclass('tide.file_migrations') is null
    and to_regclass('tide.teacher_photo_runs') is null
    and to_regclass('tide.analytics_actor_task_journey_v1') is null
    and to_regclass('tide.analytics_task_assignment_funnel_v1') is null
    and to_regclass('tide.analytics_task_funnel_v1') is null
    and to_regclass('tide.analytics_task_step_funnel_v1') is null
    and to_regclass('tide.analytics_content_quality_v1') is null
    and to_regclass('public.teacher_metric_snapshots') is null
    and to_regclass('public.lesson_facts') is null
    and to_regclass('public.lesson_dimension_scores') is null
    and to_regclass('public.tide_score_policy_versions_v1') is null
    and not exists (
      select 1
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'task_assignments'
        and column_name like 'teacher_response_%'
    )
    and not exists (
      select 1
      from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'file_objects'
        and column_name = 'visibility'
    )
    and to_regprocedure('tide.enforce_outbox_target()') is null
    and exists (
      select 1
      from public.task_templates template
      where template.row_id = 'G01:v1'
        and template.template_id = 'G01'
        and template.status = 'PUBLISHED'
        and template.execution_owner = 'TEACHER_APP'
        and template.payload->>'category' = 'MANDATORY_GROWTH'
        and template.payload->>'title' = 'Profile & Credentials Completion'
        and template.payload->>'why_template' =
          'Complete the required TESOL status and learning evidence.'
        and template.payload->>'how_summary' =
          'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.'
        and template.payload->>'completion_standard' =
          'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.'
        and (template.payload->>'score_value')::integer = 3
    )
    and exists (
      select 1
      from tide.task_validation_rules rule
      join tide.task_execution_versions execution
        on execution.id = rule.execution_version_id
      where execution.shared_template_row_id = 'G01:v1'
        and execution.task_code = 'G01'
        and execution.status = 'ACTIVE'
        and rule.rule_key = 'g01-external-status'
        and rule.rule_type = 'G01_EXTERNAL_STATUS'
        and rule.rule_version = '2026-08-11-tesol-only-v1'
        and rule.position = 3
        and rule.config = '{}'::jsonb
        and rule.teacher_failure_copy = 'TESOL 真实状态尚未通过。'
    )
    and exists (
      select 1
      from public.task_templates template
      where template.row_id = 'G02:v1'
        and template.template_id = 'G04'
        and template.status = 'PUBLISHED'
        and template.payload->>'template_id' = 'G04'
        and template.payload->>'ops_name_zh' = '首课准备'
        and template.payload->>'title' = 'Lesson Preparation'
        and template.payload->>'why_template' =
          'Complete the teaching-environment photo review and prepare the courseware before your first lesson.'
        and template.payload->>'how_summary' =
          'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.'
        and template.payload->>'completion_standard' =
          'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.'
        and template.payload->>'benefit' =
          'Your teaching environment and courseware are ready for your first lesson.'
        and template.payload->>'content_status' = 'READY'
        and (template.payload->>'score_value')::integer = 3
    )
    and exists (
      select 1
      from tide.task_execution_versions execution
      where execution.shared_template_row_id = 'G01:v1'
        and execution.task_code = 'G01'
        and execution.status = 'ACTIVE'
        and (
          select count(*)
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and (
              rule.rule_key = 'g01-external-status'
              or rule.rule_type = 'G01_EXTERNAL_STATUS'
            )
        ) = 1
        and (
          select count(*)
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and rule.rule_key = 'g01-external-status'
            and rule.rule_type = 'G01_EXTERNAL_STATUS'
            and rule.rule_version = '2026-08-11-tesol-only-v1'
            and rule.position = 3
            and rule.config = '{}'::jsonb
            and rule.teacher_failure_copy = 'TESOL 真实状态尚未通过。'
        ) = 1
    )
    and exists (
      select 1
      from tide.task_execution_versions execution
      where execution.shared_template_row_id = 'G02:v1'
        and execution.task_code = 'G04'
        and execution.status = 'ACTIVE'
        and execution.execution_contract_version = 'task-contract-v3'
        and execution.config =
          '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
        and (
          select count(*)
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
        ) = 2
        and exists (
          select 1
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
            and definition.step_key = 'g02-environment-photo'
            and definition.position = 1
            and definition.step_type = 'UPLOAD'
        )
        and exists (
          select 1
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
            and definition.step_key = 'g02-courseware-confirmation'
            and definition.position = 2
            and definition.step_type = 'CHECKLIST'
            and definition.config->>'version' =
              'g02-courseware-2026-08-05-guidance-v1'
        )
        and not exists (
          select 1
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
            and definition.step_key = 'g02-device-check'
        )
        and (
          select count(*)
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
        ) = 2
        and exists (
          select 1
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and rule.rule_key = 'all-steps-complete'
            and rule.rule_version = '2026-08-11-g04-two-part-v1'
            and rule.config =
              '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
        )
        and exists (
          select 1
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and rule.rule_key = 'g02-environment-ai-review'
            and rule.rule_type = 'AI_IMAGE_REVIEW'
            and rule.config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
            and rule.config->'criteriaKeys' =
              '["camera_angle","lighting","background","dressing"]'::jsonb
        )
    )
    and exists (
      select 1
      from tide.task_execution_versions execution
      join public.task_templates template
        on template.row_id = execution.shared_template_row_id
      where execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
        and execution.task_code = 'P-FB-NEGATIVE'
        and execution.status = 'ACTIVE'
        and execution.execution_contract_version = 'task-contract-v3'
        and execution.config =
          '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
        and template.template_id = 'P-FB-NEGATIVE'
        and template.status = 'PUBLISHED'
        and template.execution_owner = 'TEACHER_APP'
        and template.integration_mode = 'OUTBOUND_MANAGED'
        and template.source_mode = 'REAL'
        and template.payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
        and (template.payload->>'score_value')::integer = 0
        and template.payload->>'how_summary' =
          'Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.'
        and template.payload->>'completion_standard' =
          'The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.'
        and (
          select count(*)
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
        ) = 1
        and exists (
          select 1
          from tide.task_step_definitions definition
          where definition.execution_version_id = execution.id
            and definition.step_key = 'p-fb-negative-environment-photo'
            and definition.position = 1
            and definition.step_type = 'UPLOAD'
            and definition.title = 'Take a teaching-environment photo'
            and definition.config =
              '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
        )
        and (
          select count(*)
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
        ) = 2
        and exists (
          select 1
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and rule.rule_key = 'all-steps-complete'
            and rule.rule_type = 'ALL_STEPS_COMPLETE'
            and rule.rule_version =
              '2026-08-11-personalized-environment-photo-v1'
            and rule.position = 1
            and rule.config =
              '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
            and rule.teacher_failure_copy =
              '请拍摄并提交一张当前授课环境照片。'
        )
        and exists (
          select 1
          from tide.task_validation_rules rule
          where rule.execution_version_id = execution.id
            and rule.rule_key = 'p-fb-negative-environment-ai-review'
            and rule.rule_type = 'AI_IMAGE_REVIEW'
            and rule.rule_version = '2026-07-27-strict'
            and rule.position = 2
            and rule.config =
              '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
            and rule.teacher_failure_copy =
              '已保留你完成的内容，请根据提示更新这份材料。'
        )
    )
")"
if [[ "${canonical_schema_ready}" != "t" ]]; then
  echo "公司测试库虽已记账到 canonical 0038，但实存结构与最终契约不一致。初始化未执行任何写入。" >&2
  exit 1
fi

required_current_catalog_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    (
      select count(*)
      from (
        values
          ('G01'), ('G02'), ('G03'), ('G04'), ('G05'),
          ('G06'), ('G07'), ('G08'), ('G09'),
          ('P-REL-ATTENDANCE'), ('P-REL-MEMO'), ('P-FB-NEGATIVE'),
          ('P-FB-COMPLAINT'), ('P-FB-BLACKLIST')
      ) expected(template_id)
      join public.task_templates template
        on template.template_id = expected.template_id
       and template.status = 'PUBLISHED'
    ) = 14
    and (
      select count(*) from public.task_templates where status = 'PUBLISHED'
    ) = 14
")"
if [[ "${required_current_catalog_ready}" != "t" ]]; then
  echo "公司测试库缺少精确的 14 条当前已发布任务模板。请先执行运营端显式配置 Seed；初始化未写入 execution。" >&2
  exit 1
fi
template_snapshot_before="$("${ADMIN_PSQL[@]}" -Atqc "
  select md5(string_agg(row_to_json(template)::text, '' order by row_id))
  from public.task_templates template
")"

TIDE_DB_HOST="${TIDE_ADMIN_DB_HOST}" \
TIDE_DB_PORT="${TIDE_ADMIN_DB_PORT}" \
TIDE_DB_USER="${TIDE_ADMIN_DB_USER}" \
TIDE_DB_PASSWORD="${TIDE_ADMIN_DB_PASSWORD}" \
TIDE_DB_NAME="${TIDE_ADMIN_DB_NAME}" \
TASK_CATALOG_PUBLIC_WRITE=false \
TASK_CATALOG_TASK_CODES=G01,G02,G03,G04,G05,G06,G07,G08,G09,P-REL-ATTENDANCE,P-REL-MEMO,P-FB-NEGATIVE,P-FB-COMPLAINT,P-FB-BLACKLIST \
pnpm --dir "${DB_DIR}/.." exec ts-node scripts/sync-current-task-catalog.ts

template_snapshot_after="$("${ADMIN_PSQL[@]}" -Atqc "
  select md5(string_agg(row_to_json(template)::text, '' order by row_id))
  from public.task_templates template
")"
if [[ "${template_snapshot_before}" != "${template_snapshot_after}" ]]; then
  echo "共享任务模板在只读同步期间发生变化，初始化已停止。" >&2
  exit 1
fi

"${ADMIN_PSQL[@]}" --quiet <<'SQL'
\getenv app_password TIDE_APP_DB_PASSWORD
ALTER ROLE tit_teacher_crud
  LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 20
  PASSWORD :'app_password';
SELECT format(
  'ALTER ROLE tit_teacher_crud IN DATABASE %I SET search_path = tide, public',
  current_database()
)
\gexec
SELECT format(
  'GRANT CONNECT ON DATABASE %I TO tit_teacher_crud',
  current_database()
)
\gexec
SQL

"${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/scripts/grant-tit-teacher-crud.sql"

g01_source_acl="$("${ADMIN_PSQL[@]}" -Atqc "
  select concat_ws('|',
    has_table_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'SELECT'),
    has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'tchr_id', 'SELECT'),
    has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'is_cpl_tesol', 'SELECT'),
    has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'is_self_introduce', 'SELECT'),
    has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'real_name', 'SELECT'),
    coalesce(
      has_table_privilege(
        'tit_teacher_crud',
        to_regclass('public.teacher_metric_snapshots'),
        'SELECT'
      ),
      false
    )
  )
")"
if [[ "${g01_source_acl}" != "f|t|t|f|f|f" ]]; then
  echo "G01 教师源字段最小权限验收失败：${g01_source_acl}" >&2
  exit 1
fi

unset PGPASSWORD
export PGPASSWORD="${TIDE_APP_DB_PASSWORD}"
APP_PSQL=(
  psql -X -v ON_ERROR_STOP=1
  -h "${TIDE_ADMIN_DB_HOST}"
  -p "${TIDE_ADMIN_DB_PORT}"
  -U "${TIDE_APP_DB_USER}"
  -d "${TIDE_ADMIN_DB_NAME}"
)

verification="$("${APP_PSQL[@]}" -Atqc "
  select concat_ws('|',
    current_user,
    current_schema(),
    to_regclass('tide.user_accounts') is not null,
    to_regclass('tide.task_execution_versions') is not null,
    to_regclass('tide.system_notification_publications') is not null,
    to_regclass('tide.growth_stage_notification_states') is not null,
    to_regclass('tide.analytics_task_funnel_v2') is not null,
    to_regclass('public.teacher_support_tickets') is not null,
    (
      to_regclass('tide.outcome_projections') is null
      and to_regclass('tide.camp_enrollment_projections') is null
      and to_regclass('tide.audit_events') is null
      and to_regclass('tide.task_template_files') is null
      and to_regclass('tide.file_migrations') is null
      and to_regclass('tide.teacher_photo_runs') is null
      and to_regclass('tide.analytics_actor_task_journey_v1') is null
      and to_regclass('tide.analytics_task_assignment_funnel_v1') is null
      and to_regclass('tide.analytics_task_funnel_v1') is null
      and to_regclass('tide.analytics_task_step_funnel_v1') is null
      and to_regclass('tide.analytics_content_quality_v1') is null
      and not exists (
        select 1
        from information_schema.columns
        where table_schema = 'tide'
          and table_name = 'file_objects'
          and column_name = 'visibility'
      )
      and to_regprocedure('tide.enforce_outbox_target()') is null
    ),
    to_regclass('tide.task_quiz_banks') is null,
    to_regclass('tide.kuozhi_course_syncs') is not null,
    has_table_privilege(current_user, 'tide.kuozhi_course_syncs', 'SELECT'),
    has_table_privilege(current_user, 'tide.kuozhi_course_syncs', 'INSERT'),
    has_table_privilege(current_user, 'tide.kuozhi_course_syncs', 'UPDATE'),
    has_table_privilege(current_user, 'tide.kuozhi_course_syncs', 'DELETE'),
    (
      select count(*) = 0
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'task_assignments'
        and column_name like 'teacher_response_%'
    ),
    to_regclass('public.teacher_scorecard_current') is not null,
    has_table_privilege(current_user, 'public.teacher_scorecard_current', 'SELECT'),
    to_regclass('public.teacher_lesson_score_current') is not null,
    has_table_privilege(current_user, 'public.teacher_lesson_score_current', 'SELECT'),
    has_table_privilege(current_user, 'public.score_entries', 'SELECT'),
    coalesce(
      has_table_privilege(
        current_user,
        to_regclass('public.lesson_facts'),
        'SELECT'
      ),
      false
    ),
    coalesce(
      has_table_privilege(
        current_user,
        to_regclass('public.lesson_dimension_scores'),
        'SELECT'
      ),
      false
    ),
    has_table_privilege(current_user, 'public.config_versions', 'SELECT'),
    (select count(*) from tide.task_execution_versions where status = 'ACTIVE'),
    has_column_privilege(current_user, 'public.task_assignments', 'teacher_id', 'INSERT'),
    has_column_privilege(current_user, 'public.task_assignments', 'status', 'UPDATE'),
    has_column_privilege(current_user, 'tide.system_notifications', 'read_at', 'UPDATE'),
    has_column_privilege(current_user, 'tide.system_notifications', 'title', 'UPDATE'),
    has_table_privilege(current_user, 'tide.system_notifications', 'DELETE'),
    has_table_privilege(current_user, 'public.score_entries', 'INSERT'),
    (
      to_regclass('tide.job_leases') is not null
      and to_regclass('tide.job_leases_expiry_idx') is not null
      and has_table_privilege(
        current_user,
        to_regclass('tide.job_leases'),
        'SELECT'
      )
      and has_table_privilege(
        current_user,
        to_regclass('tide.job_leases'),
        'INSERT'
      )
      and has_table_privilege(
        current_user,
        to_regclass('tide.job_leases'),
        'UPDATE'
      )
      and has_table_privilege(
        current_user,
        to_regclass('tide.job_leases'),
        'DELETE'
      )
      and to_regclass('tide.account_onboarding_states') is not null
      and has_table_privilege(
        current_user,
        'tide.account_onboarding_states',
        'SELECT'
      )
      and has_table_privilege(
        current_user,
        'tide.account_onboarding_states',
        'INSERT'
      )
      and not has_table_privilege(
        current_user,
        'tide.account_onboarding_states',
        'UPDATE'
      )
      and not has_table_privilege(
        current_user,
        'tide.account_onboarding_states',
        'DELETE'
      )
      and has_table_privilege(
        current_user,
        'tide.schema_migrations',
        'SELECT'
      )
      and not has_table_privilege(
        current_user,
        'tide.schema_migrations',
        'INSERT'
      )
      and not has_table_privilege(
        current_user,
        'tide.schema_migrations',
        'UPDATE'
      )
      and not has_table_privilege(
        current_user,
        'tide.schema_migrations',
        'DELETE'
      )
      and has_table_privilege(current_user, 'public.teachers', 'SELECT')
      and not has_table_privilege(current_user, 'public.teachers', 'INSERT')
      and not has_table_privilege(current_user, 'public.teachers', 'UPDATE')
      and not has_table_privilege(current_user, 'public.teachers', 'DELETE')
      and (
        select count(*) = 9
        from (
          values
            ('G01:v1', 'G01', 'PUBLISHED', 'ACTIVE'),
            ('G02:v1', 'G04', 'PUBLISHED', 'ACTIVE'),
            ('G03:v1', 'G02', 'PUBLISHED', 'ACTIVE'),
            ('G04:v1', 'G03', 'PUBLISHED', 'ACTIVE'),
            ('G06:v1', 'G05', 'PUBLISHED', 'ACTIVE'),
            ('G07:v1', 'G06', 'PUBLISHED', 'ACTIVE'),
            ('G08:v1', 'G07', 'PUBLISHED', 'ACTIVE'),
            ('G09:v1', 'G08', 'PUBLISHED', 'ACTIVE'),
            ('G10:v1', 'G09', 'PUBLISHED', 'ACTIVE')
        ) expected(
          row_id,
          task_code,
          template_status,
          execution_status
        )
        join public.task_templates template
          on template.row_id = expected.row_id
         and template.template_id = expected.task_code
         and template.status = expected.template_status
        join tide.task_execution_versions execution
          on execution.shared_template_row_id = expected.row_id
         and execution.task_code = expected.task_code
         and execution.status = expected.execution_status
      )
      and not exists (
        select 1
        from tide.task_execution_versions execution
        where execution.task_code = 'G00'
          and (
            execution.shared_template_row_id <> 'G05:v1'
            or execution.status <> 'RETIRED'
          )
      )
      and not exists (
        select 1
        from tide.task_execution_versions execution
        where execution.shared_template_row_id = 'G05:v1'
          and (
            execution.task_code <> 'G00'
            or execution.status <> 'RETIRED'
          )
      )
      and exists (
        select 1
        from tide.task_execution_versions execution
        where execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          and execution.task_code = 'P-FB-NEGATIVE'
          and execution.status = 'ACTIVE'
          and execution.execution_contract_version = 'task-contract-v3'
          and execution.config =
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
          and (
            select count(*)
            from tide.task_step_definitions definition
            where definition.execution_version_id = execution.id
          ) = 1
          and exists (
            select 1
            from tide.task_step_definitions definition
            where definition.execution_version_id = execution.id
              and definition.step_key = 'p-fb-negative-environment-photo'
              and definition.step_type = 'UPLOAD'
              and definition.position = 1
              and definition.title = 'Take a teaching-environment photo'
              and definition.config =
                '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
          )
          and (
            select count(*)
            from tide.task_validation_rules rule
            where rule.execution_version_id = execution.id
          ) = 2
          and exists (
            select 1
            from tide.task_validation_rules rule
            where rule.execution_version_id = execution.id
              and rule.rule_key = 'p-fb-negative-environment-ai-review'
              and rule.rule_type = 'AI_IMAGE_REVIEW'
              and rule.rule_version = '2026-07-27-strict'
              and rule.position = 2
              and rule.config =
                '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
              and rule.teacher_failure_copy =
                '已保留你完成的内容，请根据提示更新这份材料。'
          )
          and exists (
            select 1
            from tide.task_validation_rules rule
            where rule.execution_version_id = execution.id
              and rule.rule_key = 'all-steps-complete'
              and rule.rule_type = 'ALL_STEPS_COMPLETE'
              and rule.rule_version =
                '2026-08-11-personalized-environment-photo-v1'
              and rule.position = 1
              and rule.config =
                '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
              and rule.teacher_failure_copy =
                '请拍摄并提交一张当前授课环境照片。'
          )
      )
      and to_regclass('tide.analytics_task_event_semantics_v2') is not null
      and to_regclass('tide.analytics_actor_task_journey_v2') is not null
      and to_regclass('tide.analytics_task_assignment_funnel_v2') is not null
      and to_regclass('tide.analytics_task_funnel_v2') is not null
      and to_regclass('tide.analytics_task_step_funnel_v2') is not null
      and to_regclass('tide.analytics_content_quality_v2') is not null
    )
  )
")"
if [[ "${verification}" != "tit_teacher_crud|tide|t|t|t|t|t|t|t|t|t|t|t|f|f|t|t|t|t|t|f|f|f|f|14|f|t|t|f|f|f|t" ]]; then
  echo "应用账号验收失败：${verification}" >&2
  exit 1
fi

echo "公司测试库初始化完成：public rev56 与 canonical Tide 0038 账本/checksum/实存结构只读门禁、G01 TESOL-only、G04 照片与课件两模块、源宽表 v1.2、P-FB-NEGATIVE 环境拍照配置、首次登录引导、固定任务语义、14 个当前任务 execution、教师工单共享表和 tit_teacher_crud 最小权限均已验证；未执行任何 Schema 迁移或 Mock Seed。"
