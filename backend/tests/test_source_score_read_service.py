from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from typing import Iterator

import pytest
from sqlalchemy import delete, event

from app.database import engine, session_scope
from app.db_models import (
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from app.score_read_service import ScoreReadService


NOW = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
SOURCE_TEACHER_ID = "SCORE-READ-SOURCE"
LEGACY_TEACHER_ID = "SCORE-READ-LEGACY"
SOURCE_LESSON_IDS = ("SOURCE-LESSON-OLD", "SOURCE-LESSON-NEW")


def _clean_records() -> None:
    teacher_ids = (SOURCE_TEACHER_ID, LEGACY_TEACHER_ID)
    with session_scope(engine) as session:
        session.execute(
            delete(LessonScoreResultRecord).where(
                LessonScoreResultRecord.lesson_id.in_(SOURCE_LESSON_IDS)
            )
        )
        session.execute(
            delete(LessonSourceWideRecord).where(
                LessonSourceWideRecord.teacher_id.in_(teacher_ids)
            )
        )
        session.execute(
            delete(TeacherSourceWideRecord).where(
                TeacherSourceWideRecord.tchr_id.in_(teacher_ids)
            )
        )
        session.execute(
            delete(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id.in_(teacher_ids)
            )
        )
        session.execute(
            delete(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id.in_(teacher_ids)
            )
        )
        session.execute(
            delete(TeacherRecord).where(TeacherRecord.teacher_id.in_(teacher_ids))
        )


@pytest.fixture(autouse=True)
def _isolated_score_read_records():
    _clean_records()
    yield
    _clean_records()


@contextmanager
def _captured_sql() -> Iterator[list[str]]:
    statements: list[str] = []

    def capture(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def _teacher(teacher_id: str, *, source_snapshot_label: str) -> TeacherRecord:
    return TeacherRecord(
        teacher_id=teacher_id,
        camp_enrollment_id=f"CAMP:{teacher_id}",
        name=teacher_id,
        country=None,
        timezone="UTC",
        camp_day=1,
        graduation_state="IN_PROGRESS",
        gold_qualified=False,
        total_score=16,
        graduation_threshold=60,
        data_mode="REAL",
        source_snapshot_label=source_snapshot_label,
        payload={
            "score_policy_version": "score-policy-test",
            "score_policy_sha256": "a" * 64,
            "external_display_score": 16,
            "graduation_criteria_met": False,
            "gold_criteria_met": False,
        },
        created_at=NOW,
        updated_at=NOW,
    )


def _source_lesson(
    lesson_id: str,
    *,
    lesson_date: date,
) -> LessonSourceWideRecord:
    return LessonSourceWideRecord(
        course_id=lesson_id,
        lesson_date=lesson_date,
        lesson_time=time(10, 30),
        is_peak=True,
        teacher_id=SOURCE_TEACHER_ID,
        student_id="STUDENT-SOURCE",
        lesson_status="end",
        absence_reason_detail=None,
        is_late=False,
        is_early=False,
        negative_score=None,
        has_negative_feedback_tag=False,
        complaint_category_l1=None,
        complaint_category_l2=None,
        complaint_category_l3=None,
        is_blocked=False,
        is_favorited=True,
        has_positive_feedback_tag=True,
        feedback_detail="Good class",
        is_camera_off=False,
        is_cpu_usage_high=False,
        is_network_delay_high=False,
        is_false_early_leave=False,
    )


def _source_teacher(teacher_id: str) -> TeacherSourceWideRecord:
    return TeacherSourceWideRecord(tchr_id=teacher_id, real_name=teacher_id)


def _lesson_result(lesson_id: str) -> LessonScoreResultRecord:
    return LessonScoreResultRecord(
        lesson_id=lesson_id,
        user_feedback_score=10,
        reliability_score=4,
        class_quality_score=2,
        lesson_total_score=16,
        dimensions={
            "USER_FEEDBACK": {
                "components": [
                    {
                        "awarded": True,
                        "score": 5,
                        "evidence_status": "CONFIRMED",
                    },
                    {
                        "awarded": True,
                        "score": 5,
                        "evidence_status": "CONFIRMED",
                    },
                ]
            },
            "RELIABILITY": {
                "components": [
                    {
                        "awarded": True,
                        "score": 4,
                        "evidence_status": "CONFIRMED",
                    },
                    {
                        "awarded": False,
                        "score": 0,
                        "evidence_status": "SOURCE_MISSING",
                    },
                ]
            },
            "CLASS_QUALITY": {
                "components": [
                    {
                        "awarded": True,
                        "score": 2,
                        "evidence_status": "CONFIRMED",
                    }
                ]
            },
        },
        score_rule_version="score-policy-test",
        projection_revision=1,
        calculated_at=NOW,
    )


def test_source_wide_teacher_reads_only_current_source_lesson_results() -> None:
    with session_scope(engine) as session:
        session.add(_source_teacher(SOURCE_TEACHER_ID))
        session.add(
            _teacher(
                SOURCE_TEACHER_ID,
                source_snapshot_label="SOURCE_WIDE_CURRENT",
            )
        )
        for lesson_id, lesson_date in zip(
            SOURCE_LESSON_IDS,
            (date(2026, 8, 1), date(2026, 8, 5)),
        ):
            session.add(_source_lesson(lesson_id, lesson_date=lesson_date))
            session.add(_lesson_result(lesson_id))

    with _captured_sql() as statements:
        scorecard = ScoreReadService(engine).teacher_scorecard(
            SOURCE_TEACHER_ID,
            lesson_page=1,
            lesson_page_size=1,
        )

    sql = "\n".join(statements).lower()
    assert "lesson_source_wide" in sql
    assert "lesson_score_results" in sql
    assert "teacher_source_wide" in sql
    assert "teacher_metric_snapshots" not in sql
    assert "lesson_facts" not in sql
    assert "lesson_dimension_scores" not in sql

    assert set(scorecard) == {
        "teacher_id",
        "camp_enrollment_id",
        "score_rule_version",
        "score_policy_sha256",
        "raw_total_score",
        "public_total_score",
        "graduation_state",
        "graduation_qualified",
        "gold_qualified",
        "graduation_current_criteria_met",
        "gold_current_criteria_met",
        "calculated_at",
        "source",
        "dimensions",
        "lessons",
        "thresholds",
    }
    assert set(scorecard["source"]) == {
        "teacher_source_status",
        "score_projection_id",
    }
    assert scorecard["source"]["teacher_source_status"] == "CONFIRMED"
    assert scorecard["thresholds"] == {"graduation_raw_score": 60.0}
    assert scorecard["lessons"]["total"] == 2
    assert scorecard["lessons"]["page"] == 1
    assert scorecard["lessons"]["page_size"] == 1
    assert len(scorecard["lessons"]["items"]) == 1
    lesson = scorecard["lessons"]["items"][0]
    assert set(lesson) == {
        "lesson_id",
        "source_appoint_id",
        "scheduled_start_at",
        "lesson_local_date",
        "lesson_local_time",
        "status",
        "valid_for_scoring",
        "evidence_status",
        "business_facts",
        "dimensions",
    }
    assert lesson["lesson_id"] == "SOURCE-LESSON-NEW"
    assert lesson["source_appoint_id"] == "SOURCE-LESSON-NEW"
    assert lesson["scheduled_start_at"] == "2026-08-05T10:30:00+00:00"
    assert lesson["status"] == "end"
    assert lesson["valid_for_scoring"] is True
    assert lesson["evidence_status"] == "PARTIAL"
    assert lesson["business_facts"]["user_feedback"]["is_rebooked"] is None
    assert lesson["business_facts"]["classroom_quality"] == {
        "is_camera_off": False,
        "is_cpu_usage_high": False,
        "is_network_delay_high": False,
        "hardware_quality_passed": True,
        "is_perfect": True,
    }
    assert [item["code"] for item in lesson["dimensions"]] == [
        "USER_FEEDBACK",
        "RELIABILITY",
        "CLASS_QUALITY",
    ]
    assert [item["evidence_status"] for item in lesson["dimensions"]] == [
        "CONFIRMED",
        "PARTIAL",
        "CONFIRMED",
    ]
    assert [item["evidence_coverage"] for item in lesson["dimensions"]] == [
        "2/2",
        "1/2",
        "1/1",
    ]
    assert [
        item["code"] for item in lesson["dimensions"][0]["business_facts"]
    ] == ["FEEDBACK_PRAISE", "FEEDBACK_FAVORITE"]


def test_non_source_teacher_never_uses_a_legacy_read_branch() -> None:
    with session_scope(engine) as session:
        session.add(
            _teacher(
                LEGACY_TEACHER_ID,
                source_snapshot_label="LEGACY_IMPORT",
            )
        )

    with _captured_sql() as statements:
        scorecard = ScoreReadService(engine).teacher_scorecard(
            LEGACY_TEACHER_ID,
        )

    sql = "\n".join(statements).lower()
    assert "teacher_source_wide" in sql
    assert "lesson_source_wide" in sql
    assert "lesson_score_results" in sql
    assert "teacher_metric_snapshots" not in sql
    assert "lesson_facts" not in sql
    assert "lesson_dimension_scores" not in sql
    assert scorecard["source"]["teacher_source_status"] == "SOURCE_MISSING"
    assert scorecard["lessons"] == {
        "page": 1,
        "page_size": 12,
        "total": 0,
        "items": [],
    }


def test_source_lesson_without_result_is_explicitly_source_missing() -> None:
    with session_scope(engine) as session:
        session.add(_source_teacher(SOURCE_TEACHER_ID))
        session.add(
            _teacher(
                SOURCE_TEACHER_ID,
                source_snapshot_label="SOURCE_WIDE_CURRENT",
            )
        )
        session.add(
            _source_lesson(
                SOURCE_LESSON_IDS[0],
                lesson_date=date(2026, 8, 1),
            )
        )

    scorecard = ScoreReadService(engine).teacher_scorecard(SOURCE_TEACHER_ID)

    assert scorecard["lessons"]["total"] == 1
    lesson = scorecard["lessons"]["items"][0]
    assert lesson["lesson_id"] == SOURCE_LESSON_IDS[0]
    assert lesson["evidence_status"] == "SOURCE_MISSING"
    assert lesson["dimensions"] == []


def test_confirmed_zero_score_is_not_treated_as_missing_source() -> None:
    result = _lesson_result(SOURCE_LESSON_IDS[0])
    result.user_feedback_score = 0
    result.reliability_score = 0
    result.class_quality_score = 0
    result.lesson_total_score = 0
    result.dimensions = {
        dimension: {
            "components": [
                {
                    "awarded": False,
                    "score": 0,
                    "evidence_status": "CONFIRMED",
                }
                for _component in raw_dimension["components"]
            ]
        }
        for dimension, raw_dimension in result.dimensions.items()
    }
    with session_scope(engine) as session:
        session.add(_source_teacher(SOURCE_TEACHER_ID))
        session.add(
            _teacher(
                SOURCE_TEACHER_ID,
                source_snapshot_label="SOURCE_WIDE_CURRENT",
            )
        )
        session.add(
            _source_lesson(
                SOURCE_LESSON_IDS[0],
                lesson_date=date(2026, 8, 1),
            )
        )
        session.add(result)

    scorecard = ScoreReadService(engine).teacher_scorecard(SOURCE_TEACHER_ID)

    lesson = scorecard["lessons"]["items"][0]
    assert lesson["evidence_status"] == "CONFIRMED"
    assert [dimension["score"] for dimension in lesson["dimensions"]] == [
        0.0,
        0.0,
        0.0,
    ]
