from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
import pytest
from sqlalchemy import func, select

from app.database import engine, session_scope
from app.db_models import (
    ComplaintCategoryRuleRecord,
    ComplaintRuleImportRecord,
)
from app.lesson_ingestion import (
    COMPLAINT_SOURCE_SHEET,
    EXPECTED_COMPLAINT_HEADERS,
    LegacyLessonBaselineImportRetiredError,
    LessonImportValidationError,
    import_complaint_category_rules,
    import_lesson_baseline,
)
from scripts.import_complaint_category_rules import main as complaint_cli_main
from scripts.import_lesson_baseline import main as lesson_cli_main


def _complaint_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = COMPLAINT_SOURCE_SHEET
    sheet.append(["现行版本"])
    sheet.append(list(EXPECTED_COMPLAINT_HEADERS))
    for index in range(43):
        sheet.append(
            [
                "课堂投诉" if index == 0 else None,
                "出席问题" if index == 0 else None,
                f"测试三级分类-{index + 1}",
                f"P{index % 5}",
                f"Course {index + 1}",
                f"https://example.invalid/course/{index + 1}",
            ]
        )
    workbook.save(path)


def test_retired_lesson_import_fails_before_file_or_database_access() -> None:
    with pytest.raises(LegacyLessonBaselineImportRetiredError) as caught:
        import_lesson_baseline(
            "/path/that/must/not/be/read/lessons.xlsx",
            "/path/that/must/not/be/read/complaints.xlsx",
            bind=object(),  # type: ignore[arg-type]
            expected_lesson_row_count=1,
            dry_run=False,
            replace_current=True,
        )

    assert isinstance(caught.value, LessonImportValidationError)
    assert caught.value.error_code == "LEGACY_LESSON_BASELINE_IMPORT_RETIRED"
    assert str(caught.value).startswith(
        "LEGACY_LESSON_BASELINE_IMPORT_RETIRED:"
    )


def test_retired_lesson_cli_returns_stable_machine_readable_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = lesson_cli_main(["missing-lessons.xlsx", "missing-rules.xlsx"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["error"].startswith(
        "LEGACY_LESSON_BASELINE_IMPORT_RETIRED:"
    )


def test_retired_module_has_no_legacy_projection_model_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "lesson_ingestion.py"
    ).read_text(encoding="utf-8")

    for retired_model in (
        "LessonFactRecord",
        "LessonDimensionScoreRecord",
        "TeacherMetricSnapshotRecord",
    ):
        assert retired_model not in source


def test_complaint_rules_remain_independently_importable_and_idempotent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "complaint-rules.xlsx"
    _complaint_workbook(source)

    dry_run = import_complaint_category_rules(
        source,
        bind=engine,
        expected_rule_count=43,
        dry_run=True,
    )
    assert dry_run.rule_count == 43
    assert dry_run.source_row_count == 43
    with session_scope(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(ComplaintCategoryRuleRecord)
        ) == 0

    imported = import_complaint_category_rules(
        source,
        bind=engine,
        expected_sha256=dry_run.source_sha256,
        expected_rule_count=43,
    )
    assert imported.batch_id == dry_run.batch_id
    assert imported.idempotent_reimport is False
    with session_scope(engine) as session:
        imported_source = session.get(
            ComplaintRuleImportRecord,
            imported.source_sha256,
        )
        assert imported_source is not None
        assert imported_source.source_filename == "complaint-rules.xlsx"
        assert len(imported_source.raw_rows) == 43
        assert imported_source.raw_rows[0]["source_row_number"] == 3
        assert imported_source.raw_rows[0][
            "Course Title in the Learning Hub"
        ] == "Course 1"
        assert imported_source.raw_rows[0]["link"] == (
            "https://example.invalid/course/1"
        )
        assert session.scalar(
            select(func.count()).select_from(ComplaintCategoryRuleRecord).where(
                ComplaintCategoryRuleRecord.source_sha256
                == imported.source_sha256
            )
        ) == 43

    repeated = import_complaint_category_rules(source, bind=engine)
    assert repeated.idempotent_reimport is True
    assert repeated.source_row_count == 43


def test_complaint_rule_cli_reports_validation_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = complaint_cli_main(["missing-rules.xlsx"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "complaint workbook does not exist" in payload["error"]
