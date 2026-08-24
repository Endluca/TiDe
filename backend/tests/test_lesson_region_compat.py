from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, insert, select

from app.db_models import (
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    PersonalizedTriggerMatchRecord,
)


VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _migration(filename: str):
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expand_contract_revisions_are_ordered_around_v2_and_outbox() -> None:
    expand = _migration("20260822_65a_lesson_region_expand.py")
    rev66 = _migration("20260822_66_dts_v2_shadow_source.py")
    contract = _migration("20260822_84a_lesson_region_contract.py")
    rev85 = _migration("20260822_85_outbox_three_state.py")

    assert expand.down_revision == "20260819_65_g09_set_course"
    assert rev66.down_revision == expand.revision
    assert contract.down_revision == "20260822_84_dts_v2_domain_outbox"
    assert rev85.down_revision == contract.revision


def test_expand_writer_contract_is_explicit_or_transaction_local_only() -> None:
    source = (
        VERSIONS / "20260822_65a_lesson_region_expand.py"
    ).read_text(encoding="utf-8")

    assert 'sa.Column("source_region", sa.String(length=8), nullable=True)' in source
    assert "current_setting('tit.dts_source_region',true)" in source
    assert "set_config('tit.dts_source_region',p_source_region,true)" in source
    assert "context_region IS NULL" in source
    assert "context_region NOT IN ('dom','ovs')" in source
    assert "COMPAT_REGION_CONTEXT_MISMATCH" in source
    assert "source_region := 'dom'" not in source
    assert "server_default='dom'" not in source
    assert 'server_default=sa.text("\'dom\'")' not in source


def test_contract_backfill_is_manifest_only_and_fail_closed() -> None:
    source = (
        VERSIONS / "20260822_84a_lesson_region_contract.py"
    ).read_text(encoding="utf-8")

    assert "COMPAT_WRITER_NOT_READY" in source
    assert "COMPAT_REGION_MISSING" in source
    assert "FROM public.lesson_source_region_backfill_manifest manifest" in source
    assert "SET source_region=manifest.source_region" in source
    assert "SYSTEM:FRESH_EMPTY_SCHEMA" in source
    assert "AND NOT EXISTS (SELECT 1 FROM public.lesson_source_wide)" in source
    assert "GROUP BY source_region,\"课程id\"" in source
    assert '["source_region", "课程id"]' in source
    assert "payload->>'source_region' IS NULL" in source
    assert (
        "DISABLE TRIGGER trg_lesson_source_wide_outbox_v1" in source
    )
    assert (
        "DISABLE TRIGGER guard_dom_lesson_student_privacy_v1" in source
    )
    assert "ENABLE TRIGGER trg_lesson_source_wide_outbox_v1" in source
    assert "COMPAT_REGION_STUDENT_PRIVACY_INVALID" in source
    assert "SET source_region='dom'" not in source
    assert "SET source_region='ovs'" not in source


def test_current_models_use_composite_lesson_identity_and_consumers() -> None:
    lesson = LessonSourceWideRecord.__table__
    result = LessonScoreResultRecord.__table__
    match = PersonalizedTriggerMatchRecord.__table__

    assert tuple(column.name for column in lesson.primary_key) == (
        "source_region",
        "课程id",
    )
    assert tuple(column.name for column in result.primary_key) == (
        "lesson_source_region",
        "lesson_id",
    )
    result_fk = next(
        constraint
        for constraint in result.foreign_key_constraints
        if constraint.name == "fk_lesson_score_result_source_lesson_region"
    )
    match_fk = next(
        constraint
        for constraint in match.foreign_key_constraints
        if constraint.name == "fk_personalized_trigger_match_lesson_region"
    )
    expected_targets = (
        "lesson_source_wide.source_region",
        "lesson_source_wide.课程id",
    )
    assert tuple(item.target_fullname for item in result_fk.elements) == expected_targets
    assert tuple(item.target_fullname for item in match_fk.elements) == expected_targets


def test_same_course_id_can_exist_once_per_region_in_sqlite_model() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    lesson = LessonSourceWideRecord.__table__
    lesson.create(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(lesson),
            [
                {
                    "source_region": "dom",
                    "课程id": "SAME-COURSE",
                    "老师id": "DOM-TEACHER",
                },
                {
                    "source_region": "ovs",
                    "课程id": "SAME-COURSE",
                    "老师id": "OVS-TEACHER",
                },
            ],
        )
        rows = connection.execute(
            select(
                lesson.c.source_region,
                lesson.c["课程id"],
                lesson.c["老师id"],
            ).order_by(lesson.c.source_region)
        ).all()

    assert rows == [
        ("dom", "SAME-COURSE", "DOM-TEACHER"),
        ("ovs", "SAME-COURSE", "OVS-TEACHER"),
    ]
    assert isinstance(
        next(
            constraint
            for constraint in lesson.constraints
            if constraint.name == "ck_lesson_source_wide_region"
        ),
        sa.CheckConstraint,
    )
