from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_88a_runtime_primary_guard.py"
)


def test_runtime_guard_migration_freezes_shadow_and_primary_boundaries() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260822_88a_runtime_primary_guard"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_88_v2_score_projection"'
    ) in source
    assert "pg_advisory_xact_lock_shared" in source
    assert "'tit.dts_v2_mode'" in source
    assert "p_component <> 'DOMAIN' AND control_mode <> 'V2_PRIMARY'" in source
    assert "p_component <> 'DOMAIN' AND generation < 1" in source
    assert "GRANT SELECT ON TABLE" in source
    assert "public.dts_dirty_key_inputs" in source
    assert "REVOKE SELECT ON TABLE public.dts_pipeline_control" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
