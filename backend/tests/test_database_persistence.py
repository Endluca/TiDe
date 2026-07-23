from __future__ import annotations

from datetime import datetime, timezone

from app.database import engine, session_scope
from app.db_models import (
    NotificationEventRecord,
    NotificationRecord,
    TaskAssignmentRecord,
)
from app.store import DatabaseStore
from app.task_service import TaskService


def test_shared_assignment_is_database_fact_and_survives_repository_restart() -> None:
    now = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            TaskAssignmentRecord(
                assignment_id="ASSIGNMENT-PERSISTED-G02",
                teacher_id="T-1002",
                task_code="G02",
                template_version_id="G02:v1",
                task_kind="FIXED_GROWTH",
                creator_system="TRIGGER_CENTER",
                status="VIEWED",
                priority="P1",
                why="A verified device and network check is required.",
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=None,
                source_mode="MOCK",
                dedupe_key="fixed:T-1002:G02",
                created_by="tit_teacher_test",
                updated_by="tit_teacher_test",
                row_version=2,
                assigned_at=now,
                status_changed_at=now,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )

    first_repository = TaskService(engine)
    restarted_repository = TaskService(engine)

    assert first_repository.list_assignments() == restarted_repository.list_assignments()
    assert restarted_repository.list_assignments()[0]["assignment_id"] == (
        "ASSIGNMENT-PERSISTED-G02"
    )
    assert restarted_repository.list_assignments()[0]["status"] == "VIEWED"


def test_legacy_working_set_never_copies_shared_assignments_into_memory() -> None:
    now = datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        session.add(
            TaskAssignmentRecord(
                assignment_id="ASSIGNMENT-DB-ONLY-G03",
                teacher_id="T-1003",
                task_code="G03",
                template_version_id="G03:v1",
                task_kind="FIXED_GROWTH",
                creator_system="TRIGGER_CENTER",
                status="ASSIGNED",
                priority="P1",
                why="Platform policies are part of the mandatory path.",
                due_at=None,
                timezone_used=None,
                timezone_source=None,
                timezone_verified_at=None,
                status_reason_code=None,
                source_mode="MOCK",
                dedupe_key="fixed:T-1003:G03",
                created_by="tit_teacher_test",
                updated_by="tit_teacher_test",
                row_version=1,
                assigned_at=now,
                status_changed_at=now,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )

    restarted_store = DatabaseStore(engine, seed_on_empty=False)

    assert restarted_store.tasks == {}
    assert TaskService(engine).list_assignments()[0]["assignment_id"] == (
        "ASSIGNMENT-DB-ONLY-G03"
    )


def test_notification_operational_indexes_are_part_of_model_metadata() -> None:
    assert {
        index.name for index in NotificationRecord.__table__.indexes
    } >= {"ix_notifications_teacher_requested_desc"}
    assert {
        index.name for index in NotificationEventRecord.__table__.indexes
    } >= {"uq_notification_events_notification_request_hash"}
