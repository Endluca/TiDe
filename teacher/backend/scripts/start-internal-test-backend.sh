#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIDE_NVM_DIR="${HOME}/.nvm"
COMPANY_TEST_ENV="${BACKEND_DIR}/database/.env.company-test"

if [[ -s "${TIDE_NVM_DIR}/nvm.sh" ]]; then
  export NVM_DIR="${TIDE_NVM_DIR}"
  # shellcheck disable=SC1091
  source "${TIDE_NVM_DIR}/nvm.sh"
  nvm use --silent default
fi

if [[ ! -f "${COMPANY_TEST_ENV}" ]]; then
  echo "缺少公司测试库配置：${COMPANY_TEST_ENV}" >&2
  exit 1
fi

set -a
source "${COMPANY_TEST_ENV}"
source "${BACKEND_DIR}/.env.local"
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
    echo "公司测试库配置项 ${variable_name} 不能为空。" >&2
    exit 1
  fi
done

if [[ "${TIDE_APP_DB_USER}" != "tit_teacher_crud" ]]; then
  echo "内部测试后端必须使用 tit_teacher_crud。" >&2
  exit 1
fi

COMPANY_TEST_DATABASE_URL="$(
  TIDE_INTERNAL_DB_HOST="${TIDE_ADMIN_DB_HOST}" \
  TIDE_INTERNAL_DB_PORT="${TIDE_ADMIN_DB_PORT}" \
  TIDE_INTERNAL_DB_NAME="${TIDE_ADMIN_DB_NAME}" \
  TIDE_INTERNAL_DB_USER="${TIDE_APP_DB_USER}" \
  TIDE_INTERNAL_DB_PASSWORD="${TIDE_APP_DB_PASSWORD}" \
  TIDE_INTERNAL_DB_SSLMODE="${TIDE_ADMIN_DB_SSLMODE:-disable}" \
  node -e '
    const url = new URL("postgresql://localhost");
    url.hostname = process.env.TIDE_INTERNAL_DB_HOST;
    url.port = process.env.TIDE_INTERNAL_DB_PORT;
    url.pathname = `/${process.env.TIDE_INTERNAL_DB_NAME}`;
    url.username = process.env.TIDE_INTERNAL_DB_USER;
    url.password = process.env.TIDE_INTERNAL_DB_PASSWORD;
    url.searchParams.set("sslmode", process.env.TIDE_INTERNAL_DB_SSLMODE);
    process.stdout.write(url.toString());
  '
)"

export NODE_ENV=development
export PORT="${PORT:-3000}"
export DATABASE_REQUIRED=true
export TIDE_DATABASE_URL="${COMPANY_TEST_DATABASE_URL}"
export SHIWEN_READ_DATABASE_URL="${COMPANY_TEST_DATABASE_URL}"
export SHIWEN_READ_MODE=DIRECT_TABLES
export SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK=false
export CORS_ORIGINS="${TIDE_INTERNAL_CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173}"
export PUBLIC_APP_URL="${TIDE_INTERNAL_PUBLIC_APP_URL:-http://localhost:5173}"
export PUBLIC_API_URL="${TIDE_INTERNAL_PUBLIC_API_URL:-http://127.0.0.1:${PORT}}"
export LOCAL_FILE_STORAGE_DIR="${BACKEND_DIR}/storage/private"

cd "${BACKEND_DIR}"
pnpm build
exec pnpm start
