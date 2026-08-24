from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.db_models import TeacherRecord


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_75_camp_state_contract.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "camp_state_contract_v75",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_75_normalizes_before_installing_two_state_check(
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
        "create_check_constraint",
        lambda *args, **kwargs: operations.append(
            ("create_check_constraint", (args, kwargs))
        ),
    )

    migration.upgrade()

    assert migration.revision == "20260822_75_camp_state_contract"
    assert migration.down_revision == "20260822_74_favorite_schema"
    assert [kind for kind, _value in operations] == [
        "execute",
        "execute",
        "execute",
        "create_check_constraint",
        "execute",
    ]
    preflight = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in preflight
    assert "CAMP_STATE_UNKNOWN" in preflight
    assert "CAMP_STATE_PAYLOAD_INVALID" in preflight
    assert migration.IRREVERSIBLE_TRIGGER in preflight
    backfill = str(operations[1][1])
    assert "WHEN graduation_state = 'GRADUATED' THEN 'GRADUATED'" in backfill
    assert "ELSE 'IN_CAMP'" in backfill
    assert "jsonb_set" in backfill
    scorecard_view = str(operations[2][1])
    assert "public.teacher_scorecard_current" in scorecard_view
    assert "pg_get_viewdef" in scorecard_view
    assert "replace(" in scorecard_view
    assert "'IN_PROGRESS'" in scorecard_view
    assert "'IN_CAMP'" in scorecard_view
    assert "CAMP_STATE_SCORECARD_VIEW_MISSING" in scorecard_view
    assert "CAMP_STATE_SCORECARD_VIEW_BACKFILL_INCOMPLETE" in scorecard_view
    check_args, check_kwargs = operations[3][1]
    assert check_args == (
        "ck_teachers_graduation_state_v2",
        "teachers",
        "graduation_state IN ('IN_CAMP', 'GRADUATED') "
        "AND jsonb_typeof(payload) = 'object' "
        "AND payload ? 'graduation_state' "
        "AND payload ->> 'graduation_state' = graduation_state",
    )
    assert check_kwargs == {"schema": "public"}


def test_revision_75_downgrade_keeps_normalized_and_earned_facts(
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

    migration.downgrade()

    assert [kind for kind, _value in operations] == [
        "execute",
        "drop_constraint",
    ]
    guard = str(operations[0][1])
    assert "IN ACCESS EXCLUSIVE MODE" in guard
    assert migration.IRREVERSIBLE_TRIGGER in guard
    assert "UPDATE public.teachers" not in guard
    assert "teacher_qualifications" not in guard
    assert operations[1] == (
        "drop_constraint",
        (
            ("ck_teachers_graduation_state_v2", "teachers"),
            {"schema": "public", "type_": "check"},
        ),
    )


def test_teacher_metadata_declares_only_two_camp_states() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in TeacherRecord.__table__.constraints
        if constraint.name
    }
    assert checks["ck_teachers_graduation_state_v2"] == (
        "graduation_state IN ('IN_CAMP', 'GRADUATED') "
        "AND jsonb_typeof(payload) = 'object' "
        "AND payload ? 'graduation_state' "
        "AND payload ->> 'graduation_state' = graduation_state"
    )
