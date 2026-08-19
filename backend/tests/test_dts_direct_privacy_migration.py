from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260819_63_dts_direct_privacy.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_direct_privacy_v63",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_63_accepts_only_labeled_direct_runtime_writes(
    monkeypatch,
) -> None:
    migration = _load_migration()
    executed: list[str] = []
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

    migration.upgrade()
    sql = "\n".join(executed)

    assert migration.revision == "20260819_63_dts_direct_privacy"
    assert migration.down_revision == "20260818_62_dts_claim_idx"
    assert "current_setting('tit.dts_source_region', true)" in sql
    assert "actor_name <> 'tit_dts_ingest_runtime'" in sql
    assert "direct_source_region NOT IN ('dom', 'ovs')" in sql
    assert "has_dom_source OR has_ovs_source" in sql
    assert "^dom:v1:[0-9a-f]{64}$" in sql
    assert "direct_source_region = 'ovs'" in sql
    assert "NEW.\"学员id\" LIKE 'dom:%'" in sql
    assert "persisted source provenance or direct DTS region" in sql


def test_revision_63_is_noop_off_postgresql(monkeypatch) -> None:
    migration = _load_migration()
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert executed == []


def test_revision_63_is_forward_only() -> None:
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
