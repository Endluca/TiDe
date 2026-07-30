#!/usr/bin/env bash
set -euo pipefail

exec /opt/homebrew/bin/cloudflared tunnel \
  --url http://127.0.0.1:3000 \
  --no-autoupdate
