from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_99_blacklist_threshold_three_state.py"
)


def test_revision_99_installs_fail_closed_blacklist_threshold_command() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_99_blacklist_three_state"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_98_task_v2_refresh"'
    ) in source
    assert "text,text,bigint,bigint,text,jsonb" in source
    assert "source_collection_complete" in source
    assert "source_missing_student_count" in source
    assert "threshold_state='ACTIVE'" in source
    assert "threshold_state='SUPPRESSED'" in source
    assert "threshold_state='SOURCE_MISSING'" in source
    assert "desired_status:=existing_match.match_status" in source
    assert "desired_candidate_active:=" in source
    assert "existing_match.source_candidate_active" in source


def test_revision_99_restricts_new_command_and_retires_old_signature() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "RENAME TO reconcile_blacklist_threshold_retired_v1" in source
    assert "FROM PUBLIC,tit_growth_app" in source
    assert (
        "TO tit_growth_app" in source
        and "FROM PUBLIC;" in source
    )
