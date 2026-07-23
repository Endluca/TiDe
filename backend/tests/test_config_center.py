from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    SCORE_POLICY_V2_PAYLOAD,
    SCORE_POLICY_V3_PAYLOAD,
    SCORE_POLICY_V4_PAYLOAD,
    SCORE_POLICY_V5_PAYLOAD,
    SCORE_POLICY_V6_PAYLOAD,
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
)
from app.config_service import (
    ConfigDomainError,
    ConfigService,
    is_agent_effectively_enabled,
    seed_default_configs,
    validate_config_payload,
)
from app.config_routes import get_config_service, resolve_config_actor, router
from app.auth import OperatorIdentity, current_operator
from app.auth_models import OperatorRole
from app.database import Base
from scripts.upgrade_score_config_v2 import upgrade_score_config_v2
from scripts.upgrade_score_config_v3 import upgrade_score_config_v3
from scripts.upgrade_score_config_v4 import upgrade_score_config_v4
from scripts.upgrade_score_config_v5 import upgrade_score_config_v5
from scripts.upgrade_score_config_v6 import upgrade_score_config_v6


@pytest.fixture()
def service() -> ConfigService:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return ConfigService(factory)


def _draft(service: ConfigService, key: ConfigKey, payload: dict) -> dict:
    return service.create_draft(key, actor_id="ops-creator", payload=payload)


def test_empty_database_does_not_implicitly_seed(service: ConfigService) -> None:
    assert service.list_versions() == []
    assert service.get_published_payload(ConfigKey.SCORE_GRADUATION) is None


def test_default_payloads_match_frozen_v6_score_contract() -> None:
    score = DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]
    assert score["policy_version"] == "v6"
    assert score["scoring_items"] == {
        "capacity": {
            "milestone_id": "CAPACITY_PEAK_SLOT_40",
            "metric": "peak_slot_cnt",
            "operator": "GTE",
            "threshold": 40,
            "score_value": 10,
            "maximum_points": 10,
            "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
        },
        "new_teacher_tasks": {"maximum_points": 30},
        "feedback_praise": {"points_per_unit": 5},
        "feedback_favorite": {"points_per_unit": 5},
        "feedback_rebook_15d": {"points_per_unit": 8},
        "reliability_on_time": {"points_per_unit": 2},
        "reliability_peak": {"points_per_unit": 1},
        "classroom_quality": {
            "metric": "perfect_cnt",
            "points_per_unit": 1.6,
            "source_mode": "REAL_TEACHER_SNAPSHOT",
        },
    }
    assert score["thresholds"] == {
        "graduation_raw_score": 100,
        "gold_raw_score": 200,
        "graduation_external_score": 100,
        "gold_external_score": 200,
    }
    assert score["hard_gates"]["graduation"] == {
        "required_mandatory_task_count": 10,
        "maximum_l0_complaint_count": 0,
    }
    assert score["hard_gates"]["gold"] == {"inherits_graduation": True}
    assert score["graduation_effect"] == "IMMEDIATE_ON_CRITERIA"

    agent = DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY]
    assert agent == {
        "enabled": True,
        "kill_switch": False,
        "max_primary_tasks": 1,
        "max_secondary_tasks": 2,
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "allow_task_invention": False,
    }
    delivery = DEFAULT_CONFIG_PAYLOADS[ConfigKey.DELIVERY_POLICY]
    assert delivery == {
        "normal_reminder_minutes_before_due": 60,
        "urgent_reminder_minutes_before_due": 15,
        "p0_response_window_minutes": 120,
        "p0_reminder_minutes_before_response_due": 30,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["thresholds"].update(gold_raw_score=100),
        lambda payload: payload["thresholds"].update(
            graduation_external_score=300,
            gold_external_score=400,
        ),
        lambda payload: payload["hard_gates"]["graduation"].update(
            required_mandatory_task_count=9
        ),
        lambda payload: payload["hard_gates"]["graduation"].update(
            maximum_l0_complaint_count=1
        ),
        lambda payload: payload["hard_gates"]["gold"].update(
            inherits_graduation=False
        ),
        lambda payload: payload["scoring_items"]["classroom_quality"].update(
            points_per_unit=2
        ),
        lambda payload: payload["scoring_items"]["feedback_praise"].update(points_per_unit=6),
        lambda payload: payload["scoring_items"]["capacity"].update(maximum_points=11),
        lambda payload: payload["scoring_items"]["capacity"].update(threshold=39),
        lambda payload: payload["scoring_items"]["capacity"].update(
            settlement_mode="REVERSIBLE"
        ),
        lambda payload: payload.update(graduation_effect="AFTER_SETTLEMENT_WINDOW"),
    ],
)
def test_invalid_v6_score_policy_is_rejected_before_storage(service: ConfigService, mutate) -> None:
    payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION])
    mutate(payload)

    with pytest.raises(ConfigDomainError) as caught:
        _draft(service, ConfigKey.SCORE_GRADUATION, payload)

    assert caught.value.error_code == "CONFIG_PAYLOAD_REJECTED"
    assert service.list_versions() == []


def test_unknown_or_secret_config_fields_never_enter_database(service: ConfigService) -> None:
    payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY])
    payload["OPENAI_API_KEY"] = "not-a-real-secret"

    with pytest.raises(ConfigDomainError) as caught:
        _draft(service, ConfigKey.AGENT_POLICY, payload)

    assert caught.value.error_code == "CONFIG_PAYLOAD_REJECTED"
    assert service.list_versions() == []

    clean = _draft(
        service,
        ConfigKey.AGENT_POLICY,
        deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY]),
    )
    secret_in_model = deepcopy(clean["payload"])
    secret_in_model["model"] = "sk-" + "credential-shaped-test-value-" + "x" * 24
    with pytest.raises(ConfigDomainError) as update_error:
        service.update_draft(clean["version_id"], secret_in_model, actor_id="ops-creator")

    assert update_error.value.error_code == "CONFIG_PAYLOAD_REJECTED"
    assert service.get_version(clean["version_id"])["payload"]["model"] == "gpt-5.6-terra"


def test_agent_kill_switch_always_disables_agent() -> None:
    payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY])
    payload.update(enabled=True, kill_switch=True, provider="openai")

    normalized = validate_config_payload(ConfigKey.AGENT_POLICY, payload)

    assert normalized["allow_task_invention"] is False
    assert normalized["max_primary_tasks"] == 1
    assert normalized["max_secondary_tasks"] <= 2
    assert is_agent_effectively_enabled(normalized) is False


def test_creator_cannot_publish_high_impact_config(service: ConfigService) -> None:
    draft = _draft(
        service,
        ConfigKey.DELIVERY_POLICY,
        deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.DELIVERY_POLICY]),
    )
    validation = service.validate_version(draft["version_id"], actor_id="ops-creator")
    assert validation["valid"] is True

    with pytest.raises(ConfigDomainError) as caught:
        service.publish_version(draft["version_id"], actor_id="ops-creator")

    assert caught.value.error_code == "FOUR_EYES_REQUIRED"
    assert service.get_version(draft["version_id"])["status"] == ConfigStatus.VALIDATED.value


def test_publishing_new_version_retires_but_never_overwrites_history(service: ConfigService) -> None:
    first = _draft(
        service,
        ConfigKey.DELIVERY_POLICY,
        deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.DELIVERY_POLICY]),
    )
    assert service.validate_version(first["version_id"], actor_id="ops-creator")["valid"]
    first_published = service.publish_version(first["version_id"], actor_id="ops-publisher")
    original_payload = deepcopy(first_published["payload"])

    second = service.create_draft(
        ConfigKey.DELIVERY_POLICY,
        actor_id="ops-creator-2",
        from_version_id=first["version_id"],
    )
    changed_payload = deepcopy(second["payload"])
    changed_payload["normal_reminder_minutes_before_due"] = 720
    service.update_draft(second["version_id"], changed_payload, actor_id="ops-creator-2")
    assert service.validate_version(second["version_id"], actor_id="ops-validator-2")["valid"]
    second_published = service.publish_version(second["version_id"], actor_id="ops-publisher-2")

    first_after = service.get_version(first["version_id"])
    assert first_after["status"] == ConfigStatus.RETIRED.value
    assert first_after["payload"] == original_payload
    assert second_published["status"] == ConfigStatus.PUBLISHED.value
    assert second_published["payload"]["normal_reminder_minutes_before_due"] == 720

    with pytest.raises(ConfigDomainError) as caught:
        service.update_draft(first["version_id"], changed_payload, actor_id="ops-creator-2")
    assert caught.value.error_code == "CONFIG_NOT_EDITABLE"


def test_published_v6_cannot_be_downgraded_to_historical_v2_or_v3(service: ConfigService) -> None:
    current = _draft(
        service,
        ConfigKey.SCORE_GRADUATION,
        deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]),
    )
    assert service.validate_version(current["version_id"], actor_id="ops-validator")["valid"]
    service.publish_version(current["version_id"], actor_id="ops-publisher")

    historical = _draft(
        service,
        ConfigKey.SCORE_GRADUATION,
        deepcopy(SCORE_POLICY_V2_PAYLOAD),
    )
    assert service.validate_version(historical["version_id"], actor_id="ops-validator-2")["valid"]
    with pytest.raises(ConfigDomainError) as caught:
        service.publish_version(historical["version_id"], actor_id="ops-publisher-2")

    assert caught.value.error_code == "SCORE_POLICY_DOWNGRADE_FORBIDDEN"
    assert service.get_version(current["version_id"])["status"] == ConfigStatus.PUBLISHED.value

    historical_v3 = _draft(
        service,
        ConfigKey.SCORE_GRADUATION,
        deepcopy(SCORE_POLICY_V3_PAYLOAD),
    )
    assert service.validate_version(historical_v3["version_id"], actor_id="ops-validator-3")[
        "valid"
    ]
    with pytest.raises(ConfigDomainError) as v3_caught:
        service.publish_version(historical_v3["version_id"], actor_id="ops-publisher-3")
    assert v3_caught.value.error_code == "SCORE_POLICY_DOWNGRADE_FORBIDDEN"


def test_published_policy_cannot_be_removed_without_replacement(service: ConfigService) -> None:
    draft = _draft(
        service,
        ConfigKey.AGENT_POLICY,
        deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY]),
    )
    assert service.validate_version(draft["version_id"], actor_id="ops-creator")["valid"]
    published = service.publish_version(draft["version_id"], actor_id="ops-publisher")

    with pytest.raises(ConfigDomainError) as caught:
        service.retire_version(published["version_id"], actor_id="ops-publisher")

    assert caught.value.error_code == "PUBLISHED_CONFIG_REPLACEMENT_REQUIRED"
    assert service.get_published_payload(ConfigKey.AGENT_POLICY) == published["payload"]


def test_explicit_seed_publishes_three_domains_with_separate_actors(service: ConfigService) -> None:
    result = seed_default_configs(
        session_factory=service.session_factory,
        creator_actor_id="seed-creator",
        publisher_actor_id="seed-publisher",
    )

    assert len(result["created"]) == 3
    assert result["skipped"] == []
    published = service.list_versions(status=ConfigStatus.PUBLISHED)
    assert {item["config_key"] for item in published} == {item.value for item in ConfigKey}
    assert all(item["created_by"] != item["published_by"] for item in published)

    with service.session_factory() as session:
        assert session.query(ConfigVersionRecord).count() == 3


def test_local_score_v2_upgrade_retires_legacy_publication_and_is_idempotent(
    service: ConfigService,
) -> None:
    with service.session_factory() as session, session.begin():
        session.add(
            ConfigVersionRecord(
                version_id="CFG-SCORE_GRADUATION-LEGACY",
                config_key=ConfigKey.SCORE_GRADUATION.value,
                version_number=1,
                status=ConfigStatus.PUBLISHED.value,
                high_impact=True,
                payload={
                    "dimensions": {
                        key: {"cap": 20, "weight": 0.2, "minimum_score": 0}
                        for key in (
                            "reliability",
                            "user_feedback",
                            "classroom_quality",
                            "capacity",
                            "new_teacher_tasks",
                        )
                    },
                    "total_threshold": 80,
                    "settlement_window_hours": 72,
                },
                validation_errors=[],
                created_by="legacy-creator",
                updated_by="legacy-publisher",
                published_by="legacy-publisher",
            )
        )

    upgraded = upgrade_score_config_v2(service=service, app_env="test")
    repeated = upgrade_score_config_v2(service=service, app_env="test")

    assert upgraded["status"] == "UPGRADED"
    assert repeated == {"status": "SKIPPED_ALREADY_V2", "policy_version": "v2"}
    versions = service.list_versions(ConfigKey.SCORE_GRADUATION)
    assert [(item["version_number"], item["status"]) for item in versions] == [
        (2, ConfigStatus.PUBLISHED.value),
        (1, ConfigStatus.RETIRED.value),
    ]
    assert versions[0]["payload"] == validate_config_payload(
        ConfigKey.SCORE_GRADUATION, SCORE_POLICY_V2_PAYLOAD
    )
    assert versions[0]["created_by"] != versions[0]["published_by"]


def test_score_v2_upgrade_refuses_nonlocal_environment(service: ConfigService) -> None:
    with pytest.raises(RuntimeError, match="outside local/dev/test"):
        upgrade_score_config_v2(service=service, app_env="production")


def test_local_score_v3_upgrade_retires_v2_without_rewriting_history_and_is_idempotent(
    service: ConfigService,
) -> None:
    historical = upgrade_score_config_v2(service=service, app_env="test")
    historical_payload = deepcopy(
        service.get_version(historical["version_id"])["payload"]
    )

    upgraded = upgrade_score_config_v3(service=service, app_env="test")
    repeated = upgrade_score_config_v3(service=service, app_env="test")

    assert upgraded["status"] == "UPGRADED"
    assert upgraded["policy_version"] == "v3"
    assert repeated == {"status": "SKIPPED_ALREADY_V3", "policy_version": "v3"}
    versions = service.list_versions(ConfigKey.SCORE_GRADUATION)
    assert [(item["version_number"], item["status"]) for item in versions] == [
        (2, ConfigStatus.PUBLISHED.value),
        (1, ConfigStatus.RETIRED.value),
    ]
    assert versions[0]["payload"] == validate_config_payload(
        ConfigKey.SCORE_GRADUATION,
        SCORE_POLICY_V3_PAYLOAD,
    )
    assert service.get_version(historical["version_id"])["payload"] == historical_payload


def test_score_v3_upgrade_refuses_nonlocal_environment(service: ConfigService) -> None:
    with pytest.raises(RuntimeError, match="outside local/dev/test"):
        upgrade_score_config_v3(service=service, app_env="production")


def test_local_score_v4_upgrade_retires_v3_without_rewriting_history_and_is_idempotent(
    service: ConfigService,
) -> None:
    historical = upgrade_score_config_v3(service=service, app_env="test")
    historical_payload = deepcopy(service.get_version(historical["version_id"])["payload"])

    upgraded = upgrade_score_config_v4(service=service, app_env="test")
    repeated = upgrade_score_config_v4(service=service, app_env="test")

    assert upgraded["status"] == "UPGRADED"
    assert upgraded["policy_version"] == "v4"
    assert repeated == {"status": "SKIPPED_ALREADY_V4", "policy_version": "v4"}
    versions = service.list_versions(ConfigKey.SCORE_GRADUATION)
    assert [(item["version_number"], item["status"]) for item in versions] == [
        (2, ConfigStatus.PUBLISHED.value),
        (1, ConfigStatus.RETIRED.value),
    ]
    assert versions[0]["payload"] == validate_config_payload(
        ConfigKey.SCORE_GRADUATION,
        SCORE_POLICY_V4_PAYLOAD,
    )
    assert service.get_version(historical["version_id"])["payload"] == historical_payload


def test_score_v4_upgrade_refuses_nonlocal_environment(service: ConfigService) -> None:
    with pytest.raises(RuntimeError, match="outside local/dev/test"):
        upgrade_score_config_v4(service=service, app_env="production")


def test_local_score_v5_upgrade_retires_v4_without_rewriting_history_and_is_idempotent(
    service: ConfigService,
) -> None:
    historical = upgrade_score_config_v4(service=service, app_env="test")
    historical_payload = deepcopy(service.get_version(historical["version_id"])["payload"])

    upgraded = upgrade_score_config_v5(service=service, app_env="test")
    repeated = upgrade_score_config_v5(service=service, app_env="test")

    assert upgraded["status"] == "UPGRADED"
    assert upgraded["policy_version"] == "v5"
    assert repeated == {"status": "SKIPPED_ALREADY_V5", "policy_version": "v5"}
    versions = service.list_versions(ConfigKey.SCORE_GRADUATION)
    assert [(item["version_number"], item["status"]) for item in versions] == [
        (2, ConfigStatus.PUBLISHED.value),
        (1, ConfigStatus.RETIRED.value),
    ]
    assert versions[0]["payload"] == validate_config_payload(
        ConfigKey.SCORE_GRADUATION,
        SCORE_POLICY_V5_PAYLOAD,
    )
    assert service.get_version(historical["version_id"])["payload"] == historical_payload


def test_score_v5_upgrade_refuses_nonlocal_environment(service: ConfigService) -> None:
    with pytest.raises(RuntimeError, match="outside local/dev/test"):
        upgrade_score_config_v5(service=service, app_env="production")


def test_local_score_v6_upgrade_retires_v5_without_rewriting_history_and_is_idempotent(
    service: ConfigService,
) -> None:
    historical = upgrade_score_config_v5(service=service, app_env="test")
    historical_payload = deepcopy(service.get_version(historical["version_id"])["payload"])

    upgraded = upgrade_score_config_v6(service=service, app_env="test")
    repeated = upgrade_score_config_v6(service=service, app_env="test")

    assert upgraded["status"] == "UPGRADED"
    assert upgraded["policy_version"] == "v6"
    assert repeated == {"status": "SKIPPED_ALREADY_V6", "policy_version": "v6"}
    versions = service.list_versions(ConfigKey.SCORE_GRADUATION)
    assert [(item["version_number"], item["status"]) for item in versions] == [
        (2, ConfigStatus.PUBLISHED.value),
        (1, ConfigStatus.RETIRED.value),
    ]
    assert versions[0]["payload"] == validate_config_payload(
        ConfigKey.SCORE_GRADUATION,
        SCORE_POLICY_V6_PAYLOAD,
    )
    assert service.get_version(historical["version_id"])["payload"] == historical_payload


def test_score_v6_upgrade_refuses_nonlocal_environment(service: ConfigService) -> None:
    with pytest.raises(RuntimeError, match="outside local/dev/test"):
        upgrade_score_config_v6(service=service, app_env="production")


def test_config_api_uses_trusted_actor_and_rejects_actor_in_body(service: ConfigService) -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_config_service] = lambda: service
    app.dependency_overrides[current_operator] = lambda: OperatorIdentity(
        operator_id="ops-creator",
        username="ops.creator",
        display_name="Creator",
        roles=[OperatorRole.CONFIG_PUBLISHER],
    )
    app.dependency_overrides[resolve_config_actor] = lambda: "ops-creator"
    client = TestClient(app)
    payload = deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.AGENT_POLICY])

    spoofed = client.post(
        "/api/configs/drafts",
        json={"config_key": "AGENT_POLICY", "payload": payload, "actor_id": "spoofed-admin"},
    )
    assert spoofed.status_code == 422

    draft = client.post(
        "/api/configs/drafts",
        json={"config_key": "AGENT_POLICY", "payload": payload},
    )
    assert draft.status_code == 201
    version_id = draft.json()["version_id"]
    assert draft.json()["created_by"] == "ops-creator"
    assert client.post(f"/api/configs/{version_id}/validate").status_code == 200

    same_actor_publish = client.post(f"/api/configs/{version_id}/publish")
    assert same_actor_publish.status_code == 409
    assert same_actor_publish.json()["detail"]["error_code"] == "FOUR_EYES_REQUIRED"

    app.dependency_overrides[resolve_config_actor] = lambda: "ops-publisher"
    published = client.post(f"/api/configs/{version_id}/publish")
    assert published.status_code == 200
    assert published.json()["published_by"] == "ops-publisher"
