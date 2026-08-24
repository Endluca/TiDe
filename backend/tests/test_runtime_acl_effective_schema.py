from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.database import Base
from app import auth_models, config_models, db_models  # noqa: F401


MIGRATION_42_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_42_runtime_acl_effective.py"
)
MIGRATION_43_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_43_source_results.py"
)


def _migration_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_effective_acl_covers_every_root_table_and_all_score_views() -> None:
    migration = _migration_module(
        MIGRATION_42_PATH,
        "runtime_acl_effective_migration",
    )
    source_results = _migration_module(
        MIGRATION_43_PATH,
        "source_results_migration_for_acl",
    )

    assert len(migration.revision) <= 32
    assert source_results.down_revision == migration.revision
    managed = set(migration.MANAGED_RELATIONS) | {
        "lesson_score_results",
        "teacher_qualifications",
        "complaint_rule_imports",
        "course_favorite_attributions",
        "course_favorite_observations",
        "dts_ingest_checkpoints",
        "dts_ingest_events",
        "dts_source_rows",
        "dts_dirty_keys",
        "dts_dirty_keys_legacy_archive_v80",
        "dts_dirty_key_inputs",
        "dts_dirty_key_dependencies",
        "dts_dirty_key_state_audits",
        "dts_source_partition_epochs",
        "dts_source_row_versions",
        "dts_pipeline_control",
        "dts_pipeline_bootstrap_audits",
        "dts_ingest_issues",
        "source_courses",
        "source_course_participations",
        "teacher_student_relationship_current",
        "teacher_student_relationship_events",
        "lesson_score_component_settlements",
        "complaint_rule_publication_audits",
        "completion_correction_pointer_upgrade_archive",
        "domain_aggregate_revisions",
        "dts_projection_read_routes",
        "dts_projection_switch_audits",
        "dts_qualification_gate_commands",
        "dts_source_profile_approvals_v2",
        "dts_source_scope_commands",
        "dts_source_scope_memberships",
        "dts_source_scope_snapshots",
        "dts_source_scope_states",
        "dts_source_scope_transition_audits",
        "dts_source_snapshot_fences",
        "dts_source_snapshot_desired_rows",
        "dts_source_snapshot_rows",
        "dts_source_table_publish_generations",
        "dts_teacher_time_recheck_audits",
        "dts_teacher_time_recheck_results",
        "dts_teacher_time_recheck_schedule",
        "dts_v2_reconciliation_runs",
        "lesson_source_region_backfill_manifest",
        "lesson_source_region_migration_control",
        "ops_case_recovery_events",
        "outbox_events_legacy_archive",
        "score_entry_idempotency_aliases",
        "source_course_complaints",
        "source_course_fact_current",
        "source_course_labels",
        "source_participation_fact_current",
    }
    assert {table.name for table in Base.metadata.sorted_tables} <= managed
    assert set(migration.ROOT_RUNTIME_NO_ACCESS_VIEWS) == {
        "teacher_scorecard_current",
        "teacher_lesson_score_current",
    }


def test_effective_acl_removes_public_and_legacy_view_grants() -> None:
    source = MIGRATION_42_PATH.read_text(encoding="utf-8")

    assert "FROM PUBLIC" in source
    assert "FROM tit_growth_app" in source
    assert "aclexplode" in source
    assert "privileges.grantee = 0" in source
    assert "has_table_privilege" in source
    assert "Public grants are intentionally not recreated" in source
    assert "GRANT ALL" not in source.upper()
