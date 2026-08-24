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
    / "20260822_79_dts_v2_epoch_control.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_epoch_control_v79",
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


def test_revision_79_is_additive_and_installs_one_atomic_bootstrap(
    monkeypatch,
) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...], dict[str, object]]] = {}
    constraints: list[tuple[tuple[object, ...], dict[str, object]]] = []
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
        "create_check_constraint",
        lambda *args, **kwargs: constraints.append((args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_79_dts_v2_epoch_control"
    assert migration.down_revision == "20260822_78_pending_score_guard"
    assert set(created) == {
        "dts_pipeline_control",
        "dts_pipeline_bootstrap_audits",
        "dts_ingest_issues",
    }
    assert indexes["ix_dts_ingest_issues_open_seen"][0] == "dts_ingest_issues"
    assert str(
        indexes["ix_dts_ingest_issues_open_seen"][2]["postgresql_where"]
    ) == "status = 'OPEN'"
    assert constraints == [
        (
            (
                "ck_dts_ingest_issue_derived_id",
                "dts_ingest_issues",
                "ingest_issue_id = public.dts_ingest_issue_id_v2("
                "connector_delivery_identity_hash, payload_hmac, "
                "hmac_key_version)",
            ),
            {"schema": "public"},
        )
    ]

    control_columns = _columns(created["dts_pipeline_control"])
    assert control_columns["control_id"].nullable is False
    assert control_columns["initial_h0_vector"].type.__class__.__name__ == "JSONB"
    assert control_columns["initial_h0_vector_hash"].type.length == 64
    assert control_columns["projection_generation"].nullable is False
    control_checks = _checks(created["dts_pipeline_control"])
    assert "V1_COMPAT_DUAL_CAPTURE" in str(
        control_checks["ck_dts_pipeline_control_mode"].sqltext
    )
    assert "control_id = 'PRIMARY'" == str(
        control_checks["ck_dts_pipeline_control_singleton"].sqltext
    )

    issue_columns = _columns(created["dts_ingest_issues"])
    assert "raw_payload" not in issue_columns
    assert "student_id" not in issue_columns
    assert "hmac_key" not in issue_columns
    assert issue_columns["diagnostic_summary"].type.__class__.__name__ == "JSONB"

    sql = "\n".join(executed)
    assert "CREATE FUNCTION public.bootstrap_initial_broker_epoch_v2(" in sql
    assert "SECURITY DEFINER" in sql
    assert "IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "dts_normalize_initial_broker_epoch_vector_v2" in sql
    assert "dts_initial_broker_epoch_vector_hash_v2" in sql
    assert "INITIAL_EPOCH_BOOTSTRAP_CONFLICT" in sql
    assert "CHECKPOINT_VECTOR_MISMATCH" in sql
    assert "MANIFEST_HASH_MISMATCH" in sql
    assert "V1_COMPAT_DUAL_CAPTURE" in sql
    assert "'status', 'NOOP'" in sql
    assert ") <> 8" in sql
    assert "'consumer_group', route_item ->> 'consumer_group'" in sql
    assert "route.value ->> 'consumer_group'" in sql
    assert "consumer_group = route_item ->> 'consumer_group'" in sql
    assert "p_consumer_group is the bootstrap fleet identity" in sql
    assert "source_partition_epoch_id =" in sql
    assert "checkpoint_row_version = 1" in sql
    assert "is_current_epoch = true" in sql
    assert "activation_manifest_hash" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert migration.BOOTSTRAP_FUNCTION_SIGNATURE in sql
    assert "GRANT EXECUTE" not in sql
    assert "raw payload" in sql


def test_revision_79_downgrade_is_empty_only_and_drops_its_objects(
    monkeypatch,
) -> None:
    migration = _load_migration()
    operations: list[tuple[str, object]] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: operations.append(("execute", str(statement))),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: operations.append(
            ("drop_constraint", (args, kwargs))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: operations.append(("drop_index", (args, kwargs))),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda *args, **kwargs: operations.append(("drop_table", (args, kwargs))),
    )

    migration.downgrade()

    guard = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in guard
    assert "refusing DTS v2 epoch-control downgrade" in guard
    assert "activation_mode = 'H0_BOOTSTRAP'" in guard
    assert "source_partition_epoch_id IS NOT NULL" in guard
    assert [
        operation[1][0][0]
        for operation in operations
        if operation[0] == "drop_table"
    ] == [
        "dts_ingest_issues",
        "dts_pipeline_bootstrap_audits",
        "dts_pipeline_control",
    ]
    assert "dts_broker_epoch_id_v2" in str(operations[-1][1])


def test_revision_79_models_match_control_and_issue_schema() -> None:
    assert db_models.DtsPipelineControlRecord.__tablename__ == (
        "dts_pipeline_control"
    )
    assert db_models.DtsPipelineBootstrapAuditRecord.__tablename__ == (
        "dts_pipeline_bootstrap_audits"
    )
    assert db_models.DtsIngestIssueRecord.__tablename__ == "dts_ingest_issues"

    control = db_models.DtsPipelineControlRecord.__table__
    assert tuple(control.primary_key.columns.keys()) == ("control_id",)
    assert {
        "mode",
        "projection_generation",
        "consumer_group",
        "initial_h0_vector",
        "initial_h0_vector_hash",
        "initial_h0_bootstrap_run_id",
        "time_catchup_status",
    } <= set(control.columns.keys())
    assert control.c.initial_h0_vector.nullable is False
    assert control.comment == (
        "Protected singleton DTS mode and immutable initial H0 vector; "
        "created only by whole-vector bootstrap."
    )
    assert control.c.consumer_group.comment == (
        "Bootstrap fleet identity; per-subscription consumer groups are "
        "stored in initial_h0_vector routes"
    )

    audit = db_models.DtsPipelineBootstrapAuditRecord.__table__
    assert tuple(audit.primary_key.columns.keys()) == ("bootstrap_run_id",)
    assert len(audit.foreign_keys) == 1
    assert next(iter(audit.foreign_keys)).target_fullname == (
        "dts_pipeline_control.control_id"
    )
    assert audit.comment == (
        "Append-only initial broker epoch bootstrap audit; one successful "
        "PRIMARY H0 command."
    )
    assert audit.c.consumer_group.comment == (
        "Bootstrap fleet identity, not a route consumer group"
    )

    issue = db_models.DtsIngestIssueRecord.__table__
    assert tuple(issue.primary_key.columns.keys()) == ("ingest_issue_id",)
    assert {
        "connector_delivery_identity_hash",
        "payload_hmac",
        "hmac_key_version",
        "current_error_codes",
        "issue_revision",
        "diagnostic_summary",
    } <= set(issue.columns.keys())
    assert not ({"raw_payload", "student_id", "hmac_key"} & set(issue.columns))
    assert issue.comment == (
        "Sanitized invalid-delivery work items; raw payload, raw student "
        "identity, and HMAC keys are prohibited."
    )
    assert db_models.DtsSourcePartitionEpochRecord.__table__.comment == (
        "DTS v2 epoch registry; initial ACTIVE BROKER rows are created only "
        "by the whole-vector H0 bootstrap function."
    )
