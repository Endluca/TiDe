from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app import db_models


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260812_57_dts_ingest_state.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("dts_ingest_state_v57", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_57_creates_durable_state_and_exact_acl(monkeypatch) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    indexes: set[tuple[str, str]] = set()
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *items, **_kwargs: created.setdefault(name, items),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, *_args, **_kwargs: indexes.add((name, table)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()
    sql = "\n".join(executed)

    assert migration.revision == "20260812_57_dts_state"
    assert migration.down_revision == "20260812_56_lean_roles"
    assert set(created) == set(migration.DTS_STATE_TABLES)
    assert indexes == {
        ("ix_dts_ingest_events_source_table_processed", "dts_ingest_events"),
        ("ix_dts_source_rows_table_active", "dts_source_rows"),
        ("ix_dts_source_rows_dependency_keys", "dts_source_rows"),
        ("ix_dts_dirty_keys_ready", "dts_dirty_keys"),
    }
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE" in sql
    assert "TO tit_dts_ingest_runtime" in sql
    assert "non-DTS runtime can access ingest state" in sql
    assert "DTS runtime may not erase ingest history" in sql
    assert "GRANT DELETE" not in sql


def test_runtime_orm_matches_the_four_dts_state_tables() -> None:
    assert db_models.DtsIngestCheckpointRecord.__tablename__ == (
        "dts_ingest_checkpoints"
    )
    assert db_models.DtsIngestEventRecord.__tablename__ == "dts_ingest_events"
    assert db_models.DtsSourceRowRecord.__tablename__ == "dts_source_rows"
    assert db_models.DtsDirtyKeyRecord.__tablename__ == "dts_dirty_keys"
    assert {
        "source_region",
        "topic",
        "partition_id",
        "offset_value",
    } == {
        column.name
        for column in db_models.DtsIngestEventRecord.__table__.primary_key.columns
    }
    assert {
        "source_region",
        "key_type",
        "key_part_1",
        "key_part_2",
    } == {
        column.name
        for column in db_models.DtsDirtyKeyRecord.__table__.primary_key.columns
    }
    assert "source_row" in db_models.DtsSourceRowRecord.__table__.columns
    assert "last_record_id" in db_models.DtsSourceRowRecord.__table__.columns
    ledger_indexes = {
        index.name: index
        for index in db_models.DtsIngestEventRecord.__table__.indexes
    }
    assert "ix_dts_ingest_events_source_table_processed" not in ledger_indexes
    ledger_brin = ledger_indexes["ix_dts_ingest_events_processed_at_brin"]
    assert tuple(column.name for column in ledger_brin.columns) == (
        "processed_at",
    )
    assert ledger_brin.dialect_options["postgresql"]["using"] == "brin"
    assert ledger_brin.dialect_options["postgresql"]["with"] == {
        "pages_per_range": 64
    }


def test_revision_57_refuses_to_drop_persisted_replay_state(monkeypatch) -> None:
    migration = _load_migration()
    executed: list[str] = []
    dropped: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )
    monkeypatch.setattr(migration.op, "drop_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda name, **_kwargs: dropped.append(name),
    )

    migration.downgrade()

    assert "refusing DTS state downgrade" in "\n".join(executed)
    assert dropped == [
        "dts_dirty_keys",
        "dts_source_rows",
        "dts_ingest_events",
        "dts_ingest_checkpoints",
    ]
