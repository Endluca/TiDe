from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime_settings import validate_production_runtime


validate_production_runtime()

from app.shared_task_score_settlement import settle_shared_task_scores_once


DEFAULT_HEARTBEAT_PATH = Path("/tmp/tit-score-worker-heartbeat")


def _write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{time.time():.6f}\n", encoding="ascii")
    temporary.replace(path)


def _heartbeat_is_fresh(path: Path, *, max_age_seconds: float) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age_seconds <= max_age_seconds


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consume shared-task events and settle current mandatory scores."
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
    parser.add_argument(
        "--heartbeat-path",
        type=Path,
        default=Path(
            os.environ.get(
                "TIT_SCORE_WORKER_HEARTBEAT",
                str(DEFAULT_HEARTBEAT_PATH),
            )
        ),
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Exit 0 only when the worker heartbeat is fresh.",
    )
    parser.add_argument(
        "--max-heartbeat-age-seconds",
        type=float,
        default=15.0,
        help="Maximum heartbeat age accepted by --healthcheck.",
    )
    args = parser.parse_args()
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be greater than 0")
    if args.max_heartbeat_age_seconds <= 0:
        parser.error("--max-heartbeat-age-seconds must be greater than 0")
    if args.healthcheck:
        return (
            0
            if _heartbeat_is_fresh(
                args.heartbeat_path,
                max_age_seconds=args.max_heartbeat_age_seconds,
            )
            else 1
        )

    _write_heartbeat(args.heartbeat_path)
    while True:
        try:
            result = settle_shared_task_scores_once(max_events=args.max_events)
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _write_heartbeat(args.heartbeat_path)
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
