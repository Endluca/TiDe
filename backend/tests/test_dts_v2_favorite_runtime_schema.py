from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_87_favorite_runtime.py"
)


def test_revision_87_is_additive_after_ops_case_v2() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_87_favorite_runtime"' in source
    assert 'down_revision: Union[str, None] = "20260822_86_ops_case_v2"' in source
    assert "op.create_table(" not in source
    assert "lesson_score_components" not in source
    assert "lesson_component_settlements" not in source


def test_runtime_uses_database_due_time_primary_gate_and_global_generation() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "observed_at<=claim_as_of" in source
    assert "lease_expires_at=claim_as_of+interval '60 seconds'" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "favorite_runtime_mode_v1() IS DISTINCT FROM 'V2_PRIMARY'" in source
    assert "favorite_runtime_projection_generation_v1()" in source
    assert "p_projection_generation IS DISTINCT FROM global_generation" in source
    assert "completion_end_time + interval '24 hours'" in source


def test_runtime_score_path_is_rev74_specific_idempotent_and_reversible() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "favorite-attribution-v2" in source
    assert "FAVORITE_AWARD" in source
    assert "FAVORITE_REVERSAL" in source
    assert "uq_score_entries_reversal_once_v2" in source
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in source
    assert "ct_favorite_attribution_score_v2" in source
    assert "DTS_V2_FAVORITE_RUNTIME_DOWNGRADE_DATA_PRESENT" in source


def test_shared_runtime_receives_commands_and_new_tables_stay_protected() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert (
        'OUTBOX_RUNTIME_ROLE = "tit_growth_app"' in source
    )
    assert (
        'RECOVERY_RUNTIME_ROLE = '
        '"tit_growth_app"' in source
    )
    assert "FROM {OUTBOX_RUNTIME_ROLE}" in source
    assert "TO {OUTBOX_RUNTIME_ROLE}" in source
    assert "TO {RECOVERY_RUNTIME_ROLE}" in source
    assert "CREATE ROLE" not in source
    assert "COMMENT ON ROLE" not in source
    assert "REVOKE ALL PRIVILEGES ON TABLE" in source
    assert "public.course_favorite_observations" in source
    assert "public.course_favorite_attributions" in source
