from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, insert, select

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
    operation: str = "INSERT",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region="dom",
        topic="dom-topic",
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

    assert schedule_slot_is_peak("dmo", weekday, 37) is True
    assert schedule_slot_is_peak("dmo", weekday, 44) is True
    assert schedule_slot_is_peak("dmo", weekday, 19) is False
    assert schedule_slot_is_peak("dmo", weekend, 19) is True
    assert schedule_slot_is_peak("dmo", weekend, 24) is True
    assert schedule_slot_is_peak("dmo", weekend, 25) is False


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
                teach_area_type="dmo",
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
                teach_area_type="dmo",
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
    assert inserted_teacher["perfect_cnt"] == 1
    assert inserted_teacher["first_completed_student_cnt"] == 1
    assert deleted_teacher["total_booked_cnt"] == 0
    assert deleted_teacher["total_completed_cnt"] == 0
    assert deleted_teacher["perfect_cnt"] == 0
    assert deleted_teacher["first_completed_student_cnt"] == 0
    assert deleted_teacher["first_booked_dt"] is None
    assert deleted_teacher["first_completed_dt"] is None
    assert remaining_lessons == []


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
                    "课程id": "1",
                    "老师id": "123",
                    "学员id": "student-a",
                    "课程状态": "end",
                    "收藏": False,
                },
                {
                    "课程id": "2",
                    "老师id": "123",
                    "学员id": "student-a",
                    "课程状态": "end",
                    "收藏": False,
                },
            ],
        )
        projector._update_lesson(connection, "1", {"收藏": True})
        first = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "2", {"收藏": True})
        second = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "1", {"收藏": False})
        third = connection.execute(select(teacher_table)).mappings().one()
        projector._update_lesson(connection, "2", {"收藏": False})
        fourth = connection.execute(select(teacher_table)).mappings().one()

    assert first["feedback_favorite_cnt"] == 1
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
        "certification_type": "tesol",
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
    assert deleted["is_cpl_tesol"] is False


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
                **{"课程id": "1", "老师id": "123"}
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


def test_midstream_teacher_update_and_delete_do_not_create_missing_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = TeacherSourceWideRecord.__table__
    table.create(engine)
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

    assert rows == []
    assert projector.drain_counts()["ignored"] == 2


def test_midstream_appoint_update_and_delete_do_not_create_missing_row() -> None:
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
                teach_area_type="dmo",
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

    assert lessons == []
    assert teacher["total_booked_cnt"] == 0
    assert projector.drain_counts()["ignored"] == 2


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
                {"课程id": "1", "老师id": "123", "未开摄像头": True},
                {"课程id": "2", "老师id": "123", "未开摄像头": False},
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
                {"课程id": "1", "老师id": "123"},
                {"课程id": "2", "老师id": "123"},
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
    sink._lock_stream_checkpoint = lambda _connection, _event: next_offset
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


def test_direct_ignored_event_still_advances_checkpoint() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TeacherSourceWideRecord.__table__.create(engine)
    sink = object.__new__(PostgresDtsEventSink)
    projector = _direct_projector()
    written_offsets: list[int] = []
    sink._direct_projector = projector
    sink._lock_stream_checkpoint = lambda _connection, _event: None
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
