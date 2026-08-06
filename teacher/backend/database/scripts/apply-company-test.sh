#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-${DB_DIR}/.env.company-test}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少公司测试库配置：${ENV_FILE}" >&2
  exit 1
fi

while IFS='=' read -r key value; do
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
    and to_regclass('public.teacher_metric_snapshots') is not null
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

tide_schema_exists="$("${ADMIN_PSQL[@]}" -Atqc "select to_regnamespace('tide') is not null")"

migration_files=(
  "${DB_DIR}/migrations/0001_initial.up.sql"
  "${DB_DIR}/migrations/0002_shared_database_exchange.up.sql"
  "${DB_DIR}/migrations/0003_file_upload_intents.up.sql"
  "${DB_DIR}/migrations/0004_task_command_receipts.up.sql"
  "${DB_DIR}/migrations/0005_faq_message_commands.up.sql"
  "${DB_DIR}/migrations/0006_teacher_profile_g01_support.up.sql"
  "${DB_DIR}/migrations/0007_shared_task_assignment_links.up.sql"
  "${DB_DIR}/migrations/0008_remove_legacy_task_exchange.up.sql"
  "${DB_DIR}/migrations/0009_task_view_command.up.sql"
  "${DB_DIR}/migrations/0010_current_task_execution.up.sql"
  "${DB_DIR}/migrations/0011_system_notification_delivery.up.sql"
  "${DB_DIR}/migrations/0012_system_notification_publication_guards.up.sql"
  "${DB_DIR}/migrations/0013_system_notification_owner_maintenance.up.sql"
  "${DB_DIR}/migrations/0014_teacher_photo_processing.up.sql"
  "${DB_DIR}/migrations/0015_teacher_photo_filter_strength.up.sql"
  "${DB_DIR}/migrations/0016_database_quiz_banks.up.sql"
  "${DB_DIR}/migrations/0017_task_assignment_teacher_response.up.sql"
  "${DB_DIR}/migrations/0018_remove_task_assignment_teacher_response.up.sql"
  "${DB_DIR}/migrations/0019_growth_stage_notification_state.up.sql"
  "${DB_DIR}/migrations/0020_product_analytics.up.sql"
  "${DB_DIR}/migrations/0021_teacher_support_tickets.up.sql"
  "${DB_DIR}/migrations/0022_performance_job_leases.up.sql"
  "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.up.sql"
  "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.up.sql"
  "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.up.sql"
  "${DB_DIR}/migrations/0026_kuozhi_course_syncs.up.sql"
  "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.up.sql"
)

if [[ "${tide_schema_exists}" == "t" ]]; then
  migration_ready="$("${ADMIN_PSQL[@]}" -Atqc "
    select
      to_regclass('tide.user_accounts') is not null
      and to_regclass('tide.task_execution_versions') is not null
      and to_regclass('tide.app_events') is not null
      and to_regclass('tide.teacher_tasks') is null
  ")"
  if [[ "${migration_ready}" != "t" ]]; then
    echo "tide Schema 处于未知或不完整状态，初始化已停止。" >&2
    exit 1
  fi
else
  {
    printf 'BEGIN;\n'
    awk '$0 != "BEGIN;" && $0 != "COMMIT;"' "${migration_files[@]}"
    printf 'COMMIT;\n'
  } | "${ADMIN_PSQL[@]}" --quiet
fi

system_notification_delivery_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('tide.system_notification_publications') is not null
")"
if [[ "${system_notification_delivery_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0011_system_notification_delivery.up.sql"
fi

system_notification_guards_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select position(
    'recipient_count IS DISTINCT' in
    pg_get_functiondef('tide.protect_system_notification_publication()'::regprocedure)
  ) > 0
")"
if [[ "${system_notification_guards_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0012_system_notification_publication_guards.up.sql"
fi

system_notification_owner_maintenance_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select position(
    'RETURN OLD' in
    pg_get_functiondef('tide.protect_system_notification_content()'::regprocedure)
  ) > 0
")"
if [[ "${system_notification_owner_maintenance_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0013_system_notification_owner_maintenance.up.sql"
fi

teacher_photo_processing_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('tide.teacher_photo_runs') is not null
")"
if [[ "${teacher_photo_processing_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0014_teacher_photo_processing.up.sql"
fi

teacher_photo_full_strength_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select position(
    '1.000' in pg_get_constraintdef(oid)
  ) > 0
  from pg_constraint
  where conrelid = 'tide.teacher_photo_runs'::regclass
    and conname = 'teacher_photo_runs_strength_check'
")"
if [[ "${teacher_photo_full_strength_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0015_teacher_photo_filter_strength.up.sql"
fi

assignment_teacher_response_column_count="$("${ADMIN_PSQL[@]}" -Atqc "
  select count(*)
  from information_schema.columns
  where table_schema = 'public'
    and table_name = 'task_assignments'
    and column_name like 'teacher_response_%'
")"
if [[ "${assignment_teacher_response_column_count}" == "4" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0018_remove_task_assignment_teacher_response.up.sql"
elif [[ "${assignment_teacher_response_column_count}" != "0" ]]; then
  echo "共享任务事实说明字段处于不完整状态，删除已停止。" >&2
  exit 1
fi

growth_stage_notification_state_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('tide.growth_stage_notification_states') is not null
")"
if [[ "${growth_stage_notification_state_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0019_growth_stage_notification_state.up.sql"
fi

product_analytics_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('tide.analytics_task_funnel_v1') is not null
")"
if [[ "${product_analytics_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0020_product_analytics.up.sql"
fi

teacher_support_tickets_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('public.teacher_support_tickets') is not null
")"
if [[ "${teacher_support_tickets_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0021_teacher_support_tickets.up.sql"
fi

performance_job_leases_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select
    to_regclass('tide.job_leases') is not null
    and exists (
      select 1
      from information_schema.columns
      where table_schema = 'tide'
        and table_name = 'teacher_photo_runs'
        and column_name = 'processing_owner'
    )
")"
if [[ "${performance_job_leases_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0022_performance_job_leases.up.sql"
fi

operator_reply_atomicity_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  select position(
    'teacher_reply_deadline_at = reply_at + interval ''48 hours''' in
    pg_get_functiondef(
      'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
    )
  ) > 0
")"
if [[ "${operator_reply_atomicity_ready}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.up.sql"
fi

support_ticket_security_hardened="$("${ADMIN_PSQL[@]}" -Atqc "
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
if [[ "${support_ticket_security_hardened}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet -f "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.up.sql"
fi

"${ADMIN_PSQL[@]}" --quiet \
  -f "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.up.sql"

kuozhi_course_syncs_exists="$("${ADMIN_PSQL[@]}" -Atqc "
  select to_regclass('tide.kuozhi_course_syncs') is not null
")"
if [[ "${kuozhi_course_syncs_exists}" != "t" ]]; then
  "${ADMIN_PSQL[@]}" --quiet \
    -f "${DB_DIR}/migrations/0026_kuozhi_course_syncs.up.sql"
else
  kuozhi_course_syncs_complete="$("${ADMIN_PSQL[@]}" -Atqc "
    select
      (
        select count(*) = 11
        from information_schema.columns
        where table_schema = 'tide'
          and table_name = 'kuozhi_course_syncs'
          and column_name in (
            'id', 'account_id', 'task_assignment_id', 'idempotency_key',
            'command_id', 'request_hash', 'mapping_version', 'sync_status',
            'completion_decision', 'response_body', 'created_at'
          )
      )
      and exists (
        select 1
        from pg_constraint
        where conrelid = 'tide.kuozhi_course_syncs'::regclass
          and conname = 'kuozhi_course_syncs_real_mode_check'
      )
      and to_regclass('tide.kuozhi_course_syncs_assignment_time_idx') is not null
  ")"
  if [[ "${kuozhi_course_syncs_complete}" != "t" ]]; then
    echo "现有 tide.kuozhi_course_syncs 结构不完整，迁移已停止。" >&2
    exit 1
  fi
fi

"${ADMIN_PSQL[@]}" --quiet \
  -f "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.up.sql"

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

"${ADMIN_PSQL[@]}" --quiet -v app_password="${TIDE_APP_DB_PASSWORD}" <<'SQL'
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
    to_regclass('tide.analytics_task_funnel_v1') is not null,
    to_regclass('public.teacher_support_tickets') is not null,
    to_regclass('tide.teacher_photo_runs') is not null,
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
    has_table_privilege(current_user, 'public.lesson_facts', 'SELECT'),
    has_table_privilege(current_user, 'public.lesson_dimension_scores', 'SELECT'),
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
      and to_regclass('tide.teacher_photo_runs_pending_claim_idx') is not null
      and (
        select count(*) = 4
        from information_schema.columns
        where table_schema = 'tide'
          and table_name = 'teacher_photo_runs'
          and column_name in (
            'processing_owner',
            'lease_expires_at',
            'attempt_count',
            'next_attempt_at'
          )
      )
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
      and (
        select count(*) = 10
        from (
          values
            ('G01:v1', 'G01', 'PUBLISHED', 'ACTIVE'),
            ('G02:v1', 'G04', 'PUBLISHED', 'ACTIVE'),
            ('G03:v1', 'G02', 'PUBLISHED', 'ACTIVE'),
            ('G04:v1', 'G03', 'PUBLISHED', 'ACTIVE'),
            ('G05:v1', 'G00', 'RETIRED', 'RETIRED'),
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

echo "公司测试库初始化完成：tide Schema、多副本任务租约、0027 本地考试清理、阔知课程同步、固定任务语义与分析视图、教师工单共享表、当前成长任务与个性化任务执行配置、通知状态、共享事实说明字段清理、首课画面处理和应用账号权限均已验证。"
