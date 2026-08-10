from __future__ import annotations

import argparse
import sys

from app.simulated_cohort_seed import (
    LegacySimulatedCohortRetiredError,
    remove_balanced_simulated_cohort,
)


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    try:
        remove_balanced_simulated_cohort(
            object(),  # type: ignore[arg-type]
            expected_teacher_count=args.expected_teachers,
            apply=args.apply,
        )
    except LegacySimulatedCohortRetiredError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    raise AssertionError("retired simulated cohort cleanup unexpectedly ran")


if __name__ == "__main__":
    raise SystemExit(main())
