from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.db_models import DtsDirtyKeyRecord


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260818_62_dts_claim_idx.py"
    )
    spec = importlib.util.spec_from_file_location(
        "dts_claim_index_v62",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _AutocommitContext:
    def __init__(self) -> None:
        self.entries = 0

    @contextmanager
    def autocommit_block(self):
        self.entries += 1
        yield


def test_v62_builds_state_specific_claim_indexes_concurrently(monkeypatch) -> None:
    migration = _load_migration()
    context = _AutocommitContext()
    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    dropped: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(migration.op, "get_context", lambda: context)
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: dropped.append((args, kwargs)),
    )

    migration.upgrade()

    assert migration.revision == "20260818_62_dts_claim_idx"
    assert migration.down_revision == "20260814_61_teacher_copy"
    assert context.entries == 1
    assert [call[0][0] for call in dropped] == [
        "ix_dts_dirty_keys_retry_due",
        "ix_dts_dirty_keys_pending_fifo",
    ]
    assert all(
        call[1] == {
            "table_name": "dts_dirty_keys",
            "schema": "public",
            "if_exists": True,
            "postgresql_concurrently": True,
        }
        for call in dropped
    )
    assert [call[0][0] for call in created] == [
        "ix_dts_dirty_keys_pending_fifo",
        "ix_dts_dirty_keys_retry_due",
    ]
    assert created[0][0][1:] == (
        "dts_dirty_keys",
        ["last_seen_at", "key_type", "key_part_1", "key_part_2"],
    )
    assert created[1][0][1:] == (
        "dts_dirty_keys",
        [
            "next_attempt_at",
            "last_seen_at",
            "key_type",
            "key_part_1",
            "key_part_2",
        ],
    )
    assert all(
        call[1]["schema"] == "public"
        and call[1]["unique"] is False
        and call[1]["postgresql_concurrently"] is True
        and "if_not_exists" not in call[1]
        for call in created
    )
    assert str(created[0][1]["postgresql_where"]) == "status = 'PENDING'"
    assert str(created[1][1]["postgresql_where"]) == (
        "status = 'RETRY' AND next_attempt_at < 'infinity'::timestamptz"
    )


def test_v62_drops_claim_indexes_concurrently_in_reverse_order(monkeypatch) -> None:
    migration = _load_migration()
    context = _AutocommitContext()
    dropped: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(migration.op, "get_context", lambda: context)
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: dropped.append((args, kwargs)),
    )

    migration.downgrade()

    assert context.entries == 1
    assert [call[0][0] for call in dropped] == [
        "ix_dts_dirty_keys_retry_due",
        "ix_dts_dirty_keys_pending_fifo",
    ]
    assert all(
        call[1] == {
            "table_name": "dts_dirty_keys",
            "schema": "public",
            "if_exists": True,
            "postgresql_concurrently": True,
        }
        for call in dropped
    )


def test_dirty_key_orm_declares_exact_partial_index_shapes() -> None:
    indexes = {
        index.name: index for index in DtsDirtyKeyRecord.__table__.indexes
    }

    pending = indexes["ix_dts_dirty_keys_pending_fifo_v2"]
    retry = indexes["ix_dts_dirty_keys_retry_due_v2"]
    assert tuple(column.name for column in pending.columns) == (
        "next_attempt_at",
        "updated_at",
        "source_region",
        "key_type",
        "key_part_1",
        "key_part_2",
    )
    assert str(pending.dialect_options["postgresql"]["where"]) == (
        "status = 'PENDING'"
    )
    assert tuple(column.name for column in retry.columns) == (
        "next_attempt_at",
        "updated_at",
        "source_region",
        "key_type",
        "key_part_1",
        "key_part_2",
    )
    assert str(retry.dialect_options["postgresql"]["where"]) == "status = 'RETRY'"
