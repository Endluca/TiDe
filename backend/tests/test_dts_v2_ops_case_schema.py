from __future__ import annotations

import importlib.util
from pathlib import Path

from app.db_models import (
    AuditEventRecord,
    OpsCaseRecord,
    OpsCaseRecoveryEventRecord,
    OpsDecisionRecord,
)


REVISION = "20260822_86_ops_case_v2"
DOWN_REVISION = "20260822_85_outbox_three_state"


def _migration_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260822_86_ops_case_v2.py"
    )


def _load_migration():
    path = _migration_path()
    spec = importlib.util.spec_from_file_location(REVISION, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_86_is_additive_after_outbox_three_state() -> None:
    migration = _load_migration()

    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION
    source = _migration_path().read_text(encoding="utf-8")
    assert "CREATE ROLE {OUTBOX_RUNTIME_ROLE} LOGIN NOINHERIT" in source
    assert "tit_dts_outbox_worker_runtime" in source
    assert "record_dts_v2_technical_case(" in source
    assert "record_dts_v2_technical_case_recovery(" in source
    assert "FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime" in source
    assert "DTS_V2_TECHNICAL_CASE_WORK_NOT_DEAD" in source
    assert "DTS_V2_TECHNICAL_RECOVERY_NOT_COMPLETE" in source
    assert "OPS_CASE_APPEND_ONLY" in source
    assert "OPS_DECISION_FACT_IMMUTABLE" in source
    assert "IN_REVIEW" in source


def test_ops_case_metadata_matches_the_v2_identity_and_recovery_contract() -> None:
    case_table = OpsCaseRecord.__table__
    decision_table = OpsDecisionRecord.__table__
    recovery_table = OpsCaseRecoveryEventRecord.__table__
    audit_table = AuditEventRecord.__table__

    assert case_table.c.case_id.type.length == 768
    assert case_table.c.teacher_id.nullable is True
    assert case_table.c.source_ref.type.length == 768
    assert case_table.c.source_region.type.length == 8
    assert case_table.c.source_appoint_id.type.length == 512
    assert case_table.c.case_revision.nullable is False
    assert case_table.c.row_version.nullable is False
    assert case_table.c.recovery_evidence_count.nullable is False
    assert audit_table.c.case_id.type.length == 768

    case_constraints = {
        constraint.name for constraint in case_table.constraints
    }
    assert {
        "ck_ops_case_v2_versions",
        "ck_ops_case_v2_region",
        "ck_ops_case_v2_subject",
        "ck_ops_case_v2_completion_identity",
        "ck_ops_case_v2_technical_shape",
        "ck_ops_case_v2_technical_source_ref",
    }.issubset(case_constraints)
    case_indexes = {index.name for index in case_table.indexes}
    assert {
        "uq_ops_cases_source_ref_v2",
        "uq_ops_cases_completion_course_v2",
        "ix_ops_cases_source_course_v2",
    }.issubset(case_indexes)

    assert decision_table.c.case_id.type.length == 768
    assert decision_table.c.downstream_projection_status.type.length == 24
    assert decision_table.c.projection_event_ids.nullable is True
    decision_constraints = {
        constraint.name for constraint in decision_table.constraints
    }
    assert {
        "ck_ops_decision_v2_row_version",
        "ck_ops_decision_projection_status_v2",
        "ck_ops_decision_projection_events_v2",
    }.issubset(decision_constraints)

    assert recovery_table.comment == (
        "Append-only evidence that the original DTS v2 work reached "
        "PUBLISHED; DEAD to PENDING alone never creates this row."
    )
    recovery_constraints = {
        constraint.name for constraint in recovery_table.constraints
    }
    assert {
        "pk_ops_case_recovery_events",
        "uq_ops_case_recovery_work_generation",
        "fk_ops_case_recovery_case",
        "ck_ops_case_recovery_versions",
        "ck_ops_case_recovery_work_status",
        "ck_ops_case_recovery_hashes",
    }.issubset(recovery_constraints)


def test_outbox_worker_requires_the_dedicated_runtime_role_and_orders_proofs() -> None:
    worker_source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "dts_v2_outbox_worker.py"
    ).read_text(encoding="utf-8")

    assert '_OUTBOX_RUNTIME_ROLE = "tit_dts_outbox_worker_runtime"' in worker_source
    assert "DTS_V2_OUTBOX_RUNTIME_ROLE_REQUIRED" in worker_source
    assert worker_source.index("_publish_locked(connection, event)") < (
        worker_source.index("self.technical_cases.resolve_after_success")
    )
    assert worker_source.index("_dead_letter_locked(") < worker_source.index(
        "self.technical_cases.record_dead_letter("
    )
