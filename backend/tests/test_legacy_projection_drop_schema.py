from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import sqlalchemy as sa

from app import db_models
from app.database import Base


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260807_47_drop_legacy_projections.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "legacy_projection_drop_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _postgres_bind() -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


def test_legacy_projection_tables_are_not_runtime_orm_models() -> None:
    for model_name in (
        "TeacherMetricSnapshotRecord",
        "LessonFactRecord",
        "LessonDimensionScoreRecord",
    ):
        assert not hasattr(db_models, model_name)
    for table_name in (
        "teacher_metric_snapshots",
        "lesson_facts",
        "lesson_dimension_scores",
    ):
        assert table_name not in Base.metadata.tables


def test_upgrade_is_empty_dependency_gated_and_drops_in_fk_order(
    monkeypatch,
) -> None:
    migration = _migration_module()
    executed: list[str] = []
    dropped: list[tuple[str, dict]] = []
    monkeypatch.setattr(migration.op, "get_bind", _postgres_bind)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda table_name, **kwargs: dropped.append((table_name, kwargs)),
    )

    migration.upgrade()

    assert migration.down_revision == "20260807_46_teacher_g01_source"
    assert len(migration.revision) <= 32
    assert dropped == [
        ("lesson_dimension_scores", {"schema": "public"}),
        ("lesson_facts", {"schema": "public"}),
        ("teacher_metric_snapshots", {"schema": "public"}),
    ]

    sql = "\n".join(executed)
    assert "tide.analytics_task_business_change_v1 must be retired" in sql
    assert "LOCK TABLE" in sql
    assert "IN ACCESS EXCLUSIVE MODE" in sql
    for table_name in migration.LEGACY_TABLES:
        assert f"FROM public.{table_name}" in sql
    assert "refusing to drop populated legacy projections" in sql
    assert "pg_rewrite" in sql
    assert "dependent views" in sql
    assert "pg_constraint" in sql
    assert "external foreign keys" in sql
    assert "pg_inherits" in sql
    assert "pg_publication_rel" in sql
    assert "DROP TABLE" not in sql
    assert "CASCADE" not in sql.upper()


def test_downgrade_restores_exact_tables_constraints_indexes_and_acl(
    monkeypatch,
) -> None:
    migration = _migration_module()
    created_tables: dict[str, tuple[tuple[object, ...], dict]] = {}
    created_indexes: list[tuple[str, str, tuple[str, ...], dict]] = []
    dropped_columns: list[tuple[str, str, dict]] = []
    executed: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", _postgres_bind)
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *elements, **kwargs: created_tables.setdefault(
            name,
            (elements, kwargs),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns, **kwargs: created_indexes.append(
            (name, table, tuple(columns), kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column, **kwargs: dropped_columns.append(
            (table, column, kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.downgrade()

    assert list(created_tables) == [
        "teacher_metric_snapshots",
        "lesson_facts",
        "lesson_dimension_scores",
    ]
    assert all(options == {"schema": "public"} for _, options in created_tables.values())

    expected_columns = {
        "teacher_metric_snapshots": (
            "snapshot_id",
            "batch_id",
            "teacher_id",
            "snapshot_label",
            "source_row_number",
            "data_mode",
            "real_name",
            "employment_status",
            "bu",
            "based_type",
            "teach_area_type",
            "onboard_date",
            "onboard_30d_end_date",
            "lessons_completed",
            "total_completed_cnt",
            "peak_completed_cnt",
            "perfect_cnt",
            "on_time_completed_cnt",
            "feedback_praise_cnt",
            "feedback_favorite_cnt",
            "completed_again_student_15d_cnt",
            "late_cnt",
            "early_cnt",
            "real_absent_cnt",
            "severe_redline_event",
            "capacity_score",
            "new_teacher_task_score",
            "class_quality_no_issue_rate",
            "reliability_score",
            "user_feedback_score",
            "class_quality_score",
            "raw_total_score",
            "public_total_score",
            "metric_inputs",
            "metric_provenance",
            "raw_payload",
            "created_at",
            "updated_at",
            "score_rule_version",
            "score_policy_snapshot",
            "score_policy_sha256",
            "peak_slot_cnt",
            "first_booked_date",
            "is_cpl_tesol",
            "is_self_introduce",
            "absent_cnt",
        ),
        "lesson_facts": (
            "lesson_id",
            "source_appoint_id",
            "camp_enrollment_id",
            "teacher_id",
            "scheduled_start_at",
            "scheduled_end_at",
            "lesson_lifecycle_status",
            "valid_for_scoring",
            "evidence_status",
            "data_mode",
            "payload",
            "created_at",
            "updated_at",
            "lesson_local_date",
            "lesson_local_time",
            "student_id_hash",
            "is_late",
            "is_early",
            "is_false_early_leave",
            "negative_score",
            "has_negative_feedback_tag",
            "negative_tag_value",
            "absence_reason_detail",
            "complaint_category_l1",
            "complaint_category_l2",
            "complaint_category_l3",
            "complaint_source_level",
            "complaint_level_rank",
            "complaint_route",
            "complaint_rule_id",
            "is_blocked",
            "is_favorited",
            "has_positive_feedback_tag",
            "positive_tag_value",
            "is_rebooked",
            "is_camera_off",
            "is_cpu_usage_high",
            "is_network_delay_high",
            "source_batch_id",
            "source_record_id",
            "is_peak",
            "feedback_detail",
            "negative_tag_values",
        ),
        "lesson_dimension_scores": (
            "score_state_id",
            "camp_enrollment_id",
            "lesson_id",
            "teacher_id",
            "dimension",
            "current_score",
            "evidence_status",
            "evidence_coverage",
            "score_rule_version",
            "current_revision",
            "score_as_of",
            "last_score_entry_id",
            "payload",
            "updated_at",
        ),
    }
    for table_name, expected_names in expected_columns.items():
        elements, _options = created_tables[table_name]
        columns = [item for item in elements if isinstance(item, sa.Column)]
        assert tuple(column.name for column in columns) == expected_names

    assert dropped_columns == [
        (
            "lesson_facts",
            "negative_tag_value",
            {"schema": "public"},
        )
    ]

    constraints = {
        table_name: {
            item.name
            for item in elements
            if isinstance(item, sa.Constraint)
        }
        for table_name, (elements, _options) in created_tables.items()
    }
    assert constraints["teacher_metric_snapshots"] == {
        "ck_teacher_metric_snapshot_mixed",
        "fk_teacher_metric_snapshot_batch",
        "teacher_metric_snapshots_pkey",
        "uq_teacher_metric_snapshot_batch_teacher",
    }
    assert constraints["lesson_facts"] == {
        "ck_lesson_fact_complaint_rank",
        "lesson_facts_teacher_id_fkey",
        "fk_lesson_fact_complaint_rule",
        "fk_lesson_fact_source_batch",
        "fk_lesson_fact_source_record",
        "lesson_facts_pkey",
        "uq_lesson_fact_source_record",
    }
    assert constraints["lesson_dimension_scores"] == {
        "lesson_dimension_scores_lesson_id_fkey",
        "lesson_dimension_scores_teacher_id_fkey",
        "lesson_dimension_scores_pkey",
        "uq_lesson_dimension_state",
    }

    expected_indexes = {
        "ix_teacher_metric_snapshots_batch_id",
        "ix_teacher_metric_snapshots_teacher_id",
        "ix_teacher_metric_snapshots_snapshot_label",
        "ix_teacher_metric_snapshots_employment_status",
        "ix_teacher_metric_snapshots_bu",
        "ix_teacher_metric_snapshots_based_type",
        "ix_teacher_metric_snapshots_teach_area_type",
        "ix_teacher_metric_snapshot_ops_filter",
        "ix_teacher_metric_snapshots_score_policy_sha256",
        "ix_teacher_metric_snapshots_first_booked_date",
        "ix_lesson_facts_camp_enrollment_id",
        "ix_lesson_facts_teacher_id",
        "ix_lesson_facts_student_id_hash",
        "ix_lesson_facts_complaint_rule_id",
        "ix_lesson_facts_source_batch_id",
        "ix_lesson_fact_teacher_local_date",
        "ix_lesson_fact_complaint_l3",
        "ix_lesson_dimension_scores_camp_enrollment_id",
        "ix_lesson_dimension_scores_lesson_id",
        "ix_lesson_dimension_scores_teacher_id",
        "ix_lesson_dimension_score_teacher_lesson",
    }
    assert {name for name, _table, _columns, _options in created_indexes} == (
        expected_indexes
    )
    assert all(options == {"schema": "public"} for *_rest, options in created_indexes)

    teacher_columns = {
        item.name: item
        for item in created_tables["teacher_metric_snapshots"][0]
        if isinstance(item, sa.Column)
    }
    lesson_columns = {
        item.name: item
        for item in created_tables["lesson_facts"][0]
        if isinstance(item, sa.Column)
    }
    score_columns = {
        item.name: item
        for item in created_tables["lesson_dimension_scores"][0]
        if isinstance(item, sa.Column)
    }
    assert str(teacher_columns["peak_slot_cnt"].server_default.arg) == "0"
    assert str(lesson_columns["negative_tag_values"].server_default.arg) == (
        "'[]'::jsonb"
    )
    assert str(score_columns["updated_at"].server_default.arg) == (
        "CURRENT_TIMESTAMP"
    )

    acl_sql = "\n".join(executed)
    assert "FROM PUBLIC, tit_growth_app" in acl_sql
    assert "GRANT SELECT, UPDATE ON TABLE" in acl_sql
    assert "public.teacher_metric_snapshots" in acl_sql
    assert "GRANT SELECT ON TABLE" in acl_sql
    assert "public.lesson_facts" in acl_sql
    assert "GRANT SELECT, INSERT, DELETE ON TABLE" in acl_sql
    assert "public.lesson_dimension_scores" in acl_sql
    assert "tit_teacher_crud" in acl_sql
    assert "tit_teacher_crud" in acl_sql
    assert "GRANT ALL" not in acl_sql.upper()


def test_revision_is_postgresql_only(monkeypatch) -> None:
    migration = _migration_module()
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda _statement: (_ for _ in ()).throw(
            AssertionError("sqlite migration must be a no-op")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sqlite migration must not drop tables")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sqlite migration must not create tables")
        ),
    )

    migration.upgrade()
    migration.downgrade()
