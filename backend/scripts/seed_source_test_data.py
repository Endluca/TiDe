from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import build_engine  # noqa: E402
from app.source_test_seed import (  # noqa: E402
    ALLOWED_ENVIRONMENTS,
    seed_source_test_data,
)
from scripts import migrate_test_database  # noqa: E402


def _guarded_test_engine():
    expected_database = migrate_test_database._expected_database()
    migrate_test_database._reject_ambient_libpq_environment()
    database_url = migrate_test_database._database_url()
    migrate_test_database._validate_approved_test_identity(database_url)
    migrate_test_database._validate_database_target(
        database_url,
        expected_database,
    )
    return build_engine(database_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or apply the explicit synthetic source-wide seed."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the seed. Without this flag every write is rolled back.",
    )
    parser.add_argument(
        "--test-database",
        action="store_true",
        help=(
            "Resolve the owner credential from Keychain and require the "
            "configured and live database names to match."
        ),
    )
    args = parser.parse_args(argv)

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env not in ALLOWED_ENVIRONMENTS:
        print(
            "Refusing source test seed: APP_ENV must be local/dev/development/test.",
            file=sys.stderr,
        )
        return 2

    if args.apply and not args.test_database:
        print(
            "Refusing source test seed commit: --apply requires "
            "--test-database and the approved TiDe v2 target guard.",
            file=sys.stderr,
        )
        return 2

    engine = _guarded_test_engine() if args.test_database else build_engine()
    try:
        result = seed_source_test_data(engine, apply=args.apply)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
