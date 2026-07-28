from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import build_engine
from app.simulated_cohort_seed import (
    DEFAULT_SEED,
    build_balanced_plan,
    seed_balanced_simulated_cohort,
    simulated_cohort_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the explicit 20-teacher balanced test cohort."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-database")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if not args.apply:
        plans = build_balanced_plan(seed=args.seed)
        print(
            json.dumps(
                {
                    "applied": False,
                    "teacher_count": len(plans),
                    "camp_days": [item.camp_day for item in plans],
                    "lesson_counts": [item.lesson_count for item in plans],
                    "completed_task_counts": [
                        item.completed_task_count for item in plans
                    ],
                    "employment_statuses": [
                        item.employment_status for item in plans
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return

    if not args.expected_database:
        parser.error("--apply requires --expected-database")
    engine = build_engine()
    with engine.connect() as connection:
        actual_database = str(
            connection.scalar(text("SELECT current_database()"))
        )
    if actual_database != args.expected_database:
        raise RuntimeError(
            f"Database guard failed: expected={args.expected_database}, "
            f"actual={actual_database}"
        )
    if actual_database not in {
        "tit_growth",
        "tit_growth_test",
        "tide_business_demo",
    }:
        raise RuntimeError(
            f"Simulation seed is not allowed for database {actual_database!r}"
        )

    result = seed_balanced_simulated_cohort(engine, seed=args.seed)
    result["database"] = actual_database
    result["applied"] = True
    result["summary"] = simulated_cohort_summary(engine)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
