"""Pure routing for validated source-wide-table change events.

This module only translates one outbox payload into the smallest registered
handler and recomputation scopes.  It deliberately performs no database I/O,
event consumption, or recomputation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .source_contracts import SOURCE_FIELD_DEPENDENCIES, TEACHER_SOURCE_TABLE


ALLOWED_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE"})


class SourceChangeRoutingError(ValueError):
    """The source change payload does not satisfy the routing contract."""


@dataclass(frozen=True)
class SourceChangeRoute:
    """Deterministic downstream work selected for one source change."""

    source_table: str
    source_id: str
    operation: str
    changed_fields: tuple[str, ...]
    handlers: tuple[str, ...]
    scopes: tuple[str, ...]
    affected_teacher_ids: tuple[str, ...]


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceChangeRoutingError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_teacher_id(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceChangeRoutingError(
            f"{key} must be null or a non-empty string"
        )
    return value.strip()


def _changed_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = payload.get("changed_fields")
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
    ):
        raise SourceChangeRoutingError(
            "changed_fields must be a non-empty sequence of field names"
        )
    if any(not isinstance(field, str) or not field for field in value):
        raise SourceChangeRoutingError(
            "changed_fields must contain only non-empty strings"
        )
    return tuple(value)


def _affected_teacher_ids(
    operation: str,
    old_teacher_id: str | None,
    new_teacher_id: str | None,
) -> tuple[str, ...]:
    if operation == "INSERT":
        if new_teacher_id is None:
            raise SourceChangeRoutingError(
                "new_teacher_id is required for INSERT"
            )
        affected = {new_teacher_id}
    elif operation == "DELETE":
        if old_teacher_id is None:
            raise SourceChangeRoutingError(
                "old_teacher_id is required for DELETE"
            )
        affected = {old_teacher_id}
    else:
        if old_teacher_id is None or new_teacher_id is None:
            raise SourceChangeRoutingError(
                "old_teacher_id and new_teacher_id are required for UPDATE"
            )
        affected = {old_teacher_id, new_teacher_id}
    return tuple(sorted(affected))


def route_source_change(payload: Mapping[str, Any]) -> SourceChangeRoute:
    """Validate and deterministically route one source-table outbox payload."""

    if not isinstance(payload, Mapping):
        raise SourceChangeRoutingError("payload must be a mapping")

    source_table = _required_text(payload, "source_table")
    source_id = _required_text(payload, "source_id")

    dependencies = SOURCE_FIELD_DEPENDENCIES.get(source_table)
    if dependencies is None:
        raise SourceChangeRoutingError(
            f"unknown source_table: {source_table}"
        )

    operation = _required_text(payload, "operation")
    if operation not in ALLOWED_OPERATIONS:
        raise SourceChangeRoutingError(f"invalid operation: {operation}")

    changed_fields = tuple(sorted(set(_changed_fields(payload))))
    unknown_fields = sorted(set(changed_fields) - set(dependencies))
    if unknown_fields:
        raise SourceChangeRoutingError(
            f"unknown fields for {source_table}: {unknown_fields}"
        )

    old_teacher_id = _optional_teacher_id(payload, "old_teacher_id")
    new_teacher_id = _optional_teacher_id(payload, "new_teacher_id")
    affected_teacher_ids = _affected_teacher_ids(
        operation,
        old_teacher_id,
        new_teacher_id,
    )

    handlers: set[str] = set()
    scopes: set[str] = set()
    for field in changed_fields:
        dependency = dependencies[field]
        handlers.update(dependency.handlers)
        if dependency.recompute_scope != "NONE":
            scopes.add(dependency.recompute_scope)

    # A teacher identity row creates the fixed-task baseline only once.  A
    # source-row deletion is a removal/reconciliation signal; it must never be
    # interpreted as another teacher-registration command.
    if source_table == TEACHER_SOURCE_TABLE and operation != "INSERT":
        handlers.discard("FIXED_TASK_BASELINE")
    if source_table == TEACHER_SOURCE_TABLE and operation == "DELETE":
        handlers.discard("TEACHER_IDENTITY")
        handlers.add("TEACHER_SOURCE_REMOVAL")
        scopes.add("SINGLE_TEACHER_SOURCE_REMOVAL")

    return SourceChangeRoute(
        source_table=source_table,
        source_id=source_id,
        operation=operation,
        changed_fields=changed_fields,
        handlers=tuple(sorted(handlers)),
        scopes=tuple(sorted(scopes)),
        affected_teacher_ids=affected_teacher_ids,
    )


__all__ = [
    "ALLOWED_OPERATIONS",
    "SourceChangeRoute",
    "SourceChangeRoutingError",
    "route_source_change",
]
