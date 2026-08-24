from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, insert, select

from app.db_models import (
    DtsSourceRowRecord,
    LessonSourceWideRecord,
    TeacherSourceWideRecord,
)
from app.dts_direct_projector import (
    DtsDirectWideProjector,
    direct_projection_event,
    schedule_slot_is_peak,
    slot_activation,
)
from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConfigurationError,
    DtsRecordError,
)
from app.dts_ingest_store import DtsIngestStoreError, PostgresDtsEventSink
from app.dts_wide_projector import DtsWideProjectionError
from app.personalized_rules import evaluate_lesson
from scripts.run_dts_ingest import _projection_mode


def _schedule_event(
    *,
    operation: str = "UPDATE",
    before_status: str | None = "off",
    after_status: str | None = "on",
) -> DtsChangeEvent:
    before = (
        {
            "id": 1,
            "teacher_id": 123,
            "date": "2026-08-19",
            "time_slot": 37,
            "status": before_status,
        }
        if before_status is not None
        else None
    )
    after = (
        {
            "id": 1,
            "teacher_id": 123,
            "date": "2026-08-19",
            "time_slot": 37,
            "status": after_status,
        }
        if after_status is not None
        else None
    )
    return DtsChangeEvent(
        source_region="dom",
        topic="dom-topic",
        partition=0,
        offset=1,
        record_id=1,
        source_timestamp=1,
        source_txid="tx",
        source_position="position",
        operation=operation,
        database_name="dom",
        schema_name="public",
        table_name="dom_teacher_class_schedule",
        before=before,
        after=after,
    )


def _direct_projector() -> DtsDirectWideProjector:
    return DtsDirectWideProjector(
        environ={
            "TIT_DTS_COHORT_START": "2026-08-19",
        }
    )


def _child_event(
    *,
    table_name: str,
    offset: int,
    source_region: str = "dom",
    operation: str = "INSERT",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region=source_region,
        topic=f"{source_region}-topic",
        partition=0,
        offset=offset,
        record_id=offset,
        source_timestamp=offset,
        source_txid="tx",
        source_position=str(offset),
        operation=operation,
        database_name="dom",
        schema_name="public",
        table_name=table_name,
        before=before,
        after=after,
    )


def test_slot_activation_counts_only_first_transition_to_on() -> None:
    activated = slot_activation(_schedule_event())
    assert activated is not None
    assert activated["time_slot"] == 37

    assert slot_activation(
        _schedule_event(before_status="on", after_status="on")
    ) is None
    assert slot_activation(
        _schedule_event(before_status="on", after_status="off")
    ) is None
    assert slot_activation(
        _schedule_event(
            operation="DELETE",
            before_status="on",
            after_status=None,
        )
    ) is None


def test_inserted_open_slot_is_one_activation() -> None:
    assert slot_activation(
        _schedule_event(
            operation="INSERT",
            before_status=None,
            after_status="on",
        )
    ) is not None


def test_domestic_peak_ranges_match_confirmed_slot_rule() -> None:
    weekday = date(2026, 8, 19)  # Wednesday
    weekend = date(2026, 8, 22)  # Saturday

    assert schedule_slot_is_peak("dom", weekday, 37) is True
    assert schedule_slot_is_peak("dom", weekday, 44) is True
    assert schedule_slot_is_peak("dom", weekday, 19) is False
    assert schedule_slot_is_peak("dom", weekend, 19) is True
    assert schedule_slot_is_peak("dom", weekend, 24) is True
    assert schedule_slot_is_peak("dom", weekend, 25) is False


def test_overseas_peak_ranges_keep_overnight_and_weekend_morning() -> None:
    weekday = date(2026, 8, 19)
    weekend = date(2026, 8, 22)

    assert schedule_slot_is_peak("ovs", weekday, 1) is True
    assert schedule_slot_is_peak("ovs", weekday, 12) is True
    assert schedule_slot_is_peak("ovs", weekday, 37) is True
    assert schedule_slot_is_peak("ovs", weekday, 48) is True
    assert schedule_slot_is_peak("ovs", weekday, 19) is False
    assert schedule_slot_is_peak("ovs", weekend, 19) is True


def test_schedule_event_increments_slot_counters_once_without_history() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
                total_slot_cnt=0,
                reg_slot_cnt=0,
                peak_slot_cnt=0,
                slot_days=0,
                peak_slot_days=0,
            )
        )
        projector.apply(connection, _schedule_event())
        projector.apply(
            connection,
            _schedule_event(before_status="on", after_status="on"),
        )
        row = connection.execute(
            select(table).where(table.c.tchr_id == "123")
        ).mappings().one()

    assert row["total_slot_cnt"] == 1
    assert row["reg_slot_cnt"] == 0
    assert row["peak_slot_cnt"] == 1
    assert row["slot_days"] == 1
    assert row["peak_slot_days"] == 1
    assert row["capacity_peak_slot_rate"] == 1.0
    assert row["capacity_key_slot_day_rate"] == 1.0


def test_appoint_insert_and_delete_apply_exact_teacher_deltas() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    appoint = {
        "id": 99,
        "t_id": 123,
        "student_token": "dom:v1:" + "b" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "start_time": "2026-08-19 18:00:00",
        "status": "end",
        "use_point": "buy",
        "week": 3,
    }
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=6,
                after=appoint,
            ),
        )
        inserted_teacher = connection.execute(
            select(teacher_table)
        ).mappings().one()
        inserted_lesson = connection.execute(
            select(lesson_table)
        ).mappings().one()

        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=7,
                operation="DELETE",
                before=appoint,
            ),
        )
        deleted_teacher = connection.execute(
            select(teacher_table)
        ).mappings().one()
        remaining_lessons = connection.execute(
            select(lesson_table)
        ).mappings().all()

    assert inserted_lesson["课程id"] == "99"
    assert inserted_teacher["total_booked_cnt"] == 1
    assert inserted_teacher["total_completed_cnt"] == 1
    assert inserted_teacher["peak_completed_cnt"] == 1
    assert inserted_lesson["迟到"] is None
    assert inserted_lesson["早退"] is None
    assert inserted_teacher["perfect_cnt"] == 0
    assert inserted_teacher["first_completed_student_cnt"] == 1
    assert deleted_teacher["total_booked_cnt"] == 0
    assert deleted_teacher["total_completed_cnt"] == 0
    assert deleted_teacher["perfect_cnt"] == 0
    assert deleted_teacher["first_completed_student_cnt"] == 0
    assert deleted_teacher["first_booked_dt"] is None
    assert deleted_teacher["first_completed_dt"] is None
    assert remaining_lessons == []


def test_appoint_admission_keeps_on_free_null_student_after_day_30() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=61,
                after={
                    "id": 991,
                    "t_id": 123,
                    "date": "2026-10-01",
                    "time": "18:00:00",
                    "status": "on",
                    "use_point": "free",
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()
        teacher = connection.execute(select(teacher_table)).mappings().one()

    assert lesson["课程状态"] == "on"
    assert lesson["学员id"] is None
    assert teacher["total_booked_cnt"] == 1
    assert teacher["capacity_avg_completed_per_day"] == 0.0


def test_direct_fails_closed_if_revision_71_has_not_normalized_dmo() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dmo",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "991",
                    "老师id": "123",
                    "课程状态": "on",
                }
            )
        )
        with pytest.raises(
            DtsWideProjectionError,
            match="DTS_DIRECT_DOM_AREA_MIGRATION_REQUIRED",
        ):
            projector.apply(
                connection,
                _child_event(
                    table_name="dom_appoint",
                    offset=62,
                    operation="UPDATE",
                    before={"id": 991, "t_id": 123, "status": "on"},
                    after={
                        "id": 991,
                        "t_id": 123,
                        "date": "2026-08-19",
                        "status": "end",
                    },
                ),
            )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["课程状态"] == "on"


def test_distinct_student_delta_does_not_double_count_favorite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                total_booked_cnt=2,
                total_completed_cnt=2,
                first_completed_student_cnt=1,
                feedback_favorite_cnt=0,
            )
        )
        connection.execute(
            insert(lesson_table),
            [
                {
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "学员id": "student-a",
                    "课程状态": "end",
                    "收藏": False,
                },
                {
                    "source_region": "dom",
                    "课程id": "2",
                    "老师id": "123",
                    "学员id": "student-a",
                    "课程状态": "end",
                    "收藏": False,
                },
            ],
        )
        projector._update_lesson(connection, "dom", "1", {"收藏": True})
        first = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "dom", "2", {"收藏": True})
        second = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "dom", "1", {"收藏": False})
        third = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "dom", "2", {"收藏": False})
        fourth = connection.execute(select(teacher_table)).mappings().one()

    assert first["feedback_favorite_cnt"] == 1
    assert first["feedback_favorite_rate"] is None
    assert first["feedback_block_rate"] is None
    assert second["feedback_favorite_cnt"] == 1
    assert third["feedback_favorite_cnt"] == 1
    assert fourth["feedback_favorite_cnt"] == 0


def test_tesol_event_uses_before_and_after_without_history_table() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    teacher_table.create(engine)
    projector = _direct_projector()
    active = {
        "id": 1,
        "teacher_id": 123,
        "certification_code": "16",
        "certification_status": 1,
    }
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(tchr_id="123", is_cpl_tesol=False)
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_certification",
                offset=8,
                after=active,
            ),
        )
        enabled = connection.execute(select(teacher_table)).mappings().one()
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_certification",
                offset=9,
                operation="DELETE",
                before=active,
            ),
        )
        deleted = connection.execute(select(teacher_table)).mappings().one()

    assert enabled["is_cpl_tesol"] is True
    assert deleted["is_cpl_tesol"] is None


def test_tesol_requires_code_16_and_completed_status() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(tchr_id="123", is_cpl_tesol=False)
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_certification",
                offset=81,
                after={
                    "id": 1,
                    "teacher_id": 123,
                    "certification_type": "tesol",
                    "certification_code": "15",
                    "certification_status": 1,
                },
            ),
        )
        wrong_code = connection.execute(select(table)).mappings().one()
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_certification",
                offset=82,
                after={
                    "id": 2,
                    "teacher_id": 123,
                    "certification_code": "16",
                    "certification_status": 0,
                },
            ),
        )
        incomplete = connection.execute(select(table)).mappings().one()

    assert wrong_code["is_cpl_tesol"] is False
    assert incomplete["is_cpl_tesol"] is None


@pytest.mark.parametrize(
    ("center_type", "expected"),
    [(1, "CBT"), (5, "TBT"), (None, "HBT"), (99, "HBT")],
)
def test_teacher_center_type_uses_confirmed_default_mapping(
    center_type: object,
    expected: str,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
    LessonSourceWideRecord.__table__.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher",
                offset=83,
                after={
                    "id": 123,
                    "center_type": center_type,
                    "status_on_time": "2026-08-19 00:00:00",
                    "course": "h5_tc",
                },
            ),
        )
        teacher = connection.execute(select(table)).mappings().one()

    assert teacher["center_type_desc"] == expected
    assert teacher["teach_area_type"] == "dom"


def test_direct_mode_requires_projection_enabled() -> None:
    assert _projection_mode(
        enabled=True,
        environ={"TIT_DTS_PROJECTION_MODE": "direct"},
    ) == "direct"
    with pytest.raises(
        DtsConfigurationError,
        match="DTS_DIRECT_PROJECTION_REQUIRES_ENABLED",
    ):
        _projection_mode(
            enabled=False,
            environ={"TIT_DTS_PROJECTION_MODE": "direct"},
        )


def test_direct_complaint_uses_persisted_category_events() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    category_table = DtsSourceRowRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    category_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                total_booked_cnt=1,
                total_completed_cnt=1,
                first_completed_student_cnt=1,
            )
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "学员id": "student-a",
                    "课程状态": "end",
                }
            )
        )
        for offset, category_id, parent_id, name in (
            (20, 13, None, "投诉"),
            (21, 20, 13, "教学问题"),
            (22, 30, 20, "课堂处理"),
        ):
            projector.apply(
                connection,
                _child_event(
                    table_name="dom_complaint_cate",
                    offset=offset,
                    after={
                        "id": category_id,
                        "cate_parent": parent_id,
                        "cate_cn_name": name,
                    },
                ),
            )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_complaint",
                offset=23,
                after={
                    "id": 99,
                    "appoint_id": 1,
                    "complaint_type": 13,
                    "complaint_type_child": 20,
                    "complaint_type_grandson": 30,
                    "approve": "y",
                    "validity": 1,
                },
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_complaint_cate",
                offset=24,
                operation="UPDATE",
                before={
                    "id": 30,
                    "cate_parent": 20,
                    "cate_cn_name": "课堂处理",
                },
                after={
                    "id": 30,
                    "cate_parent": 20,
                    "cate_cn_name": "课堂处理-新",
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()
        teacher = connection.execute(select(teacher_table)).mappings().one()
        categories = connection.execute(
            select(category_table).order_by(category_table.c.source_key)
        ).mappings().all()

    assert lesson["投诉一级分类"] == "投诉"
    assert lesson["投诉二级分类"] == "教学问题"
    assert lesson["投诉三级分类"] == "课堂处理-新"
    assert teacher["feedback_complaint_cnt"] == 1
    assert teacher["feedback_valid_complaint_cnt"] == 1
    assert len(categories) == 3
    assert all(row["source_table"] == "dom_complaint_cate" for row in categories)
    category_30 = next(
        row for row in categories if row["source_key_data"] == {"id": "30"}
    )
    assert category_30["source_row"]["cate_cn_name"] == "课堂处理-新"
    assert category_30["row_version"] == 2


def test_direct_complaint_is_ignored_when_category_event_is_missing() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    category_table = DtsSourceRowRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    category_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_complaint",
                offset=24,
                after={
                    "id": 99,
                    "appoint_id": 1,
                    "complaint_type": 13,
                    "approve": "y",
                    "validity": 1,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["投诉一级分类"] is None
    assert projector.drain_counts()["ignored"] == 1


def test_direct_missing_complaint_level3_counts_but_only_routes_pending_data() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    category_table = DtsSourceRowRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    category_table.create(engine)
    projector = _direct_projector()

    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "课程状态": "end",
                }
            )
        )
        for offset, category_id, parent_id, name in (
            (25, 13, None, "投诉"),
            (26, 20, 13, "出席问题"),
        ):
            projector.apply(
                connection,
                _child_event(
                    table_name="dom_complaint_cate",
                    offset=offset,
                    after={
                        "id": category_id,
                        "cate_parent": parent_id,
                        "cate_cn_name": name,
                    },
                ),
            )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_complaint",
                offset=27,
                after={
                    "id": 99,
                    "appoint_id": 1,
                    "complaint_type": 13,
                    "complaint_type_child": 20,
                    "complaint_type_grandson": None,
                    "approve": "y",
                    "validity": 1,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()
        teacher = connection.execute(select(teacher_table)).mappings().one()

    decisions = evaluate_lesson(dict(lesson), complaint_rules={})
    assert lesson["投诉一级分类"] == "投诉"
    assert lesson["投诉二级分类"] == "出席问题"
    assert lesson["投诉三级分类"] is None
    assert teacher["feedback_complaint_cnt"] == 1
    assert teacher["feedback_valid_complaint_cnt"] == 1
    assert len(decisions) == 1
    assert decisions[0].output_type == "PENDING_DATA"
    assert decisions[0].task_code is None


def test_midstream_teacher_update_creates_then_delete_removes_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
    LessonSourceWideRecord.__table__.create(engine)
    projector = _direct_projector()
    teacher = {
        "id": 123,
        "status_on_time": "2026-08-19 00:00:00",
        "course": "h5_tc",
    }
    with engine.begin() as connection:
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher",
                offset=25,
                operation="UPDATE",
                before={**teacher, "status": "off"},
                after={**teacher, "status": "on"},
            ),
        )
        created = connection.execute(select(table)).mappings().one()
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher",
                offset=26,
                operation="DELETE",
                before=teacher,
            ),
        )
        rows = connection.execute(select(table)).mappings().all()

    assert created["tchr_id"] == "123"
    assert created["status"] == "on"
    assert rows == []
    counts = projector.drain_counts()
    assert counts["teacher_upserts"] == 1
    assert counts["teacher_deletes"] == 1
    assert counts["ignored"] == 0


def test_midstream_appoint_update_creates_then_delete_removes_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    appoint = {
        "id": 99,
        "t_id": 123,
        "student_token": "dom:v1:" + "b" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "status": "end",
        "use_point": "buy",
    }
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
                total_booked_cnt=0,
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=27,
                operation="UPDATE",
                before={**appoint, "status": "wait"},
                after=appoint,
            ),
        )
        created = connection.execute(select(lesson_table)).mappings().one()
        teacher_after_create = connection.execute(
            select(teacher_table)
        ).mappings().one()
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=28,
                operation="DELETE",
                before=appoint,
            ),
        )
        lessons = connection.execute(select(lesson_table)).mappings().all()
        teacher = connection.execute(select(teacher_table)).mappings().one()

    assert created["课程id"] == "99"
    assert created["课程状态"] == "end"
    assert teacher_after_create["total_booked_cnt"] == 1
    assert lessons == []
    assert teacher["total_booked_cnt"] == 0
    counts = projector.drain_counts()
    assert counts["lesson_upserts"] == 1
    assert counts["lesson_deletes"] == 1
    assert counts["ignored"] == 0


def test_midstream_old_teacher_and_course_are_not_filtered_by_cohort() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_teacher",
                    offset=281,
                    operation="UPDATE",
                    before={"id": 123, "status": "off"},
                    after={
                        "id": 123,
                        "status": "on",
                        "status_on_time": "2020-01-01 00:00:00",
                        "course": "h5_tc",
                    },
                ),
                _child_event(
                    table_name="dom_appoint",
                    offset=282,
                    operation="UPDATE",
                    before={"id": 99, "status": "on"},
                    after={
                        "id": 99,
                        "t_id": 123,
                        "student_token": "dom:v1:" + "e" * 64,
                        "date": "2026-08-19",
                        "time": "18:00:00",
                        "status": "on",
                        "use_point": "buy",
                    },
                ),
            ),
        )
        teachers = connection.execute(select(teacher_table)).mappings().all()
        lessons = connection.execute(select(lesson_table)).mappings().all()

    assert len(teachers) == 1
    assert teachers[0]["onboard_date"] == date(2020, 1, 1)
    assert len(lessons) == 1
    assert lessons[0]["课程id"] == "99"
    counts = projector.drain_counts()
    assert counts["ignored"] == 0
    assert counts["batch_prefiltered"] == 0


def test_teacher_update_with_missing_onboard_keeps_teacher_and_lesson() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    teacher = {
        "id": 123,
        "status": "on",
        "status_on_time": "2020-01-01 00:00:00",
        "course": "h5_tc",
    }
    appoint = {
        "id": 99,
        "t_id": 123,
        "student_token": "dom:v1:" + "f" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "status": "on",
    }
    with engine.begin() as connection:
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher",
                offset=283,
                after=teacher,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=284,
                after=appoint,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher",
                offset=285,
                operation="UPDATE",
                before=teacher,
                after={**teacher, "status_on_time": None},
            ),
        )
        stored_teacher = connection.execute(
            select(teacher_table)
        ).mappings().one()
        stored_lessons = connection.execute(
            select(lesson_table)
        ).mappings().all()

    assert stored_teacher["status_on_date"] is None
    assert stored_teacher["onboard_date"] is None
    assert stored_teacher["onboard_30d_end_date"] is None
    assert stored_teacher["job_days"] is None
    assert stored_teacher["job_month"] is None
    assert [row["课程id"] for row in stored_lessons] == ["99"]
    counts = projector.drain_counts()
    assert counts["teacher_deletes"] == 0
    assert counts["lesson_deletes"] == 0


def test_midstream_teacher_child_events_ignore_missing_teacher() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        projector.apply(connection, _schedule_event())
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_certification",
                offset=29,
                after={
                    "id": 1,
                    "teacher_id": 123,
                    "certification_type": "tesol",
                    "certification_status": 1,
                },
            ),
        )

    assert projector.drain_counts()["ignored"] == 2


def test_midstream_course_child_events_ignore_missing_lesson() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TeacherSourceWideRecord.__table__.create(engine)
    LessonSourceWideRecord.__table__.create(engine)
    DtsSourceRowRecord.__table__.create(engine)
    projector = _direct_projector()
    student_token = "dom:v1:" + "c" * 64
    events = (
        ("dom_teacher_absent_reason", {"id": 1, "appoint_id": 999}),
        ("dom_teacher_penalty", {"id": 2, "appoint_id": 999}),
        ("dom_user_teacher_grading", {"id": 3, "appoint_id": 999}),
        (
            "dom_grading_label_log",
            {
                "id": 4,
                "appoint_id": 999,
                "label_name": "标签",
                "type": 1,
                "status": "normal",
            },
        ),
        ("dom_qa_task_close_camera_record", {"id": 5, "appoint_id": 999}),
        ("dom_qa_task_fake_early_leave_record", {"id": 6, "appoint_id": 999}),
        (
            "dom_qa_ac_classroom_record",
            {"id": 7, "info": {"cpu": [{"appoint_id": 999}]}},
        ),
        (
            "dom_teacher_favorite",
            {
                "id": 8,
                "tea_id": 123,
                "student_token": student_token,
                "add_time": "2026-08-20 00:00:00",
            },
        ),
        (
            "dom_teacher_blacklist",
            {
                "id": 9,
                "teacher_id": 123,
                "student_token": student_token,
                "valid_start_time": "2026-08-20 00:00:00",
                "is_valid_forever": 1,
            },
        ),
        (
            "dom_complaint",
            {
                "id": 10,
                "appoint_id": 999,
                "complaint_type": 13,
                "approve": "y",
                "validity": 1,
            },
        ),
    )
    with engine.begin() as connection:
        for offset, (table_name, after) in enumerate(events, start=30):
            projector.apply(
                connection,
                _child_event(
                    table_name=table_name,
                    offset=offset,
                    after=after,
                ),
            )

    assert projector.drain_counts()["ignored"] == len(events)


def test_direct_projection_whitelists_and_reconstructs_sparse_update() -> None:
    event = _child_event(
        table_name="dom_appoint",
        offset=2,
        operation="UPDATE",
        before={
            "id": 7,
            "t_id": 123,
            "status": "on",
            "student_token": "dom:v1:" + "a" * 64,
            "password": "must-not-cross-boundary",
        },
        after={
            "id": 7,
            "status": "end",
            "unrelated_payload": "drop-me",
        },
    )

    projected = direct_projection_event(event)

    assert projected.before == {
        "id": 7,
        "t_id": 123,
        "status": "on",
        "student_token": "dom:v1:" + "a" * 64,
    }
    assert projected.after == {
        "id": 7,
        "t_id": 123,
        "status": "end",
        "student_token": "dom:v1:" + "a" * 64,
    }


def test_direct_projection_rejects_primary_key_change() -> None:
    event = _child_event(
        table_name="dom_appoint",
        offset=2,
        operation="UPDATE",
        before={"id": 7},
        after={"id": 8},
    )

    with pytest.raises(
        DtsRecordError,
        match="DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED",
    ):
        direct_projection_event(event)


def test_before_course_is_cleared_before_after_course_is_applied() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table),
            [
                {
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "未开摄像头": True,
                },
                {
                    "source_region": "dom",
                    "课程id": "2",
                    "老师id": "123",
                    "未开摄像头": False,
                },
            ],
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_qa_task_close_camera_record",
                offset=3,
                operation="UPDATE",
                before={"id": 9, "appoint_id": 1},
                after={"id": 9, "appoint_id": 2},
            ),
        )
        rows = {
            row["课程id"]: row["未开摄像头"]
            for row in connection.execute(select(lesson_table)).mappings()
        }

    assert rows == {"1": False, "2": True}


def test_direct_batch_applies_one_teacher_delta_per_message() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                total_booked_cnt=2,
                feedback_total_eval_cnt=0,
                feedback_praise_cnt=0,
                feedback_negative_cnt=0,
            )
        )
        connection.execute(
            insert(lesson_table),
            [
                {
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "课程状态": "end",
                },
                {
                    "source_region": "dom",
                    "课程id": "2",
                    "老师id": "123",
                    "课程状态": "end",
                },
            ],
        )
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_user_teacher_grading",
                    offset=4,
                        after={
                            "id": 10,
                            "appoint_id": 1,
                            "use_point": "buy",
                            "score": 1,
                        "type": "unsatisfactory",
                    },
                ),
                _child_event(
                    table_name="dom_user_teacher_grading",
                    offset=5,
                        after={
                            "id": 11,
                            "appoint_id": 2,
                            "use_point": "free",
                            "score": 5,
                        "type": "satisfactory",
                    },
                ),
            ),
        )
        teacher = connection.execute(select(teacher_table)).mappings().one()

    assert teacher["feedback_total_eval_cnt"] == 2
    assert teacher["feedback_negative_cnt"] == 1
    assert teacher["feedback_praise_cnt"] == 1
    assert projector.drain_counts()["teacher_delta_updates"] == 2


def test_direct_pre_end_feedback_and_complaint_are_facts_but_not_teacher_totals() -> None:
    row = {
        "课程id": "1",
        "老师id": "123",
        "课程状态": "on",
        "好评标签": True,
        "投诉一级分类": "投诉",
    }

    contribution = DtsDirectWideProjector._lesson_contribution(row)

    assert contribution["total_booked_cnt"] == 1
    assert contribution["feedback_total_eval_cnt"] == 0
    assert contribution["feedback_praise_cnt"] == 0
    assert contribution["feedback_complaint_cnt"] == 0

    completed = DtsDirectWideProjector._lesson_contribution(
        {**row, "课程状态": "end"}
    )
    assert completed["feedback_total_eval_cnt"] == 1
    assert completed["feedback_praise_cnt"] == 1
    assert completed["feedback_complaint_cnt"] == 1


def test_direct_absence_uses_only_reason_type_and_no_notification_count() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(tchr_id="123", no_notice_cnt=0)
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "课程状态": "t_absent",
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_absent_reason",
                offset=91,
                after={
                    "id": 1,
                    "appoint_id": 1,
                    "t_id": 123,
                    "reason_type": "No Notification",
                    "reason_desc": "must not be read",
                },
            ),
        )
        no_notice_lesson = connection.execute(
            select(lesson_table)
        ).mappings().one()
        no_notice_teacher = connection.execute(
            select(teacher_table)
        ).mappings().one()
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_absent_reason",
                offset=92,
                operation="UPDATE",
                before={
                    "id": 1,
                    "appoint_id": 1,
                    "t_id": 123,
                    "reason_type": "No Notification",
                },
                after={
                    "id": 1,
                    "appoint_id": 1,
                    "t_id": 123,
                    "reason_type": "Unfilled Lesson Memo",
                },
            ),
        )
        memo_lesson = connection.execute(select(lesson_table)).mappings().one()
        memo_teacher = connection.execute(select(teacher_table)).mappings().one()

    assert no_notice_lesson["缺席原因明细"] == "No Notification"
    assert no_notice_teacher["no_notice_cnt"] == 1
    assert memo_lesson["缺席原因明细"] == "Unfilled Lesson Memo"
    assert memo_teacher["no_notice_cnt"] == 0


def test_substituted_teacher_absence_and_penalty_do_not_leak_to_current_owner() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    appoint_a = {
        "id": 901,
        "t_id": 123,
        "student_token": "dom:v1:" + "e" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "start_time": "2026-08-19 18:00:00",
        "end_time": "2026-08-19 18:30:00",
        "status": "on",
        "week": 3,
    }
    appoint_b = {**appoint_a, "t_id": 456}

    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table),
            [
                {
                    "tchr_id": "123",
                    "teach_area_type": "dom",
                    "onboard_date": date(2026, 8, 19),
                    "onboard_30d_end_date": date(2026, 9, 17),
                },
                {
                    "tchr_id": "456",
                    "teach_area_type": "dom",
                    "onboard_date": date(2026, 8, 19),
                    "onboard_30d_end_date": date(2026, 9, 17),
                },
            ],
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=201,
                after=appoint_a,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_absent_reason",
                offset=202,
                after={
                    "id": 1,
                    "appoint_id": 901,
                    "t_id": 123,
                    "reason_type": "Unfilled Lesson Memo",
                },
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_penalty",
                offset=203,
                after={
                    "id": 2,
                    "appoint_id": 901,
                    "t_id": 123,
                    "lesson_start_time": "2026-08-19 18:00:00",
                    "in_time": "2026-08-19 18:01:00",
                    "out_time": "2026-08-19 18:29:00",
                    "appeal_status": 1,
                },
            ),
        )
        connection.execute(
            lesson_table.update()
            .where(lesson_table.c["课程id"] == "901")
            .values(**{"是否拉黑": True, "收藏": True})
        )
        connection.execute(
            teacher_table.update()
            .where(teacher_table.c.tchr_id == "123")
            .values(feedback_favorite_cnt=1, feedback_block_cnt=1)
        )
        before_substitution = connection.execute(
            select(lesson_table)
        ).mappings().one()
        assert before_substitution["缺席原因明细"] == "Unfilled Lesson Memo"
        assert before_substitution["迟到"] is True
        assert before_substitution["早退"] is True
        assert before_substitution["是否拉黑"] is True
        assert before_substitution["收藏"] is True
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=204,
                operation="UPDATE",
                before=appoint_a,
                after=appoint_b,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_absent_reason",
                offset=205,
                after={
                    "id": 3,
                    "appoint_id": 901,
                    "t_id": 123,
                    "reason_type": "No Notification",
                },
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_penalty",
                offset=206,
                after={
                    "id": 4,
                    "appoint_id": 901,
                    "t_id": 123,
                    "lesson_start_time": "2026-08-19 18:00:00",
                    "in_time": "2026-08-19 18:01:00",
                    "out_time": "2026-08-19 18:29:00",
                    "appeal_status": 1,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["老师id"] == "456"
    assert lesson["缺席原因明细"] is None
    assert lesson["迟到"] is None
    assert lesson["早退"] is None
    assert lesson["是否拉黑"] is False
    assert lesson["收藏"] is False
    decisions = evaluate_lesson(dict(lesson), complaint_rules={})
    assert not any(
        decision.task_code in {"P-REL-MEMO", "P-REL-ATTENDANCE"}
        for decision in decisions
    )


def test_first_end_teacher_stays_frozen_after_later_appoint_teacher_change() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    appoint_a = {
        "id": 902,
        "t_id": 123,
        "student_token": "dom:v1:" + "f" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "start_time": "2026-08-19 18:00:00",
        "end_time": "2026-08-19 18:30:00",
        "status": "on",
        "week": 3,
    }
    first_end = {**appoint_a, "t_id": 456, "status": "end"}
    later_change = {**first_end, "t_id": 789, "status": "on"}

    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table),
            [
                {"tchr_id": teacher_id, "teach_area_type": "dom"}
                for teacher_id in ("123", "456", "789")
            ],
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=211,
                after=appoint_a,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=212,
                operation="UPDATE",
                before=appoint_a,
                after=first_end,
            ),
        )
        at_first_end = connection.execute(select(lesson_table)).mappings().one()
        assert at_first_end["老师id"] == "456"
        assert at_first_end["课程状态"] == "end"

        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=213,
                operation="UPDATE",
                before=first_end,
                after=later_change,
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()
        teachers = {
            row["tchr_id"]: row
            for row in connection.execute(select(teacher_table)).mappings()
        }

    assert lesson["老师id"] == "456"
    assert lesson["课程状态"] == "end"
    assert teachers["123"]["total_completed_cnt"] == 0
    assert teachers["456"]["total_completed_cnt"] == 1
    assert teachers["789"]["total_completed_cnt"] in {None, 0}


def test_direct_batch_keeps_frozen_end_owner_for_following_child_events() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    first_end = {
        "id": 903,
        "t_id": 456,
        "student_token": "dom:v1:" + "a" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "start_time": "2026-08-19 18:00:00",
        "end_time": "2026-08-19 18:30:00",
        "status": "end",
        "week": 3,
    }
    later_change = {**first_end, "t_id": 789, "status": "on"}
    events = (
        _child_event(
            table_name="dom_appoint",
            offset=221,
            operation="UPDATE",
            before=first_end,
            after=later_change,
        ),
        _child_event(
            table_name="dom_teacher_absent_reason",
            offset=222,
            after={
                "id": 1,
                "appoint_id": 903,
                "t_id": 789,
                "reason_type": "No Notification",
            },
        ),
        _child_event(
            table_name="dom_teacher_absent_reason",
            offset=223,
            after={
                "id": 2,
                "appoint_id": 903,
                "t_id": 456,
                "reason_type": "Unfilled Lesson Memo",
            },
        ),
    )

    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table),
            [
                {"tchr_id": "456", "teach_area_type": "dom"},
                {"tchr_id": "789", "teach_area_type": "dom"},
            ],
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "903",
                    "老师id": "456",
                    "课程状态": "end",
                    "上课日期": date(2026, 8, 19),
                    "上课时间": time(18, 0),
                }
            )
        )
        projector.apply_batch(connection, events)
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["老师id"] == "456"
    assert lesson["课程状态"] == "end"
    assert lesson["缺席原因明细"] == "Unfilled Lesson Memo"


def test_direct_dom_grading_uses_use_point_and_ignores_grading_status() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table),
            [
                {"source_region": "dom", "课程id": "1", "老师id": "123"},
                {"source_region": "dom", "课程id": "2", "老师id": "123"},
                {"source_region": "dom", "课程id": "3", "老师id": "123"},
            ],
        )
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_user_teacher_grading",
                    offset=93,
                    after={
                        "id": 1,
                        "appoint_id": 1,
                        "use_point": "buy",
                        "score": 1,
                        "type": "satisfactory",
                        "status": 99,
                    },
                ),
                _child_event(
                    table_name="dom_user_teacher_grading",
                    offset=94,
                    after={
                        "id": 2,
                        "appoint_id": 2,
                        "use_point": "free",
                        "score": 5,
                        "type": "unsatisfactory",
                        "status": 99,
                    },
                ),
                _child_event(
                    table_name="dom_user_teacher_grading",
                    offset=95,
                    after={
                        "id": 3,
                        "appoint_id": 3,
                        "use_point": "unknown",
                        "score": 1,
                        "type": "satisfactory",
                    },
                ),
            ),
        )
        lessons = {
            row["课程id"]: row
            for row in connection.execute(select(lesson_table)).mappings()
        }

    assert lessons["1"]["差评分"] == 1
    assert lessons["1"]["差评标签"] is True
    assert lessons["1"]["好评标签"] is False
    assert lessons["2"]["差评分"] is None
    assert lessons["2"]["差评标签"] is True
    assert lessons["2"]["好评标签"] is False
    assert lessons["3"]["差评标签"] is None
    assert lessons["3"]["好评标签"] is None


def test_direct_ovs_grading_stays_unknown_even_when_legacy_row_is_active() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "ovs",
                    "课程id": "1",
                    "老师id": "123",
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                source_region="ovs",
                table_name="ovs_user_teacher_grading",
                offset=96,
                after={
                    "id": 1,
                    "appoint_id": 1,
                    "score": 1,
                    "type": "satisfactory",
                    "status": 0,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["差评分"] is None
    assert lesson["差评标签"] is None
    assert lesson["好评标签"] is None


def test_direct_dom_label_ignores_type_status_and_ovs_stays_unknown() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table),
            [
                {"source_region": "dom", "课程id": "1", "老师id": "123"},
                {"source_region": "ovs", "课程id": "2", "老师id": "123"},
            ],
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_grading_label_log",
                offset=97,
                after={
                    "id": 1,
                    "appoint_id": 1,
                    "label_id": 9,
                    "label_name": "DOM 标签",
                    "type": 9,
                    "status": "deleted",
                },
            ),
        )
        projector.apply(
            connection,
            _child_event(
                source_region="ovs",
                table_name="ovs_grading_label_log",
                offset=98,
                after={
                    "id": 2,
                    "appoint_id": 2,
                    "label_id": 9,
                    "label_name": "OVS 标签",
                    "type": 1,
                    "status": "normal",
                },
            ),
        )
        lessons = {
            row["课程id"]: row
            for row in connection.execute(select(lesson_table)).mappings()
        }

    assert lessons["1"]["评价详情"] == "DOM 标签"
    assert lessons["2"]["评价详情"] is None


def test_direct_label_dictionary_change_never_rewrites_log_name() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    lesson_table = LessonSourceWideRecord.__table__
    TeacherSourceWideRecord.__table__.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "评价详情": "日志原名",
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_grading_label",
                offset=99,
                operation="UPDATE",
                before={"id": 9, "label_name": "日志原名"},
                after={"id": 9, "label_name": "字典新名"},
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["评价详情"] == "日志原名"


def test_direct_appoint_keeps_retired_qa_hardware_null_without_fake_early_column() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "cpu占用过高": None,
                    "网络延迟过高": None,
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=99,
                operation="UPDATE",
                before={"id": 1, "t_id": 123, "status": "on"},
                after={
                    "id": 1,
                    "t_id": 123,
                    "date": "2026-08-19",
                    "status": "on",
                },
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_qa_ac_classroom_record",
                offset=100,
                after={"id": 1, "info": {"cpu": [{"appoint_id": 1}]}},
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["cpu占用过高"] is None
    assert lesson["网络延迟过高"] is None
    assert "假早退" not in lesson_table.c


def test_direct_malformed_nonnull_complaint_grandson_is_not_valid() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    category_table = DtsSourceRowRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    category_table.create(engine)
    projector = _direct_projector()
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                feedback_complaint_cnt=1,
                feedback_valid_complaint_cnt=1,
            )
        )
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "1",
                    "老师id": "123",
                    "投诉一级分类": "旧投诉",
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_complaint",
                offset=101,
                after={
                    "id": 1,
                    "appoint_id": 1,
                    "complaint_type": 13,
                    "complaint_type_grandson": "not-an-id",
                    "approve": "y",
                    "validity": 1,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["投诉一级分类"] is None


def test_direct_batch_prefilters_missing_course_targets_with_one_query() -> None:
    sequential_engine = create_engine("sqlite+pysqlite:///:memory:")
    batch_engine = create_engine("sqlite+pysqlite:///:memory:")
    LessonSourceWideRecord.__table__.create(sequential_engine)
    LessonSourceWideRecord.__table__.create(batch_engine)
    events = tuple(
        _child_event(
            table_name="dom_qa_task_close_camera_record",
            offset=offset,
            after={"id": offset, "appoint_id": 999},
        )
        for offset in range(100, 200)
    )

    sequential_statements: list[str] = []
    batch_statements: list[str] = []
    sqlalchemy_event.listen(
        sequential_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: sequential_statements.append(
            statement
        ),
    )
    sqlalchemy_event.listen(
        batch_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: batch_statements.append(
            statement
        ),
    )

    sequential = _direct_projector()
    with sequential_engine.begin() as connection:
        for event in events:
            sequential.apply(connection, event)
    batched = _direct_projector()
    with batch_engine.begin() as connection:
        batched.apply_batch(connection, events)

    assert len(sequential_statements) == 100
    assert len(batch_statements) == 1
    counts = batched.drain_counts()
    assert counts["events"] == 100
    assert counts["ignored"] == 100
    assert counts["batch_prefiltered"] == 100
    assert counts["batch_target_queries"] == 1


def test_direct_batch_prefilters_relationships_without_matching_lesson() -> None:
    sequential_engine = create_engine("sqlite+pysqlite:///:memory:")
    batch_engine = create_engine("sqlite+pysqlite:///:memory:")
    for engine in (sequential_engine, batch_engine):
        TeacherSourceWideRecord.__table__.create(engine)
        LessonSourceWideRecord.__table__.create(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(TeacherSourceWideRecord.__table__).values(tchr_id="123")
            )
    student_token = "dom:v1:" + "e" * 64
    events = tuple(
        _child_event(
            table_name="dom_teacher_favorite",
            offset=offset,
            after={
                "id": offset,
                "tea_id": 123,
                "student_token": student_token,
                "add_time": "2026-08-20 00:00:00",
            },
        )
        for offset in range(300, 400)
    )
    sequential_statements: list[str] = []
    batch_statements: list[str] = []
    sqlalchemy_event.listen(
        sequential_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: sequential_statements.append(
            statement
        ),
    )
    sqlalchemy_event.listen(
        batch_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: batch_statements.append(
            statement
        ),
    )

    sequential = _direct_projector()
    with sequential_engine.begin() as connection:
        for event in events:
            sequential.apply(connection, event)
    batched = _direct_projector()
    with batch_engine.begin() as connection:
        batched.apply_batch(connection, events)

    assert len(sequential_statements) == 100
    assert len(batch_statements) == 2
    counts = batched.drain_counts()
    assert counts["events"] == 100
    assert counts["ignored"] == 100
    assert counts["batch_prefiltered"] == 100
    assert counts["batch_target_queries"] == 2
    assert counts["ignored_by_suffix"] == {"teacher_favorite": 100}


def test_direct_batch_uses_cached_relationship_course() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    student_token = "dom:v1:" + "f" * 64
    projector = _direct_projector()

    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "99",
                    "老师id": "123",
                    "学员id": student_token,
                    "上课日期": date(2026, 8, 19),
                    "上课时间": time(18, 0),
                    "课程状态": "end",
                    "收藏": False,
                    "是否拉黑": False,
                }
            )
        )
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_teacher_favorite",
                    offset=400,
                    after={
                        "id": 1,
                        "tea_id": 123,
                        "student_token": student_token,
                        "add_time": "2026-08-20 00:00:00",
                    },
                ),
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["收藏"] is True
    counts = projector.drain_counts()
    assert counts["relationship_cache_hits"] == 1
    assert counts["relationship_fallback_queries"] == 0
    assert counts["batch_target_queries"] == 2


def test_direct_batch_falls_back_after_same_batch_lesson_insert() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    student_token = "dom:v1:" + "a" * 64
    projector = _direct_projector()

    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_appoint",
                    offset=410,
                    after={
                        "id": 99,
                        "t_id": 123,
                        "student_token": student_token,
                        "date": "2026-08-19",
                        "time": "18:00:00",
                        "status": "end",
                        "use_point": "buy",
                    },
                ),
                _child_event(
                    table_name="dom_teacher_favorite",
                    offset=411,
                    after={
                        "id": 1,
                        "tea_id": 123,
                        "student_token": student_token,
                        "add_time": "2026-08-20 00:00:00",
                    },
                ),
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["收藏"] is True
    counts = projector.drain_counts()
    assert counts["relationship_cache_hits"] == 0
    assert counts["relationship_fallback_queries"] == 1


def test_direct_batch_keeps_same_batch_teacher_course_dependency_order() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    teacher = {
        "id": 123,
        "status_on_time": "2026-08-19 00:00:00",
        "course": "h5_tc",
    }
    appoint = {
        "id": 99,
        "t_id": 123,
        "student_token": "dom:v1:" + "d" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "status": "end",
        "use_point": "buy",
    }

    with engine.begin() as connection:
        projector.apply_batch(
            connection,
            (
                _child_event(
                    table_name="dom_teacher",
                    offset=200,
                    operation="UPDATE",
                    before={"id": 123, "status": "off"},
                    after=teacher,
                ),
                _child_event(
                    table_name="dom_appoint",
                    offset=201,
                    operation="UPDATE",
                    before={"id": 99, "status": "on"},
                    after=appoint,
                ),
                _child_event(
                    table_name="dom_qa_task_close_camera_record",
                    offset=202,
                    after={"id": 1, "appoint_id": 99},
                ),
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()
        stored_teacher = connection.execute(select(teacher_table)).mappings().one()

    assert lesson["未开摄像头"] is True
    assert stored_teacher["total_booked_cnt"] == 1
    assert projector.drain_counts()["batch_prefiltered"] == 0


class _RecordingProjector:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def apply(self, _connection: object, event: DtsChangeEvent) -> None:
        self.offsets.append(event.offset)


def _direct_sink_for_checkpoint_test(
    *,
    next_offset: int | None,
) -> tuple[PostgresDtsEventSink, _RecordingProjector, list[int]]:
    sink = object.__new__(PostgresDtsEventSink)
    projector = _RecordingProjector()
    written_offsets: list[int] = []
    sink._direct_projector = projector
    sink._lock_direct_stream_checkpoint = (
        lambda _connection, _event: next_offset
    )
    sink._write_checkpoint = (
        lambda _connection, event: written_offsets.append(event.offset)
    )
    return sink, projector, written_offsets


def test_direct_checkpoint_replay_does_not_project_again() -> None:
    sink, projector, written_offsets = _direct_sink_for_checkpoint_test(
        next_offset=2
    )

    duplicate = sink._apply_direct_transaction(object(), _schedule_event())

    assert duplicate is True
    assert projector.offsets == []
    assert written_offsets == []


def test_direct_postgres_write_sets_transaction_local_source_region() -> None:
    class _Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        def execute(self, statement, parameters):
            self.calls.append((str(statement), dict(parameters)))

    connection = _Connection()

    PostgresDtsEventSink._set_direct_source_region(connection, "dom")

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "pg_catalog.set_config" in statement
    assert "tit.dts_source_region" in statement
    assert parameters == {"source_region": "dom"}


def test_direct_postgres_lock_checkpoint_and_source_region_share_one_query() -> None:
    class _Result:
        def scalar_one_or_none(self) -> int:
            return 42

    class _Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def execute(self, statement, parameters):
            self.calls.append((str(statement), dict(parameters)))
            return _Result()

    event = _schedule_event()
    sink = object.__new__(PostgresDtsEventSink)
    connection = _Connection()

    checkpoint = sink._lock_direct_stream_checkpoint(connection, event)

    assert checkpoint == 42
    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "pg_advisory_xact_lock" in statement
    assert "pg_catalog.set_config" in statement
    assert "FOR UPDATE" in statement
    assert parameters == {
        "stream_identity": '["dom","dom-topic",0]',
        "source_region": "dom",
        "topic": "dom-topic",
        "partition_id": 0,
    }


def test_direct_ignored_event_still_advances_checkpoint() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TeacherSourceWideRecord.__table__.create(engine)
    sink = object.__new__(PostgresDtsEventSink)
    projector = _direct_projector()
    written_offsets: list[int] = []
    sink._direct_projector = projector
    sink._lock_direct_stream_checkpoint = lambda _connection, _event: None
    sink._write_checkpoint = (
        lambda _connection, event: written_offsets.append(event.offset)
    )

    with engine.begin() as connection:
        duplicate = sink._apply_direct_transaction(
            connection,
            _schedule_event(),
        )

    assert duplicate is False
    assert projector.drain_counts()["ignored"] == 1
    assert written_offsets == [1]


def test_direct_batch_rejects_offset_gap_before_advancing_checkpoint() -> None:
    sink, projector, written_offsets = _direct_sink_for_checkpoint_test(
        next_offset=1
    )
    first = _schedule_event()
    gap = DtsChangeEvent(**{**first.__dict__, "offset": 3})

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_DATABASE_OFFSET_NOT_CONTIGUOUS",
    ):
        sink._apply_direct_batch_transaction(object(), (first, gap))

    assert projector.offsets == []
    assert written_offsets == []


def test_direct_batch_projects_once_and_checkpoints_last_offset() -> None:
    sink, projector, written_offsets = _direct_sink_for_checkpoint_test(
        next_offset=1
    )
    batches: list[list[int]] = []

    def apply_batch(
        _connection: object,
        events: tuple[DtsChangeEvent, ...],
    ) -> None:
        batches.append([event.offset for event in events])

    projector.apply_batch = apply_batch  # type: ignore[attr-defined]
    first = _schedule_event()
    second = DtsChangeEvent(**{**first.__dict__, "offset": 2})

    duplicate_flags = sink._apply_direct_batch_transaction(
        object(),
        (first, second),
    )

    assert duplicate_flags == (False, False)
    assert batches == [[1, 2]]
    assert projector.offsets == []
    assert written_offsets == [2]


def test_direct_null_penalty_evidence_is_not_counted_as_perfect() -> None:
    base = {
        "课程状态": "end",
        "迟到": None,
        "早退": False,
        "课程id": "9001",
    }

    assert DtsDirectWideProjector._lesson_contribution(base)["perfect_cnt"] == 0
    assert DtsDirectWideProjector._lesson_contribution(
        {**base, "迟到": False}
    )["perfect_cnt"] == 1


def test_direct_null_penalty_appeal_projects_unknown_flags() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()

    with engine.begin() as connection:
        connection.execute(insert(teacher_table).values(tchr_id="123"))
        connection.execute(
            insert(lesson_table).values(
                **{
                    "source_region": "dom",
                    "课程id": "9002",
                    "老师id": "123",
                    "课程状态": "on",
                    "迟到": False,
                    "早退": False,
                }
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_penalty",
                offset=500,
                after={
                    "id": 1,
                    "appoint_id": 9002,
                    "t_id": 123,
                    "lesson_start_time": "2026-08-19 18:00:00",
                    "in_time": "2026-08-19 18:01:00",
                    "out_time": "2026-08-19 18:29:00",
                    "appeal_status": None,
                },
            ),
        )
        lesson = connection.execute(select(lesson_table)).mappings().one()

    assert lesson["迟到"] is None
    assert lesson["早退"] is None


def test_direct_late_early_rate_deduplicates_one_course() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    teacher_table = TeacherSourceWideRecord.__table__
    lesson_table = LessonSourceWideRecord.__table__
    teacher_table.create(engine)
    lesson_table.create(engine)
    projector = _direct_projector()
    appoint = {
        "id": 901,
        "t_id": 123,
        "student_token": "dom:v1:" + "d" * 64,
        "date": "2026-08-19",
        "time": "18:00:00",
        "start_time": "2026-08-19 18:00:00",
        "end_time": "2026-08-19 18:30:00",
        "status": "end",
    }
    with engine.begin() as connection:
        connection.execute(
            insert(teacher_table).values(
                tchr_id="123",
                teach_area_type="dom",
                onboard_date=date(2026, 8, 19),
                onboard_30d_end_date=date(2026, 9, 17),
            )
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_appoint",
                offset=501,
                after=appoint,
            ),
        )
        projector.apply(
            connection,
            _child_event(
                table_name="dom_teacher_penalty",
                offset=502,
                after={
                    "id": 1,
                    "appoint_id": 901,
                    "t_id": 123,
                    "lesson_start_time": "2026-08-19 18:00:00",
                    "in_time": "2026-08-19 18:01:00",
                    "out_time": "2026-08-19 18:29:00",
                    "appeal_status": 1,
                },
            ),
        )
        teacher = connection.execute(select(teacher_table)).mappings().one()

    assert teacher["late_cnt"] == 1
    assert teacher["early_cnt"] == 1
    assert teacher["reliability_late_early_rate"] == 1.0
