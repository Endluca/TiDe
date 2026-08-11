#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_FILE="${TIDE_DATABASE_ENV_FILE:-${DB_DIR}/.env}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少 ${ENV_FILE}。" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

TIDE_DB_HOST="${TIDE_DB_HOST:-${TIDE_ADMIN_DB_HOST:-127.0.0.1}}"
TIDE_DB_PORT="${TIDE_DB_PORT:-${TIDE_ADMIN_DB_PORT:-5432}}"
TIDE_DB_USER="${TIDE_DB_USER:-${TIDE_ADMIN_DB_USER:-}}"
TIDE_DB_PASSWORD="${TIDE_DB_PASSWORD:-${TIDE_ADMIN_DB_PASSWORD:-}}"

export PGPASSWORD="${TIDE_DB_PASSWORD}"
ADMIN_PSQL=(psql -X -v ON_ERROR_STOP=1 -h "${TIDE_DB_HOST}" -p "${TIDE_DB_PORT}" -U "${TIDE_DB_USER}")
TEST_DB="tide_rollback_test"

"${ADMIN_PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB}" >/dev/null
"${ADMIN_PSQL[@]}" -d postgres -c "CREATE DATABASE ${TEST_DB}" >/dev/null

cleanup() {
  "${ADMIN_PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB}" >/dev/null
}
trap cleanup EXIT

run_sql() {
  "${ADMIN_PSQL[@]}" -d "${TEST_DB}" -f "$1" >/dev/null
}

run_sql "${DB_DIR}/fixtures/0001_shared_contract.sql"
run_sql "${DB_DIR}/fixtures/0002_score_entry_contract.sql"
run_sql "${DB_DIR}/fixtures/0003_course_score_snapshot_contract.sql"
run_sql "${DB_DIR}/migrations/0001_initial.up.sql"
run_sql "${DB_DIR}/migrations/0002_shared_database_exchange.up.sql"
run_sql "${DB_DIR}/migrations/0003_file_upload_intents.up.sql"
run_sql "${DB_DIR}/migrations/0004_task_command_receipts.up.sql"
run_sql "${DB_DIR}/migrations/0005_faq_message_commands.up.sql"
run_sql "${DB_DIR}/migrations/0006_teacher_profile_g01_support.up.sql"
run_sql "${DB_DIR}/seed/0000_mock_shared_catalog.sql"
run_sql "${DB_DIR}/migrations/0007_shared_task_assignment_links.up.sql"
run_sql "${DB_DIR}/migrations/0008_remove_legacy_task_exchange.up.sql"
run_sql "${DB_DIR}/migrations/0009_task_view_command.up.sql"
run_sql "${DB_DIR}/migrations/0010_current_task_execution.up.sql"
run_sql "${DB_DIR}/migrations/0011_system_notification_delivery.up.sql"
run_sql "${DB_DIR}/migrations/0012_system_notification_publication_guards.up.sql"
run_sql "${DB_DIR}/migrations/0013_system_notification_owner_maintenance.up.sql"
run_sql "${DB_DIR}/migrations/0014_teacher_photo_processing.up.sql"
run_sql "${DB_DIR}/migrations/0015_teacher_photo_filter_strength.up.sql"
run_sql "${DB_DIR}/migrations/0016_database_quiz_banks.up.sql"
run_sql "${DB_DIR}/migrations/0017_task_assignment_teacher_response.up.sql"
run_sql "${DB_DIR}/migrations/0018_remove_task_assignment_teacher_response.up.sql"
run_sql "${DB_DIR}/migrations/0019_growth_stage_notification_state.up.sql"
run_sql "${DB_DIR}/migrations/0020_product_analytics.up.sql"
business_change_view_definition="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc \
  "select pg_get_viewdef('tide.analytics_task_business_change_v1'::regclass, true)")"
[[ -n "${business_change_view_definition}" ]] || {
  echo "0020 未建立待退役业务变化视图" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0021_teacher_support_tickets.up.sql"
run_sql "${DB_DIR}/migrations/0022_performance_job_leases.up.sql"
run_sql "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.up.sql"
run_sql "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.up.sql"
run_sql "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.up.sql"
run_sql "${DB_DIR}/migrations/0026_kuozhi_course_syncs.up.sql"
run_sql "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.up.sql"
run_sql "${DB_DIR}/migrations/0028_retire_task_business_change_view.up.sql"
unused_view_definitions="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select string_agg(
    view_name || ':' || pg_get_viewdef(format('tide.%I', view_name)::regclass, true),
    E'\\n' order by view_name
  )
  from unnest(array[
    'analytics_actor_task_journey_v1',
    'analytics_task_assignment_funnel_v1',
    'analytics_task_funnel_v1',
    'analytics_task_step_funnel_v1',
    'analytics_content_quality_v1'
  ]::text[]) expected(view_name)
")"
unused_table_signature="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  with target(table_name) as (
    values
      ('outcome_projections'),
      ('camp_enrollment_projections'),
      ('audit_events'),
      ('task_template_files'),
      ('file_migrations'),
      ('teacher_photo_runs')
  ), signature_parts as (
    select
      'column' as kind,
      columns.table_name,
      columns.column_name || ':' || columns.udt_name || ':' ||
        columns.is_nullable || ':' ||
        coalesce(columns.column_default, '') as definition
    from information_schema.columns columns
    join target using (table_name)
    where columns.table_schema = 'tide'
    union all
    select
      'constraint', target.table_name, constraint_row.conname || ':' ||
        pg_get_constraintdef(constraint_row.oid, true)
    from target
    join pg_class relation
      on relation.oid = format('tide.%I', target.table_name)::regclass
    join pg_constraint constraint_row
      on constraint_row.conrelid = relation.oid
    union all
    select 'index', indexes.tablename, indexes.indexname || ':' || indexes.indexdef
    from pg_indexes indexes
    join target on target.table_name = indexes.tablename
    where indexes.schemaname = 'tide'
  )
  select md5(string_agg(
    kind || ':' || table_name || ':' || definition,
    E'\\n' order by kind, table_name, definition
  ))
  from signature_parts
")"
run_sql "${DB_DIR}/migrations/0029_remove_unused_tide_objects.up.sql"
file_visibility_signature="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    columns.column_name,
    columns.udt_name,
    columns.is_nullable,
    columns.column_default,
    pg_get_constraintdef(constraint_row.oid, true)
  )
  from information_schema.columns columns
  join pg_constraint constraint_row
    on constraint_row.conrelid = 'tide.file_objects'::regclass
   and constraint_row.conname = 'file_objects_visibility_check'
  where columns.table_schema = 'tide'
    and columns.table_name = 'file_objects'
    and columns.column_name = 'visibility'
")"
orphan_function_definition="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc \
  "select pg_get_functiondef('tide.enforce_outbox_target()'::regprocedure)")"
[[ -n "${file_visibility_signature}" && -n "${orphan_function_definition}" ]] || {
  echo "0030 前置字段或函数结构缺失" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.up.sql"
run_sql "${DB_DIR}/migrations/0031_g04_independent_sections.up.sql"
run_sql "${DB_DIR}/migrations/0032_first_login_onboarding.up.sql"
run_sql "${DB_DIR}/migrations/0033_g01_tesol_only.up.sql"
run_sql "${DB_DIR}/seed/0005_mock_g04_two_part_catalog.sql"
run_sql "${DB_DIR}/migrations/0037_g04_remove_device_check.up.sql"
run_sql "${DB_DIR}/seed/0002_mock_shiwen_views.sql"
run_sql "${DB_DIR}/seed/0004_mock_faq_knowledge.sql"
TIDE_DB_NAME="${TEST_DB}" pnpm --dir "${DB_DIR}/.." exec ts-node scripts/sync-current-task-catalog.ts >/dev/null
run_sql "${DB_DIR}/scripts/grant-tit-teacher-crud.sql"

final_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    to_regclass('public.task_assignments') is not null,
    to_regclass('tide.task_execution_versions') is not null,
    to_regclass('tide.app_events') is not null,
    (
      to_regclass('tide.outcome_projections') is null
      and to_regclass('tide.camp_enrollment_projections') is null
      and to_regclass('tide.audit_events') is null
      and to_regclass('tide.task_template_files') is null
      and to_regclass('tide.file_migrations') is null
      and to_regclass('tide.teacher_photo_runs') is null
    ),
    to_regclass('tide.task_quiz_banks') is null,
    to_regclass('tide.kuozhi_course_syncs') is not null,
    (
      select count(*) = 0
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'task_assignments'
        and column_name like 'teacher_response_%'
    ),
    to_regclass('tide.system_notification_publications') is not null,
    to_regclass('tide.growth_stage_notification_states') is not null,
    (
      to_regclass('tide.analytics_task_funnel_v1') is null
      and to_regclass('tide.analytics_task_funnel_v2') is not null
    ),
    to_regclass('public.teacher_support_tickets') is not null,
    to_regclass('tide.job_leases') is not null,
    to_regclass('tide.account_onboarding_states') is not null,
    to_regclass('tide.teacher_tasks') is null,
    to_regclass('tide.analytics_task_business_change_v1') is null,
    (
      not exists (
        select 1
        from information_schema.columns
        where table_schema = 'tide'
          and table_name = 'file_objects'
          and column_name = 'visibility'
      )
      and to_regprocedure('tide.enforce_outbox_target()') is null
    ),
    (
      select count(*) = 2
        and bool_and(
          (definition.step_key = 'g02-environment-photo'
            and definition.position = 1
            and definition.step_type = 'UPLOAD')
          or
          (definition.step_key = 'g02-courseware-confirmation'
            and definition.position = 2
            and definition.step_type = 'CHECKLIST')
        )
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
    ),
    not exists (
      select 1
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
        and definition.step_key = 'g02-device-check'
    ),
    exists (
      select 1
      from public.task_templates template
      where template.row_id = 'G02:v1'
        and template.template_id = 'G04'
        and template.payload->>'ops_name_zh' = '首课准备'
        and template.payload->>'title' = 'Lesson Preparation'
    ),
    (select count(*) from public.task_assignments where teacher_id = 'MOCK-TEACHER-001'),
    (select count(*) from tide.task_execution_versions),
    (select count(*) from public.task_templates where status = 'PUBLISHED')
  )
")"
[[ "${final_state}" == "t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|9|15|15" ]] || {
  echo "空库升级后状态异常: ${final_state}" >&2
  exit 1
}

# Mirror a second local apply.sh run: seed 0000 resets the shared Mock copy,
# while the exact 0037 execution guard must skip historical 0031. The overlay
# then restores public copy and 0037 must remain idempotent.
run_sql "${DB_DIR}/seed/0000_mock_shared_catalog.sql"
g04_second_apply_guard="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select exists (
    select 1
    from tide.task_execution_versions execution
    where execution.shared_template_row_id = 'G02:v1'
      and execution.task_code = 'G04'
      and execution.config->>'contentVersion' = '2026-08-11-g04-two-part'
      and (
        select array_agg(definition.step_key order by definition.position) =
          array['g02-environment-photo', 'g02-courseware-confirmation']::text[]
        from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
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
          and rule.config =
            '{\"requiredStepKeys\":[\"g02-environment-photo\",\"g02-courseware-confirmation\"]}'::jsonb
      )
  )
")"
[[ "${g04_second_apply_guard}" == "t" ]] || {
  echo "二次本地 apply 未识别 exact 0037 G04，历史 0031 将被错误重放" >&2
  exit 1
}
run_sql "${DB_DIR}/seed/0005_mock_g04_two_part_catalog.sql"
run_sql "${DB_DIR}/migrations/0033_g01_tesol_only.up.sql"
run_sql "${DB_DIR}/migrations/0037_g04_remove_device_check.up.sql"
g04_second_apply_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select exists (
    select 1
    from public.task_templates template
    join tide.task_execution_versions execution
      on execution.shared_template_row_id = template.row_id
    where template.row_id = 'G02:v1'
      and template.template_id = 'G04'
      and template.payload->>'ops_name_zh' = '首课准备'
      and template.payload->>'title' = 'Lesson Preparation'
      and execution.config->>'contentVersion' = '2026-08-11-g04-two-part'
      and (
        select count(*) = 2
        from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
      )
      and not exists (
        select 1
        from tide.task_step_definitions definition
        where definition.execution_version_id = execution.id
          and definition.step_key = 'g02-device-check'
      )
  )
")"
[[ "${g04_second_apply_state}" == "t" ]] || {
  echo "二次本地 apply 未恢复 public G04 文案或破坏两段执行形状" >&2
  exit 1
}

run_sql "${DB_DIR}/migrations/0037_g04_remove_device_check.down.sql"
g04_after_0037_down_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    (
      select array_agg(definition.step_key order by definition.position) =
        array['g02-environment-photo', 'g02-courseware-confirmation']::text[]
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
    ),
    not exists (
      select 1
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
        and definition.step_key = 'g02-device-check'
    )
  )
")"
[[ "${g04_after_0037_down_state}" == "t|t" ]] || {
  echo "0037 forward-only down 错误恢复了 G04 设备检测: ${g04_after_0037_down_state}" >&2
  exit 1
}

run_sql "${DB_DIR}/migrations/0033_g01_tesol_only.down.sql"
g01_rule_down_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|', rule_version, teacher_failure_copy)
  from tide.task_validation_rules rule
  join tide.task_execution_versions execution
    on execution.id = rule.execution_version_id
  where execution.shared_template_row_id = 'G01:v1'
    and rule.rule_key = 'g01-external-status'
")"
[[ "${g01_rule_down_state}" == "2026-07-22|Self-intro 和 TESOL 真实状态尚未全部通过。" ]] || {
  echo "0033 down 未精确恢复 G01 外部状态规则：${g01_rule_down_state}" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0032_first_login_onboarding.down.sql"
account_onboarding_down_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select to_regclass('tide.account_onboarding_states') is null
")"
[[ "${account_onboarding_down_state}" == "t" ]] || {
  echo "0032 down 未删除新手引导状态表" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0032_first_login_onboarding.up.sql"
run_sql "${DB_DIR}/migrations/0033_g01_tesol_only.up.sql"
g01_rule_up_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|', rule_version, teacher_failure_copy)
  from tide.task_validation_rules rule
  join tide.task_execution_versions execution
    on execution.id = rule.execution_version_id
  where execution.shared_template_row_id = 'G01:v1'
    and rule.rule_key = 'g01-external-status'
")"
[[ "${g01_rule_up_state}" == "2026-08-11-tesol-only-v1|TESOL 真实状态尚未通过。" ]] || {
  echo "0033 down-up 未精确恢复 TESOL-only 规则：${g01_rule_up_state}" >&2
  exit 1
}
account_onboarding_up_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select to_regclass('tide.account_onboarding_states') is not null
")"
[[ "${account_onboarding_up_state}" == "t" ]] || {
  echo "0032 down-up 未恢复新手引导状态表" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0033_g01_tesol_only.down.sql"
run_sql "${DB_DIR}/migrations/0032_first_login_onboarding.down.sql"
run_sql "${DB_DIR}/migrations/0031_g04_independent_sections.down.sql"
g04_after_0031_down_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    (
      select array_agg(definition.step_key order by definition.position) =
        array['g02-environment-photo', 'g02-courseware-confirmation']::text[]
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
    ),
    not exists (
      select 1
      from tide.task_step_definitions definition
      join tide.task_execution_versions execution
        on execution.id = definition.execution_version_id
      where execution.shared_template_row_id = 'G02:v1'
        and definition.step_key = 'g02-device-check'
    )
  )
")"
[[ "${g04_after_0031_down_state}" == "t|t" ]] || {
  echo "0031 forward-only down 错误改动了 0037 的 G04 两段结构: ${g04_after_0031_down_state}" >&2
  exit 1
}

run_sql "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.down.sql"
restored_file_visibility_signature="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    columns.column_name,
    columns.udt_name,
    columns.is_nullable,
    columns.column_default,
    pg_get_constraintdef(constraint_row.oid, true)
  )
  from information_schema.columns columns
  join pg_constraint constraint_row
    on constraint_row.conrelid = 'tide.file_objects'::regclass
   and constraint_row.conname = 'file_objects_visibility_check'
  where columns.table_schema = 'tide'
    and columns.table_name = 'file_objects'
    and columns.column_name = 'visibility'
")"
restored_orphan_function_definition="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc \
  "select pg_get_functiondef('tide.enforce_outbox_target()'::regprocedure)")"
[[ "${restored_file_visibility_signature}" == "${file_visibility_signature}" ]] || {
  echo "0030 回滚未精确恢复 file_objects.visibility" >&2
  exit 1
}
[[ "${restored_orphan_function_definition}" == "${orphan_function_definition}" ]] || {
  echo "0030 回滚未精确恢复 enforce_outbox_target()" >&2
  exit 1
}

run_sql "${DB_DIR}/migrations/0029_remove_unused_tide_objects.down.sql"
restored_unused_view_definitions="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select string_agg(
    view_name || ':' || pg_get_viewdef(format('tide.%I', view_name)::regclass, true),
    E'\\n' order by view_name
  )
  from unnest(array[
    'analytics_actor_task_journey_v1',
    'analytics_task_assignment_funnel_v1',
    'analytics_task_funnel_v1',
    'analytics_task_step_funnel_v1',
    'analytics_content_quality_v1'
  ]::text[]) expected(view_name)
")"
restored_unused_table_signature="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  with target(table_name) as (
    values
      ('outcome_projections'),
      ('camp_enrollment_projections'),
      ('audit_events'),
      ('task_template_files'),
      ('file_migrations'),
      ('teacher_photo_runs')
  ), signature_parts as (
    select
      'column' as kind,
      columns.table_name,
      columns.column_name || ':' || columns.udt_name || ':' ||
        columns.is_nullable || ':' ||
        coalesce(columns.column_default, '') as definition
    from information_schema.columns columns
    join target using (table_name)
    where columns.table_schema = 'tide'
    union all
    select
      'constraint', target.table_name, constraint_row.conname || ':' ||
        pg_get_constraintdef(constraint_row.oid, true)
    from target
    join pg_class relation
      on relation.oid = format('tide.%I', target.table_name)::regclass
    join pg_constraint constraint_row
      on constraint_row.conrelid = relation.oid
    union all
    select 'index', indexes.tablename, indexes.indexname || ':' || indexes.indexdef
    from pg_indexes indexes
    join target on target.table_name = indexes.tablename
    where indexes.schemaname = 'tide'
  )
  select md5(string_agg(
    kind || ':' || table_name || ':' || definition,
    E'\\n' order by kind, table_name, definition
  ))
  from signature_parts
")"
[[ "${restored_unused_view_definitions}" == "${unused_view_definitions}" ]] || {
  echo "0029 回滚未精确恢复 v1 分析视图" >&2
  exit 1
}
[[ "${restored_unused_table_signature}" == "${unused_table_signature}" ]] || {
  echo "0029 回滚未精确恢复已清理表结构" >&2
  exit 1
}

run_sql "${DB_DIR}/migrations/0028_retire_task_business_change_view.down.sql"
restored_business_change_view_definition="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc \
  "select pg_get_viewdef('tide.analytics_task_business_change_v1'::regclass, true)")"
[[ "${restored_business_change_view_definition}" == "${business_change_view_definition}" ]] || {
  echo "0028 回滚未精确恢复原业务变化视图" >&2
  exit 1
}
run_sql "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.down.sql"
run_sql "${DB_DIR}/migrations/0026_kuozhi_course_syncs.down.sql"
run_sql "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.down.sql"
run_sql "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.down.sql"
run_sql "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.down.sql"
run_sql "${DB_DIR}/migrations/0022_performance_job_leases.down.sql"
run_sql "${DB_DIR}/migrations/0021_teacher_support_tickets.down.sql"
run_sql "${DB_DIR}/migrations/0020_product_analytics.down.sql"
run_sql "${DB_DIR}/migrations/0019_growth_stage_notification_state.down.sql"
run_sql "${DB_DIR}/migrations/0018_remove_task_assignment_teacher_response.down.sql"
run_sql "${DB_DIR}/migrations/0017_task_assignment_teacher_response.down.sql"
run_sql "${DB_DIR}/migrations/0016_database_quiz_banks.down.sql"
run_sql "${DB_DIR}/migrations/0015_teacher_photo_filter_strength.down.sql"
run_sql "${DB_DIR}/migrations/0014_teacher_photo_processing.down.sql"
"${ADMIN_PSQL[@]}" -d "${TEST_DB}" -c "DELETE FROM tide.task_validation_rules WHERE execution_version_id IN (SELECT id FROM tide.task_execution_versions WHERE task_code !~ '^G(0[1-9]|10)$'); DELETE FROM tide.task_step_definitions WHERE execution_version_id IN (SELECT id FROM tide.task_execution_versions WHERE task_code !~ '^G(0[1-9]|10)$'); DELETE FROM tide.task_execution_versions WHERE task_code !~ '^G(0[1-9]|10)$'; DELETE FROM public.task_templates WHERE template_id !~ '^G(0[1-9]|10)$';" >/dev/null
run_sql "${DB_DIR}/migrations/0013_system_notification_owner_maintenance.down.sql"
run_sql "${DB_DIR}/migrations/0012_system_notification_publication_guards.down.sql"
run_sql "${DB_DIR}/migrations/0011_system_notification_delivery.down.sql"
run_sql "${DB_DIR}/migrations/0010_current_task_execution.down.sql"
run_sql "${DB_DIR}/migrations/0009_task_view_command.down.sql"
run_sql "${DB_DIR}/migrations/0008_remove_legacy_task_exchange.down.sql"
run_sql "${DB_DIR}/migrations/0007_shared_task_assignment_links.down.sql"
run_sql "${DB_DIR}/migrations/0006_teacher_profile_g01_support.down.sql"
run_sql "${DB_DIR}/migrations/0005_faq_message_commands.down.sql"
run_sql "${DB_DIR}/migrations/0004_task_command_receipts.down.sql"
run_sql "${DB_DIR}/migrations/0003_file_upload_intents.down.sql"
run_sql "${DB_DIR}/migrations/0002_shared_database_exchange.down.sql"
run_sql "${DB_DIR}/migrations/0001_initial.down.sql"

schema_count="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "select count(*) from information_schema.schemata where schema_name = 'tide'")"
[[ "${schema_count}" == "0" ]] || {
  echo "回滚后 tide Schema 仍存在" >&2
  exit 1
}

echo "空库升级至 0037，验证 G01 TESOL-only 与 G04 两段结构的 forward-only down 后逐级回滚通过。"
