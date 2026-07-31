from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260729_38_task_score_catalog_alignment.py"
    )
    spec = importlib.util.spec_from_file_location(
        "task_score_catalog_alignment_v38",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_v38_aligns_exact_points_only_before_any_earned_task_score(
    monkeypatch,
) -> None:
    migration = _load_migration()
    assert migration.down_revision == "20260729_37_read_perf"

    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()

    sql = "\n".join(executed)
    assert "('G01', 3)" in sql
    assert "('G02', 2)" in sql
    assert "('G03', 2)" in sql
    assert "('G04', 3)" in sql
    assert "('G05', 3)" in sql
    assert "('G06', 4)" in sql
    assert "('G07', 3)" in sql
    assert "('G08', 5)" in sql
    assert "('G09', 5)" in sql
    assert "entry_type = 'FIXED_TASK_AWARD'" in sql
    assert "public.score_accounts" in sql
    assert "public.score_component_accounts" in sql
    assert "governed recalculation" in sql
    assert "jsonb_build_object" in sql


def test_v38_downgrade_does_not_restore_the_wrong_distribution(
    monkeypatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda _statement: (_ for _ in ()).throw(
            AssertionError("downgrade must not rewrite task points")
        ),
    )

    assert migration.downgrade() is None

