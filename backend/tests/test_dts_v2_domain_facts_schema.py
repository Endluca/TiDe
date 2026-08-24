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
    / "20260822_81_dts_v2_domain_facts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_domain_facts_v81",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_81_is_additive_typed_domain_schema(monkeypatch) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
    unique_constraints: list[tuple[str, str, tuple[str, ...]]] = []
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
            name,
            (table, tuple(columns)),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_unique_constraint",
        lambda name, table, columns, **_kwargs: unique_constraints.append(
            (name, table, tuple(columns))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_81_dts_v2_domain_facts"
    assert migration.down_revision == "20260822_80_dts_v2_dirty_queue"
    assert set(created) == {
        "source_course_labels",
        "source_course_complaints",
        "source_course_fact_current",
        "source_participation_fact_current",
        "domain_aggregate_revisions",
    }
    assert unique_constraints == [
        (
            "uq_complaint_rule_identity_version",
            "complaint_category_rules",
            ("rule_id", "source_sha256"),
        )
    ]
    assert indexes["ix_source_course_labels_course_label"][0] == (
        "source_course_labels"
    )
    for suffix in ("l1", "l2", "l3"):
        assert f"ix_source_course_complaints_category_{suffix}" in indexes

    sql = "\n".join(executed)
    assert "dts_domain_aggregate_key_valid_v2" in sql
    assert "TEACHER_STUDENT" in sql
    assert "COMPLAINT_CATEGORY" in sql
    assert "TASK_PLAN" in sql
    assert "^dom:v1:[0-9a-f]{64}$" in sql
    assert "tit_dts_domain_projector_runtime" in sql
    assert "GRANT SELECT,INSERT,UPDATE" in sql
    assert "domain_aggregate_revisions" in sql
    assert "outbox_events" not in sql


def test_revision_81_downgrade_removes_only_its_objects(monkeypatch) -> None:
    migration = _load_migration()
    dropped_tables: list[str] = []
    dropped_constraints: list[tuple[str, str, str | None]] = []
    executed: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda name, **_kwargs: dropped_tables.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table, **kwargs: dropped_constraints.append(
            (name, table, kwargs.get("type_"))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.downgrade()

    assert dropped_tables == [
        "domain_aggregate_revisions",
        "source_participation_fact_current",
        "source_course_fact_current",
        "source_course_complaints",
        "source_course_labels",
    ]
    assert dropped_constraints == [
        (
            "uq_complaint_rule_identity_version",
            "complaint_category_rules",
            "unique",
        )
    ]
    assert executed == [
        "DROP FUNCTION IF EXISTS "
        "public.dts_domain_aggregate_key_valid_v2(text,jsonb)"
    ]


def test_domain_fact_orm_matches_region_typed_and_revision_contract() -> None:
    expected_primary_keys = {
        db_models.SourceCourseLabelRecord: (
            "source_region",
            "source_log_id",
        ),
        db_models.SourceCourseComplaintRecord: (
            "source_region",
            "source_complaint_id",
        ),
        db_models.SourceCourseFactCurrentRecord: (
            "source_region",
            "source_appoint_id",
        ),
        db_models.SourceParticipationFactCurrentRecord: (
            "source_region",
            "source_appoint_id",
            "participation_seq",
        ),
        db_models.DomainAggregateRevisionRecord: (
            "aggregate_type",
            "aggregate_id",
        ),
    }
    for model, expected in expected_primary_keys.items():
        assert tuple(column.name for column in model.__table__.primary_key) == (
            expected
        )

    labels = db_models.SourceCourseLabelRecord.__table__
    assert labels.c.source_log_id_type.nullable is False
    assert isinstance(labels.c.source_log_id_numeric.type, sa.Numeric)
    assert isinstance(labels.c.source_log_id_text.type, sa.Text)
    assert labels.c.label_id_type.nullable is False
    assert labels.c.is_deleted.nullable is False
    assert labels.c.source_row_revision.nullable is False
    assert labels.c.source_position.nullable is False

    complaints = db_models.SourceCourseComplaintRecord.__table__
    for prefix in (
        "source_complaint_id",
        "source_teacher_id",
        "complaint_type",
        "complaint_type_child",
        "complaint_type_grandson",
    ):
        assert f"{prefix}_type" in complaints.c
        assert f"{prefix}_numeric" in complaints.c
        assert f"{prefix}_text" in complaints.c
    complaint_fk_names = {
        constraint.name
        for constraint in complaints.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }
    assert complaint_fk_names == {
        "fk_source_course_complaint_course",
        "fk_source_course_complaint_rule_version",
    }

    participation = db_models.SourceParticipationFactCurrentRecord.__table__
    participation_fk = next(iter(participation.foreign_key_constraints))
    assert participation_fk.name == (
        "fk_source_participation_fact_participation"
    )

    aggregate = db_models.DomainAggregateRevisionRecord.__table__
    assert aggregate.c.canonical_key.nullable is False
    assert aggregate.c.canonical_key_sha256.nullable is False
    assert aggregate.c.revision.nullable is False
    assert aggregate.c.last_source_row_revision.nullable is True
    assert aggregate.c.last_source_position.nullable is True
    assert aggregate.c.aggregate_state.nullable is False
    assert aggregate.c.aggregate_state_sha256.nullable is False
    aggregate_checks = {
        constraint.name
        for constraint in aggregate.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert {
        "ck_domain_aggregate_type",
        "ck_domain_aggregate_canonical_key",
        "ck_domain_aggregate_id",
        "ck_domain_aggregate_state_hash",
        "ck_domain_aggregate_dom_student_token",
    }.issubset(aggregate_checks)

    rule_constraints = {
        constraint.name
        for constraint in db_models.ComplaintCategoryRuleRecord.__table__.constraints
    }
    assert "uq_complaint_rule_identity_version" in rule_constraints
