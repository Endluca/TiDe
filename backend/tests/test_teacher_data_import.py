from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config_models import ConfigKey, DEFAULT_CONFIG_PAYLOADS
from app.teacher_data_import import (
    EXPECTED_HEADERS,
    ImportValidationError,
    LegacyClassQualityRecalculationRetiredError,
    LegacyTeacherImportRetiredError,
    SCORE_POLICY_SHA256,
    SCORE_POLICY_SNAPSHOT,
    _as_optional_boolean,
    import_teacher_metrics,
    recalculate_current_class_quality_scores,
    score_policy_sha256,
)
from scripts.import_teacher_metrics import main as import_cli_main
from scripts.recalculate_class_quality_scores import main as quality_cli_main


def test_retired_teacher_import_fails_before_file_or_database_access() -> None:
    missing_source = Path("/path/that/must/not/be/read/teacher-metrics.xlsx")
    poison_bind = object()

    with pytest.raises(LegacyTeacherImportRetiredError) as caught:
        import_teacher_metrics(
            missing_source,
            bind=poison_bind,  # type: ignore[arg-type]
            snapshot_label="must-not-write",
            expected_sha256="0" * 64,
            expected_row_count=1,
        )

    assert isinstance(caught.value, ImportValidationError)
    assert caught.value.error_code == "LEGACY_TEACHER_METRIC_IMPORT_RETIRED"
    assert str(caught.value).startswith(
        "LEGACY_TEACHER_METRIC_IMPORT_RETIRED:"
    )


def test_retired_class_quality_rebuild_fails_before_database_access() -> None:
    with pytest.raises(LegacyClassQualityRecalculationRetiredError) as caught:
        recalculate_current_class_quality_scores(
            bind=object(),  # type: ignore[arg-type]
            dry_run=False,
        )

    assert caught.value.error_code == (
        "LEGACY_CLASS_QUALITY_RECALCULATION_RETIRED"
    )
    assert str(caught.value).startswith(
        "LEGACY_CLASS_QUALITY_RECALCULATION_RETIRED:"
    )


def test_retired_import_cli_returns_stable_machine_readable_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = import_cli_main(["missing.xlsx"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["error"].startswith(
        "LEGACY_TEACHER_METRIC_IMPORT_RETIRED:"
    )


def test_retired_quality_cli_returns_stable_error_without_test_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = quality_cli_main(["--apply", "--test-database"])

    assert exit_code == 2
    assert capsys.readouterr().err.startswith(
        "LEGACY_CLASS_QUALITY_RECALCULATION_RETIRED:"
    )


def test_retired_module_has_no_legacy_table_model_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "teacher_data_import.py"
    ).read_text(encoding="utf-8")

    for retired_model in (
        "TeacherMetricSnapshotRecord",
        "DataImportBatchRecord",
        "ScoreAccountRecord",
        "ScoreEntryRecord",
        "TeacherRecord",
    ):
        assert retired_model not in source


def test_compatibility_constants_and_pure_boolean_parser_remain_stable() -> None:
    assert len(EXPECTED_HEADERS) == 61
    assert EXPECTED_HEADERS[0] == "tchr_id"
    assert EXPECTED_HEADERS[-1] == "capacity_key_slot_day_rate"
    assert SCORE_POLICY_SNAPSHOT == DEFAULT_CONFIG_PAYLOADS[
        ConfigKey.SCORE_GRADUATION
    ]
    assert SCORE_POLICY_SHA256 == score_policy_sha256(SCORE_POLICY_SNAPSHOT)
    assert len(SCORE_POLICY_SHA256) == 64

    assert _as_optional_boolean({}, "flag", row_number=2) is None
    assert _as_optional_boolean({"flag": "1"}, "flag", row_number=2) is True
    assert _as_optional_boolean({"flag": 0}, "flag", row_number=2) is False
    with pytest.raises(
        ImportValidationError,
        match="explicit boolean or controlled 0/1",
    ):
        _as_optional_boolean({"flag": "yes"}, "flag", row_number=2)
