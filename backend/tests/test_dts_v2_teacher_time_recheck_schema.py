from __future__ import annotations

from pathlib import Path

from app.db_models import (
    DtsTeacherTimeRecheckAuditRecord,
    DtsTeacherTimeRecheckResultRecord,
    DtsTeacherTimeRecheckScheduleRecord,
)


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_97_teacher_time_recheck.py"
)


def test_time_recheck_orm_has_daily_immutable_proof_tables() -> None:
    assert DtsTeacherTimeRecheckScheduleRecord.__tablename__ == (
        "dts_teacher_time_recheck_schedule"
    )
    result_columns = {
        column.name
        for column in DtsTeacherTimeRecheckResultRecord.__table__.columns
    }
    assert {
        "teacher_id",
        "business_date_beijing",
        "dom_aggregate_revision",
        "ovs_aggregate_revision",
        "regional_state_sha256",
        "projection_generation",
        "claimed_work_revision",
        "time_values",
        "plan_sha256",
    }.issubset(result_columns)
    assert DtsTeacherTimeRecheckAuditRecord.__tablename__ == (
        "dts_teacher_time_recheck_audits"
    )


def test_migration_exposes_complete_protected_clock_worker_contract() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for signature in (
        "enqueue_due_teacher_time_rechecks_v2",
        "claim_teacher_time_rechecks_v2",
        "complete_teacher_time_recheck_v2",
        "fail_teacher_time_recheck_v2",
        "reap_expired_teacher_time_rechecks_v2",
        "materialize_teacher_time_recheck_v2",
        "teacher_time_recheck_result_proof_v1",
        "dts_v2_teacher_time_recheck_health_v1",
    ):
        assert signature in source
    assert "time '00:05:00'" in source
    assert "AT TIME ZONE 'Asia/Shanghai'" in source
    assert "SOURCEWIDE_TIME_RECHECK" in source
    assert "TEACHER_TIME_RECHECK" in source
    assert "SUPERSEDED_BY_CURRENT_DATE" in source
    assert "DTS_V2_TEACHER_TIME_RECHECK_DATE_REGRESSION" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert 'OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"' in source
    assert "TO {OUTBOX_ROLE}" in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source
