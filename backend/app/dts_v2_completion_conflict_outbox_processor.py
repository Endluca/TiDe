"""Materialize the single operational Case for a completion conflict."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_aggregate_state_reader import DtsV2AggregateStateReader
from .dts_v2_outbox_worker import DtsV2OutboxEvent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset(
    {
        "NONE",
        "PENDING",
        "RESOLVED_KEEP",
        "RESOLVED_UPDATE",
        "RESOLVED_TRANSFER",
        "RESOLVED_VOID",
    }
)
_OUTCOMES = frozenset(
    {"CREATED", "UPDATED", "UNCHANGED", "NOT_PENDING", "SHADOW_ONLY"}
)


class DtsV2CompletionConflictOutboxProcessorError(RuntimeError):
    """The conflict aggregate or protected command response is unsafe."""


class DtsV2CompletionConflictOutboxProcessor:
    """Validate current state and call the sole protected Case command.

    The PostgreSQL command re-reads and locks the authoritative COURSE and
    source-course rows.  The aggregate snapshot is still checked here so a
    malformed or future Outbox event can never be acknowledged.
    """

    def __init__(
        self,
        *,
        aggregate_reader: DtsV2AggregateStateReader | None = None,
    ) -> None:
        self.aggregate_reader = aggregate_reader or DtsV2AggregateStateReader()

    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]:
        if (
            not isinstance(event, DtsV2OutboxEvent)
            or event.aggregate_type != "COMPLETION_CONFLICT"
            or event.event_type != "source_wide.changed.v2"
        ):
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_EVENT_REQUIRED"
            )
        snapshot = self.aggregate_reader.read_current(connection, event)
        key = snapshot.aggregate_key
        source_region = key.get("source_region")
        source_appoint_id = key.get("source_appoint_id")
        if (
            source_region not in {"dom", "ovs"}
            or not isinstance(source_appoint_id, str)
            or not source_appoint_id
        ):
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_KEY_INVALID"
            )
        conflict = _conflict_state(snapshot.aggregate_state)
        value = connection.execute(
            text(
                """
                SELECT public.reconcile_completion_conflict_case_v2(
                    :source_region,:source_appoint_id,
                    :expected_aggregate_revision,:triggering_event_id
                )
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "expected_aggregate_revision": snapshot.current_revision,
                "triggering_event_id": event.event_id,
            },
        ).scalar_one()
        result = _command_result(value)
        outcome = result.get("outcome")
        if outcome not in _OUTCOMES:
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_COMMAND_RESULT_INVALID"
            )
        expected_pending = conflict["status"] == "PENDING"
        if (outcome == "NOT_PENDING") is not (not expected_pending):
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_COMMAND_STATE_MISMATCH"
            )
        if outcome == "SHADOW_ONLY" and not expected_pending:
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_COMMAND_STATE_MISMATCH"
            )
        expected_case_id = (
            None
            if outcome == "NOT_PENDING"
            else conflict["case_id"]
        )
        actual_case_id = result.get("case_id")
        identity_matches = actual_case_id == expected_case_id
        if outcome == "SHADOW_ONLY":
            identity_matches = actual_case_id in {None, conflict["case_id"]}
        if not identity_matches:
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_COMMAND_IDENTITY_MISMATCH"
            )
        return {
            "cases_created": int(outcome == "CREATED"),
            "cases_updated": int(outcome == "UPDATED"),
            "shadow_only": int(outcome == "SHADOW_ONLY"),
            "non_pending": int(outcome == "NOT_PENDING"),
            "superseded_events": int(snapshot.is_superseded_event),
        }


def _conflict_state(aggregate_state: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(aggregate_state) != {"completion_conflict"}:
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_STATE_INVALID"
        )
    conflict = aggregate_state.get("completion_conflict")
    expected_fields = {
        "status",
        "case_id",
        "fingerprint",
        "completion_teacher_id",
        "completion_teacher_id_type",
        "completion_participation_seq",
    }
    if not isinstance(conflict, Mapping) or set(conflict) != expected_fields:
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_STATE_INVALID"
        )
    status = conflict.get("status")
    if status not in _STATUSES:
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_STATUS_INVALID"
        )
    case_id = conflict.get("case_id")
    fingerprint = conflict.get("fingerprint")
    if status == "PENDING":
        if (
            not isinstance(case_id, str)
            or not case_id.startswith("course-completion-correction:")
            or not isinstance(fingerprint, str)
            or _SHA256.fullmatch(fingerprint) is None
        ):
            raise DtsV2CompletionConflictOutboxProcessorError(
                "DTS_V2_COMPLETION_CONFLICT_PENDING_EVIDENCE_INVALID"
            )
    elif fingerprint is not None:
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_NON_PENDING_EVIDENCE_INVALID"
        )
    return dict(conflict)


def _command_result(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_COMMAND_RESULT_INVALID"
        )
    if set(value) != {"outcome", "case_id"}:
        raise DtsV2CompletionConflictOutboxProcessorError(
            "DTS_V2_COMPLETION_CONFLICT_COMMAND_RESULT_INVALID"
        )
    return value


__all__ = [
    "DtsV2CompletionConflictOutboxProcessor",
    "DtsV2CompletionConflictOutboxProcessorError",
]
