from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260729_37_read_path_indexes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "read_path_indexes_v37",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_v37_indexes_and_parks_only_unconsumed_output_retry_intents(
    monkeypatch,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260729_36_perfect_score"

    created_indexes: list[str] = []
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, *_args, **_kwargs: created_indexes.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert created_indexes == [
        "ix_lesson_dimension_score_teacher_lesson",
        "ix_personalized_trigger_match_active_output",
        "ix_outbox_pending_settlement_claim",
        "ix_audit_events_teacher_sequence",
    ]
    sql = "\n".join(executed)
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert "ix_audit_events_structured_search_trgm" in sql
    assert "status = 'PARKED'" in sql
    assert "event_type = 'outbound_output.retry_requested.v1'" in sql
    assert "task.assignment_changed.shared" not in sql


def test_v37_downgrade_removes_only_its_indexes(monkeypatch) -> None:
    migration = _load_migration()
    dropped_indexes: list[str] = []
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **_kwargs: dropped_indexes.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.downgrade()

    rollback_sql = "\n".join(executed)
    assert "previous_status" in rollback_sql
    assert "previous_last_error" in rollback_sql
    assert "payload = payload - '_migration_20260729_37'" in rollback_sql
    assert dropped_indexes == [
        "ix_audit_events_structured_search_trgm",
        "ix_audit_events_teacher_sequence",
        "ix_outbox_pending_settlement_claim",
        "ix_personalized_trigger_match_active_output",
        "ix_lesson_dimension_score_teacher_lesson",
    ]
