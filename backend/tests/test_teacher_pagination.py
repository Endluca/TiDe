from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import engine, session_scope
from app.db_models import TeacherRecord
from app.main import app
from app.teacher_read_service import DashboardReadService, TeacherReadService


client = TestClient(app)


def test_dashboard_uses_compact_database_aggregation() -> None:
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        dashboard = DashboardReadService(engine).dashboard()
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert dashboard["teacher_count"] == 4
    assert dashboard["data_mode_counts"] == {"MOCK": 4}
    assert dashboard["employment_status_counts"] == {"unknown": 4}
    assert len(statements) == 4
    assert all(
        "teacher_metric_snapshots" not in statement.casefold()
        for statement in statements
    )


def test_teacher_reads_use_current_teacher_projection_without_snapshot_join() -> None:
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
        teacher.total_score = 123
        teacher.graduation_threshold = 110
        teacher.payload = {
            **teacher.payload,
            "employment_status": " HEI ",
            # The typed scalar is the current total; this stale compatibility
            # value must not override it.
            "raw_total_score": 999,
            "external_display_score": 88,
            "graduation_threshold": 110,
            "gold_threshold": 200,
            "first_booked_date": "2026-08-01",
            "is_cpl_tesol": True,
            "is_self_introduce": False,
            "lessons_completed": 17,
            "score_policy_version": "source-current-v1",
            "score_policy_sha256": "abc123",
        }

    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    service = TeacherReadService(engine)
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        page = service.list_teachers(employment_status="hei")
        options = service.teacher_options(keyword="T-1001")
        detail = service.teacher_detail("T-1001")
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert page["total"] == 1
    assert page["items"][0]["teacher_id"] == "T-1001"
    assert page["items"][0]["raw_total_score"] == 123
    assert page["items"][0]["external_display_score"] == 88
    assert options[0]["employment_status"] == " HEI "
    assert detail["raw_total_score"] == 123
    assert detail["total_score"] == 123
    assert detail["external_display_score"] == 88
    assert detail["first_booked_date"] == "2026-08-01"
    assert detail["is_cpl_tesol"] is True
    assert detail["is_self_introduce"] is False
    assert detail["lessons_completed"] == 17
    assert detail["score_policy_version"] == "source-current-v1"
    assert detail["score_policy_sha256"] == "abc123"
    assert all(
        "teacher_metric_snapshots" not in statement.casefold()
        for statement in statements
    )


def test_teacher_facing_score_never_exceeds_200_while_raw_keeps_accumulating() -> None:
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, "T-1001")
        assert teacher is not None
        teacher.total_score = 275
        teacher.payload = {
            **teacher.payload,
            "external_display_score": 275,
        }

    detail = TeacherReadService(engine).teacher_detail("T-1001")

    assert detail["raw_total_score"] == 275
    assert detail["external_display_score"] == 200


def test_teacher_list_is_paged_filtered_and_lightweight() -> None:
    response = client.get("/api/teachers?page=1&page_size=2&data_mode=MOCK")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2
    assert body["filters"]["data_mode"] == "MOCK"
    assert body["filters"]["available_data_modes"] == ["MOCK"]
    assert body["filters"]["available_employment_statuses"] == ["UNKNOWN"]

    for item in body["items"]:
        assert "metric_inputs" not in item
        assert "metric_provenance" not in item
        assert "hard_gates" not in item
        assert "tasks" not in item
        assert "ops_cases" not in item
        assert len(item["dimensions"]) == 5
        assert all(set(dimension) <= {"code", "label", "score", "source_mode"} for dimension in item["dimensions"])


def test_teacher_list_search_and_employment_filter_are_server_side() -> None:
    response = client.get(
        "/api/teachers",
        params={"keyword": "ana", "employment_status": "UNKNOWN"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["teacher_id"] for item in body["items"]] == ["T-1002"]
    assert body["filters"]["keyword"] == "ana"
    assert body["filters"]["employment_status"] == "unknown"


def test_teacher_options_are_full_lightweight_and_do_not_read_score_policy() -> None:
    response = client.get("/api/teacher-options")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 4
    assert [item["teacher_id"] for item in body] == ["T-1001", "T-1002", "T-1003", "T-1004"]
    assert all(
        set(item)
        == {
            "teacher_id",
            "name",
            "data_mode",
            "employment_status",
            "graduation_state",
            "task_issuance_blockers",
        }
        for item in body
    )
    assert all(item["task_issuance_blockers"] == [] for item in body)


def test_1069_teacher_list_projects_only_24_and_avoids_megabyte_response() -> None:
    with session_scope(engine) as session:
        for index in range(4, 1069):
            teacher_id = f"T-PAGE-{index + 1:04d}"
            session.add(
                TeacherRecord(
                    teacher_id=teacher_id,
                    camp_enrollment_id=f"CAMP-{teacher_id}",
                    name=f"Teacher {index + 1}",
                    country=None,
                    timezone="UTC",
                    camp_day=1,
                    graduation_state="IN_CAMP",
                    gold_qualified=False,
                    total_score=0,
                    graduation_threshold=100,
                    data_mode="MIXED",
                    source_snapshot_label=None,
                    payload={
                        "teacher_id": teacher_id,
                        "employment_status": "on",
                        "dimensions": [],
                    },
                )
            )
    response = client.get("/api/teachers")

    assert response.status_code == 200
    assert response.json()["total"] == 1069
    assert len(response.json()["items"]) == 24
    assert len(response.content) < 250_000

    options = client.get("/api/teacher-options")
    assert options.status_code == 200
    assert len(options.json()) == 30
    assert len(options.content) < 40_000

    searched_options = client.get(
        "/api/teacher-options",
        params={"keyword": "T-PAGE-1069", "limit": 30},
    )
    assert searched_options.status_code == 200
    assert [
        item["teacher_id"] for item in searched_options.json()
    ] == ["T-PAGE-1069"]


def test_teacher_list_rejects_page_sizes_above_100() -> None:
    response = client.get("/api/teachers?page_size=101")

    assert response.status_code == 422
    assert response.json()["field_path"] == "$.page_size"
