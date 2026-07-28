from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import engine, session_scope
from app.db_models import TeacherRecord
from app.main import app
from app.teacher_read_service import DashboardReadService


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
                    graduation_state="IN_PROGRESS",
                    gold_qualified=False,
                    total_score=0,
                    graduation_threshold=100,
                    data_mode="MIXED",
                    source_batch_id=None,
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


def test_teacher_list_rejects_page_sizes_above_100() -> None:
    response = client.get("/api/teachers?page_size=101")

    assert response.status_code == 422
    assert response.json()["field_path"] == "$.page_size"
