#!/bin/sh

set -eu

if [ "${TIT_PROCESS_PROFILE:-application}" = "dts-ingest" ]; then
  cd /app/operations
  exec /opt/venv/bin/python scripts/run_dts_ingest.py \
    --healthcheck \
    --heartbeat-path /tmp/tit-dts-ingest-heartbeat \
    --readiness-path /tmp/tit-dts-ingest-readiness \
    --max-heartbeat-age-seconds 90
fi

source_wide_enabled="$(/app/bin/source-wide-enabled.sh)"

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
/opt/venv/bin/python scripts/settle_shared_task_scores.py \
  --healthcheck \
  --heartbeat-path /tmp/tit-score-worker-heartbeat \
  --max-heartbeat-age-seconds 90

if [ "${source_wide_enabled}" = "false" ]; then
  printf '%s\n' \
    'SourceWide healthcheck intentionally skipped: TIT_SOURCE_WIDE_ENABLED=false' >&2
  exit 0
fi

exec /opt/venv/bin/python scripts/run_source_wide_worker.py \
  --healthcheck \
  --heartbeat-path /tmp/tit-source-worker-heartbeat \
  --readiness-path /tmp/tit-source-worker-readiness \
  --max-heartbeat-age-seconds 90 \
  --max-readiness-age-seconds 90
