"""Retired teacher snapshot import compatibility surface.

The XLSX importer in this module used to populate ``teacher_metric_snapshots``
and derived score projections.  The only current teacher source is now
``teacher_source_wide``; it is owned by the source-monitor consumer and its
changes are projected by SourceWide Worker.

The public names remain importable so old operational commands fail with a
stable, explicit error instead of crashing during import or, worse, writing a
partial legacy batch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from .config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    SCORE_POLICY_V2_PAYLOAD,
    ConfigKey,
    ScoreGraduationConfig,
)


SOURCE_SHEET = "境外教师明细"
SOURCE_SYSTEM = "MANUAL_XLSX:OVERSEAS_NEW_TEACHER_30D_WIDE"
SCORE_RULE_VERSION = "new_teacher_30d_20260728_v1"
SCORE_POLICY_SNAPSHOT = ScoreGraduationConfig.model_validate(
    DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]
).model_dump(mode="json")
SCORE_POLICY_V2_SNAPSHOT = ScoreGraduationConfig.model_validate(
    SCORE_POLICY_V2_PAYLOAD
).model_dump(mode="json")
CAPACITY_MILESTONE_ID = "CAPACITY_PEAK_SLOT_40"
CAPACITY_MILESTONE_REASON_CODE = "CAPACITY_MILESTONE_ACHIEVED"
CAPACITY_MILESTONE_SETTLEMENT_MODE = "FIRST_ACHIEVEMENT_LOCKED"
CAPACITY_MILESTONE_POLICY_VERSIONS = frozenset(
    {"v1", "v4", "v5", "v6", "v7", "v8", "v9", "v10"}
)
DIRECT_EXTERNAL_SCALE_POLICY_VERSIONS = frozenset(
    {"v1", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"}
)


def score_policy_sha256(policy: dict[str, Any]) -> str:
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SCORE_POLICY_SHA256 = score_policy_sha256(SCORE_POLICY_SNAPSHOT)

# Kept as a read-only compatibility constant for callers that still identify
# the retired workbook contract.  It is not accepted for persistence.
EXPECTED_HEADERS: tuple[str, ...] = (
    "tchr_id",
    "real_name",
    "tchr_group",
    "tchr_group_desc",
    "center_type_id",
    "center_type_desc",
    "bu",
    "based_type",
    "status",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_days",
    "job_month",
    "is_ft_hbt",
    "is_fte",
    "teach_area_type",
    "tchr_score",
    "onboard_date",
    "onboard_30d_end_date",
    "first_open_slot_dt",
    "first_booked_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "total_completed_cnt",
    "peak_completed_cnt",
    "absent_cnt",
    "late_cnt",
    "early_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "completed_again_student_15d_cnt",
    "feedback_total_eval_cnt",
    "feedback_praise_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_favorite_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "peak_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_rebook_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
)


class ImportValidationError(ValueError):
    """Compatibility base for historical import command errors."""


class LegacyTeacherImportRetiredError(ImportValidationError):
    """Raised before any I/O when the retired teacher importer is invoked."""

    error_code = "LEGACY_TEACHER_METRIC_IMPORT_RETIRED"

    def __init__(self) -> None:
        super().__init__(
            f"{self.error_code}: teacher files must be written to "
            "teacher_source_wide by the source-monitor consumer"
        )


class LegacyClassQualityRecalculationRetiredError(RuntimeError):
    """Raised before any I/O when the retired quality rebuild is invoked."""

    error_code = "LEGACY_CLASS_QUALITY_RECALCULATION_RETIRED"

    def __init__(self) -> None:
        super().__init__(
            f"{self.error_code}: classroom quality is maintained from "
            "lesson_source_wide by SourceWide Worker"
        )


@dataclass(frozen=True)
class TeacherMetricImportResult:
    """Historical return shape retained for import compatibility only."""

    batch_id: str
    source_uri: str
    source_sha256: str
    source_sheet: str
    snapshot_label: str
    row_count: int
    column_count: int
    data_mode: str
    idempotent_reimport: bool
    perfect_vs_on_time_difference_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClassQualityRecalculationResult:
    """Historical return shape retained for import compatibility only."""

    policy_version: str
    score_policy_sha256: str
    points_per_unit: float
    teachers_scanned: int
    current_snapshots_found: int
    snapshots_updated: int
    teacher_payloads_updated: int
    score_accounts_created: int
    score_accounts_updated: int
    teachers_without_current_snapshot: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_optional_boolean(
    raw: dict[str, Any], field: str, *, row_number: int
) -> bool | None:
    """Compatibility parser used by old validation callers; it never writes."""

    value = raw.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(int(value))
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    raise ImportValidationError(
        f"row {row_number}: {field} must be an explicit boolean or controlled 0/1, "
        f"got {value!r}"
    )


def import_teacher_metrics(
    source_path: str | Path,
    *,
    bind: Engine | None = None,
    snapshot_label: str | None = None,
    sheet_name: str = SOURCE_SHEET,
    source_system: str = SOURCE_SYSTEM,
    expected_sha256: str | None = None,
    expected_row_count: int | None = None,
) -> TeacherMetricImportResult:
    """Fail closed because the snapshot importer is no longer a write path."""

    del (
        source_path,
        bind,
        snapshot_label,
        sheet_name,
        source_system,
        expected_sha256,
        expected_row_count,
    )
    raise LegacyTeacherImportRetiredError


def recalculate_current_class_quality_scores(
    *,
    bind: Engine | None = None,
    dry_run: bool = True,
) -> ClassQualityRecalculationResult:
    """Fail closed because current quality is a source-wide projection."""

    del bind, dry_run
    raise LegacyClassQualityRecalculationRetiredError


__all__ = [
    "CAPACITY_MILESTONE_ID",
    "CAPACITY_MILESTONE_POLICY_VERSIONS",
    "CAPACITY_MILESTONE_REASON_CODE",
    "CAPACITY_MILESTONE_SETTLEMENT_MODE",
    "ClassQualityRecalculationResult",
    "DIRECT_EXTERNAL_SCALE_POLICY_VERSIONS",
    "EXPECTED_HEADERS",
    "ImportValidationError",
    "LegacyClassQualityRecalculationRetiredError",
    "LegacyTeacherImportRetiredError",
    "SCORE_POLICY_SHA256",
    "SCORE_POLICY_SNAPSHOT",
    "SCORE_POLICY_V2_SNAPSHOT",
    "SCORE_RULE_VERSION",
    "SOURCE_SHEET",
    "SOURCE_SYSTEM",
    "TeacherMetricImportResult",
    "_as_optional_boolean",
    "import_teacher_metrics",
    "recalculate_current_class_quality_scores",
    "score_policy_sha256",
]
