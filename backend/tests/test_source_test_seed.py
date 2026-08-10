from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from app.db_models import LessonSourceWideRecord, TeacherSourceWideRecord
from app.source_test_seed import (
    SCENARIO,
    SourceTestSeedCollisionError,
    seed_source_test_data,
)
from scripts import seed_source_test_data as seed_script


@pytest.fixture
def source_engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    TeacherSourceWideRecord.__table__.create(engine)
    LessonSourceWideRecord.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _counts(bind: Engine) -> tuple[int, int]:
    with Session(bind) as session:
        teacher_count = int(
            session.scalar(
                select(func.count()).select_from(TeacherSourceWideRecord)
            )
            or 0
        )
        lesson_count = int(
            session.scalar(
                select(func.count()).select_from(LessonSourceWideRecord)
            )
            or 0
        )
    return teacher_count, lesson_count


def test_preview_is_the_default_and_rolls_back(source_engine: Engine) -> None:
    result = seed_source_test_data(source_engine)

    assert result == {
        "scenario": SCENARIO,
        "applied": False,
        "teacher_count": 2,
        "lesson_count": 4,
    }
    assert _counts(source_engine) == (0, 0)


def test_apply_writes_exact_rows_and_repeated_apply_is_idempotent(
    source_engine: Engine,
) -> None:
    first = seed_source_test_data(source_engine, apply=True)
    second = seed_source_test_data(source_engine, apply=True)

    assert first == second == {
        "scenario": SCENARIO,
        "applied": True,
        "teacher_count": 2,
        "lesson_count": 4,
    }
    assert _counts(source_engine) == (2, 4)

    with Session(source_engine) as session:
        teachers = session.scalars(
            select(TeacherSourceWideRecord).order_by(
                TeacherSourceWideRecord.tchr_id
            )
        ).all()
        lessons = session.scalars(
            select(LessonSourceWideRecord).order_by(
                LessonSourceWideRecord.course_id
            )
        ).all()

    assert [teacher.tchr_id for teacher in teachers] == [
        "TEST-SOURCE-TEACHER-001",
        "TEST-SOURCE-TEACHER-002",
    ]
    assert all("TEST ONLY" in (teacher.real_name or "") for teacher in teachers)
    assert all(teacher.is_cpl_tesol is None for teacher in teachers)
    assert all(teacher.is_self_introduce is None for teacher in teachers)
    assert [lesson.course_id for lesson in lessons] == [
        "TEST-SOURCE-LESSON-001",
        "TEST-SOURCE-LESSON-002",
        "TEST-SOURCE-LESSON-003",
        "TEST-SOURCE-LESSON-004",
    ]

    normal, attendance, complaint, hardware = lessons
    assert (normal.is_late, normal.is_early, normal.is_camera_off) == (
        False,
        False,
        False,
    )
    assert attendance.is_late is True
    assert attendance.is_early is True
    assert complaint.complaint_category_l3 == "TEST-COMPLAINT-L3"
    assert hardware.is_camera_off is True
    assert hardware.is_cpu_usage_high is True
    assert hardware.is_network_delay_high is True


def test_seed_does_not_clean_unrelated_source_rows(source_engine: Engine) -> None:
    with Session(source_engine) as session:
        session.add(
            TeacherSourceWideRecord(
                tchr_id="UNRELATED-TEST-TEACHER",
                real_name="Unrelated Synthetic Teacher [TEST ONLY]",
            )
        )
        session.add(
            LessonSourceWideRecord(
                course_id="UNRELATED-TEST-LESSON",
                teacher_id="UNRELATED-TEST-TEACHER",
            )
        )
        session.commit()

    seed_source_test_data(source_engine, apply=True)

    assert _counts(source_engine) == (3, 5)


def test_core_seed_rejects_non_test_environment_before_writing(
    source_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(RuntimeError, match="source test seed requires APP_ENV"):
        seed_source_test_data(source_engine, apply=True)

    assert _counts(source_engine) == (0, 0)


def test_reserved_id_collision_fails_without_overwriting(
    source_engine: Engine,
) -> None:
    with Session(source_engine) as session:
        session.add(
            TeacherSourceWideRecord(
                tchr_id="TEST-SOURCE-TEACHER-001",
                real_name="Conflicting Synthetic Value [TEST ONLY]",
            )
        )
        session.commit()

    with pytest.raises(SourceTestSeedCollisionError, match="reserved synthetic"):
        seed_source_test_data(source_engine, apply=True)

    assert _counts(source_engine) == (1, 0)
    with Session(source_engine) as session:
        existing = session.get(
            TeacherSourceWideRecord,
            "TEST-SOURCE-TEACHER-001",
        )
        assert existing is not None
        assert existing.real_name == "Conflicting Synthetic Value [TEST ONLY]"


def test_script_rejects_non_test_environment_before_building_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        seed_script,
        "build_engine",
        lambda: (_ for _ in ()).throw(
            AssertionError("engine must not be built in production")
        ),
    )

    assert seed_script.main([]) == 2
    assert "APP_ENV" in capsys.readouterr().err


def test_script_preview_is_safe_and_unguarded_apply_is_rejected(
    source_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "do-not-print-this-password"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://owner:{secret}@db.invalid/test",
    )
    monkeypatch.setattr(seed_script, "build_engine", lambda: source_engine)
    monkeypatch.setattr(source_engine, "dispose", lambda: None)

    assert seed_script.main([]) == 0
    preview_output = capsys.readouterr().out
    assert '"applied": false' in preview_output
    assert secret not in preview_output
    assert _counts(source_engine) == (0, 0)

    assert seed_script.main(["--apply"]) == 2
    rejected = capsys.readouterr()
    assert "--apply requires --test-database" in rejected.err
    assert secret not in rejected.err
    assert _counts(source_engine) == (0, 0)


def test_test_database_seed_uses_the_double_database_guard(
    source_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    database_url = "postgresql+psycopg://owner:hidden@db.invalid/tit_growth_test_v2"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        seed_script.migrate_test_database,
        "_expected_database",
        lambda: "tit_growth_test_v2",
    )
    monkeypatch.setattr(
        seed_script.migrate_test_database,
        "_reject_ambient_libpq_environment",
        lambda: events.append("environment"),
    )
    monkeypatch.setattr(
        seed_script.migrate_test_database,
        "_database_url",
        lambda: database_url,
    )

    def approve(received_url: str) -> None:
        assert received_url == database_url
        events.append("approved")

    def validate(received_url: str, expected: str) -> None:
        assert received_url == database_url
        assert expected == "tit_growth_test_v2"
        events.append("guarded")

    def guarded_build(received_url: str | None = None):
        assert events == ["environment", "approved", "guarded"]
        assert received_url == database_url
        return source_engine

    monkeypatch.setattr(
        seed_script.migrate_test_database,
        "_validate_approved_test_identity",
        approve,
    )
    monkeypatch.setattr(
        seed_script.migrate_test_database,
        "_validate_database_target",
        validate,
    )
    monkeypatch.setattr(seed_script, "build_engine", guarded_build)
    monkeypatch.setattr(source_engine, "dispose", lambda: None)

    assert seed_script.main(["--test-database"]) == 0
    assert events == ["environment", "approved", "guarded"]
    assert '"applied": false' in capsys.readouterr().out
    assert _counts(source_engine) == (0, 0)

    events.clear()
    assert seed_script.main(["--test-database", "--apply"]) == 0
    assert events == ["environment", "approved", "guarded"]
    assert '"applied": true' in capsys.readouterr().out
    assert _counts(source_engine) == (2, 4)
