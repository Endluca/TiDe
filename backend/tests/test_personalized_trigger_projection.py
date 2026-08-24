from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timezone

from sqlalchemy import select

from app import lesson_ingestion
from app import personalized_trigger_projection as projection
from app.database import engine, session_scope
from app.db_models import TaskAssignmentRecord, TeacherRecord


def test_legacy_importer_uses_the_canonical_trigger_projection() -> None:
    assert lesson_ingestion.TRIGGER_RULE_VERSION == projection.TRIGGER_RULE_VERSION
    assert lesson_ingestion._LessonRow is projection.LessonTriggerRow
    assert lesson_ingestion._feedback_labels is projection.feedback_labels
    assert lesson_ingestion._template_map is projection.published_template_map
    assert lesson_ingestion._build_output_specs is projection.build_output_specs
    assert lesson_ingestion._materialize_outputs is projection.materialize_outputs


def _negative_lesson(
    *,
    row_number: int,
    lesson_id: str,
    label: str,
) -> projection.LessonTriggerRow:
    return projection.LessonTriggerRow(
        row_number=row_number,
        raw_payload={"课程id": lesson_id},
        source_region="ovs",
        lesson_id=lesson_id,
        teacher_id="TEACHER-ENVIRONMENT",
        student_id=f"STUDENT-{row_number}",
        local_date=date(2026, 8, 11),
        local_time=time(10, row_number),
        local_start_at=datetime(2026, 8, 11, 10, row_number),
        lifecycle_status="end",
        is_peak=False,
        is_late=False,
        is_early=False,
        negative_score=1.0,
        has_negative_tag=True,
        feedback_detail=label,
        negative_tags=(label,),
        absence_reason_detail=None,
        complaint_l1=None,
        complaint_l2=None,
        complaint_l3=None,
        is_blocked=False,
        is_favorited=False,
        has_positive_tag=False,
        is_rebooked=False,
        is_camera_off=False,
        is_cpu_usage_high=False,
        is_network_delay_high=False,
    )


def test_repeated_environment_labels_emit_a_stable_teacher_execution_variant() -> None:
    lessons: list[projection.LessonTriggerRow] = []
    row_number = 1
    for label in [
        "灯光过暗/亮",
        "环境乱/灯光差",
        "缺少互动",
    ]:
        first = _negative_lesson(
            row_number=row_number,
            lesson_id=f"LESSON-{row_number}",
            label=label,
        )
        lessons.extend(
            [
                first,
                replace(
                    first,
                    row_number=row_number + 1,
                    lesson_id=f"LESSON-{row_number + 1}",
                    student_id=f"STUDENT-{row_number + 1}",
                    local_time=time(10, row_number + 1),
                    local_start_at=datetime(2026, 8, 11, 10, row_number + 1),
                ),
            ]
        )
        row_number += 2

    specs, _, _, _ = projection.build_output_specs(
        lessons,
        complaint_rules={},
        complaint_rule_ids={},
    )
    negative_specs = {
        spec.evidence["negative_feedback_label"]: spec
        for spec in specs
        if spec.task_code == "P-FB-NEGATIVE"
    }

    for label in projection.TEACHING_ENVIRONMENT_PHOTO_LABELS:
        assert negative_specs[label].evidence["teacher_execution_variant"] == (
            projection.TEACHING_ENVIRONMENT_PHOTO_VARIANT
        )
    assert "teacher_execution_variant" not in negative_specs["缺少互动"].evidence


def test_repeated_negative_labels_do_not_bind_mutable_pre_end_teacher() -> None:
    first = replace(
        _negative_lesson(row_number=1, lesson_id="LESSON-ON-1", label="缺少互动"),
        lifecycle_status="on",
    )
    second = replace(
        first,
        row_number=2,
        lesson_id="LESSON-ON-2",
        student_id="STUDENT-2",
    )

    specs, _, pending_count, _ = projection.build_output_specs(
        [first, second],
        complaint_rules={},
        complaint_rule_ids={},
    )

    assert not any(spec.task_code == "P-FB-NEGATIVE" for spec in specs)
    assert pending_count == 0


def test_materialized_variant_is_frozen_from_the_trusted_rule_not_context() -> None:
    lessons = [
        _negative_lesson(
            row_number=row_number,
            lesson_id=f"LESSON-{row_number}",
            label=label,
        )
        for label, row_numbers in (("灯光过暗/亮", (1, 2)), ("缺少互动", (3, 4)))
        for row_number in row_numbers
    ]
    specs, _, _, _ = projection.build_output_specs(
        lessons,
        complaint_rules={},
        complaint_rule_ids={},
    )
    materialized_at = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)

    with session_scope(engine) as session:
        teacher = TeacherRecord(
            teacher_id="TEACHER-ENVIRONMENT",
            camp_enrollment_id="CAMP-ENVIRONMENT",
            name="Environment Teacher",
            country="CN",
            timezone="Asia/Shanghai",
            camp_day=1,
            graduation_state="IN_CAMP",
            gold_qualified=False,
            total_score=0,
            graduation_threshold=30,
            data_mode="REAL",
            source_snapshot_label=None,
            payload={},
            created_at=materialized_at,
            updated_at=materialized_at,
        )
        session.add(teacher)
        session.flush()
        templates = projection.published_template_map(session)

        projection.materialize_outputs(
            session,
            specs=specs,
            templates=templates,
            teachers={teacher.teacher_id: teacher},
            materialized_at=materialized_at,
            evidence_context={"teacher_execution_variant": "UNTRUSTED_OVERRIDE"},
        )
        session.flush()

        assignments = session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.task_code == "P-FB-NEGATIVE"
            )
        ).all()
        by_label = {
            assignment.evidence_snapshot["signal_samples"][0]["evidence"][
                "negative_feedback_label"
            ]: assignment
            for assignment in assignments
        }
        assert by_label["灯光过暗/亮"].evidence_snapshot[
            "teacher_execution_variant"
        ] == projection.TEACHING_ENVIRONMENT_PHOTO_VARIANT
        assert (
            "teacher_execution_variant"
            not in by_label["缺少互动"].evidence_snapshot
        )
