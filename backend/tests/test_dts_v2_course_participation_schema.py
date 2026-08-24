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
    / "20260822_67_course_participation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_course_participation_v67",
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


def test_revision_67_creates_only_locked_shadow_course_tables(monkeypatch) -> None:
    migration = _load_migration()
    created: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...], dict[str, object]]] = {}
    foreign_keys: dict[str, dict[str, object]] = {}
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

    def _capture_foreign_key(name: str, *_args: object, **kwargs: object) -> None:
        foreign_keys[name] = kwargs

    monkeypatch.setattr(migration.op, "create_index", _capture_index)
    monkeypatch.setattr(migration.op, "create_foreign_key", _capture_foreign_key)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_67_course_part"
    assert migration.down_revision == "20260822_66_dts_v2_shadow"
    assert migration.SHADOW_SCHEMA_ONLY is True
    assert migration.DEFERRED_BIDIRECTIONAL_GUARD_IMPLEMENTED is False
    assert migration.DEFERRED_SEMANTIC_GUARDS_IMPLEMENTED is False
    assert set(created) == {"source_courses", "source_course_participations"}
    for items in created.values():
        assert _columns(items)["source_appoint_id"].type.length == 512
    course_columns = _columns(created["source_courses"])
    participation_columns = _columns(created["source_course_participations"])
    assert course_columns["current_teacher_id_type"].type.length == 16
    assert course_columns["completion_teacher_id_type"].type.length == 16
    assert participation_columns["teacher_id_type"].nullable is False
    completion_pointer_check = str(
        _checks(created["source_courses"])[
            "ck_source_course_completion_pointer_group"
        ].sqltext
    )
    assert "completion_source_revision IS NOT NULL" in completion_pointer_check
    assert "completion_teacher_id_type IN ('NUMERIC', 'TEXT')" in (
        completion_pointer_check
    )

    assert str(
        indexes["uq_source_course_participation_current"][2][
            "postgresql_where"
        ]
    ) == "is_current IS TRUE"
    assert str(
        indexes["uq_source_course_participation_completion"][2][
            "postgresql_where"
        ]
    ) == "participation_role = 'COMPLETION'"
    assert set(foreign_keys) == {
        "fk_source_course_current_participation",
        "fk_source_course_completion_participation",
    }
    assert all(foreign_key["deferrable"] is True for foreign_key in foreign_keys.values())
    assert all(
        foreign_key["initially"] == "DEFERRED"
        for foreign_key in foreign_keys.values()
    )

    sql = "\n".join(executed)
    assert "DTS v2 shadow only" in sql
    assert "frozen-field and reverse-pointer guards not activated" in sql
    assert "provenance semantic guard not yet activated" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE" in sql
    assert "tit_dts_ingest_runtime" in sql
    assert "GRANT " not in sql
    assert "CREATE CONSTRAINT TRIGGER" not in sql


def test_course_participation_orm_has_provenance_and_pointer_constraints() -> None:
    course = db_models.SourceCourseRecord.__table__
    participation = db_models.SourceCourseParticipationRecord.__table__

    assert course.c.source_appoint_id.type.length == 512
    assert participation.c.source_appoint_id.type.length == 512
    assert course.c.current_teacher_id_type.type.length == 16
    assert course.c.completion_teacher_id_type.type.length == 16
    assert participation.c.teacher_id_type.nullable is False
    assert {column.name for column in course.primary_key.columns} == {
        "source_region",
        "source_appoint_id",
    }
    assert {column.name for column in participation.primary_key.columns} == {
        "source_region",
        "source_appoint_id",
        "participation_seq",
    }

    course_fks = {constraint.name: constraint for constraint in course.foreign_key_constraints}
    assert set(course_fks) == {
        "fk_source_course_current_participation",
        "fk_source_course_completion_participation",
        "fk_source_course_completion_conflict_case_v2",
    }
    assert all(constraint.deferrable is True for constraint in course_fks.values())
    assert all(constraint.initially == "DEFERRED" for constraint in course_fks.values())
    assert course_fks[
        "fk_source_course_current_participation"
    ].column_keys == [
        "source_region",
        "source_appoint_id",
        "current_participation_seq",
        "current_teacher_id",
        "current_teacher_id_type",
    ]
    assert course_fks[
        "fk_source_course_completion_participation"
    ].column_keys == [
        "source_region",
        "source_appoint_id",
        "completion_participation_seq",
        "completion_teacher_id",
        "completion_teacher_id_type",
    ]

    participation_fks = {
        constraint.name: constraint
        for constraint in participation.foreign_key_constraints
    }
    assert set(participation_fks) == {
        "fk_source_course_participation_course",
        "fk_source_course_participation_source_version",
    }
    source_version_fk = participation_fks[
        "fk_source_course_participation_source_version"
    ]
    assert len(source_version_fk.column_keys) == 5
    assert source_version_fk.deferrable is True

    indexes = {index.name: index for index in participation.indexes}
    assert indexes["uq_source_course_participation_current"].unique is True
    assert indexes["uq_source_course_participation_completion"].unique is True
    assert tuple(
        column.name
        for column in indexes[
            "ix_source_course_participation_teacher_time"
        ].columns
    )[:3] == ("source_region", "teacher_id_type", "teacher_id")
    assert (
        str(
            indexes["uq_source_course_participation_current"].dialect_options[
                "postgresql"
            ]["where"]
        )
        == "is_current IS TRUE"
    )


def test_revision_67_downgrade_locks_before_checking_shadow_data(
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
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(migration.op, "drop_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "drop_table", lambda *_args, **_kwargs: None)

    migration.downgrade()

    guard_sql = executed[0]
    assert guard_sql.index("LOCK TABLE") < guard_sql.index("IF EXISTS")
    assert "IN ACCESS EXCLUSIVE MODE" in guard_sql
