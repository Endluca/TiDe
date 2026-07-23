from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select

from app.config_models import (
    SCORE_POLICY_V4_PAYLOAD,
    SCORE_POLICY_V5_PAYLOAD,
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
)
from app.database import Base, session_scope
from app.db_models import (
    DataImportBatchRecord,
    NotificationRecord,
    ScoreAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TeacherMetricSnapshotRecord,
    TeacherRecord,
)
from app.services import GrowthService
from app.store import DatabaseStore
from app.teacher_data_import import (
    EXPECTED_HEADERS,
    ImportValidationError,
    SCORE_POLICY_SHA256,
    SCORE_POLICY_SNAPSHOT,
    SCORE_RULE_VERSION,
    _as_optional_boolean,
    import_teacher_metrics,
    recalculate_current_class_quality_scores,
    score_policy_sha256,
)
from app.task_seed import seed_task_catalog


def _source_row(teacher_id: int, *, name: str = "Real Teacher") -> list[object]:
    values: dict[str, object] = {
        "tchr_id": teacher_id,
        "real_name": name,
        "bu": "PH",
        "based_type": "HBT",
        "status": "on",
        "job_days": 90,
        "teach_area_type": "ovs",
        "onboard_date": date(2026, 4, 1),
        "onboard_30d_end_date": date(2026, 4, 30),
        "first_booked_dt": date(2026, 4, 3),
        "is_cpl_tesol": 1,
        "is_self_introduce": 0,
        "total_completed_cnt": 12,
        "peak_completed_cnt": 4,
        "absent_cnt": 1,
        "late_cnt": 2,
        "early_cnt": 1,
        "perfect_cnt": 8,
        "completed_again_student_15d_cnt": 1,
        "feedback_praise_cnt": 3,
        "feedback_favorite_cnt": 2,
    }
    return [values.get(header) for header in EXPECTED_HEADERS]


def _source_row_with_peak_slots(teacher_id: int, peak_slot_cnt: object) -> list[object]:
    row = _source_row(teacher_id)
    row[EXPECTED_HEADERS.index("peak_slot_cnt")] = peak_slot_cnt
    return row


def _write_workbook(
    path: Path,
    rows: list[list[object]],
    *,
    headers: tuple[str, ...] = EXPECTED_HEADERS,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "境外教师明细"
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(row[: len(headers)])
    workbook.save(path)
    workbook.close()
    return path


def _engine(tmp_path: Path, name: str = "teacher-import.db"):
    selected = create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    Base.metadata.create_all(selected)
    seed_task_catalog(selected)
    return selected


def test_import_rejects_any_missing_source_field_before_writing(tmp_path: Path) -> None:
    source = _write_workbook(
        tmp_path / "missing-column.xlsx",
        [_source_row(9001)],
        headers=EXPECTED_HEADERS[:-1],
    )
    engine = _engine(tmp_path)

    with pytest.raises(ImportValidationError, match="column contract"):
        import_teacher_metrics(source, bind=engine, snapshot_label="2026-04")

    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(DataImportBatchRecord)) == 0
        assert session.scalar(select(func.count()).select_from(TeacherRecord)) == 0


def test_import_rejects_duplicate_teacher_id_before_writing(tmp_path: Path) -> None:
    source = _write_workbook(
        tmp_path / "duplicate.xlsx",
        [_source_row(9001), _source_row(9001, name="Duplicate")],
    )
    engine = _engine(tmp_path)

    with pytest.raises(ImportValidationError, match="duplicate tchr_id"):
        import_teacher_metrics(source, bind=engine, snapshot_label="2026-04")

    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(DataImportBatchRecord)) == 0
        assert session.scalar(select(func.count()).select_from(TeacherRecord)) == 0


def test_import_rejects_non_strict_first_booked_date(tmp_path: Path) -> None:
    row = _source_row(9001)
    row[EXPECTED_HEADERS.index("first_booked_dt")] = "2026-04-03-not-a-date"
    source = _write_workbook(tmp_path / "invalid-first-booked-date.xlsx", [row])

    with pytest.raises(ImportValidationError, match="first_booked_dt must be an ISO/Excel date"):
        import_teacher_metrics(source, bind=_engine(tmp_path), snapshot_label="2026-04")


def test_future_profile_boolean_contract_preserves_null_and_rejects_implicit_values() -> None:
    assert _as_optional_boolean({}, "is_cpl_tesol", row_number=2) is None
    assert _as_optional_boolean(
        {"is_cpl_tesol": False}, "is_cpl_tesol", row_number=2
    ) is False
    assert _as_optional_boolean(
        {"is_cpl_tesol": "1"}, "is_cpl_tesol", row_number=2
    ) is True
    with pytest.raises(ImportValidationError, match="explicit boolean or controlled 0/1"):
        _as_optional_boolean(
            {"is_cpl_tesol": "yes"}, "is_cpl_tesol", row_number=2
        )


def test_import_is_idempotent_without_rewriting_snapshot_or_accounts(tmp_path: Path) -> None:
    source = _write_workbook(
        tmp_path / "teacher-metrics.xlsx",
        [_source_row(9001), _source_row(9002, name="Another Real Teacher")],
    )
    engine = _engine(tmp_path)

    first = import_teacher_metrics(
        source,
        bind=engine,
        snapshot_label="2026-04-overseas-new-teacher-30d",
        expected_row_count=2,
    )
    with session_scope(engine) as session:
        before = session.scalar(
            select(TeacherMetricSnapshotRecord).where(
                TeacherMetricSnapshotRecord.teacher_id == "9001"
            )
        ).updated_at

    second = import_teacher_metrics(
        source,
        bind=engine,
        snapshot_label="2026-04-overseas-new-teacher-30d",
        expected_sha256=first.source_sha256,
        expected_row_count=2,
    )

    assert second.batch_id == first.batch_id
    assert second.idempotent_reimport is True
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(DataImportBatchRecord)) == 1
        assert session.scalar(select(func.count()).select_from(TeacherMetricSnapshotRecord)) == 2
        assert session.scalar(select(func.count()).select_from(TeacherRecord)) == 2
        assert session.scalar(select(func.count()).select_from(ScoreAccountRecord)) == 10
        assignments = session.scalars(
            select(TaskAssignmentRecord).order_by(
                TaskAssignmentRecord.teacher_id,
                TaskAssignmentRecord.task_code,
            )
        ).all()
        assert len(assignments) == 20
        assert {item.status for item in assignments} == {"ASSIGNED"}
        assert {item.task_kind for item in assignments} == {"FIXED_GROWTH"}
        assert {item.creator_system for item in assignments} == {"TRIGGER_CENTER"}
        assert {item.source_mode for item in assignments} == {"REAL"}
        assert {item.evidence_snapshot["trigger_code"] for item in assignments} == {
            "NEW_TEACHER_CREATED"
        }
        assert session.scalar(select(func.count()).select_from(NotificationRecord)) == 0
        after = session.scalar(
            select(TeacherMetricSnapshotRecord).where(
                TeacherMetricSnapshotRecord.teacher_id == "9001"
            )
        ).updated_at
        assert after == before


def test_import_keeps_lossless_source_row_and_explicit_provenance(tmp_path: Path) -> None:
    source = _write_workbook(tmp_path / "provenance.xlsx", [_source_row(9001)])
    engine = _engine(tmp_path)
    result = import_teacher_metrics(source, bind=engine, snapshot_label="2026-04")

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "9001")
        snapshot = session.scalar(select(TeacherMetricSnapshotRecord))
        batch = session.get(DataImportBatchRecord, result.batch_id)

        assert teacher.data_mode == "MIXED"
        assert teacher.source_batch_id == result.batch_id
        assert teacher.payload["timezone"] is None
        assert teacher.timezone == "UTC"
        assert teacher.payload["metric_inputs"]["on_time_completed_cnt"] == 9
        assert (
            teacher.payload["metric_provenance"]["on_time_completed_cnt"]["source_mode"]
            == "DERIVED_REAL"
        )
        assert teacher.payload["metric_provenance"]["real_absent_cnt"]["source_mode"] == (
            "SOURCE_MISSING"
        )
        assert teacher.payload["metric_inputs"]["real_absent_cnt"] == 0
        assert teacher.payload["metric_inputs"]["new_teacher_task_score"] == 0
        assert teacher.payload["metric_inputs"]["class_quality_no_issue_rate"] == 0
        assert teacher.payload["metric_inputs"]["perfect_cnt"] == 8
        assert snapshot.class_quality_score == 12.8
        assert teacher.payload["metric_provenance"]["perfect_cnt"]["source_mode"] == "REAL"
        assert teacher.payload["metric_provenance"]["new_teacher_task_score"][
            "source_mode"
        ] == "SOURCE_MISSING"
        assert teacher.payload["metric_provenance"]["class_quality_no_issue_rate"][
            "source_mode"
        ] == "SOURCE_MISSING"
        assert teacher.payload["graduation_state"] == "IN_PROGRESS"
        assert snapshot.data_mode == "MIXED"
        assert len(snapshot.raw_payload) == len(EXPECTED_HEADERS)
        assert snapshot.raw_payload["tchr_id"] == 9001
        assert snapshot.raw_payload["first_booked_dt"].startswith("2026-04-03")
        assert snapshot.first_booked_date == date(2026, 4, 3)
        assert teacher.payload["first_booked_date"] == "2026-04-03"
        assert teacher.payload["profile_provenance"]["first_booked_date"][
            "source_mode"
        ] == "REAL"
        # These two fields belong to the future external daily feed, not the
        # delivered 61-column workbook. Missing evidence must stay NULL.
        assert snapshot.is_cpl_tesol is None
        assert snapshot.is_self_introduce is None
        assert teacher.payload["is_cpl_tesol"] is None
        assert teacher.payload["is_self_introduce"] is None
        assert "g01" not in {key.casefold() for key in teacher.payload}
        assert snapshot.score_rule_version == SCORE_RULE_VERSION
        assert snapshot.score_policy_snapshot == SCORE_POLICY_SNAPSHOT
        canonical_policy = json.dumps(
            snapshot.score_policy_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert hashlib.sha256(canonical_policy).hexdigest() == SCORE_POLICY_SHA256
        assert batch.payload["validation"]["score_rule_version"] == SCORE_RULE_VERSION
        assert batch.payload["validation"]["score_policy_sha256"] == SCORE_POLICY_SHA256


def test_teacher_detail_projects_typed_snapshot_profile_without_persisting_it(
    tmp_path: Path,
) -> None:
    source = _write_workbook(
        tmp_path / "typed-profile-projection.xlsx",
        [_source_row(9001), _source_row(9002, name="Missing Typed Profile")],
    )
    engine = _engine(tmp_path)
    result = import_teacher_metrics(
        source,
        bind=engine,
        snapshot_label="typed-profile-projection",
    )

    with session_scope(engine) as session:
        snapshots = {
            snapshot.teacher_id: snapshot
            for snapshot in session.scalars(
                select(TeacherMetricSnapshotRecord)
            ).all()
        }
        snapshots["9001"].first_booked_date = date(2026, 4, 8)
        snapshots["9001"].is_cpl_tesol = False
        snapshots["9001"].is_self_introduce = True

        snapshots["9002"].first_booked_date = None
        snapshots["9002"].is_cpl_tesol = None
        snapshots["9002"].is_self_introduce = None
        second_teacher = session.get(TeacherRecord, "9002")
        second_teacher.payload = {
            **second_teacher.payload,
            "first_booked_date": "2026-04-03",
            "is_cpl_tesol": True,
            "is_self_introduce": False,
        }

    service = GrowthService(DatabaseStore(engine))
    observed = service.teacher_detail("9001")
    missing = service.teacher_detail("9002")

    assert observed["first_booked_date"] == "2026-04-08"
    assert observed["is_cpl_tesol"] is False
    assert observed["is_self_introduce"] is True
    assert observed["data_mode"] == "MIXED"
    assert observed["country"] == "Unknown"
    assert observed["timezone"] is None

    expected_fields = (
        "first_booked_date",
        "is_cpl_tesol",
        "is_self_introduce",
    )
    for field in expected_fields:
        assert observed["profile_provenance"][field] == {
            "source_mode": "REAL",
            "source_field": f"teacher_metric_snapshots.{field}",
            "batch_id": result.batch_id,
            "note": "Typed teacher profile value from the teacher metric snapshot.",
        }
        assert missing[field] is None
        assert missing["profile_provenance"][field] == {
            "source_mode": "SOURCE_MISSING",
            "source_field": f"teacher_metric_snapshots.{field}",
            "batch_id": result.batch_id,
            "note": "The teacher metric snapshot has no typed value for this field.",
        }

    with session_scope(engine) as session:
        stored_first = session.get(TeacherRecord, "9001").payload
        stored_second = session.get(TeacherRecord, "9002").payload
        assert stored_first["first_booked_date"] == "2026-04-03"
        assert stored_first["is_cpl_tesol"] is None
        assert stored_first["is_self_introduce"] is None
        assert stored_second["first_booked_date"] == "2026-04-03"
        assert stored_second["is_cpl_tesol"] is True
        assert stored_second["is_self_introduce"] is False


def test_missing_perfect_count_scores_zero_without_claiming_observed_zero(
    tmp_path: Path,
) -> None:
    row = _source_row(9001)
    row[EXPECTED_HEADERS.index("perfect_cnt")] = None
    source = _write_workbook(tmp_path / "missing-perfect.xlsx", [row])
    engine = _engine(tmp_path)

    import_teacher_metrics(source, bind=engine, snapshot_label="missing-perfect")

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "9001")
        snapshot = session.scalar(select(TeacherMetricSnapshotRecord))
        assert teacher is not None
        assert snapshot is not None
        assert snapshot.perfect_cnt == 0
        assert snapshot.class_quality_score == 0
        assert teacher.payload["metric_provenance"]["perfect_cnt"]["source_mode"] == (
            "SOURCE_MISSING"
        )


def test_class_quality_recalculation_uses_published_v5_and_is_idempotent(
    tmp_path: Path,
) -> None:
    source = _write_workbook(
        tmp_path / "stale-class-quality.xlsx",
        [_source_row(9001)],
    )
    engine = _engine(tmp_path)
    with session_scope(engine) as session:
        session.add(
            ConfigVersionRecord(
                version_id="CFG-SCORE-V5-PUBLISHED",
                config_key=ConfigKey.SCORE_GRADUATION.value,
                version_number=5,
                status=ConfigStatus.PUBLISHED.value,
                high_impact=True,
                payload=SCORE_POLICY_V5_PAYLOAD,
                validation_errors=[],
                created_by="test",
                updated_by="test",
                validated_by="test",
                published_by="test",
            )
        )

    import_teacher_metrics(
        source,
        bind=engine,
        snapshot_label="stale-class-quality",
    )

    with session_scope(engine) as session:
        snapshot = session.scalar(select(TeacherMetricSnapshotRecord))
        teacher = session.get(TeacherRecord, "9001")
        account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "9001",
                ScoreAccountRecord.dimension == "CLASS_QUALITY",
            )
        )
        assert snapshot is not None
        assert teacher is not None
        assert account is not None
        stale_total = round(snapshot.raw_total_score - snapshot.class_quality_score, 2)
        snapshot.score_rule_version = "new_teacher_30d_20260723_v4"
        snapshot.score_policy_snapshot = SCORE_POLICY_V4_PAYLOAD
        snapshot.score_policy_sha256 = score_policy_sha256(SCORE_POLICY_V4_PAYLOAD)
        snapshot.class_quality_score = 0
        snapshot.raw_total_score = stale_total
        snapshot.public_total_score = stale_total

        teacher_payload = dict(teacher.payload)
        teacher_payload["score_rule_version"] = "new_teacher_30d_20260723_v4"
        teacher_payload["score_policy_version"] = "v4"
        teacher_payload["score_policy_sha256"] = score_policy_sha256(
            SCORE_POLICY_V4_PAYLOAD
        )
        teacher_payload["raw_total_score"] = stale_total
        teacher_payload["total_score"] = stale_total
        teacher_payload["external_display_score"] = stale_total
        teacher_payload["dimensions"] = [
            (
                {
                    **dimension,
                    "score": 0,
                    "score_rule_version": "new_teacher_30d_20260723_v4",
                    "formula": (
                        "total_completed_cnt * 2 * "
                        "class_quality_no_issue_rate"
                    ),
                    "source_fields": ["total_completed_cnt"],
                }
                if dimension["code"] == "CLASS_QUALITY"
                else dimension
            )
            for dimension in teacher_payload["dimensions"]
        ]
        teacher.payload = teacher_payload
        teacher.total_score = stale_total

        account.current_score = 0
        account.score_rule_version = "new_teacher_30d_20260723_v4"
        account.payload = next(
            dimension
            for dimension in teacher_payload["dimensions"]
            if dimension["code"] == "CLASS_QUALITY"
        )

    preview = recalculate_current_class_quality_scores(
        bind=engine,
        dry_run=True,
    )
    assert preview.policy_version == "v5"
    assert preview.points_per_unit == 1.6
    assert preview.snapshots_updated == 1
    assert preview.teacher_payloads_updated == 1
    assert preview.score_accounts_updated == 1
    with session_scope(engine) as session:
        assert session.scalar(select(TeacherMetricSnapshotRecord)).class_quality_score == 0

    applied = recalculate_current_class_quality_scores(
        bind=engine,
        dry_run=False,
    )
    assert applied.policy_version == "v5"
    assert applied.current_snapshots_found == 1
    assert applied.snapshots_updated == 1
    assert applied.teacher_payloads_updated == 1
    assert applied.score_accounts_created == 0
    assert applied.score_accounts_updated == 1

    with session_scope(engine) as session:
        snapshot = session.scalar(select(TeacherMetricSnapshotRecord))
        teacher = session.get(TeacherRecord, "9001")
        account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "9001",
                ScoreAccountRecord.dimension == "CLASS_QUALITY",
            )
        )
        assert snapshot.class_quality_score == 12.8
        assert snapshot.raw_total_score == round(
            snapshot.reliability_score
            + snapshot.user_feedback_score
            + snapshot.class_quality_score
            + snapshot.capacity_score
            + snapshot.new_teacher_task_score,
            2,
        )
        assert snapshot.score_policy_snapshot["policy_version"] == "v5"
        assert snapshot.score_policy_sha256 == applied.score_policy_sha256
        assert snapshot.score_rule_version == "new_teacher_30d_20260723_v5"

        quality_dimension = next(
            dimension
            for dimension in teacher.payload["dimensions"]
            if dimension["code"] == "CLASS_QUALITY"
        )
        assert quality_dimension == {
            "code": "CLASS_QUALITY",
            "label": "课堂质量",
            "score": 12.8,
            "minimum": 0,
            "weight": 0,
            "data_mode": "REAL",
            "score_rule_version": "new_teacher_30d_20260723_v5",
            "formula": "perfect_cnt * 1.6",
            "source_fields": ["perfect_cnt"],
        }
        assert teacher.payload["metric_inputs"]["perfect_cnt"] == 8
        assert teacher.payload["total_score"] == snapshot.raw_total_score
        assert teacher.total_score == snapshot.raw_total_score
        assert account.current_score == 12.8
        assert account.payload == quality_dimension
        assert account.version == 2

    repeated = recalculate_current_class_quality_scores(
        bind=engine,
        dry_run=False,
    )
    assert repeated.snapshots_updated == 0
    assert repeated.teacher_payloads_updated == 0
    assert repeated.score_accounts_created == 0
    assert repeated.score_accounts_updated == 0


def test_capacity_peak_slot_boundary_and_first_achievement_never_reverses(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    below = _write_workbook(
        tmp_path / "peak-39.xlsx",
        [_source_row_with_peak_slots(9001, 39)],
    )
    reached = _write_workbook(
        tmp_path / "peak-40.xlsx",
        [_source_row_with_peak_slots(9002, 40)],
    )

    import_teacher_metrics(below, bind=engine, snapshot_label="peak-39")
    import_teacher_metrics(reached, bind=engine, snapshot_label="peak-40")

    with session_scope(engine) as session:
        below_teacher = session.get(TeacherRecord, "9001")
        reached_teacher = session.get(TeacherRecord, "9002")
        assert below_teacher.payload["metric_inputs"]["capacity_score"] == 0
        assert reached_teacher.payload["metric_inputs"]["capacity_score"] == 10
        entries = session.scalars(
            select(ScoreEntryRecord).where(
                ScoreEntryRecord.reason_code == "CAPACITY_MILESTONE_ACHIEVED"
            )
        ).all()
        assert len(entries) == 1
        assert entries[0].teacher_id == "9002"
        assert entries[0].delta_score == 10
        assert entries[0].reversal_of_score_entry_id is None

    corrected = _write_workbook(
        tmp_path / "peak-corrected-to-39.xlsx",
        [_source_row_with_peak_slots(9002, 39)],
    )
    import_teacher_metrics(corrected, bind=engine, snapshot_label="peak-corrected-to-39")

    with session_scope(engine) as session:
        latest = session.scalar(
            select(TeacherMetricSnapshotRecord).where(
                TeacherMetricSnapshotRecord.snapshot_label == "peak-corrected-to-39"
            )
        )
        assert latest.peak_slot_cnt == 39
        assert latest.capacity_score == 10
        assert latest.metric_inputs["capacity_milestone_currently_meets_threshold"] is False
        assert latest.metric_inputs["capacity_milestone_achieved"] is True
        assert session.scalar(
            select(func.count()).select_from(ScoreEntryRecord).where(
                ScoreEntryRecord.teacher_id == "9002",
                ScoreEntryRecord.dimension == "CAPACITY",
            )
        ) == 1


def test_peak_slot_zero_is_real_but_missing_is_source_missing(tmp_path: Path) -> None:
    observed = _write_workbook(
        tmp_path / "peak-observed-zero.xlsx",
        [_source_row_with_peak_slots(9001, 0)],
    )
    missing = _write_workbook(
        tmp_path / "peak-missing.xlsx",
        [_source_row_with_peak_slots(9002, None)],
    )
    engine = _engine(tmp_path)

    import_teacher_metrics(observed, bind=engine, snapshot_label="peak-observed-zero")
    import_teacher_metrics(missing, bind=engine, snapshot_label="peak-missing")

    with session_scope(engine) as session:
        snapshots = {
            item.teacher_id: item
            for item in session.scalars(select(TeacherMetricSnapshotRecord)).all()
        }
        assert snapshots["9001"].peak_slot_cnt == 0
        assert snapshots["9001"].metric_provenance["peak_slot_cnt"]["source_mode"] == "REAL"
        assert snapshots["9002"].peak_slot_cnt == 0
        assert snapshots["9002"].metric_provenance["peak_slot_cnt"]["source_mode"] == (
            "SOURCE_MISSING"
        )


@pytest.mark.parametrize("invalid_value", [-1, "not-an-integer"])
def test_peak_slot_invalid_or_negative_value_rejects_whole_batch(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    source = _write_workbook(
        tmp_path / f"peak-invalid-{invalid_value}.xlsx",
        [_source_row_with_peak_slots(9001, invalid_value)],
    )
    engine = _engine(tmp_path)

    with pytest.raises(ImportValidationError, match="peak_slot_cnt"):
        import_teacher_metrics(source, bind=engine, snapshot_label="peak-invalid")

    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(DataImportBatchRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ScoreEntryRecord)) == 0


def test_teacher_dirty_detection_skips_unchanged_accounts(tmp_path: Path) -> None:
    source = _write_workbook(tmp_path / "dirty-detection.xlsx", [_source_row(9001)])
    engine = _engine(tmp_path)
    DatabaseStore(engine, seed_on_empty=True)
    import_teacher_metrics(source, bind=engine, snapshot_label="2026-04")

    restarted = DatabaseStore(engine)
    restarted.persist()
    assert restarted.last_persist_stats == {
        "teachers_written": 0,
        "score_accounts_written": 0,
    }

    restarted.teachers["9001"]["risk_tags"].append("test-only-dirty-marker")
    restarted.persist()
    assert restarted.last_persist_stats == {
        "teachers_written": 1,
        "score_accounts_written": 5,
    }
