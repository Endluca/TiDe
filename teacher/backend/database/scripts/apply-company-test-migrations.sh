#!/usr/bin/env bash
set -euo pipefail
set +x

umask 077

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL_MIGRATOR="${DB_DIR}/scripts/apply-production.sh"

APPROVED_TEST_DB_HOST="ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
APPROVED_TEST_DB_PORT="5432"
APPROVED_TEST_DB_NAME="tit_growth_test_v2"
APPROVED_TEST_DB_OWNER="postgres"
APPROVED_TEST_DB_SSLMODE="disable"
EXPECTED_PUBLIC_HEAD="20260811_57_g02_document"
EXPECTED_TEACHER_START_HEAD="0038_personalized_environment_photo"
APPROVED_TEACHER_TARGET="0040_g02_document_read_status"

BASELINE_TIDE_MIGRATIONS=(
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

APPROVED_TIDE_MIGRATIONS=(
  "${BASELINE_TIDE_MIGRATIONS[@]}"
  0039_g02_policy_document
  0040_g02_document_read_status
)

fail() {
  echo "$1" >&2
  exit 1
}

cleanup() {
  unset PGPASSWORD TIDE_ADMIN_DB_PASSWORD
}
trap cleanup EXIT

if [[ "$#" -ne 1 ]]; then
  fail "用法：apply-company-test-migrations.sh /Git工作区外/company-test.env"
fi
if [[ "${TIDE_MIGRATION_TEST_MODE:-false}" != "false" ]]; then
  fail "公司 TEST 增量迁移禁止使用通用 TIDE_MIGRATION_TEST_MODE 绕过。"
fi
if [[ -n "${TIDE_MIGRATION_DATABASE_URL:-}" ]]; then
  fail "公司 TEST 增量迁移禁止使用连接 URI；请提供受控配置文件。"
fi

ENV_FILE="$1"
if [[ "${ENV_FILE}" != /* || ! -f "${ENV_FILE}" || -L "${ENV_FILE}" ]]; then
  fail "公司 TEST 增量迁移只接受绝对路径的普通配置文件（禁止符号链接）。"
fi
if stat -f '%Lp' "${ENV_FILE}" >/dev/null 2>&1; then
  env_file_mode="$(stat -f '%Lp' "${ENV_FILE}")"
else
  env_file_mode="$(stat -c '%a' "${ENV_FILE}")"
fi
if [[ "${env_file_mode}" != "600" ]]; then
  fail "公司 TEST 增量迁移配置权限必须精确为 600。"
fi

env_file_dir="$(cd "$(dirname "${ENV_FILE}")" && pwd -P)"
ENV_FILE="${env_file_dir}/$(basename "${ENV_FILE}")"
source_root_candidate="$(cd "${DB_DIR}/../../.." && pwd -P)"
if [[ -e "${source_root_candidate}/.git" ]]; then
  protected_source_root="${source_root_candidate}"
else
  protected_source_root="$(cd "${DB_DIR}/.." && pwd -P)"
fi
case "${ENV_FILE}" in
  "${protected_source_root}"|"${protected_source_root}"/*)
    fail "公司 TEST 增量迁移配置必须放在 Git/镜像工作区之外。"
    ;;
esac

config_keys=(
  TIDE_ADMIN_DB_HOST
  TIDE_ADMIN_DB_PORT
  TIDE_ADMIN_DB_USER
  TIDE_ADMIN_DB_NAME
  TIDE_ADMIN_DB_PASSWORD
  TIDE_ADMIN_DB_SSLMODE
)
for config_key in "${config_keys[@]}"; do
  unset "${config_key}"
done
seen_config_keys="|"
while IFS= read -r config_line || [[ -n "${config_line}" ]]; do
  config_line="${config_line%$'\r'}"
  if [[ -z "${config_line}" || "${config_line}" =~ ^[[:space:]]*# ]]; then
    continue
  fi
  if [[ ! "${config_line}" =~ ^([A-Z0-9_]+)=(.*)$ ]]; then
    fail "公司 TEST 增量迁移配置含非法行；只接受 KEY=VALUE。"
  fi
  config_key="${BASH_REMATCH[1]}"
  config_value="${BASH_REMATCH[2]}"
  case "${config_key}" in
    TIDE_ADMIN_DB_HOST|TIDE_ADMIN_DB_PORT|TIDE_ADMIN_DB_USER|TIDE_ADMIN_DB_NAME|TIDE_ADMIN_DB_PASSWORD|TIDE_ADMIN_DB_SSLMODE)
      ;;
    *)
      fail "公司 TEST 增量迁移配置含未批准字段：${config_key}。"
      ;;
  esac
  if [[ "${seen_config_keys}" == *"|${config_key}|"* ]]; then
    fail "公司 TEST 增量迁移配置字段重复：${config_key}。"
  fi
  seen_config_keys+="${config_key}|"
  printf -v "${config_key}" '%s' "${config_value}"
done < "${ENV_FILE}"

for config_key in "${config_keys[@]}"; do
  if [[ -z "${!config_key:-}" ]]; then
    fail "公司 TEST 增量迁移配置项 ${config_key} 不能为空。"
  fi
done

if [[ "${TIDE_ADMIN_DB_HOST}" != "${APPROVED_TEST_DB_HOST}" \
      || "${TIDE_ADMIN_DB_PORT}" != "${APPROVED_TEST_DB_PORT}" \
      || "${TIDE_ADMIN_DB_NAME}" != "${APPROVED_TEST_DB_NAME}" \
      || "${TIDE_ADMIN_DB_USER}" != "${APPROVED_TEST_DB_OWNER}" ]]; then
  fail "公司 TEST 增量迁移只允许连接已批准的 tit_growth_test_v2 测试目标。"
fi
if [[ "${TIDE_ADMIN_DB_SSLMODE}" != "${APPROVED_TEST_DB_SSLMODE}" ]]; then
  fail "sslmode=disable 只允许用于已批准的 tit_growth_test_v2 测试目标。"
fi
if [[ ! -x "${CANONICAL_MIGRATOR}" ]]; then
  fail "缺少可执行 canonical 迁移器：apply-production.sh。"
fi

canonical_target_chain="$(awk -v target="${APPROVED_TEACHER_TARGET}" '
  $0 == "PRODUCTION_MIGRATIONS=(" { in_array = 1; next }
  in_array && $0 == ")" { exit }
  in_array {
    line = $0
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)
    if (line ~ /^[0-9][0-9][0-9][0-9]_[a-z0-9_]+$/) {
      print line
      if (line == target) exit
    }
  }
' "${CANONICAL_MIGRATOR}")"
approved_target_chain="$(printf '%s\n' "${APPROVED_TIDE_MIGRATIONS[@]}")"
if [[ "${canonical_target_chain}" != "${approved_target_chain}" ]]; then
  fail "canonical 迁移器到 0040 的链路不再是批准的 0038 + G02 0039/0040，已停止。"
fi

sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
  else
    shasum -a 256 "${file_path}" | awk '{print $1}'
  fi
}

ledger_manifest_for() {
  local migration_order=0
  local migration_id
  local migration_file
  local migration_sha256
  local manifest=""
  for migration_id in "$@"; do
    migration_order=$((migration_order + 1))
    migration_file="${DB_DIR}/migrations/${migration_id}.up.sql"
    if [[ ! -f "${migration_file}" ]]; then
      fail "缺少批准的 canonical Tide 迁移文件：${migration_id}.up.sql。"
    fi
    if [[ "$(sed -n '1p' "${migration_file}")" != "BEGIN;" \
          || "$(tail -n 1 "${migration_file}")" != "COMMIT;" ]]; then
      fail "canonical Tide 迁移文件不是单一事务：${migration_id}.up.sql。"
    fi
    migration_sha256="$(sha256_file "${migration_file}")"
    if [[ -n "${manifest}" ]]; then
      manifest+=$'\n'
    fi
    manifest+="${migration_order}|${migration_id}|${migration_id}.up.sql|${migration_sha256}"
  done
  printf '%s' "${manifest}"
}

expected_baseline_manifest="$(ledger_manifest_for "${BASELINE_TIDE_MIGRATIONS[@]}")"
expected_target_manifest="$(ledger_manifest_for "${APPROVED_TIDE_MIGRATIONS[@]}")"

export PGPASSWORD="${TIDE_ADMIN_DB_PASSWORD}"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-8}"
export PGSSLMODE="${APPROVED_TEST_DB_SSLMODE}"
ADMIN_PSQL=(
  psql -X --no-password -v ON_ERROR_STOP=1
  -h "${APPROVED_TEST_DB_HOST}"
  -p "${APPROVED_TEST_DB_PORT}"
  -U "${APPROVED_TEST_DB_OWNER}"
  -d "${APPROVED_TEST_DB_NAME}"
)

connected_identity="$("${ADMIN_PSQL[@]}" -AtF '|' <<'SQL'
SELECT
    current_database(),
    current_user,
    session_user,
    current_setting('server_version_num')::integer >= 160000,
    COALESCE(
        (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
        false
    ),
    (SELECT rolsuper FROM pg_roles WHERE rolname = current_user);
SQL
)"
if [[ "${connected_identity}" != "${APPROVED_TEST_DB_NAME}|${APPROVED_TEST_DB_OWNER}|${APPROVED_TEST_DB_OWNER}|t|f|t" ]]; then
  fail "公司 TEST 实际数据库、owner、PostgreSQL 版本或非 TLS 会话与批准目标不一致。"
fi

public_head="$("${ADMIN_PSQL[@]}" -Atqc "
  SELECT CASE WHEN count(*) = 1 THEN min(version_num) ELSE '' END
  FROM public.alembic_version
")"
if [[ "${public_head}" != "${EXPECTED_PUBLIC_HEAD}" ]]; then
  fail "公司 TEST public Head 必须精确为 ${EXPECTED_PUBLIC_HEAD}，当前为 ${public_head:-未记账}；未执行 teacher 写入。"
fi

if [[ "$("${ADMIN_PSQL[@]}" -Atqc "SELECT to_regclass('tide.schema_migrations') IS NOT NULL")" != "t" ]]; then
  fail "公司 TEST 缺少 canonical Tide 迁移账本；未执行 teacher 写入。"
fi
teacher_head="$("${ADMIN_PSQL[@]}" -Atqc "
  SELECT COALESCE(
    (SELECT migration_id FROM tide.schema_migrations ORDER BY migration_order DESC LIMIT 1),
    ''
  )
")"
if [[ "${teacher_head}" != "${EXPECTED_TEACHER_START_HEAD}" ]]; then
  fail "公司 TEST teacher Head 必须精确从 ${EXPECTED_TEACHER_START_HEAD} 开始，当前为 ${teacher_head:-未记账}；未执行写入。"
fi
actual_baseline_manifest="$("${ADMIN_PSQL[@]}" -AtF '|' -c "
  SELECT migration_order, migration_id, filename, sha256
  FROM tide.schema_migrations
  ORDER BY migration_order
")"
if [[ "${actual_baseline_manifest}" != "${expected_baseline_manifest}" ]]; then
  fail "公司 TEST teacher 0038 账本的顺序、文件名或 SHA-256 不是精确 canonical；未执行写入。"
fi

TIDE_COMPANY_TEST_MIGRATION_MODE=true \
TIDE_COMPANY_TEST_CONFIG_FILE="${ENV_FILE}" \
TIDE_COMPANY_TEST_DB_HOST="${APPROVED_TEST_DB_HOST}" \
TIDE_COMPANY_TEST_DB_PORT="${APPROVED_TEST_DB_PORT}" \
TIDE_COMPANY_TEST_DB_USER="${APPROVED_TEST_DB_OWNER}" \
TIDE_COMPANY_TEST_DB_NAME="${APPROVED_TEST_DB_NAME}" \
TIDE_COMPANY_TEST_DB_SSLMODE="${APPROVED_TEST_DB_SSLMODE}" \
TIDE_MIGRATION_TEST_MODE=false \
TIDE_MIGRATION_DATABASE_URL= \
TIDE_MIGRATION_EXPECTED_DATABASE="${APPROVED_TEST_DB_NAME}" \
TIDE_MIGRATION_TARGET="${APPROVED_TEACHER_TARGET}" \
  bash "${CANONICAL_MIGRATOR}"

post_public_head="$("${ADMIN_PSQL[@]}" -Atqc "
  SELECT CASE WHEN count(*) = 1 THEN min(version_num) ELSE '' END
  FROM public.alembic_version
")"
if [[ "${post_public_head}" != "${EXPECTED_PUBLIC_HEAD}" ]]; then
  fail "teacher 增量迁移后 public Head 发生变化，验证失败。"
fi
post_teacher_head="$("${ADMIN_PSQL[@]}" -Atqc "
  SELECT COALESCE(
    (SELECT migration_id FROM tide.schema_migrations ORDER BY migration_order DESC LIMIT 1),
    ''
  )
")"
if [[ "${post_teacher_head}" != "${APPROVED_TEACHER_TARGET}" ]]; then
  fail "teacher 增量迁移后 Head 不是 ${APPROVED_TEACHER_TARGET}，验证失败。"
fi
actual_target_manifest="$("${ADMIN_PSQL[@]}" -AtF '|' -c "
  SELECT migration_order, migration_id, filename, sha256
  FROM tide.schema_migrations
  ORDER BY migration_order
")"
if [[ "${actual_target_manifest}" != "${expected_target_manifest}" ]]; then
  fail "teacher 增量迁移后账本不是精确 canonical 0040，验证失败。"
fi
g02_read_status_ready="$("${ADMIN_PSQL[@]}" -Atqc "
  SELECT
    EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = 'tide'
        AND table_name = 'task_step_progress'
        AND column_name = 'reached_end'
        AND data_type = 'boolean'
        AND is_nullable = 'NO'
        AND column_default IN ('false', 'false::boolean')
    )
    AND EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conrelid = 'tide.task_step_progress'::regclass
        AND conname = 'task_step_progress_g02_read_status_check'
        AND contype = 'c'
        AND convalidated
    )
    AND EXISTS (
      SELECT 1
      FROM pg_trigger
      WHERE tgrelid = 'tide.task_step_progress'::regclass
        AND tgname = 'task_step_progress_g02_assignment_completion_check'
        AND NOT tgisinternal
        AND tgdeferrable
        AND tginitdeferred
    );
")"
if [[ "${g02_read_status_ready}" != "t" ]]; then
  fail "teacher 0040 已记账但 G02 reached_end 数据库契约未就绪，验证失败。"
fi

echo "公司 TEST teacher 增量迁移已完成并验证：0038 -> 0039 -> 0040；public Head 保持 ${EXPECTED_PUBLIC_HEAD}。"
