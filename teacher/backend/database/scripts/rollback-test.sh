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
ADMIN_PSQL=(psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "${TIDE_DB_PORT}" -U "${TIDE_DB_USER}")
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
run_sql "${DB_DIR}/migrations/0021_teacher_support_tickets.up.sql"
run_sql "${DB_DIR}/migrations/0022_performance_job_leases.up.sql"
run_sql "${DB_DIR}/migrations/0023_teacher_support_operator_atomicity.up.sql"
run_sql "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.up.sql"
run_sql "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.up.sql"
run_sql "${DB_DIR}/migrations/0026_kuozhi_course_syncs.up.sql"
run_sql "${DB_DIR}/migrations/0027_remove_local_quiz_runtime.up.sql"
run_sql "${DB_DIR}/seed/0002_mock_shiwen_views.sql"
run_sql "${DB_DIR}/seed/0004_mock_faq_knowledge.sql"
TIDE_DB_NAME="${TEST_DB}" pnpm --dir "${DB_DIR}/.." exec ts-node scripts/sync-current-task-catalog.ts >/dev/null
run_sql "${DB_DIR}/scripts/grant-tit-teacher-crud.sql"

final_state="$("${ADMIN_PSQL[@]}" -d "${TEST_DB}" -Atqc "
  select concat_ws('|',
    to_regclass('public.task_assignments') is not null,
    to_regclass('tide.task_execution_versions') is not null,
    to_regclass('tide.app_events') is not null,
    to_regclass('tide.teacher_photo_runs') is not null,
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
    to_regclass('tide.analytics_task_funnel_v1') is not null,
    to_regclass('public.teacher_support_tickets') is not null,
    to_regclass('tide.job_leases') is not null,
    to_regclass('tide.teacher_tasks') is null,
    (select count(*) from public.task_assignments where teacher_id = 'MOCK-TEACHER-001'),
    (select count(*) from tide.task_execution_versions),
    (select count(*) from public.task_templates where status = 'PUBLISHED')
  )
")"
[[ "${final_state}" == "t|t|t|t|t|t|t|t|t|t|t|t|t|9|15|15" ]] || {
  echo "空库升级后状态异常: ${final_state}" >&2
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

echo "空库升级至 0027 并逐级回滚验证通过。"
