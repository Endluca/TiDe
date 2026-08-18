from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.db_models import TeacherSourceWideRecord
from app.dts_wide_projector import (
    DtsWideProjectionError,
    DtsWideProjectionSettings,
    DtsWideProjector,
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
    projector = DtsWideProjector(object(), worker_id="test", settings=settings)

    assert projector._teacher_in_cohort(
        {"status_on_time": "2026-08-13 00:00:00"}
    )
    assert projector._teacher_in_cohort(
        {"status_on_time": "2026-08-31 23:59:59"}
    )
    assert not projector._teacher_in_cohort(
        {"status_on_time": "2026-09-01 00:00:00"}
    )

    with pytest.raises(DtsWideProjectionError, match="DTS_COHORT_WINDOW_INVALID"):
        DtsWideProjectionSettings.from_env(
            {
                "TIT_DTS_COHORT_START": "2026-08-13",
                "TIT_DTS_COHORT_END_EXCLUSIVE": "2026-08-13",
            }
        )


def test_lesson_scope_requires_confirmed_teacher_region_and_30_day_window() -> None:
    projector = DtsWideProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    teacher = {
        "status_on_time": "2026-08-13 00:00:00",
        "course": "global_cn,global_pool",
    }

    assert projector._lesson_matches_teacher_window(
        "ovs", date(2026, 8, 13), teacher
    )
    assert projector._lesson_matches_teacher_window(
        "ovs", date(2026, 9, 11), teacher
    )
    assert not projector._lesson_matches_teacher_window(
        "ovs", date(2026, 9, 12), teacher
    )
    assert not projector._lesson_matches_teacher_window(
        "dom", date(2026, 8, 13), teacher
    )
    assert not projector._lesson_matches_teacher_window(
        "ovs", date(2026, 8, 13), {"course": "global_cn"}
    )


def test_domestic_appoint_scope_accepts_only_protected_student_subject() -> None:
    token = "dom:v1:" + "a" * 64

    assert DtsWideProjector._appoint_in_scope(
        {
            "use_point": "buy",
            "status": "end",
            "student_token": token,
        }
    )
    assert not DtsWideProjector._appoint_in_scope(
        {"use_point": "buy", "status": "end"}
    )


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

    def execute(self, statement: object) -> _FirstResult:
        self.execute_count += 1
        self.statements.append(statement)
        return _FirstResult(
            next(self.first_values, None),
            rowcount=next(self.rowcounts, 0),
        )


class _BeginContext:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def __enter__(self) -> object:
        return self.connection

    def __exit__(self, *_args: object) -> bool:
        return False


class _BatchEngine:
    def begin(self) -> _BeginContext:
        return _BeginContext(object())


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

    def _lock_next_dirty_key(self, _connection: object) -> object:
        self.lock_calls += 1
        if self.lock_calls > 1:
            return None
        return {
            "key_type": "COURSE",
            "key_part_1": "course-1",
            "key_part_2": "",
            "attempt_count": self.persisted_attempts,
        }

    def _mark_processing(self, *_args: object) -> None:
        return None

    def _dispatch(self, *_args: object) -> object:
        self.dispatch_calls += 1
        raise DtsWideProjectionError("DTS_TEACHER_DEPENDENCY_PENDING")

    def _mark_retry(self, *_args: object) -> int:
        self.retry_calls += 1
        return self.next_attempt


def test_transient_projection_failure_retries_below_the_limit() -> None:
    projector = _FailingBatchProjector(persisted_attempts=0, next_attempt=1)

    result = projector.run_batch(max_keys=2)

    assert result["retries"] == 1
    assert projector.dispatch_calls == 1
    assert projector.retry_calls == 1


def test_projection_failure_raises_stable_error_when_retry_limit_is_reached() -> None:
    projector = _FailingBatchProjector(persisted_attempts=1, next_attempt=2)

    with pytest.raises(
        DtsWideProjectionError,
        match="^DTS_WIDE_PROJECTION_RETRY_EXHAUSTED$",
    ):
        projector.run_batch(max_keys=1)

    assert projector.dispatch_calls == 1
    assert projector.retry_calls == 1


def test_persisted_exhausted_key_fails_again_without_dispatching() -> None:
    projector = _FailingBatchProjector(persisted_attempts=2, next_attempt=3)

    with pytest.raises(
        DtsWideProjectionError,
        match="^DTS_WIDE_PROJECTION_RETRY_EXHAUSTED$",
    ):
        projector.run_batch(max_keys=1)

    assert projector.dispatch_calls == 0
    assert projector.retry_calls == 0


class _TeacherPendingProjector(DtsWideProjector):
    def _appoint_source(self, _connection: object, _course_id: str) -> object:
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
            "lesson-1",
            {
                "last_source_region": "ovs",
                "last_topic": "ovs-topic",
                "last_partition": 0,
                "last_offset": 1,
            },
        )

    assert connection.execute_count == 2


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


class _TeacherOutsideCohortProjector(DtsWideProjector):
    def _teacher_source(self, _connection: object, _teacher_id: str) -> object:
        return SimpleNamespace(
            row={
                "id": "teacher-1",
                "status_on_time": "2026-08-12 23:59:59",
            }
        )


def test_teacher_update_outside_cohort_deletes_lessons_before_teacher() -> None:
    projector = _TeacherOutsideCohortProjector(
        object(),
        worker_id="test",
        settings=DtsWideProjectionSettings(cohort_start=date(2026, 8, 13)),
    )
    connection = _ReadOnlyConnection(rowcounts=[3, 1])

    result = projector._project_teacher(connection, "teacher-1")

    assert result.lesson_deletes == 3
    assert result.teacher_deletes == 1
    assert result.unchanged == 0
    assert connection.execute_count == 2
    assert "DELETE FROM lesson_source_wide" in str(connection.statements[0])
    assert "DELETE FROM teacher_source_wide" in str(connection.statements[1])


class _AppointPendingProjector(DtsWideProjector):
    def _appoint_source(self, _connection: object, _course_id: str) -> None:
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
        projector._complaint_names(object(), "lesson-1")


def test_complaint_projection_uses_the_complete_category_path() -> None:
    projector = _ComplaintCategoryProjector(
        [
            {"id": "13", "cate_cn_name": "一级"},
            {"id": "14", "cate_cn_name": "二级"},
            {"id": "15", "cate_cn_name": "三级"},
        ]
    )

    assert projector._complaint_names(object(), "lesson-1") == (
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

    assert projector._complaint_names(object(), "lesson-1") == (
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
