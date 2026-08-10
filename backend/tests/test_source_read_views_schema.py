from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_44_source_read_views.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "source_read_views_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture(monkeypatch, migration, method: str) -> str:
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
    getattr(migration, method)()
    return "\n".join(executed)


def test_revision_44_preserves_the_teacher_facing_column_contract() -> None:
    migration = _migration_module()

    assert migration.down_revision == "20260806_43_source_results"
    assert len(migration.revision) <= 32
    assert migration.TEACHER_SCORECARD_COLUMNS == (
        "teacher_id",
        "camp_enrollment_id",
        "raw_total_score",
        "public_total_score",
        "graduation_state",
        "graduation_qualified",
        "gold_qualified",
        "graduation_threshold",
        "gold_threshold",
        "mandatory_task_completed_count",
        "mandatory_task_total_count",
        "score_rule_version",
        "score_policy_sha256",
        "calculated_at",
        "dimensions",
    )
    assert migration.TEACHER_LESSON_SCORE_COLUMNS == (
        "teacher_id",
        "lesson_id",
        "lesson_sequence",
        "lesson_count",
        "source_appoint_id",
        "scheduled_start_at",
        "lesson_local_date",
        "lesson_local_time",
        "lesson_lifecycle_status",
        "valid_for_scoring",
        "evidence_status",
        "lesson_total_score",
        "score_rule_version",
        "updated_at",
        "business_facts",
        "dimensions",
        "is_perfect",
    )


def test_upgrade_switches_only_the_two_views_to_current_source_results(
    monkeypatch,
) -> None:
    migration = _migration_module()
    sql = _capture(monkeypatch, migration, "upgrade")

    assert "CREATE OR REPLACE VIEW public.teacher_scorecard_current" in sql
    assert "CREATE OR REPLACE VIEW public.teacher_lesson_score_current" in sql
    assert "FROM public.teachers AS teacher" in sql
    assert "LEFT JOIN public.teacher_source_wide AS source" in sql
    assert "COALESCE(\n                source.tchr_id," in sql
    assert "JOIN public.teacher_qualifications AS qualification" in sql
    assert "FROM public.lesson_source_wide AS source" in sql
    assert "JOIN public.lesson_score_results AS result" in sql
    assert "teacher.source_snapshot_label = 'SOURCE_WIDE_CURRENT'" in sql
    assert "score_dimensions.dimension_count = 5" in sql
    assert "jsonb_typeof(result.dimensions) = 'object'" in sql

    assert "public.teacher_metric_snapshots" not in sql
    assert "public.lesson_facts" not in sql
    assert "public.lesson_dimension_scores" not in sql

    for dimension in (
        "USER_FEEDBACK",
        "RELIABILITY",
        "CLASS_QUALITY",
        "CAPACITY",
        "NEW_TEACHER_TASK",
    ):
        assert dimension in sql
    for component in (
        "FEEDBACK_PRAISE",
        "FEEDBACK_FAVORITE",
        "PERFECT_COMPLETED",
        "PEAK_COMPLETED",
        "CLASS_QUALITY_HARDWARE",
        "CAPACITY_PEAK_SLOT_40",
        "G01",
        "G09",
    ):
        assert component in sql

    assert "'source_appoint_id'" not in sql
    assert 'source."课程id" AS source_appoint_id' in sql
    assert "AT TIME ZONE 'UTC'" in sql
    assert "result.score_rule_version::text AS score_rule_version" in sql
    assert (
        "lower(btrim(COALESCE(source.\"课程状态\", ''))) = 'end'"
        in sql
    )
    assert "'is_rebooked', NULL" in sql
    assert "'hardware_quality_passed'" in sql
    assert "'is_perfect'" in sql

    assert "TO tit_growth_app" not in sql
    for role in ("tit_teacher_crud", "tide_business_app"):
        assert f"TO {role}" in sql


def test_scorecard_keeps_earned_results_after_a_source_teacher_delete(
    monkeypatch,
) -> None:
    migration = _migration_module()
    sql = _capture(monkeypatch, migration, "upgrade")

    assert "FROM public.teachers AS teacher" in sql
    assert "LEFT JOIN public.teacher_source_wide AS source" in sql
    assert "teacher.teacher_id" in sql
    assert "qualification.graduation_qualified" in sql
    assert "teacher.source_snapshot_label = 'SOURCE_WIDE_CURRENT'" in sql


def test_downgrade_restores_the_revision_43_legacy_view_dependencies(
    monkeypatch,
) -> None:
    migration = _migration_module()
    sql = _capture(monkeypatch, migration, "downgrade")

    assert "public.teacher_metric_snapshots AS snapshot" in sql
    assert "FROM public.lesson_facts AS fact" in sql
    assert "FROM public.lesson_dimension_scores AS score" in sql
    assert "teacher.graduation_state = 'GRADUATED'" in sql
    assert "teacher.gold_qualified" in sql

    assert "public.teacher_source_wide" not in sql
    assert "public.lesson_source_wide" not in sql
    assert "public.lesson_score_results" not in sql
    assert "public.teacher_qualifications" not in sql


def test_revision_44_is_a_postgresql_only_view_change(monkeypatch) -> None:
    migration = _migration_module()
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
    migration.downgrade()

    assert executed == []
