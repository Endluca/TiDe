from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event

from app.config_models import ConfigKey, DEFAULT_CONFIG_PAYLOADS
from app.database import engine, session_scope
from app.db_models import TeacherRecord
from app.score_read_model import (
    SOURCE_SNAPSHOT_LABEL,
    ScoreProjectionSourceContractError,
    refresh_persisted_score_read_models,
)


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _teacher(
    teacher_id: str,
    *,
    source_snapshot_label: str | None,
    total_score: float = 0,
) -> TeacherRecord:
    return TeacherRecord(
        teacher_id=teacher_id,
        camp_enrollment_id=f"CAMP:{teacher_id}",
        name=teacher_id,
        country=None,
        timezone="UTC",
        camp_day=1,
        graduation_state="IN_PROGRESS",
        gold_qualified=False,
        total_score=total_score,
        graduation_threshold=100,
        data_mode="REAL",
        source_snapshot_label=source_snapshot_label,
        payload={},
        created_at=NOW,
        updated_at=NOW,
    )


def test_module_has_no_retired_projection_model_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "score_read_model.py"
    ).read_text(encoding="utf-8")

    for retired_model in (
        "TeacherMetricSnapshotRecord",
        "LessonFactRecord",
        "LessonDimensionScoreRecord",
    ):
        assert retired_model not in source


def test_targeted_refresh_rejects_non_source_wide_teacher_without_legacy_sql() -> None:
    teacher_id = "LEGACY-REFRESH-REJECTED"
    with session_scope(engine) as session:
        session.add(
            _teacher(
                teacher_id,
                source_snapshot_label="LEGACY_SNAPSHOT",
                total_score=17,
            )
        )

    statements: list[str] = []

    def capture_sql(*args) -> None:  # noqa: ANN002
        statements.append(str(args[2]).casefold())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        with pytest.raises(ScoreProjectionSourceContractError) as caught:
            with session_scope(engine) as session:
                refresh_persisted_score_read_models(
                    session,
                    trigger_type="MANUAL_REBUILD",
                    teacher_ids=[teacher_id],
                )
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    assert caught.value.error_code == (
        "SCORE_PROJECTION_REQUIRES_SOURCE_WIDE_CURRENT"
    )
    assert caught.value.teacher_ids == (teacher_id,)
    assert "count=1" in str(caught.value)
    executed_sql = "\n".join(statements)
    assert "teacher_metric_snapshots" not in executed_sql
    assert "lesson_facts" not in executed_sql
    assert "lesson_dimension_scores" not in executed_sql
    with session_scope(engine) as session:
        assert session.get(TeacherRecord, teacher_id).total_score == 17


def test_full_refresh_fails_closed_before_mixing_current_and_old_teachers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_id = "CURRENT-IN-MIXED-REFRESH"
    legacy_id = "LEGACY-IN-MIXED-REFRESH"
    with session_scope(engine) as session:
        session.add_all(
            [
                _teacher(
                    current_id,
                    source_snapshot_label=SOURCE_SNAPSHOT_LABEL,
                ),
                _teacher(
                    legacy_id,
                    source_snapshot_label=None,
                ),
            ]
        )

    delegated = False

    def must_not_delegate(*args, **kwargs):  # noqa: ANN002,ANN003
        nonlocal delegated
        delegated = True
        raise AssertionError("mixed source contracts must fail before delegation")

    monkeypatch.setattr(
        "app.source_wide_worker.refresh_source_wide_score_read_models",
        must_not_delegate,
    )
    with pytest.raises(ScoreProjectionSourceContractError) as caught:
        with session_scope(engine) as session:
            refresh_persisted_score_read_models(
                session,
                trigger_type="SCORE_POLICY_PUBLISHED",
            )

    assert legacy_id in caught.value.teacher_ids
    assert current_id not in caught.value.teacher_ids
    assert delegated is False


def test_current_refresh_delegates_in_same_transaction_and_preserves_result_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher_id = "SOURCE-WIDE-REFRESH"
    with session_scope(engine) as session:
        session.add(
            _teacher(
                teacher_id,
                source_snapshot_label=SOURCE_SNAPSHOT_LABEL,
            )
        )

    policy = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION])
    captured: dict[str, object] = {}

    def fake_source_refresh(session, **kwargs):  # noqa: ANN001,ANN003
        assert session.in_transaction()
        captured.update(kwargs)
        return {
            "teacher_count": 1,
            "lesson_result_changes": 2,
            "component_changes": 3,
            "account_changes": 4,
            "qualification_changes": 1,
            "score_rule_version": policy["policy_version"],
        }

    monkeypatch.setattr(
        "app.source_wide_worker.refresh_source_wide_score_read_models",
        fake_source_refresh,
    )
    with session_scope(engine) as session:
        result = refresh_persisted_score_read_models(
            session,
            trigger_type="SCORE_POLICY_PUBLISHED",
            trigger_ref="CFG-NEW",
            teacher_ids=[teacher_id],
            score_policy_payload=policy,
            score_config_version_id="CFG-NEW",
        )

    assert captured["teacher_ids"] == [teacher_id]
    assert captured["score_policy_payload"] == policy
    assert captured["score_config_version_id"] == "CFG-NEW"
    assert captured["acquire_lock"] is False
    assert result["teacher_count"] == 1
    assert result["lesson_score_state_count"] == 2
    assert result["component_account_count"] == 3
    assert result["source_wide_recalculation"]["qualification_changes"] == 1
    assert result["source_versions"]["teacher_batch_ids"] == []
    assert result["source_versions"]["lesson_batch_ids"] == []
    assert result["source_versions"]["score_config_version_id"] == "CFG-NEW"
    assert len(result["source_versions"]["score_policy_sha256"]) == 64


def test_source_refresh_failure_rolls_back_the_caller_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher_id = "SOURCE-WIDE-ROLLBACK"
    with session_scope(engine) as session:
        session.add(
            _teacher(
                teacher_id,
                source_snapshot_label=SOURCE_SNAPSHOT_LABEL,
                total_score=11,
            )
        )

    def fail_after_write(session, **kwargs):  # noqa: ANN001,ANN003
        teacher = session.get(TeacherRecord, teacher_id)
        teacher.total_score = 999
        session.flush()
        raise RuntimeError("source-wide projection failed")

    monkeypatch.setattr(
        "app.source_wide_worker.refresh_source_wide_score_read_models",
        fail_after_write,
    )
    with pytest.raises(RuntimeError, match="source-wide projection failed"):
        with session_scope(engine) as session:
            refresh_persisted_score_read_models(
                session,
                trigger_type="SCORE_POLICY_PUBLISHED",
                teacher_ids=[teacher_id],
                score_policy_payload=deepcopy(
                    DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]
                ),
                score_config_version_id="CFG-ROLLBACK",
            )

    with session_scope(engine) as session:
        assert session.get(TeacherRecord, teacher_id).total_score == 11
