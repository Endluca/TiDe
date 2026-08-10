from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lesson_ingestion import (  # noqa: E402
    LessonImportValidationError,
    import_complaint_category_rules,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and atomically import the reviewed complaint-category "
            "rule workbook without writing lesson source or score projections."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-rule-count", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = import_complaint_category_rules(
            args.source,
            expected_sha256=args.expected_sha256,
            expected_rule_count=args.expected_rule_count,
            dry_run=args.dry_run,
        )
    except LessonImportValidationError as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps({"ok": True, **result.as_dict()}, ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
