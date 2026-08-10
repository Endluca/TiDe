from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.simulated_cohort_seed import (
    DEFAULT_SEED,
    LegacySimulatedCohortRetiredError,
    seed_balanced_simulated_cohort,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Retired compatibility command. Runtime Mock cohort seeding is "
            "disabled."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-database")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    try:
        seed_balanced_simulated_cohort(
            object(),  # type: ignore[arg-type]
            seed=args.seed,
        )
    except LegacySimulatedCohortRetiredError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    raise AssertionError("retired simulated cohort command unexpectedly ran")


if __name__ == "__main__":
    raise SystemExit(main())
