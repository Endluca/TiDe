from __future__ import annotations

from datetime import date, datetime

import pytest

from app.dts_v2_source_repository import (
    DtsV2CurrentSourceRow,
    DtsV2SourceRepository,
    DtsV2SourceRepositoryError,
)


def _row(**overrides):
    value = {
        "source_region": "dom",
        "source_table": "dom_user_teacher_grading",
        "source_key": "12",
        "source_key_type": "NUMERIC",
        "source_row_revision": 3,
        "source_row": {
            "id": 12,
            "appoint_id": 9001,
            "use_point": "buy",
            "update_time": "2026-08-22T10:00:00+08:00",
        },
        "source_field_types": {
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "use_point": "TEXT",
            "update_time": "TEMPORAL",
        },
        "source_position_v2": {"v": 1, "offset_value": 9},
        "source_payload_hash": "a" * 64,
        "is_deleted": False,
        "provenance_state": "V2_CONFIRMED",
    }
    value.update(overrides)
    return value


def test_current_source_row_exposes_only_evidence_checked_values() -> None:
    row = DtsV2CurrentSourceRow.from_database_row(_row())
    assert row.identity.source_id == "12"
    assert row.typed_id("appoint_id") == ("9001", "NUMERIC")
    assert row.text_value("use_point") == "buy"
    assert row.datetime_value("update_time") == datetime.fromisoformat(
        "2026-08-22T10:00:00+08:00"
    )
    assert row.version_vector_entry()["source_payload_hash"] == "a" * 64


def test_date_and_datetime_never_guess_missing_timezone() -> None:
    aware = DtsV2CurrentSourceRow.from_database_row(
        _row(
            source_row={
                "id": 12,
                "course_date": "2026-08-22",
                "add_time": "2026-08-22T10:00:00+08:00",
            },
            source_field_types={
                "id": "NUMERIC",
                "course_date": "TEMPORAL",
                "add_time": "TEMPORAL",
            },
        )
    )
    assert aware.date_value("course_date") == date(2026, 8, 22)
    assert aware.datetime_value("add_time").utcoffset() is not None

    naive = DtsV2CurrentSourceRow.from_database_row(
        _row(
            source_row={"id": 12, "add_time": "2026-08-22 10:00:00"},
            source_field_types={"id": "NUMERIC", "add_time": "TEMPORAL"},
        )
    )
    with pytest.raises(
        DtsV2SourceRepositoryError,
        match="DATETIME_TIMEZONE_MISSING",
    ):
        naive.datetime_value("add_time")


def test_dom_raw_student_alias_is_rejected_even_if_typed() -> None:
    with pytest.raises(
        DtsV2SourceRepositoryError,
        match="DOM_RAW_STUDENT_ID_FORBIDDEN",
    ):
        DtsV2CurrentSourceRow.from_database_row(
            _row(
                source_row={"id": 12, "s_id": 10086},
                source_field_types={"id": "NUMERIC", "s_id": "NUMERIC"},
            )
        )


@pytest.mark.parametrize(
    "override",
    [
        {"provenance_state": "LEGACY_PENDING"},
        {"source_row_revision": None},
        {"source_key": "012"},
        {"source_payload_hash": "raw"},
        {"source_field_types": {"id": "NUMERIC"}},
    ],
)
def test_unconfirmed_or_incomplete_current_rows_fail_closed(override) -> None:
    with pytest.raises(DtsV2SourceRepositoryError):
        DtsV2CurrentSourceRow.from_database_row(_row(**override))


class _MappingsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        return _MappingsResult(self.rows)


def test_repository_reads_by_protected_dependency_without_raw_payload() -> None:
    connection = _Connection([_row()])
    rows = DtsV2SourceRepository().read_for_dependency(
        connection,
        source_region="dom",
        source_tables=("dom_user_teacher_grading",),
        dependency_kind="course_ids",
        dependency_value="9001",
    )
    assert rows[0].source_key == "12"
    sql, parameters = connection.calls[0]
    assert "dependency_keys @>" in sql
    assert "FOR SHARE" in sql
    assert parameters["dependency"] == '{"course_ids":["9001"]}'


def test_repository_rejects_cross_region_table() -> None:
    with pytest.raises(DtsV2SourceRepositoryError, match="TABLES_INVALID"):
        DtsV2SourceRepository().read_for_dependency(
            _Connection([]),
            source_region="dom",
            source_tables=("ovs_appoint",),
            dependency_kind="course_ids",
            dependency_value="1",
        )
