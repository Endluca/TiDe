from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime_settings import validate_production_runtime


def _required_int(name: str) -> int:
    return int(os.environ[name])


def main() -> int:
    # This must run before Uvicorn creates worker processes. Validating only
    # while importing app.main is too late to stop an unsafe worker count.
    validate_production_runtime()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8010,
        workers=_required_int("TIT_API_WORKERS"),
        limit_concurrency=_required_int("TIT_API_LIMIT_CONCURRENCY"),
        timeout_keep_alive=_required_int("TIT_API_KEEPALIVE_SECONDS"),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get(
            "TIT_TRUSTED_PROXY_IPS",
            "127.0.0.1",
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
