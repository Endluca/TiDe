from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260822_98_shared_task_v2_refresh.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("task_v2_refresh", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_98_is_linear_and_command_is_restricted() -> None:
    migration = _module()
    assert migration.revision == "20260822_98_task_v2_refresh"
    assert migration.down_revision == "20260822_97_teacher_time_recheck"
    sql = PATH.read_text(encoding="utf-8")
    assert "actor_name<>'{APP_ROLE}'" in sql
    assert "control_row.mode<>'V2_PRIMARY'" in sql
    assert "task.assignment_changed.shared" in sql
    assert "input_kind IN ('SOURCE_REVISION','SCOPE_REVISION')" in sql
    assert "'OPERATOR_RECOVERY'" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "TO {APP_ROLE}" in sql
    assert "DTS_V2_SHARED_TASK_REFRESH_DOWNGRADE_REQUIRES_NON_PRIMARY" in sql
