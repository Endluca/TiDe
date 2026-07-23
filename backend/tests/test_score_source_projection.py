from __future__ import annotations

from datetime import datetime, timezone

from app.database import engine, session_scope
from app.db_models import (
    LessonFactRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from app.store import DatabaseStore


NOW = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)
TASK_CODES = tuple(f"G{number:02d}" for number in range(1, 11))


def _add_teacher(teacher_id: str) -> None:
    with session_scope(engine) as session:
        session.add(
            TeacherRecord(
                teacher_id=teacher_id,
                camp_enrollment_id=f"CAMP-{teacher_id}",
                name=f"Teacher {teacher_id}",
                country="PH",
                timezone="Asia/Manila",
                camp_day=10,
                graduation_state="IN_PROGRESS",
                total_score=0,
                graduation_threshold=100,
                data_mode="REAL",
                source_batch_id=None,
                source_snapshot_label="TEST",
                payload={"teacher_id": teacher_id, "data_mode": "REAL"},
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _add_fixed_assignments(
    teacher_id: str,
    *,
    codes: tuple[str, ...],
    completed: set[str],
) -> None:
    with session_scope(engine) as session:
        for code in codes:
            template = session.get(TaskTemplateRecord, f"{code}:v1")
            assert template is not None
            is_completed = code in completed
            session.add(
                TaskAssignmentRecord(
                    assignment_id=f"ASSIGN-{teacher_id}-{code}",
                    teacher_id=teacher_id,
                    task_code=code,
                    template_version_id=template.row_id,
                    task_kind="FIXED_GROWTH",
                    creator_system="TRIGGER_CENTER",
                    status="COMPLETED" if is_completed else "ASSIGNED",
                    priority=str(template.payload["priority"]),
                    why=str(template.payload["why_template"]),
                    display_title=None,
                    evidence_snapshot={},
                    due_at=None,
                    timezone_used=None,
                    timezone_source=None,
                    timezone_verified_at=None,
                    status_reason_code=None,
                    source_mode="REAL",
                    dedupe_key=f"fixed:{teacher_id}:{code}",
                    created_by="TRIGGER_CENTER_TEST",
                    updated_by="TRIGGER_CENTER_TEST",
                    row_version=2 if is_completed else 1,
                    assigned_at=NOW,
                    status_changed_at=NOW,
                    completed_at=NOW if is_completed else None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


def _add_lesson(
    teacher_id: str,
    sequence: int,
    *,
    complaint_l1: str | None = None,
    complaint_l2: str | None = None,
    complaint_l3: str | None = None,
    complaint_level_rank: int | None = None,
    data_mode: str = "REAL",
) -> None:
    lesson_id = f"LESSON-{teacher_id}-{sequence}"
    with session_scope(engine) as session:
        session.add(
            LessonFactRecord(
                lesson_id=lesson_id,
                source_appoint_id=lesson_id,
                camp_enrollment_id=f"CAMP-{teacher_id}",
                teacher_id=teacher_id,
                lesson_lifecycle_status="COMPLETED",
                complaint_category_l1=complaint_l1,
                complaint_category_l2=complaint_l2,
                complaint_category_l3=complaint_l3,
                complaint_level_rank=complaint_level_rank,
                valid_for_scoring=False,
                evidence_status="OBSERVED_REAL_SOURCE",
                data_mode=data_mode,
                payload={},
                created_at=NOW,
                updated_at=NOW,
            )
        )


def test_mandatory_growth_projection_awards_completed_pinned_templates_immediately() -> None:
    partial_teacher = "PROJECTION-PARTIAL"
    complete_teacher = "PROJECTION-COMPLETE"
    _add_teacher(partial_teacher)
    _add_teacher(complete_teacher)
    _add_fixed_assignments(
        partial_teacher,
        codes=("G01", "G09"),
        completed={"G01", "G09"},
    )
    _add_fixed_assignments(
        complete_teacher,
        codes=TASK_CODES,
        completed=set(TASK_CODES),
    )

    values = DatabaseStore(engine).score_account_values(
        {partial_teacher, complete_teacher}
    )

    partial = values[partial_teacher]["NEW_TEACHER_TASK"]
    assert partial == {
        "score": 14.0,
        "source_mode": "TASK_BASELINE_INCOMPLETE",
        "score_rule_version": "shared-fixed-growth.current-status.v1",
        "assignment_count": 2,
        "completed_count": 2,
        "expected_count": 10,
    }

    complete = values[complete_teacher]["NEW_TEACHER_TASK"]
    assert complete == {
        "score": 30.0,
        "source_mode": "SYSTEM_TASK_STATUS",
        "score_rule_version": "shared-fixed-growth.current-status.v1",
        "assignment_count": 10,
        "completed_count": 10,
        "expected_count": 10,
    }


def test_missing_fixed_baseline_is_internal_initialization_anomaly() -> None:
    teacher_id = "PROJECTION-NO-FIXED-BASELINE"
    _add_teacher(teacher_id)

    task_score = DatabaseStore(engine).score_account_values({teacher_id})[
        teacher_id
    ]["NEW_TEACHER_TASK"]

    assert task_score == {
        "score": 0,
        "source_mode": "TASK_BASELINE_INCOMPLETE",
        "score_rule_version": "shared-fixed-growth.current-status.v1",
        "assignment_count": 0,
        "completed_count": 0,
        "expected_count": 10,
    }


def test_invalid_pinned_task_template_fails_closed_instead_of_using_snapshot_score() -> None:
    teacher_id = "PROJECTION-INVALID-TEMPLATE"
    _add_teacher(teacher_id)
    with session_scope(engine) as session:
        template = session.get(TaskTemplateRecord, "G01:v1")
        assert template is not None
        template.payload = {
            **template.payload,
            "score_value": None,
        }
    _add_fixed_assignments(
        teacher_id,
        codes=("G01",),
        completed={"G01"},
    )

    task_score = DatabaseStore(engine).score_account_values({teacher_id})[
        teacher_id
    ]["NEW_TEACHER_TASK"]

    assert task_score["score"] == 0
    assert task_score["source_mode"] == "TASK_STATUS_INVALID"
    assert task_score["assignment_count"] == 0
    assert task_score["completed_count"] == 0
    assert task_score["expected_count"] == 10


def test_l0_complaints_are_aggregated_from_real_lesson_level_ranks() -> None:
    teacher_id = "PROJECTION-L0"
    _add_teacher(teacher_id)
    _add_lesson(
        teacher_id,
        1,
        complaint_l3="L0 category A",
        complaint_level_rank=0,
    )
    _add_lesson(
        teacher_id,
        2,
        complaint_l3="L0 category B",
        complaint_level_rank=0,
    )
    _add_lesson(
        teacher_id,
        3,
        complaint_l3="L1 category",
        complaint_level_rank=1,
    )
    _add_lesson(teacher_id, 4)
    # Synthetic rows cannot establish or change a real qualification gate.
    _add_lesson(
        teacher_id,
        5,
        complaint_l3="Mock L0 category",
        complaint_level_rank=0,
        data_mode="MOCK",
    )

    complaint = DatabaseStore(engine).score_account_values({teacher_id})[
        teacher_id
    ]["L0_COMPLAINT"]

    assert complaint == {
        "count": 2,
        "source_mode": "DERIVED_REAL",
        "source_field": "lesson_facts.complaint_level_rank",
        "lesson_count": 4,
        "unmapped_complaint_count": 0,
    }


def test_unmapped_complaint_fails_closed_even_when_level_three_is_missing() -> None:
    teacher_id = "PROJECTION-UNMAPPED-COMPLAINT"
    _add_teacher(teacher_id)
    _add_lesson(
        teacher_id,
        1,
        complaint_l1="Teacher issue",
        complaint_l2="Attendance issue",
        complaint_l3=None,
        complaint_level_rank=None,
    )

    complaint = DatabaseStore(engine).score_account_values({teacher_id})[
        teacher_id
    ]["L0_COMPLAINT"]

    assert complaint["count"] == 0
    assert complaint["source_mode"] == "COMPLAINT_LEVEL_MAPPING_INCOMPLETE"
    assert complaint["lesson_count"] == 1
    assert complaint["unmapped_complaint_count"] == 1
