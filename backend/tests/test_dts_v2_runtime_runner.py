from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import run_dts_v2_runtime as runner
from app.dts_v2_runtime_composition import DtsV2RuntimeHealthSnapshot


def _hash(character: str) -> str:
    return character * 64


def test_runner_validates_dedicated_role_target_and_safe_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TIT_V2_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setenv(
        "TIT_V2_DOMAIN_DATABASE_URL",
        "postgresql+psycopg://tit_dts_domain_projector_runtime:secret@"
        "db.invalid/tit_growth?sslmode=verify-full",
    )
    for name, character in (
        ("TIT_V2_SOURCE_PROFILE_MANIFEST_SHA256", "a"),
        ("TIT_V2_SOURCE_PARTITION_EPOCH_SHA256", "b"),
        ("TIT_V2_INITIAL_H0_VECTOR_SHA256", "c"),
        ("TIT_V2_CUTOVER_RUN_ID_SHA256", "d"),
    ):
        monkeypatch.setenv(name, _hash(character))

    assert runner._database_url("domain").endswith(
        "/tit_growth?sslmode=verify-full"
    )
    identity = runner._safe_identity()
    assert set(identity) == {
        "source_profile_manifest_sha256",
        "source_partition_epoch_sha256",
        "initial_h0_vector_sha256",
        "cutover_run_id_sha256",
    }
    assert "secret" not in str(identity)


def test_runtime_file_payload_contains_only_safe_aggregate_health() -> None:
    snapshot = SimpleNamespace(
        mode="V1_COMPAT_DUAL_CAPTURE",
        projection_generation=0,
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
        {
            "source_profile_manifest_sha256": _hash("a"),
            "source_partition_epoch_sha256": _hash("b"),
            "initial_h0_vector_sha256": _hash("c"),
            "cutover_run_id_sha256": _hash("d"),
        },
    )
    assert payload["mode"] == "V1_COMPAT_DUAL_CAPTURE"
    assert payload["projection_generation"] == 0
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
        f"postgresql+psycopg://tit_growth_app:{secret}@db.invalid/"
        "tit_growth?sslmode=verify-full",
    )
    with pytest.raises(
        runner.DtsV2RuntimeRunnerError,
        match="DTS_V2_RUNTIME_DATABASE_URL_ROLE_MISMATCH",
    ) as raised:
        runner._database_url("domain")
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    ("component", "mode", "expected"),
    [
        ("domain", "V1_COMPAT_DUAL_CAPTURE", True),
        ("domain", "ROLLED_BACK", True),
        ("outbox", "V1_COMPAT_DUAL_CAPTURE", False),
        ("favorite", "ROLLED_BACK", False),
        ("outbox", "V2_PRIMARY", True),
        ("favorite", "V2_PRIMARY", True),
    ],
)
def test_component_activity_follows_pipeline_mode(
    component: str,
    mode: str,
    expected: bool,
) -> None:
    assert runner._component_active(
        component,
        SimpleNamespace(mode=mode),
    ) is expected


def test_outbox_and_favorite_standby_are_database_ready() -> None:
    for component, mode in (
        ("outbox", "V1_COMPAT_DUAL_CAPTURE"),
        ("favorite", "ROLLED_BACK"),
    ):
        snapshot = SimpleNamespace(
            mode=mode,
            projection_generation=0,
        )
        assert runner._component_ready(component, snapshot) is True


def test_outbox_primary_requires_time_recheck_health() -> None:
    base = DtsV2RuntimeHealthSnapshot(
        protocol_version="dts-v2-outbox-runtime-health-v1",
        mode="V2_PRIMARY",
        projection_generation=4,
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
        projection_generation=4,
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


def test_outbox_dual_mode_stays_alive_without_building_or_running_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name, character in (
        ("TIT_V2_SOURCE_PROFILE_MANIFEST_SHA256", "a"),
        ("TIT_V2_SOURCE_PARTITION_EPOCH_SHA256", "b"),
        ("TIT_V2_INITIAL_H0_VECTOR_SHA256", "c"),
        ("TIT_V2_CUTOVER_RUN_ID_SHA256", "d"),
    ):
        monkeypatch.setenv(name, _hash(character))
    monkeypatch.setenv("TIT_V2_EXPECTED_DATABASE", "tit_growth")
    monkeypatch.setattr(runner, "_database_url", lambda _component: "ignored")

    class Engine:
        def dispose(self) -> None:
            pass

    engine = Engine()
    monkeypatch.setattr(runner, "build_engine", lambda *_a, **_k: engine)
    snapshot = SimpleNamespace(
        mode="V1_COMPAT_DUAL_CAPTURE",
        projection_generation=0,
        runnable_count=9,
        active_lease_count=0,
        expired_lease_count=0,
        business_wait_count=0,
        dead_count=0,
        stale_runnable_count=0,
        oldest_runnable_age_seconds=1,
        oldest_active_lease_age_seconds=None,
    )
    monkeypatch.setattr(runner, "_runtime_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(
        runner,
        "_build_worker",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("standby must not construct a worker")
        ),
    )
    monkeypatch.setattr(runner, "_STOP", False)
    heartbeat = tmp_path / "heartbeat"
    readiness = tmp_path / "readiness"
    args = SimpleNamespace(
        component="outbox",
        healthcheck=False,
        watch=False,
        interval_seconds=1.0,
        stale_after_seconds=900,
        max_heartbeat_age_seconds=90,
        max_readiness_age_seconds=90,
        heartbeat_path=heartbeat,
        readiness_path=readiness,
    )

    assert runner.run(args) == 0
    payload = json.loads(heartbeat.read_text(encoding="ascii"))
    assert payload["active"] is False
    assert payload["last_run_counts"] == {}
    assert readiness.exists()
