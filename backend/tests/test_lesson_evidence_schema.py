from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, select

from app.database import engine, session_scope
from app.db_models import (
    ComplaintCategoryRuleRecord,
    DataImportBatchRecord,
    LessonFactRecord,
    NotificationRecord,
    PersonalizedTriggerMatchRecord,
    SourceRecord,
    TaskAssignmentRecord,
)


def test_lesson_projection_covers_the_24_source_fields_without_plain_student_id() -> None:
    columns = set(inspect(LessonFactRecord).columns.keys())

    assert {
        "lesson_id",
        "lesson_local_date",
        "lesson_local_time",
        "teacher_id",
        "student_id_hash",
        "lesson_lifecycle_status",
        "is_peak",
        "is_late",
        "is_early",
        "negative_score",
        "has_negative_feedback_tag",
        "feedback_detail",
        "negative_tag_values",
        "absence_reason_detail",
        "complaint_category_l1",
        "complaint_category_l2",
        "complaint_category_l3",
        "is_blocked",
        "is_favorited",
        "has_positive_feedback_tag",
        "positive_tag_value",
        "is_rebooked",
        "is_camera_off",
        "is_cpu_usage_high",
        "is_network_delay_high",
        "is_false_early_leave",
    }.issubset(columns)
    assert "student_id" not in columns
    assert {
        "source_batch_id",
        "source_record_id",
        "complaint_source_level",
        "complaint_level_rank",
        "complaint_route",
    }.issubset(columns)


def test_real_api_batch_and_lossless_source_row_are_portable_to_sqlite() -> None:
    now = datetime.now(timezone.utc)
    batch = DataImportBatchRecord(
        batch_id="TEST-LESSON-API-BATCH",
        source_kind="LESSON_FACT",
        sync_mode="API_DAILY",
        source_system="test-source",
        source_filename="api-2026-07-22.json",
        source_uri="test://lesson-source",
        source_sha256="a" * 64,
        source_sheet="api",
        snapshot_label="2026-07-22",
        data_mode="REAL",
        column_count=24,
        row_count=1,
        header=["课程id", "老师id", "学员id"],
        status="COMPLETED",
        imported_at=now,
        payload={},
    )
    source_record = SourceRecord(
        source_record_id="SRC-TEST-LESSON-1",
        batch_id=batch.batch_id,
        source_sheet="api",
        source_row_number=1,
        business_key="lesson:1",
        teacher_id="teacher-1",
        lesson_id="1",
        occurred_at=now,
        row_sha256="b" * 64,
        raw_payload={"课程id": 1, "学员id": 99},
        created_at=now,
    )
    complaint_rule = ComplaintCategoryRuleRecord(
        rule_id="COMPLAINT-RULE-TEST-1",
        batch_id=batch.batch_id,
        source_sheet="api",
        source_row_number=1,
        category_l1="关于老师",
        category_l2="教学技巧问题",
        category_l3="无纠错",
        category_l3_normalized="无纠错",
        source_level="P4",
        normalized_level="L4",
        severity_rank=4,
        default_route="TEACHER_TASK",
        learning_title="Correcting Learners",
        learning_url=None,
        raw_payload={"P级": "P4"},
        created_at=now,
    )

    with session_scope(engine) as session:
        session.add_all([batch, source_record, complaint_rule])

    with session_scope(engine) as session:
        stored = session.scalar(
            select(SourceRecord).where(
                SourceRecord.source_record_id == source_record.source_record_id
            )
        )
        assert stored is not None
        assert stored.raw_payload == {"课程id": 1, "学员id": 99}
        assert stored.batch_id == batch.batch_id


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
