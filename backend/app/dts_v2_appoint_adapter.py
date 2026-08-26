"""Pure bridge from a verified v2 appoint version to the participation reducer.

This module is intentionally not imported by the DTS consumer or a database
writer.  It is the narrow type boundary between the protected source-version
contract and ``dts_course_participation``: no source value is guessed or
silently coerced here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .dts_course_participation import (
    AppointSnapshot,
    AppointSourceVersion,
    SourceEventReference,
)
from .dts_source_contract_v2 import (
    V2SourceRouteDecision,
    v2_source_primary_key_type,
    v2_source_profile_id,
)


class V2AppointAdapterError(ValueError):
    """A source version cannot be represented without losing its meaning."""


_DOM_STUDENT_TOKEN_PATTERN = re.compile(r"dom:v1:[0-9a-f]{64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class V2AppointAdaptedVersion:
    """Typed appoint identity plus the reducer input derived from one route."""

    source_region: str
    source_appoint_id: str
    source_key_type: str
    source_version: AppointSourceVersion


def adapt_v2_appoint_route(
    route: V2SourceRouteDecision,
    *,
    source_region: str,
    source_row_revision: int,
    source_ref: SourceEventReference,
) -> V2AppointAdaptedVersion:
    """Convert one verified appoint route into an immutable reducer version.

    The caller must persist/replay this result in source-row-revision order.
    This function makes no attempt to obtain a current row or to repair an
    incomplete source route.
    """

    _validate_route(route, source_region=source_region, source_ref=source_ref)
    before = _snapshot(route.before_row, source_region=source_region, route=route)
    after = _snapshot(route.after_row, source_region=source_region, route=route)
    version = AppointSourceVersion(
        source_row_revision=source_row_revision,
        source_ref=source_ref,
        operation=route.operation,
        before=before,
        after=after,
    )
    # route identity has already been canonicalized by the source contract;
    # keeping its explicit union-family tag prevents NUMERIC "009" and TEXT
    # "009" from being collapsed by a future persistence layer.
    assert route.source_key is not None
    assert route.source_key_type is not None
    return V2AppointAdaptedVersion(
        source_region=source_region,
        source_appoint_id=route.source_key,
        source_key_type=route.source_key_type,
        source_version=version,
    )


def _validate_route(
    route: V2SourceRouteDecision,
    *,
    source_region: str,
    source_ref: SourceEventReference,
) -> None:
    if source_region not in {"dom", "ovs"}:
        raise V2AppointAdapterError("DTS_V2_APPOINT_REGION_INVALID")
    expected_table = f"{source_region}_appoint"
    if (
        route.route_status != "VERSIONED"
        or route.source_table != expected_table
        or route.operation not in {"INSERT", "UPDATE", "DELETE"}
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_ROUTE_NOT_VERSIONED")
    expected_profile = v2_source_profile_id(expected_table)
    expected_key_type = v2_source_primary_key_type(expected_table)
    if expected_profile is None or expected_key_type is None:
        raise V2AppointAdapterError(
            "DTS_V2_APPOINT_PROFILE_EVIDENCE_MISSING"
        )
    if route.source_schema_profile_id != expected_profile:
        raise V2AppointAdapterError("DTS_V2_APPOINT_PROFILE_EVIDENCE_MISSING")
    if (
        not isinstance(route.source_field_types, Mapping)
        or not route.source_field_types
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPE_EVIDENCE_MISSING")
    if route.source_field_types.get("id") != route.source_key_type:
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPE_EVIDENCE_MISSING")
    if (
        route.source_key_type != expected_key_type
        or not isinstance(
        route.source_key, str
        )
        or not route.source_key
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_SOURCE_KEY_INVALID")
    if not isinstance(route.protected_payload_hash, str) or not (
        _SHA256_PATTERN.fullmatch(route.protected_payload_hash)
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_PAYLOAD_HASH_INVALID")
    if (
        source_ref.source_timestamp is None
        or source_ref.source_timestamp.utcoffset() is None
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_SOURCE_TIMESTAMP_REQUIRED")

    canonical_key = _canonical_typed_id(
        route.source_key,
        source_type=route.source_key_type,
        field_name="id",
    )
    if canonical_key != route.source_key:
        raise V2AppointAdapterError("DTS_V2_APPOINT_SOURCE_KEY_NOT_CANONICAL")
    for image in (route.before_row, route.after_row):
        if image is not None and not isinstance(image, Mapping):
            raise V2AppointAdapterError("DTS_V2_APPOINT_IMAGE_INVALID")
        if image is None:
            continue
        image_key = _canonical_typed_id(
            image.get("id"),
            source_type=_required_type(route.source_field_types, "id"),
            field_name="id",
        )
        if image_key != route.source_key:
            raise V2AppointAdapterError(
                "DTS_V2_APPOINT_IMAGE_IDENTITY_MISMATCH"
            )


def _snapshot(
    row: Mapping[str, Any] | None,
    *,
    source_region: str,
    route: V2SourceRouteDecision,
) -> AppointSnapshot | None:
    if row is None:
        return None
    types = route.source_field_types
    assert isinstance(types, Mapping)
    if source_region == "dom" and "s_id" in row:
        # A protected DOM route has removed raw student aliases completely.
        raise V2AppointAdapterError("DTS_V2_DOM_RAW_STUDENT_ID_FORBIDDEN")
    teacher_id = _optional_typed_id(row, types, "t_id")
    teacher_id_type = (
        _required_type(types, "t_id") if teacher_id is not None else None
    )
    status = _optional_text(row, types, "status")
    use_point = _optional_text(row, types, "use_point")
    end_time = _optional_temporal_text(row, types, "end_time")
    lesson_local_date = _optional_temporal_text(row, types, "date")
    lesson_local_time = _optional_temporal_text(row, types, "time")
    student_token = _student_token(row, types, source_region=source_region)
    is_peak = _is_peak(
        source_region=source_region,
        week_value=row.get("week"),
        week_type=types.get("week"),
        lesson_time=lesson_local_time,
    )
    return AppointSnapshot(
        teacher_id=teacher_id,
        status=status,
        teacher_id_type=teacher_id_type,
        use_point=use_point,
        end_time=end_time,
        student_token=student_token,
        lesson_local_date=lesson_local_date,
        lesson_local_time=lesson_local_time,
        is_peak=is_peak,
    )


def _optional_typed_id(
    row: Mapping[str, Any],
    types: Mapping[str, str],
    field_name: str,
) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    return _canonical_typed_id(
        value,
        source_type=_required_type(types, field_name),
        field_name=field_name,
    )


def _canonical_typed_id(value: Any, *, source_type: str, field_name: str) -> str:
    if source_type == "TEXT":
        if not isinstance(value, str) or not value:
            raise V2AppointAdapterError("DTS_V2_APPOINT_TYPED_ID_INVALID")
        return value
    if source_type != "NUMERIC" or isinstance(value, bool):
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPED_ID_INVALID")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPED_ID_INVALID") from exc
    if not number.is_finite():
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPED_ID_INVALID")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    return rendered


def _optional_text(
    row: Mapping[str, Any],
    types: Mapping[str, str],
    field_name: str,
) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    if _required_type(types, field_name) != "TEXT" or not isinstance(value, str):
        raise V2AppointAdapterError("DTS_V2_APPOINT_TEXT_FIELD_INVALID")
    return value


def _optional_temporal_text(
    row: Mapping[str, Any],
    types: Mapping[str, str],
    field_name: str,
) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    if _required_type(types, field_name) not in {
        "TEXT",
        "TEMPORAL",
    } or not isinstance(value, str):
        raise V2AppointAdapterError("DTS_V2_APPOINT_TEMPORAL_FIELD_INVALID")
    return value


def _student_token(
    row: Mapping[str, Any],
    types: Mapping[str, str],
    *,
    source_region: str,
) -> str | None:
    if source_region == "dom":
        token = _optional_text(row, types, "student_token")
        if token is not None and not _DOM_STUDENT_TOKEN_PATTERN.fullmatch(
            token
        ):
            raise V2AppointAdapterError("DTS_V2_DOM_STUDENT_TOKEN_INVALID")
        return token
    # OVS has no protected token derivation step.  A token already present is
    # authoritative; otherwise its typed ``s_id`` is the explicit source key.
    token = _optional_text(row, types, "student_token")
    if token is not None:
        return token
    return _optional_typed_id(row, types, "s_id")


def _is_peak(
    *,
    source_region: str,
    week_value: Any,
    week_type: Any,
    lesson_time: str | None,
) -> bool | None:
    if week_value is None or lesson_time is None:
        return None
    if week_type != "NUMERIC" or isinstance(week_value, bool):
        raise V2AppointAdapterError("DTS_V2_APPOINT_WEEK_INVALID")
    try:
        numeric_week = Decimal(str(week_value))
    except (InvalidOperation, ValueError) as exc:
        raise V2AppointAdapterError("DTS_V2_APPOINT_WEEK_INVALID") from exc
    if (
        not numeric_week.is_finite()
        or numeric_week != numeric_week.to_integral_value()
    ):
        raise V2AppointAdapterError("DTS_V2_APPOINT_WEEK_INVALID")
    week = int(numeric_week)
    try:
        parsed_time = time.fromisoformat(lesson_time)
    except ValueError:
        return None
    if parsed_time.tzinfo is not None:
        return None
    if week not in {0, 1, 2, 3, 4, 5, 6, 7}:
        return None
    weekend = week in {0, 6, 7}
    morning = time(9, 0) <= parsed_time <= time(11, 30)
    if source_region == "dom":
        return time(18, 0) <= parsed_time <= time(21, 30) or (weekend and morning)
    return (
        time(18, 0) <= parsed_time <= time(23, 30)
        or time(0, 0) <= parsed_time <= time(5, 30)
        or (weekend and morning)
    )


def _required_type(types: Mapping[str, str], field_name: str) -> str:
    source_type = types.get(field_name)
    if source_type not in {"NUMERIC", "TEXT", "TEMPORAL", "BOOLEAN"}:
        raise V2AppointAdapterError("DTS_V2_APPOINT_TYPE_EVIDENCE_MISSING")
    return source_type
