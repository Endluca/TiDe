from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.dts_teacher_aggregate_v2 import TeacherSourceWideProjectionV2
from app.dts_v2_teacher_outbox_processor import TeacherMaterializationPlanV2
from app.dts_v2_teacher_time_recheck import (
    DtsV2TeacherTimeRecheckError,
    DtsV2TeacherTimeRecheckWorker,
    PostgresDtsV2TeacherTimeRecheckMaterializer,
    PostgresDtsV2TeacherTimeRecheckStore,
    TeacherTimeRecheckHealthV2,
)


class _Result:
    def __init__(self, *, scalar: Any = None, rows=()):
        self.scalar = scalar
        self.rows = list(rows)

    def scalar_one(self):
        return self.scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class _StoreConnection:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        if "claim_teacher_time_rechecks_v2" in sql:
            return _Result(
                rows=(
                    {
                        "teacher_id": "7",
                        "business_date_beijing": date(2026, 8, 31),
                        "lease_token": "lease-1",
                        "claimed_work_revision": 1,
                        "row_version": 2,
                    },
                )
            )
        if "enqueue_due_teacher_time_rechecks_v2" in sql:
            return _Result(scalar={"enqueued": 1, "superseded": 0})
        if "complete_teacher_time_recheck_v2" in sql:
            return _Result(scalar={"status": "COMPLETED", "row_version": 3})
        if "fail_teacher_time_recheck_v2" in sql:
            return _Result(scalar={"status": "RETRY", "row_version": 3})
        if "reap_expired_teacher_time_rechecks_v2" in sql:
            return _Result(scalar=2)
        if "dts_v2_teacher_time_recheck_health_v1" in sql:
            return _Result(
                scalar={
                    "protocol_version": (
                        "dts-v2-teacher-time-recheck-health-v1"
                    ),
                    "mode": "V2_PRIMARY",
                    "projection_generation": 9,
                    "schedule_due": False,
                    "current_date_missing_count": 0,
                    "runnable_count": 0,
                    "active_lease_count": 0,
                    "expired_lease_count": 0,
                    "dead_count": 0,
                    "stale_runnable_count": 0,
                    "oldest_runnable_age_seconds": None,
                }
            )
        raise AssertionError(sql)


def test_store_uses_only_protected_commands_and_parses_typed_claim() -> None:
    connection = _StoreConnection()
    store = PostgresDtsV2TeacherTimeRecheckStore()

    assert store.enqueue_due(connection) == {"enqueued": 1, "superseded": 0}
    claims = store.claim(
        connection,
        worker_id="time-recheck-1",
        batch_size=5,
        lease_seconds=120,
    )
    assert len(claims) == 1
    assert claims[0].business_date_beijing == date(2026, 8, 31)
    assert store.complete(connection, claims[0])["status"] == "COMPLETED"
    assert (
        store.fail(
            connection,
            claims[0],
            error_code="DOMAIN_PROJECTOR_TRANSIENT",
        )["status"]
        == "RETRY"
    )
    assert store.reap(connection, batch_size=5) == 2
    health = store.read_health(connection, stale_after_seconds=900)
    assert isinstance(health, TeacherTimeRecheckHealthV2)
    assert health.ready is True
    sql = "\n".join(statement for statement, _ in connection.calls).upper()
    assert "INSERT INTO" not in sql
    assert "UPDATE PUBLIC.DTS_DIRTY" not in sql
    assert "DELETE FROM" not in sql


class _Guard:
    def __init__(self, generation: int | None):
        self.generation = generation

    def acquire(self, connection, *, component):
        del connection
        assert component == "OUTBOX"
        return self.generation


class _MaterializerConnection:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.payload: dict[str, Any] | None = None

    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        if "materialize_teacher_time_recheck_v2" in sql:
            self.payload = json.loads(values["payload"])
            return _Result(
                scalar={
                    "teacher_source_changes": 1,
                    "teacher_identity_changes": 1,
                    "time_recheck_result_changes": 1,
                }
            )
        if "teacher_time_recheck_result_proof_v1" in sql:
            assert self.payload is not None
            return _Result(
                scalar={
                    "teacher_id": "7",
                    "business_date_beijing": "2026-08-31",
                    "dom_aggregate_revision": 11,
                    "ovs_aggregate_revision": 7,
                    "regional_state_sha256": {
                        "dom": "a" * 64,
                        "ovs": "b" * 64,
                    },
                    "projection_generation": 9,
                    "triggering_event_id": "time:7:2026-08-31",
                    "claimed_work_revision": 1,
                    "time_values": self.payload["time_values"],
                    "plan_sha256": "c" * 64,
                }
            )
        raise AssertionError(sql)


class _Scores:
    def __init__(self):
        self.call = None

    def refresh_teacher(self, connection, *, teacher_id, projection_generation):
        del connection
        self.call = (teacher_id, projection_generation)
        return {"score_account_changes": 1}


def _plan() -> TeacherMaterializationPlanV2:
    return TeacherMaterializationPlanV2(
        teacher_id="7",
        teacher_id_type="NUMERIC",
        business_date_beijing=date(2026, 8, 31),
        projection=TeacherSourceWideProjectionV2(
            values={
                "tchr_id": "7",
                "job_days": 30,
                "job_month": 2,
                "online_status": "EXISTING",
                "online_status_evidence_status": "CONFIRMED",
            },
            first_date_evidence={},
            metric_evidence={},
        ),
        regional_revisions={"dom": 11, "ovs": 7},
        regional_state_sha256={"dom": "a" * 64, "ovs": "b" * 64},
    )


def test_materializer_persists_business_date_proof_then_refreshes_score() -> None:
    connection = _MaterializerConnection()
    scores = _Scores()
    result = PostgresDtsV2TeacherTimeRecheckMaterializer(
        score_refresher=scores,
        primary_guard=_Guard(9),  # type: ignore[arg-type]
    ).apply_teacher_plan(
        connection,  # type: ignore[arg-type]
        _plan(),
        triggering_event_id="time:7:2026-08-31",
        claimed_work_revision=1,
    )

    assert result == {
        "teacher_source_changes": 1,
        "teacher_identity_changes": 1,
        "time_recheck_result_changes": 1,
        "score_account_changes": 1,
    }
    assert connection.payload is not None
    assert connection.payload["business_date_beijing"] == "2026-08-31"
    assert connection.payload["time_values"] == {
        "job_days": 30,
        "job_month": 2,
        "online_status": "EXISTING",
        "online_status_evidence_status": "CONFIRMED",
    }
    assert scores.call == ("7", 9)


class _NeverStore:
    def enqueue_due(self, connection):
        raise AssertionError("standby must not enqueue")


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    @contextmanager
    def begin(self):
        yield object()


def test_worker_is_intentional_standby_outside_primary() -> None:
    worker = DtsV2TeacherTimeRecheckWorker(
        _Engine(),  # type: ignore[arg-type]
        worker_id="time-recheck-1",
        materializer=object(),  # type: ignore[arg-type]
        store=_NeverStore(),  # type: ignore[arg-type]
        primary_guard=_Guard(None),  # type: ignore[arg-type]
    )

    assert worker.run_once(max_claims=5) == {
        "active": False,
        "enqueued": 0,
        "superseded": 0,
        "reaped": 0,
        "claimed": 0,
        "completed": 0,
        "retries": 0,
        "dead": 0,
        "materialization_counts": {},
    }


def test_materializer_rejects_unproved_regional_hash() -> None:
    invalid = _plan()
    invalid = TeacherMaterializationPlanV2(
        teacher_id=invalid.teacher_id,
        teacher_id_type=invalid.teacher_id_type,
        business_date_beijing=invalid.business_date_beijing,
        projection=invalid.projection,
        regional_revisions=invalid.regional_revisions,
        regional_state_sha256={"dom": "not-a-hash", "ovs": "b" * 64},
    )
    with pytest.raises(
        DtsV2TeacherTimeRecheckError,
        match="REGIONAL_VECTOR_INVALID",
    ):
        PostgresDtsV2TeacherTimeRecheckMaterializer(
            score_refresher=_Scores(),
            primary_guard=_Guard(9),  # type: ignore[arg-type]
        ).apply_teacher_plan(
            _MaterializerConnection(),  # type: ignore[arg-type]
            invalid,
            triggering_event_id="time:7:2026-08-31",
            claimed_work_revision=1,
        )
