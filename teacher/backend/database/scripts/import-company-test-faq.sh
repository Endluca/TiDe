#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-${BACKEND_DIR}/database/.env.company-test}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少公司测试库配置：${ENV_FILE}" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

required_variables=(
  TIDE_ADMIN_DB_HOST
  TIDE_ADMIN_DB_PORT
  TIDE_ADMIN_DB_NAME
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
  echo "FAQ 只能使用公司测试库应用账号 tit_teacher_crud 导入。" >&2
  exit 1
fi

export TIDE_DATABASE_URL="$(
  TIDE_FAQ_DB_HOST="${TIDE_ADMIN_DB_HOST}" \
  TIDE_FAQ_DB_PORT="${TIDE_ADMIN_DB_PORT}" \
  TIDE_FAQ_DB_NAME="${TIDE_ADMIN_DB_NAME}" \
  TIDE_FAQ_DB_USER="${TIDE_APP_DB_USER}" \
  TIDE_FAQ_DB_PASSWORD="${TIDE_APP_DB_PASSWORD}" \
  TIDE_FAQ_DB_SSLMODE="${TIDE_ADMIN_DB_SSLMODE:-disable}" \
  node -e '
    const url = new URL("postgresql://localhost");
    url.hostname = process.env.TIDE_FAQ_DB_HOST;
    url.port = process.env.TIDE_FAQ_DB_PORT;
    url.pathname = `/${process.env.TIDE_FAQ_DB_NAME}`;
    url.username = process.env.TIDE_FAQ_DB_USER;
    url.password = process.env.TIDE_FAQ_DB_PASSWORD;
    url.searchParams.set("sslmode", process.env.TIDE_FAQ_DB_SSLMODE);
    process.stdout.write(url.toString());
  '
)"

cd "${BACKEND_DIR}"
pnpm import:faq
unset TIDE_DATABASE_URL
