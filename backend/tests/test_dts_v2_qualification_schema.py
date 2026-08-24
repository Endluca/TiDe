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
    / "20260822_70_qualification_schema.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dts_v2_qualification_schema_v70",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _checks(table: sa.Table) -> dict[str, sa.CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name is not None
    }


def test_revision_70_is_an_expand_only_confirmed_state_slice(
    monkeypatch,
) -> None:
    migration = _load_migration()
    added: list[tuple[str, sa.Column, dict[str, object]]] = []
    checks: dict[str, tuple[str, str, dict[str, object]]] = {}
    executed: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column, **kwargs: added.append(
            (table, column, kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition, **kwargs: checks.setdefault(
            name,
            (table, str(condition), kwargs),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    assert migration.revision == "20260822_70_qualification_schema"
    assert migration.down_revision == "20260822_69_dts_v2_source_guard"
    assert migration.EXPAND_ONLY is True
    assert migration.GRADUATION_SCORE_LOCK_VALUE == 100.0
    assert [(table, column.name) for table, column, _ in added] == [
        ("teachers", "online_status"),
        ("teacher_qualifications", "graduation_score_locked"),
    ]
    assert all(column.nullable for _, column, _ in added)
    assert isinstance(added[0][1].type, sa.String)
    assert added[0][1].type.length == 32
    assert isinstance(added[1][1].type, sa.Float)
    assert set(checks) == {
        "ck_teachers_online_status_v2",
        "ck_teacher_qualification_graduation_score_locked_v2",
    }

    sql = "\n".join(executed)
    assert "UPDATE public.teachers" not in sql
    assert "SET graduation_score_locked = 100.0" in sql
    assert "revision = revision + 1" in sql
    assert "graduation_qualified_at" not in sql
    assert "gold_qualified_at" not in sql
    assert "GRADUATION_SCORE_LOCKED_IMMUTABLE" in sql
    # Revision 59 already grants this runtime table-level CRUD and removes all
    # explicit column ACLs.  A newly added column inherits that table grant;
    # adding a column grant here would violate the active ACL contract.
    assert "GRANT " not in sql
    assert "REVOKE UPDATE" not in sql


def test_orm_matches_revision_70_columns_and_checks() -> None:
    teacher_table = db_models.TeacherRecord.__table__
    qualification_table = db_models.TeacherQualificationRecord.__table__

    online_status = teacher_table.c.online_status
    assert isinstance(online_status.type, sa.String)
    assert online_status.type.length == 32
    assert online_status.nullable is True

    locked_score = qualification_table.c.graduation_score_locked
    assert isinstance(locked_score.type, sa.Float)
    assert locked_score.nullable is True

    teacher_checks = _checks(teacher_table)
    assert "ck_teachers_online_status_v2" in teacher_checks
    graduation_state_check = str(
        teacher_checks["ck_teachers_graduation_state_v2"].sqltext
    )
    assert graduation_state_check == (
        "graduation_state IN ('IN_CAMP', 'GRADUATED') "
        "AND jsonb_typeof(payload) = 'object' "
        "AND payload ? 'graduation_state' "
        "AND payload ->> 'graduation_state' = graduation_state"
    )

    qualification_checks = _checks(qualification_table)
    locked_check = str(
        qualification_checks[
            "ck_teacher_qualification_graduation_score_locked_v2"
        ].sqltext
    )
    assert "graduation_score_locked IS NULL" in locked_check
    assert "graduation_qualified IS TRUE" in locked_check
    assert "graduation_score_locked = 100.0" in locked_check


def test_revision_70_downgrade_guards_only_new_v2_facts(monkeypatch) -> None:
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
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda *_args, **_kwargs: None,
    )

    migration.downgrade()

    guard_sql = executed[0]
    assert guard_sql.index("LOCK TABLE") < guard_sql.index("IF EXISTS")
    assert "WHERE online_status IS NOT NULL" in guard_sql
    assert "WHERE graduation_score_locked IS NOT NULL" in guard_sql
    assert "SELECT 1 FROM public.teachers LIMIT 1" not in guard_sql
    assert "SELECT 1 FROM public.teacher_qualifications LIMIT 1" not in guard_sql
