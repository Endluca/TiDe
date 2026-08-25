"""Pure v2 source-version routing contract.

The active v1 projectors deliberately do not import this module.  It defines
the protected, append-only payload that the v2 ingest path will persist before
domain projection is enabled.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from .dts_source_consumer import (
    DATA_OPERATIONS,
    DOMESTIC_STUDENT_ID_FIELDS,
    SOURCE_FIELD_WHITELIST,
    SUPPORTED_REGIONS,
    DtsChangeEvent,
    DtsRecordError,
    _assert_domestic_event_protected_with_fingerprint,
    _attest_v2_source_image_completeness,
    _has_v2_source_image_completeness_proof,
    _source_event_proof_fingerprint,
)
from .dts_source_profile_registry_v2 import (
    DtsSourceProfileManifestError,
    load_dts_source_profile_registry,
)


RETIRED_CONTROL_SUFFIXES = frozenset(
    {
        "qa_ac_classroom_record",
        "qa_task_fake_early_leave_record",
    }
)

V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION = {
    "ovs": frozenset(
        {
            "appoint",
            "complaint",
            "grading_label",
            "grading_label_log",
            "qa_task_close_camera_record",
            "teacher_blacklist",
            "teacher_favorite",
            "user_complaint",
            "user_teacher_grading",
        }
    ),
    "dom": frozenset(
        {
            "appoint",
            "complaint",
            "complaint_cate",
            "grading_label",
            "grading_label_log",
            "qa_task_close_camera_record",
            "teacher",
            "teacher_absent_reason",
            "teacher_blacklist",
            "teacher_certification",
            "teacher_class_schedule",
            "teacher_favorite",
            "teacher_penalty",
            "user_complaint",
            "user_teacher_grading",
        }
    ),
}

V2_BUSINESS_SOURCE_TABLES = frozenset(
    f"{region}_{suffix}"
    for region, suffixes in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION.items()
    for suffix in suffixes
)

V2_KNOWN_SOURCE_TABLES = frozenset(
    V2_BUSINESS_SOURCE_TABLES
    | {
        f"{region}_{suffix}"
        for region in SUPPORTED_REGIONS
        for suffix in RETIRED_CONTROL_SUFFIXES
    }
)

# These values remain temporarily available to the v1 compatibility projector,
# but they are not legal v2 business evidence.
_V2_REMOVED_BUSINESS_FIELDS = {
    "appoint": frozenset({"cancel_reason"}),
    "teacher_absent_reason": frozenset({"reason_desc"}),
}

V2_SOURCE_FIELD_WHITELIST = {
    suffix: SOURCE_FIELD_WHITELIST[suffix]
    - _V2_REMOVED_BUSINESS_FIELDS.get(suffix, frozenset())
    for suffixes in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION.values()
    for suffix in suffixes
}

# The versioned production manifest is empty until physical DTS fields, update
# image behaviour and primary-key types have approved evidence.  It is loaded
# independently from the v1 target whitelist, so a whitelist can never
# self-attest a profile.
V2_SOURCE_PROFILE_REGISTRY = load_dts_source_profile_registry(
    allowed_tables=V2_BUSINESS_SOURCE_TABLES,
)
for _profile_table, _profile in (
    V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
):
    _profile_suffix = _profile_table.removeprefix(f"{_profile.region}_")
    if not _profile.persisted_protected_fields.issubset(
        V2_SOURCE_FIELD_WHITELIST[_profile_suffix]
    ):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PERSISTED_FIELD_NOT_ALLOWED"
        )
V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE: dict[str, frozenset[str]] = {
    table: profile.selected_raw_fields
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_SELECTED_FIELD_SET_POLICY_BY_TABLE: dict[str, str] = {
    table: profile.selected_field_set_policy
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_IMAGE_MODES_BY_TABLE: dict[str, Mapping[str, str]] = {
    table: profile.image_modes
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_PERSISTED_PROTECTED_FIELDS_BY_TABLE: dict[str, frozenset[str]] = {
    table: profile.persisted_protected_fields
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_PROTECTED_DERIVED_FIELDS_BY_TABLE: dict[str, frozenset[str]] = {
    table: profile.protected_derived_fields
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE: dict[str, str] = {
    table: profile.primary_key_type
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE: dict[str, str] = {
    table: profile.profile_id
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_FIELD_TYPE_EVIDENCE_BY_TABLE: dict[str, Mapping[str, str]] = {
    table: profile.field_type_evidence
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}
V2_SOURCE_RAW_FIELD_TYPE_NUMBERS_BY_TABLE: dict[str, Mapping[str, int]] = {
    table: profile.raw_field_type_numbers
    for table, profile in V2_SOURCE_PROFILE_REGISTRY.profiles_by_table.items()
}


def _event_contract_profile_id(table: str) -> str | None:
    if table not in V2_BUSINESS_SOURCE_TABLES:
        return None
    return f"dts-event-fields:v1:{table}"


def v2_source_profile_id(table: str) -> str | None:
    """Return an approved physical profile or the code-owned event contract.

    Fresh-start does not reconstruct rows that predate the configured start.
    A new INSERT is therefore its own baseline; later sparse changes merge only
    into that persisted baseline.  The fallback identifies the versioned
    allow-list contract and does not claim knowledge of the physical source
    table schema.
    """

    return V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE.get(
        table
    ) or _event_contract_profile_id(table)


def _uses_event_field_contract(table: str) -> bool:
    return (
        table in V2_BUSINESS_SOURCE_TABLES
        and table not in V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE
    )


def _persisted_fields_for_table(table: str, region: str) -> frozenset[str] | None:
    configured = V2_PERSISTED_PROTECTED_FIELDS_BY_TABLE.get(table)
    if configured is not None:
        return configured
    prefix = f"{region}_"
    if not table.startswith(prefix):
        return None
    suffix = table.removeprefix(prefix)
    fields = V2_SOURCE_FIELD_WHITELIST.get(suffix)
    if fields is None:
        return None
    if region == "dom":
        return fields - DOMESTIC_STUDENT_ID_FIELDS
    return fields


V2_EVENT_CONTRACT_MANIFEST_SHA256 = hashlib.sha256(
    json.dumps(
        [
            {
                "source_region": table.split("_", 1)[0],
                "source_table": table,
                "profile_id": _event_contract_profile_id(table),
                "persisted_fields": sorted(
                    _persisted_fields_for_table(
                        table,
                        table.split("_", 1)[0],
                    )
                    or ()
                ),
            }
            for table in sorted(V2_BUSINESS_SOURCE_TABLES)
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class V2ValidatedCurrentRow:
    """A current row whose v2 profile and typed identity were persisted."""

    source_region: str
    source_table: str
    source_key: str
    source_key_type: str
    source_schema_profile_id: str
    source_field_types: Mapping[str, str]
    row: Mapping[str, Any]
    provenance_state: str = "V2_CONFIRMED"

    def __post_init__(self) -> None:
        if self.source_region not in SUPPORTED_REGIONS:
            raise ValueError("validated current source_region is invalid")
        if not self.source_table or not self.source_key:
            raise ValueError("validated current identity is incomplete")
        if self.provenance_state != "V2_CONFIRMED":
            raise ValueError("validated current provenance is not confirmed")
        if not self.source_schema_profile_id:
            raise ValueError("validated current profile is missing")
        if not isinstance(self.row, Mapping):
            raise ValueError("validated current row is invalid")
        if any(not isinstance(field_name, str) for field_name in self.row):
            raise ValueError("validated current row field name is invalid")
        if not isinstance(self.source_field_types, Mapping):
            raise ValueError("validated current field types are invalid")
        if any(
            not isinstance(field_name, str)
            for field_name in self.source_field_types
        ):
            raise ValueError(
                "validated current field type name is invalid"
            )
        normalized_key_type = _normalized_source_key_type(
            self.source_key_type
        )
        if normalized_key_type is None:
            raise ValueError("validated current source_key_type is missing")
        expected_key_type = V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE.get(
            self.source_table
        )
        if (
            expected_key_type is not None
            and normalized_key_type != expected_key_type
        ):
            raise ValueError(
                "validated current primary key type differs from its profile"
            )
        _, normalized_source_key = _canonical_source_id(
            self.source_key,
            source_type=normalized_key_type,
        )
        object.__setattr__(self, "source_key_type", normalized_key_type)
        object.__setattr__(self, "source_key", normalized_source_key)
        normalized_field_types = {
            str(field_name): _normalized_source_field_type(field_type)
            for field_name, field_type in self.source_field_types.items()
        }
        missing_field_types = {
            str(field_name)
            for field_name, value in self.row.items()
            if value is not None and str(field_name) not in normalized_field_types
        }
        if missing_field_types:
            raise ValueError("validated current typed evidence is incomplete")
        if normalized_field_types.get("id") != normalized_key_type:
            raise ValueError("validated current primary key type is inconsistent")
        expected_field_types = V2_SOURCE_FIELD_TYPE_EVIDENCE_BY_TABLE.get(
            self.source_table,
            {},
        )
        if any(
            field_name in normalized_field_types
            and normalized_field_types[field_name] != expected_type
            for field_name, expected_type in expected_field_types.items()
        ):
            raise ValueError(
                "validated current field type differs from its profile"
            )
        object.__setattr__(
            self,
            "source_field_types",
            normalized_field_types,
        )


@dataclass(frozen=True)
class V2SourceRouteDecision:
    """Payload-free control decision or protected business version draft."""

    route_status: str
    source_table: str | None
    operation: str
    source_key: str | None = None
    source_key_type: str | None = None
    source_key_data_json: str | None = None
    source_key_numeric: Decimal | None = None
    source_key_text: str | None = None
    source_schema_profile_id: str | None = None
    source_field_types: Mapping[str, str] | None = None
    before_row: Mapping[str, Any] | None = None
    after_row: Mapping[str, Any] | None = None
    protected_payload_hash: str | None = None

    @property
    def should_append_version(self) -> bool:
        return self.route_status == "VERSIONED"


def with_v2_source_image_completeness(
    event: DtsChangeEvent,
) -> DtsChangeEvent:
    """Derive completeness from the versioned source-schema profile.

    This must run immediately after ``build_change_event`` and before DOM
    student protection removes raw source aliases.  Callers cannot self-report
    completeness: only a table profile whose complete selected column set is
    present on every required image may enable first-event reconstruction.
    """

    table = event.table_name or ""
    expected_fields = V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE.get(table)
    if event.operation not in DATA_OPERATIONS:
        return replace(
            event,
            source_images_complete=False,
            source_image_profile_id=None,
            _v2_source_image_completeness_proof=None,
            _domestic_protection_proof=None,
        )
    if expected_fields is None:
        profile_id = _event_contract_profile_id(table)
        if profile_id is None:
            return replace(
                event,
                source_images_complete=False,
                source_image_profile_id=None,
                _v2_source_image_completeness_proof=None,
                _domestic_protection_proof=None,
            )
        _assert_event_matches_source_profile(event, table=table)
        before, after = _required_images(event)
        _require_authoritative_identity_images(
            operation=event.operation,
            before=before,
            after=after,
        )
        if event.operation == "INSERT":
            return _attest_v2_source_image_completeness(
                event,
                source_image_profile_id=profile_id,
            )
        return replace(
            event,
            source_images_complete=False,
            source_image_profile_id=None,
            _v2_source_image_completeness_proof=None,
            _domestic_protection_proof=None,
        )
    _assert_event_matches_source_profile(event, table=table)
    complete = _raw_source_images_are_complete(
        event,
        table=table,
        selected_raw_fields=expected_fields,
    )
    if complete:
        return _attest_v2_source_image_completeness(
            event,
            source_image_profile_id=(
                V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE[table]
            ),
        )
    return replace(
        event,
        source_images_complete=False,
        source_image_profile_id=None,
        _v2_source_image_completeness_proof=None,
        _domestic_protection_proof=None,
    )


def build_v2_source_route(
    event: DtsChangeEvent,
    *,
    current: V2ValidatedCurrentRow | None = None,
) -> V2SourceRouteDecision:
    """Build one complete protected version draft after current-row merging."""

    if event.source_region not in SUPPORTED_REGIONS:
        raise DtsRecordError("DTS_SOURCE_REGION_UNSUPPORTED")
    if event.operation not in DATA_OPERATIONS:
        return V2SourceRouteDecision(
            route_status="IGNORED_CONTROL_RECORD",
            source_table=event.table_name,
            operation=event.operation,
        )

    table = event.table_name or ""
    prefix = f"{event.source_region}_"
    if not table.startswith(prefix):
        if table in V2_KNOWN_SOURCE_TABLES:
            raise DtsRecordError("DTS_SOURCE_REGION_TABLE_MISMATCH")
        return V2SourceRouteDecision(
            route_status="IGNORED_TABLE_OUTSIDE_REGION_PROFILE",
            source_table=event.table_name,
            operation=event.operation,
        )
    suffix = table.removeprefix(prefix)
    if suffix in RETIRED_CONTROL_SUFFIXES:
        return V2SourceRouteDecision(
            route_status="IGNORED_RETIRED_SOURCE",
            source_table=table,
            operation=event.operation,
        )
    if suffix not in V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION[event.source_region]:
        return V2SourceRouteDecision(
            route_status="IGNORED_TABLE_NOT_IN_V2_WHITELIST",
            source_table=table,
            operation=event.operation,
        )
    whitelist = V2_SOURCE_FIELD_WHITELIST[suffix]
    if not isinstance(event.source_images_complete, bool):
        raise DtsRecordError("DTS_SOURCE_IMAGE_COMPLETENESS_INVALID")
    if not isinstance(event.source_field_types, Mapping):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPES_INVALID")
    for field_name, source_type in event.source_field_types.items():
        if not isinstance(field_name, str):
            raise DtsRecordError("DTS_SOURCE_FIELD_TYPES_INVALID")
        _normalized_source_field_type(source_type)
    if not isinstance(event.source_field_type_numbers, Mapping):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_NUMBERS_INVALID")
    for field_name, type_number in event.source_field_type_numbers.items():
        if not isinstance(field_name, str) or (
            type(type_number) is not int or type_number < 0
        ):
            raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_NUMBERS_INVALID")
    expected_profile_id = v2_source_profile_id(table)
    if expected_profile_id is None:
        raise DtsRecordError("DTS_SOURCE_SCHEMA_PROFILE_MISSING")
    persisted_fields = _persisted_fields_for_table(
        table,
        event.source_region,
    )
    if persisted_fields is None:
        raise DtsRecordError("DTS_SOURCE_SCHEMA_PROFILE_INCOMPLETE")
    whitelist = persisted_fields
    _assert_event_matches_source_profile(event, table=table)
    if (
        event.source_image_profile_id is not None
        and event.source_image_profile_id != expected_profile_id
    ):
        raise DtsRecordError("DTS_SOURCE_SCHEMA_PROFILE_MISMATCH")
    event_fingerprint = _source_event_proof_fingerprint(event)
    _assert_domestic_event_protected_with_fingerprint(
        event,
        require_process_proof=True,
        event_fingerprint=event_fingerprint,
    )
    if current is not None and (
        current.source_region != event.source_region
        or current.source_table != table
        or current.source_schema_profile_id != expected_profile_id
    ):
        raise DtsRecordError("DTS_SOURCE_CURRENT_PROVENANCE_MISMATCH")
    if current is not None and any(
        field_name not in whitelist for field_name in current.row
    ):
        raise DtsRecordError("DTS_SOURCE_CURRENT_PROVENANCE_MISMATCH")

    before_patch, after_patch = _required_images(event)
    _require_authoritative_identity_images(
        operation=event.operation,
        before=before_patch,
        after=after_patch,
    )
    _validate_source_field_shapes(before_patch, whitelist)
    _validate_source_field_shapes(after_patch, whitelist)
    complete_profile_proof = _has_v2_source_image_completeness_proof(
        event,
        expected_profile_id=expected_profile_id,
        event_fingerprint=event_fingerprint,
    ) and (
        _uses_event_field_contract(table)
        or _protected_images_retain_complete_shape(
            event,
            expected_fields=V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE[table],
        )
    )
    if (
        event.source_images_complete
        or event.source_image_profile_id is not None
        or event._v2_source_image_completeness_proof is not None
    ) and not complete_profile_proof:
        raise DtsRecordError("DTS_SOURCE_IMAGE_COMPLETENESS_PROOF_INVALID")
    if event.operation == "INSERT":
        if not complete_profile_proof:
            raise DtsRecordError("DTS_SOURCE_IMAGE_INCOMPLETE")
    protected_current = _whitelisted_image(
        None if current is None else current.row,
        whitelist,
    )
    _validate_source_field_shapes(protected_current, whitelist)
    has_existing_current = protected_current is not None
    protected_before_patch = _whitelisted_image(before_patch, whitelist)
    protected_after_patch = _whitelisted_image(after_patch, whitelist)
    if event.operation in {"UPDATE", "DELETE"} and protected_current is None:
        if not complete_profile_proof:
            return V2SourceRouteDecision(
                route_status="WAITING_CURRENT_ROW",
                source_table=table,
                operation=event.operation,
            )
        if event.operation == "UPDATE":
            protected_current = dict(protected_after_patch or {})
            protected_current.update(protected_before_patch or {})
        else:
            protected_current = dict(protected_before_patch or {})

    protected_before, protected_after = _merge_complete_images(
        operation=event.operation,
        current_row=protected_current,
        before_patch=protected_before_patch,
        after_patch=protected_after_patch,
    )
    event_key_type = _normalized_source_key_type(
        event.source_field_types.get("id")
    )
    normalized_current_key_type = _normalized_source_key_type(
        None if current is None else current.source_key_type
    )
    if (
        event_key_type is not None
        and normalized_current_key_type is not None
        and event_key_type != normalized_current_key_type
    ):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_DRIFT")
    effective_key_type = event_key_type or normalized_current_key_type
    expected_key_type = V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE.get(table)
    if effective_key_type is None or (
        expected_key_type is not None
        and effective_key_type != expected_key_type
    ):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_MISMATCH")
    identity_images: list[Mapping[str, Any]] = []
    if has_existing_current:
        assert protected_current is not None
        identity_images.append(protected_current)
    if protected_before is not None:
        identity_images.append(protected_before)
    if protected_after is not None:
        identity_images.append(protected_after)
    canonical_identities = [
        _canonical_source_id(
            image.get("id"),
            source_type=effective_key_type,
        )
        for image in identity_images
    ]
    if not canonical_identities:
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_MISSING")
    source_key_type, source_key = canonical_identities[0]
    if any(
        identity != (source_key_type, source_key)
        for identity in canonical_identities[1:]
    ):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED")
    if current is not None and (
        current.source_key_type != source_key_type
        or current.source_key != source_key
    ):
        raise DtsRecordError("DTS_SOURCE_CURRENT_IDENTITY_MISMATCH")
    source_field_types = _complete_source_field_types(
        event=event,
        current=current,
        before_patch=before_patch,
        after_patch=after_patch,
        protected_before=protected_before,
        protected_after=protected_after,
        whitelist=whitelist,
    )
    source_key_numeric = (
        Decimal(source_key) if source_key_type == "NUMERIC" else None
    )
    source_key_text = source_key if source_key_type == "TEXT" else None
    source_key_data_json = (
        f'{{"id":{source_key}}}'
        if source_key_numeric is not None
        else json.dumps(
            {"id": source_key_text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    payload = {
        "source_region": event.source_region,
        "source_table": table,
        "source_key_type": source_key_type,
        "source_key": source_key,
        "source_schema_profile_id": expected_profile_id,
        "source_field_types": source_field_types,
        "operation": event.operation,
        "before_row": protected_before,
        "after_row": protected_after,
    }
    payload_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return V2SourceRouteDecision(
        route_status="VERSIONED",
        source_table=table,
        operation=event.operation,
        source_key=source_key,
        source_key_type=source_key_type,
        source_key_data_json=source_key_data_json,
        source_key_numeric=source_key_numeric,
        source_key_text=source_key_text,
        source_schema_profile_id=expected_profile_id,
        source_field_types=source_field_types,
        before_row=protected_before,
        after_row=protected_after,
        protected_payload_hash=payload_hash,
    )


def _assert_event_matches_source_profile(
    event: DtsChangeEvent,
    *,
    table: str,
) -> None:
    """Reject observed type evidence that drifts from the attested profile."""

    if not isinstance(event.source_field_types, Mapping):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPES_INVALID")
    for field_name, source_type in event.source_field_types.items():
        if not isinstance(field_name, str):
            raise DtsRecordError("DTS_SOURCE_FIELD_TYPES_INVALID")
        _normalized_source_field_type(source_type)
    expected_field_types = V2_SOURCE_FIELD_TYPE_EVIDENCE_BY_TABLE.get(
        table,
        {},
    )
    if not isinstance(event.source_field_type_numbers, Mapping):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_NUMBERS_INVALID")
    for field_name, type_number in event.source_field_type_numbers.items():
        if not isinstance(field_name, str) or (
            type(type_number) is not int or type_number < 0
        ):
            raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_NUMBERS_INVALID")
    expected_type_numbers = V2_SOURCE_RAW_FIELD_TYPE_NUMBERS_BY_TABLE.get(
        table,
        {},
    )
    for field_name, expected_number in expected_type_numbers.items():
        if field_name not in event.source_field_type_numbers:
            raise DtsRecordError(
                "DTS_SOURCE_FIELD_TYPE_NUMBER_EVIDENCE_MISSING"
            )
        if event.source_field_type_numbers[field_name] != expected_number:
            raise DtsRecordError(
                "DTS_SOURCE_FIELD_TYPE_NUMBER_PROFILE_MISMATCH"
            )
    for field_name, expected_type in expected_field_types.items():
        actual_type = event.source_field_types.get(field_name)
        if actual_type is not None:
            if _normalized_source_field_type(actual_type) != expected_type:
                raise DtsRecordError(
                    "DTS_SOURCE_FIELD_TYPE_PROFILE_MISMATCH"
                )
            continue
        if field_name not in expected_type_numbers:
            raise DtsRecordError(
                "DTS_SOURCE_FIELD_TYPE_EVIDENCE_MISSING"
            )


def _raw_source_images_are_complete(
    event: DtsChangeEvent,
    *,
    table: str,
    selected_raw_fields: frozenset[str],
) -> bool:
    if V2_SOURCE_SELECTED_FIELD_SET_POLICY_BY_TABLE.get(table) != "EXACT":
        raise DtsRecordError("DTS_SOURCE_FIELD_SET_POLICY_INVALID")
    image_modes = V2_SOURCE_IMAGE_MODES_BY_TABLE.get(table)
    if image_modes is None or event.operation not in image_modes:
        raise DtsRecordError("DTS_SOURCE_IMAGE_MODE_MISSING")
    image_mode = image_modes[event.operation]
    if image_mode not in {"FULL", "SPARSE"} or (
        event.operation == "INSERT" and image_mode != "FULL"
    ):
        raise DtsRecordError("DTS_SOURCE_IMAGE_MODE_INVALID")
    required_images = (
        (event.before, event.after)
        if event.operation == "UPDATE"
        else (event.before,)
        if event.operation == "DELETE"
        else (event.after,)
    )
    complete = True
    for image in required_images:
        if image is None or not isinstance(image, Mapping):
            complete = False
            continue
        if any(not isinstance(field_name, str) for field_name in image):
            raise DtsRecordError("DTS_SOURCE_IMAGE_FIELD_NAME_INVALID")
        image_fields = frozenset(image)
        if not image_fields.issubset(selected_raw_fields):
            raise DtsRecordError("DTS_SOURCE_IMAGE_FIELD_SET_UNEXPECTED")
        if image_fields != selected_raw_fields:
            complete = False
            if image_mode == "FULL" and event.operation != "INSERT":
                raise DtsRecordError("DTS_SOURCE_IMAGE_MODE_VIOLATION")
    return complete


def _required_images(
    event: DtsChangeEvent,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    before = event.before
    after = event.after
    if before is not None and not isinstance(before, Mapping):
        raise DtsRecordError("DTS_SOURCE_IMAGE_INVALID")
    if after is not None and not isinstance(after, Mapping):
        raise DtsRecordError("DTS_SOURCE_IMAGE_INVALID")
    if event.operation == "INSERT" and after is None:
        raise DtsRecordError("DTS_SOURCE_AFTER_IMAGE_MISSING")
    if event.operation == "DELETE" and before is None:
        raise DtsRecordError("DTS_SOURCE_BEFORE_IMAGE_MISSING")
    if event.operation == "UPDATE" and (before is None or after is None):
        raise DtsRecordError("DTS_SOURCE_UPDATE_IMAGES_REQUIRED")
    return before, after


def _protected_images_retain_complete_shape(
    event: DtsChangeEvent,
    *,
    expected_fields: frozenset[str],
) -> bool:
    required_images = (
        (event.before, event.after)
        if event.operation == "UPDATE"
        else (event.before,)
        if event.operation == "DELETE"
        else (event.after,)
    )
    protected_expected_fields = (
        expected_fields - DOMESTIC_STUDENT_ID_FIELDS
        if event.source_region == "dom"
        else expected_fields
    )
    protected_derived_fields = V2_PROTECTED_DERIVED_FIELDS_BY_TABLE.get(
        event.table_name or "",
        frozenset(),
    )
    allowed_fields = protected_expected_fields | protected_derived_fields
    return all(
        isinstance(image, Mapping)
        and protected_expected_fields.issubset(image.keys())
        and set(image).issubset(allowed_fields)
        for image in required_images
    )


def _require_authoritative_identity_images(
    *,
    operation: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> None:
    required = (
        (after,)
        if operation == "INSERT"
        else (before,)
        if operation == "DELETE"
        else (before, after)
    )
    if any(image is None or image.get("id") is None for image in required):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_MISSING")


def _validate_source_field_shapes(
    image: Mapping[str, Any] | None,
    whitelist: frozenset[str],
) -> None:
    if image is None:
        return
    for field_name, value in image.items():
        if field_name not in whitelist or value is None:
            continue
        if isinstance(value, Mapping) or (
            isinstance(value, (list, tuple, set, bytes, bytearray))
        ):
            raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")


def _merge_complete_images(
    *,
    operation: str,
    current_row: Mapping[str, Any] | None,
    before_patch: Mapping[str, Any] | None,
    after_patch: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if operation == "INSERT":
        return None, after_patch
    if operation == "DELETE":
        complete_before = dict(current_row or {})
        complete_before.update(before_patch or {})
        return complete_before, None
    complete_before = dict(current_row or {})
    complete_before.update(before_patch or {})
    complete_after = dict(complete_before)
    complete_after.update(after_patch or {})
    return complete_before, complete_after


def _canonical_source_id(
    value: Any,
    *,
    source_type: str | None,
) -> tuple[str, str]:
    if isinstance(value, bool) or value is None:
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_MISSING")
    normalized_type = _normalized_source_key_type(source_type)
    if normalized_type == "TEXT":
        if not isinstance(value, str):
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_INVALID")
        if value == "":
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_MISSING")
        return "TEXT", value
    if normalized_type == "NUMERIC" or isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_INVALID")
        try:
            numeric = Decimal(str(value))
        except InvalidOperation as exc:
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_INVALID") from exc
        if not numeric.is_finite():
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_INVALID")
        rendered = format(numeric, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        if rendered in {"", "-0"}:
            rendered = "0"
        return "NUMERIC", rendered
    if isinstance(value, str):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_MISSING")
    raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_INVALID")


def _normalized_source_key_type(source_type: Any) -> str | None:
    if source_type is None:
        return None
    if not isinstance(source_type, str):
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_INVALID")
    normalized_type = source_type.upper()
    if normalized_type not in {"NUMERIC", "TEXT"}:
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_TYPE_INVALID")
    return normalized_type


def _normalized_source_field_type(source_type: Any) -> str:
    if not isinstance(source_type, str):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    normalized_type = source_type.upper()
    if normalized_type not in {
        "NUMERIC",
        "TEXT",
        "BOOLEAN",
        "TEMPORAL",
    }:
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    return normalized_type


def _complete_source_field_types(
    *,
    event: DtsChangeEvent,
    current: V2ValidatedCurrentRow | None,
    before_patch: Mapping[str, Any] | None,
    after_patch: Mapping[str, Any] | None,
    protected_before: Mapping[str, Any] | None,
    protected_after: Mapping[str, Any] | None,
    whitelist: frozenset[str],
) -> dict[str, str]:
    """Build typed evidence for every non-null value in the version fact."""

    included_fields = {
        field_name
        for image in (protected_before, protected_after)
        if image is not None
        for field_name in image
    }
    evidence: dict[str, str] = {}

    def add_image(
        image: Mapping[str, Any] | None,
        explicit_types: Mapping[str, str],
    ) -> None:
        if image is None:
            return
        for raw_field_name, value in image.items():
            field_name = str(raw_field_name)
            if (
                field_name not in whitelist
                or field_name not in included_fields
                or value is None
            ):
                continue
            explicit_type = explicit_types.get(field_name)
            field_type = (
                _normalized_source_field_type(explicit_type)
                if explicit_type is not None
                else _inferred_source_field_type(value)
            )
            _validate_source_value_for_type(value, field_type)
            previous = evidence.get(field_name)
            if previous is not None and previous != field_type:
                raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_DRIFT")
            evidence[field_name] = field_type

    if current is not None:
        add_image(current.row, current.source_field_types)
    add_image(before_patch, event.source_field_types)
    add_image(after_patch, event.source_field_types)

    non_null_fields = {
        field_name
        for image in (protected_before, protected_after)
        if image is not None
        for field_name, value in image.items()
        if value is not None
    }
    if not non_null_fields.issubset(evidence):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_EVIDENCE_MISSING")
    return {
        field_name: evidence[field_name]
        for field_name in sorted(evidence)
        if field_name in non_null_fields
    }


def _inferred_source_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float, Decimal)):
        return "NUMERIC"
    if isinstance(value, (datetime, date, time)):
        return "TEMPORAL"
    if isinstance(value, str):
        return "TEXT"
    raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")


def _validate_source_value_for_type(value: Any, source_type: str) -> None:
    if source_type == "BOOLEAN":
        if isinstance(value, bool):
            return
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    if source_type == "TEXT":
        if isinstance(value, str):
            return
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    if source_type == "TEMPORAL":
        if isinstance(value, (str, datetime, date, time)):
            return
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    if isinstance(value, bool):
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsRecordError("DTS_SOURCE_FIELD_TYPE_INVALID") from exc
    if not numeric.is_finite():
        raise DtsRecordError("DTS_NON_FINITE_NUMBER_NOT_ALLOWED")


def _whitelisted_image(
    image: Mapping[str, Any] | None,
    whitelist: frozenset[str],
) -> Mapping[str, Any] | None:
    if image is None:
        return None
    return {
        name: _json_value(value)
        for name, value in image.items()
        if name in whitelist
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DtsRecordError("DTS_NON_STRING_JSON_KEY_NOT_ALLOWED")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DtsRecordError("DTS_NON_FINITE_NUMBER_NOT_ALLOWED")
        return format(value, "f")
    if isinstance(value, bytes):
        raise DtsRecordError("DTS_BINARY_FIELD_NOT_ALLOWED_IN_V2_VERSION")
    if isinstance(value, float) and not math.isfinite(value):
        raise DtsRecordError("DTS_NON_FINITE_NUMBER_NOT_ALLOWED")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DtsRecordError("DTS_SOURCE_VALUE_TYPE_NOT_ALLOWED")
