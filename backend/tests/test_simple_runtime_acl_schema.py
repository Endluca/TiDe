from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260812_59_simple_runtime_acl.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "simple_runtime_acl_v59",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_59_merges_both_public_heads_and_matches_final_table_acl(
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

    assert migration.revision == "20260812_59_simple_acl"
    assert migration.down_revision == (
        "20260811_57_g02_document",
        "20260812_58_table_acl",
    )
    assert set(migration.GROWTH_READ_RELATIONS) == {
        "public.teacher_source_wide",
        "public.lesson_source_wide",
    }
    assert set(
        migration.GROWTH_CRUD_RELATIONS
        + migration.OPTIONAL_GROWTH_CRUD_RELATIONS
    ) == {
        "public.teachers",
        "public.complaint_category_rules",
        "public.personalized_trigger_matches",
        "public.lesson_score_results",
        "public.teacher_qualifications",
        "public.score_accounts",
        "public.score_component_accounts",
        "public.score_entries",
        "public.task_templates",
        "public.task_assignments",
        "public.notifications",
        "public.ops_cases",
        "public.ops_decisions",
        "public.outbox_events",
        "public.audit_events",
        "public.idempotency_records",
        "public.config_versions",
        "public.config_publication_audits",
        "public.operator_accounts",
        "public.operator_role_grants",
        "public.operator_sessions",
        "public.teacher_support_tickets",
    }
    assert set(migration.TEACHER_READ_RELATIONS) == {
        "public.alembic_version",
        "public.task_templates",
        "public.teachers",
        "public.teacher_scorecard_current",
        "public.teacher_lesson_score_current",
        "public.teacher_g01_status_current",
    }
    assert set(
        migration.TEACHER_CRUD_RELATIONS
        + migration.OPTIONAL_TEACHER_CRUD_RELATIONS
    ) == {
        "public.task_assignments",
        "public.notifications",
        "public.notification_events",
        "public.teacher_support_tickets",
    }
    assert set(migration.DTS_CRUD_RELATIONS) == {
        "public.teacher_source_wide",
        "public.lesson_source_wide",
        "public.dts_ingest_checkpoints",
        "public.dts_ingest_events",
        "public.dts_source_rows",
        "public.dts_dirty_keys",
    }
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
    assert "'SELECT,INSERT,UPDATE,DELETE'" not in sql
    assert "ON ALL TABLES IN SCHEMA tide TO tit_teacher_crud" in sql
    assert "REVOKE ALL PRIVILEGES (%I)" in sql
    assert "guard_teacher_notification_write" in sql
    assert "guard_notification_event_history" in sql
    assert "guard_audit_event_history" in sql
    assert "BEFORE UPDATE OR DELETE ON public.audit_events" in sql
    assert "audit event history is append-only" in sql
    assert "guard_runtime_append_only_fact" in sql
    for append_only_relation in (
        "score_entries",
        "idempotency_records",
        "config_publication_audits",
        "ops_decisions",
    ):
        assert (
            f"BEFORE UPDATE OR DELETE ON public.{append_only_relation}"
            in sql
        )
    assert "guard_simple_support_ticket_write" in sql
    assert "guard_dts_runtime_state_write" in sql
    assert "guard_runtime_schema_migration_write" in sql
    assert "guard_crm_sso_login_write" in sql
    for optional_relation in (
        "public.teacher_support_tickets",
        "tide.schema_migrations",
        "tide.crm_sso_logins",
        "tide.system_notifications",
    ):
        assert f"'{optional_relation}'::regclass" not in sql


def test_revision_59_has_no_overlap_between_teacher_read_and_crud_sets() -> None:
    migration = _load_migration()
    teacher_read = set(migration.TEACHER_READ_RELATIONS)
    teacher_crud = set(
        migration.TEACHER_CRUD_RELATIONS
        + migration.OPTIONAL_TEACHER_CRUD_RELATIONS
    )

    assert teacher_read.isdisjoint(teacher_crud)
    assert "public.teacher_source_wide" not in teacher_read | teacher_crud
