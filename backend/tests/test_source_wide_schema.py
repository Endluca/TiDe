from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

import sqlalchemy as sa

from app.db_models import LessonSourceWideRecord, TeacherSourceWideRecord
from app.source_contracts import (
    LESSON_SOURCE_FIELDS,
    TEACHER_CSV_FIELDS,
    TEACHER_SOURCE_FIELDS,
)


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260806_39_source_wide.py"
    )
    spec = importlib.util.spec_from_file_location(
        "source_wide_v39",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _capture_upgrade(monkeypatch):
    migration = _load_migration()
    created_tables: dict[str, tuple[tuple[sa.Column, ...], dict]] = {}
    created_indexes: list[tuple[str, str, tuple[str, ...], dict]] = []
    executed: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )

    def create_table(name, *columns, **kwargs):
        created_tables[name] = (columns, kwargs)

    def create_index(name, table_name, columns, **kwargs):
        created_indexes.append((name, table_name, tuple(columns), kwargs))

    monkeypatch.setattr(migration.op, "create_table", create_table)
    monkeypatch.setattr(migration.op, "create_index", create_index)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration.upgrade()
    return migration, created_tables, created_indexes, executed


def _column_names(model) -> tuple[str, ...]:
    return tuple(column.name for column in model.__table__.columns)


def _type_signature(column: sa.Column) -> tuple[type, int | None, bool | None]:
    return (
        type(column.type),
        getattr(column.type, "length", None),
        getattr(column.type, "timezone", None),
    )


RETIRED_IN_V12 = {
    "tchr_group",
    "tchr_group_desc",
    "based_type",
    "is_ft_hbt",
    "is_fte",
    "tchr_score",
    "completed_again_student_15d_cnt",
    "feedback_rebook_rate",
}


def test_orm_source_wide_tables_have_53_mapped_plus_2_g01_and_23_lesson_columns() -> None:
    assert TeacherSourceWideRecord.__tablename__ == "teacher_source_wide"
    assert LessonSourceWideRecord.__tablename__ == "lesson_source_wide"
    assert _column_names(TeacherSourceWideRecord) == TEACHER_SOURCE_FIELDS
    assert _column_names(LessonSourceWideRecord) == LESSON_SOURCE_FIELDS
    assert len(TeacherSourceWideRecord.__table__.columns) == 55
    assert len(LessonSourceWideRecord.__table__.columns) == 23
    assert "是否复约" not in _column_names(LessonSourceWideRecord)

    assert tuple(
        column.name for column in TeacherSourceWideRecord.__table__.primary_key
    ) == ("tchr_id",)
    assert tuple(
        column.name for column in LessonSourceWideRecord.__table__.primary_key
    ) == ("课程id",)
    assert not LessonSourceWideRecord.__table__.foreign_keys
    assert LessonSourceWideRecord.__table__.columns["老师id"].nullable is False


def test_source_types_preserve_existing_date_semantics_and_unbounded_text() -> None:
    teacher_columns = TeacherSourceWideRecord.__table__.columns
    lesson_columns = LessonSourceWideRecord.__table__.columns

    for field in ("first_open_slot_dt", "first_booked_dt", "first_completed_dt"):
        assert type(teacher_columns[field].type) is sa.Date
    assert type(teacher_columns["job_month"].type) is sa.Float

    for field in (
        "real_name",
        "center_type_desc",
        "bu",
        "status",
        "teach_area_type",
    ):
        assert type(teacher_columns[field].type) is sa.Text
    for field in (
        "课程状态",
        "缺席原因明细",
        "投诉一级分类",
        "投诉二级分类",
        "投诉三级分类",
        "评价详情",
    ):
        assert type(lesson_columns[field].type) is sa.Text


def test_revision_39_preserves_the_original_csv_columns_and_types(monkeypatch) -> None:
    migration, created_tables, _indexes, _executed = _capture_upgrade(monkeypatch)
    assert migration.down_revision == "20260729_38_catalog_scores"
    assert set(created_tables) == {"teacher_source_wide", "lesson_source_wide"}

    teacher_columns, teacher_options = created_tables["teacher_source_wide"]
    lesson_columns, lesson_options = created_tables["lesson_source_wide"]
    assert teacher_options == {"schema": "public"}
    assert lesson_options == {"schema": "public"}
    revision_39_teacher_fields = tuple(column.name for column in teacher_columns)
    assert tuple(
        field for field in revision_39_teacher_fields if field not in RETIRED_IN_V12
    ) == TEACHER_CSV_FIELDS
    assert set(revision_39_teacher_fields) - set(TEACHER_CSV_FIELDS) == RETIRED_IN_V12
    assert tuple(column.name for column in lesson_columns) == LESSON_SOURCE_FIELDS
    assert "是否复约" not in {column.name for column in lesson_columns}
    assert [column.name for column in teacher_columns if column.primary_key] == [
        "tchr_id"
    ]
    assert [column.name for column in lesson_columns if column.primary_key] == [
        "课程id"
    ]
    assert not any(column.foreign_keys for column in lesson_columns)

    lesson_orm = LessonSourceWideRecord.__table__.columns
    teacher_orm = TeacherSourceWideRecord.__table__.columns
    assert {
        column.name: _type_signature(column)
        for column in teacher_columns
        if column.name in TEACHER_CSV_FIELDS
    } == {
        column_name: _type_signature(teacher_orm[column_name])
        for column_name in TEACHER_CSV_FIELDS
    }
    assert {
        column.name: _type_signature(column) for column in lesson_columns
    } == {
        column.name: _type_signature(column) for column in lesson_orm
    }


def test_only_two_read_path_indexes_are_created(monkeypatch) -> None:
    _migration, _tables, indexes, _executed = _capture_upgrade(monkeypatch)
    assert indexes == [
        (
            "ix_lesson_source_wide_teacher_time",
            "lesson_source_wide",
            ("老师id", "上课日期", "上课时间"),
            {"unique": False, "schema": "public"},
        ),
        (
            "ix_lesson_source_wide_teacher_student_time",
            "lesson_source_wide",
            ("老师id", "学员id", "上课日期", "上课时间"),
            {"unique": False, "schema": "public"},
        ),
    ]


def test_trigger_emits_only_real_field_diffs_and_compact_payload(monkeypatch) -> None:
    _migration, _tables, _indexes, executed = _capture_upgrade(monkeypatch)
    sql = "\n".join(executed)

    assert "AFTER INSERT OR UPDATE OR DELETE ON public.teacher_source_wide" in sql
    assert "AFTER INSERT OR UPDATE OR DELETE ON public.lesson_source_wide" in sql
    assert "old_row -> key_name IS DISTINCT FROM new_row -> key_name" in sql
    assert "IF cardinality(changed_fields) = 0" in sql
    assert "source_table_name := TG_TABLE_NAME" in sql
    assert "source_table_name := TG_TABLE_SCHEMA" not in sql
    assert "teacher_source_wide.tchr_id is immutable" in sql
    assert "lesson_source_wide.课程id is immutable" in sql
    assert "INSERT INTO public.outbox_events" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "md5(concat_ws(" in sql
    assert "gen_random_uuid" not in sql
    assert "uuid_generate" not in sql

    payload_start = sql.index("jsonb_build_object(")
    payload_end = sql.index("),\n                'PENDING'", payload_start)
    payload_sql = sql[payload_start:payload_end]
    payload_keys = re.findall(r"^\s+'([^']+)',", payload_sql, flags=re.MULTILINE)
    assert payload_keys == [
        "source_table",
        "source_id",
        "operation",
        "changed_fields",
        "old_teacher_id",
        "new_teacher_id",
    ]
    assert "raw_payload" not in payload_sql
    assert "new_row," not in payload_sql
    assert "old_row," not in payload_sql


def test_source_tables_have_separate_minimum_read_and_write_roles(monkeypatch) -> None:
    _migration, _tables, _indexes, executed = _capture_upgrade(monkeypatch)
    sql = "\n".join(executed)

    assert "required database role tit_growth_app does not exist" in sql
    assert "required database role tit_source_monitor does not exist" in sql
    assert "tit_source_monitor must be a NOLOGIN permission group" in sql
    assert "FROM PUBLIC" in sql
    assert "GRANT SELECT ON TABLE" in sql
    assert "TO tit_growth_app" in sql
    assert "rolname = 'tit_source_monitor'" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in sql
    assert "TO tit_source_monitor" in sql
    assert "GRANT UPDATE ON TABLE public.outbox_events" not in sql
    assert "TO tit_teacher_crud" not in sql


def test_downgrade_refuses_to_drop_active_source_state(monkeypatch) -> None:
    migration = _load_migration()
    executed: list[str] = []
    dropped_tables: list[tuple[str, dict]] = []
    dropped_indexes: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **kwargs: dropped_indexes.append((name, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda name, **kwargs: dropped_tables.append((name, kwargs)),
    )

    migration.downgrade()

    guard_sql = "\n".join(executed)
    assert "EXISTS (SELECT 1 FROM public.teacher_source_wide LIMIT 1)" in guard_sql
    assert "EXISTS (SELECT 1 FROM public.lesson_source_wide LIMIT 1)" in guard_sql
    assert "event_type = 'source_wide.changed.v1'" in guard_sql
    assert "status = 'PENDING'" in guard_sql
    assert "refusing to drop active source-wide state" in guard_sql
    assert "DROP TRIGGER IF EXISTS trg_teacher_source_wide_outbox_v1" in guard_sql
    assert "DROP FUNCTION IF EXISTS public.emit_source_wide_change_v1()" in guard_sql
    assert dropped_indexes == [
        (
            "ix_lesson_source_wide_teacher_student_time",
            {"table_name": "lesson_source_wide", "schema": "public"},
        ),
        (
            "ix_lesson_source_wide_teacher_time",
            {"table_name": "lesson_source_wide", "schema": "public"},
        ),
    ]
    assert dropped_tables == [
        ("lesson_source_wide", {"schema": "public"}),
        ("teacher_source_wide", {"schema": "public"}),
    ]
