from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.teacher_data_import import (  # noqa: E402
    LegacyClassQualityRecalculationRetiredError,
    recalculate_current_class_quality_scores,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Retired compatibility command. SourceWide Worker now rebuilds "
            "classroom quality from lesson_source_wide."
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
    args = parser.parse_args(argv)
    try:
        recalculate_current_class_quality_scores(dry_run=not args.apply)
    except LegacyClassQualityRecalculationRetiredError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    raise AssertionError("retired recalculation unexpectedly returned")


if __name__ == "__main__":
    raise SystemExit(main())
