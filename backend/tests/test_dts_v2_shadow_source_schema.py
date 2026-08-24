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
    / "20260822_66_dts_v2_shadow_source.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_shadow_source_v66",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _columns(items: tuple[object, ...]) -> dict[str, sa.Column]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.Column) and item.name is not None
    }


def _checks(items: tuple[object, ...]) -> dict[str, sa.CheckConstraint]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.CheckConstraint) and item.name is not None
    }


def test_revision_66_is_additive_inert_and_append_only(monkeypatch) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    added: dict[str, list[sa.Column]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...], dict[str, object]]] = {}
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
        "add_column",
        lambda table, column, **_kwargs: added.setdefault(table, []).append(column),
    )

    def _capture_index(
        name: str,
        table: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        indexes[name] = (table, tuple(columns), kwargs)

    monkeypatch.setattr(migration.op, "create_index", _capture_index)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_66_dts_v2_shadow"
    assert migration.down_revision == "20260822_65a_lesson_region_exp"
    assert set(created) == {
        "dts_source_partition_epochs",
        "dts_source_row_versions",
    }
    assert {
        table: tuple(column.name for column in columns)
        for table, columns in added.items()
    } == {
        "dts_ingest_events": migration.EVENT_TRANSITION_COLUMNS,
        "dts_ingest_checkpoints": migration.CHECKPOINT_TRANSITION_COLUMNS,
        "dts_source_rows": migration.SOURCE_ROW_TRANSITION_COLUMNS,
        "dts_dirty_keys": migration.DIRTY_KEY_TRANSITION_COLUMNS,
    }
    assert all(column.nullable for columns in added.values() for column in columns)
    source_row_transition = {
        column.name: column for column in added["dts_source_rows"]
    }
    assert source_row_transition["source_schema_profile_id"].type.length == 160
    assert (
        source_row_transition["source_field_types"].type.__class__.__name__
        == "JSONB"
    )

    version_columns = _columns(created["dts_source_row_versions"])
    assert isinstance(version_columns["record_id_numeric"].type, sa.Numeric)
    assert isinstance(version_columns["source_key_numeric"].type, sa.Numeric)
    assert version_columns["source_key_data"].nullable is False
    assert version_columns["source_key_type"].nullable is False
    assert version_columns["source_schema_profile_id"].nullable is False
    assert version_columns["source_schema_profile_id"].type.length == 160
    assert version_columns["source_field_types"].nullable is False
    assert version_columns["source_field_types"].type.__class__.__name__ == "JSONB"
    assert {"diff_step", "source_table_publish_generation"} <= set(
        version_columns
    )
    epoch_columns = _columns(created["dts_source_partition_epochs"])
    assert epoch_columns["row_version"].nullable is False
    epoch_shape = str(
        _checks(created["dts_source_partition_epochs"])[
            "ck_dts_source_partition_epoch_shape"
        ].sqltext
    )
    for required_field in (
        "epoch_sequence",
        "start_offset",
        "v2_epoch_bootstrap_floor",
    ):
        assert f"{required_field} IS NOT NULL" in epoch_shape

    version_checks = _checks(created["dts_source_row_versions"])
    source_key_check = str(
        version_checks["ck_dts_source_row_version_source_key"].sqltext
    )
    assert "trim_scale(source_key_numeric)::text" in source_key_check
    assert "source_key_data -> 'id'" in source_key_check
    assert "record_id" not in source_key_check
    assert str(
        version_checks[
            "ck_dts_source_row_version_schema_profile"
        ].sqltext
    ) == "btrim(source_schema_profile_id) <> ''"
    assert str(
        version_checks["ck_dts_source_row_version_field_types"].sqltext
    ) == "jsonb_typeof(source_field_types) = 'object'"
    lifecycle = str(
        version_checks["ck_dts_source_row_version_lifecycle"].sqltext
    )
    assert "source_row_revision IS NOT NULL" in lifecycle
    diff_step = str(version_checks["ck_dts_source_row_version_diff_step"].sqltext)
    assert "diff_step IS NOT NULL" in diff_step
    assert "source_table_publish_generation IS NOT NULL" in diff_step

    baseline_index = indexes["uq_dts_source_row_version_snapshot_key"]
    assert baseline_index[2]["unique"] is True
    assert str(baseline_index[2]["postgresql_where"]) == (
        "version_kind = 'BASELINE'"
    )
    diff_index = indexes[
        "uq_dts_source_row_version_snapshot_diff_key_step"
    ]
    assert diff_index[1][-1] == "diff_step"
    assert str(diff_index[2]["postgresql_where"]) == (
        "version_kind = 'SNAPSHOT_DIFF'"
    )

    sql = "\n".join(executed)
    assert "no epoch bootstrap or activation path installed" in sql
    assert "no runtime writer granted" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "DTS_SOURCE_ROW_VERSION_IMMUTABLE" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE" in sql
    assert "tit_dts_ingest_runtime" in sql
    assert "GRANT " not in sql
    assert "INSERT INTO" not in sql.upper()


def test_revision_66_orm_matches_shadow_tables_and_transition_columns() -> None:
    transition_columns = {
        db_models.DtsIngestEventRecord: {
            "identity_version",
            "source_partition_epoch_id",
            "source_position_v2",
            "event_payload_hash",
        },
        db_models.DtsIngestCheckpointRecord: {
            "source_partition_epoch_id",
            "consumer_group",
            "checkpoint_row_version",
            "is_current_epoch",
        },
        db_models.DtsSourceRowRecord: {
            "source_row_revision",
            "last_source_partition_epoch_id",
            "last_version_kind",
            "source_position_v2",
            "record_id_type",
            "record_id_numeric",
            "record_id_text",
            "source_timestamp_v2",
            "source_payload_hash",
            "provenance_state",
            "source_key_type",
            "source_key_numeric",
            "source_key_text",
            "source_schema_profile_id",
            "source_field_types",
        },
        db_models.DtsDirtyKeyRecord: {
            "source_region",
            "required_work_revision",
            "claimed_through_work_revision",
            "completed_work_revision",
            "last_input_identity_hash",
            "last_input_revision",
            "work_generation",
            "dead_generation",
            "blocked_by",
            "lease_owner_kind",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
        },
    }
    for model, expected in transition_columns.items():
        assert expected <= set(model.__table__.columns.keys())
        if model is db_models.DtsDirtyKeyRecord:
            hardened_by_revision_80 = {
                "source_region",
                "required_work_revision",
                "completed_work_revision",
                "last_input_identity_hash",
                "last_input_revision",
                "work_generation",
                "dead_generation",
            }
            assert all(
                not model.__table__.c[name].nullable
                for name in hardened_by_revision_80
            )
            assert all(
                model.__table__.c[name].nullable
                for name in expected - hardened_by_revision_80
            )
        else:
            assert all(model.__table__.c[name].nullable for name in expected)

    migration = _load_migration()
    assert tuple(db_models.DtsIngestEventRecord.__table__.columns.keys())[
        -len(migration.EVENT_TRANSITION_COLUMNS) :
    ] == migration.EVENT_TRANSITION_COLUMNS
    assert tuple(db_models.DtsIngestCheckpointRecord.__table__.columns.keys())[
        -len(migration.CHECKPOINT_TRANSITION_COLUMNS) :
    ] == migration.CHECKPOINT_TRANSITION_COLUMNS
    assert tuple(db_models.DtsSourceRowRecord.__table__.columns.keys())[
        -len(migration.SOURCE_ROW_TRANSITION_COLUMNS) :
    ] == migration.SOURCE_ROW_TRANSITION_COLUMNS
    # Revision 80 promotes source_region into the leading primary key and
    # appends created_at/updated_at, so the revision-66 transition columns are
    # no longer required to remain the ORM's final contiguous suffix.
    assert set(migration.DIRTY_KEY_TRANSITION_COLUMNS) <= set(
        db_models.DtsDirtyKeyRecord.__table__.columns.keys()
    )

    current = db_models.DtsSourceRowRecord.__table__
    epoch = db_models.DtsSourcePartitionEpochRecord.__table__
    version = db_models.DtsSourceRowVersionRecord.__table__
    assert {
        "source_region",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
    } == {column.name for column in epoch.primary_key.columns}
    assert {
        "source_region",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
        "offset_value",
    } == {column.name for column in version.primary_key.columns}
    assert isinstance(version.c.record_id_numeric.type, sa.Numeric)
    assert isinstance(version.c.source_key_numeric.type, sa.Numeric)
    assert version.c.source_key_data.nullable is False
    assert version.c.source_key_type.nullable is False
    assert version.c.source_schema_profile_id.nullable is False
    assert version.c.source_schema_profile_id.type.length == 160
    assert version.c.source_field_types.nullable is False
    assert (
        db_models.DtsSourceRowRecord.__table__.c.source_schema_profile_id.type.length
        == 160
    )
    source_key_check = next(
        constraint
        for constraint in version.constraints
        if constraint.name == "ck_dts_source_row_version_source_key"
    )
    source_key_sql = str(source_key_check.sqltext)
    assert "jsonb_typeof(source_key_data)" in source_key_sql
    assert "source_key_type = 'NUMERIC'" in source_key_sql
    assert "source_key_type = 'TEXT'" in source_key_sql
    assert "trim_scale(source_key_numeric)::text" in source_key_sql
    assert "record_id" not in source_key_sql
    schema_profile_check = next(
        constraint
        for constraint in version.constraints
        if constraint.name == "ck_dts_source_row_version_schema_profile"
    )
    assert str(schema_profile_check.sqltext) == (
        "btrim(source_schema_profile_id) <> ''"
    )
    field_types_check = next(
        constraint
        for constraint in version.constraints
        if constraint.name == "ck_dts_source_row_version_field_types"
    )
    assert str(field_types_check.sqltext) == (
        "jsonb_typeof(source_field_types) = 'object'"
    )
    field_type_values_check = next(
        constraint
        for constraint in version.constraints
        if constraint.name
        == "ck_dts_source_row_version_field_type_values"
    )
    field_type_values_sql = str(field_type_values_check.sqltext)
    assert "dts_v2_source_field_types_valid(source_field_types)" in (
        field_type_values_sql
    )
    assert "source_field_types ? 'id'" in field_type_values_sql
    assert "IS NOT DISTINCT FROM source_key_type" in field_type_values_sql
    transition_shape_check = next(
        constraint
        for constraint in current.constraints
        if constraint.name == "ck_dts_source_row_v2_transition_shape"
    )
    transition_shape_sql = str(transition_shape_check.sqltext)
    assert "dts_v2_source_row_transition_valid(" in transition_shape_sql
    for column_name in (
        "source_key",
        "source_key_data",
        "source_row_revision",
        "last_source_partition_epoch_id",
        "source_field_types",
    ):
        assert column_name in transition_shape_sql
    assert {
        "uq_dts_source_row_version_snapshot_key",
        "uq_dts_source_row_version_snapshot_diff_key_step",
        "uq_dts_source_row_version_source_revision",
        "ix_dts_source_row_versions_source_order",
    } <= {index.name for index in version.indexes}


def test_legacy_schema_validator_recognizes_arbitrary_precision_numeric() -> None:
    from app.dts_ingest_store import (
        DTS_V2_TRANSITION_COLUMN_NAMES,
        _postgres_column_type,
    )

    assert _postgres_column_type(sa.Numeric()) == "numeric"
    migration = _load_migration()
    assert DTS_V2_TRANSITION_COLUMN_NAMES == {
        "dts_ingest_events": frozenset(migration.EVENT_TRANSITION_COLUMNS),
        "dts_ingest_checkpoints": frozenset(
            migration.CHECKPOINT_TRANSITION_COLUMNS
        ),
        "dts_source_rows": frozenset(migration.SOURCE_ROW_TRANSITION_COLUMNS),
        "dts_dirty_keys": frozenset(migration.DIRTY_KEY_TRANSITION_COLUMNS),
    }


def test_revision_66_downgrade_locks_before_checking_shadow_data(
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
    monkeypatch.setattr(migration.op, "drop_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "drop_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "drop_table", lambda *_args, **_kwargs: None)

    migration.downgrade()

    guard_sql = executed[0]
    assert guard_sql.index("LOCK TABLE") < guard_sql.index("IF EXISTS")
    assert "IN ACCESS EXCLUSIVE MODE" in guard_sql
    for columns in (
        migration.EVENT_TRANSITION_COLUMNS,
        migration.CHECKPOINT_TRANSITION_COLUMNS,
        migration.SOURCE_ROW_TRANSITION_COLUMNS,
        migration.DIRTY_KEY_TRANSITION_COLUMNS,
    ):
        for column_name in columns:
            assert column_name in guard_sql
