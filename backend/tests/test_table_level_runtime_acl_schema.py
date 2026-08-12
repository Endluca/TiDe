from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260812_58_table_level_runtime_acl.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "table_level_runtime_acl_v58",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_58_uses_table_grants_with_database_guards(monkeypatch) -> None:
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

    assert migration.revision == "20260812_58_table_acl"
    assert migration.down_revision == "20260812_57_dts_state"
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE" in sql
    assert "public.task_assignments" in sql
    assert "public.outbox_events" in sql
    assert "public.lesson_score_results" in sql
    assert "public.teacher_qualifications" in sql
    assert "GRANT SELECT, UPDATE ON TABLE public.operator_accounts" in sql
    assert "GRANT SELECT, UPDATE ON TABLE\n                    public.task_assignments" in sql
    assert "public.teacher_g01_status_current" in sql
    assert "public.guard_outbox_event_update" in sql
    assert "public.guard_lesson_score_result_identity" in sql
    assert "public.guard_operator_account_runtime_update" in sql
    assert "public.guard_teacher_notification_update" in sql
    assert "public.guard_teacher_support_ticket_update" in sql
    assert "REVOKE ALL PRIVILEGES (%I)" in sql


def test_teacher_g01_queries_use_the_restricted_view() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = (
        root
        / "teacher"
        / "backend"
        / "src"
        / "tasks"
        / "g01-external-status-rule.handler.ts",
        root
        / "teacher"
        / "backend"
        / "src"
        / "tide"
        / "tide.repository.ts",
    )

    for source_path in sources:
        source = source_path.read_text(encoding="utf-8")
        assert "public.teacher_g01_status_current" in source
        assert "public.teacher_source_wide" not in source
