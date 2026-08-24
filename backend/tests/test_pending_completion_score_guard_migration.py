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
    / "20260822_78_pending_completion_score_guard.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "pending_completion_score_guard_v78",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_78_is_the_rev77_leaf_and_does_not_rewrite_history() -> None:
    migration = _load_migration()

    assert migration.revision == "20260822_78_pending_score_guard"
    assert migration.down_revision == "20260822_77_retire_cpu_network"
    assert "20260822_76_lesson_score_components.py" not in str(MIGRATION_PATH)


def test_pending_relaxation_keeps_cross_table_matching_guards() -> None:
    migration = _load_migration()
    relaxed = migration._assert_course_function_sql(reject_pending=False)
    rev76 = migration._assert_course_function_sql(reject_pending=True)

    assert "completion_conflict_status = 'PENDING'" not in relaxed
    assert rev76.count("completion_conflict_status = 'PENDING'") == 2
    for required_guard in (
        "course_row.completion_voided_at IS NOT NULL",
        "settlement_row.completion_participation_seq",
        "settlement_row.teacher_id",
        "part.participation_role = 'COMPLETION'",
        "DTS_V2_LESSON_COMPONENT_AWARD_ENTRY_MISMATCH",
        "result_row.v2_completion_participation_seq",
        "DTS_V2_LESSON_SCORE_RESULT_COMPLETION_MISMATCH",
    ):
        assert required_guard in relaxed


def test_pending_writes_are_immediately_blocked_but_reversal_is_not() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "BEFORE INSERT OR UPDATE" in source
    assert "TG_OP = 'INSERT' OR NEW.status = 'AWARDED'" in source
    assert "DTS_V2_LESSON_COMPONENT_PENDING_AWARD_WRITE_FORBIDDEN" in source
    assert "BEFORE INSERT OR UPDATE OR DELETE" in source
    assert "DTS_V2_LESSON_SCORE_RESULT_PENDING_WRITE_FORBIDDEN" in source
    assert source.count("FOR UPDATE;") == 2
    assert "NEW.status = 'REVERSED'" not in source
    assert "DTS_V2_PENDING_SCORE_DOWNGRADE_DATA_PRESENT" in source


def test_orm_comments_match_rev78_pending_freeze_contract() -> None:
    settlement = LessonScoreComponentSettlementRecord.__table__
    result = LessonScoreResultRecord.__table__

    assert settlement.comment == (
        "DTS v2 shadow: PENDING retains frozen awards but blocks "
        "new/replacement/re-award writes; no production writer is active"
    )
    assert result.c.v2_completion_participation_seq.comment == (
        "Frozen v2 completion ownership; PENDING retains existing matching "
        "results but forbids result writes"
    )
