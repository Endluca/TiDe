"""Protected DTS v2 reconciliation and production read-route commands.

The module is deliberately a thin client.  PostgreSQL owns validation,
locking, idempotency, and the atomic control/route transition.  In
particular, this client never updates ``dts_pipeline_control`` or the stable
teacher read views directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection


RECONCILIATION_REGPROCEDURE = (
    "public.record_dts_v2_reconciliation_pass_v1(text,jsonb,text)"
)
SWITCH_REGPROCEDURE = (
    "public.switch_dts_projection_mode_v2(text,bigint,bigint,text)"
)
READINESS_REGPROCEDURE = "public.dts_projection_readiness_v1()"
PREVIEW_REGPROCEDURE = (
    "public.preview_dts_v2_reconciliation_evidence_v1(timestamptz,text)"
)
FRESH_START_REGPROCEDURE = (
    "public.bootstrap_dts_v2_primary_fresh_v1(text,text,jsonb,text,text)"
)
RECONCILIATION_PROTOCOL = "dts-v2-reconciliation-pass-v1"
ROUTE_CONTRACT_VERSION = "teacher-read-route-v1"

RECONCILIATION_RESULT_TYPES = (
    "TEACHER_WIDE",
    "LESSON_SCORE",
    "LESSON_COMPONENT_SETTLEMENT",
    "FAVORITE_OBSERVATION",
    "FAVORITE_ATTRIBUTION",
    "TRIGGER_MATCH",
    "TASK_PLAN",
    "CASE_PLAN",
    "NOTIFICATION_PLAN",
    "SCORE_ACCOUNT",
    "SCORE_COMPONENT_ACCOUNT",
    "SCORE_ENTRY_PLAN",
    "QUALIFICATION",
    "OUTBOX_COVERAGE",
)


class DtsV2ProjectionCutoverError(RuntimeError):
    """A cutover command or its protected response violated the contract."""


@dataclass(frozen=True)
class DtsV2ReconciliationEvidence:
    base_control_version: int
    base_route_version: int
    target_projection_generation: int
    source_profile_manifest_version: int
    source_profile_manifest_sha256: str
    source_profile_vector: Sequence[Mapping[str, Any]]
    source_fence_hash: str
    v1_result_manifest: Sequence[Mapping[str, Any]]
    v2_result_manifest: Sequence[Mapping[str, Any]]
    legacy_output_manifest: Mapping[str, Any]
    technical_gate_manifest: Mapping[str, Any]
    evaluation_as_of: datetime

    def request(self) -> dict[str, Any]:
        evaluation = self.evaluation_as_of
        if evaluation.tzinfo is None or evaluation.utcoffset() is None:
            _fail("DTS_V2_RECONCILIATION_EVALUATION_TIME_INVALID")
        evaluation_utc = evaluation.astimezone(timezone.utc)
        return {
            "protocol_version": RECONCILIATION_PROTOCOL,
            "base_control_version": self.base_control_version,
            "base_route_version": self.base_route_version,
            "target_projection_generation": (
                self.target_projection_generation
            ),
            "source_profile_manifest_version": (
                self.source_profile_manifest_version
            ),
            "source_profile_manifest_sha256": (
                self.source_profile_manifest_sha256
            ),
            "source_profile_vector": [
                dict(item) for item in self.source_profile_vector
            ],
            "source_fence_hash": self.source_fence_hash,
            "v1_result_manifest": [
                dict(item) for item in self.v1_result_manifest
            ],
            "v2_result_manifest": [
                dict(item) for item in self.v2_result_manifest
            ],
            "legacy_output_manifest": dict(self.legacy_output_manifest),
            "technical_gate_manifest": dict(self.technical_gate_manifest),
            "evaluation_as_of": evaluation_utc.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class DtsV2ProjectionReadiness:
    ready: bool
    code: str
    mode: str | None
    active_projection: str | None
    projection_generation: int | None
    control_row_version: int | None
    route_row_version: int | None
    time_catchup_status: str | None
    reconciliation_run_id: str | None
    fresh_start_run_id: str | None


class PostgresDtsV2ProjectionCutoverStore:
    """Invoke the database-owned reconciliation and route state machine."""

    def bootstrap_fresh_start(
        self,
        connection: Connection,
        *,
        run_id: str,
        consumer_group: str,
        routes: Sequence[Mapping[str, Any]],
        expected_vector_hash: str,
        source_profile_manifest_sha256: str,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                "SELECT public.bootstrap_dts_v2_primary_fresh_v1("
                ":run_id,:consumer_group,CAST(:routes AS jsonb),"
                ":vector_hash,:profile_sha256)"
            ),
            {
                "run_id": run_id,
                "consumer_group": consumer_group,
                "routes": json.dumps(
                    [dict(route) for route in routes],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                "vector_hash": expected_vector_hash,
                "profile_sha256": source_profile_manifest_sha256,
            },
        ).scalar_one()
        result = _mapping(value, "DTS_V2_FRESH_START_RESPONSE_INVALID")
        if result.get("run_id") != run_id:
            _fail("DTS_V2_FRESH_START_RESPONSE_IDENTITY_MISMATCH")
        if result.get("status") not in {"APPLIED", "REPLAYED"}:
            _fail("DTS_V2_FRESH_START_RESPONSE_STATUS_INVALID")
        if (
            result.get("target_mode") != "V2_PRIMARY"
            or result.get("active_projection") != "V2"
            or result.get("event_scope") != "POST_H0_ONLY"
            or result.get("history_policy") != "NO_BACKFILL"
            or result.get("qualification_grants_enabled") is not False
        ):
            _fail("DTS_V2_FRESH_START_RESPONSE_STATE_INVALID")
        return result

    def preview_reconciliation_evidence(
        self,
        connection: Connection,
        *,
        evaluation_as_of: datetime,
        source_profile_manifest_sha256: str,
    ) -> dict[str, Any]:
        if (
            evaluation_as_of.tzinfo is None
            or evaluation_as_of.utcoffset() is None
        ):
            _fail("DTS_V2_RECONCILIATION_EVALUATION_TIME_INVALID")
        value = connection.execute(
            text(
                "SELECT public.preview_dts_v2_reconciliation_evidence_v1("
                ":evaluation_as_of,:profile_sha256)"
            ),
            {
                "evaluation_as_of": evaluation_as_of,
                "profile_sha256": source_profile_manifest_sha256,
            },
        ).scalar_one()
        result = _mapping(value, "DTS_V2_RECONCILIATION_PREVIEW_INVALID")
        if result.get("protocol_version") != RECONCILIATION_PROTOCOL:
            _fail("DTS_V2_RECONCILIATION_PREVIEW_INVALID")
        return result

    def record_reconciliation_request(
        self,
        connection: Connection,
        *,
        run_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_value = dict(request)
        request_hash = _canonical_hash(request_value)
        value = connection.execute(
            text(
                "SELECT public.record_dts_v2_reconciliation_pass_v1("
                ":run_id,CAST(:request AS jsonb),:request_hash)"
            ),
            {
                "run_id": run_id,
                "request": _canonical_json(request_value),
                "request_hash": request_hash,
            },
        ).scalar_one()
        result = _mapping(value, "DTS_V2_RECONCILIATION_RESPONSE_INVALID")
        if (
            result.get("run_id") != run_id
            or result.get("request_hash") != request_hash
        ):
            _fail("DTS_V2_RECONCILIATION_RESPONSE_IDENTITY_MISMATCH")
        if result.get("status") not in {"APPLIED", "REPLAYED"}:
            _fail("DTS_V2_RECONCILIATION_RESPONSE_STATUS_INVALID")
        return result

    def record_reconciliation_pass(
        self,
        connection: Connection,
        *,
        run_id: str,
        evidence: DtsV2ReconciliationEvidence,
    ) -> dict[str, Any]:
        return self.record_reconciliation_request(
            connection,
            run_id=run_id,
            request=evidence.request(),
        )

    def switch_projection(
        self,
        connection: Connection,
        *,
        run_id: str,
        expected_control_version: int,
        expected_route_version: int,
        target_mode: str,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                "SELECT public.switch_dts_projection_mode_v2("
                ":run_id,:control_version,:route_version,:target_mode)"
            ),
            {
                "run_id": run_id,
                "control_version": expected_control_version,
                "route_version": expected_route_version,
                "target_mode": target_mode,
            },
        ).scalar_one()
        result = _mapping(value, "DTS_V2_SWITCH_RESPONSE_INVALID")
        if result.get("run_id") != run_id:
            _fail("DTS_V2_SWITCH_RESPONSE_IDENTITY_MISMATCH")
        if result.get("status") not in {"APPLIED", "REPLAYED"}:
            _fail("DTS_V2_SWITCH_RESPONSE_STATUS_INVALID")
        if result.get("target_mode") != target_mode:
            _fail("DTS_V2_SWITCH_RESPONSE_MODE_MISMATCH")
        return result

    def read_readiness(
        self,
        connection: Connection,
    ) -> DtsV2ProjectionReadiness:
        value = connection.execute(
            text("SELECT public.dts_projection_readiness_v1()")
        ).scalar_one()
        result = _mapping(value, "DTS_V2_READINESS_RESPONSE_INVALID")
        ready = result.get("ready")
        code = result.get("code")
        if type(ready) is not bool or not isinstance(code, str) or not code:
            _fail("DTS_V2_READINESS_RESPONSE_SHAPE_INVALID")
        integer_fields: dict[str, int | None] = {}
        for field in (
            "projection_generation",
            "control_row_version",
            "route_row_version",
        ):
            candidate = result.get(field)
            if candidate is not None and type(candidate) is not int:
                _fail("DTS_V2_READINESS_RESPONSE_SHAPE_INVALID")
            integer_fields[field] = candidate
        return DtsV2ProjectionReadiness(
            ready=ready,
            code=code,
            mode=_optional_string(result.get("mode")),
            active_projection=_optional_string(
                result.get("active_projection")
            ),
            projection_generation=integer_fields["projection_generation"],
            control_row_version=integer_fields["control_row_version"],
            route_row_version=integer_fields["route_row_version"],
            time_catchup_status=_optional_string(
                result.get("time_catchup_status")
            ),
            reconciliation_run_id=_optional_string(
                result.get("reconciliation_run_id")
            ),
            fresh_start_run_id=_optional_string(
                result.get("fresh_start_run_id")
            ),
        )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, error: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        _fail(error)
    return dict(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _fail("DTS_V2_READINESS_RESPONSE_SHAPE_INVALID")
    return value


def _fail(code: str) -> None:
    raise DtsV2ProjectionCutoverError(code)


__all__ = [
    "DtsV2ProjectionCutoverError",
    "DtsV2ProjectionReadiness",
    "DtsV2ReconciliationEvidence",
    "FRESH_START_REGPROCEDURE",
    "PostgresDtsV2ProjectionCutoverStore",
    "PREVIEW_REGPROCEDURE",
    "READINESS_REGPROCEDURE",
    "RECONCILIATION_PROTOCOL",
    "RECONCILIATION_REGPROCEDURE",
    "RECONCILIATION_RESULT_TYPES",
    "ROUTE_CONTRACT_VERSION",
    "SWITCH_REGPROCEDURE",
]
