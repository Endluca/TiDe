#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import engine as default_engine
from app.score_read_model import refresh_persisted_score_read_models


def _company_test_engine():
    from scripts.migrate_test_database import _database_url

    return create_engine(_database_url(), pool_pre_ping=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild persisted teacher dimensions, score components and "
            "per-lesson score results."
        )
    )
    parser.add_argument(
        "--teacher-id",
        action="append",
        dest="teacher_ids",
        help="Only rebuild this teacher; repeat the flag for multiple teachers.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the rebuild. The default is a rolled-back preview.",
    )
    parser.add_argument(
        "--test-database",
        action="store_true",
        help="Use the company test PostgreSQL owner credential from Keychain.",
    )
    args = parser.parse_args()

    selected_engine = _company_test_engine() if args.test_database else default_engine
    try:
        with Session(selected_engine) as session:
            result = refresh_persisted_score_read_models(
                session,
                trigger_type="MANUAL_BACKFILL",
                trigger_ref="scripts/rebuild_score_read_model.py",
                teacher_ids=args.teacher_ids,
            )
            session.flush()
            if args.apply:
                session.commit()
            else:
                session.rollback()
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if args.test_database:
            selected_engine.dispose()

    print(
        json.dumps(
            {**result, "applied": args.apply},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
