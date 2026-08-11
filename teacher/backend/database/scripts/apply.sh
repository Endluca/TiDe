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

if [[ "${TIDE_DB_NAME:-}" != "tide_dev" ]]; then
  echo "本地 Mock Seed 只允许写入 tide_dev，当前数据库为：${TIDE_DB_NAME:-未配置}。" >&2
  exit 1
fi

if [[ "${ALLOW_MOCK_SEED:-false}" != "true" ]]; then
  echo "本地初始化会写入 Mock Seed。确认目标为本地 tide_dev 后，请设置 ALLOW_MOCK_SEED=true。" >&2
  exit 1
fi

export PGPASSWORD="${TIDE_DB_PASSWORD}"
PSQL=(psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "${TIDE_DB_PORT}" -U "${TIDE_DB_USER}" -d "${TIDE_DB_NAME}")

shared_contract_exists="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('public.task_assignments') is not null
    and to_regclass('public.lesson_facts') is not null
    and to_regclass('public.lesson_dimension_scores') is not null
    and to_regclass('public.config_versions') is not null
    and exists (
      select 1
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'task_assignments'
        and column_name = 'display_title'
    )
    and exists (
      select 1
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'task_assignments'
        and column_name = 'evidence_snapshot'
    )
")"
if [[ "${shared_contract_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/fixtures/0001_shared_contract.sql"
fi
"${PSQL[@]}" -f "${DB_DIR}/fixtures/0002_score_entry_contract.sql"
"${PSQL[@]}" -f "${DB_DIR}/fixtures/0003_course_score_snapshot_contract.sql"

# 只在本地开发库为受限应用角色启用登录；生产角色由 DBA 管理。
"${PSQL[@]}" -v teacher_role_password="${TIDE_DB_PASSWORD}" >/dev/null <<'SQL'
ALTER ROLE tit_teacher_crud LOGIN PASSWORD :'teacher_role_password';
SQL

shared_links_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.task_execution_versions') is not null")"

if [[ "${shared_links_exists}" != "t" ]]; then
  initial_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.user_accounts') is not null")"
  exchange_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.shiwen_personalized_status_events_v1') is not null")"
  file_intents_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.file_upload_intents') is not null")"
  task_commands_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.task_command_receipts') is not null")"
  faq_commands_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.qa_message_commands') is not null")"
  profile_projections_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.teacher_identity_projections') is not null")"

  if [[ "${initial_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0001_initial.up.sql"
  fi
  if [[ "${exchange_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0002_shared_database_exchange.up.sql"
  fi
  if [[ "${file_intents_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0003_file_upload_intents.up.sql"
  fi
  if [[ "${task_commands_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0004_task_command_receipts.up.sql"
  fi
  if [[ "${faq_commands_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0005_faq_message_commands.up.sql"
  fi
  if [[ "${profile_projections_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0006_teacher_profile_g01_support.up.sql"
  fi

  "${PSQL[@]}" -f "${DB_DIR}/seed/0000_mock_shared_catalog.sql"
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0007_shared_task_assignment_links.up.sql"
else
  "${PSQL[@]}" -f "${DB_DIR}/seed/0000_mock_shared_catalog.sql"
fi

legacy_task_table_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.teacher_tasks') is not null")"
if [[ "${legacy_task_table_exists}" == "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0008_remove_legacy_task_exchange.up.sql"
fi

view_command_enabled="$("${PSQL[@]}" -Atqc "
  select pg_get_constraintdef(oid) like '%VIEW%'
  from pg_constraint
  where conrelid = 'tide.task_command_receipts'::regclass
    and conname = 'task_command_receipts_type_check'
")"
if [[ "${view_command_enabled}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0009_task_view_command.up.sql"
fi

generic_task_codes_enabled="$("${PSQL[@]}" -Atqc "
  select position('A-Z0-9' in pg_get_constraintdef(oid)) > 0
  from pg_constraint
  where conrelid = 'tide.task_execution_versions'::regclass
    and conname = 'task_execution_versions_code_check'
")"
if [[ "${generic_task_codes_enabled}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0010_current_task_execution.up.sql"
fi

system_notification_delivery_enabled="$("${PSQL[@]}" -Atqc "
  select to_regclass('tide.system_notification_publications') is not null
")"
if [[ "${system_notification_delivery_enabled}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0011_system_notification_delivery.up.sql"
fi

system_notification_guards_enabled="$("${PSQL[@]}" -Atqc "
  select position(
    'recipient_count IS DISTINCT' in
    pg_get_functiondef('tide.protect_system_notification_publication()'::regprocedure)
  ) > 0
")"
if [[ "${system_notification_guards_enabled}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0012_system_notification_publication_guards.up.sql"
fi

system_notification_owner_maintenance_enabled="$("${PSQL[@]}" -Atqc "
  select position(
    'RETURN OLD' in
    pg_get_functiondef('tide.protect_system_notification_content()'::regprocedure)
  ) > 0
")"
if [[ "${system_notification_owner_maintenance_enabled}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0013_system_notification_owner_maintenance.up.sql"
fi

unused_tide_objects_removed="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.analytics_task_event_semantics_v2') is not null
    and to_regclass('tide.teacher_photo_runs') is null
")"
if [[ "${unused_tide_objects_removed}" != "t" ]]; then
  teacher_photo_processing_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.teacher_photo_runs') is not null")"
  if [[ "${teacher_photo_processing_exists}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0014_teacher_photo_processing.up.sql"
  fi

  teacher_photo_full_strength_enabled="$("${PSQL[@]}" -Atqc "
    select position(
      '1.000' in pg_get_constraintdef(oid)
    ) > 0
    from pg_constraint
    where conrelid = 'tide.teacher_photo_runs'::regclass
      and conname = 'teacher_photo_runs_strength_check'
  ")"
  if [[ "${teacher_photo_full_strength_enabled}" != "t" ]]; then
    "${PSQL[@]}" -f "${DB_DIR}/migrations/0015_teacher_photo_filter_strength.up.sql"
  fi
fi

quiz_banks_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.task_quiz_banks') is not null")"
if [[ "${quiz_banks_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0016_database_quiz_banks.up.sql"
fi

assignment_teacher_response_column_count="$("${PSQL[@]}" -Atqc "
  select count(*)
  from information_schema.columns
  where table_schema = 'public'
    and table_name = 'task_assignments'
    and column_name like 'teacher_response_%'
")"
if [[ "${assignment_teacher_response_column_count}" == "4" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0018_remove_task_assignment_teacher_response.up.sql"
elif [[ "${assignment_teacher_response_column_count}" != "0" ]]; then
  echo "共享任务事实说明字段处于不完整状态，删除已停止。" >&2
  exit 1
fi

growth_stage_notification_state_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.growth_stage_notification_states') is not null")"
if [[ "${growth_stage_notification_state_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0019_growth_stage_notification_state.up.sql"
fi

product_analytics_exists="$("${PSQL[@]}" -Atqc "select to_regclass('tide.analytics_task_funnel_v2') is not null")"
if [[ "${product_analytics_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0020_product_analytics.up.sql"
fi

teacher_support_tickets_exists="$("${PSQL[@]}" -Atqc "select to_regclass('public.teacher_support_tickets') is not null")"
if [[ "${teacher_support_tickets_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0021_teacher_support_tickets.up.sql"
fi

performance_job_leases_exists="$("${PSQL[@]}" -Atqc "
  select
    to_regclass('tide.job_leases') is not null
    and (
      to_regclass('tide.teacher_photo_runs') is null
      or exists (
        select 1
        from information_schema.columns
        where table_schema = 'tide'
          and table_name = 'teacher_photo_runs'
          and column_name = 'processing_owner'
      )
    )
")"
if [[ "${performance_job_leases_exists}" != "t" ]]; then
  if [[ "${unused_tide_objects_removed}" == "t" ]]; then
    echo "0029 已应用但 tide.job_leases 缺失，数据库处于不一致状态。" >&2
    exit 1
  fi
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0022_performance_job_leases.up.sql"
fi

operator_reply_atomicity_enabled="$("${PSQL[@]}" -Atqc "
  select position(
    'teacher_reply_deadline_at = reply_at + interval ''48 hours''' in
    pg_get_functiondef(
      'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
    )
  ) > 0
")"
if [[ "${operator_reply_atomicity_enabled}" != "t" ]]; then
  tit_growth_role_exists="$("${PSQL[@]}" -Atqc "
    select exists (select 1 from pg_roles where rolname = 'tit_growth_app')
  ")"
  if [[ "${tit_growth_role_exists}" != "t" ]]; then
    "${PSQL[@]}" -c "CREATE ROLE tit_growth_app NOLOGIN"
  fi
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.up.sql"
fi

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
    )
")"
if [[ "${support_ticket_security_hardened}" != "t" ]]; then
  support_ticket_owner_exists="$("${PSQL[@]}" -Atqc "
    select exists (
      select 1
      from pg_roles
      where rolname = 'tide_support_ticket_owner'
    )
  ")"
  if [[ "${support_ticket_owner_exists}" != "t" ]]; then
    "${PSQL[@]}" -c "
      CREATE ROLE tide_support_ticket_owner
        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS
    "
  fi
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.up.sql"
fi

"${PSQL[@]}" -f "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.up.sql"
kuozhi_course_syncs_exists="$("${PSQL[@]}" -Atqc "
  select to_regclass('tide.kuozhi_course_syncs') is not null
")"
if [[ "${kuozhi_course_syncs_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0026_kuozhi_course_syncs.up.sql"
fi
"${PSQL[@]}" -f "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.up.sql"
"${PSQL[@]}" -f "${DB_DIR}/migrations/0028_retire_task_business_change_view.up.sql"
"${PSQL[@]}" -f "${DB_DIR}/migrations/0029_remove_unused_tide_objects.up.sql"
unused_column_cleanup_state="$("${PSQL[@]}" -AtF '|' -c "
  select
    exists (
      select 1
      from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'file_objects'
        and column_name = 'visibility'
    ),
    to_regprocedure('tide.enforce_outbox_target()') is not null
")"
if [[ "${unused_column_cleanup_state}" == "t|t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.up.sql"
elif [[ "${unused_column_cleanup_state}" != "f|f" ]]; then
  echo "0030 无用字段与孤儿函数处于不一致状态，迁移已停止。" >&2
  exit 1
fi
g04_two_part_execution_ready="$("${PSQL[@]}" -Atqc "
  with g04 as (
    select id
    from tide.task_execution_versions
    where shared_template_row_id = 'G02:v1'
      and task_code = 'G04'
      and execution_contract_version = 'task-contract-v3'
      and status = 'ACTIVE'
      and config =
        '{\"estimatedMinutes\":15,\"allowRetry\":true,\"contentStatus\":\"READY\",\"contentVersion\":\"2026-08-11-g04-two-part\",\"pendingReason\":null,\"independentModules\":{\"stepKeys\":[\"g02-environment-photo\",\"g02-courseware-confirmation\"],\"allowOutOfOrderProgress\":true,\"keepAssignmentInProgressUntilPassed\":true}}'::jsonb
  )
  select
    (select count(*) from g04) = 1
    and (
      select count(*)
      from tide.task_step_definitions definition
      join g04 on g04.id = definition.execution_version_id
    ) = 2
    and exists (
      select 1
      from tide.task_step_definitions definition
      join g04 on g04.id = definition.execution_version_id
      where definition.step_key = 'g02-environment-photo'
        and definition.position = 1
        and definition.step_type = 'UPLOAD'
        and definition.title = 'Take a teaching-environment photo'
        and definition.config =
          '{\"version\":\"g02-photo-2026-07-22\",\"role\":\"ENVIRONMENT_PHOTO\",\"accept\":[\"image/jpeg\"],\"captureOnly\":true,\"maxFiles\":1}'::jsonb
    )
    and exists (
      select 1
      from tide.task_step_definitions definition
      join g04 on g04.id = definition.execution_version_id
      where definition.step_key = 'g02-courseware-confirmation'
        and definition.position = 2
        and definition.step_type = 'CHECKLIST'
        and definition.title = 'Review and confirm lesson-preparation guidance'
        and definition.config =
          '{\"version\":\"g02-courseware-2026-08-05-guidance-v1\",\"role\":\"COURSEWARE_CONFIRMATION\",\"items\":[{\"key\":\"courseware-prepared\",\"label\":\"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.\",\"labelZh\":\"我已阅读备课须知并浏览全部课件，已完成本节课备课。\"}]}'::jsonb
    )
    and (
      select count(*)
      from tide.task_validation_rules rule
      join g04 on g04.id = rule.execution_version_id
    ) = 2
    and exists (
      select 1
      from tide.task_validation_rules rule
      join g04 on g04.id = rule.execution_version_id
      where rule.rule_key = 'all-steps-complete'
        and rule.rule_type = 'ALL_STEPS_COMPLETE'
        and rule.position = 1
        and rule.rule_version = '2026-08-11-g04-two-part-v1'
        and rule.config =
          '{\"requiredStepKeys\":[\"g02-environment-photo\",\"g02-courseware-confirmation\"]}'::jsonb
        and rule.teacher_failure_copy =
          '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
    )
    and exists (
      select 1
      from tide.task_validation_rules rule
      join g04 on g04.id = rule.execution_version_id
      where rule.rule_key = 'g02-environment-ai-review'
        and rule.rule_type = 'AI_IMAGE_REVIEW'
        and rule.rule_version = '2026-07-27-strict'
        and rule.position = 2
        and rule.config->>'stepKey' = 'g02-environment-photo'
        and rule.config->>'criteriaVersion' =
          'lesson-preparation-camera-view-2026-08-v7-background-veto'
        and rule.config->'criteriaKeys' =
          '[\"camera_angle\",\"lighting\",\"background\",\"dressing\"]'::jsonb
        and rule.config->'allowedMimeTypes' =
          '[\"image/jpeg\",\"image/png\",\"image/webp\"]'::jsonb
        and rule.config->>'systemPrompt' =
          'You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.'
        and rule.config->>'userText' =
          'Review this real teaching-environment photo strictly against camera angle, lighting, background and dressing only.'
        and (
          select count(*)
          from jsonb_object_keys(rule.config)
        ) = 6
        and rule.teacher_failure_copy =
          '已保留你完成的内容，请根据提示更新这份材料。'
    )
")"
if [[ "${g04_two_part_execution_ready}" == "t" ]]; then
  echo "G04 已是 exact 0037 two-part execution，跳过历史 0031 重放。"
else
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0031_g04_independent_sections.up.sql"
fi

account_onboarding_states_exists="$("${PSQL[@]}" -Atqc "
  select to_regclass('tide.account_onboarding_states') is not null
")"
if [[ "${account_onboarding_states_exists}" != "t" ]]; then
  "${PSQL[@]}" -f "${DB_DIR}/migrations/0032_first_login_onboarding.up.sql"
fi
"${PSQL[@]}" -f "${DB_DIR}/migrations/0033_g01_tesol_only.up.sql"
"${PSQL[@]}" -f "${DB_DIR}/seed/0005_mock_g04_two_part_catalog.sql"
"${PSQL[@]}" -f "${DB_DIR}/migrations/0037_g04_remove_device_check.up.sql"

"${PSQL[@]}" -f "${DB_DIR}/seed/0002_mock_shiwen_views.sql"
"${PSQL[@]}" -f "${DB_DIR}/seed/0004_mock_faq_knowledge.sql"
pnpm --dir "${DB_DIR}/.." exec ts-node scripts/sync-current-task-catalog.ts
"${PSQL[@]}" -f "${DB_DIR}/scripts/grant-tit-teacher-crud.sql"

echo "迁移 0001 至 0037、共享表本地契约和当前 Seeds 已检查并执行。"
