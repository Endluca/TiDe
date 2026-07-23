from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lesson_ingestion import (  # noqa: E402
    EXPECTED_LESSON_ROW_COUNT,
    LessonImportValidationError,
    import_lesson_baseline,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strictly preflight and atomically import the 24-column real lesson "
            "baseline, current complaint levels and personalized outputs."
        )
    )
    parser.add_argument("lesson_source", type=Path)
    parser.add_argument("complaint_source", type=Path)
    parser.add_argument(
        "--expected-lesson-row-count",
        type=int,
        default=EXPECTED_LESSON_ROW_COUNT,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full file/database preflight and output planning without writing",
    )
    parser.add_argument(
        "--replace-current",
        action="store_true",
        help=(
            "Replace the current manual lesson projection while retaining old raw "
            "source batches and preserving consumed output history"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = import_lesson_baseline(
            args.lesson_source,
            args.complaint_source,
            expected_lesson_row_count=args.expected_lesson_row_count,
            dry_run=args.dry_run,
            replace_current=args.replace_current,
        )
    except LessonImportValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
