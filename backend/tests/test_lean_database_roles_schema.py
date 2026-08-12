from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260812_56_lean_database_roles.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "lean_database_roles_v56",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_56_applies_the_five_role_runtime_contract(monkeypatch) -> None:
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

    assert migration.revision == "20260812_56_lean_roles"
    assert migration.down_revision == "20260811_55_source_wide_v12"
    assert "tit_growth_app" in sql
    assert "tit_dts_ingest_runtime" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in sql
    assert "public.teacher_source_wide" in sql
    assert "public.lesson_source_wide" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.task_assignments" in sql
    assert "GRANT UPDATE (status, status_reason_code" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.outbox_events" in sql
    assert "GRANT UPDATE (status, attempt_count" in sql
    assert "tit_growth_app source-wide access is not read-only" in sql
    assert "DTS can mutate a non-source-wide relation" in sql


def test_revision_56_retires_only_legacy_source_access(monkeypatch) -> None:
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

    migration._retire_legacy_source_acl()
    sql = "\n".join(executed)

    assert "tit_source_monitor" in sql
    assert "tit_source_worker" in sql
    assert "tit_source_worker_runtime" in sql
    assert "DROP ROLE" not in sql
    assert "tit_teacher_crud" not in sql
    assert "tide_sys_admin" not in sql


def test_revision_56_downgrade_does_not_recreate_credentials(monkeypatch) -> None:
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

    migration.downgrade()
    sql = "\n".join(executed)

    assert "REVOKE ALL PRIVILEGES" in sql
    assert "CREATE ROLE" not in sql
    assert "ALTER ROLE" not in sql
    assert "PASSWORD" not in sql
