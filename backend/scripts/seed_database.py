from __future__ import annotations

import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import database_health
from app.task_seed import seed_task_catalog


def main() -> int:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env not in {"local", "dev", "development", "test"}:
        print(
            "Refusing to seed local task templates: set APP_ENV=local explicitly.",
            file=sys.stderr,
        )
        return 2
    task_seed = seed_task_catalog()
    health = database_health()
    print(
        "Seeded TIT growth database: "
        f"{task_seed['template_catalog_size']} current task templates, "
        "0 teacher/runtime business facts, "
        f"dialect={health['dialect']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
