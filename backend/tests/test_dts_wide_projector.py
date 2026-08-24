from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app import dts_wide_projector as projector_module
from app.db_models import TeacherSourceWideRecord
from app.dts_wide_projector import (
    DtsWideProjectionError,
    DtsWideProjectionSettings,
    DtsWideProjector,
    _ProjectionCounts,
)


def test_open_ended_new_teacher_cohort_starts_on_august_13() -> None:
    settings = DtsWideProjectionSettings.from_env({})

    assert settings.cohort_start == date(2026, 8, 13)
    assert settings.cohort_end_exclusive is None
    assert settings.retry_max_attempts == 8
    settings.require_subscription_boundary(1786550400)
    settings.require_subscription_boundary(1786523400)

    with pytest.raises(
        DtsWideProjectionError,
        match="DTS_START_AT_AFTER_COHORT_START",
    ):
        settings.require_subscription_boundary(1786550401)


@pytest.mark.parametrize("value", ["0", "101", "not-an-integer"])
def test_projection_retry_limit_is_bounded_and_validated(value: str) -> None:
    with pytest.raises(
        DtsWideProjectionError,
        match="TIT_DTS_PROJECTION_MAX_ATTEMPTS_INVALID",
    ):
        DtsWideProjectionSettings.from_env(
            {"TIT_DTS_PROJECTION_MAX_ATTEMPTS": value}
        )

    assert DtsWideProjectionSettings.from_env(
        {"TIT_DTS_PROJECTION_MAX_ATTEMPTS": "7"}
    ).retry_max_attempts == 7


def test_optional_cohort_end_is_exclusive_and_validated() -> None:
    settings = DtsWideProjectionSettings.from_env(
        {
            "TIT_DTS_COHORT_START": "2026-08-13",
            "TIT_DTS_COHORT_END_EXCLUSIVE": "2026-09-01",
        }
    )
    assert settings.cohort_start == date(2026, 8, 13)
    assert settings.cohort_end_exclusive == date(2026, 9, 1)

    with pytest.raises(DtsWideProjectionError, match="DTS_COHORT_WINDOW_INVALID"):
        DtsWideProjectionSettings.from_env(
            {
                "TIT_DTS_COHORT_START": "2026-08-13",
                "TIT_DTS_COHORT_END_EXCLUSIVE": "2026-08-13",
            }
        )


def test_lesson_scope_requires_region_but_not_a_30_day_window() -> None:
    projector = DtsWideProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    teacher = {
        "status_on_time": "2026-08-13 00:00:00",
        "course": "global_cn,global_pool",
    }

    assert projector._lesson_matches_teacher_region("ovs", teacher)
    assert projector._lesson_matches_teacher_region(
        "ovs",
        {**teacher, "status_on_time": "2020-01-01 00:00:00"},
    )
    assert not projector._lesson_matches_teacher_region("dom", teacher)
    assert not projector._lesson_matches_teacher_region(
        "ovs", {"course": None}
    )


def test_appoint_scope_does_not_filter_status_use_point_or_student() -> None:
    token = "dom:v1:" + "a" * 64

    assert DtsWideProjector._appoint_in_scope(
        {
            "use_point": "buy",
            "status": "end",
            "student_token": token,
        }
    )
    assert DtsWideProjector._appoint_in_scope(
        {"use_point": "buy", "status": "end"}
    )
    assert DtsWideProjector._appoint_in_scope(
        {"use_point": "free", "status": "on"}
    )
    assert DtsWideProjector._appoint_in_scope(
        {"use_point": None, "status": None}
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"is_valid_forever": 1, "valid_end_time": None}, True),
        ({"is_valid_forever": 0, "valid_end_time": "2999-01-01"}, True),
        ({"is_valid_forever": 0, "valid_end_time": None}, False),
        ({"is_valid_forever": None, "valid_end_time": "2026-08-22"}, False),
    ],
)
def test_blacklist_requires_permanent_flag_or_year_2999(
    row: dict[str, object],
    expected: bool,
) -> None:
    assert DtsWideProjector._active_blacklist(row) is expected


class _FirstResult:
    def __init__(self, first_value: object = None, *, rowcount: int = 0) -> None:
        self.first_value = first_value
        self.rowcount = rowcount

    def mappings(self) -> _FirstResult:
        return self

    def first(self) -> object:
        return self.first_value

    def scalar_one_or_none(self) -> object:
        return self.first_value

    def scalar_one(self) -> object:
        return self.first_value


class _ReadOnlyConnection:
    def __init__(
        self,
        first_values: list[object] | None = None,
        rowcounts: list[int] | None = None,
    ) -> None:
        self.execute_count = 0
        self.first_values = iter(first_values or [])
        self.rowcounts = iter(rowcounts or [])
        self.statements: list[object] = []
        self.parameters: list[object] = []

    def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> _FirstResult:
        self.execute_count += 1
        self.statements.append(statement)
        self.parameters.append(parameters)
        return _FirstResult(
            next(self.first_values, None),
            rowcount=next(self.rowcounts, 0),
        )


def test_dirty_key_claim_uses_independent_v1_compat_state_machine() -> None:
    expected = {
        "source_region": "ovs",
        "key_type": "COURSE",
        "key_part_1": "123",
        "key_part_2": "",
    }
    connection = _ReadOnlyConnection(first_values=[expected])
    projector = DtsWideProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )

    claimed = projector._lock_next_dirty_key(connection)

    assert claimed == expected
    assert connection.execute_count == 1
    sql = " ".join(str(connection.statements[0]).split())
    assert "claim_v1_compat_dirty_key_v1(:worker_id)" in sql
    assert connection.parameters[0] == {"worker_id": "test"}
    assert "FROM public.dts_dirty_keys" not in sql


class _BeginContext:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def __enter__(self) -> object:
        return self.connection

    def __exit__(self, *_args: object) -> bool:
        return False


class _BatchConnection:
    def __init__(self) -> None:
        self.transaction_count = 0

    def begin(self) -> _BeginContext:
        self.transaction_count += 1
        return _BeginContext(self)


class _BatchEngine:
    def __init__(self) -> None:
        self.connection = _BatchConnection()
        self.connect_calls = 0

    def connect(self) -> _BeginContext:
        self.connect_calls += 1
        return _BeginContext(self.connection)

    def begin(self) -> _BeginContext:
        return _BeginContext(self.connection)


def test_upsert_uses_returning_when_insert_rowcount_is_unknown() -> None:
    connection = _ReadOnlyConnection(
        first_values=["teacher-1"],
        rowcounts=[-1],
    )

    changed = DtsWideProjector._upsert(
        connection,
        TeacherSourceWideRecord.__table__,
        {"tchr_id": "teacher-1", "status": "active"},
        primary_keys=("tchr_id",),
    )

    assert changed is True
    sql = str(connection.statements[0].compile(dialect=postgresql.dialect()))
    assert "RETURNING teacher_source_wide.tchr_id" in sql


def test_upsert_without_returned_key_is_unchanged_despite_rowcount() -> None:
    connection = _ReadOnlyConnection(first_values=[None], rowcounts=[1])

    changed = DtsWideProjector._upsert(
        connection,
        TeacherSourceWideRecord.__table__,
        {"tchr_id": "teacher-1", "status": "active"},
        primary_keys=("tchr_id",),
    )

    assert changed is False


class _FailingBatchProjector(DtsWideProjector):
    def __init__(self, *, persisted_attempts: int, next_attempt: int) -> None:
        super().__init__(
            _BatchEngine(),
            worker_id="test",
            settings=DtsWideProjectionSettings(
                cohort_start=date(2026, 8, 13),
                retry_max_attempts=2,
            ),
        )
        self.persisted_attempts = persisted_attempts
        self.next_attempt = next_attempt
        self.lock_calls = 0
        self.dispatch_calls = 0
        self.retry_calls = 0
        self.retry_connection: object | None = None

    def _lock_next_dirty_key(self, _connection: object) -> object:
        self.lock_calls += 1
        if self.lock_calls > 1:
            return None
        return {
            "source_region": "ovs",
            "key_type": "COURSE",
            "key_part_1": "course-1",
            "key_part_2": "",
            "attempt_count": self.persisted_attempts,
        }

    def _dispatch(self, *_args: object) -> object:
        self.dispatch_calls += 1
        raise DtsWideProjectionError("DTS_TEACHER_DEPENDENCY_PENDING")

    def _mark_retry(self, *_args: object, **_kwargs: object) -> int:
        self.retry_calls += 1
        self.retry_connection = _kwargs.get("connection")
        return self.next_attempt


class _SuccessfulBatchProjector(DtsWideProjector):
    def __init__(self) -> None:
        super().__init__(
            _BatchEngine(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.lock_calls = 0

    def _lock_next_dirty_key(self, _connection: object) -> object:
        self.lock_calls += 1
        if self.lock_calls > 2:
            return None
        return {
            "source_region": "ovs",
            "key_type": "COURSE",
            "key_part_1": f"course-{self.lock_calls}",
            "key_part_2": "",
        }

    def _dispatch(self, *_args: object) -> object:
        return SimpleNamespace(
            lesson_upserts=0,
            lesson_deletes=0,
            teacher_upserts=0,
            teacher_deletes=0,
            unchanged=1,
        )

    def _mark_completed(self, *_args: object) -> None:
        return None


def test_projection_batch_reuses_one_checkout_across_key_transactions() -> None:
    projector = _SuccessfulBatchProjector()

    result = projector.run_batch(max_keys=10)

    assert result["dirty_keys"] == 2
    assert result["unchanged"] == 2
    assert projector.engine.connect_calls == 1
    assert projector.engine.connection.transaction_count == 3


def test_transient_projection_failure_retries_below_the_limit() -> None:
    projector = _FailingBatchProjector(persisted_attempts=0, next_attempt=1)

    result = projector.run_batch(max_keys=2)

    assert result["retries"] == 1
    assert result["quarantined"] == 0
    assert projector.dispatch_calls == 1
    assert projector.retry_calls == 1
    assert projector.retry_connection is projector.engine.connection


def test_projection_failure_is_quarantined_when_retry_limit_is_reached() -> None:
    projector = _FailingBatchProjector(persisted_attempts=1, next_attempt=2)

    result = projector.run_batch(max_keys=1)

    assert result["retries"] == 1
    assert result["quarantined"] == 1
    assert projector.dispatch_calls == 1
    assert projector.retry_calls == 1


def test_persisted_exhausted_key_gets_one_attempt_under_new_projection_rules() -> None:
    projector = _FailingBatchProjector(persisted_attempts=2, next_attempt=3)

    result = projector.run_batch(max_keys=1)

    assert result["retries"] == 1
    assert result["quarantined"] == 1
    assert projector.dispatch_calls == 1
    assert projector.retry_calls == 1


def test_projection_batch_stops_at_its_time_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projector = _FailingBatchProjector(persisted_attempts=0, next_attempt=1)
    times = iter((10.0, 11.0, 11.0))
    monkeypatch.setattr(projector_module, "monotonic", lambda: next(times))

    result = projector.run_batch(max_keys=1000, max_seconds=0.5)

    assert result["dirty_keys"] == 0
    assert result["budget_exhausted"] == 1
    assert result["elapsed_ms"] == 1000
    assert projector.lock_calls == 0


@pytest.mark.parametrize("max_seconds", [0.0, -1.0, float("inf")])
def test_projection_batch_time_budget_must_be_positive_and_finite(
    max_seconds: float,
) -> None:
    projector = _FailingBatchProjector(persisted_attempts=0, next_attempt=1)

    with pytest.raises(
        DtsWideProjectionError,
        match="^DTS_PROJECTOR_MAX_SECONDS_INVALID$",
    ):
        projector.run_batch(max_seconds=max_seconds)


class _RetryEngine:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def begin(self) -> _BeginContext:
        return _BeginContext(self.connection)


def test_exhausted_retry_is_delegated_to_compat_command() -> None:
    connection = _ReadOnlyConnection(
        first_values=[{"status": "DEAD", "attempt_count": 2}]
    )
    projector = DtsWideProjector(
        _RetryEngine(connection),
        worker_id="test",
        settings=DtsWideProjectionSettings(
            cohort_start=date(2026, 8, 13),
            retry_max_attempts=2,
        ),
    )

    attempt = projector._mark_retry(
        ("ovs", "COURSE", "course-1", ""),
        DtsWideProjectionError("DTS_APPOINT_DEPENDENCY_PENDING"),
    )

    assert attempt == 2
    sql = " ".join(str(connection.statements[0]).split())
    assert "fail_v1_compat_dirty_key_v1" in sql
    assert connection.parameters[0] == {
        "worker_id": "test",
        "source_region": "ovs",
        "key_type": "COURSE",
        "key_part_1": "course-1",
        "key_part_2": "",
        "error_code": "DTS_APPOINT_DEPENDENCY_PENDING",
        "max_attempts": 2,
        "retry_base_seconds": 10,
        "retry_max_seconds": 300,
    }


class _TeacherPendingProjector(DtsWideProjector):
    def _appoint_source(
        self, _connection: object, _source_region: str, _course_id: str
    ) -> object:
        return SimpleNamespace(
            region="ovs",
            row={
                "id": "lesson-1",
                "t_id": "teacher-1",
                "s_id": "student-1",
                "date": "2026-08-13",
                "start_time": "2026-08-13 18:00:00",
                "status": "end",
                "use_point": "buy",
            },
        )

    def _teacher_source(self, _connection: object, _teacher_id: str) -> None:
        return None


def test_course_retries_without_emitting_a_source_wide_row_until_teacher_is_known() -> None:
    projector = _TeacherPendingProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection()

    with pytest.raises(
        DtsWideProjectionError,
        match="DTS_TEACHER_DEPENDENCY_PENDING",
    ):
        projector._project_course(
            connection,
            "ovs",
            "lesson-1",
            {
                "last_source_region": "ovs",
                "last_topic": "ovs-topic",
                "last_partition": 0,
                "last_offset": 1,
            },
        )

    assert connection.execute_count == 2


class _MissingAppointProjector(DtsWideProjector):
    def __init__(self) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )

    def _appoint_source(
        self, _connection: object, _source_region: str, _course_id: str
    ) -> None:
        return None

    def _appoint_tombstoned(
        self,
        _connection: object,
        _source_region: str,
        _course_id: str,
    ) -> bool:
        return False


def test_missing_appoint_always_retries_even_for_pre_cohort_child_evidence() -> None:
    projector = _MissingAppointProjector()
    connection = _ReadOnlyConnection(first_values=[None])

    with pytest.raises(
        DtsWideProjectionError,
        match="^DTS_APPOINT_DEPENDENCY_PENDING$",
    ):
        projector._project_course(connection, "ovs", "course-1", {})


class _SourceRowCacheProjector(DtsWideProjector):
    def __init__(self) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.query_calls = 0

    def _query_active_source_rows(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> list[object]:
        self.query_calls += 1
        return [
            SimpleNamespace(
                region="ovs",
                table="ovs_appoint",
                row={"id": "course-1"},
            ),
            SimpleNamespace(
                region="ovs",
                table="ovs_qa_task_close_camera_record",
                row={"appoint_id": "course-1"},
            ),
        ]


def test_source_row_cache_serves_narrow_queries_from_one_course_prefetch() -> None:
    projector = _SourceRowCacheProjector()
    projector._source_row_cache = {}

    projector._active_source_rows(
        object(),
        suffixes=("appoint", "qa_task_close_camera_record"),
        dependency_name="course_ids",
        dependency_value="course-1",
    )
    camera = projector._active_source_rows(
        object(),
        suffixes=("qa_task_close_camera_record",),
        dependency_name="course_ids",
        dependency_value="course-1",
        regions=("ovs",),
    )
    repeated = projector._active_source_rows(
        object(),
        suffixes=("qa_task_close_camera_record",),
        dependency_name="course_ids",
        dependency_value="course-1",
        regions=("ovs",),
    )

    assert projector.query_calls == 1
    assert projector._source_cache_hits == 2
    assert [item.table for item in camera] == [
        "ovs_qa_task_close_camera_record"
    ]
    assert repeated == camera


def test_multi_course_dependency_lookup_uses_one_indexable_query() -> None:
    class EmptyRows:
        def mappings(self) -> EmptyRows:
            return self

        def __iter__(self):
            return iter(())

    class CaptureConnection:
        def __init__(self) -> None:
            self.statements: list[object] = []

        def execute(self, statement: object) -> EmptyRows:
            self.statements.append(statement)
            return EmptyRows()

    projector = DtsWideProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = CaptureConnection()

    rows = projector._active_source_rows_for_dependency_values(
        connection,
        suffixes=("complaint", "user_teacher_grading"),
        dependency_name="course_ids",
        dependency_values=("course-1", "course-2"),
    )

    sql = str(connection.statements[0].compile(dialect=postgresql.dialect()))
    assert rows == []
    assert len(connection.statements) == 1
    assert sql.count("@>") == 2
    assert "dts_source_rows.dependency_keys" in sql


class _TeacherFactsProjector(DtsWideProjector):
    def __init__(
        self,
        *,
        absence_rows: list[object] | None = None,
        feedback_rows: list[object] | None = None,
    ) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.absence_rows = absence_rows or []
        self.feedback_rows = feedback_rows or []
        self.feedback_query_calls = 0

    def _active_source_rows(self, *_args: object, **_kwargs: object) -> list[object]:
        return self.absence_rows

    def _active_source_rows_for_dependency_values(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> list[object]:
        self.feedback_query_calls += 1
        return self.feedback_rows


def test_no_notice_only_counts_explicit_absent_participation() -> None:
    projector = _TeacherFactsProjector()
    lessons = [
        {
            "source_region": "dom",
            "课程id": "course-1",
            "上课日期": date(2026, 8, 13),
            "上课时间": "13:30:00",
            "课程状态": "t_absent",
            "缺席原因明细": "No Notification",
        },
        {
            "source_region": "dom",
            "课程id": "course-2",
            "上课日期": date(2026, 8, 13),
            "上课时间": "13:30:00",
            "课程状态": "t_absent",
            "缺席原因明细": "other",
        },
        {
            "source_region": "dom",
            "课程id": "course-on",
            "上课日期": date(2026, 8, 13),
            "上课时间": "13:30:00",
            "课程状态": "on",
            "缺席原因明细": "No Notification",
        },
    ]

    no_notice = projector._teacher_absence_facts(
        object(),
        "teacher-1",
        lessons,
    )

    assert no_notice == {("dom", "course-1")}


def test_teacher_feedback_facts_bulk_reduce_all_courses_once() -> None:
    projector = _TeacherFactsProjector(
        feedback_rows=[
            SimpleNamespace(
                region="ovs",
                table="ovs_user_teacher_grading",
                row={
                    "id": "1",
                    "appoint_id": "course-1",
                    "score": 5,
                    "status": 0,
                    "is_del": 0,
                    "update_time": "2026-08-13 14:00:00",
                },
            ),
            SimpleNamespace(
                region="ovs",
                table="ovs_user_teacher_grading",
                row={
                    "id": "2",
                    "appoint_id": "course-2",
                    "score": 1,
                    "status": 1,
                    "is_del": 0,
                },
            ),
            SimpleNamespace(
                region="ovs",
                table="ovs_complaint",
                row={
                    "id": "10",
                    "stu_id": "student-1",
                    "appoint_id": "course-1",
                    "complaint_type": 13,
                    "complaint_type_grandson": 81,
                    "approve": "y",
                    "validity": 1,
                },
            ),
            SimpleNamespace(
                region="ovs",
                table="ovs_complaint",
                row={
                    "id": "11",
                    "stu_id": "student-2",
                    "appoint_id": "course-2",
                    "complaint_type": 13,
                    "complaint_type_grandson": 81,
                    "approve": "n",
                    "validity": 0,
                },
            ),
        ]
    )

    evaluated, complaint_total, valid_complaint_total = (
        projector._teacher_feedback_facts(
            object(),
            {("ovs", "course-1"), ("ovs", "course-2")},
        )
    )

    assert projector.feedback_query_calls == 1
    assert evaluated == set()
    assert complaint_total == 2
    assert valid_complaint_total == 1


def test_queued_missing_complaint_level3_remains_valid_for_counting() -> None:
    projector = _TeacherFactsProjector(
        feedback_rows=[
            SimpleNamespace(
                region="dom",
                table="dom_complaint",
                row={
                    "id": "10",
                    "stu_id": "student-1",
                    "appoint_id": "course-1",
                    "complaint_type": 13,
                    "complaint_type_child": 20,
                    "complaint_type_grandson": None,
                    "approve": "y",
                    "validity": 1,
                },
            )
        ]
    )

    _evaluated, complaint_total, valid_complaint_total = (
        projector._teacher_feedback_facts(object(), {("dom", "course-1")})
    )

    assert complaint_total == 1
    assert valid_complaint_total == 1


class _CompatibilityCourseFactsProjector(DtsWideProjector):
    def __init__(self, rows_by_suffix: dict[str, list[object]]) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.rows_by_suffix = rows_by_suffix

    def _active_source_rows(
        self,
        _connection: object,
        *,
        suffixes: tuple[str, ...],
        regions: tuple[str, ...] | None = None,
        **_kwargs: object,
    ) -> list[object]:
        assert len(suffixes) == 1
        rows = self.rows_by_suffix.get(suffixes[0], [])
        if regions is None:
            return rows
        return [row for row in rows if row.region in regions]


def test_queued_dom_grading_and_labels_use_dom_rules_only() -> None:
    projector = _CompatibilityCourseFactsProjector(
        {
            "user_teacher_grading": [
                SimpleNamespace(
                    region="dom",
                    table="dom_user_teacher_grading",
                    row={
                        "id": 1,
                        "appoint_id": "course-1",
                        "use_point": "buy",
                        "score": 1,
                        "type": "satisfactory",
                        "status": 99,
                        "is_del": 0,
                    },
                ),
                SimpleNamespace(
                    region="ovs",
                    table="ovs_user_teacher_grading",
                    row={
                        "id": 2,
                        "appoint_id": "course-1",
                        "score": 1,
                        "status": 0,
                        "is_del": 0,
                    },
                ),
            ],
            "grading_label_log": [
                SimpleNamespace(
                    region="dom",
                    table="dom_grading_label_log",
                    row={
                        "id": 1,
                        "appoint_id": "course-1",
                        "label_id": 9,
                        "label_name": "DOM 标签",
                        "type": 9,
                        "status": "deleted",
                    },
                ),
                SimpleNamespace(
                    region="ovs",
                    table="ovs_grading_label_log",
                    row={
                        "id": 2,
                        "appoint_id": "course-1",
                        "label_id": 9,
                        "label_name": "OVS 标签",
                        "type": 9,
                        "status": "deleted",
                    },
                ),
            ],
        }
    )

    dom = projector._latest_grading(object(), "course-1", "dom")
    ovs = projector._latest_grading(object(), "course-1", "ovs")

    assert dom is not None
    assert projector_module._grading_projection("dom", dom) == (
        1.0,
        True,
        False,
    )
    assert ovs is not None
    assert projector_module._grading_projection("ovs", ovs) == (
        None,
        None,
        None,
    )
    assert projector._feedback_detail(object(), "course-1", "dom") == (
        "DOM 标签"
    )
    assert projector._feedback_detail(object(), "course-1", "ovs") is None


def test_queued_absence_uses_latest_reason_type_only() -> None:
    projector = _CompatibilityCourseFactsProjector(
        {
            "teacher_absent_reason": [
                SimpleNamespace(
                    region="dom",
                    table="dom_teacher_absent_reason",
                    row={
                        "id": 1,
                        "appoint_id": "course-1",
                        "t_id": "teacher-1",
                        "reason_type": "No Notification",
                        "reason_desc": "must not be read",
                        "add_time": "2026-08-13 13:30:00",
                    },
                )
            ]
        }
    )

    assert projector._absence_reason(
        object(),
        "dom",
        "course-1",
        "teacher-1",
    ) == "No Notification"


@pytest.mark.parametrize(
    ("center_type", "expected"),
    [(1, "CBT"), (5, "TBT"), (None, "HBT"), (99, "HBT")],
)
def test_queued_center_type_mapping_defaults_to_hbt(
    center_type: object,
    expected: str,
) -> None:
    assert projector_module._center_type_description(center_type) == expected


def test_queued_tesol_uses_code_16_not_certificate_type() -> None:
    code_16 = SimpleNamespace(
        row={
            "certification_code": "16",
            "certification_type": "not-tesol",
            "certification_status": 1,
        }
    )
    wrong_code = SimpleNamespace(
        row={
            "certification_code": "15",
            "certification_type": "tesol",
            "certification_status": 1,
        }
    )

    assert DtsWideProjector._tesol_state(
        object(),
        "teacher-1",
        [code_16, wrong_code],
    ) is True


def test_queued_relationship_counts_do_not_require_any_course() -> None:
    projector = _CompatibilityCourseFactsProjector(
        {
            "teacher_favorite": [
                SimpleNamespace(
                    region="dom",
                    table="dom_teacher_favorite",
                    row={
                        "id": 1,
                        "tea_id": "teacher-1",
                        "student_token": "dom:v1:" + "a" * 64,
                    },
                ),
                # Duplicate source records for one current relationship still
                # count one student.
                SimpleNamespace(
                    region="dom",
                    table="dom_teacher_favorite",
                    row={
                        "id": 2,
                        "tea_id": "teacher-1",
                        "student_token": "dom:v1:" + "a" * 64,
                    },
                ),
            ],
            "teacher_blacklist": [
                SimpleNamespace(
                    region="ovs",
                    table="ovs_teacher_blacklist",
                    row={
                        "id": 3,
                        "teacher_id": "teacher-1",
                        "student_id": "student-9",
                        "is_valid_forever": 1,
                    },
                ),
                SimpleNamespace(
                    region="ovs",
                    table="ovs_teacher_blacklist",
                    row={
                        "id": 4,
                        "teacher_id": "teacher-1",
                        "student_id": "student-not-business-blocked",
                        "is_valid_forever": 0,
                        "valid_end_time": "2026-08-22",
                    },
                ),
            ],
        }
    )

    favorites = projector._teacher_relationship_students(
        object(),
        "teacher-1",
        relation_suffix="teacher_favorite",
    )
    blocked = projector._teacher_relationship_students(
        object(),
        "teacher-1",
        relation_suffix="teacher_blacklist",
    )

    assert favorites == {("dom", "dom:v1:" + "a" * 64)}
    assert blocked == {("ovs", "student-9")}


def test_queued_teacher_area_uses_dom_not_dmo() -> None:
    assert DtsWideProjector._teacher_area({"course": "h5_tc"}) == "dom"
    assert DtsWideProjector._teacher_area(
        {"course": "global_pool"}
    ) == "ovs"


class _TeacherTombstonedCourseProjector(_TeacherPendingProjector):
    def _teacher_tombstoned(
        self,
        _connection: object,
        _teacher_id: str,
    ) -> bool:
        return True


def test_course_with_teacher_tombstone_is_deleted_instead_of_retried() -> None:
    projector = _TeacherTombstonedCourseProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection(
        first_values=[{"课程id": "lesson-1", "老师id": "teacher-1"}],
        rowcounts=[0, 1],
    )

    result = projector._project_course(
        connection,
        "ovs",
        "lesson-1",
        {
            "last_source_region": "ovs",
            "last_topic": "ovs-topic",
            "last_partition": 0,
            "last_offset": 1,
        },
    )

    assert result.lesson_deletes == 1
    assert result.unchanged == 0
    assert connection.execute_count == 2
    assert "DELETE FROM lesson_source_wide" in str(connection.statements[1])


class _TeacherTombstoneProjector(DtsWideProjector):
    def _teacher_source(self, _connection: object, _teacher_id: str) -> None:
        return None

    def _teacher_tombstoned(
        self,
        _connection: object,
        _teacher_id: str,
    ) -> bool:
        return True


def test_teacher_tombstone_deletes_lessons_before_teacher() -> None:
    projector = _TeacherTombstoneProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection(rowcounts=[2, 1])

    result = projector._project_teacher(connection, "teacher-1")

    assert result.lesson_deletes == 2
    assert result.teacher_deletes == 1
    assert result.unchanged == 0
    assert connection.execute_count == 2
    assert "DELETE FROM lesson_source_wide" in str(connection.statements[0])
    assert "DELETE FROM teacher_source_wide" in str(connection.statements[1])


class _TeacherAnyOnboardProjector(DtsWideProjector):
    def __init__(self, status_on_time: object) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.status_on_time = status_on_time
        self.projected_values: dict[str, object] | None = None

    def _teacher_source(self, _connection: object, _teacher_id: str) -> object:
        return SimpleNamespace(
            row={
                "id": "teacher-1",
                "status": "on",
                "status_on_time": self.status_on_time,
                "course": "h5_tc",
            }
        )

    def _teacher_lessons(
        self,
        _connection: object,
        _teacher_id: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "source_region": "dom",
                "课程id": "lesson-existing",
                "上课日期": date(2026, 8, 22),
                "是否高峰": False,
                "学员id": "student-1",
                "课程状态": "on",
                "好评标签": False,
                "差评标签": False,
            }
        ]

    def _teacher_absence_facts(
        self,
        *_args: object,
    ) -> set[str]:
        return set()

    def _teacher_feedback_facts(
        self,
        *_args: object,
    ) -> tuple[set[str], int, int]:
        return set(), 0, 0

    def _teacher_relationship_students(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> set[tuple[str, str]]:
        return set()

    def _teacher_schedules(
        self,
        *_args: object,
    ) -> list[dict[str, object]]:
        return []

    def _active_source_rows(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> list[object]:
        return []

    def _tesol_state(self, *_args: object) -> None:
        return None

    def _upsert(
        self,
        _connection: object,
        _table: object,
        values: object,
        **_kwargs: object,
    ) -> bool:
        self.projected_values = dict(values)  # type: ignore[arg-type]
        return True


class _TeacherAbsenceNotPenaltyProjector(_TeacherAnyOnboardProjector):
    def __init__(self) -> None:
        super().__init__("2026-08-01 00:00:00")

    def _teacher_lessons(
        self,
        _connection: object,
        _teacher_id: str,
    ) -> list[dict[str, object]]:
        common = {
            "source_region": "dom",
            "上课日期": date(2026, 8, 22),
            "上课时间": "13:30:00",
            "是否高峰": False,
            "学员id": "student-1",
            "迟到": False,
            "早退": False,
            "好评标签": False,
            "差评标签": False,
            "缺席原因明细": "No Notification",
        }
        return [
            {**common, "课程id": "lesson-end", "课程状态": "end"},
            {**common, "课程id": "lesson-on", "课程状态": "on"},
        ]

    def _teacher_absence_facts(
        self,
        connection: object,
        teacher_id: str,
        lessons: list[dict[str, object]],
    ) -> set[str]:
        return DtsWideProjector._teacher_absence_facts(
            self,
            connection,
            teacher_id,
            lessons,
        )

@pytest.mark.parametrize(
    ("status_on_time", "expected_onboard", "expected_job_days"),
    [
        ("2020-01-01 00:00:00", date(2020, 1, 1), 2425),
        (None, None, None),
    ],
)
def test_teacher_projection_keeps_old_or_undated_teacher_and_existing_course(
    status_on_time: object,
    expected_onboard: date | None,
    expected_job_days: int | None,
) -> None:
    projector = _TeacherAnyOnboardProjector(status_on_time)
    connection = _ReadOnlyConnection(first_values=[date(2026, 8, 22)])

    result = projector._project_teacher(connection, "teacher-1")

    assert result.teacher_upserts == 1
    assert result.teacher_deletes == 0
    assert result.lesson_deletes == 0
    assert projector.projected_values is not None
    assert projector.projected_values["onboard_date"] == expected_onboard
    assert projector.projected_values["onboard_30d_end_date"] == (
        date(2020, 1, 30) if expected_onboard is not None else None
    )
    assert projector.projected_values["job_days"] == expected_job_days
    assert projector.projected_values["total_booked_cnt"] == 1
    if expected_onboard is None:
        assert (
            projector.projected_values["capacity_avg_completed_per_day"]
            is None
        )
    assert all(
        "DELETE FROM" not in str(statement)
        for statement in connection.statements
    )


def test_absence_reason_does_not_invent_penalty_or_break_perfect_lesson() -> None:
    projector = _TeacherAbsenceNotPenaltyProjector()
    connection = _ReadOnlyConnection(first_values=[date(2026, 8, 22)])

    projector._project_teacher(connection, "teacher-1")

    assert projector.projected_values is not None
    assert projector.projected_values["late_cnt"] == 0
    assert projector.projected_values["early_cnt"] == 0
    assert projector.projected_values["anomaly_cnt"] == 0
    assert projector.projected_values["perfect_cnt"] == 1
    assert projector.projected_values["no_notice_cnt"] == 0


class _CourseAnyCohortTeacherProjector(DtsWideProjector):
    def __init__(
        self,
        status_on_time: object,
        *,
        appoint_teacher_id: str = "teacher-1",
        appoint_status: str = "on",
    ) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.status_on_time = status_on_time
        self.appoint_teacher_id = appoint_teacher_id
        self.appoint_status = appoint_status
        self.teacher_source_ids: list[str] = []
        self.projected_values: dict[str, object] | None = None

    def _appoint_source(
        self, _connection: object, _source_region: str, _course_id: str
    ) -> object:
        return SimpleNamespace(
            region="dom",
            row={
                "id": "lesson-1",
                "t_id": self.appoint_teacher_id,
                "student_token": "dom:v1:" + "a" * 64,
                "date": "2026-08-22",
                "start_time": "2026-08-22 18:00:00",
                "status": self.appoint_status,
            },
        )

    def _teacher_source(self, _connection: object, teacher_id: str) -> object:
        self.teacher_source_ids.append(teacher_id)
        return SimpleNamespace(
            row={
                "id": teacher_id,
                "status_on_time": self.status_on_time,
                "course": "h5_tc",
            }
        )

    def _ensure_teacher_wide(
        self,
        _connection: object,
        _teacher_id: str,
    ) -> _ProjectionCounts:
        return _ProjectionCounts()

    def _absence_reason(self, *_args: object) -> None:
        return None

    def _active_source_rows(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> list[object]:
        return []

    def _latest_grading(self, *_args: object) -> None:
        return None

    def _feedback_detail(self, *_args: object) -> None:
        return None

    def _complaint_names(
        self,
        *_args: object,
    ) -> tuple[None, None, None]:
        return None, None, None

    def _relationship_assigned_to_course(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        return False

    def _has_course_record(self, *_args: object) -> bool:
        return False

    def _upsert(
        self,
        _connection: object,
        _table: object,
        values: object,
        **_kwargs: object,
    ) -> bool:
        self.projected_values = dict(values)  # type: ignore[arg-type]
        return True

    def _enqueue_lesson_teachers_if_changed(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        return None


@pytest.mark.parametrize("status_on_time", ["2020-01-01", None])
def test_course_projection_accepts_old_or_undated_teacher_update(
    status_on_time: object,
) -> None:
    projector = _CourseAnyCohortTeacherProjector(status_on_time)
    connection = _ReadOnlyConnection(
        first_values=[
            {
                "课程id": "lesson-1",
                "老师id": "teacher-1",
                "课程状态": "on",
            }
        ]
    )

    result = projector._project_course(
        connection,
        "dom",
        "lesson-1",
        {
            "last_source_region": "dom",
            "last_topic": "dom-topic",
            "last_partition": 0,
            "last_offset": 1,
        },
    )

    assert result.lesson_upserts == 1
    assert result.lesson_deletes == 0
    assert projector.projected_values is not None
    assert projector.projected_values["课程id"] == "lesson-1"
    assert all(
        "DELETE FROM" not in str(statement)
        for statement in connection.statements
    )


def test_queued_projection_keeps_first_end_teacher_after_source_owner_changes(
) -> None:
    projector = _CourseAnyCohortTeacherProjector(
        "2026-08-01",
        appoint_teacher_id="teacher-2",
        appoint_status="on",
    )
    connection = _ReadOnlyConnection(
        first_values=[
            {
                "课程id": "lesson-1",
                "老师id": "teacher-1",
                "课程状态": "end",
            }
        ]
    )

    result = projector._project_course(
        connection,
        "dom",
        "lesson-1",
        {
            "last_source_region": "dom",
            "last_topic": "dom-topic",
            "last_partition": 0,
            "last_offset": 2,
        },
    )

    assert result.lesson_upserts == 1
    assert projector.projected_values is not None
    assert projector.projected_values["老师id"] == "teacher-1"
    assert projector.projected_values["课程状态"] == "end"
    assert projector.teacher_source_ids == ["teacher-1"]


class _AppointPendingProjector(DtsWideProjector):
    def _appoint_source(
        self, _connection: object, _source_region: str, _course_id: str
    ) -> None:
        return None


def test_missing_appoint_is_retried_instead_of_deleting_an_existing_lesson() -> None:
    projector = _AppointPendingProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection(
        first_values=[{"课程id": "lesson-1", "老师id": "teacher-1"}, None]
    )

    with pytest.raises(
        DtsWideProjectionError,
        match="DTS_APPOINT_DEPENDENCY_PENDING",
    ):
        projector._project_course(
            connection,
            "ovs",
            "lesson-1",
            {
                "last_source_region": "ovs",
                "last_topic": "ovs-topic",
                "last_partition": 0,
                "last_offset": 1,
            },
        )

    assert connection.execute_count == 2


class _TeacherWideEnsureProjector(DtsWideProjector):
    def __init__(self) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.projected_teacher_ids: list[str] = []

    def _project_teacher(self, _connection: object, teacher_id: str) -> object:
        from app.dts_wide_projector import _ProjectionCounts

        self.projected_teacher_ids.append(teacher_id)
        return _ProjectionCounts(teacher_upserts=1)


def test_teacher_wide_identity_is_materialized_before_a_first_lesson() -> None:
    projector = _TeacherWideEnsureProjector()

    result = projector._ensure_teacher_wide(
        _ReadOnlyConnection(first_values=[None]),
        "teacher-1",
    )

    assert result.teacher_upserts == 1
    assert projector.projected_teacher_ids == ["teacher-1"]


def test_existing_teacher_wide_identity_is_not_reprojected() -> None:
    projector = _TeacherWideEnsureProjector()

    result = projector._ensure_teacher_wide(
        _ReadOnlyConnection(first_values=["teacher-1"]),
        "teacher-1",
    )

    assert result.teacher_upserts == 0
    assert projector.projected_teacher_ids == []


class _ComplaintCategoryProjector(DtsWideProjector):
    def __init__(
        self,
        categories: list[dict[str, object]],
        *,
        complaint_type_child: object = "14",
        complaint_type_grandson: object = "15",
    ) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.categories = categories
        self.complaint_type_child = complaint_type_child
        self.complaint_type_grandson = complaint_type_grandson

    def _active_source_rows(
        self,
        _connection: object,
        *,
        suffixes: tuple[str, ...],
        **_kwargs: object,
    ) -> list[object]:
        if suffixes == ("user_complaint",):
            return []
        if suffixes == ("complaint",):
            return [
                SimpleNamespace(
                    table="ovs_complaint",
                    row={
                        "id": "1",
                        "stu_id": "student-1",
                        "appoint_id": "lesson-1",
                        "complaint_type": "13",
                        "complaint_type_child": self.complaint_type_child,
                        "complaint_type_grandson": self.complaint_type_grandson,
                        "approve": "y",
                        "validity": 1,
                    },
                )
            ]
        if suffixes == ("complaint_cate",):
            return [
                SimpleNamespace(table="dom_complaint_cate", row=row)
                for row in self.categories
            ]
        raise AssertionError(suffixes)


def test_complaint_projection_waits_for_the_static_category_dictionary() -> None:
    projector = _ComplaintCategoryProjector([])

    with pytest.raises(
        DtsWideProjectionError,
        match="DTS_COMPLAINT_CATEGORY_DEPENDENCY_PENDING",
    ):
        projector._complaint_names(object(), "ovs", "lesson-1")


def test_complaint_projection_uses_the_complete_category_path() -> None:
    projector = _ComplaintCategoryProjector(
        [
            {"id": "13", "cate_cn_name": "一级"},
            {"id": "14", "cate_cn_name": "二级"},
            {"id": "15", "cate_cn_name": "三级"},
        ]
    )

    assert projector._complaint_names(object(), "ovs", "lesson-1") == (
        "一级",
        "二级",
        "三级",
    )


@pytest.mark.parametrize("sentinel", ["-1", "0", -1, 0])
def test_complaint_projection_treats_absent_level_sentinels_as_null(
    sentinel: object,
) -> None:
    projector = _ComplaintCategoryProjector(
        [{"id": "13", "cate_cn_name": "一级"}],
        complaint_type_child=sentinel,
        complaint_type_grandson=sentinel,
    )

    assert projector._complaint_names(object(), "ovs", "lesson-1") == (
        "一级",
        None,
        None,
    )


def test_internal_teacher_redirty_resets_an_exhausted_retry_cycle() -> None:
    projector = DtsWideProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection()

    projector._enqueue_teacher(
        connection,
        "teacher-1",
        {
            "last_source_region": "ovs",
            "last_source_table": "ovs_appoint",
            "last_topic": "ovs-topic",
            "last_partition": 0,
            "last_offset": 42,
        },
    )

    compiled = connection.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert any(
        f"attempt_count = %({name})s" in sql and value == 0
        for name, value in compiled.params.items()
    )


class _TeacherEnqueueTrackingProjector(DtsWideProjector):
    def __init__(self) -> None:
        super().__init__(
            object(),
            worker_id="test",
            settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
        )
        self.enqueued: list[str | None] = []

    def _enqueue_teacher(
        self,
        _connection: object,
        teacher_id: str | None,
        _origin: object,
    ) -> None:
        self.enqueued.append(teacher_id)


def test_unchanged_lesson_does_not_amplify_teacher_dirty_keys() -> None:
    projector = _TeacherEnqueueTrackingProjector()

    projector._enqueue_lesson_teachers_if_changed(
        object(),
        changed=False,
        teacher_id="teacher-1",
        old_teacher_id="teacher-1",
        origin={},
    )
    projector._enqueue_lesson_teachers_if_changed(
        object(),
        changed=True,
        teacher_id="teacher-2",
        old_teacher_id="teacher-1",
        origin={},
    )

    assert projector.enqueued == ["teacher-2", "teacher-1"]
