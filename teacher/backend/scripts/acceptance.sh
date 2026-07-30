#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATABASE_ENV="${BACKEND_DIR}/database/.env"

if [[ ! -f "${DATABASE_ENV}" ]]; then
  echo "缺少 ${DATABASE_ENV}，请先复制 database/.env.example 并设置本地密码。" >&2
  exit 1
fi

cd "${BACKEND_DIR}"
set -a
source "${DATABASE_ENV}"
set +a

export NODE_ENV=test
export RUN_DATABASE_INTEGRATION=true
export COMPANY_TEST_DATABASE_ENABLED=false
export TEST_DATABASE_ADMIN_URL="postgresql://${TIDE_DB_USER}:${TIDE_DB_PASSWORD}@127.0.0.1:${TIDE_DB_PORT}/${TIDE_DB_NAME}"
export TIDE_DATABASE_URL="postgresql://tit_teacher_crud:${TIDE_DB_PASSWORD}@127.0.0.1:${TIDE_DB_PORT}/${TIDE_DB_NAME}"
export SHIWEN_READ_DATABASE_URL="${TEST_DATABASE_ADMIN_URL}"
export SHIWEN_TEACHER_IDENTITY_VIEW=tide.test_shiwen_teacher_identity_v1

bash database/scripts/apply.sh
pnpm lint
pnpm build
pnpm test --runInBand
pnpm test:e2e --runInBand
ruby contracts/verify-contracts.rb
bash database/scripts/verify.sh
bash database/scripts/rollback-test.sh
bash "${BACKEND_DIR}/../docs/接口与数据/check_prd_interface_sync.sh"

echo "后端本地全链路验收通过。"
