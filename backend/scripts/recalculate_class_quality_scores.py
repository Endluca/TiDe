from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.teacher_data_import import recalculate_current_class_quality_scores


def _company_test_engine():
    from scripts.migrate_test_database import _database_url

    return create_engine(_database_url(), pool_pre_ping=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild current classroom-quality projections from "
            "teacher_metric_snapshots.perfect_cnt."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Commit the recalculation. Without this flag the command is a "
            "transactionally rolled-back dry run."
        ),
    )
    parser.add_argument(
        "--test-database",
        action="store_true",
        help=(
            "Use the company test PostgreSQL owner credential from macOS "
            "Keychain instead of DATABASE_URL."
        ),
    )
    args = parser.parse_args()

    selected_engine = None
    try:
        selected_engine = _company_test_engine() if args.test_database else None
        result = recalculate_current_class_quality_scores(
            bind=selected_engine,
            dry_run=not args.apply,
        )
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if selected_engine is not None:
            selected_engine.dispose()
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
