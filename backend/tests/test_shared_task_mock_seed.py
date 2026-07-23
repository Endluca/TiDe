from __future__ import annotations

from sqlalchemy import func, select

from app.database import engine, session_scope
from app.db_models import (
    NotificationRecord,
    OpsCaseRecord,
    OutboundOutputRecord,
    OutboxEventRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
)
from app.shared_task_mock_seed import (
    SHARED_TASK_MOCK_SCENARIO,
    SHARED_TASK_MOCK_TEACHER_ID,
    seed_shared_task_mock_outputs,
)
from app.services import GrowthService
from app.store import DatabaseStore
from app.task_service import TaskService


def test_shared_task_mock_seed_is_current_idempotent_and_non_deliverable() -> None:
    with session_scope(engine) as session:
        score_count_before = session.scalar(select(func.count()).select_from(ScoreEntryRecord))

    first = seed_shared_task_mock_outputs(engine)
    second = seed_shared_task_mock_outputs(engine)

    assert first == second
    assert first["scenario"] == SHARED_TASK_MOCK_SCENARIO
    assert first["teacher_id"] == SHARED_TASK_MOCK_TEACHER_ID
    assert first["assignment_statuses"] == {
        "G01": "ASSIGNED",
        "G02": "IN_PROGRESS",
        "G03": "COMPLETED",
    }
    assert first["output_display_types"] == [
        "EXTERNAL_ACTION_REQUEST",
        "IN_APP_NOTIFICATION",
        "OPS_CASE",
        "REMINDER",
    ]
    assert first["score_entries_written"] == 0
    assert first["real_delivery_enabled"] is False

    with session_scope(engine) as session:
        assignments = session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.teacher_id == SHARED_TASK_MOCK_TEACHER_ID
            )
        ).all()
        assert len(assignments) == 3
        assert {item.task_code for item in assignments} == {"G01", "G02", "G03"}
        assert {item.task_kind for item in assignments} == {"FIXED_GROWTH"}
        assert {item.creator_system for item in assignments} == {"TRIGGER_CENTER"}
        assert {item.source_mode for item in assignments} == {"MOCK"}
        assert {item.created_by for item in assignments} == {"MOCK_SEED"}
        outputs = session.scalars(
            select(OutboundOutputRecord).where(
                OutboundOutputRecord.source_type == "MOCK_SEED"
            )
        ).all()
        assert len(outputs) == 4
        assert {item.output_id for item in outputs} == set(first["output_ids"])
        assert {item.payload["origin"] for item in outputs} == {"MOCK_SEED"}
        assert {item.payload["source"] for item in outputs} == {"MOCK_SEED"}
        assert {item.payload["delivery_disabled"] for item in outputs} == {True}
        assert not any(item.retryable for item in outputs)
        action = next(
            item for item in outputs if item.display_type == "EXTERNAL_ACTION_REQUEST"
        )
        assert action.requires_human_approval is True
        assert action.payload["execution_allowed"] is False

        notification = session.get(NotificationRecord, "MOCK-NOTIFICATION-SHARED-G01")
        assert notification is not None
        assert notification.status == "CANCELLED"
        assert notification.payload["delivery_disabled"] is True

        case = session.get(OpsCaseRecord, "MOCK-CASE-SHARED-G02")
        assert case is not None
        assert case.payload["mock_only"] is True
        assert case.payload["execution_allowed"] is False
        assert case.payload["summary"]
        assert case.payload["recommended_action"]

        pending_outbox = session.scalars(
            select(OutboxEventRecord).where(
                OutboxEventRecord.aggregate_id.in_(first["assignment_ids"]),
                OutboxEventRecord.status != "CANCELLED",
            )
        ).all()
        assert pending_outbox == []
        score_count_after = session.scalar(select(func.count()).select_from(ScoreEntryRecord))
        assert score_count_after == score_count_before

    task_page_rows = TaskService(engine).list_assignments(
        teacher_id=SHARED_TASK_MOCK_TEACHER_ID
    )
    assert {item["status"] for item in task_page_rows} == {
        "ASSIGNED",
        "IN_PROGRESS",
        "COMPLETED",
    }
    assert all(item["title"] for item in task_page_rows)

    reloaded = DatabaseStore(engine, seed_on_empty=False)
    output_page_rows = [
        item
        for item in reloaded.outbound_outputs.values()
        if item.get("source_type") == "MOCK_SEED"
    ]
    assert {item["display_type"] for item in output_page_rows} == {
        "IN_APP_NOTIFICATION",
        "REMINDER",
        "OPS_CASE",
        "EXTERNAL_ACTION_REQUEST",
    }
    action_queue = GrowthService(reloaded).action_queue()
    mock_case = next(
        item for item in action_queue if item["queue_id"] == "MOCK-CASE-SHARED-G02"
    )
    assert mock_case["summary"] == "模拟教师正在处理 G02，供运营行动台检查 Case 展示。"
