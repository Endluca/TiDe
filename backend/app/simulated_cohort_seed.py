"""Fail-closed compatibility surface for the retired simulated cohort seed.

The former utility populated retired teacher snapshots, lesson facts and
derived lesson scores.  Runtime databases must not receive Mock business data,
and current source-wide tables are owned by the source-monitor consumer.
Isolated tests should create only the exact source rows they need in their
disposable database and run SourceWide Worker explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine


SCENARIO = "BALANCED_TEACHER_COHORT_20260728_V1"
BATCH_ID = "SIM-COHORT-20260728-V1"
DEFAULT_SEED = 20260728


class LegacySimulatedCohortRetiredError(RuntimeError):
    error_code = "LEGACY_SIMULATED_COHORT_RETIRED"

    def __init__(self) -> None:
        super().__init__(
            f"{self.error_code}: runtime Mock seeding is disabled; use exact "
            "teacher_source_wide and lesson_source_wide fixtures only in an "
            "isolated disposable test database"
        )


@dataclass(frozen=True)
class SimulatedTeacherPlan:
    """Historical return type retained so old imports fail predictably."""

    teacher_id: str
    name: str
    country: str
    teacher_timezone: str
    camp_day: int
    lesson_count: int
    completed_task_count: int
    employment_status: str
    task_statuses: dict[str, str]


def _retired(*_args: Any, **_kwargs: Any) -> None:
    raise LegacySimulatedCohortRetiredError


def build_balanced_plan(*, seed: int = DEFAULT_SEED) -> list[SimulatedTeacherPlan]:
    del seed
    _retired()


def seed_balanced_simulated_cohort(
    bind: Engine,
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    del bind, seed
    _retired()


def simulated_cohort_summary(bind: Engine) -> dict[str, Any]:
    del bind
    _retired()


def remove_balanced_simulated_cohort(
    bind: Engine,
    *,
    expected_teacher_count: int = 20,
    apply: bool = False,
) -> dict[str, Any]:
    del bind, expected_teacher_count, apply
    _retired()


__all__ = [
    "BATCH_ID",
    "DEFAULT_SEED",
    "LegacySimulatedCohortRetiredError",
    "SCENARIO",
    "SimulatedTeacherPlan",
    "build_balanced_plan",
    "remove_balanced_simulated_cohort",
    "seed_balanced_simulated_cohort",
    "simulated_cohort_summary",
]
