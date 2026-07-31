from __future__ import annotations

from copy import deepcopy
from datetime import date, time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import engine, session_scope
from app.db_models import (
    LessonDimensionScoreRecord,
    LessonFactRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TeacherRecord,
)
from app.score_read_model import (
    _lesson_component_payloads,
    refresh_persisted_score_read_models,
)
from app.score_read_service import ScoreReadService
from app.main import app


client = TestClient(app)


def _favorite_test_lesson(
    lesson_id: str,
    *,
    teacher_id: str = "T-1",
    student_id_hash: str | None = "S-1",
    lesson_date: date | None = date(2026, 7, 1),
    lesson_time: time | None = time(10, 0),
    is_favorited: bool | None = True,
    valid_for_scoring: bool = True,
    lifecycle_status: str = "end",
) -> SimpleNamespace:
    return SimpleNamespace(
        lesson_id=lesson_id,
        teacher_id=teacher_id,
        student_id_hash=student_id_hash,
        lesson_local_date=lesson_date,
        lesson_local_time=lesson_time,
        lesson_lifecycle_status=lifecycle_status,
        valid_for_scoring=valid_for_scoring,
        is_late=False,
        is_early=False,
        is_peak=False,
        is_favorited=is_favorited,
        has_positive_feedback_tag=False,
        is_rebooked=False,
        is_camera_off=False,
        is_cpu_usage_high=False,
        is_network_delay_high=False,
    )


def _favorite_components(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if row["dimension"] != "USER_FEEDBACK":
            continue
        component = next(
            item
            for item in row["components"]  # type: ignore[union-attr]
            if item["code"] == "FEEDBACK_FAVORITE"
        )
        lesson = row["lesson"]
        result[lesson.lesson_id] = component  # type: ignore[union-attr]
    return result


def test_favorite_score_is_attributed_only_to_first_lesson_per_pair() -> None:
    lessons = [
        _favorite_test_lesson(
            "A-LATER",
            student_id_hash="A",
            lesson_date=date(2026, 7, 2),
        ),
        _favorite_test_lesson(
            "A-FIRST",
            student_id_hash="A",
            lesson_date=date(2026, 7, 1),
        ),
        _favorite_test_lesson(
            "B-NOT-FAVORITED",
            student_id_hash="B",
            lesson_date=date(2026, 7, 1),
            is_favorited=False,
        ),
        _favorite_test_lesson(
            "B-FIRST-FAVORITE",
            student_id_hash="B",
            lesson_date=date(2026, 7, 2),
        ),
        _favorite_test_lesson(
            "C-INVALID",
            student_id_hash="C",
            lesson_date=date(2026, 7, 1),
            valid_for_scoring=False,
        ),
        _favorite_test_lesson(
            "C-FIRST-VALID",
            student_id_hash="C",
            lesson_date=date(2026, 7, 2),
        ),
        _favorite_test_lesson(
            "D-NO-STUDENT",
            student_id_hash=None,
        ),
        _favorite_test_lesson(
            "E-TIE-2",
            student_id_hash="E",
        ),
        _favorite_test_lesson(
            "E-TIE-1",
            student_id_hash="E",
        ),
        _favorite_test_lesson(
            "F-NO-TIME",
            student_id_hash="F",
            lesson_time=None,
        ),
        _favorite_test_lesson(
            "F-KNOWN-TIME",
            student_id_hash="F",
            lesson_date=date(2026, 7, 2),
        ),
        _favorite_test_lesson(
            "OTHER-TEACHER-A",
            teacher_id="T-2",
            student_id_hash="A",
        ),
    ]

    rows, attributed = _lesson_component_payloads(
        lessons,
        points={
            "FEEDBACK_PRAISE": 5,
            "FEEDBACK_FAVORITE": 5,
            "PERFECT_COMPLETED": 4,
            "PEAK_COMPLETED": 2,
            "CLASS_QUALITY_HARDWARE": 2,
        },
    )
    favorites = _favorite_components(rows)

    assert favorites["A-FIRST"]["awarded"] is True
    assert favorites["A-FIRST"]["score"] == 5
    assert favorites["A-LATER"]["fact_value"] is True
    assert favorites["A-LATER"]["awarded"] is False
    assert favorites["A-LATER"]["score"] == 0
    assert favorites["B-NOT-FAVORITED"]["awarded"] is False
    assert favorites["B-FIRST-FAVORITE"]["awarded"] is True
    assert favorites["C-INVALID"]["awarded"] is False
    assert favorites["C-FIRST-VALID"]["awarded"] is True
    assert favorites["D-NO-STUDENT"]["awarded"] is False
    assert favorites["D-NO-STUDENT"]["evidence_status"] == "SOURCE_MISSING"
    assert favorites["E-TIE-1"]["awarded"] is True
    assert favorites["E-TIE-2"]["awarded"] is False
    assert favorites["F-NO-TIME"]["awarded"] is False
    assert favorites["F-KNOWN-TIME"]["awarded"] is False
    assert favorites["F-KNOWN-TIME"]["evidence_status"] == "SOURCE_MISSING"
    assert favorites["OTHER-TEACHER-A"]["awarded"] is True
    assert attributed["FEEDBACK_FAVORITE"] == {"count": 5.0, "score": 25.0}


def test_favorite_teacher_total_stays_aggregate_while_later_lesson_scores_zero() -> None:
    first_lesson_id = ""
    total_score_before = 0.0
    user_feedback_score_before = 0.0
    raw_total_before = 0.0
    public_total_before = 0.0
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
        first = session.scalar(
            select(LessonFactRecord).where(
                LessonFactRecord.teacher_id == teacher.teacher_id
            )
        )
        assert first is not None
        first_lesson_id = first.lesson_id
        first.lesson_local_date = date(2026, 7, 1)
        first.lesson_local_time = time(10, 0)
        first.student_id_hash = "STUDENT-A"
        first.lesson_lifecycle_status = "end"
        first.valid_for_scoring = True
        first.is_favorited = True
        first.has_positive_feedback_tag = False
        first.is_late = False
        first.is_early = False
        first.is_peak = False
        first.is_camera_off = False
        first.is_cpu_usage_high = False
        first.is_network_delay_high = False
        payload = deepcopy(teacher.payload)
        payload["metric_inputs"] = {
            **payload.get("metric_inputs", {}),
            "feedback_favorite_cnt": 3,
        }
        payload["metric_provenance"] = {
            **payload.get("metric_provenance", {}),
            "feedback_favorite_cnt": {"source_mode": "REAL"},
        }
        teacher.payload = payload
        refresh_persisted_score_read_models(
            session,
            trigger_type="MANUAL_BACKFILL",
            teacher_ids=[teacher.teacher_id],
        )

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
        user_feedback = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "T-1001",
                ScoreAccountRecord.dimension == "USER_FEEDBACK",
            )
        )
        first = session.get(LessonFactRecord, first_lesson_id)
        assert user_feedback is not None
        assert first is not None
        total_score_before = teacher.total_score
        user_feedback_score_before = user_feedback.current_score
        raw_total_before = float(teacher.payload["raw_total_score"])
        public_total_before = float(teacher.payload["external_display_score"])
        session.add(
            LessonFactRecord(
                lesson_id="FAVORITE-LATER",
                source_appoint_id="APPOINT-FAVORITE-LATER",
                camp_enrollment_id=first.camp_enrollment_id,
                teacher_id=teacher.teacher_id,
                lesson_lifecycle_status="end",
                lesson_local_date=date(2026, 7, 2),
                lesson_local_time=time(10, 0),
                student_id_hash="STUDENT-A",
                is_late=True,
                is_early=False,
                is_peak=False,
                is_favorited=True,
                has_positive_feedback_tag=False,
                is_rebooked=False,
                is_camera_off=True,
                is_cpu_usage_high=False,
                is_network_delay_high=False,
                valid_for_scoring=True,
                evidence_status="CONFIRMED",
                data_mode=first.data_mode,
                payload=deepcopy(first.payload),
            )
        )
        refresh_persisted_score_read_models(
            session,
            trigger_type="LESSON_SOURCE_UPDATED",
            trigger_ref="FAVORITE-LATER",
            teacher_ids=[teacher.teacher_id],
        )

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        user_feedback = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "T-1001",
                ScoreAccountRecord.dimension == "USER_FEEDBACK",
            )
        )
        favorite_account = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == "T-1001",
                ScoreComponentAccountRecord.component_code
                == "FEEDBACK_FAVORITE",
            )
        )
        assert teacher is not None
        assert user_feedback is not None
        assert favorite_account is not None
        assert teacher.total_score == total_score_before
        assert float(teacher.payload["raw_total_score"]) == raw_total_before
        assert (
            float(teacher.payload["external_display_score"])
            == public_total_before
        )
        assert user_feedback.current_score == user_feedback_score_before
        assert favorite_account.unit_count == 3
        assert favorite_account.current_score == 15
        assert favorite_account.lesson_attributed_count == 1
        assert favorite_account.lesson_attributed_score == 5
        assert favorite_account.unattributed_score == 10
        assert favorite_account.reconciliation_status == "PARTIAL"

        lesson_scores = list(
            session.scalars(
                select(LessonDimensionScoreRecord).where(
                    LessonDimensionScoreRecord.teacher_id == "T-1001",
                    LessonDimensionScoreRecord.dimension == "USER_FEEDBACK",
                )
            ).all()
        )
        favorites = {
            row.lesson_id: next(
                component
                for component in row.payload["business_facts"]
                if component["code"] == "FEEDBACK_FAVORITE"
            )
            for row in lesson_scores
        }
        assert favorites[first_lesson_id]["fact_value"] is True
        assert favorites[first_lesson_id]["awarded"] is True
        assert favorites[first_lesson_id]["score"] == 5
        assert favorites["FAVORITE-LATER"]["fact_value"] is True
        assert favorites["FAVORITE-LATER"]["awarded"] is False
        assert favorites["FAVORITE-LATER"]["score"] == 0


def test_score_read_model_is_idempotent_and_queryable() -> None:
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
        lesson = session.scalar(
            select(LessonFactRecord).where(
                LessonFactRecord.teacher_id == "T-1001"
            )
        )
        assert lesson is not None
        lesson.lesson_lifecycle_status = "end"
        lesson.is_late = False
        lesson.is_early = False
        lesson.is_camera_off = False
        lesson.is_cpu_usage_high = False
        lesson.is_network_delay_high = False
        payload = deepcopy(teacher.payload)
        payload["metric_inputs"] = {
            "total_completed_cnt": 10,
            "on_time_completed_cnt": 8,
            "peak_completed_cnt": 4,
            "peak_slot_cnt": 40,
            "capacity_milestone_achieved": True,
            "perfect_cnt": 6,
            "feedback_praise_cnt": 2,
            "feedback_favorite_cnt": 1,
            "completed_again_student_15d_cnt": 1,
            "new_teacher_task_score": 0,
        }
        payload["metric_provenance"] = {
            key: {"source_mode": "REAL"}
            for key in payload["metric_inputs"]
        }
        teacher.payload = payload
        task_account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "T-1001",
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        assert task_account is not None
        # A stale imported/account value must never override shared task status.
        task_account.current_score = 30
        first = refresh_persisted_score_read_models(
            session,
            trigger_type="MANUAL_BACKFILL",
            teacher_ids=["T-1001"],
        )
    assert first["teacher_count"] == 1

    with session_scope(engine) as session:
        components = list(
            session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == "T-1001"
                )
            ).all()
        )
        lesson_scores = list(
            session.scalars(
                select(LessonDimensionScoreRecord).where(
                    LessonDimensionScoreRecord.teacher_id == "T-1001"
                )
            ).all()
        )
        assert len(components) == 15
        assert "FEEDBACK_REBOOK_15D" not in {
            item.component_code for item in components
        }
        assert {
            item.dimension for item in components
        } == {
            "USER_FEEDBACK",
            "RELIABILITY",
            "CLASS_QUALITY",
            "CAPACITY",
            "NEW_TEACHER_TASK",
        }
        assert all(item.projection_revision == 1 for item in components)
        task_components = [
            item for item in components if item.dimension == "NEW_TEACHER_TASK"
        ]
        task_dimension = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "T-1001",
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        assert task_dimension is not None
        assert sum(item.current_score for item in task_components) == (
            task_dimension.current_score
        )
        assert task_dimension.current_score == 0
        class_quality_lesson_scores = [
            item
            for item in lesson_scores
            if item.dimension == "CLASS_QUALITY"
        ]
        assert len(class_quality_lesson_scores) == 1
        assert class_quality_lesson_scores[0].current_score == 2
        assert class_quality_lesson_scores[0].evidence_status == "CONFIRMED"
        assert class_quality_lesson_scores[0].payload["business_facts"] == [
            {
                "code": "CLASS_QUALITY_HARDWARE",
                "business_fact": "hardware_quality_passed",
                "fact_value": True,
                "awarded": True,
                "points_per_unit": 2.0,
                "score": 2.0,
                "evidence_status": "CONFIRMED",
                "inputs": {
                    "is_camera_off": False,
                    "is_cpu_usage_high": False,
                    "is_network_delay_high": False,
                },
            }
        ]
        reliability_lesson_score = next(
            item
            for item in lesson_scores
            if item.dimension == "RELIABILITY"
        )
        perfect_component = next(
            item
            for item in reliability_lesson_score.payload["business_facts"]
            if item["business_fact"] == "is_perfect"
        )
        assert perfect_component["awarded"] is True
        assert perfect_component["score"] == 4
        assert perfect_component["evidence_status"] == "CONFIRMED"
        first_lesson_revisions = {
            (item.lesson_id, item.dimension): item.current_revision
            for item in lesson_scores
        }

    with session_scope(engine) as session:
        refresh_persisted_score_read_models(
            session,
            trigger_type="MANUAL_BACKFILL",
            teacher_ids=["T-1001"],
        )
    with session_scope(engine) as session:
        assert all(
            item.projection_revision == 2
            for item in session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == "T-1001"
                )
            ).all()
        )
        for item in session.scalars(
            select(LessonDimensionScoreRecord).where(
                LessonDimensionScoreRecord.teacher_id == "T-1001"
            )
        ).all():
            assert item.current_revision == (
                first_lesson_revisions[(item.lesson_id, item.dimension)] + 1
            )

    scorecard = ScoreReadService(engine).teacher_scorecard(
        "T-1001",
        lesson_page=1,
        lesson_page_size=12,
    )
    assert scorecard["teacher_id"] == "T-1001"
    assert len(scorecard["dimensions"]) == 5
    assert {
        item["code"]: item["source_mode"]
        for item in scorecard["dimensions"]
        } == {
            "USER_FEEDBACK": "REAL",
            "RELIABILITY": "DERIVED_REAL",
        "CLASS_QUALITY": "DERIVED_REAL",
        "CAPACITY": "DERIVED_REAL",
        "NEW_TEACHER_TASK": "SYSTEM_TASK_STATUS",
    }
    assert scorecard["lessons"]["page"] == 1
    for lesson in scorecard["lessons"]["items"]:
        assert "business_facts" in lesson
        class_quality = next(
            item
            for item in lesson["dimensions"]
            if item["code"] == "CLASS_QUALITY"
        )
        assert class_quality["score"] == 2
        assert class_quality["evidence_status"] == "CONFIRMED"
        assert {
            item["code"] for item in lesson["dimensions"]
        } <= {"USER_FEEDBACK", "RELIABILITY", "CLASS_QUALITY"}

    response = client.get(
        "/api/teachers/T-1001/scorecard",
        params={"lesson_page": 1, "lesson_page_size": 12},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["teacher_id"] == "T-1001"
    assert len(body["dimensions"]) == 5
    assert body["lessons"]["page_size"] == 12

    with session_scope(engine) as session:
        lesson = session.scalar(
            select(LessonFactRecord).where(
                LessonFactRecord.teacher_id == "T-1001"
            )
        )
        assert lesson is not None
        lesson.is_network_delay_high = True
        refresh_persisted_score_read_models(
            session,
            trigger_type="LESSON_SOURCE_UPDATED",
            trigger_ref=lesson.lesson_id,
            teacher_ids=["T-1001"],
        )
    with session_scope(engine) as session:
        class_quality_account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == "T-1001",
                ScoreAccountRecord.dimension == "CLASS_QUALITY",
            )
        )
        class_quality_lesson = session.scalar(
            select(LessonDimensionScoreRecord).where(
                LessonDimensionScoreRecord.teacher_id == "T-1001",
                LessonDimensionScoreRecord.dimension == "CLASS_QUALITY",
            )
        )
        assert class_quality_account is not None
        assert class_quality_account.current_score == 0
        assert class_quality_lesson is not None
        assert class_quality_lesson.current_score == 0
        assert class_quality_lesson.evidence_status == "CONFIRMED"

        lesson = session.scalar(
            select(LessonFactRecord).where(
                LessonFactRecord.teacher_id == "T-1001"
            )
        )
        assert lesson is not None
        lesson.is_network_delay_high = None
        refresh_persisted_score_read_models(
            session,
            trigger_type="LESSON_SOURCE_UPDATED",
            trigger_ref=lesson.lesson_id,
            teacher_ids=["T-1001"],
        )
    with session_scope(engine) as session:
        class_quality_lesson = session.scalar(
            select(LessonDimensionScoreRecord).where(
                LessonDimensionScoreRecord.teacher_id == "T-1001",
                LessonDimensionScoreRecord.dimension == "CLASS_QUALITY",
            )
        )
        assert class_quality_lesson is not None
        assert class_quality_lesson.current_score == 0
        assert class_quality_lesson.evidence_status == "SOURCE_MISSING"
