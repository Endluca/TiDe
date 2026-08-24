from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.dts_v2_projection_cutover import (
    DtsV2ProjectionCutoverError,
    DtsV2ReconciliationEvidence,
    PostgresDtsV2ProjectionCutoverStore,
    RECONCILIATION_PROTOCOL,
    RECONCILIATION_RESULT_TYPES,
)


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one(self) -> Any:
        return self.value


class _Connection:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, parameters=None) -> _Result:
        self.calls.append((str(statement), dict(parameters or {})))
        return _Result(self.responses.pop(0))


def _result_manifest() -> list[dict[str, Any]]:
    return [
        {
            "result_type": result_type,
            "row_count": 0,
            "content_hash": "a" * 64,
        }
        for result_type in RECONCILIATION_RESULT_TYPES
    ]


def _evidence() -> DtsV2ReconciliationEvidence:
    manifest = _result_manifest()
    return DtsV2ReconciliationEvidence(
        base_control_version=3,
        base_route_version=2,
        target_projection_generation=2,
        source_profile_manifest_version=1,
        source_profile_manifest_sha256="b" * 64,
        source_profile_vector=(
            {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "source_schema_profile_id": "dts-source-schema:v2:" + "c" * 64,
            },
        ),
        source_fence_hash="d" * 64,
        v1_result_manifest=manifest,
        v2_result_manifest=manifest,
        legacy_output_manifest={"issue_count": 0},
        technical_gate_manifest={
            "full_reconciliation_provider": "AVAILABLE",
            "compat_dirty_provider": "AVAILABLE",
        },
        evaluation_as_of=datetime(2026, 8, 22, 8, tzinfo=timezone.utc),
    )


def test_evidence_request_is_canonical_and_contains_both_fail_closed_gates() -> None:
    request = _evidence().request()

    assert request["protocol_version"] == RECONCILIATION_PROTOCOL
    assert request["evaluation_as_of"] == "2026-08-22T08:00:00.000000Z"
    assert request["technical_gate_manifest"] == {
        "full_reconciliation_provider": "AVAILABLE",
        "compat_dirty_provider": "AVAILABLE",
    }
    assert [row["result_type"] for row in request["v2_result_manifest"]] == list(
        RECONCILIATION_RESULT_TYPES
    )


def test_preview_then_record_passes_exact_database_generated_request_hash() -> None:
    request = _evidence().request()
    expected_hash = hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    connection = _Connection(
        [
            request,
            {
                "status": "APPLIED",
                "run_id": "cutover-1",
                "request_hash": expected_hash,
            },
        ]
    )
    store = PostgresDtsV2ProjectionCutoverStore()

    preview = store.preview_reconciliation_evidence(
        connection,  # type: ignore[arg-type]
        evaluation_as_of=datetime(2026, 8, 22, 8, tzinfo=timezone.utc),
        source_profile_manifest_sha256="b" * 64,
    )
    result = store.record_reconciliation_request(
        connection,  # type: ignore[arg-type]
        run_id="cutover-1",
        request=preview,
    )

    assert result["status"] == "APPLIED"
    assert "preview_dts_v2_reconciliation_evidence_v1" in connection.calls[0][0]
    assert connection.calls[1][1]["request_hash"] == expected_hash


def test_switch_and_readiness_validate_database_response_shape() -> None:
    connection = _Connection(
        [
            {
                "status": "REPLAYED",
                "run_id": "cutover-1",
                "target_mode": "V2_PRIMARY",
            },
            {
                "ready": True,
                "code": "READY_V2_PRIMARY",
                "mode": "V2_PRIMARY",
                "active_projection": "V2",
                "projection_generation": 2,
                "control_row_version": 4,
                "route_row_version": 3,
                "time_catchup_status": "COMPLETE",
                "reconciliation_run_id": "cutover-1",
            },
        ]
    )
    store = PostgresDtsV2ProjectionCutoverStore()

    switched = store.switch_projection(
        connection,  # type: ignore[arg-type]
        run_id="cutover-1",
        expected_control_version=3,
        expected_route_version=2,
        target_mode="V2_PRIMARY",
    )
    readiness = store.read_readiness(connection)  # type: ignore[arg-type]

    assert switched["status"] == "REPLAYED"
    assert readiness.ready is True
    assert readiness.active_projection == "V2"
    assert readiness.projection_generation == 2
    assert readiness.fresh_start_run_id is None


def test_fresh_start_requires_event_only_v2_response() -> None:
    connection = _Connection(
        [
            {
                "status": "APPLIED",
                "run_id": "fresh-1",
                "target_mode": "V2_PRIMARY",
                "active_projection": "V2",
                "event_scope": "POST_H0_ONLY",
                "history_policy": "NO_BACKFILL",
                "qualification_grants_enabled": False,
            },
            {
                "ready": True,
                "code": "READY_V2_PRIMARY_FRESH",
                "mode": "V2_PRIMARY",
                "active_projection": "V2",
                "projection_generation": 1,
                "control_row_version": 2,
                "route_row_version": 2,
                "time_catchup_status": "COMPLETE",
                "reconciliation_run_id": None,
                "fresh_start_run_id": "fresh-1",
            },
        ]
    )
    store = PostgresDtsV2ProjectionCutoverStore()

    result = store.bootstrap_fresh_start(
        connection,  # type: ignore[arg-type]
        run_id="fresh-1",
        consumer_group="fresh-v2",
        routes=(
            {
                "source_region": "dom",
                "topic": "dom-topic",
                "partition_id": 0,
            },
        ),
        expected_vector_hash="a" * 64,
        source_profile_manifest_sha256="b" * 64,
    )
    readiness = store.read_readiness(connection)  # type: ignore[arg-type]

    assert result["status"] == "APPLIED"
    assert "bootstrap_dts_v2_primary_fresh_v1" in connection.calls[0][0]
    assert readiness.code == "READY_V2_PRIMARY_FRESH"
    assert readiness.reconciliation_run_id is None
    assert readiness.fresh_start_run_id == "fresh-1"


def test_naive_evaluation_time_and_unknown_switch_status_fail_closed() -> None:
    evidence = _evidence()
    naive = DtsV2ReconciliationEvidence(
        **{
            **evidence.__dict__,
            "evaluation_as_of": datetime(2026, 8, 22, 8),
        }
    )
    with pytest.raises(
        DtsV2ProjectionCutoverError,
        match="DTS_V2_RECONCILIATION_EVALUATION_TIME_INVALID",
    ):
        naive.request()

    with pytest.raises(
        DtsV2ProjectionCutoverError,
        match="DTS_V2_SWITCH_RESPONSE_STATUS_INVALID",
    ):
        PostgresDtsV2ProjectionCutoverStore().switch_projection(
            _Connection(
                [
                    {
                        "status": "UNKNOWN",
                        "run_id": "cutover-1",
                        "target_mode": "V2_PRIMARY",
                    }
                ]
            ),  # type: ignore[arg-type]
            run_id="cutover-1",
            expected_control_version=3,
            expected_route_version=2,
            target_mode="V2_PRIMARY",
        )
