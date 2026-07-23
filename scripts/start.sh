#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/backend/.env.local}"
RUNTIME_DIR="$ROOT_DIR/.runtime"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到环境文件：$ENV_FILE"
  exit 1
fi
if [[ ! -x "$ROOT_DIR/backend/.venv/bin/uvicorn" ]] || [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "依赖尚未安装，请先运行 ./scripts/setup.sh \"$ENV_FILE\""
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

: "${DATABASE_URL:?环境文件缺少 DATABASE_URL}"
: "${APP_ENV:?环境文件缺少 APP_ENV}"

export TIDE_BACKEND_PORT="${TIDE_BACKEND_PORT:-8010}"
export TIDE_FRONTEND_PORT="${TIDE_FRONTEND_PORT:-5174}"

for port in "$TIDE_BACKEND_PORT" "$TIDE_FRONTEND_PORT"; do
  if command -v lsof >/dev/null && lsof -ti "tcp:$port" >/dev/null 2>&1; then
    echo "端口 $port 已被占用，请先关闭对应服务，或设置 TIDE_BACKEND_PORT / TIDE_FRONTEND_PORT。"
    exit 1
  fi
done

mkdir -p "$RUNTIME_DIR"

cleanup() {
  trap - EXIT INT TERM
  for pid in "${WORKER_PID:-}" "${FRONTEND_PID:-}" "${BACKEND_PID:-}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup EXIT INT TERM

(
  cd "$ROOT_DIR/backend"
  exec .venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port "$TIDE_BACKEND_PORT" \
    --env-file "$ENV_FILE"
) >"$RUNTIME_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

(
  cd "$ROOT_DIR/backend"
  exec .venv/bin/python scripts/settle_shared_task_scores.py \
    --watch \
    --interval-seconds 3
) >"$RUNTIME_DIR/score-worker.log" 2>&1 &
WORKER_PID=$!

(
  cd "$ROOT_DIR/frontend"
  exec npm run dev -- \
    --host 127.0.0.1 \
    --port "$TIDE_FRONTEND_PORT" \
    --strictPort
) >"$RUNTIME_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo "正在连接测试数据库并加载数据，首次启动可能需要约 1 分钟..."
READY=0
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$TIDE_BACKEND_PORT/api/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    echo "后端启动失败，请查看 $RUNTIME_DIR/backend.log"
    exit 1
  fi
  sleep 1
done

if [[ "$READY" != "1" ]]; then
  echo "后端未在 120 秒内就绪，请查看 $RUNTIME_DIR/backend.log"
  exit 1
fi

echo "TiDe 已启动："
echo "  Web App  http://127.0.0.1:$TIDE_FRONTEND_PORT"
echo "  API      http://127.0.0.1:$TIDE_BACKEND_PORT"
echo "  日志     $RUNTIME_DIR"
echo "按 Ctrl+C 停止全部本地进程。"

while true; do
  for pid in "$BACKEND_PID" "$WORKER_PID" "$FRONTEND_PID"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "有进程意外退出，请查看 $RUNTIME_DIR 下的日志。"
      exit 1
    fi
  done
  sleep 2
done
