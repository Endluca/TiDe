from __future__ import annotations

import importlib.util
from pathlib import Path

from app.db_models import (
    LessonScoreComponentSettlementRecord,
    LessonScoreResultRecord,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_76_lesson_score_components.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "lesson_score_components_v76",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_76_is_an_inert_schema_only_leaf() -> None:
    migration = _load_migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert migration.revision == "20260822_76_lesson_score_components"
    assert migration.down_revision == "20260822_75_camp_state_contract"
    assert migration.SHADOW_SCHEMA_ONLY is True
    assert migration.RUNTIME_ROUTE_ACTIVATED is False
    assert migration.DEFERRED_COMPLETION_OWNERSHIP_GUARD_IMPLEMENTED is True
    assert migration.SEMANTIC_SCORE_ENTRY_GUARD_IMPLEMENTED is True
    assert "GRANT INSERT" not in source
    assert "no production writer is active" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_RUNTIME_ROUTE_INACTIVE" in source
    assert "BEFORE INSERT OR UPDATE ON public.lesson_score_results" in source


def test_settlement_identity_and_one_current_award_are_database_contracts() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for column in (
        '"source_region"',
        '"source_appoint_id"',
        '"completion_participation_seq"',
        '"component_code"',
        '"teacher_id"',
        '"award_generation"',
        '"component_score"',
        '"score_rule_version"',
        '"evidence_fingerprint"',
        '"current_award_score_entry_id"',
        '"last_reversal_score_entry_id"',
        '"materialization_origin"',
        '"materialized_by_run_id"',
        '"award_projection_generation"',
    ):
        assert column in source
    assert "pk_lesson_score_component_settlements" in source
    assert "uq_lesson_component_one_current_award" in source
    assert 'postgresql_where=sa.text("status = \'AWARDED\'")' in source
    assert "award_generation > 1" in source
    assert "last_reversal_score_entry_id IS NOT NULL" in source


def test_entry_references_are_semantically_checked_not_only_foreign_keys() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "dts_v2_lesson_score_entry_valid" in source
    assert "LESSON_COMPONENT_AWARD" in source
    assert "LESSON_COMPONENT_REVERSAL" in source
    assert "entry.teacher_id = guard_teacher_id" in source
    assert "entry.reason_code = guard_component_code" in source
    assert "entry.idempotency_key = CASE guard_entry_kind" in source
    assert "'lesson-reversal:' || expected_reversal_of" in source
    assert "entry.delta_score::numeric" in source
    assert "entry.reversal_of_score_entry_id" in source
    assert "lesson-score-component-v2" in source
    for payload_field in (
        "source_region",
        "source_appoint_id",
        "completion_participation_seq",
        "component_code",
        "award_generation",
        "evidence_fingerprint",
    ):
        assert f"entry.payload ->> '{payload_field}'" in source


def test_completion_and_transfer_guards_are_deferred_on_all_owners() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for trigger in (
        "ct_lesson_component_course_guard",
        "ct_source_course_lesson_score_guard",
        "ct_source_participation_lesson_score_guard",
        "ct_lesson_score_result_v2_course_guard",
    ):
        assert trigger in source
    assert source.count("DEFERRABLE INITIALLY DEFERRED") >= 4
    assert source.count('deferrable=True') >= 5
    assert source.count('initially="DEFERRED"') >= 5
    assert "part.participation_role = 'COMPLETION'" in source
    assert "DTS_V2_LESSON_COMPONENT_COMPLETION_MISMATCH" in source
    assert "DTS_V2_LESSON_COMPONENT_REPLACE_INVALID" in source


def test_legacy_results_are_nullable_unbackfilled_and_cannot_be_forged() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert '"v2_source_region", sa.String(length=8), nullable=True' in source
    assert '"v2_source_appoint_id", sa.String(length=512), nullable=True' in source
    assert "v2_completion_participation_seq" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_UNEXPECTED_BACKFILL" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_LEGACY_OWNERSHIP_IMMUTABLE" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_TRANSFER_REVISION_INVALID" in source
    assert "UPDATE public.lesson_score_results" not in source


def test_upgrade_and_downgrade_have_explicit_metadata_and_data_gates() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "ALTER COLUMN version_num TYPE varchar(64)" in source
    assert "DTS_V2_LESSON_COMPONENT_SCHEMA_ALREADY_PRESENT" in source
    assert "DTS_V2_LESSON_COMPONENT_DEFERRED_GUARD_MISSING" in source
    assert "IN ACCESS EXCLUSIVE MODE" in source
    assert "DTS_V2_LESSON_COMPONENT_DOWNGRADE_DATA_PRESENT" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_DOWNGRADE_OWNERSHIP_PRESENT" in source


def test_orm_metadata_matches_the_rev76_tables_constraints_and_indexes() -> None:
    settlement = LessonScoreComponentSettlementRecord.__table__
    assert set(settlement.primary_key.columns.keys()) == {
        "source_region",
        "source_appoint_id",
        "completion_participation_seq",
        "component_code",
    }
    assert set(settlement.columns.keys()) == {
        "source_region",
        "source_appoint_id",
        "completion_participation_seq",
        "component_code",
        "teacher_id",
        "status",
        "award_generation",
        "component_score",
        "score_rule_version",
        "evidence_fingerprint",
        "current_award_score_entry_id",
        "last_reversal_score_entry_id",
        "materialization_origin",
        "materialized_by_run_id",
        "award_projection_generation",
        "row_version",
        "awarded_at",
        "reversed_at",
        "created_at",
        "updated_at",
    }
    constraint_names = {
        constraint.name for constraint in settlement.constraints if constraint.name
    }
    assert {
        "pk_lesson_score_component_settlements",
        "fk_lesson_component_settlement_participation",
        "fk_lesson_component_current_award_entry",
        "fk_lesson_component_last_reversal_entry",
        "ck_lesson_component_region",
        "ck_lesson_component_code",
        "ck_lesson_component_values",
        "ck_lesson_component_status_shape",
        "ck_lesson_component_origin",
    } <= constraint_names
    assert {index.name for index in settlement.indexes} == {
        "uq_lesson_component_one_current_award",
        "ix_lesson_component_teacher",
    }

    result = LessonScoreResultRecord.__table__
    assert {
        "v2_source_region",
        "v2_source_appoint_id",
        "v2_completion_participation_seq",
        "v2_teacher_id",
        "v2_projection_generation",
    } <= set(result.columns.keys())
    result_constraint_names = {
        constraint.name for constraint in result.constraints if constraint.name
    }
    assert {
        "ck_lesson_score_result_v2_ownership",
        "fk_lesson_score_result_v2_course",
        "fk_lesson_score_result_v2_score_owner",
    } <= result_constraint_names
    assert "uq_lesson_score_result_v2_course" in {
        index.name for index in result.indexes
    }
