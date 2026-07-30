from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_service import seed_default_configs


def main() -> int:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env not in {"local", "dev", "development", "test"}:
        print(
            "Refusing to seed default configs: set APP_ENV=local for an explicit local seed.",
            file=sys.stderr,
        )
        return 2
    result = seed_default_configs()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
