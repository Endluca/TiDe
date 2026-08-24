from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from app import db_models


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_80_dts_v2_dirty_queue.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("dts_dirty_queue_v80", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_80_installs_region_identity_state_machine_and_acl(
    monkeypatch,
) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
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
        lambda name, table, columns, **_kwargs: indexes.setdefault(
            name, (table, tuple(columns))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_80_dts_v2_dirty_queue"
    assert migration.down_revision == "20260822_79_dts_v2_epoch_control"
    assert set(created) == {
        "dts_dirty_keys_legacy_archive_v80",
        "dts_dirty_key_inputs",
        "dts_dirty_key_dependencies",
        "dts_dirty_key_state_audits",
    }
    assert indexes["ix_dts_dirty_key_dependencies_reverse"] == (
        "dts_dirty_key_dependencies",
        ("dependency_type", "dependency_region", "dependency_key"),
    )

    sql = "\n".join(executed)
    assert "DIRTY_V2_MIGRATION_LEGACY_NOT_DRAINED" in sql
    assert "dts_dirty_keys_legacy_archive_v80" in sql
    assert "public.dts_canonical_json_sha256_v1(to_jsonb(dirty))" in sql
    assert "DELETE FROM public.dts_dirty_keys" in sql
    assert "SET source_region = last_source_region" not in sql
    assert "PRIMARY KEY (\n                source_region" in sql
    assert "^dom:v1:[0-9a-f]{64}$" in sql
    assert "TEACHER_TIME_RECHECK" in sql
    assert "WAITING_DEPENDENCY" in sql
    assert "required_work_revision" in sql
    assert "claimed_through_work_revision" in sql
    assert "completed_work_revision" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "DIRTY_LEASE_LOST" in sql
    assert "power(2,next_attempt-1)" in sql
    assert "next_attempt=8 THEN 'DEAD'" in sql
    assert "DEAD_REOPENED_BY_INPUT" in sql
    assert "DIRTY_REQUIRED_REVISION_MISMATCH" in sql
    assert "CREATE FUNCTION public.enqueue_dirty_from_source_revision_v2" in sql
    assert "CREATE FUNCTION public.claim_domain_dirty_keys_v2" in sql
    assert "CREATE FUNCTION public.renew_domain_dirty_key_v2" in sql
    assert "CREATE FUNCTION public.complete_domain_dirty_key_v2" in sql
    assert "CREATE FUNCTION public.wait_domain_dirty_key_v2" in sql
    assert "CREATE FUNCTION public.fail_domain_dirty_key_v2" in sql
    assert "CREATE FUNCTION public.reap_expired_domain_dirty_keys_v2" in sql
    assert "CREATE FUNCTION public.recover_dts_dirty_key_v2" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE" in sql
    assert "tit_dts_domain_projector_runtime" in sql
    assert "restricted NOINHERIT LOGIN role" in sql


def test_v2_dirty_queue_orm_has_region_qualified_keys_and_typed_children() -> None:
    dirty_pk = tuple(
        column.name
        for column in db_models.DtsDirtyKeyRecord.__table__.primary_key.columns
    )
    assert dirty_pk == (
        "source_region",
        "key_type",
        "key_part_1",
        "key_part_2",
    )
    assert db_models.DtsDirtyKeyRecord.__table__.c.source_region.nullable is False
    assert (
        db_models.DtsDirtyKeyRecord.__table__.c.required_work_revision.nullable
        is False
    )
    assert db_models.DtsDirtyKeyInputRecord.__tablename__ == (
        "dts_dirty_key_inputs"
    )
    assert db_models.DtsDirtyKeyLegacyArchiveV80Record.__tablename__ == (
        "dts_dirty_keys_legacy_archive_v80"
    )
    assert db_models.DtsDirtyKeyDependencyRecord.__tablename__ == (
        "dts_dirty_key_dependencies"
    )
    assert db_models.DtsDirtyKeyStateAuditRecord.__tablename__ == (
        "dts_dirty_key_state_audits"
    )
    input_constraints = {
        constraint.name
        for constraint in db_models.DtsDirtyKeyInputRecord.__table__.constraints
    }
    assert "uq_dts_dirty_key_input_work_revision" in input_constraints
    assert "fk_dts_dirty_key_input_key" in input_constraints
    dependency_indexes = {
        index.name
        for index in db_models.DtsDirtyKeyDependencyRecord.__table__.indexes
    }
    assert dependency_indexes == {"ix_dts_dirty_key_dependencies_reverse"}
    assert isinstance(
        db_models.DtsDirtyKeyStateAuditRecord.__table__.c.audit_id.type,
        sa.BigInteger,
    )
