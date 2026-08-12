#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$(cd "${DB_DIR}/.." && pwd)"
ENV_FILE="${1:-}"
APPLY_FLAG="${2:-}"
APPROVED_TEST_DB_HOST="ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
APPROVED_TEST_DB_PORT="5432"
APPROVED_TEST_DB_NAME="tit_growth_test_v2"
APPROVED_TEST_DB_OWNER="postgres"

if [[ -z "${ENV_FILE}" || "${APPLY_FLAG}" != "--apply" ]]; then
  echo "用法：publish-company-test-g03.sh /Git工作区外/company-test.env --apply" >&2
  exit 1
fi
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
    TIDE_ADMIN_DB_HOST|TIDE_ADMIN_DB_PORT|TIDE_ADMIN_DB_USER|TIDE_ADMIN_DB_NAME|TIDE_ADMIN_DB_PASSWORD)
      export "${key}=${value}"
      ;;
  esac
done < "${ENV_FILE}"

for variable_name in \
  TIDE_ADMIN_DB_HOST \
  TIDE_ADMIN_DB_PORT \
  TIDE_ADMIN_DB_USER \
  TIDE_ADMIN_DB_NAME \
  TIDE_ADMIN_DB_PASSWORD; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "配置项 ${variable_name} 不能为空。" >&2
    exit 1
  fi
done

if [[ "${TIDE_ADMIN_DB_HOST}" != "${APPROVED_TEST_DB_HOST}" \
      || "${TIDE_ADMIN_DB_PORT}" != "${APPROVED_TEST_DB_PORT}" \
      || "${TIDE_ADMIN_DB_NAME}" != "${APPROVED_TEST_DB_NAME}" \
      || "${TIDE_ADMIN_DB_USER}" != "${APPROVED_TEST_DB_OWNER}" ]]; then
  echo "G03 发布器只允许连接已批准的 tit_growth_test_v2 测试目标。" >&2
  exit 1
fi

TIDE_DB_HOST="${TIDE_ADMIN_DB_HOST}" \
TIDE_DB_PORT="${TIDE_ADMIN_DB_PORT}" \
TIDE_DB_USER="${TIDE_ADMIN_DB_USER}" \
TIDE_DB_PASSWORD="${TIDE_ADMIN_DB_PASSWORD}" \
TIDE_DB_NAME="${TIDE_ADMIN_DB_NAME}" \
TASK_CATALOG_PUBLIC_WRITE=true \
TASK_CATALOG_TASK_CODES=G03 \
pnpm --dir "${BACKEND_DIR}" exec ts-node scripts/sync-current-task-catalog.ts

echo "G03 shared template and execution config were published to tit_growth_test_v2."
