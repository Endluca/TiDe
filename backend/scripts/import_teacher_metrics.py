from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.teacher_data_import import (  # noqa: E402
    ImportValidationError,
    SOURCE_SHEET,
    SOURCE_SYSTEM,
    import_teacher_metrics,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strictly validate and idempotently import the 61-column overseas "
            "new-teacher metrics workbook."
        )
    )
    parser.add_argument("source", type=Path, help="Path to the source .xlsx workbook")
    parser.add_argument(
        "--snapshot-label",
        help="Stable business snapshot label (defaults to the workbook filename stem)",
    )
    parser.add_argument("--sheet", default=SOURCE_SHEET)
    parser.add_argument("--source-system", default=SOURCE_SYSTEM)
    parser.add_argument("--expected-sha256", help="Fail if the file content checksum differs")
    parser.add_argument(
        "--expected-row-count",
        type=int,
        help="Fail if the number of non-empty teacher rows differs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = import_teacher_metrics(
            args.source,
            snapshot_label=args.snapshot_label,
            sheet_name=args.sheet,
            source_system=args.source_system,
            expected_sha256=args.expected_sha256,
            expected_row_count=args.expected_row_count,
        )
    except ImportValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
