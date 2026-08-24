from __future__ import annotations

from pathlib import Path

from app.database import Base
import app.db_models  # noqa: F401


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260822_83_dts_v2_scope_coordinator.py"
)


def test_revision_83_is_additive_fail_closed_and_role_restricted() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, None] = "20260822_82_v2_runtime_acl"' in source
    assert "SNAPSHOT_DIFF_REQUIRED_NOT_IMPLEMENTED" in source
    assert "CDC_OVERLAY_REPLAY_NOT_IMPLEMENTED" in source
    assert "SOURCE_SCOPE_FALSE_COMPLETE_FORBIDDEN" in source
    assert "tit_dts_scope_coordinator_runtime" in source
    assert "SECURITY DEFINER" in source
    assert "FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "requests." not in source
    assert "kafka" not in source.lower()


def test_revision_83_metadata_contains_the_complete_scope_identity_graph() -> None:
    expected = {
        "dts_source_table_publish_generations",
        "dts_source_scope_snapshots",
        "dts_source_snapshot_fences",
        "dts_source_snapshot_rows",
        "dts_source_scope_states",
        "dts_source_scope_memberships",
        "dts_source_scope_commands",
        "dts_source_scope_transition_audits",
    }
    assert expected <= set(Base.metadata.tables)

    state = Base.metadata.tables["dts_source_scope_states"]
    membership = Base.metadata.tables["dts_source_scope_memberships"]
    assert {
        "source_region",
        "source_table",
        "scope_kind",
        "scope_level",
        "scope_key",
    } <= {column.name for column in state.primary_key.columns}
    assert membership.c.effective_is_present.computed is not None
    assert membership.c.cdc_overlay_is_present.nullable


def test_revision_83_seeds_only_the_frozen_dom_ovs_whitelist() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    expected_tables = {
        "dom_appoint",
        "dom_complaint",
        "dom_complaint_cate",
        "dom_grading_label",
        "dom_grading_label_log",
        "dom_qa_task_close_camera_record",
        "dom_teacher",
        "dom_teacher_absent_reason",
        "dom_teacher_blacklist",
        "dom_teacher_certification",
        "dom_teacher_class_schedule",
        "dom_teacher_favorite",
        "dom_teacher_penalty",
        "dom_user_complaint",
        "dom_user_teacher_grading",
        "ovs_appoint",
        "ovs_complaint",
        "ovs_grading_label",
        "ovs_grading_label_log",
        "ovs_qa_task_close_camera_record",
        "ovs_teacher_blacklist",
        "ovs_teacher_favorite",
        "ovs_user_complaint",
        "ovs_user_teacher_grading",
    }
    source_tables_block = source.split("SOURCE_TABLES:", 1)[1].split(
        "HISTORY_SOURCE_TABLES:", 1
    )[0]
    for table in expected_tables:
        assert f'"{table}"' in source_tables_block
    assert source_tables_block.count('(\"dom\",') == 15
    assert source_tables_block.count('(\"ovs\",') == 9
