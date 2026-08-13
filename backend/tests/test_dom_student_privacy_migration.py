from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260813_60_dom_student_privacy.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "dom_student_privacy_v60",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_60_installs_fail_closed_database_privacy_guards(
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

    assert migration.revision == "20260813_60_dom_privacy"
    assert migration.down_revision == "20260812_59_simple_acl"
    for raw_field in ("s_id", "stu_id", "user_id", "student_id"):
        assert f"'{raw_field}'" in sql
    assert "'student_ids'" in sql
    assert "'student_subjects'" in sql
    assert "'student_token'" in sql
    assert "object_item.key = 'info'" in sql
    assert "object_item.key IN ('cancel_reason', 'reason_desc')" in sql
    assert "'Domestic reason redacted'" in sql
    assert "value_text::jsonb" in sql
    assert "^dom:v1:[0-9a-f]{64}$" in sql
    assert "public.dom_student_json_is_safe_v1" in sql
    assert (
        "GRANT EXECUTE ON FUNCTION public.dom_student_json_is_safe_v1(jsonb)"
        in sql
    )
    assert "TO tit_dts_ingest_runtime" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON public.%I" in sql
    assert "IF TG_TABLE_NAME = 'dts_source_rows' THEN" in sql
    assert "ELSIF TG_TABLE_NAME = 'dts_dirty_keys' THEN" in sql
    assert "domestic student privacy contract is immutable" in sql
    assert "OLD.source_table = '__dom_student_privacy_contract__'" in sql
    assert "NEW.source_table = '__dom_student_privacy_contract__'" in sql
    assert "NEW.source_table = 'dom_appoint'" in sql
    assert "= NEW.source_row ->> 'id'" in sql
    assert "lesson wide state lacks one unambiguous source region" in sql
    assert "provenance.dom_sources > 0" in sql
    assert "provenance.ovs_sources > 0" in sql
    assert "key_part_2 <> ''" not in sql
    assert "guard_dom_lesson_student_privacy_v1" in sql
    assert "BEFORE INSERT OR UPDATE ON public.lesson_source_wide" in sql
    assert "appoints.source_region = 'dom'" in sql
    assert "appoints.source_table = 'dom_appoint'" in sql
    assert "appoints.is_deleted IS FALSE" not in sql
    assert "has_dom_source AND has_ovs_source" in sql
    assert "lesson wide write requires persisted source provenance" in sql
    assert "actor_name = 'tit_dts_ingest_runtime'" not in sql.split(
        "CREATE OR REPLACE FUNCTION public.guard_dom_lesson_student_privacy_v1"
    )[1]
    assert "lessons.\"学员id\" !~" in sql
    assert "NEW.\"学员id\" !~" in sql
    assert sql.index("$dom_student_privacy_existing_state$") < sql.index(
        "CREATE OR REPLACE FUNCTION public.guard_dts_runtime_state_write"
    )


def test_revision_60_is_noop_off_postgresql(monkeypatch) -> None:
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


def test_revision_60_privacy_boundary_is_forward_only() -> None:
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
