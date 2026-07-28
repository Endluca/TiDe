from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260728_31_persist_task_why_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "persist_task_why_evidence",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_persists_the_same_teacher_evidence_once() -> None:
    migration = _load_migration()
    base = (
        "This task was assigned because one or more lessons contained an "
        "attendance signal such as absence, late arrival, or early departure. "
        "Complete the attendance training and quiz."
    )
    snapshot = {
        "lesson_ids": ["530737235"],
        "signal_samples": [
            {
                "lesson_id": "530737235",
                "evidence": {
                    "lesson_id": "530737235",
                    "is_late": True,
                    "is_early": False,
                    "is_fake_early": False,
                },
            }
        ],
    }

    enriched = migration._with_teacher_evidence(base, snapshot)

    assert enriched == (
        f"{base} Evidence: Lesson IDs: 530737235; late arrival recorded."
    )
    assert migration._with_teacher_evidence(enriched, snapshot) == enriched
    assert migration.down_revision == "20260728_30_renumber_g01_g09"
