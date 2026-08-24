"""Strict, versioned DTS v2 physical source-profile registry.

The registry is deliberately independent from the v1 target-field allow-list.
A target allow-list says what TIT may retain; it is not evidence of which
physical columns DTS selects or whether an image is complete.  Production
therefore loads an empty manifest until an approved evidence artifact is
reviewed and pinned by SHA-256.

This module also contains a value-blind metadata aggregator for a separately
authorised diagnostic consumer group.  It reads only event metadata and image
keys; source values, record identities, offsets, topics and student values are
never emitted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


DEFAULT_DTS_SOURCE_PROFILE_MANIFEST_PATH = Path(__file__).with_name(
    "dts_source_profiles_v2.json"
)

_MANIFEST_VERSION = 1
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_PROFILE_COUNT = 512
_MAX_OBSERVATION_SIGNATURES = 4_096
_SUPPORTED_REGIONS = frozenset({"dom", "ovs"})
_SOURCE_KEY_TYPES = frozenset({"NUMERIC", "TEXT"})
_SOURCE_FIELD_TYPES = frozenset(
    {"NUMERIC", "TEXT", "BOOLEAN", "TEMPORAL"}
)
_PROTECTED_DERIVED_FIELDS = frozenset({"student_token"})
_DOMESTIC_STUDENT_ID_FIELDS = frozenset(
    {"s_id", "student_id", "stu_id", "user_id"}
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DtsSourceProfileManifestError(ValueError):
    """A stable, payload-free source-profile manifest validation error."""


@dataclass(frozen=True)
class DtsSourceProfile:
    """One attested DTS source-table profile.

    ``selected_raw_fields`` are the exact physical DTS selection before any
    privacy transformation. ``persisted_protected_fields`` are the fields TIT
    may retain after protection. ``protected_derived_fields`` are created by
    TIT and must never be used to prove a raw image complete.
    """

    table: str
    region: str
    primary_key_type: str
    selected_raw_fields: frozenset[str]
    selected_field_set_policy: str
    persisted_protected_fields: frozenset[str]
    protected_derived_fields: frozenset[str]
    image_modes: Mapping[str, str]
    field_type_evidence: Mapping[str, str]
    raw_field_type_numbers: Mapping[str, int]
    evidence_provenance: str
    evidence_sha256: str
    profile_id: str


@dataclass(frozen=True)
class DtsSourceProfileRegistry:
    manifest_version: int
    artifact_sha256: str
    manifest_sha256: str
    profile_vector: tuple[Mapping[str, str], ...]
    profiles_by_table: Mapping[str, DtsSourceProfile]

    def profile_for(self, table: str) -> DtsSourceProfile | None:
        return self.profiles_by_table.get(table)


def load_dts_source_profile_registry(
    path: str | Path = DEFAULT_DTS_SOURCE_PROFILE_MANIFEST_PATH,
    *,
    allowed_tables: Iterable[str] | None = None,
) -> DtsSourceProfileRegistry:
    """Load and fully validate a source-profile manifest.

    Missing, malformed, ambiguous or partially attested manifests fail closed.
    ``allowed_tables`` lets the caller constrain profiles to its own business
    routing registry without coupling this physical-schema loader to v1.
    """

    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_MANIFEST_MISSING"
        ) from exc
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_MANIFEST_SIZE_INVALID"
        )
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_MANIFEST_JSON_INVALID"
        ) from exc
    except DtsSourceProfileManifestError:
        raise

    root = _require_mapping(document, "DTS_SOURCE_PROFILE_MANIFEST_INVALID")
    _require_exact_keys(
        root,
        required={"manifest_version", "profiles"},
        optional=set(),
        error="DTS_SOURCE_PROFILE_MANIFEST_KEYS_INVALID",
    )
    if type(root["manifest_version"]) is not int or root["manifest_version"] != 1:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_MANIFEST_VERSION_UNSUPPORTED"
        )
    profile_documents = root["profiles"]
    if not isinstance(profile_documents, list) or (
        len(profile_documents) > _MAX_PROFILE_COUNT
    ):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_LIST_INVALID"
        )

    allowed = None if allowed_tables is None else frozenset(allowed_tables)
    profiles: dict[str, DtsSourceProfile] = {}
    for profile_document in profile_documents:
        profile = _parse_profile(profile_document)
        if allowed is not None and profile.table not in allowed:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_TABLE_NOT_ALLOWED"
            )
        if profile.table in profiles:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_TABLE_DUPLICATE"
            )
        profiles[profile.table] = profile

    profile_vector_values = tuple(
        {
            "source_region": profile.region,
            "source_table": profile.table,
            "source_schema_profile_id": profile.profile_id,
        }
        for profile in sorted(
            profiles.values(),
            key=lambda item: (item.region, item.table),
        )
    )
    canonical_profile_vector = json.dumps(
        profile_vector_values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DtsSourceProfileRegistry(
        manifest_version=_MANIFEST_VERSION,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        # The database approval contract historically calls the canonical
        # profile-vector hash ``manifest_sha256``.  Keep that public identity
        # exact; the byte-for-byte JSON artifact hash remains separately
        # available as ``artifact_sha256``.
        manifest_sha256=hashlib.sha256(canonical_profile_vector).hexdigest(),
        profile_vector=tuple(
            MappingProxyType(value) for value in profile_vector_values
        ),
        profiles_by_table=MappingProxyType(profiles),
    )


def _parse_profile(value: Any) -> DtsSourceProfile:
    profile = _require_mapping(value, "DTS_SOURCE_PROFILE_INVALID")
    _require_exact_keys(
        profile,
        required={
            "table",
            "region",
            "primary_key_type",
            "selected_raw_fields",
            "selected_field_set_policy",
            "persisted_protected_fields",
            "protected_derived_fields",
            "image_modes",
            "evidence",
        },
        optional={"field_type_evidence", "raw_field_type_numbers"},
        error="DTS_SOURCE_PROFILE_KEYS_INVALID",
    )

    table = _require_identifier(profile["table"], "DTS_SOURCE_PROFILE_TABLE_INVALID")
    region = _require_string(profile["region"], "DTS_SOURCE_PROFILE_REGION_INVALID")
    if region not in _SUPPORTED_REGIONS or not table.startswith(f"{region}_"):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_REGION_TABLE_MISMATCH"
        )
    primary_key_type = _require_string(
        profile["primary_key_type"],
        "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_INVALID",
    )
    if primary_key_type not in _SOURCE_KEY_TYPES:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_INVALID"
        )

    selected_raw_fields = _require_field_list(
        profile["selected_raw_fields"],
        error="DTS_SOURCE_PROFILE_SELECTED_RAW_FIELDS_INVALID",
        allow_empty=False,
    )
    if "id" not in selected_raw_fields:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_FIELD_MISSING"
        )
    if selected_raw_fields & _PROTECTED_DERIVED_FIELDS:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_DERIVED_FIELD_MARKED_RAW"
        )
    selected_field_set_policy = _require_string(
        profile["selected_field_set_policy"],
        "DTS_SOURCE_PROFILE_FIELD_SET_POLICY_INVALID",
    )
    if selected_field_set_policy != "EXACT":
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_FIELD_SET_POLICY_INVALID"
        )

    protected_derived_fields = _require_field_list(
        profile["protected_derived_fields"],
        error="DTS_SOURCE_PROFILE_DERIVED_FIELDS_INVALID",
        allow_empty=True,
    )
    if not protected_derived_fields.issubset(_PROTECTED_DERIVED_FIELDS):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_DERIVED_FIELD_UNSUPPORTED"
        )
    if selected_raw_fields & protected_derived_fields:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_RAW_DERIVED_FIELDS_OVERLAP"
        )
    if region != "dom" and protected_derived_fields:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_DERIVED_FIELD_REGION_INVALID"
        )
    if "student_token" in protected_derived_fields and not (
        selected_raw_fields & _DOMESTIC_STUDENT_ID_FIELDS
    ):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_STUDENT_TOKEN_SOURCE_MISSING"
        )

    persisted_protected_fields = _require_field_list(
        profile["persisted_protected_fields"],
        error="DTS_SOURCE_PROFILE_PERSISTED_FIELDS_INVALID",
        allow_empty=False,
    )
    allowed_protected_fields = (
        selected_raw_fields - _DOMESTIC_STUDENT_ID_FIELDS
        if region == "dom"
        else selected_raw_fields
    ) | protected_derived_fields
    if not persisted_protected_fields.issubset(allowed_protected_fields):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PERSISTED_FIELD_NOT_PROTECTED"
        )
    if "id" not in persisted_protected_fields:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PERSISTED_PRIMARY_KEY_MISSING"
        )
    if not protected_derived_fields.issubset(persisted_protected_fields):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_DERIVED_FIELD_NOT_PERSISTED"
        )

    image_modes_document = _require_mapping(
        profile["image_modes"],
        "DTS_SOURCE_PROFILE_IMAGE_MODES_INVALID",
    )
    _require_exact_keys(
        image_modes_document,
        required={"INSERT", "UPDATE", "DELETE"},
        optional=set(),
        error="DTS_SOURCE_PROFILE_IMAGE_MODES_INVALID",
    )
    image_modes: dict[str, str] = {}
    for operation in ("INSERT", "UPDATE", "DELETE"):
        mode = _require_string(
            image_modes_document[operation],
            "DTS_SOURCE_PROFILE_IMAGE_MODE_INVALID",
        )
        if mode not in {"FULL", "SPARSE"}:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_IMAGE_MODE_INVALID"
            )
        image_modes[operation] = mode
    if image_modes["INSERT"] != "FULL":
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_INSERT_IMAGE_MUST_BE_FULL"
        )

    field_types_document = profile.get("field_type_evidence", {})
    field_types_mapping = _require_mapping(
        field_types_document,
        "DTS_SOURCE_PROFILE_FIELD_TYPES_INVALID",
    )
    field_type_evidence: dict[str, str] = {}
    for raw_field_name, raw_field_type in field_types_mapping.items():
        field_name = _require_identifier(
            raw_field_name,
            "DTS_SOURCE_PROFILE_FIELD_TYPES_INVALID",
        )
        if field_name not in selected_raw_fields:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_FIELD_TYPE_NOT_RAW"
            )
        field_type = _require_string(
            raw_field_type,
            "DTS_SOURCE_PROFILE_FIELD_TYPE_INVALID",
        )
        if field_type not in _SOURCE_FIELD_TYPES:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_FIELD_TYPE_INVALID"
            )
        field_type_evidence[field_name] = field_type
    if (
        field_type_evidence.get("id") is not None
        and field_type_evidence["id"] != primary_key_type
    ):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_EVIDENCE_MISMATCH"
        )

    type_numbers_document = profile.get("raw_field_type_numbers", {})
    type_numbers_mapping = _require_mapping(
        type_numbers_document,
        "DTS_SOURCE_PROFILE_TYPE_NUMBERS_INVALID",
    )
    raw_field_type_numbers: dict[str, int] = {}
    for raw_field_name, raw_type_number in type_numbers_mapping.items():
        field_name = _require_identifier(
            raw_field_name,
            "DTS_SOURCE_PROFILE_TYPE_NUMBERS_INVALID",
        )
        if field_name not in selected_raw_fields:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_TYPE_NUMBER_NOT_RAW"
            )
        if type(raw_type_number) is not int or raw_type_number < 0:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_TYPE_NUMBER_INVALID"
            )
        raw_field_type_numbers[field_name] = raw_type_number
    if raw_field_type_numbers and "id" not in raw_field_type_numbers:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_PRIMARY_KEY_TYPE_NUMBER_MISSING"
        )

    evidence = _require_mapping(
        profile["evidence"],
        "DTS_SOURCE_PROFILE_EVIDENCE_INVALID",
    )
    _require_exact_keys(
        evidence,
        required={"provenance", "sha256"},
        optional=set(),
        error="DTS_SOURCE_PROFILE_EVIDENCE_KEYS_INVALID",
    )
    provenance = _require_string(
        evidence["provenance"],
        "DTS_SOURCE_PROFILE_EVIDENCE_PROVENANCE_INVALID",
        max_length=512,
    )
    if any(ord(character) < 32 for character in provenance):
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_EVIDENCE_PROVENANCE_INVALID"
        )
    evidence_sha256 = _require_string(
        evidence["sha256"],
        "DTS_SOURCE_PROFILE_EVIDENCE_SHA256_INVALID",
    )
    if _SHA256_PATTERN.fullmatch(evidence_sha256) is None:
        raise DtsSourceProfileManifestError(
            "DTS_SOURCE_PROFILE_EVIDENCE_SHA256_INVALID"
        )

    identity = {
        "profile_version": 2,
        "table": table,
        "region": region,
        "primary_key_type": primary_key_type,
        "selected_raw_fields": sorted(selected_raw_fields),
        "selected_field_set_policy": selected_field_set_policy,
        "persisted_protected_fields": sorted(persisted_protected_fields),
        "protected_derived_fields": sorted(protected_derived_fields),
        "image_modes": {
            operation: image_modes[operation]
            for operation in ("INSERT", "UPDATE", "DELETE")
        },
        "field_type_evidence": {
            field_name: field_type_evidence[field_name]
            for field_name in sorted(field_type_evidence)
        },
        "raw_field_type_numbers": {
            field_name: raw_field_type_numbers[field_name]
            for field_name in sorted(raw_field_type_numbers)
        },
        "evidence": {
            "provenance": provenance,
            "sha256": evidence_sha256,
        },
    }
    profile_digest = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return DtsSourceProfile(
        table=table,
        region=region,
        primary_key_type=primary_key_type,
        selected_raw_fields=selected_raw_fields,
        selected_field_set_policy=selected_field_set_policy,
        persisted_protected_fields=persisted_protected_fields,
        protected_derived_fields=protected_derived_fields,
        image_modes=MappingProxyType(image_modes),
        field_type_evidence=MappingProxyType(field_type_evidence),
        raw_field_type_numbers=MappingProxyType(raw_field_type_numbers),
        evidence_provenance=provenance,
        evidence_sha256=evidence_sha256,
        profile_id=f"dts-source-schema:v2:{profile_digest}",
    )


def _require_mapping(value: Any, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise DtsSourceProfileManifestError(error)
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    error: str,
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise DtsSourceProfileManifestError(error)


def _require_string(
    value: Any,
    error: str,
    *,
    max_length: int = 128,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > max_length
    ):
        raise DtsSourceProfileManifestError(error)
    return value


def _require_identifier(value: Any, error: str) -> str:
    identifier = _require_string(value, error)
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise DtsSourceProfileManifestError(error)
    return identifier


def _require_field_list(
    value: Any,
    *,
    error: str,
    allow_empty: bool,
) -> frozenset[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise DtsSourceProfileManifestError(error)
    fields = [_require_identifier(item, error) for item in value]
    if len(set(fields)) != len(fields) or fields != sorted(fields):
        raise DtsSourceProfileManifestError(error)
    return frozenset(fields)


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DtsSourceProfileManifestError(
                "DTS_SOURCE_PROFILE_MANIFEST_JSON_KEY_DUPLICATE"
            )
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> None:
    del value
    raise DtsSourceProfileManifestError(
        "DTS_SOURCE_PROFILE_MANIFEST_JSON_INVALID"
    )


@dataclass(frozen=True, order=True)
class _MetadataObservationSignature:
    table_name: str
    operation: str
    before_presence: str
    before_fields: tuple[str, ...]
    after_presence: str
    after_fields: tuple[str, ...]
    observed_field_types: tuple[tuple[str, str], ...]
    observed_field_type_numbers: tuple[tuple[str, int], ...]


class DtsSourceMetadataAggregator:
    """Aggregate decoded change-event shapes without reading source values."""

    def __init__(self, *, max_signatures: int = _MAX_OBSERVATION_SIGNATURES) -> None:
        if type(max_signatures) is not int or max_signatures <= 0:
            raise ValueError("DTS_SOURCE_METADATA_MAX_SIGNATURES_INVALID")
        self._max_signatures = max_signatures
        self._counts: Counter[_MetadataObservationSignature] = Counter()

    def observe(self, event: Any) -> None:
        table_name = _require_observation_identifier(
            getattr(event, "table_name", None),
            "DTS_SOURCE_METADATA_TABLE_INVALID",
        )
        operation = getattr(event, "operation", None)
        if not isinstance(operation, str) or not operation:
            raise ValueError("DTS_SOURCE_METADATA_OPERATION_INVALID")
        before_presence, before_fields = _image_metadata(
            getattr(event, "before", None)
        )
        after_presence, after_fields = _image_metadata(
            getattr(event, "after", None)
        )
        raw_field_types = getattr(event, "source_field_types", None)
        if not isinstance(raw_field_types, Mapping):
            raise ValueError("DTS_SOURCE_METADATA_FIELD_TYPES_INVALID")
        field_types: list[tuple[str, str]] = []
        for field_name, field_type in raw_field_types.items():
            normalized_name = _require_observation_identifier(
                field_name,
                "DTS_SOURCE_METADATA_FIELD_TYPES_INVALID",
            )
            if not isinstance(field_type, str):
                raise ValueError("DTS_SOURCE_METADATA_FIELD_TYPES_INVALID")
            normalized_type = field_type.upper()
            if normalized_type not in _SOURCE_FIELD_TYPES:
                raise ValueError("DTS_SOURCE_METADATA_FIELD_TYPES_INVALID")
            field_types.append((normalized_name, normalized_type))
        raw_type_numbers = getattr(event, "source_field_type_numbers", {})
        if not isinstance(raw_type_numbers, Mapping):
            raise ValueError("DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID")
        type_numbers: list[tuple[str, int]] = []
        for field_name, type_number in raw_type_numbers.items():
            normalized_name = _require_observation_identifier(
                field_name,
                "DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID",
            )
            if type(type_number) is not int or type_number < 0:
                raise ValueError("DTS_SOURCE_METADATA_TYPE_NUMBERS_INVALID")
            type_numbers.append((normalized_name, type_number))
        signature = _MetadataObservationSignature(
            table_name=table_name,
            operation=operation.upper(),
            before_presence=before_presence,
            before_fields=before_fields,
            after_presence=after_presence,
            after_fields=after_fields,
            observed_field_types=tuple(sorted(field_types)),
            observed_field_type_numbers=tuple(sorted(type_numbers)),
        )
        if signature not in self._counts and len(self._counts) >= self._max_signatures:
            raise ValueError("DTS_SOURCE_METADATA_SIGNATURE_LIMIT_EXCEEDED")
        self._counts[signature] += 1

    def to_payload(self) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for signature in sorted(self._counts):
            observations.append(
                {
                    "table_name": signature.table_name,
                    "operation": signature.operation,
                    "before_image": {
                        "presence": signature.before_presence,
                        "field_count": len(signature.before_fields),
                        "field_names": list(signature.before_fields),
                    },
                    "after_image": {
                        "presence": signature.after_presence,
                        "field_count": len(signature.after_fields),
                        "field_names": list(signature.after_fields),
                    },
                    "observed_field_types": {
                        field_name: field_type
                        for field_name, field_type in signature.observed_field_types
                    },
                    "observed_field_type_numbers": {
                        field_name: type_number
                        for field_name, type_number in (
                            signature.observed_field_type_numbers
                        )
                    },
                    "sample_count": self._counts[signature],
                }
            )
        return {"schema_version": 1, "observations": observations}

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def evidence_sha256(self) -> str:
        return hashlib.sha256(
            self.to_canonical_json().encode("utf-8")
        ).hexdigest()


def _image_metadata(image: Any) -> tuple[str, tuple[str, ...]]:
    if image is None:
        return "ABSENT", ()
    if not isinstance(image, Mapping):
        raise ValueError("DTS_SOURCE_METADATA_IMAGE_INVALID")
    fields = tuple(
        sorted(
            _require_observation_identifier(
                field_name,
                "DTS_SOURCE_METADATA_IMAGE_FIELD_INVALID",
            )
            for field_name in image.keys()
        )
    )
    return ("EMPTY" if not fields else "FIELDS"), fields


def _require_observation_identifier(value: Any, error: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(error)
    return value


__all__ = [
    "DEFAULT_DTS_SOURCE_PROFILE_MANIFEST_PATH",
    "DtsSourceMetadataAggregator",
    "DtsSourceProfile",
    "DtsSourceProfileManifestError",
    "DtsSourceProfileRegistry",
    "load_dts_source_profile_registry",
]
