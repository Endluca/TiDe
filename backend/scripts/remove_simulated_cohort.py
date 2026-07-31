from __future__ import annotations

import argparse
import json

from app.database import engine
from app.simulated_cohort_seed import remove_balanced_simulated_cohort


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded cleanup for the explicit July 28 test cohort.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the cleanup. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--expected-teachers",
        type=int,
        default=20,
        help="Safety guard for the exact number of simulation teachers.",
    )
    args = parser.parse_args()
    result = remove_balanced_simulated_cohort(
        engine,
        expected_teacher_count=args.expected_teachers,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
