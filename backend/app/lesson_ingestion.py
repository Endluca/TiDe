"""Retired lesson-baseline compatibility and complaint-rule management.

The historical XLSX command wrote ``lesson_facts`` and derived task/score
projections in one transaction.  Current lesson facts enter only through
``lesson_source_wide`` and are projected by SourceWide Worker, so that command
must fail before touching a file or database.

Complaint category mappings remain an independently owned configuration fact.
Their reviewed workbook can still be imported through
``import_complaint_category_rules`` without writing either source-wide table or
any retired lesson projection.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, NoReturn, Sequence

from openpyxl import load_workbook
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from .database import engine as default_engine
from .db_models import (
    ComplaintRuleImportRecord,
    ComplaintCategoryRuleRecord,
)
from .personalized_rules import normalize_text
from .personalized_trigger_projection import (
    TRIGGER_RULE_VERSION,
    LessonTriggerProjectionError as LessonImportValidationError,
    LessonTriggerRow as _LessonRow,
    build_output_specs as _build_output_specs,
    feedback_labels as _feedback_labels,
    materialize_outputs as _materialize_outputs,
    published_template_map as _template_map,
)
from .source_contracts import LESSON_SOURCE_FIELDS


LESSON_SOURCE_SYSTEM = "MANUAL_XLSX:NEW_TEACHER_30D_LESSONS"
COMPLAINT_SOURCE_SYSTEM = "MANUAL_XLSX:COMPLAINT_LEVEL_RULES"
COMPLAINT_SOURCE_SHEET = "客服&销售&学员端投诉分级"
COMPLAINT_SOURCE_REGION = "A1:F45"
EXPECTED_LESSON_ROW_COUNT = 37_317
EXPECTED_LESSON_HEADERS = LESSON_SOURCE_FIELDS
EXPECTED_COMPLAINT_HEADERS: tuple[str, ...] = (
    "一级分类",
    "二级分类",
    "三级分类",
    "P级",
    "Course Title in the Learning Hub",
    "link",
)


class LegacyLessonBaselineImportRetiredError(LessonImportValidationError):
    """Raised before any I/O when the retired lesson importer is invoked."""

    error_code = "LEGACY_LESSON_BASELINE_IMPORT_RETIRED"

    def __init__(self) -> None:
        super().__init__(
            f"{self.error_code}: lesson files must be written to "
            "lesson_source_wide by the source-monitor consumer"
        )


@dataclass(frozen=True)
class ComplaintRuleImportResult:
    batch_id: str
    source_sha256: str
    source_row_count: int
    rule_count: int
    unmapped_source_rows: list[int]
    dry_run: bool
    idempotent_reimport: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _ComplaintRow:
    row_number: int
    category_l1: str
    category_l2: str
    category_l3: str
    source_level: str
    severity_rank: int
    default_route: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise LessonImportValidationError("source contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _optional_text(value: Any) -> str | None:
    normalized = normalize_text(value)
    return normalized or None


def _complaint_route(level2: str, severity_rank: int) -> str:
    if level2 == "出席问题":
        return "RELIABILITY"
    if level2 == "网络设备问题":
        return "CLASS_QUALITY"
    if severity_rank <= 1:
        return "OPS_CASE"
    return "USER_FEEDBACK"


def _read_complaint_workbook(
    source: Path,
) -> tuple[str, list[_ComplaintRow], list[int], list[dict[str, Any]]]:
    if not source.is_file():
        raise LessonImportValidationError(
            f"complaint workbook does not exist: {source}"
        )
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        if COMPLAINT_SOURCE_SHEET not in workbook.sheetnames:
            raise LessonImportValidationError(
                f"complaint sheet is missing: {COMPLAINT_SOURCE_SHEET}"
            )
        worksheet = workbook[COMPLAINT_SOURCE_SHEET]
        region = list(
            worksheet.iter_rows(
                min_row=1,
                max_row=45,
                min_col=1,
                max_col=6,
                values_only=True,
            )
        )
        if len(region) != 45 or normalize_text(region[0][0]) != "现行版本":
            raise LessonImportValidationError(
                f"complaint source region {COMPLAINT_SOURCE_REGION} "
                "is not the reviewed current version"
            )
        headers = tuple(_json_value(value) for value in region[1])
        if headers != EXPECTED_COMPLAINT_HEADERS:
            raise LessonImportValidationError(
                "complaint header contract mismatch: "
                f"expected {EXPECTED_COMPLAINT_HEADERS!r}, got {headers!r}"
            )

        current_l1 = ""
        current_l2 = ""
        rules: list[_ComplaintRow] = []
        skipped_rows: list[int] = []
        raw_rows: list[dict[str, Any]] = []
        seen_l3: dict[str, int] = {}
        for row_number, values in enumerate(region[2:], start=3):
            raw = {
                field: _json_value(value)
                for field, value in zip(EXPECTED_COMPLAINT_HEADERS, values)
            }
            raw_rows.append(raw)
            current_l1 = normalize_text(values[0]) or current_l1
            current_l2 = normalize_text(values[1]) or current_l2
            if not current_l1 or not current_l2:
                raise LessonImportValidationError(
                    f"complaint row {row_number}: level-1/level-2 "
                    "fill-down has no source value"
                )
            level_match = re.fullmatch(
                r"P([0-4])",
                normalize_text(values[3]).upper(),
            )
            if level_match is None:
                raise LessonImportValidationError(
                    f"complaint row {row_number}: P级 must be P0-P4, "
                    f"got {values[3]!r}"
                )
            category_l3 = _optional_text(values[2])
            if category_l3 is None:
                skipped_rows.append(row_number)
                continue
            normalized_l3 = normalize_text(category_l3)
            previous_row = seen_l3.get(normalized_l3)
            if previous_row is not None:
                raise LessonImportValidationError(
                    "complaint third-level category is duplicated: "
                    f"{category_l3!r} at rows {previous_row} and {row_number}"
                )
            seen_l3[normalized_l3] = row_number
            severity_rank = int(level_match.group(1))
            rules.append(
                _ComplaintRow(
                    row_number=row_number,
                    category_l1=current_l1,
                    category_l2=current_l2,
                    category_l3=category_l3,
                    source_level=f"P{severity_rank}",
                    severity_rank=severity_rank,
                    default_route=_complaint_route(
                        current_l2,
                        severity_rank,
                    ),
                )
            )
        if len(raw_rows) != 43:
            raise LessonImportValidationError(
                "complaint source must contain rows 3-45"
            )
        return _sha256(source), rules, skipped_rows, raw_rows
    finally:
        workbook.close()


def _complaint_rule_id(batch_id: str, row_number: int) -> str:
    return f"CR-{batch_id}-{row_number}"


def _add_complaint_batch(
    session: Session,
    *,
    source: Path,
    source_sha256: str,
    batch_id: str,
    rules: Sequence[_ComplaintRow],
    raw_rows: Sequence[dict[str, Any]],
    imported_at: datetime,
) -> None:
    session.add(
        ComplaintRuleImportRecord(
            source_sha256=source_sha256,
            source_filename=source.name,
            raw_rows=[
                {"source_row_number": row_number, **raw}
                for row_number, raw in enumerate(raw_rows, start=3)
            ],
            imported_at=imported_at,
        )
    )
    session.flush()
    for item in rules:
        session.add(
            ComplaintCategoryRuleRecord(
                rule_id=_complaint_rule_id(batch_id, item.row_number),
                source_sha256=source_sha256,
                source_row_number=item.row_number,
                category_l1=item.category_l1,
                category_l2=item.category_l2,
                category_l3=item.category_l3,
                category_l3_normalized=normalize_text(item.category_l3),
                source_level=item.source_level,
                severity_rank=item.severity_rank,
                default_route=item.default_route,
                created_at=imported_at,
            )
        )
    session.flush()


def import_lesson_baseline(
    lesson_source: str | Path,
    complaint_source: str | Path,
    *,
    bind: Engine | None = None,
    expected_lesson_row_count: int | None = EXPECTED_LESSON_ROW_COUNT,
    dry_run: bool = False,
    replace_current: bool = False,
) -> NoReturn:
    """Fail closed because lesson XLSX is no longer a persistence path."""

    del (
        lesson_source,
        complaint_source,
        bind,
        expected_lesson_row_count,
        dry_run,
        replace_current,
    )
    raise LegacyLessonBaselineImportRetiredError


def import_complaint_category_rules(
    source_path: str | Path,
    *,
    bind: Engine | None = None,
    expected_sha256: str | None = None,
    expected_rule_count: int | None = None,
    dry_run: bool = False,
) -> ComplaintRuleImportResult:
    """Validate and atomically persist the independently owned rule mapping."""

    source = Path(source_path).expanduser().resolve()
    source_sha256, rules, skipped_rows, raw_rows = _read_complaint_workbook(
        source
    )
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise LessonImportValidationError(
            "complaint source checksum mismatch: "
            f"expected {expected_sha256}, got {source_sha256}"
        )
    if expected_rule_count is not None and len(rules) != expected_rule_count:
        raise LessonImportValidationError(
            "complaint rule-count mismatch: "
            f"expected {expected_rule_count}, got {len(rules)}"
        )

    batch_id = f"COMPLAINT-{source_sha256[:24]}"
    selected_engine = bind or default_engine
    session = Session(selected_engine, expire_on_commit=False)
    try:
        existing = session.scalar(
            select(ComplaintRuleImportRecord).where(
                ComplaintRuleImportRecord.source_sha256 == source_sha256,
            )
        )
        if existing is not None:
            if len(existing.raw_rows) != len(raw_rows):
                raise LessonImportValidationError(
                    "complaint content conflicts with its immutable import"
                )
            return ComplaintRuleImportResult(
                batch_id=batch_id,
                source_sha256=source_sha256,
                source_row_count=len(raw_rows),
                rule_count=len(rules),
                unmapped_source_rows=list(skipped_rows),
                dry_run=dry_run,
                idempotent_reimport=True,
            )

        result = ComplaintRuleImportResult(
            batch_id=batch_id,
            source_sha256=source_sha256,
            source_row_count=len(raw_rows),
            rule_count=len(rules),
            unmapped_source_rows=list(skipped_rows),
            dry_run=dry_run,
            idempotent_reimport=False,
        )
        if dry_run:
            session.rollback()
            return result

        _add_complaint_batch(
            session,
            source=source,
            source_sha256=source_sha256,
            batch_id=batch_id,
            rules=rules,
            raw_rows=raw_rows,
            imported_at=_utcnow(),
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "COMPLAINT_SOURCE_REGION",
    "COMPLAINT_SOURCE_SHEET",
    "COMPLAINT_SOURCE_SYSTEM",
    "ComplaintRuleImportResult",
    "EXPECTED_COMPLAINT_HEADERS",
    "EXPECTED_LESSON_HEADERS",
    "EXPECTED_LESSON_ROW_COUNT",
    "LESSON_SOURCE_SYSTEM",
    "LegacyLessonBaselineImportRetiredError",
    "LessonImportValidationError",
    "TRIGGER_RULE_VERSION",
    "import_complaint_category_rules",
    "import_lesson_baseline",
]
