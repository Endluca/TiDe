from __future__ import annotations

import importlib.util
from pathlib import Path

from app.db_models import Base


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "migrations/versions/20260822_85_outbox_three_state.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_outbox_three_state", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_and_three_state_boundary_are_explicit() -> None:
    module = _module()
    source = MIGRATION.read_text(encoding="utf-8")
    assert module.revision == "20260822_85_outbox_three_state"
    assert module.down_revision == "20260822_84a_lesson_region_contract"
    assert "status IN ('PENDING','PUBLISHED','DEAD_LETTER')" in source
    assert "OUTBOX_INITIAL_STATE_INVALID" in source
    assert "OUTBOX_RECOVERY_DENIED" in source
    assert "OUTBOX_RECOVERY_NOT_READY" in source
    assert "LEGACY_OUTBOX_STATUS_CONFLICT" in source
    assert "LEGACY_OUTBOX_PAYLOAD_UNSAFE" in source
    assert "CANCELLED','PARKED" in source
    assert "UPDATE public.outbox_events\n            SET status='PENDING'" in source


def test_pending_same_state_is_only_a_strict_retry() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    guard = source.split(
        "CREATE OR REPLACE FUNCTION public.guard_outbox_event_update()", 1
    )[1].split("CREATE FUNCTION public.guard_outbox_event_insert_v2()", 1)[0]
    assert "NEW.row_version IS DISTINCT FROM OLD.row_version + 1" in guard
    assert "NEW.attempt_count IS DISTINCT FROM\n                        OLD.attempt_count + 1" in guard
    assert "NEW.available_at <= transaction_timestamp()" in guard
    assert "NEW.last_error IS NULL" in guard
    assert "NEW.recovery_count IS DISTINCT FROM OLD.recovery_count" in guard
    assert "NEW.recovered_at IS DISTINCT FROM OLD.recovered_at" in guard


def test_archive_and_recovery_are_typed_audited_commands() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "MOCK_SEED_CANCELLED" in source
    assert "MIGRATION_20260729_37_RETRY_PARKED" in source
    assert "fixed_task_award_count=0" in source
    assert "LEGACY_OUTBOX_ARCHIVED_V2" in source
    assert "OUTBOX_EVENT_RECOVERED_V2" in source
    assert "scope='OUTBOX_RECOVERY_V2'" in source
    assert "expires_at IS NOT NULL" in source
    assert "OUTBOX_RECOVERY_COMMAND_CONFLICT" in source
    assert "OUTBOX_RECOVERY_REPLAY_INVALID" in source
    assert "OUTBOX_RECOVERY_STALE" in source
    assert "REVOKE ALL ON FUNCTION public.recover_outbox_event_v2" in source
    assert "tit_dts_outbox_recovery_runtime" in source
    assert "tit_dts_cutover_migration" in source


def test_outbox_orm_matches_rev84_and_rev85_shapes() -> None:
    active = Base.metadata.tables["outbox_events"]
    archive = Base.metadata.tables["outbox_events_legacy_archive"]
    assert active.c.outbox_id.type.length == 160
    assert active.c.event_id.type.length == 512
    assert active.c.aggregate_id.type.length == 160
    assert active.c.payload_sha256.type.length == 64
    assert active.c.recovery_count.nullable is False
    assert active.c.row_version.nullable is False
    assert active.c.settled_by_run_id.type.length == 160
    assert active.comment == (
        "Active v2 Outbox work. Only PENDING, PUBLISHED, and DEAD_LETTER "
        "are valid; DEAD_LETTER recovery is an audited command."
    )
    assert archive.c.event_id.type.length == 512
    assert archive.c.archive_source_row_hash.type.length == 64
    assert archive.c.proof_hash.type.length == 64
    assert archive.c.archive_audit_event_id.foreign_keys
    assert archive.comment.startswith("Append-only audit archive")
    assert "ix_outbox_dead_letter_v2" in {
        index.name for index in active.indexes
    }
    assert {
        constraint.name for constraint in archive.constraints
    }.issuperset(
        {
            "ck_outbox_legacy_archive_status",
            "ck_outbox_legacy_archive_hashes",
            "ck_outbox_legacy_archive_proof_reason",
            "ck_outbox_legacy_archive_state",
            "fk_outbox_legacy_archive_audit_event",
        }
    )
