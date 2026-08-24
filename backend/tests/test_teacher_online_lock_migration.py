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
    / "20260822_72_teacher_online_lock_contract.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "teacher_online_lock_contract_v72",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_72_backfills_only_confirmed_source_wide_teacher_state(
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
        "create_index",
        lambda *args, **kwargs: operations.append(
            ("create_index", (args, kwargs))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *args, **kwargs: operations.append(
            ("create_check", (args, kwargs))
        ),
    )

    migration.upgrade()

    assert migration.revision == "20260822_72_teacher_online_lock"
    assert migration.down_revision == "20260822_71_dom_teacher_area"
    assert [kind for kind, _ in operations] == [
        "execute",
        "execute",
        "execute",
        "create_index",
        "create_check",
    ]

    lock_sql = str(operations[0][1])
    assert "public.teachers" in lock_sql
    assert "public.teacher_source_wide" in lock_sql
    assert "public.teacher_qualifications" in lock_sql
    assert "SHARE ROW EXCLUSIVE" in lock_sql

    online_sql = str(operations[1][1])
    assert "CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai'" in online_sql
    assert "lower(btrim(source.status)) = 'on'" in online_sql
    assert "lower(btrim(source.status)) = 'off' THEN 'LEFT'" in online_sql
    assert "lower(btrim(source.status)) = 'hei' THEN 'BLOCKED'" in online_sql
    assert "BETWEEN 0 AND 29" in online_sql
    assert "clock.business_date - source.status_on_date >= 30" in online_sql
    assert "LEFT JOIN public.teacher_source_wide" in online_sql
    assert "source_snapshot_label = 'SOURCE_WIDE_CURRENT'" in online_sql
    assert "online_status IS DISTINCT FROM projected.online_status" in online_sql
    assert "payload = projected.payload" in online_sql
    assert "'{employment_status}'" in online_sql
    assert "'{online_status_onboard_date}'" in online_sql
    assert "'{online_status_evidence_status}'" in online_sql
    assert "'{online_status_business_date}'" in online_sql
    assert "teacher.payload IS DISTINCT FROM projected.payload" in online_sql

    lock_backfill_sql = str(operations[2][1])
    assert "SET graduation_score_locked = 100.0" in lock_backfill_sql
    assert "revision = revision + 1" in lock_backfill_sql
    assert "graduation_qualified IS TRUE" in lock_backfill_sql
    assert "graduation_score_locked IS NULL" in lock_backfill_sql

    index_args, index_kwargs = operations[3][1]
    assert index_args == (
        migration.ONLINE_REFRESH_INDEX,
        "teachers",
        ["source_snapshot_label", "online_status", "teacher_id"],
    )
    assert index_kwargs == {"unique": False, "schema": "public"}

    check_args, check_kwargs = operations[4][1]
    assert check_args == (
        migration.GRADUATION_REQUIRES_LOCK_CONSTRAINT,
        "teacher_qualifications",
        "graduation_qualified IS FALSE "
        "OR (graduation_score_locked IS NOT NULL "
        "AND graduation_score_locked = 100.0)",
    )
    assert check_kwargs == {"schema": "public"}


def test_revision_72_downgrade_drops_only_contract_objects(monkeypatch) -> None:
    migration = _load_migration()
    dropped: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: dropped.append(
            ("constraint", args, kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: dropped.append(("index", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("downgrade must not rewrite v2 facts")
        ),
    )

    migration.downgrade()

    assert dropped == [
        (
            "constraint",
            (
                migration.GRADUATION_REQUIRES_LOCK_CONSTRAINT,
                "teacher_qualifications",
            ),
            {"schema": "public", "type_": "check"},
        ),
        (
            "index",
            (migration.ONLINE_REFRESH_INDEX,),
            {"table_name": "teachers", "schema": "public"},
        ),
    ]


def test_orm_metadata_matches_revision_72_contract_objects() -> None:
    teacher_table = db_models.TeacherRecord.__table__
    qualification_table = db_models.TeacherQualificationRecord.__table__

    indexes = {index.name: index for index in teacher_table.indexes}
    online_index = indexes[
        "ix_teachers_source_snapshot_online_status_teacher"
    ]
    assert [column.name for column in online_index.columns] == [
        "source_snapshot_label",
        "online_status",
        "teacher_id",
    ]
    assert online_index.unique is False

    checks = {
        constraint.name: constraint
        for constraint in qualification_table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    reverse_lock = str(
        checks[
            "ck_teacher_qualification_"
            "graduation_requires_score_locked_v2"
        ].sqltext
    )
    assert reverse_lock == (
        "graduation_qualified IS FALSE "
        "OR (graduation_score_locked IS NOT NULL "
        "AND graduation_score_locked = 100.0)"
    )
    # Revision 72 augments rather than weakens the revision-70 direction.
    assert "ck_teacher_qualification_graduation_score_locked_v2" in checks
