from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import engine, session_scope
from app.db_models import (
    LessonDimensionScoreRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TeacherRecord,
)
from app.score_read_model import refresh_persisted_score_read_models
from app.score_read_service import ScoreReadService
from app.main import app


client = TestClient(app)


def test_score_read_model_is_idempotent_and_queryable() -> None:
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
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
        assert len(components) == 14
        assert "FEEDBACK_REBOOK_15D" not in {
            item.component_code for item in components
        }
        assert {
            item.dimension for item in components
        } == {
            "USER_FEEDBACK",
            "RELIABILITY",
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
        assert class_quality_lesson_scores[0].current_score == 0
        assert class_quality_lesson_scores[0].evidence_status == "NOT_APPLICABLE"
        assert class_quality_lesson_scores[0].payload["business_facts"] == []
        reliability_lesson_score = next(
            item
            for item in lesson_scores
            if item.dimension == "RELIABILITY"
        )
        perfect_component = next(
            item
            for item in reliability_lesson_score.payload["business_facts"]
            if item["business_fact"] == "perfect_cnt"
        )
        assert perfect_component["awarded"] is False
        assert perfect_component["score"] == 0
        assert perfect_component["evidence_status"] == "SOURCE_MISSING"
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
        "RELIABILITY": "REAL",
        "CLASS_QUALITY": "NOT_APPLICABLE",
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
        assert class_quality["score"] == 0
        assert class_quality["evidence_status"] == "NOT_APPLICABLE"
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
