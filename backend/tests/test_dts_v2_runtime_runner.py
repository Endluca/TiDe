from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import run_dts_v2_runtime as runner
from app.dts_v2_runtime_composition import DtsV2RuntimeHealthSnapshot


def test_runner_validates_dedicated_role_target_and_fixed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TIT_V2_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setenv(
        "TIT_V2_DOMAIN_DATABASE_URL",
        "postgresql+psycopg://tit_growth_app:secret@"
        "db.invalid/tit_growth?sslmode=verify-full",
    )
    assert runner._database_url("domain").endswith(
        "/tit_growth?sslmode=verify-full"
    )
    identity = runner._safe_identity()
    assert identity == {"pipeline_contract": "single-event-pipeline-v1"}
    assert "secret" not in str(identity)


def test_runtime_file_payload_contains_only_safe_aggregate_health() -> None:
    snapshot = SimpleNamespace(
        mode="V2_PRIMARY",
        projection_generation=1,
        runnable_count=1,
        active_lease_count=2,
        expired_lease_count=0,
        business_wait_count=3,
        dead_count=0,
        stale_runnable_count=0,
        oldest_runnable_age_seconds=4,
        oldest_active_lease_age_seconds=5,
    )
    payload = runner._safe_payload(
        "domain",
        snapshot,
        {"pipeline_contract": "single-event-pipeline-v1"},
    )
    assert payload["mode"] == "V2_PRIMARY"
    assert payload["projection_generation"] == 1
    assert payload["active"] is True
    assert not any(
        token in str(payload).lower()
        for token in ("password", "database_url", "source_appoint_id")
    )


def test_wrong_database_role_fails_without_echoing_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-print-this"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TIT_V2_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setenv(
        "TIT_V2_DOMAIN_DATABASE_URL",
        f"postgresql+psycopg://tit_teacher_crud:{secret}@db.invalid/"
        "tit_growth?sslmode=verify-full",
    )
    with pytest.raises(
        runner.DtsV2RuntimeRunnerError,
        match="DTS_V2_RUNTIME_DATABASE_URL_ROLE_MISMATCH",
    ) as raised:
        runner._database_url("domain")
    assert secret not in str(raised.value)


def test_unexpected_runtime_error_exposes_only_safe_code_or_sqlstate() -> None:
    secret = "password=must-not-leak"
    coded = RuntimeError(
        f"{secret} DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED detail"
    )
    assert runner._safe_unexpected_error(coded) == (
        "DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED"
    )

    class DatabaseError(RuntimeError):
        def __init__(self) -> None:
            super().__init__(secret)
            self.orig = SimpleNamespace(
                sqlstate="42501",
                __str__=lambda _self: secret,
            )

    fallback = runner._safe_unexpected_error(DatabaseError())
    assert fallback == "DTS_V2_RUNTIME_UNEXPECTED:DatabaseError:42501"
    assert secret not in fallback


@pytest.mark.parametrize(
    ("mode", "generation", "expected"),
    [
        ("V2_PRIMARY", 1, True),
        ("V2_PRIMARY", 2, False),
        ("V1_COMPAT_DUAL_CAPTURE", 0, False),
        ("ROLLED_BACK", 1, False),
    ],
)
def test_component_activity_follows_pipeline_mode(
    mode: str,
    generation: int,
    expected: bool,
) -> None:
    assert runner._component_active(
        "domain",
        SimpleNamespace(mode=mode, projection_generation=generation),
    ) is expected


def test_outbox_primary_requires_time_recheck_health() -> None:
    base = DtsV2RuntimeHealthSnapshot(
        protocol_version="dts-v2-outbox-runtime-health-v1",
        mode="V2_PRIMARY",
        projection_generation=1,
        runnable_count=0,
        active_lease_count=0,
        expired_lease_count=0,
        business_wait_count=0,
        dead_count=0,
        stale_runnable_count=0,
        oldest_runnable_age_seconds=None,
        oldest_active_lease_age_seconds=None,
    )
    health = runner.TeacherTimeRecheckHealthV2(
        protocol_version="dts-v2-teacher-time-recheck-health-v1",
        mode="V2_PRIMARY",
        projection_generation=1,
        schedule_due=False,
        current_date_missing_count=0,
        runnable_count=0,
        active_lease_count=0,
        expired_lease_count=0,
        dead_count=0,
        stale_runnable_count=0,
        oldest_runnable_age_seconds=None,
    )
    snapshot = runner._OutboxRuntimeSnapshot(base, health)
    assert runner._component_ready("outbox", snapshot) is True
    not_scheduled = runner._OutboxRuntimeSnapshot(
        base,
        runner.TeacherTimeRecheckHealthV2(
            **{
                **health.__dict__,
                "schedule_due": True,
            }
        ),
    )
    assert runner._component_ready("outbox", not_scheduled) is False


def test_outbox_runs_event_and_clock_driven_workers_together() -> None:
    class Worker:
        outbox = SimpleNamespace(
            run_once=lambda *, max_events: {
                "claimed": max_events,
                "failed": 0,
            }
        )
        teacher_time_recheck = SimpleNamespace(
            run_once=lambda *, max_claims: {
                "claimed": max_claims - 1,
                "failed": 0,
            }
        )

    assert runner._run_worker_once("outbox", Worker(), 3) == {
        "outbox_claimed": 3,
        "outbox_failed": 0,
        "teacher_time_recheck_claimed": 2,
        "teacher_time_recheck_failed": 0,
    }


def test_qualification_gate_env_is_exact_and_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED", "false"
    )
    assert runner._qualification_grants_enabled_from_env() is False
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED", "true"
    )
    assert runner._qualification_grants_enabled_from_env() is True
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED", "1"
    )
    with pytest.raises(
        runner.DtsV2RuntimeRunnerError,
        match="TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED_INVALID",
    ):
        runner._qualification_grants_enabled_from_env()
