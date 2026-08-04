#!/bin/sh

set -eu

/opt/venv/bin/python - <<'PY'
import os
import urllib.request


def check(url, host=None):
    headers = {"Host": host} if host else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read(256)


teacher_host = os.environ.get("TIDE_TEACHER_HOST", "").strip()
if not teacher_host or "://" in teacher_host or "/" in teacher_host:
    raise RuntimeError("TIDE_TEACHER_HOST must be a hostname without scheme or path")


check(
    "http://127.0.0.1:8010/api/health",
    os.environ.get("TIT_HEALTHCHECK_HOST", "127.0.0.1"),
)
check(
    "http://127.0.0.1:8080/healthz",
    teacher_host,
)
check(
    "http://127.0.0.1:8080/health/ready",
    teacher_host,
)
PY

cd /app/operations
exec /opt/venv/bin/python scripts/settle_shared_task_scores.py \
  --healthcheck \
  --heartbeat-path /tmp/tit-score-worker-heartbeat \
  --max-heartbeat-age-seconds 90
