#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="/Users/ai-efficient-center/.tide-internal/backend"
BACKEND_SERVICE="gui/$(id -u)/com.aiec.tide-internal-backend"
COMPANY_TEST_ENV="${SOURCE_DIR}/database/.env.company-test"

if [[ ! -f "${COMPANY_TEST_ENV}" ]]; then
  echo "缺少公司测试库配置：${COMPANY_TEST_ENV}" >&2
  exit 1
fi

if [[ "$(stat -f '%Lp' "${COMPANY_TEST_ENV}")" != "600" ]]; then
  echo "公司测试库配置权限必须为 600：${COMPANY_TEST_ENV}" >&2
  exit 1
fi

mkdir -p "${DEPLOY_DIR}/storage/private"

rsync -a --delete \
  --exclude '.git/' \
  --exclude 'coverage/' \
  --exclude 'storage/private/' \
  "${SOURCE_DIR}/" \
  "${DEPLOY_DIR}/"

chmod -R go-rwx /Users/ai-efficient-center/.tide-internal

if launchctl print "${BACKEND_SERVICE}" >/dev/null 2>&1; then
  launchctl kickstart -k "${BACKEND_SERVICE}"
fi

printf 'Internal test backend deployed to %s\n' "${DEPLOY_DIR}"
