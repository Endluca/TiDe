"""TASK_PLAN Outbox handler and protected materialization command wrapper."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_aggregate_state_reader import DtsV2AggregateStateReader
from .dts_v2_outbox_worker import DtsV2OutboxEvent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMPTY_SET_HASH = hashlib.sha256(b"[]").hexdigest()
_TASK_CODES = frozenset(
    {
        "P-REL-MEMO",
        "P-REL-ATTENDANCE",
        "P-FB-NEGATIVE",
        "P-FB-COMPLAINT",
        "P-FB-BLACKLIST",
    }
)
_PLAN_FIELDS = {
    "protocol_version",
    "assignment_dedupe_key",
    "teacher_id",
    "target_task_code",
    "materializable",
    "eligibility_generation",
    "eligible_since_at",
    "timezone_used",
    "timezone_source",
    "timezone_verified_at",
    "teacher_row_version",
    "template_version_id",
    "template_revision",
    "teacher_copy_version_id",
    "teacher_copy_config_key",
    "teacher_copy_version_number",
    "active_match_set_hash",
    "blocker_code",
    "blocker_set_hash",
    "plan_state_hash",
}
_OUTCOMES = frozenset(
    {"MATERIALIZED", "EXISTING_ASSIGNMENT", "NOT_MATERIALIZABLE"}
)


class DtsV2TaskPlanOutboxProcessorError(RuntimeError):
    """A TASK_PLAN request cannot safely create or link one assignment."""


class DtsV2TaskPlanOutboxProcessor:
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
            or event.aggregate_type != "TASK_PLAN"
            or event.event_type != "task.materialization.requested.v2"
        ):
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_EVENT_REQUIRED"
            )
        snapshot = self.aggregate_reader.read_current(connection, event)
        state = _validate_plan_state(snapshot.aggregate_state)
        if state["assignment_dedupe_key"] != snapshot.aggregate_key.get(
            "assignment_dedupe_key"
        ):
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_KEY_MISMATCH"
            )
        if snapshot.is_superseded_event:
            return {
                "superseded_events": 1,
                "assignments_created": 0,
                "assignments_linked": 0,
                "not_materializable": 0,
            }

        value = connection.execute(
            text(
                """
                SELECT public.materialize_task_plan_v2(
                    :event_id,:aggregate_id,:aggregate_revision,
                    :expected_plan_state_hash
                )
                """
            ),
            {
                "event_id": event.event_id,
                "aggregate_id": event.aggregate_id,
                "aggregate_revision": snapshot.current_revision,
                "expected_plan_state_hash": state["plan_state_hash"],
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != {
            "outcome",
            "assignment_id",
        }:
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_COMMAND_RESULT_INVALID"
            )
        outcome = value.get("outcome")
        assignment_id = value.get("assignment_id")
        if outcome not in _OUTCOMES:
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_COMMAND_RESULT_INVALID"
            )
        if outcome == "NOT_MATERIALIZABLE":
            if assignment_id is not None or state["materializable"] is True:
                raise DtsV2TaskPlanOutboxProcessorError(
                    "DTS_V2_TASK_PLAN_COMMAND_STATE_MISMATCH"
                )
        elif (
            not isinstance(assignment_id, str)
            or not assignment_id
            or state["materializable"] is not True
        ):
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_COMMAND_STATE_MISMATCH"
            )
        return {
            "superseded_events": 0,
            "assignments_created": int(outcome == "MATERIALIZED"),
            "assignments_linked": int(outcome == "EXISTING_ASSIGNMENT"),
            "not_materializable": int(outcome == "NOT_MATERIALIZABLE"),
        }


def _validate_plan_state(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PLAN_FIELDS:
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    state = dict(value)
    if state.get("protocol_version") != "task-plan-state-v1":
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    for key in ("assignment_dedupe_key", "teacher_id", "target_task_code"):
        if not isinstance(state.get(key), str) or not state[key]:
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_STATE_INVALID"
            )
    task_code = state["target_task_code"]
    teacher_id = state["teacher_id"]
    assignment_key = state["assignment_dedupe_key"]
    if task_code not in _TASK_CODES or not _assignment_key_matches(
        assignment_key,
        teacher_id=teacher_id,
        task_code=task_code,
    ):
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    if type(state.get("materializable")) is not bool:
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    for key in (
        "eligibility_generation",
        "teacher_row_version",
        "template_revision",
        "teacher_copy_version_number",
    ):
        item = state.get(key)
        if item is not None and (type(item) is not int or item < 0):
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_STATE_INVALID"
            )
    for key in ("active_match_set_hash", "blocker_set_hash", "plan_state_hash"):
        item = state.get(key)
        if not isinstance(item, str) or _SHA256.fullmatch(item) is None:
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_STATE_INVALID"
            )
    hash_basis = {key: state[key] for key in _PLAN_FIELDS - {"plan_state_hash"}}
    expected_hash = hashlib.sha256(
        json.dumps(
            hash_basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if state["plan_state_hash"] != expected_hash:
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_HASH_MISMATCH"
        )
    _validate_basis_shape(state)
    blocker_code = state.get("blocker_code")
    if not isinstance(blocker_code, str) or not blocker_code:
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    if state["materializable"]:
        if blocker_code != "NONE" or state["blocker_set_hash"] != _EMPTY_SET_HASH:
            raise DtsV2TaskPlanOutboxProcessorError(
                "DTS_V2_TASK_PLAN_STATE_INVALID"
            )
    elif blocker_code == "NONE":
        raise DtsV2TaskPlanOutboxProcessorError(
            "DTS_V2_TASK_PLAN_STATE_INVALID"
        )
    return state


def _validate_basis_shape(state: Mapping[str, Any]) -> None:
    generation = state.get("eligibility_generation")
    template_id = state.get("template_version_id")
    template_revision = state.get("template_revision")
    copy_id = state.get("teacher_copy_version_id")
    copy_key = state.get("teacher_copy_config_key")
    copy_number = state.get("teacher_copy_version_number")
    eligible_since = state.get("eligible_since_at")
    timezone_used = state.get("timezone_used")
    timezone_source = state.get("timezone_source")
    timezone_verified = state.get("timezone_verified_at")
    teacher_row_version = state.get("teacher_row_version")

    if (template_id is None) is not (template_revision is None):
        _invalid_state()
    if template_id is not None and (
        not isinstance(template_id, str)
        or not template_id
        or type(template_revision) is not int
        or template_revision < 1
    ):
        _invalid_state()
    copy_values = (copy_id, copy_key, copy_number)
    if any(item is None for item in copy_values) and not all(
        item is None for item in copy_values
    ):
        _invalid_state()
    if copy_id is not None and (
        not isinstance(copy_id, str)
        or not copy_id
        or copy_key != "teacher_personalized_copy"
        or type(copy_number) is not int
        or copy_number < 1
    ):
        _invalid_state()
    time_values = (
        eligible_since,
        timezone_used,
        timezone_source,
        timezone_verified,
    )
    if any(item is None for item in time_values) and not all(
        item is None for item in time_values
    ):
        _invalid_state()
    if eligible_since is not None:
        _aware_timestamp(eligible_since)
        _aware_timestamp(timezone_verified)
        if (
            not isinstance(timezone_used, str)
            or not timezone_used
            or not isinstance(timezone_source, str)
            or not timezone_source
        ):
            _invalid_state()
        try:
            ZoneInfo(timezone_used)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            _invalid_state()

    basis_values = (
        eligible_since,
        timezone_used,
        timezone_source,
        timezone_verified,
        teacher_row_version,
        template_id,
        template_revision,
        copy_id,
        copy_key,
        copy_number,
    )
    if generation is None:
        if state.get("materializable") or any(
            item is not None for item in basis_values
        ):
            _invalid_state()
        return
    if generation >= 1 and (
        any(item is None for item in basis_values)
        or type(teacher_row_version) is not int
        or teacher_row_version < 1
    ):
        _invalid_state()


def _assignment_key_matches(
    value: str,
    *,
    teacher_id: str,
    task_code: str,
) -> bool:
    prefix = f"personalized:{task_code}:{teacher_id}"
    if task_code in {"P-REL-MEMO", "P-REL-ATTENDANCE", "P-FB-BLACKLIST"}:
        return value == prefix
    return value.startswith(prefix + ":") and len(value) > len(prefix) + 1


def _aware_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        _invalid_state()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid_state()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid_state()


def _invalid_state() -> None:
    raise DtsV2TaskPlanOutboxProcessorError(
        "DTS_V2_TASK_PLAN_STATE_INVALID"
    )


__all__ = [
    "DtsV2TaskPlanOutboxProcessor",
    "DtsV2TaskPlanOutboxProcessorError",
]
