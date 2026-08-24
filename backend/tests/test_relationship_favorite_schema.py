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
    / "20260822_74_relationship_favorite_schema.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "relationship_favorite_schema_v74",
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


def _foreign_keys(
    items: tuple[object, ...],
) -> dict[str, sa.ForeignKeyConstraint]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.ForeignKeyConstraint) and item.name is not None
    }


def _unique_constraints(
    items: tuple[object, ...],
) -> dict[str, sa.UniqueConstraint]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.UniqueConstraint) and item.name is not None
    }


def test_revision_74_builds_only_the_inert_four_layer_schema(
    monkeypatch,
) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
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

    assert migration.revision == "20260822_74_favorite_schema"
    assert migration.down_revision == "20260822_73_retire_false_early"
    assert migration.SHADOW_SCHEMA_ONLY is True
    assert migration.RUNTIME_ROUTE_ACTIVATED is False
    assert migration.RELATIONSHIP_EVENT_HISTORY_IMMUTABLE is True
    assert set(created) == {
        "teacher_student_relationship_events",
        "teacher_student_relationship_current",
        "course_favorite_observations",
        "course_favorite_attributions",
    }

    event_items = created["teacher_student_relationship_events"]
    event_columns = _columns(event_items)
    assert not any(isinstance(column.type, sa.JSON) for column in event_columns.values())
    assert event_columns["event_sequence"].identity is not None
    assert event_columns["operation"].type.length == 48
    assert event_columns["old_student_token"].type.length == 128
    assert "dom:v1" in str(
        _checks(event_items)["ck_relationship_event_dom_student_tokens"].sqltext
    )
    event_fk = _foreign_keys(event_items)["fk_relationship_event_source_version"]
    assert event_fk.deferrable is True
    assert event_fk.initially == "DEFERRED"

    current_items = created["teacher_student_relationship_current"]
    current_fks = _foreign_keys(current_items)
    assert set(current_fks) == {"fk_relationship_current_latest_event"}
    assert "source_courses" not in str(current_fks)
    assert _columns(current_items)["is_favorited"].nullable is True
    assert _columns(current_items)["is_blocked"].nullable is True

    observation_items = created["course_favorite_observations"]
    observation_columns = _columns(observation_items)
    assert isinstance(observation_columns["appoint_id_numeric"].type, sa.Numeric)
    assert isinstance(
        observation_columns["appoint_id_text_sort"].type,
        sa.LargeBinary,
    )
    evidence_check = str(
        _checks(observation_items)[
            "ck_favorite_observation_evidence_shape"
        ].sqltext
    )
    assert "CONFIRMED_FALSE" in evidence_check
    assert "WAITING_HISTORY" in evidence_check
    assert "HISTORY_INCOMPLETE" in evidence_check
    assert "FAVORITE_EFFECTIVE_TIME_MISSING" in evidence_check
    observation_fks = _foreign_keys(observation_items)
    assert set(observation_fks) == {
        "fk_favorite_observation_course",
        "fk_favorite_observation_completion_participation",
    }
    assert all(fk.deferrable is True for fk in observation_fks.values())
    assert "uq_favorite_observation_lease_token" in _unique_constraints(
        observation_items
    )

    attribution_items = created["course_favorite_attributions"]
    attribution_fks = _foreign_keys(attribution_items)
    assert set(attribution_fks) == {
        "fk_favorite_attribution_observation",
        "fk_favorite_attribution_completion_participation",
        "fk_favorite_attribution_current_score_entry",
        "fk_favorite_attribution_reversal_score_entry",
    }
    assert str(
        indexes["uq_favorite_attribution_current_course"][2][
            "postgresql_where"
        ]
    ) == "status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')"
    assert str(
        indexes["uq_favorite_observation_current"][2]["postgresql_where"]
    ) == "status NOT IN ('INVALIDATED', 'VOIDED')"

    sql = "\n".join(executed)
    assert "DTS_V2_RELATIONSHIP_EVENT_IMMUTABLE" in sql
    assert "DTS_V2_RELATIONSHIP_CURRENT_NOT_LATEST_EVENT" in sql
    assert "latest_event.effective_at" in sql
    assert "completion_end_time + interval '24 hours'" in sql
    assert "DTS_V2_FAVORITE_ATTRIBUTION_NOT_CANONICAL_FIRST" in sql
    assert "completion_role IS DISTINCT FROM 'COMPLETION'" in sql
    assert "DTS_V2_FAVORITE_REVERSAL_WITHOUT_INVALIDATION" in sql
    assert "DTS_V2_FAVORITE_RULE_REAWARD_INVALID" in sql
    assert "candidate.appoint_id_numeric ASC NULLS LAST" in sql
    assert "candidate.appoint_id_text_sort ASC NULLS LAST" in sql
    assert "CREATE CONSTRAINT TRIGGER" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "REVOKE ALL PRIVILEGES" in sql
    assert "GRANT " not in sql
    assert "claim_favorite_observations_v2" not in sql


def test_relationship_favorite_orm_metadata_keeps_region_and_lifetime_keys() -> None:
    events = db_models.TeacherStudentRelationshipEventRecord.__table__
    current = db_models.TeacherStudentRelationshipCurrentRecord.__table__
    observations = db_models.CourseFavoriteObservationRecord.__table__
    attributions = db_models.CourseFavoriteAttributionRecord.__table__

    assert {column.name for column in events.primary_key.columns} == {
        "source_region",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
        "offset_value",
    }
    assert {column.name for column in current.primary_key.columns} == {
        "source_region",
        "teacher_id",
        "student_token",
    }
    assert {column.name for column in observations.primary_key.columns} == {
        "source_region",
        "source_appoint_id",
        "observation_revision",
    }
    assert {column.name for column in attributions.primary_key.columns} == {
        "source_region",
        "teacher_id",
        "student_token",
    }

    assert current.c.student_token.type.length == 128
    assert observations.c.student_token.type.length == 128
    assert attributions.c.student_token.type.length == 128
    assert isinstance(observations.c.appoint_id_numeric.type, sa.Numeric)
    assert isinstance(observations.c.appoint_id_text_sort.type, sa.LargeBinary)
    assert events.c.operation.type.length == 48

    observation_indexes = {index.name: index for index in observations.indexes}
    attribution_indexes = {index.name: index for index in attributions.indexes}
    observation_uniques = {
        constraint.name
        for constraint in observations.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert "uq_favorite_observation_lease_token" in observation_uniques
    assert observation_indexes["uq_favorite_observation_current"].unique is True
    assert attribution_indexes[
        "uq_favorite_attribution_current_course"
    ].unique is True
    assert (
        str(
            attribution_indexes[
                "uq_favorite_attribution_current_course"
            ].dialect_options["postgresql"]["where"]
        )
        == "status IN ('AWARDED', 'AWARDED_PENDING_EVIDENCE')"
    )

    current_fks = {
        constraint.name: constraint
        for constraint in current.foreign_key_constraints
    }
    assert set(current_fks) == {"fk_relationship_current_latest_event"}
    assert current_fks["fk_relationship_current_latest_event"].deferrable is True
    attribution_fks = {
        constraint.name: constraint
        for constraint in attributions.foreign_key_constraints
    }
    assert "fk_favorite_attribution_observation" in attribution_fks
    assert attribution_fks[
        "fk_favorite_attribution_observation"
    ].deferrable is True


def test_revision_74_downgrade_locks_and_refuses_data_loss(monkeypatch) -> None:
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
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda name, **_kwargs: dropped.append(name),
    )

    migration.downgrade()

    guard_sql = executed[0]
    assert guard_sql.index("LOCK TABLE") < guard_sql.index("IF EXISTS")
    assert "IN ACCESS EXCLUSIVE MODE" in guard_sql
    assert "refusing relationship/favorite schema downgrade" in guard_sql
    assert dropped == [
        "course_favorite_attributions",
        "course_favorite_observations",
        "teacher_student_relationship_current",
        "teacher_student_relationship_events",
    ]
