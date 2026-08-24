"""Protected command client for a completion-ownership correction decision.

The application validates and hashes the operator request, but PostgreSQL is
the sole owner of concurrency checks, participation-role transitions, score
reversal/rebuild and COURSE/PARTICIPATION Outbox publication.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_DECISIONS = frozenset(
    {
        "KEEP_FROZEN_COMPLETION",
        "UPDATE_COMPLETION_SNAPSHOT",
        "TRANSFER_COMPLETION",
        "VOID_COMPLETION",
    }
)
_OUTCOMES = frozenset({"APPLIED_PENDING_PROJECTION", "REPLAYED"})
_CONFLICT_STATUSES = frozenset(
    {
        "RESOLVED_KEEP",
        "RESOLVED_UPDATE",
        "RESOLVED_TRANSFER",
        "RESOLVED_VOID",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^course-completion-correction:(dom|ovs):.+$")
_DECISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$")


class DtsV2CompletionCorrectionError(RuntimeError):
    """A correction request or protected command result is unsafe."""


@dataclass(frozen=True)
class CompletionCorrectionRequestV2:
    decision_id: str
    case_id: str
    decision: str
    actor_id: str
    reason: str
    expected_case_revision: int
    expected_conflict_fingerprint: str
    expected_source_revision: int
    expected_source_position: Mapping[str, Any]
    target_participation_seq: int | None = None
    completion_snapshot: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decision_id, str)
            or _DECISION_ID.fullmatch(self.decision_id) is None
            or not isinstance(self.case_id, str)
            or _CASE_ID.fullmatch(self.case_id) is None
            or self.decision not in _DECISIONS
            or not isinstance(self.actor_id, str)
            or not self.actor_id.strip()
            or self.actor_id != self.actor_id.strip()
            or not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > 2000
            or type(self.expected_case_revision) is not int
            or self.expected_case_revision < 1
            or not isinstance(self.expected_conflict_fingerprint, str)
            or _SHA256.fullmatch(self.expected_conflict_fingerprint) is None
            or type(self.expected_source_revision) is not int
            or self.expected_source_revision < 1
            or not isinstance(self.expected_source_position, Mapping)
            or not self.expected_source_position
        ):
            _fail("REQUEST_INVALID")
        target = self.target_participation_seq
        snapshot = self.completion_snapshot
        if self.decision in {
            "KEEP_FROZEN_COMPLETION",
            "VOID_COMPLETION",
        }:
            valid_shape = target is None and snapshot is None
        elif self.decision == "UPDATE_COMPLETION_SNAPSHOT":
            valid_shape = target is None and _snapshot_shape(snapshot)
        else:
            valid_shape = (
                type(target) is int
                and target >= 1
                and _snapshot_shape(snapshot)
            )
        if not valid_shape:
            _fail("DECISION_SHAPE_INVALID")

    def canonical_request(self) -> Mapping[str, Any]:
        return {
            "protocol_version": "completion-correction-command-v1",
            "decision_id": self.decision_id,
            "case_id": self.case_id,
            "decision": self.decision,
            "actor_id": self.actor_id,
            "reason": self.reason,
            "expected_case_revision": self.expected_case_revision,
            "expected_conflict_fingerprint": (
                self.expected_conflict_fingerprint
            ),
            "expected_source_revision": self.expected_source_revision,
            "expected_source_position": dict(self.expected_source_position),
            "target_participation_seq": self.target_participation_seq,
            "completion_snapshot": (
                None
                if self.completion_snapshot is None
                else dict(self.completion_snapshot)
            ),
        }


@dataclass(frozen=True)
class CompletionCorrectionResultV2:
    outcome: str
    decision_id: str
    case_id: str
    case_status: str
    completion_conflict_status: str
    projection_event_ids: tuple[str, ...]


class PostgresDtsV2CompletionCorrectionStore:
    """Call the sole SECURITY DEFINER correction command."""

    def apply(
        self,
        connection: Connection,
        request: CompletionCorrectionRequestV2,
    ) -> CompletionCorrectionResultV2:
        if not isinstance(request, CompletionCorrectionRequestV2):
            _fail("REQUEST_REQUIRED")
        payload = request.canonical_request()
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        value = connection.execute(
            text(
                """
                SELECT public.apply_completion_correction_decision_v2(
                    CAST(:request AS jsonb),:expected_request_sha256
                )
                """
            ),
            {
                "request": canonical,
                "expected_request_sha256": expected_hash,
            },
        ).scalar_one()
        return _result(value, request=request)


def _result(
    value: Any,
    *,
    request: CompletionCorrectionRequestV2,
) -> CompletionCorrectionResultV2:
    fields = {
        "outcome",
        "decision_id",
        "case_id",
        "case_status",
        "completion_conflict_status",
        "projection_event_ids",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("COMMAND_RESULT_INVALID")
    outcome = value.get("outcome")
    decision_id = value.get("decision_id")
    case_id = value.get("case_id")
    case_status = value.get("case_status")
    conflict_status = value.get("completion_conflict_status")
    event_ids = value.get("projection_event_ids")
    if (
        outcome not in _OUTCOMES
        or decision_id != request.decision_id
        or case_id != request.case_id
        or case_status != "RESOLVED"
        or conflict_status not in _CONFLICT_STATUSES
        or not isinstance(event_ids, Sequence)
        or isinstance(event_ids, (str, bytes, bytearray))
        or not event_ids
        or any(not isinstance(item, str) or not item for item in event_ids)
        or len(event_ids) != len(set(event_ids))
    ):
        _fail("COMMAND_RESULT_INVALID")
    expected_status = {
        "KEEP_FROZEN_COMPLETION": "RESOLVED_KEEP",
        "UPDATE_COMPLETION_SNAPSHOT": "RESOLVED_UPDATE",
        "TRANSFER_COMPLETION": "RESOLVED_TRANSFER",
        "VOID_COMPLETION": "RESOLVED_VOID",
    }[request.decision]
    if conflict_status != expected_status:
        _fail("COMMAND_RESULT_DECISION_MISMATCH")
    return CompletionCorrectionResultV2(
        outcome=str(outcome),
        decision_id=str(decision_id),
        case_id=str(case_id),
        case_status=str(case_status),
        completion_conflict_status=str(conflict_status),
        projection_event_ids=tuple(str(item) for item in event_ids),
    )


def _snapshot_shape(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    fields = {
        "teacher_id",
        "teacher_id_type",
        "status",
        "end_time",
        "student_token",
        "lesson_local_date",
        "lesson_local_time",
        "is_peak",
    }
    if set(value) != fields or value.get("status") != "end":
        return False
    teacher_id = value.get("teacher_id")
    teacher_type = value.get("teacher_id_type")
    if (
        not isinstance(teacher_id, str)
        or not teacher_id
        or teacher_type not in {"NUMERIC", "TEXT"}
    ):
        return False
    for name in (
        "end_time",
        "student_token",
        "lesson_local_date",
        "lesson_local_time",
    ):
        item = value.get(name)
        if item is not None and not isinstance(item, str):
            return False
    return value.get("is_peak") is None or type(value.get("is_peak")) is bool


def _fail(code: str) -> None:
    raise DtsV2CompletionCorrectionError(
        f"DTS_V2_COMPLETION_CORRECTION_{code}"
    )


__all__ = [
    "CompletionCorrectionRequestV2",
    "CompletionCorrectionResultV2",
    "DtsV2CompletionCorrectionError",
    "PostgresDtsV2CompletionCorrectionStore",
]
