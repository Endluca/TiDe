from __future__ import annotations

from pathlib import Path

from app.db_models import (
    DtsDirtyKeyRecord,
    DtsPipelineControlRecord,
    DtsQualificationGateCommandRecord,
)


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260822_96_compat_queue_qualification_gate.py"
)
REV79 = (
    BACKEND
    / "migrations"
    / "versions"
    / "20260822_79_dts_v2_epoch_control.py"
)


def test_revision_96_follows_fail_closed_cutover_revision() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_96_compat_queue_gate"' in source
    assert 'down_revision: Union[str, None] = "20260822_95_projection_cutover"' in source
    assert "dts_v1_compat_dirty_not_complete_count_v1" in source
    assert "claim_v1_compat_dirty_key_v1" in source
    assert "complete_v1_compat_dirty_key_v1" in source
    assert "fail_v1_compat_dirty_key_v1" in source
    assert "trg_sync_v1_compat_dirty_input_v1" in source
    assert "compat_status='PROCESSING'" in source
    assert "TO {INGEST_ROLE}" in source


def test_v1_compat_and_v2_have_independent_state_columns() -> None:
    columns = DtsDirtyKeyRecord.__table__.c
    assert {
        "status",
        "required_work_revision",
        "completed_work_revision",
        "compat_status",
        "compat_required_work_revision",
        "compat_claimed_work_revision",
        "compat_completed_work_revision",
        "compat_attempt_count",
        "compat_row_version",
    }.issubset(columns.keys())
    assert {
        "ck_dts_dirty_key_compat_state_v1",
        "ck_dts_dirty_key_state_shape_v2",
    }.issubset(
        constraint.name
        for constraint in DtsDirtyKeyRecord.__table__.constraints
    )


def test_qualification_gate_is_database_authoritative_and_audited() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    baseline = REV79.read_text(encoding="utf-8")

    assert "qualification_grants_enabled" in baseline
    assert "qualification_grants_enabled" in DtsPipelineControlRecord.__table__.c
    assert "enforce_irreversible_qualification_gate_v2" in source
    assert "set_irreversible_qualification_grants_v2" in source
    assert "p_expected_control_row_version" in source
    assert "dts_qualification_gate_commands" in source
    assert "OPERATOR_RECOVERY" in source
    assert "guard_qualification_gate_rollback_v2" in source
    assert (
        "DTS_QUALIFICATION_GATE_DISABLE_REQUIRED_BEFORE_ROLLBACK"
        in source
    )
    assert "qualification_grants_enabled:=old_graduated" not in source
    assert (
        DtsQualificationGateCommandRecord.__table__.name
        == "dts_qualification_gate_commands"
    )


def test_revision_96_downgrade_refuses_to_erase_material_state() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "DTS_V1_COMPAT_GATE_DOWNGRADE_REQUIRES_EMPTY_STATE" in source
    assert "qualification_grants_enabled IS TRUE" in source
    assert "compat_completed_work_revision<>" in source
    assert "'COMPLAINT_CATEGORY'" in source
