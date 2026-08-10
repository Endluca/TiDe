from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect

from app.database import engine, session_scope
from app.db_models import (
    ComplaintCategoryRuleRecord,
    ComplaintRuleImportRecord,
    LessonSourceWideRecord,
    NotificationRecord,
    PersonalizedTriggerMatchRecord,
    TaskAssignmentRecord,
)
from app.source_contracts import LESSON_SOURCE_FIELDS


def test_lesson_source_wide_is_exactly_the_23_field_contract() -> None:
    columns = tuple(
        column.name for column in inspect(LessonSourceWideRecord).columns
    )

    assert columns == LESSON_SOURCE_FIELDS
    assert len(columns) == 23
    assert "学员id" in columns
    assert "student_id_hash" not in columns
    assert "source_batch_id" not in columns


def test_complaint_rule_import_keeps_lossless_source_rows_in_one_table() -> None:
    now = datetime.now(timezone.utc)
    source_sha256 = "a" * 64
    imported = ComplaintRuleImportRecord(
        source_sha256=source_sha256,
        source_filename="complaint-rules.xlsx",
        raw_rows=[
            {
                "source_row_number": 3,
                "一级分类": "关于老师",
                "二级分类": "教学技巧问题",
                "三级分类": "无纠错",
                "P级": "P4",
                "Course Title in the Learning Hub": "Correcting Learners",
                "link": None,
            }
        ],
        imported_at=now,
    )
    complaint_rule = ComplaintCategoryRuleRecord(
        rule_id="COMPLAINT-RULE-TEST-1",
        source_sha256=source_sha256,
        source_row_number=3,
        category_l1="关于老师",
        category_l2="教学技巧问题",
        category_l3="无纠错",
        category_l3_normalized="无纠错",
        source_level="P4",
        severity_rank=4,
        default_route="TEACHER_TASK",
        created_at=now,
    )

    with session_scope(engine) as session:
        session.add_all([imported, complaint_rule])

    with session_scope(engine) as session:
        stored = session.get(ComplaintRuleImportRecord, source_sha256)
        assert stored is not None
        assert stored.raw_rows == [
            {
                "source_row_number": 3,
                "一级分类": "关于老师",
                "二级分类": "教学技巧问题",
                "三级分类": "无纠错",
                "P级": "P4",
                "Course Title in the Learning Hub": "Correcting Learners",
                "link": None,
            }
        ]


def test_task_output_and_source_only_notification_columns_exist() -> None:
    assignment_columns = set(inspect(TaskAssignmentRecord).columns.keys())
    assert {"display_title", "evidence_snapshot"}.issubset(assignment_columns)

    notification_columns = inspect(NotificationRecord).columns
    assert notification_columns.task_id.nullable is True
    assert "source_ref" in notification_columns

    match_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in PersonalizedTriggerMatchRecord.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "PENDING_DATA" in match_constraints["ck_personalized_trigger_match_status"]
    assert "PENDING_DATA" in match_constraints["ck_personalized_trigger_match_output_type"]


def test_rev14_repairs_teacher_role_and_freezes_assignment_evidence() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260722_14_lesson_trigger_evidence.py"
    ).read_text(encoding="utf-8")

    assert "20260722_13_single_tasks" in migration
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE public.task_assignments FROM tit_teacher_crud"
        in migration
    )
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE public.task_templates FROM tit_teacher_crud"
        in migration
    )
    assert "'priority', 'UPDATE'" in migration
    assert "CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write()" in migration
    assert "to_jsonb(NEW) -> 'display_title'" in migration
    assert "to_jsonb(NEW) -> 'evidence_snapshot'" in migration

    update_grant = migration.split("EXECUTE 'GRANT UPDATE (", 1)[1].split(
        ") ON TABLE public.task_assignments", 1
    )[0]
    assert "priority" not in update_grant
    assert {
        "status",
        "status_reason_code",
        "status_changed_at",
        "completed_at",
        "updated_by",
    } == {column.strip() for column in update_grant.split(",")}
