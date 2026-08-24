from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import sqlalchemy as sa

from app.db_models import (
    LessonScoreResultRecord,
    PersonalizedTriggerMatchRecord,
    TeacherQualificationRecord,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_43_source_results.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "source_results_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_derived_result_orm_preserves_rev43_and_adds_inert_v2_ownership() -> None:
    lesson_table = LessonScoreResultRecord.__table__
    rev43_columns = (
        "lesson_id",
        "user_feedback_score",
        "reliability_score",
        "class_quality_score",
        "lesson_total_score",
        "dimensions",
        "score_rule_version",
        "projection_revision",
        "calculated_at",
    )
    v2_ownership_columns = (
        "v2_source_region",
        "v2_source_appoint_id",
        "v2_completion_participation_seq",
        "v2_teacher_id",
        "v2_projection_generation",
    )
    assert tuple(column.name for column in lesson_table.columns) == (
        "lesson_source_region",
        *rev43_columns,
        *v2_ownership_columns,
    )
    assert all(lesson_table.c[name].nullable for name in v2_ownership_columns)
    assert "teacher_id" not in lesson_table.columns
    assert lesson_table.columns["dimensions"].default.arg(None) == {}
    assert str(lesson_table.columns["dimensions"].server_default.arg) == "'{}'"
    lesson_fk = next(
        constraint
        for constraint in lesson_table.foreign_key_constraints
        if constraint.name == "fk_lesson_score_result_source_lesson_region"
    )
    assert tuple(element.target_fullname for element in lesson_fk.elements) == (
        "lesson_source_wide.source_region",
        "lesson_source_wide.课程id",
    )
    assert lesson_fk.ondelete == "CASCADE"

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in lesson_table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name is not None
    }
    assert checks["ck_lesson_score_result_v2_ownership"] == (
        "(v2_source_region IS NULL "
        "AND v2_source_appoint_id IS NULL "
        "AND v2_completion_participation_seq IS NULL "
        "AND v2_teacher_id IS NULL "
        "AND v2_projection_generation IS NULL) "
        "OR (v2_source_region IN ('dom', 'ovs') "
        "AND v2_source_appoint_id IS NOT NULL "
        "AND v2_completion_participation_seq >= 1 "
        "AND v2_teacher_id IS NOT NULL "
        "AND btrim(v2_teacher_id) <> '' "
        "AND v2_projection_generation >= 1)"
    )
    v2_foreign_keys = {
        constraint.name: constraint
        for constraint in lesson_table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
        and constraint.name is not None
        and constraint.name.startswith("fk_lesson_score_result_v2_")
    }
    assert set(v2_foreign_keys) == {
        "fk_lesson_score_result_v2_course",
        "fk_lesson_score_result_v2_score_owner",
    }
    assert all(
        constraint.ondelete == "RESTRICT"
        and constraint.deferrable is True
        and constraint.initially == "DEFERRED"
        for constraint in v2_foreign_keys.values()
    )
    ownership_index = next(
        index
        for index in lesson_table.indexes
        if index.name == "uq_lesson_score_result_v2_course"
    )
    assert ownership_index.unique is True
    assert str(ownership_index.dialect_options["postgresql"]["where"]) == (
        "v2_source_region IS NOT NULL"
    )

    qualification_table = TeacherQualificationRecord.__table__
    assert tuple(column.name for column in qualification_table.primary_key) == (
        "teacher_id",
    )
    assert {
        "graduation_criteria_met",
        "graduation_qualified",
        "graduation_qualified_at",
        "gold_criteria_met",
        "gold_qualified",
        "gold_qualified_at",
        "score_rule_version",
        "gate_results",
        "revision",
        "calculated_at",
    } <= {column.name for column in qualification_table.columns}


def test_trigger_match_orm_points_to_current_lesson_source() -> None:
    lesson_fk = next(
        constraint
        for constraint in PersonalizedTriggerMatchRecord.__table__.foreign_key_constraints
        if constraint.name == "fk_personalized_trigger_match_lesson_region"
    )
    assert tuple(element.target_fullname for element in lesson_fk.elements) == (
        "lesson_source_wide.source_region",
        "lesson_source_wide.课程id",
    )
    assert lesson_fk.ondelete == "SET NULL"


def test_revision_43_creates_tables_rewires_fk_and_guards_qualifications(
    monkeypatch,
) -> None:
    migration = _migration_module()
    created_tables: list[str] = []
    dropped_constraints: list[tuple[str, str]] = []
    created_foreign_keys: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...], dict]] = []
    executed: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *_columns, **_kwargs: created_tables.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table_name, **_kwargs: dropped_constraints.append(
            (name, table_name)
        ),
    )

    def create_foreign_key(
        name,
        source_table,
        referent_table,
        local_columns,
        remote_columns,
        **kwargs,
    ):
        created_foreign_keys.append(
            (
                name,
                source_table,
                referent_table,
                tuple(local_columns),
                tuple(remote_columns),
                kwargs,
            )
        )

    monkeypatch.setattr(migration.op, "create_foreign_key", create_foreign_key)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()
    sql = "\n".join(executed)

    assert migration.down_revision == "20260806_42_effective_acl"
    assert len(migration.revision) <= 32
    assert created_tables == ["lesson_score_results", "teacher_qualifications"]
    assert dropped_constraints == [
        ("fk_personalized_trigger_match_lesson", "personalized_trigger_matches")
    ]
    assert created_foreign_keys == [
        (
            "fk_personalized_trigger_match_lesson",
            "personalized_trigger_matches",
            "lesson_source_wide",
            ("lesson_id",),
            ("课程id",),
            {
                "source_schema": "public",
                "referent_schema": "public",
                "ondelete": "SET NULL",
            },
        )
    ]
    assert "absent from lesson_source_wide" in sql
    assert "GRADUATION_QUALIFICATION_IRREVERSIBLE" in sql
    assert "GOLD_QUALIFICATION_IRREVERSIBLE" in sql
    assert "EARNED_QUALIFICATION_DELETE_FORBIDDEN" in sql
    assert "QUALIFICATION_REVISION_MUST_INCREMENT" in sql
    assert "BEFORE UPDATE OR DELETE ON public.teacher_qualifications" in sql


def test_growth_runtime_acl_covers_internal_worker_without_source_writes(
    monkeypatch,
) -> None:
    migration = _migration_module()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )
    migration._grant_growth_runtime_acl()
    grants, assertions = executed

    assert "tit_growth_app must be a restricted LOGIN role" in source
    assert "rolcanlogin" in source
    assert set(migration.SOURCE_READ_TABLES) == {
        "teacher_source_wide",
        "lesson_source_wide",
        "complaint_category_rules",
        "task_templates",
        "config_versions",
    }
    assert "task_assignments" in migration.READ_INSERT_TABLES
    assert {
        "teachers",
        "lesson_score_results",
        "teacher_qualifications",
        "score_accounts",
        "personalized_trigger_matches",
        "notifications",
        "ops_cases",
    } <= set(migration.READ_INSERT_UPDATE_TABLES)
    assert migration.READ_INSERT_UPDATE_DELETE_TABLES == (
        "score_component_accounts",
    )
    assert migration.OUTBOX_STATUS_COLUMNS == (
        "status",
        "attempt_count",
        "last_error",
        "available_at",
        "published_at",
    )
    assert migration.TASK_ASSIGNMENT_SUPPRESSION_COLUMNS == (
        "status",
        "status_reason_code",
        "status_changed_at",
        "updated_by",
    )
    assert "task_assignments" not in migration.READ_INSERT_UPDATE_TABLES
    assert "GRANT SELECT, INSERT ON TABLE public.outbox_events" in source
    assert "GRANT UPDATE ({outbox_columns})" in source
    assert "GRANT UPDATE ({task_suppression_columns})" in source
    assert "tit_growth_app may only update task suppression columns" in source
    assert (
        "GRANT UPDATE (status, status_reason_code, status_changed_at, updated_by)\n"
        "            ON TABLE public.task_assignments"
    ) in grants
    assert "public.task_assignments,\n            public.notifications" not in grants
    assert "has_table_privilege(\n                'tit_growth_app', " in assertions
    assert "'public.task_assignments', 'UPDATE'" in assertions
    assert "'public.task_assignments',\n                      columns.attname::text" in assertions
    assert "server_default=sa.text(\"'{}'::jsonb\")" in source
    assert "'[]'::jsonb" not in source
    assert "'payload',\n                'UPDATE'" in source
    assert "GRANT ALL" not in source.upper()
    assert "GRANT INSERT ON TABLE public.teacher_source_wide" not in source
    assert "GRANT UPDATE ON TABLE public.teacher_source_wide" not in source
    assert "GRANT DELETE ON TABLE public.teacher_source_wide" not in source
