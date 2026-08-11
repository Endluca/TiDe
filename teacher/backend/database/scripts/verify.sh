#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "${DB_DIR}/.env" ]]; then
  echo "缺少 ${DB_DIR}/.env，请先从 .env.example 复制并设置本地开发密码。" >&2
  exit 1
fi

set -a
source "${DB_DIR}/.env"
set +a

export PGPASSWORD="${TIDE_DB_PASSWORD}"
PSQL=(psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "${TIDE_DB_PORT}" -U "${TIDE_DB_USER}" -d "${TIDE_DB_NAME}")

assert_equals() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  [[ "${actual}" == "${expected}" ]] || {
    echo "${message}: 期望 ${expected}，实际 ${actual}" >&2
    exit 1
  }
}

server_version="$("${PSQL[@]}" -Atqc "show server_version")"
[[ "${server_version}" == 16.* ]] || {
  echo "PostgreSQL 版本不是 16: ${server_version}" >&2
  exit 1
}

template_count="$("${PSQL[@]}" -Atqc "select count(*) from public.task_templates where status = 'PUBLISHED' and template_id ~ '^G0[1-9]$'")"
score_total="$("${PSQL[@]}" -Atqc "select coalesce(sum((payload->>'score_value')::integer), 0) from public.task_templates where status = 'PUBLISHED' and template_id ~ '^G0[1-9]$'")"
assignment_count="$("${PSQL[@]}" -Atqc "select count(*) from public.task_assignments where teacher_id = 'MOCK-TEACHER-001' and task_kind = 'FIXED_GROWTH' and task_code ~ '^G0[1-9]$'")"
personalized_count="$("${PSQL[@]}" -Atqc "select count(*) from public.task_assignments where teacher_id = 'MOCK-TEACHER-001' and task_kind = 'PERSONALIZED_IMPROVEMENT'")"
personalized_template_count="$("${PSQL[@]}" -Atqc "select count(*) from public.task_templates where status = 'PUBLISHED' and template_id !~ '^G0[1-9]$'")"
execution_count="$("${PSQL[@]}" -Atqc "select count(*) from tide.task_execution_versions where status = 'ACTIVE'")"
personalized_ready_count="$("${PSQL[@]}" -Atqc "select count(*) from tide.task_execution_versions where status = 'ACTIVE' and task_code !~ '^G0[1-9]$' and config->>'contentStatus' = 'READY'")"
personalized_pending_count="$("${PSQL[@]}" -Atqc "select count(*) from tide.task_execution_versions where status = 'ACTIVE' and task_code !~ '^G0[1-9]$' and config->>'contentStatus' = 'PENDING'")"
authoritative_fixed_catalog_ready="$("${PSQL[@]}" -Atqc "
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
fixed_execution_semantic_ready="$("${PSQL[@]}" -Atqc "
  select
    not exists (
      select 1
      from tide.task_execution_versions execution
      join public.task_templates template
        on template.row_id = execution.shared_template_row_id
      where template.row_id in (
        'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
        'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
      )
        and (
          execution.task_code is distinct from template.template_id
          or (
            execution.task_code = 'G00'
            and execution.status <> 'RETIRED'
          )
          or (
            execution.task_code ~ '^G0[1-9]$'
            and execution.status <> 'ACTIVE'
          )
        )
    )
    and not exists (
      select 1
      from tide.task_execution_versions
      where task_code like 'TMP-0025-%'
    )
")"
legacy_personalized_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from tide.task_execution_versions
  where status = 'ACTIVE'
    and task_code in (
      'NT-R01', 'NT-R03', 'NT-F02-PACING', 'NT-F02-INTERACTION',
      'NT-F02-CORRECTION', 'NT-F03-SPEAKING-PACE', 'NT-F03-SCAFFOLDING',
      'NT-F03-TEACHING-AIDS', 'NT-F03-STUDENT-RESPONSE',
      'NT-F03-PRONUNCIATION', 'NT-F04-PROFESSIONALISM',
      'NT-F05-BLACKLIST', 'NT-F02-ATTITUDE', 'NT-F03-LANGUAGE',
      'NT-F04-BOUNDARIES', 'P-ENV-01'
    )
")"
step_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from tide.task_step_definitions step
  join tide.task_execution_versions execution
    on execution.id = step.execution_version_id
  where execution.status = 'ACTIVE'
")"
fixed_step_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from tide.task_step_definitions step
  join tide.task_execution_versions execution
    on execution.id = step.execution_version_id
  where execution.status = 'ACTIVE'
    and execution.task_code ~ '^G0[1-9]$'
")"
personalized_step_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from tide.task_step_definitions step
  join tide.task_execution_versions execution
    on execution.id = step.execution_version_id
  where execution.status = 'ACTIVE'
    and execution.task_code !~ '^G0[1-9]$'
")"
step_distribution="$("${PSQL[@]}" -Atqc "
  select string_agg(task_code || ':' || step_count, ',' order by task_code)
  from (
    select execution.task_code, count(step.id)::text as step_count
    from tide.task_execution_versions execution
    left join tide.task_step_definitions step
      on step.execution_version_id = execution.id
    where execution.status = 'ACTIVE'
    group by execution.task_code
  ) current_steps
")"
local_quiz_runtime_absent="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.task_quiz_banks') is null
    and to_regclass('tide.quiz_attempts') is null
    and to_regclass('tide.quiz_answers') is null
    and not exists (
      select 1 from tide.task_step_definitions where step_type = 'QUIZ'
    )
")"
rule_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from tide.task_validation_rules rule
  join tide.task_execution_versions execution
    on execution.id = rule.execution_version_id
  where execution.status = 'ACTIVE'
")"
rule_distribution="$("${PSQL[@]}" -Atqc "
  select string_agg(task_code || ':' || rule_count, ',' order by task_code)
  from (
    select execution.task_code, count(rule.id)::text as rule_count
    from tide.task_execution_versions execution
    left join tide.task_validation_rules rule
      on rule.execution_version_id = execution.id
    where execution.status = 'ACTIVE'
    group by execution.task_code
  ) current_rules
")"
g01_tesol_rule_ready="$("${PSQL[@]}" -Atqc "
  select (
    select count(*)
    from tide.task_validation_rules rule
    join tide.task_execution_versions execution
      on execution.id = rule.execution_version_id
    where execution.shared_template_row_id = 'G01:v1'
      and execution.task_code = 'G01'
      and execution.status = 'ACTIVE'
      and (
        rule.rule_key = 'g01-external-status'
        or rule.rule_type = 'G01_EXTERNAL_STATUS'
      )
  ) = 1
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
")"
g02_policy_document_ready="$("${PSQL[@]}" -Atqc "
  select exists (
    select 1
    from tide.task_execution_versions execution
    where execution.shared_template_row_id = 'G03:v1'
      and execution.task_code = 'G02'
      and execution.status = 'ACTIVE'
      and execution.execution_contract_version = 'task-contract-v3'
      and execution.config->>'contentVersion' =
        '2026-07-24-overseas-nt-policies-v1'
      and (
        select count(*) from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
          and definition.step_key = 'g02-policy-document'
          and definition.step_type = 'DOCUMENT'
          and definition.config->>'contentHash' =
            '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c'
      ) = 1
      and (
        select count(*) from tide.task_validation_rules rule
        where rule.execution_version_id = execution.id
          and rule.rule_version = '2026-08-11-g02-policy-document-v1'
      ) = 1
  )
  and exists (
    select 1 from pg_constraint
    where conrelid = 'tide.task_step_progress'::regclass
      and conname = 'task_step_progress_g02_read_status_check'
      and convalidated
  )
  and exists (
    select 1 from pg_trigger
    where tgrelid = 'tide.task_step_progress'::regclass
      and tgname = 'task_step_progress_g02_assignment_completion_check'
      and not tgisinternal
      and tgdeferrable
      and tginitdeferred
  )
")"
personalized_environment_photo_ready="$("${PSQL[@]}" -Atqc "
  select exists (
    select 1
    from tide.task_execution_versions execution
    where execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
      and execution.task_code = 'P-FB-NEGATIVE'
      and execution.status = 'ACTIVE'
      and execution.execution_contract_version = 'task-contract-v3'
      and execution.config =
        '{\"estimatedMinutes\":8,\"allowRetry\":true,\"contentStatus\":\"PENDING\",\"contentVersion\":\"2026-08-11-personalized-environment-photo-v1\",\"pendingReason\":\"JIAHE_PERSONALIZED_CONTENT_PENDING\",\"independentModules\":{\"stepKeys\":[\"p-fb-negative-environment-photo\"],\"allowOutOfOrderProgress\":true,\"keepAssignmentInProgressUntilPassed\":true}}'::jsonb
      and (
        select count(*) from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
      ) = 1
      and exists (
        select 1 from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
          and definition.step_key = 'p-fb-negative-environment-photo'
          and definition.step_type = 'UPLOAD'
          and definition.position = 1
          and definition.title = 'Take a teaching-environment photo'
          and definition.config =
            '{\"version\":\"2026-08-11-personalized-environment-photo-v1\",\"role\":\"ENVIRONMENT_PHOTO\",\"reviewProfile\":\"TEACHING_ENVIRONMENT_V1\",\"accept\":[\"image/jpeg\"],\"captureOnly\":true,\"maxFiles\":1}'::jsonb
      )
      and (
        select count(*) from tide.task_validation_rules rule
        where rule.execution_version_id = execution.id
      ) = 2
      and exists (
        select 1 from tide.task_validation_rules rule
        where rule.execution_version_id = execution.id
          and rule.rule_key = 'all-steps-complete'
          and rule.rule_type = 'ALL_STEPS_COMPLETE'
          and rule.rule_version =
            '2026-08-11-personalized-environment-photo-v1'
          and rule.position = 1
          and rule.config =
            '{\"requiredStepKeys\":[\"p-fb-negative-environment-photo\"]}'::jsonb
          and rule.teacher_failure_copy =
            '请拍摄并提交一张当前授课环境照片。'
      )
      and exists (
        select 1 from tide.task_validation_rules rule
        where rule.execution_version_id = execution.id
          and rule.rule_key = 'p-fb-negative-environment-ai-review'
          and rule.rule_type = 'AI_IMAGE_REVIEW'
          and rule.rule_version = '2026-07-27-strict'
          and rule.position = 2
          and rule.config =
            '{\"stepKey\":\"p-fb-negative-environment-photo\",\"criteriaVersion\":\"personalized-teaching-environment-2026-08-v1\",\"criteriaKeys\":[\"camera_angle\",\"lighting\",\"background\",\"dressing\"],\"allowedMimeTypes\":[\"image/jpeg\",\"image/png\",\"image/webp\"],\"reviewProfile\":\"TEACHING_ENVIRONMENT_V1\",\"systemPrompt\":\"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\\\"decision\\\":\\\"PASS|RETRY|ERROR\\\",\\\"teacherReason\\\":\\\"teacher-safe concise message\\\",\\\"confidenceSummary\\\":{},\\\"criteria\\\":[{\\\"criterionKey\\\":\\\"one configured key\\\",\\\"result\\\":\\\"PASS|FAIL|UNKNOWN\\\",\\\"teacherMessage\\\":\\\"teacher-safe message or null\\\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.\",\"userText\":\"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only.\"}'::jsonb
          and rule.teacher_failure_copy =
            '已保留你完成的内容，请根据提示更新这份材料。'
      )
  )
")"
faq_count="$("${PSQL[@]}" -Atqc "select count(*) from tide.knowledge_chunks chunk join tide.knowledge_documents document on document.id = chunk.document_id where document.document_key = 'mock-tide-confirmed-rules' and document.status = 'ACTIVE'")"
unused_tide_objects_removed="$("${PSQL[@]}" -Atqc "
  select
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
")"
unused_file_metadata_removed="$("${PSQL[@]}" -Atqc "
  select
    not exists (
      select 1
      from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'file_objects'
        and column_name = 'visibility'
    )
    and to_regprocedure('tide.enforce_outbox_target()') is null
")"
current_analytics_ready="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.analytics_actor_task_journey_v2') is not null
    and to_regclass('tide.analytics_task_assignment_funnel_v2') is not null
    and to_regclass('tide.analytics_task_funnel_v2') is not null
    and to_regclass('tide.analytics_technical_quality_v1') is not null
    and to_regclass('tide.analytics_help_usage_v1') is not null
")"
system_notification_publication_ready="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.system_notification_publications') is not null
    and exists (
      select 1 from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'system_notifications'
        and column_name = 'issued_at'
    )
")"
growth_stage_notification_state_ready="$("${PSQL[@]}" -Atqc "
  select to_regclass('tide.growth_stage_notification_states') is not null
")"
teacher_support_tickets_ready="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('public.teacher_support_tickets') is not null
    and to_regprocedure(
      'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'
    ) is not null
    and to_regprocedure(
      'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'
    ) is not null
    and to_regprocedure(
      'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'
    ) is not null
")"
performance_job_leases_ready="$("${PSQL[@]}" -Atqc "
  select to_regclass('tide.job_leases') is not null
")"
account_onboarding_states_ready="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.account_onboarding_states') is not null
    and exists (
      select 1
      from pg_constraint
      where conrelid = 'tide.account_onboarding_states'::regclass
        and conname = 'account_onboarding_states_idempotency_key_check'
        and contype = 'c'
    )
")"
operator_reply_atomicity_ready="$("${PSQL[@]}" -Atqc "
  select position(
    'teacher_reply_deadline_at = reply_at + interval ''48 hours''' in
    pg_get_functiondef(
      'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
    )
  ) > 0
")"
support_ticket_security_hardened="$("${PSQL[@]}" -Atqc "
  select
    position(
      'row_version IS DISTINCT FROM p_expected_row_version' in
      pg_get_functiondef(
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure
      )
    ) > 0
    and position(
      'row_version IS DISTINCT FROM p_expected_row_version' in
      pg_get_functiondef(
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
      )
    ) > 0
    and not exists (
      select 1
      from unnest(
        array[
          'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure,
          'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure,
          'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure,
          'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure
        ]
      ) secured_function(function_oid)
      join pg_proc procedure on procedure.oid = secured_function.function_oid
      join pg_roles owner_role on owner_role.oid = procedure.proowner
      where owner_role.rolname <> 'tide_support_ticket_owner'
         or not procedure.prosecdef
    )
")"
notification_event_dedupe_ready="$("${PSQL[@]}" -Atqc "
  select to_regclass(
    'public.uq_notification_events_notification_request_hash'
  ) is not null
")"
system_notification_guard_ready="$("${PSQL[@]}" -Atqc "
  select position(
    'recipient_count IS DISTINCT' in
    pg_get_functiondef('tide.protect_system_notification_publication()'::regprocedure)
  ) > 0
")"
system_notification_owner_maintenance_ready="$("${PSQL[@]}" -Atqc "
  select position(
    'RETURN OLD' in
    pg_get_functiondef('tide.protect_system_notification_content()'::regprocedure)
  ) > 0
")"

legacy_object_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from (values
    ('teacher_tasks'), ('task_templates'), ('task_template_versions'),
    ('external_assignments'), ('integration_events'), ('task_status_events'),
    ('support_requests'), ('client_events'), ('message_reads'),
    ('message_projections'), ('g01_review_projections'),
    ('course_attribution_projections'), ('metric_projections'),
    ('teacher_identity_projections'), ('shiwen_fixed_task_completion_events_v1'),
    ('shiwen_personalized_status_events_v1')
  ) legacy(object_name)
  where to_regclass('tide.' || legacy.object_name) is not null
")"

assignment_link_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from information_schema.columns
  where table_schema = 'tide'
    and table_name in (
      'task_attempts', 'task_step_progress', 'video_progress',
      'task_submissions', 'task_completions', 'task_command_receipts',
      'file_upload_intents'
    )
    and column_name = 'task_assignment_id'
")"
legacy_link_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from information_schema.columns
  where table_schema = 'tide' and column_name = 'teacher_task_id'
")"
shared_trigger_count="$("${PSQL[@]}" -Atqc "
  select count(*) from pg_trigger
  where tgrelid = 'public.task_assignments'::regclass
    and tgname in ('enforce_task_assignment_write', 'audit_task_assignment_write')
    and not tgisinternal
")"
assignment_teacher_response_column_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from information_schema.columns
  where table_schema = 'public'
    and table_name = 'task_assignments'
    and column_name in (
      'teacher_response_type',
      'teacher_response_text',
      'teacher_response_submitted_at',
      'teacher_response_submitted_by'
    )
")"
assignment_teacher_response_constraint_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from pg_constraint
  where conrelid = 'public.task_assignments'::regclass
    and conname in (
      'task_assignments_teacher_response_bundle_check',
      'task_assignments_teacher_response_content_check'
    )
")"
assert_equals "${template_count}" "9" "共享 G01-G09 任务模板数异常"
assert_equals "${score_total}" "30" "G01-G09 分值合计异常"
assert_equals "${assignment_count}" "9" "Mock 当前固定任务数异常"
assert_equals "${personalized_count}" "0" "Mock 环境不应自行创建个性化任务"
assert_equals "${personalized_template_count}" "6" "已发布个性化任务码族数异常"
assert_equals "${execution_count}" "15" "本地启用执行版本数异常"
assert_equals "${personalized_ready_count}" "1" "当前可执行个性化任务数异常"
assert_equals "${personalized_pending_count}" "5" "待嘉荷配置的个性化任务码族数异常"
assert_equals "${authoritative_fixed_catalog_ready}" "t" "运营端 rev38 固定任务稳定映射异常"
assert_equals "${fixed_execution_semantic_ready}" "t" "教师端固定任务执行语义未按稳定模板行对齐"
assert_equals "${legacy_personalized_count}" "0" "旧个性化执行配置仍处于启用状态"
assert_equals "${step_count}" "7" "当前可执行任务步骤总数异常"
assert_equals "${fixed_step_count}" "5" "G01-G09 步骤总数异常"
assert_equals "${personalized_step_count}" "2" "已配置个性化任务步骤总数异常"
assert_equals "${step_distribution}" "G01:2,G02:1,G03:0,G04:2,G05:0,G06:0,G07:0,G08:0,G09:0,NT-Q03:1,P-FB-BLACKLIST:0,P-FB-COMPLAINT:0,P-FB-NEGATIVE:1,P-REL-ATTENDANCE:0,P-REL-MEMO:0" "各任务步骤数量异常"
assert_equals "${local_quiz_runtime_absent}" "t" "TIDE 本地考试表或步骤仍然存在"
assert_equals "${rule_count}" "9" "当前验证规则总数异常"
assert_equals "${rule_distribution}" "G01:3,G02:1,G03:0,G04:2,G05:0,G06:0,G07:0,G08:0,G09:0,NT-Q03:1,P-FB-BLACKLIST:0,P-FB-COMPLAINT:0,P-FB-NEGATIVE:2,P-REL-ATTENDANCE:0,P-REL-MEMO:0" "各任务验证规则数量异常"
assert_equals "${g01_tesol_rule_ready}" "t" "G01 外部状态规则未收窄为 TESOL-only"
assert_equals "${g02_policy_document_ready}" "t" "G02 原生文档及阅读完成约束未就绪"
assert_equals "${personalized_environment_photo_ready}" "t" "P-FB-NEGATIVE 授课环境拍照执行配置异常"
assert_equals "${faq_count}" "3" "FAQ Mock 知识数异常"
assert_equals "${unused_tide_objects_removed}" "t" "0029 无用 tide 表或 v1 分析视图仍然存在"
assert_equals "${unused_file_metadata_removed}" "t" "0030 无用文件可见性字段或孤儿函数仍然存在"
assert_equals "${current_analytics_ready}" "t" "当前 analytics v2 或保留的技术/帮助视图缺失"
assert_equals "${system_notification_publication_ready}" "t" "系统通知发布表或发布时间字段缺失"
assert_equals "${growth_stage_notification_state_ready}" "t" "成长阶段通知观察状态表缺失"
assert_equals "${teacher_support_tickets_ready}" "t" "教师工单共享表或原子追加方法缺失"
assert_equals "${performance_job_leases_ready}" "t" "后台任务租约结构缺失"
assert_equals "${account_onboarding_states_ready}" "t" "首次登录引导状态表或幂等键约束缺失"
assert_equals "${operator_reply_atomicity_ready}" "t" "运营回复未原子维护状态和 48 小时窗口"
assert_equals "${support_ticket_security_hardened}" "t" "工单 CAS 或 SECURITY DEFINER owner 未加固"
assert_equals "${notification_event_dedupe_ready}" "t" "外部消息事件幂等索引缺失"
assert_equals "${system_notification_guard_ready}" "t" "系统通知发布不可变保护未生效"
assert_equals "${system_notification_owner_maintenance_ready}" "t" "系统通知 Owner 维护边界未生效"
assert_equals "${legacy_object_count}" "0" "旧任务副本、投影或交换链仍然存在"
assert_equals "${assignment_link_count}" "7" "过程表的 task_assignment_id 关联不完整"
assert_equals "${legacy_link_count}" "0" "过程表仍存在 teacher_task_id"
assert_equals "${shared_trigger_count}" "2" "共享任务写入触发器数异常"
assert_equals "${assignment_teacher_response_column_count}" "0" "共享任务仍残留教师事实说明字段"
assert_equals "${assignment_teacher_response_constraint_count}" "0" "共享任务仍残留教师事实说明约束"

assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.task_assignments', 'teacher_id', 'INSERT')")" "f" "教师角色不应创建共享任务"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.task_assignments', 'status', 'UPDATE')")" "t" "教师角色缺少任务状态更新权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.score_entries', 'INSERT')")" "f" "教师角色不应写积分表"
assert_equals "$("${PSQL[@]}" -Atqc "select to_regclass('public.teacher_scorecard_current') is not null")" "t" "教师积分当前视图缺失"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.teacher_scorecard_current', 'SELECT')")" "t" "教师角色缺少积分当前视图读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select to_regclass('public.teacher_lesson_score_current') is not null")" "t" "逐课积分当前视图缺失"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.teacher_lesson_score_current', 'SELECT')")" "t" "教师角色缺少逐课积分当前视图读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.score_entries', 'SELECT')")" "f" "教师角色不应读取原始积分流水"
assert_equals "$("${PSQL[@]}" -Atqc "select coalesce(has_table_privilege('tit_teacher_crud', to_regclass('public.lesson_facts'), 'SELECT'), false)")" "f" "教师角色不应读取原始课程事实"
assert_equals "$("${PSQL[@]}" -Atqc "select coalesce(has_table_privilege('tit_teacher_crud', to_regclass('public.lesson_dimension_scores'), 'SELECT'), false)")" "f" "教师角色不应读取原始逐课积分表"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.config_versions', 'SELECT')")" "f" "教师角色不应读取原始配置表"
assert_equals "$("${PSQL[@]}" -Atqc "select coalesce(has_table_privilege('tit_teacher_crud', to_regclass('public.teacher_metric_snapshots'), 'SELECT'), false)")" "f" "教师角色不应继续读取旧教师快照"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'SELECT')")" "f" "教师角色不应读取整张教师宽表"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'tchr_id', 'SELECT')")" "t" "教师角色缺少 G01 教师关联键读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'is_cpl_tesol', 'SELECT')")" "t" "教师角色缺少 TESOL 状态读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'is_self_introduce', 'SELECT')")" "f" "教师角色不应读取 G01 已停用的 Self-intro 状态"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_source_wide', 'real_name', 'SELECT')")" "f" "教师角色不应读取 G01 无关的宽表字段"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.account_onboarding_states', 'SELECT')")" "t" "教师角色缺少引导状态读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.account_onboarding_states', 'INSERT')")" "t" "教师角色缺少引导状态幂等写入权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.account_onboarding_states', 'UPDATE')")" "f" "教师角色不应改写引导终态事实"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.account_onboarding_states', 'DELETE')")" "f" "教师角色不应删除引导终态事实"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.notifications', 'read_at', 'UPDATE')")" "t" "教师角色缺少消息已读权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.notifications', 'clicked_at', 'UPDATE')")" "t" "教师角色缺少消息点击权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.notification_events', 'INSERT')")" "t" "教师角色缺少消息事件写入权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.system_notifications', 'DELETE')")" "f" "教师角色不应删除系统通知"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'tide.system_notifications', 'read_at', 'UPDATE')")" "t" "教师角色缺少系统通知已读权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'tide.system_notifications', 'title', 'UPDATE')")" "f" "教师角色不应修改已发布系统通知正文"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.system_notification_publications', 'DELETE')")" "f" "教师角色不应删除系统通知发布记录"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'tide.growth_stage_notification_states', 'UPDATE')")" "t" "教师角色缺少成长阶段通知状态维护权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.teacher_support_tickets', 'SELECT')")" "t" "教师角色缺少本人工单读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_teacher_crud', 'public.teacher_support_tickets', 'INSERT')")" "f" "教师角色不应绕过创建方法直接写工单"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_support_tickets', 'messages', 'UPDATE')")" "f" "教师角色不应整体覆盖工单消息"
assert_equals "$("${PSQL[@]}" -Atqc "select has_column_privilege('tit_teacher_crud', 'public.teacher_support_tickets', 'status', 'UPDATE')")" "t" "教师角色缺少工单状态维护权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_function_privilege('tit_teacher_crud', 'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)', 'EXECUTE')")" "t" "教师角色缺少工单创建方法权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_function_privilege('tit_teacher_crud', 'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)', 'EXECUTE')")" "t" "教师角色缺少工单消息追加方法权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_function_privilege('tit_teacher_crud', 'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)', 'EXECUTE')")" "f" "教师角色不应追加运营消息"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_growth_app', 'public.teacher_support_tickets', 'SELECT')")" "t" "运营角色缺少工单读取权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tit_growth_app', 'public.teacher_support_tickets', 'UPDATE')")" "f" "运营角色不应直接更新工单"
assert_equals "$("${PSQL[@]}" -Atqc "select has_function_privilege('tit_growth_app', 'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)', 'EXECUTE')")" "t" "运营角色缺少运营消息追加权限"
assert_equals "$("${PSQL[@]}" -Atqc "select has_function_privilege('tit_growth_app', 'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)', 'EXECUTE')")" "f" "运营角色不应追加教师消息"
assert_equals "$("${PSQL[@]}" -Atqc "select rolcanlogin from pg_roles where rolname = 'tide_support_ticket_owner'")" "f" "工单函数 owner 不得登录"
assert_equals "$("${PSQL[@]}" -Atqc "select rolsuper from pg_roles where rolname = 'tide_support_ticket_owner'")" "f" "工单函数 owner 不得是 superuser"
assert_equals "$("${PSQL[@]}" -Atqc "select has_schema_privilege('tide_support_ticket_owner', 'public', 'CREATE')")" "f" "工单函数 owner 不得在 public 建对象"
assert_equals "$("${PSQL[@]}" -Atqc "select has_table_privilege('tide_support_ticket_owner', 'public.teacher_support_tickets', 'DELETE')")" "f" "工单函数 owner 不应删除工单"
assert_equals "$("${PSQL[@]}" -Atqc "select rolcanlogin from pg_roles where rolname = 'tit_teacher_crud'")" "t" "本地教师应用角色尚未启用登录"
assert_equals "$("${PSQL[@]}" -Atqc "select pg_get_constraintdef(oid) like '%VIEW%' from pg_constraint where conrelid = 'tide.task_command_receipts'::regclass and conname = 'task_command_receipts_type_check'")" "t" "VIEW 幂等命令约束未生效"
assert_equals "$("${PSQL[@]}" -Atqc "select position('A-Z0-9' in pg_get_constraintdef(oid)) > 0 from pg_constraint where conrelid = 'tide.task_execution_versions'::regclass and conname = 'task_execution_versions_code_check'")" "t" "个性化任务执行代码约束未生效"
assert_equals "$("${PSQL[@]}" -Atqc "select payload->>'title' from public.task_templates where template_id = 'G04' and status = 'PUBLISHED' order by template_version desc limit 1")" "Lesson Preparation" "G04 未配置为当前首课准备任务"
assert_equals "$("${PSQL[@]}" -Atqc "select payload->>'ops_name_zh' from public.task_templates where template_id = 'G04' and status = 'PUBLISHED' order by template_version desc limit 1")" "首课准备" "G04 运营中文名称未保持为首课准备"
assert_equals "$("${PSQL[@]}" -Atqc "
  select
    execution.config =
      '{\"estimatedMinutes\":15,\"allowRetry\":true,\"contentStatus\":\"READY\",\"contentVersion\":\"2026-08-11-g04-two-part\",\"pendingReason\":null,\"independentModules\":{\"stepKeys\":[\"g02-environment-photo\",\"g02-courseware-confirmation\"],\"allowOutOfOrderProgress\":true,\"keepAssignmentInProgressUntilPassed\":true}}'::jsonb
    and (
      select count(*) = 2
      from tide.task_step_definitions definition
      where definition.execution_version_id = execution.id
    )
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
        and definition.config->>'version' = 'g02-courseware-2026-08-05-guidance-v1'
    )
    and not exists (
      select 1
      from tide.task_step_definitions definition
      where definition.execution_version_id = execution.id
        and definition.step_key = 'g02-device-check'
    )
    and exists (
      select 1
      from tide.task_validation_rules rule
      where rule.execution_version_id = execution.id
        and rule.rule_key = 'all-steps-complete'
        and rule.rule_version = '2026-08-11-g04-two-part-v1'
        and rule.config = '{\"requiredStepKeys\":[\"g02-environment-photo\",\"g02-courseware-confirmation\"]}'::jsonb
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
          '[\"camera_angle\",\"lighting\",\"background\",\"dressing\"]'::jsonb
    )
  from tide.task_execution_versions execution
  where execution.shared_template_row_id = 'G02:v1'
")" "t" "G04 未配置为当前照片与课件两个独立模块或规则版本异常"

"${PSQL[@]}" >/dev/null <<'SQL'
BEGIN;

SET ROLE tit_teacher_crud;

SELECT (
  public.create_teacher_support_ticket(
    '41000000-0000-4000-8000-000000000001',
    'MOCK-TEACHER-001',
    'PRODUCT_FUNCTION',
    'HELP',
    '{"page":"HELP","browser":"Chrome"}'::jsonb,
    '{
      "message_id":"41000000-0000-4000-8000-000000000011",
      "sender":"TEACHER",
      "content":"[Verify] The page did not update.",
      "images":[]
    }'::jsonb
  )
).ticket_id;

DO $verify$
DECLARE
  failed boolean := false;
BEGIN
  BEGIN
    PERFORM public.append_teacher_support_ticket_teacher_message(
      '41000000-0000-4000-8000-000000000001',
      'MOCK-TEACHER-001',
      NULL,
      '{
        "message_id":"41000000-0000-4000-8000-000000000010",
        "sender":"TEACHER",
        "content":"[Verify] NULL CAS.",
        "images":[]
      }'::jsonb
    );
  EXCEPTION WHEN serialization_failure THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher NULL expected row version bypassed CAS';
  END IF;
END
$verify$;

SELECT (
  public.append_teacher_support_ticket_teacher_message(
    '41000000-0000-4000-8000-000000000001',
    'MOCK-TEACHER-001',
    1,
    '{
      "message_id":"41000000-0000-4000-8000-000000000012",
      "sender":"TEACHER",
      "content":"[Verify] Additional context.",
      "images":[]
    }'::jsonb
  )
).ticket_id;

DO $verify$
DECLARE
  failed boolean := false;
BEGIN
  BEGIN
    UPDATE public.teacher_support_tickets
    SET messages = '[]'::jsonb
    WHERE ticket_id = '41000000-0000-4000-8000-000000000001';
  EXCEPTION WHEN insufficient_privilege THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly replaced support messages';
  END IF;

  failed := false;
  BEGIN
    PERFORM public.append_teacher_support_ticket_operator_message(
      '41000000-0000-4000-8000-000000000001',
      2,
      '{
        "message_id":"41000000-0000-4000-8000-000000000021",
        "sender":"OPERATOR",
        "content":"[Verify] Operator reply.",
        "images":[]
      }'::jsonb
    );
  EXCEPTION WHEN insufficient_privilege THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly appended an operator reply';
  END IF;
END
$verify$;

RESET ROLE;

SET ROLE tit_growth_app;
DO $verify$
DECLARE
  failed boolean := false;
BEGIN
  BEGIN
    PERFORM public.append_teacher_support_ticket_operator_message(
      '41000000-0000-4000-8000-000000000001',
      NULL,
      '{
        "message_id":"41000000-0000-4000-8000-000000000020",
        "sender":"OPERATOR",
        "content":"[Verify] NULL CAS.",
        "images":[]
      }'::jsonb
    );
  EXCEPTION WHEN serialization_failure THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'operator NULL expected row version bypassed CAS';
  END IF;
END
$verify$;

SELECT (
  public.append_teacher_support_ticket_operator_message(
    '41000000-0000-4000-8000-000000000001',
    2,
    '{
      "message_id":"41000000-0000-4000-8000-000000000021",
      "sender":"OPERATOR",
      "content":"[Verify] Operator reply.",
      "images":[]
    }'::jsonb
  )
).ticket_id;
SELECT (
  public.append_teacher_support_ticket_operator_message(
    '41000000-0000-4000-8000-000000000001',
    2,
    '{
      "message_id":"41000000-0000-4000-8000-000000000021",
      "sender":"OPERATOR",
      "content":"[Verify] Operator reply.",
      "images":[]
    }'::jsonb
  )
).ticket_id;
RESET ROLE;

DO $verify$
BEGIN
  IF (
    SELECT status = 'WAITING_TEACHER'
      AND last_operator_reply_at IS NOT NULL
      AND teacher_reply_deadline_at - last_operator_reply_at = interval '48 hours'
      AND jsonb_array_length(messages) = 3
      AND row_version = 3
    FROM public.teacher_support_tickets
    WHERE ticket_id = '41000000-0000-4000-8000-000000000001'
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'operator reply atomic transition failed';
  END IF;
END
$verify$;

SET ROLE tit_teacher_crud;

UPDATE public.teacher_support_tickets
SET
  last_read_operator_message_id =
    '41000000-0000-4000-8000-000000000021',
  teacher_last_read_at = now(),
  row_version = row_version + 1,
  updated_at = now()
WHERE ticket_id = '41000000-0000-4000-8000-000000000001'
  AND teacher_id = 'MOCK-TEACHER-001'
  AND row_version = 3;

UPDATE public.teacher_support_tickets
SET
  status = 'CLOSED',
  close_reason = 'RESOLVED',
  closed_at = now(),
  teacher_reply_deadline_at = NULL,
  row_version = row_version + 1,
  updated_at = now()
WHERE ticket_id = '41000000-0000-4000-8000-000000000001'
  AND teacher_id = 'MOCK-TEACHER-001'
  AND row_version = 4;

DO $verify$
DECLARE
  failed boolean := false;
BEGIN
  IF (
    SELECT primary_category
    FROM public.teacher_support_tickets
    WHERE ticket_id = '41000000-0000-4000-8000-000000000001'
  ) <> 'PRODUCT' THEN
    RAISE EXCEPTION 'support category mapping failed';
  END IF;

  IF (
    SELECT status = 'CLOSED'
      AND close_reason = 'RESOLVED'
      AND jsonb_array_length(messages) = 3
      AND row_version = 5
    FROM public.teacher_support_tickets
    WHERE ticket_id = '41000000-0000-4000-8000-000000000001'
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'support-ticket lifecycle verification failed';
  END IF;

  BEGIN
    PERFORM public.append_teacher_support_ticket_teacher_message(
      '41000000-0000-4000-8000-000000000001',
      'MOCK-TEACHER-001',
      5,
      '{
        "message_id":"41000000-0000-4000-8000-000000000013",
        "sender":"TEACHER",
        "content":"[Verify] Closed write.",
        "images":[]
      }'::jsonb
    );
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'closed support ticket unexpectedly accepted a message';
  END IF;
END
$verify$;

ROLLBACK;
SQL

"${PSQL[@]}" >/dev/null <<'SQL'
BEGIN;

INSERT INTO public.teachers (teacher_id, camp_enrollment_id, name, data_mode)
VALUES ('VERIFY-TEACHER-CRUD', 'VERIFY-CAMP-CRUD', '[Verify] Teacher', 'MOCK');

INSERT INTO public.task_assignments (
  teacher_id, task_code, template_version_id, task_kind, creator_system,
  priority, why, source_mode, dedupe_key
)
SELECT
  'VERIFY-TEACHER-CRUD', 'G01', row_id, 'FIXED_GROWTH', 'TEACHER_APP',
  'P1', '[Verify] legal fixed assignment', 'MOCK',
  'fixed:VERIFY-TEACHER-CRUD:G01'
FROM public.task_templates
WHERE template_id = 'G01' AND status = 'PUBLISHED'
RETURNING assignment_id AS verify_assignment_id
\gset

SET ROLE tit_teacher_crud;

UPDATE public.task_assignments
SET status = 'IN_PROGRESS', status_changed_at = now() + interval '1 millisecond'
WHERE assignment_id = :'verify_assignment_id' AND row_version = 1;

DO $verify$
DECLARE
  verify_id varchar;
  changed integer;
  failed boolean;
BEGIN
  SELECT assignment_id INTO verify_id
  FROM public.task_assignments
  WHERE dedupe_key = 'fixed:VERIFY-TEACHER-CRUD:G01';

  UPDATE public.task_assignments
  SET status = 'SUBMITTED', status_changed_at = now() + interval '2 milliseconds'
  WHERE assignment_id = verify_id AND row_version = 1;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 0 THEN
    RAISE EXCEPTION 'stale row_version update unexpectedly succeeded';
  END IF;

  failed := false;
  BEGIN
    INSERT INTO public.task_assignments (
      teacher_id, task_code, template_version_id, task_kind, creator_system,
      priority, why, source_mode, dedupe_key
    ) SELECT
      'VERIFY-TEACHER-CRUD', 'G01', row_id, 'FIXED_GROWTH',
      'TEACHER_APP', 'P1', '[Verify] duplicate', 'MOCK',
      'fixed:VERIFY-TEACHER-CRUD:G01'
    FROM public.task_templates WHERE row_id = 'G01:v1';
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'duplicate fixed assignment unexpectedly succeeded';
  END IF;

  failed := false;
  BEGIN
    INSERT INTO public.task_assignments (
      teacher_id, task_code, template_version_id, task_kind, creator_system,
      priority, why, source_mode, dedupe_key
    ) SELECT
      'VERIFY-TEACHER-CRUD', 'G02', row_id, 'FIXED_GROWTH',
      'TRIGGER_CENTER', 'P1', '[Verify] wrong creator', 'MOCK',
      'fixed:VERIFY-TEACHER-CRUD:G02'
    FROM public.task_templates WHERE row_id = 'G02:v1';
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'wrong fixed-task creator unexpectedly succeeded';
  END IF;

  failed := false;
  BEGIN
    INSERT INTO public.task_assignments (
      teacher_id, task_code, template_version_id, task_kind, creator_system,
      priority, why, source_mode, dedupe_key
    ) SELECT
      'VERIFY-TEACHER-CRUD', 'P-VERIFY', row_id, 'FIXED_GROWTH',
      'TEACHER_APP', 'P1', '[Verify] non-G fixed task', 'MOCK',
      'fixed:VERIFY-TEACHER-CRUD:P-VERIFY'
    FROM public.task_templates WHERE row_id = 'G02:v1';
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'non-G fixed assignment unexpectedly succeeded';
  END IF;

  failed := false;
  BEGIN
    INSERT INTO public.task_assignments (
      teacher_id, task_code, template_version_id, task_kind, creator_system,
      status, priority, why, source_mode, dedupe_key
    ) SELECT
      'VERIFY-TEACHER-CRUD', 'G02', row_id, 'FIXED_GROWTH',
      'TEACHER_APP', 'IN_PROGRESS', 'P1', '[Verify] wrong initial status',
      'MOCK', 'fixed:VERIFY-TEACHER-CRUD:G02'
    FROM public.task_templates WHERE row_id = 'G02:v1';
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'non-ASSIGNED initial status unexpectedly succeeded';
  END IF;

  failed := false;
  BEGIN
    UPDATE public.task_assignments
    SET status = 'WAIVED', status_reason_code = 'VERIFY',
        status_changed_at = now() + interval '3 milliseconds'
    WHERE assignment_id = verify_id;
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly waived an assignment';
  END IF;

  failed := false;
  BEGIN
    UPDATE public.task_assignments
    SET priority = 'P0'
    WHERE assignment_id = verify_id;
  EXCEPTION WHEN insufficient_privilege THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly updated an immutable field';
  END IF;

  failed := false;
  BEGIN
    UPDATE public.task_templates SET status = 'RETIRED' WHERE row_id = 'G01:v1';
  EXCEPTION WHEN insufficient_privilege THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly updated a task template';
  END IF;

  failed := false;
  BEGIN
    INSERT INTO public.score_entries (
      score_entry_id, teacher_id, task_assignment_id, entry_type,
      delta_score, idempotency_key
    ) VALUES (
      'VERIFY-SCORE', 'VERIFY-TEACHER-CRUD', verify_id,
      'TASK', 1, 'VERIFY-SCORE'
    );
  EXCEPTION WHEN insufficient_privilege THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'teacher role unexpectedly wrote a score entry';
  END IF;
END
$verify$;

RESET ROLE;

INSERT INTO public.notifications (
  notification_id, task_id, teacher_id, channel, priority, status, payload
) VALUES (
  'VERIFY-NOTIFICATION', :'verify_assignment_id', 'VERIFY-TEACHER-CRUD',
  'IN_APP', 'P1', 'STORED', '{"verify":true}'::jsonb
);

SET ROLE tit_teacher_crud;

UPDATE public.notifications
SET read_at = coalesce(read_at, now()), status = 'READ'
WHERE notification_id = 'VERIFY-NOTIFICATION';
UPDATE public.notifications
SET clicked_at = coalesce(clicked_at, now()), status = 'CLICKED'
WHERE notification_id = 'VERIFY-NOTIFICATION';

INSERT INTO public.notification_events (
  notification_event_id, notification_id, delivery_status, request_hash, payload
) VALUES (
  'VERIFY-NOTIFICATION-EVENT-READ', 'VERIFY-NOTIFICATION', 'READ',
  'verify-read', '{"verify":true}'::jsonb
);
INSERT INTO public.notification_events (
  notification_event_id, notification_id, delivery_status, request_hash, payload
) VALUES (
  'VERIFY-NOTIFICATION-EVENT-READ-DUP', 'VERIFY-NOTIFICATION', 'READ',
  'verify-read', '{"verify":true}'::jsonb
) ON CONFLICT (notification_id, request_hash) DO NOTHING;

UPDATE public.task_assignments
SET status = 'COMPLETED', completed_at = now(),
    status_changed_at = now() + interval '4 milliseconds'
WHERE assignment_id = :'verify_assignment_id' AND row_version = 2;

DO $verify$
DECLARE
  verify_id varchar;
  failed boolean := false;
BEGIN
  SELECT assignment_id INTO verify_id
  FROM public.task_assignments
  WHERE dedupe_key = 'fixed:VERIFY-TEACHER-CRUD:G01';

  BEGIN
    UPDATE public.task_assignments
    SET status = 'IN_PROGRESS', completed_at = NULL,
        status_changed_at = now() + interval '5 milliseconds'
    WHERE assignment_id = verify_id;
  EXCEPTION WHEN OTHERS THEN
    failed := true;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'terminal assignment unexpectedly moved backwards';
  END IF;
END
$verify$;

RESET ROLE;

DO $verify$
DECLARE
  verify_id varchar;
BEGIN
  SELECT assignment_id INTO verify_id
  FROM public.task_assignments
  WHERE dedupe_key = 'fixed:VERIFY-TEACHER-CRUD:G01';

  IF (SELECT row_version FROM public.task_assignments WHERE assignment_id = verify_id) <> 3 THEN
    RAISE EXCEPTION 'assignment row_version did not advance exactly twice';
  END IF;
  IF (SELECT status FROM public.task_assignments WHERE assignment_id = verify_id) <> 'COMPLETED' THEN
    RAISE EXCEPTION 'assignment status update was not persisted';
  END IF;
  IF (SELECT actor FROM public.audit_events WHERE task_id = verify_id ORDER BY sequence DESC LIMIT 1) <> 'tit_teacher_crud' THEN
    RAISE EXCEPTION 'audit actor is not tit_teacher_crud';
  END IF;
  IF (SELECT count(*) FROM public.outbox_events WHERE aggregate_id = verify_id) <> 3 THEN
    RAISE EXCEPTION 'assignment outbox event count is invalid';
  END IF;
  IF (SELECT count(*) FROM public.score_entries WHERE task_assignment_id = verify_id) <> 0 THEN
    RAISE EXCEPTION 'teacher flow unexpectedly created a score entry';
  END IF;
  IF (SELECT count(*) FROM public.notification_events WHERE notification_id = 'VERIFY-NOTIFICATION') <> 1 THEN
    RAISE EXCEPTION 'notification event idempotency failed';
  END IF;
  IF NOT (SELECT read_at IS NOT NULL AND clicked_at IS NOT NULL FROM public.notifications WHERE notification_id = 'VERIFY-NOTIFICATION') THEN
    RAISE EXCEPTION 'notification read/click timestamps were not persisted';
  END IF;
END
$verify$;

ROLLBACK;
SQL

echo "PostgreSQL ${server_version}：共享任务、tide 过程表、最小权限、审计/Outbox 和消息回写验证通过。"
