"""Test-only v2 source-schema profiles.

Production deliberately ships without an attested physical DTS schema
profile.  Pure algorithm and PostgreSQL shadow tests install these synthetic
appoint profiles explicitly so they can exercise the v2 pipeline without
turning a target whitelist into production source evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pytest import MonkeyPatch

from app import dts_source_contract_v2 as source_contract


SYNTHETIC_APPOINT_SOURCE_FIELDS = frozenset(
    source_contract.V2_SOURCE_FIELD_WHITELIST["appoint"]
    - {"student_token"}
)
SYNTHETIC_APPOINT_PRIMARY_KEY_TYPE = "NUMERIC"
SYNTHETIC_APPOINT_IMAGE_MODES = {
    "INSERT": "FULL",
    "UPDATE": "SPARSE",
    "DELETE": "FULL",
}
SYNTHETIC_APPOINT_PERSISTED_FIELDS_BY_TABLE = {
    "dom_appoint": frozenset(
        source_contract.V2_SOURCE_FIELD_WHITELIST["appoint"]
        - source_contract.DOMESTIC_STUDENT_ID_FIELDS
    ),
    "ovs_appoint": frozenset(
        source_contract.V2_SOURCE_FIELD_WHITELIST["appoint"]
        - {"student_token"}
    ),
}
SYNTHETIC_APPOINT_DERIVED_FIELDS_BY_TABLE = {
    "dom_appoint": frozenset({"student_token"}),
    "ovs_appoint": frozenset(),
}
_SYNTHETIC_EVIDENCE_SHA256 = hashlib.sha256(
    b"test-only synthetic appoint profile"
).hexdigest()


def _synthetic_profile_id(table: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "profile_version": 2,
                "table": table,
                "region": table.split("_", 1)[0],
                "primary_key_type": SYNTHETIC_APPOINT_PRIMARY_KEY_TYPE,
                "selected_raw_fields": sorted(
                    SYNTHETIC_APPOINT_SOURCE_FIELDS
                ),
                "selected_field_set_policy": "EXACT",
                "persisted_protected_fields": sorted(
                    SYNTHETIC_APPOINT_PERSISTED_FIELDS_BY_TABLE[table]
                ),
                "protected_derived_fields": sorted(
                    SYNTHETIC_APPOINT_DERIVED_FIELDS_BY_TABLE[table]
                ),
                "image_modes": SYNTHETIC_APPOINT_IMAGE_MODES,
                "field_type_evidence": {},
                "raw_field_type_numbers": {},
                "evidence": {
                    "provenance": "test-only synthetic profile",
                    "sha256": _SYNTHETIC_EVIDENCE_SHA256,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"dts-source-schema:v2:{digest}"


SYNTHETIC_APPOINT_PROFILE_IDS = {
    table: _synthetic_profile_id(table)
    for table in ("dom_appoint", "ovs_appoint")
}


@dataclass(frozen=True)
class SyntheticAppointProfiles:
    fields_by_table: dict[str, frozenset[str]]
    persisted_fields_by_table: dict[str, frozenset[str]]
    primary_key_types_by_table: dict[str, str]
    profile_ids_by_table: dict[str, str]


def install_synthetic_appoint_profiles(
    monkeypatch: MonkeyPatch,
) -> SyntheticAppointProfiles:
    fields_by_table = {
        table: SYNTHETIC_APPOINT_SOURCE_FIELDS
        for table in SYNTHETIC_APPOINT_PROFILE_IDS
    }
    primary_key_types_by_table = {
        table: SYNTHETIC_APPOINT_PRIMARY_KEY_TYPE
        for table in SYNTHETIC_APPOINT_PROFILE_IDS
    }
    persisted_fields_by_table = SYNTHETIC_APPOINT_PERSISTED_FIELDS_BY_TABLE
    for table, fields in fields_by_table.items():
        monkeypatch.setitem(
            source_contract.V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE,
            table,
            fields,
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
            table,
            primary_key_types_by_table[table],
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
            table,
            SYNTHETIC_APPOINT_PROFILE_IDS[table],
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_SELECTED_FIELD_SET_POLICY_BY_TABLE,
            table,
            "EXACT",
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_IMAGE_MODES_BY_TABLE,
            table,
            SYNTHETIC_APPOINT_IMAGE_MODES,
        )
        monkeypatch.setitem(
            source_contract.V2_PERSISTED_PROTECTED_FIELDS_BY_TABLE,
            table,
            persisted_fields_by_table[table],
        )
        monkeypatch.setitem(
            source_contract.V2_PROTECTED_DERIVED_FIELDS_BY_TABLE,
            table,
            SYNTHETIC_APPOINT_DERIVED_FIELDS_BY_TABLE[table],
        )
    return SyntheticAppointProfiles(
        fields_by_table=fields_by_table,
        persisted_fields_by_table=persisted_fields_by_table,
        primary_key_types_by_table=primary_key_types_by_table,
        profile_ids_by_table=dict(SYNTHETIC_APPOINT_PROFILE_IDS),
    )
