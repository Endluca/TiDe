from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.database import engine, session_scope
from app.db_models import (
    ComplaintCategoryRuleRecord,
    ComplaintRuleImportRecord,
    LessonSourceWideRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from app.store import DatabaseStore
from app.task_catalog import MANDATORY_TASK_CODES


NOW = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)
TASK_CODES = MANDATORY_TASK_CODES


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
) -> None:
    lesson_id = f"LESSON-{teacher_id}-{sequence}"
    with session_scope(engine) as session:
        source_sha256 = hashlib.sha256(teacher_id.encode()).hexdigest()
        if complaint_l3 is not None and complaint_level_rank is not None:
            if session.get(ComplaintRuleImportRecord, source_sha256) is None:
                session.add(
                    ComplaintRuleImportRecord(
                        source_sha256=source_sha256,
                        source_filename="complaint-rules.xlsx",
                        raw_rows=[
                            {
                                "source_row_number": sequence + 1,
                                "一级分类": complaint_l1,
                                "二级分类": complaint_l2,
                                "三级分类": complaint_l3,
                                "P级": f"P{complaint_level_rank}",
                                "Course Title in the Learning Hub": None,
                                "link": None,
                            }
                        ],
                        imported_at=NOW,
                    )
                )
            session.add(
                ComplaintCategoryRuleRecord(
                    rule_id=f"RULE-{teacher_id}-{sequence}",
                    source_sha256=source_sha256,
                    source_row_number=sequence + 1,
                    category_l1=complaint_l1,
                    category_l2=complaint_l2,
                    category_l3=complaint_l3,
                    category_l3_normalized=complaint_l3,
                    source_level=f"L{complaint_level_rank}",
                    severity_rank=complaint_level_rank,
                    default_route="DIRECT",
                    created_at=NOW,
                )
            )
        session.add(
            LessonSourceWideRecord(
                course_id=lesson_id,
                teacher_id=teacher_id,
                lesson_status="COMPLETED",
                complaint_category_l1=complaint_l1,
                complaint_category_l2=complaint_l2,
                complaint_category_l3=complaint_l3,
            )
        )


def test_mandatory_growth_projection_awards_completed_pinned_templates_immediately() -> None:
    partial_teacher = "PROJECTION-PARTIAL"
    complete_teacher = "PROJECTION-COMPLETE"
    _add_teacher(partial_teacher)
    _add_teacher(complete_teacher)
    _add_fixed_assignments(
        partial_teacher,
        codes=("G01", "G08"),
        completed={"G01", "G08"},
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
            "score": 8.0,
        "source_mode": "TASK_BASELINE_INCOMPLETE",
        "score_rule_version": "shared-fixed-growth.current-status.v1",
        "assignment_count": 2,
        "completed_count": 2,
        "expected_count": 9,
    }

    complete = values[complete_teacher]["NEW_TEACHER_TASK"]
    assert complete == {
        "score": 30.0,
        "source_mode": "SYSTEM_TASK_STATUS",
        "score_rule_version": "shared-fixed-growth.current-status.v1",
        "assignment_count": 9,
        "completed_count": 9,
        "expected_count": 9,
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
        "expected_count": 9,
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
    assert task_score["expected_count"] == 9


def test_l0_complaints_are_aggregated_from_current_lesson_source_and_rules() -> None:
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

    complaint = DatabaseStore(engine).score_account_values({teacher_id})[
        teacher_id
    ]["L0_COMPLAINT"]

    assert complaint == {
        "count": 2,
        "source_mode": "DERIVED_REAL",
        "source_field": (
            "lesson_source_wide.投诉三级分类+"
            "complaint_category_rules.source_level"
        ),
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
