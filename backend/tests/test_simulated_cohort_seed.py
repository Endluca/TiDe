from __future__ import annotations

from pathlib import Path

import pytest

from app.simulated_cohort_seed import (
    LegacySimulatedCohortRetiredError,
    build_balanced_plan,
    remove_balanced_simulated_cohort,
    seed_balanced_simulated_cohort,
    simulated_cohort_summary,
)
from scripts.remove_simulated_cohort import main as remove_cli_main
from scripts.seed_simulated_cohort import main as seed_cli_main


@pytest.mark.parametrize(
    "operation",
    (
        lambda: build_balanced_plan(),
        lambda: seed_balanced_simulated_cohort(object()),  # type: ignore[arg-type]
        lambda: simulated_cohort_summary(object()),  # type: ignore[arg-type]
        lambda: remove_balanced_simulated_cohort(object()),  # type: ignore[arg-type]
    ),
)
def test_retired_simulated_cohort_operations_fail_before_database_access(
    operation,
) -> None:
    with pytest.raises(LegacySimulatedCohortRetiredError) as caught:
        operation()

    assert caught.value.error_code == "LEGACY_SIMULATED_COHORT_RETIRED"


def test_retired_simulation_clis_return_stable_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert seed_cli_main([]) == 2
    assert capsys.readouterr().err.startswith(
        "LEGACY_SIMULATED_COHORT_RETIRED:"
    )
    assert remove_cli_main([]) == 2
    assert capsys.readouterr().err.startswith(
        "LEGACY_SIMULATED_COHORT_RETIRED:"
    )


def test_retired_simulation_module_has_no_legacy_projection_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "simulated_cohort_seed.py"
    ).read_text(encoding="utf-8")
    for retired_model in (
        "LessonFactRecord",
        "LessonDimensionScoreRecord",
        "TeacherMetricSnapshotRecord",
    ):
        assert retired_model not in source
