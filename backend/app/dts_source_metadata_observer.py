"""Value-blind DTS source-profile metadata observation.

The production CLI currently accepts only offline JSONL containing structured
``EVENT`` envelopes emitted after the official Java SDK has decoded a record.
It does not open a broker connection, import a PostgreSQL store, advance a
checkpoint, or send an SDK acknowledgement.  A future live source may be
injected into :func:`observe_official_java_metadata`, but it must preserve
those boundaries and use the separately authorised diagnostic group.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from .dts_source_profile_registry_v2 import DtsSourceMetadataAggregator


_SUPPORTED_REGIONS = frozenset({"dom", "ovs"})
_DATA_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE"})
_CONTROL_OPERATIONS = frozenset(
    {
        "DDL",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "ABORT",
        "HEARTBEAT",
        "CHECKPOINT",
        "COMMAND",
        "FILL",
        "FINISH",
        "CONTROL",
        "RDB",
        "NOOP",
        "INIT",
    }
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_INTEGER_PATTERN = re.compile(r"^[0-9]+$")
_MAX_JSONL_LINE_BYTES = 16 * 1024 * 1024
_MAX_FIELDS_PER_EVENT = 4_096
_MAX_EVENTS = 1_000_000
_MAX_DURATION_SECONDS = 86_400.0
_VALUE_NOT_RETAINED = object()


class DtsSourceMetadataObserverError(RuntimeError):
    """Stable, value-free observer failure."""


@dataclass(frozen=True)
class DtsSourceMetadataObserverSettings:
    """Explicit offline observer contract.

    The group IDs are validated as an operational safety gate but are never
    used by this offline implementation and never appear in its output.
    """

    source_region: str
    diagnostic_group_id: str
    formal_group_id: str
    input_jsonl: Path
    output_path: Path
    max_events: int
    max_duration_seconds: float

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DtsSourceMetadataObserverSettings":
        values = os.environ if environ is None else environ

        if values.get(
            "TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED", ""
        ).strip().lower() != "true":
            raise DtsSourceMetadataObserverError(
                "TIT_DTS_SOURCE_METADATA_OBSERVER_EXPLICIT_ENABLE_REQUIRED"
            )
        source_mode = _required_env(
            values,
            "TIT_DTS_SOURCE_METADATA_SOURCE_MODE",
        ).lower()
        if source_mode != "offline_jsonl":
            raise DtsSourceMetadataObserverError(
                "TIT_DTS_SOURCE_METADATA_SOURCE_MODE_UNSUPPORTED"
            )
        source_region = _required_env(
            values,
            "TIT_DTS_SOURCE_METADATA_REGION",
        ).lower()
        if source_region not in _SUPPORTED_REGIONS:
            raise DtsSourceMetadataObserverError(
                "TIT_DTS_SOURCE_METADATA_REGION_UNSUPPORTED"
            )
        diagnostic_group_id = _required_group_id(
            values,
            "TIT_DTS_SOURCE_METADATA_DIAGNOSTIC_GROUP_ID",
        )
        formal_group_id = _required_group_id(
            values,
            "TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID",
        )
        if diagnostic_group_id == formal_group_id:
            raise DtsSourceMetadataObserverError(
                "TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_REUSE_FORBIDDEN"
            )
        configured_runtime_group = values.get("TIT_DTS_GROUP_ID", "").strip()
        if configured_runtime_group:
            configured_runtime_group = _validated_group_id(
                configured_runtime_group,
                "TIT_DTS_GROUP_ID",
            )
            if configured_runtime_group != formal_group_id:
                raise DtsSourceMetadataObserverError(
                    "TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID_MISMATCH"
                )

        input_jsonl = Path(
            _required_env(values, "TIT_DTS_SOURCE_METADATA_INPUT_JSONL")
        )
        output_path = Path(
            _required_env(values, "TIT_DTS_SOURCE_METADATA_OUTPUT")
        )
        if _resolved_path(input_jsonl) == _resolved_path(output_path):
            raise DtsSourceMetadataObserverError(
                "TIT_DTS_SOURCE_METADATA_INPUT_OUTPUT_CONFLICT"
            )
        max_events = _positive_int_env(
            values,
            "TIT_DTS_SOURCE_METADATA_MAX_EVENTS",
            maximum=_MAX_EVENTS,
        )
        max_duration_seconds = _positive_float_env(
            values,
            "TIT_DTS_SOURCE_METADATA_MAX_DURATION_SECONDS",
            maximum=_MAX_DURATION_SECONDS,
        )
        return cls(
            source_region=source_region,
            diagnostic_group_id=diagnostic_group_id,
            formal_group_id=formal_group_id,
            input_jsonl=input_jsonl,
            output_path=output_path,
            max_events=max_events,
            max_duration_seconds=max_duration_seconds,
        )


@dataclass(frozen=True)
class DtsSourceMetadataEvent:
    """Only the attributes consumed by ``DtsSourceMetadataAggregator``."""

    table_name: str
    operation: str
    before: Mapping[str, object] | None
    after: Mapping[str, object] | None
    source_field_types: Mapping[str, str]
    source_field_type_numbers: Mapping[str, int]


@dataclass(frozen=True)
class DtsSourceMetadataObservationReport:
    canonical_json: str
    evidence_sha256: str


class OfficialJavaEventJsonlSource:
    """Stream structured official-Java ``EVENT`` envelopes from JSONL."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        try:
            handle = self.path.open("rb")
        except OSError as exc:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_INPUT_UNREADABLE"
            ) from exc
        with handle:
            while True:
                raw_line = handle.readline(_MAX_JSONL_LINE_BYTES + 1)
                if not raw_line:
                    return
                if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                    raise DtsSourceMetadataObserverError(
                        "DTS_SOURCE_METADATA_INPUT_LINE_TOO_LARGE"
                    )
                if not raw_line.strip():
                    continue
                try:
                    document = json.loads(
                        raw_line.decode("utf-8"),
                        object_pairs_hook=_reject_duplicate_json_keys,
                        parse_constant=_reject_non_finite_json_constant,
                    )
                except UnicodeDecodeError as exc:
                    raise DtsSourceMetadataObserverError(
                        "DTS_SOURCE_METADATA_INPUT_ENCODING_INVALID"
                    ) from exc
                except json.JSONDecodeError as exc:
                    raise DtsSourceMetadataObserverError(
                        "DTS_SOURCE_METADATA_INPUT_JSON_INVALID"
                    ) from exc
                if not isinstance(document, Mapping):
                    raise DtsSourceMetadataObserverError(
                        "DTS_SOURCE_METADATA_EVENT_ENVELOPE_INVALID"
                    )
                yield document


def metadata_event_from_official_java_envelope(
    envelope: Mapping[str, Any],
    *,
    source_region: str,
) -> DtsSourceMetadataEvent | None:
    """Reduce a structured Java event to metadata without retaining values."""

    if source_region not in _SUPPORTED_REGIONS:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_REGION_UNSUPPORTED"
        )
    if envelope.get("type") != "EVENT":
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_EVENT_ENVELOPE_INVALID"
        )
    lightweight = envelope.get("lightweight", False)
    if not isinstance(lightweight, bool):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_EVENT_ENVELOPE_INVALID"
        )
    if lightweight:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_LIGHTWEIGHT_EVENT_FORBIDDEN"
        )
    record = envelope.get("record")
    if not isinstance(record, Mapping):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_EVENT_RECORD_INVALID"
        )
    operation = record.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_OPERATION_INVALID"
        )
    normalized_operation = operation.strip().upper()
    if normalized_operation in _CONTROL_OPERATIONS:
        return None
    if normalized_operation not in _DATA_OPERATIONS:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_OPERATION_INVALID"
        )

    table_name = _record_table_name(record)
    if not table_name.startswith(f"{source_region}_"):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_TABLE_REGION_MISMATCH"
        )
    fields, descriptor_type_numbers = _metadata_fields(record.get("fields"))
    explicit_type_numbers = _metadata_type_numbers(
        record.get("fieldTypeNumbers"),
        fields=fields,
    )
    type_numbers = dict(descriptor_type_numbers)
    for field_name, type_number in explicit_type_numbers.items():
        previous = type_numbers.get(field_name)
        if previous is not None and previous != type_number:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_TYPE_NUMBER_DRIFT"
            )
        type_numbers[field_name] = type_number

    before, before_types = _image_shape_and_types(
        record.get("beforeImages"),
        fields=fields,
    )
    after, after_types = _image_shape_and_types(
        record.get("afterImages"),
        fields=fields,
    )
    field_types = dict(before_types)
    for field_name, field_type in after_types.items():
        previous = field_types.get(field_name)
        if previous is not None and previous != field_type:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_ABSTRACT_TYPE_DRIFT"
            )
        field_types[field_name] = field_type

    return DtsSourceMetadataEvent(
        table_name=table_name,
        operation=normalized_operation,
        before=before,
        after=after,
        source_field_types=MappingProxyType(dict(sorted(field_types.items()))),
        source_field_type_numbers=MappingProxyType(
            dict(sorted(type_numbers.items()))
        ),
    )


def observe_official_java_metadata(
    source: Iterable[Mapping[str, Any]],
    *,
    source_region: str,
    max_events: int,
    max_duration_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> DtsSourceMetadataObservationReport:
    """Observe a bounded source and return region-bound canonical evidence."""

    if (
        isinstance(max_events, bool)
        or not isinstance(max_events, int)
        or not 1 <= max_events <= _MAX_EVENTS
    ):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_MAX_EVENTS_INVALID"
        )
    if (
        isinstance(max_duration_seconds, bool)
        or not isinstance(max_duration_seconds, (int, float))
        or not math.isfinite(float(max_duration_seconds))
        or not 0 < float(max_duration_seconds) <= _MAX_DURATION_SECONDS
    ):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_MAX_DURATION_INVALID"
        )
    if source_region not in _SUPPORTED_REGIONS:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_REGION_UNSUPPORTED"
        )

    aggregator = DtsSourceMetadataAggregator()
    iterator = iter(source)
    consumed = 0
    started_at = monotonic()
    while consumed < max_events:
        if monotonic() - started_at >= float(max_duration_seconds):
            break
        try:
            envelope = next(iterator)
        except StopIteration:
            break
        if monotonic() - started_at >= float(max_duration_seconds):
            break
        consumed += 1
        event = metadata_event_from_official_java_envelope(
            envelope,
            source_region=source_region,
        )
        if event is not None:
            try:
                aggregator.observe(event)
            except ValueError as exc:
                raise DtsSourceMetadataObserverError(str(exc)) from exc

    payload = aggregator.to_payload()
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_NO_DATA_EVENTS"
        )
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "source_region": source_region,
        "observations": observations,
    }
    evidence_json = _canonical_json(evidence)
    evidence_sha256 = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
    report = {**evidence, "evidence_sha256": evidence_sha256}
    return DtsSourceMetadataObservationReport(
        canonical_json=_canonical_json(report),
        evidence_sha256=evidence_sha256,
    )


def write_observation_report_atomic(
    path: str | Path,
    canonical_json: str,
) -> None:
    """Replace one evidence file atomically with owner-only permissions."""

    output_path = Path(path)
    parent = output_path.parent
    if not parent.is_dir() or output_path.is_symlink():
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_OUTPUT_PATH_INVALID"
        )
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(canonical_json)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
        temporary_name = None
    except OSError as exc:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_OUTPUT_WRITE_FAILED"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _record_table_name(record: Mapping[str, Any]) -> str:
    tagged_table: str | None = None
    raw_tags = record.get("tags")
    if raw_tags is not None:
        if not isinstance(raw_tags, Mapping):
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_TABLE_INVALID"
            )
        for raw_key, raw_value in raw_tags.items():
            if not isinstance(raw_key, str):
                raise DtsSourceMetadataObserverError(
                    "DTS_SOURCE_METADATA_TABLE_INVALID"
                )
            if raw_key.lower() in {"tablename", "table"}:
                if not isinstance(raw_value, str):
                    raise DtsSourceMetadataObserverError(
                        "DTS_SOURCE_METADATA_TABLE_INVALID"
                    )
                candidate = _strip_identifier(raw_value)
                if candidate:
                    if tagged_table is not None and tagged_table != candidate:
                        raise DtsSourceMetadataObserverError(
                            "DTS_SOURCE_METADATA_TABLE_IDENTITY_CONFLICT"
                        )
                    tagged_table = candidate

    object_table: str | None = None
    raw_object_name = record.get("objectName")
    if raw_object_name is not None:
        if not isinstance(raw_object_name, str):
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_TABLE_INVALID"
            )
        parts = [
            _strip_identifier(part)
            for part in raw_object_name.replace("/", ".").split(".")
        ]
        object_table = next((part for part in reversed(parts) if part), None)
    if tagged_table and object_table and tagged_table != object_table:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_TABLE_IDENTITY_CONFLICT"
        )
    table_name = tagged_table or object_table
    if (
        table_name is None
        or _IDENTIFIER_PATTERN.fullmatch(table_name) is None
    ):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_TABLE_INVALID"
        )
    return table_name


def _metadata_fields(
    raw_fields: Any,
) -> tuple[tuple[str, ...], dict[str, int]]:
    if not isinstance(raw_fields, Sequence) or isinstance(
        raw_fields,
        (str, bytes, bytearray),
    ):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_FIELDS_INVALID"
        )
    if len(raw_fields) > _MAX_FIELDS_PER_EVENT:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_FIELDS_INVALID"
        )
    fields: list[str] = []
    type_numbers: dict[str, int] = {}
    for descriptor in raw_fields:
        if isinstance(descriptor, str):
            field_name = descriptor
            raw_type_number = None
        elif isinstance(descriptor, Mapping):
            field_name = descriptor.get("name")
            raw_type_number = descriptor.get("dataTypeNumber")
        else:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_FIELDS_INVALID"
            )
        normalized_name = _identifier(
            field_name,
            "DTS_SOURCE_METADATA_FIELDS_INVALID",
        )
        if normalized_name in fields:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_FIELDS_INVALID"
            )
        fields.append(normalized_name)
        if raw_type_number is not None:
            type_numbers[normalized_name] = _type_number(raw_type_number)
    return tuple(fields), type_numbers


def _metadata_type_numbers(
    raw_value: Any,
    *,
    fields: tuple[str, ...],
) -> dict[str, int]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, Mapping):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID"
        )
    allowed_fields = frozenset(fields)
    result: dict[str, int] = {}
    for raw_name, raw_number in raw_value.items():
        field_name = _identifier(
            raw_name,
            "DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID",
        )
        if field_name not in allowed_fields:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID"
            )
        result[field_name] = _type_number(raw_number)
    return result


def _image_shape_and_types(
    raw_images: Any,
    *,
    fields: tuple[str, ...],
) -> tuple[Mapping[str, object] | None, dict[str, str]]:
    if raw_images is None:
        return None, {}
    if not isinstance(raw_images, Sequence) or isinstance(
        raw_images,
        (str, bytes, bytearray),
    ):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_IMAGES_INVALID"
        )
    if len(raw_images) != len(fields):
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_IMAGE_FIELD_COUNT_MISMATCH"
        )
    field_types: dict[str, str] = {}
    for index, field_name in enumerate(fields):
        field_type = _abstract_type_from_image(raw_images[index])
        if field_type is not None:
            field_types[field_name] = field_type
    return (
        MappingProxyType(
            {field_name: _VALUE_NOT_RETAINED for field_name in fields}
        ),
        field_types,
    )


def _abstract_type_from_image(value: Any) -> str | None:
    if value is None or (
        isinstance(value, str) and value in {"NULL", "NONE"}
    ):
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        return "NUMERIC"
    if isinstance(value, (str, bytes, bytearray)):
        return "TEXT"
    if not isinstance(value, Mapping):
        return None
    if "timestamp" in value or "year" in value:
        return "TEMPORAL"
    if "charset" in value and "value" in value:
        return "TEXT"
    if "precision" in value and "value" in value:
        return "NUMERIC"
    if "value" not in value:
        return None
    nested = value.get("value")
    if isinstance(nested, Mapping) and "year" in nested:
        return "TEMPORAL"
    if isinstance(nested, bool):
        return "BOOLEAN"
    if isinstance(nested, (int, float)):
        return "NUMERIC"
    if isinstance(nested, (str, bytes, bytearray)):
        return "TEXT"
    return None


def _type_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DtsSourceMetadataObserverError(
            "DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID"
        )
    return value


def _identifier(value: Any, error: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise DtsSourceMetadataObserverError(error)
    return value


def _strip_identifier(value: str) -> str:
    return value.strip().strip('`"[]')


def _required_env(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise DtsSourceMetadataObserverError(f"{name}_REQUIRED")
    return value


def _required_group_id(values: Mapping[str, str], name: str) -> str:
    return _validated_group_id(_required_env(values, name), name)


def _validated_group_id(value: str, name: str) -> str:
    if len(value) > 256 or any(character.isspace() for character in value):
        raise DtsSourceMetadataObserverError(f"{name}_INVALID")
    return value


def _positive_int_env(
    values: Mapping[str, str],
    name: str,
    *,
    maximum: int,
) -> int:
    raw = _required_env(values, name)
    if _INTEGER_PATTERN.fullmatch(raw) is None:
        raise DtsSourceMetadataObserverError(f"{name}_INVALID")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise DtsSourceMetadataObserverError(f"{name}_INVALID")
    return value


def _positive_float_env(
    values: Mapping[str, str],
    name: str,
    *,
    maximum: float,
) -> float:
    raw = _required_env(values, name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise DtsSourceMetadataObserverError(f"{name}_INVALID") from exc
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise DtsSourceMetadataObserverError(f"{name}_INVALID")
    return value


def _resolved_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError as exc:
        raise DtsSourceMetadataObserverError(
            "TIT_DTS_SOURCE_METADATA_PATH_INVALID"
        ) from exc


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DtsSourceMetadataObserverError(
                "DTS_SOURCE_METADATA_INPUT_JSON_KEY_DUPLICATE"
            )
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> None:
    del value
    raise DtsSourceMetadataObserverError(
        "DTS_SOURCE_METADATA_INPUT_JSON_INVALID"
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "DtsSourceMetadataEvent",
    "DtsSourceMetadataObservationReport",
    "DtsSourceMetadataObserverError",
    "DtsSourceMetadataObserverSettings",
    "OfficialJavaEventJsonlSource",
    "metadata_event_from_official_java_envelope",
    "observe_official_java_metadata",
    "write_observation_report_atomic",
]
