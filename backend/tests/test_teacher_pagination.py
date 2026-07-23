from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.config_models import ConfigKey
from app.main import app, service
from app.store import store


client = TestClient(app)


def test_teacher_list_is_paged_filtered_and_lightweight(monkeypatch) -> None:
    policy_reads: list[ConfigKey] = []
    projection_calls: list[str] = []
    original_projection = service._project_teacher_scoring

    def read_config(key: ConfigKey):
        policy_reads.append(key)
        return None

    def project(teacher: dict, resolved_policy, score_account_overrides=None):
        projection_calls.append(teacher["teacher_id"])
        return original_projection(
            teacher,
            resolved_policy,
            score_account_overrides,
        )

    monkeypatch.setattr(service, "config_reader", read_config)
    monkeypatch.setattr(service, "_project_teacher_scoring", project)

    response = client.get("/api/teachers?page=1&page_size=2&data_mode=MOCK")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2
    assert projection_calls == [item["teacher_id"] for item in body["items"]]
    assert policy_reads == [ConfigKey.SCORE_GRADUATION]
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


def test_teacher_options_are_full_lightweight_and_do_not_read_score_policy(monkeypatch) -> None:
    def unexpected_config_read(_key: ConfigKey):
        raise AssertionError("teacher options must not read score configuration")

    monkeypatch.setattr(service, "config_reader", unexpected_config_read)
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


def test_1069_teacher_list_projects_only_24_and_avoids_megabyte_response(monkeypatch) -> None:
    template = deepcopy(store.teachers["T-1002"])
    teachers: dict[str, dict] = {}
    for index in range(1069):
        teacher = deepcopy(template)
        teacher_id = f"T-{index + 1:04d}"
        teacher.update(
            {
                "teacher_id": teacher_id,
                "name": f"Teacher {index + 1}",
                "data_mode": "MIXED",
                "employment_status": "on",
            }
        )
        teachers[teacher_id] = teacher
    store.teachers = teachers

    projection_calls: list[str] = []
    original_projection = service._project_teacher_scoring

    def project(teacher: dict, resolved_policy, score_account_overrides=None):
        projection_calls.append(teacher["teacher_id"])
        return original_projection(
            teacher,
            resolved_policy,
            score_account_overrides,
        )

    monkeypatch.setattr(service, "_project_teacher_scoring", project)
    response = client.get("/api/teachers")

    assert response.status_code == 200
    assert response.json()["total"] == 1069
    assert len(response.json()["items"]) == 24
    assert len(projection_calls) == 24
    assert len(response.content) < 250_000


def test_teacher_list_rejects_page_sizes_above_100() -> None:
    response = client.get("/api/teachers?page_size=101")

    assert response.status_code == 422
    assert response.json()["field_path"] == "$.page_size"
