"""Technical Case ownership for DTS v2 Outbox failures and recoveries."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_outbox_worker import DtsV2OutboxEvent


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_REGIONS = frozenset({"dom", "ovs"})


class DtsV2TechnicalCaseError(RuntimeError):
    """A technical failure cannot be represented without unsafe evidence."""


def outbox_technical_case_identity_v2(
    event: DtsV2OutboxEvent,
) -> tuple[str, str, str]:
    """Return ``(case_id, case_type, source_ref)`` for one v2 event."""

    _require_event(event)
    revision = _aggregate_revision(event.payload)
    if event.aggregate_type == "TASK_PLAN":
        case_type = "TASK_MATERIALIZATION_DEAD"
        source_ref = (
            f"tech-case:task-plan:{event.aggregate_id}:r{revision}"
        )
    else:
        case_type = "DOWNSTREAM_PROJECTION_DEAD"
        source_ref = f"tech-case:projection:{event.event_id}"
    case_id = "v2case:" + hashlib.sha256(source_ref.encode("utf-8")).hexdigest()
    return case_id, case_type, source_ref


class DtsV2OutboxTechnicalCaseStore:
    """Persist Outbox DEAD_LETTER and recovery evidence in the caller tx."""

    def record_dead_letter(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
        *,
        error_code: str,
    ) -> None:
        _require_error_code(error_code)
        case_id, case_type, source_ref = outbox_technical_case_identity_v2(event)
        region, appoint_id, teacher_id = _safe_subject(event)
        aggregate_revision = _aggregate_revision(event.payload)
        result = connection.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case(
                    :case_id,:case_type,:source_ref,:teacher_id,
                    :source_region,:source_appoint_id,:error_code,
                    :aggregate_type,:aggregate_id,:aggregate_revision,
                    :event_id,:attempt_count,:recovery_count
                )
                """
            ),
            {
                "case_id": case_id,
                "case_type": case_type,
                "source_ref": source_ref,
                "teacher_id": teacher_id,
                "source_region": region,
                "source_appoint_id": appoint_id,
                "error_code": error_code,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "aggregate_revision": aggregate_revision,
                "event_id": event.event_id,
                "attempt_count": 8,
                "recovery_count": event.recovery_count,
            },
        ).scalar_one()
        if result not in {"CREATED", "UPDATED", "UNCHANGED"}:
            raise DtsV2TechnicalCaseError(
                "DTS_V2_TECHNICAL_CASE_DATABASE_RESULT_INVALID"
            )

    def resolve_after_success(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> None:
        case_id, _, source_ref = outbox_technical_case_identity_v2(event)
        result = connection.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case_recovery(
                    :case_id,:source_ref,:event_id,:recovery_count
                )
                """
            ),
            {
                "case_id": case_id,
                "source_ref": source_ref,
                "event_id": event.event_id,
                "recovery_count": event.recovery_count,
            },
        ).scalar_one()
        if result not in {
            "NOT_FOUND",
            "RESOLVED",
            "EVIDENCE_APPENDED",
            "UNCHANGED",
        }:
            raise DtsV2TechnicalCaseError(
                "DTS_V2_TECHNICAL_CASE_DATABASE_RESULT_INVALID"
            )


def _require_event(event: DtsV2OutboxEvent) -> None:
    if not isinstance(event, DtsV2OutboxEvent):
        raise DtsV2TechnicalCaseError("DTS_V2_OUTBOX_EVENT_REQUIRED")
    expected_event_type = (
        "task.materialization.requested.v2"
        if event.aggregate_type == "TASK_PLAN"
        else "source_wide.changed.v2"
    )
    if event.event_type != expected_event_type:
        raise DtsV2TechnicalCaseError(
            "DTS_V2_TECHNICAL_CASE_EVENT_TYPE_MISMATCH"
        )


def _aggregate_revision(payload: Mapping[str, Any]) -> int:
    value = payload.get("aggregate_revision")
    if type(value) is not int or value < 1:
        raise DtsV2TechnicalCaseError(
            "DTS_V2_TECHNICAL_CASE_AGGREGATE_REVISION_INVALID"
        )
    return value


def _safe_subject(
    event: DtsV2OutboxEvent,
) -> tuple[str | None, str | None, str | None]:
    key = event.payload.get("aggregate_key")
    if not isinstance(key, Mapping):
        raise DtsV2TechnicalCaseError(
            "DTS_V2_TECHNICAL_CASE_AGGREGATE_KEY_INVALID"
        )
    region = key.get("source_region")
    if region is not None and region not in _REGIONS:
        raise DtsV2TechnicalCaseError(
            "DTS_V2_TECHNICAL_CASE_REGION_INVALID"
        )
    appoint_id = key.get("source_appoint_id")
    teacher_id = key.get("teacher_id")
    for value in (appoint_id, teacher_id):
        if value is not None and (
            not isinstance(value, str)
            or not value
            or value.strip() != value
        ):
            raise DtsV2TechnicalCaseError(
                "DTS_V2_TECHNICAL_CASE_SUBJECT_INVALID"
            )
    return region, appoint_id, teacher_id


def _require_error_code(value: str) -> None:
    if not isinstance(value, str) or _ERROR_CODE.fullmatch(value) is None:
        raise DtsV2TechnicalCaseError(
            "DTS_V2_TECHNICAL_CASE_ERROR_CODE_INVALID"
        )


__all__ = [
    "DtsV2OutboxTechnicalCaseStore",
    "DtsV2TechnicalCaseError",
    "outbox_technical_case_identity_v2",
]
