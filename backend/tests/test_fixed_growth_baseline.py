from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.database import engine, session_scope
from app.db_models import (
    NotificationRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from app.fixed_growth_baseline import (
    FixedGrowthBaselineError,
    ensure_fixed_growth_assignments,
)
from app.services import GrowthService
from app.store import DatabaseStore


NOW = datetime(2026, 7, 23, 4, 0, tzinfo=timezone.utc)


def _teacher(session, teacher_id: str) -> None:
    session.add(
        TeacherRecord(
            teacher_id=teacher_id,
            camp_enrollment_id=f"CAMP-{teacher_id}",
            name=f"Teacher {teacher_id}",
            country="PH",
            timezone="Asia/Manila",
            camp_day=1,
            graduation_state="IN_PROGRESS",
            total_score=0,
            graduation_threshold=100,
            data_mode="REAL",
            source_batch_id=None,
            source_snapshot_label="API_DAILY_TEST",
            payload={"teacher_id": teacher_id, "data_mode": "REAL"},
            created_at=NOW,
            updated_at=NOW,
        )
    )


def test_new_teacher_baseline_is_complete_idempotent_and_has_no_notification() -> None:
    teacher_id = "FIXED-BASELINE-NEW"
    with session_scope(engine) as session:
        _teacher(session, teacher_id)
        first = ensure_fixed_growth_assignments(
            session,
            [teacher_id],
            occurred_at=NOW,
        )
        session.flush()
        second = ensure_fixed_growth_assignments(
            session,
            [teacher_id],
            occurred_at=NOW,
        )

        assignments = session.scalars(
            select(TaskAssignmentRecord)
            .where(TaskAssignmentRecord.teacher_id == teacher_id)
            .order_by(TaskAssignmentRecord.task_code)
        ).all()
        assert first.created_assignment_count == 10
        assert first.existing_assignment_count == 0
        assert second.created_assignment_count == 0
        assert second.existing_assignment_count == 10
        assert [item.task_code for item in assignments] == [
            f"G{number:02d}" for number in range(1, 11)
        ]
        assert {item.status for item in assignments} == {"ASSIGNED"}
        assert {item.task_kind for item in assignments} == {"FIXED_GROWTH"}
        assert {item.creator_system for item in assignments} == {"TRIGGER_CENTER"}
        assert {item.source_mode for item in assignments} == {"REAL"}
        assert {
            item.evidence_snapshot["trigger_code"] for item in assignments
        } == {"NEW_TEACHER_CREATED"}
        assert session.scalar(
            select(func.count()).select_from(NotificationRecord)
        ) == 0


def test_incomplete_published_catalog_rolls_back_baseline_creation() -> None:
    teacher_id = "FIXED-BASELINE-CATALOG-ERROR"
    with session_scope(engine) as session:
        _teacher(session, teacher_id)
        template = session.get(TaskTemplateRecord, "G10:v1")
        assert template is not None
        template.status = "RETIRED"
        with pytest.raises(
            FixedGrowthBaselineError,
            match="FIXED_GROWTH_CATALOG_MUST_CONTAIN_EXACTLY_PUBLISHED_G01_G10",
        ):
            ensure_fixed_growth_assignments(
                session,
                [teacher_id],
                occurred_at=NOW,
            )
        assert session.scalar(
            select(func.count())
            .select_from(TaskAssignmentRecord)
            .where(TaskAssignmentRecord.teacher_id == teacher_id)
        ) == 0


def test_teacher_detail_reads_the_shared_fixed_task_baseline() -> None:
    teacher_id = "FIXED-BASELINE-DETAIL"
    with session_scope(engine) as session:
        _teacher(session, teacher_id)
        ensure_fixed_growth_assignments(
            session,
            [teacher_id],
            occurred_at=NOW,
        )

    detail = GrowthService(DatabaseStore(engine)).teacher_detail(teacher_id)

    assert len(detail["task_assignments"]) == 10
    assert [item["task_code"] for item in detail["task_assignments"]] == [
        f"G{number:02d}" for number in range(1, 11)
    ]
    assert {item["status"] for item in detail["task_assignments"]} == {
        "ASSIGNED"
    }
    assert {item["creator_system"] for item in detail["task_assignments"]} == {
        "TRIGGER_CENTER"
    }
