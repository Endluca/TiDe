#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.score_policy_baseline import reset_score_policy_v1  # noqa: E402
from scripts.migrate_test_database import _database_url  # noqa: E402


LOCAL_APP_ENVS = frozenset({"local", "dev", "development", "test"})


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse score-policy history to the approved v1 baseline and "
            "rebuild current mandatory-task scores."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the reset; without this flag the transaction is rolled back",
    )
    parser.add_argument(
        "--test-database",
        action="store_true",
        help="resolve the shared test-database owner URL from macOS Keychain",
    )
    args = parser.parse_args()

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env not in LOCAL_APP_ENVS:
        raise RuntimeError(
            "Refusing to reset score policy outside local/dev/test environments."
        )
    if args.test_database:
        url = _database_url()
    else:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError(
                "DATABASE_URL is required unless --test-database is used"
            )

    engine = create_engine(url, future=True, pool_pre_ping=True)
    with Session(engine, expire_on_commit=False) as session:
        result = reset_score_policy_v1(session)
        if args.apply:
            session.commit()
            result["committed"] = True
        else:
            session.rollback()
            result["committed"] = False
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
