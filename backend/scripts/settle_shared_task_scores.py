from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shared_task_score_settlement import settle_shared_task_scores_once


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consume shared-task events and settle REAL G01-G10 scores."
    )
    parser.add_argument("--max-events", type=int, default=100)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling instead of exiting after one batch.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=3.0,
        help="Polling interval used with --watch (default: 3 seconds).",
    )
    args = parser.parse_args()
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be greater than 0")

    while True:
        try:
            result = settle_shared_task_scores_once(max_events=args.max_events)
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if not args.watch or result["claimed"] or result["failed"]:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.watch:
            return 0 if result["failed"] == 0 else 1
        try:
            time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
