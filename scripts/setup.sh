#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/backend/.env.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到环境文件：$ENV_FILE"
  echo "请先把单独收到的 TiDe.env 复制为 backend/.env.local，或把路径作为第一个参数传入。"
  exit 1
fi

command -v python3 >/dev/null || { echo "需要 Python 3.9 或更高版本。"; exit 1; }
command -v npm >/dev/null || { echo "需要 Node.js 18 或更高版本。"; exit 1; }

echo "安装后端依赖..."
python3 -m venv "$ROOT_DIR/backend/.venv"
"$ROOT_DIR/backend/.venv/bin/pip" install -q --upgrade pip
"$ROOT_DIR/backend/.venv/bin/pip" install -q -r "$ROOT_DIR/backend/requirements.txt"

echo "安装前端依赖..."
(cd "$ROOT_DIR/frontend" && npm ci --silent)

echo "准备完成。运行：./scripts/start.sh \"$ENV_FILE\""
