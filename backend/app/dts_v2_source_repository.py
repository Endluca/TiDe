"""Typed, privacy-safe reads from the persisted DTS v2 current source mirror."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from sqlalchemy import text

from .dts_child_selectors_v2 import SelectorSourceIdentity
from .dts_source_consumer import DOMESTIC_STUDENT_ID_FIELDS


_REGIONS = frozenset({"dom", "ovs"})
_SOURCE_TYPES = frozenset({"NUMERIC", "TEXT", "TEMPORAL", "BOOLEAN"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DtsV2SourceRepositoryError(ValueError):
    """A persisted source row cannot be consumed without guessing."""


@dataclass(frozen=True)
class DtsV2CurrentSourceRow:
    source_region: str
    source_table: str
    source_key: str
    source_key_type: str
    source_row_revision: int
    source_row: Mapping[str, Any]
    source_field_types: Mapping[str, str]
    source_position: Mapping[str, Any]
    source_payload_hash: str
    is_deleted: bool

    @classmethod
    def from_database_row(
        cls,
        value: Mapping[str, Any],
    ) -> "DtsV2CurrentSourceRow":
        if not isinstance(value, Mapping):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATABASE_ROW_INVALID"
            )
        if value.get("provenance_state") != "V2_CONFIRMED":
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_PROVENANCE_NOT_CONFIRMED"
            )
        region = value.get("source_region")
        table = value.get("source_table")
        key = value.get("source_key")
        key_type = value.get("source_key_type")
        revision = value.get("source_row_revision")
        source_row = value.get("source_row")
        field_types = value.get("source_field_types")
        position = value.get("source_position_v2")
        payload_hash = value.get("source_payload_hash")
        is_deleted = value.get("is_deleted")
        if region not in _REGIONS:
            raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_REGION_INVALID")
        if (
            not isinstance(table, str)
            or not table.startswith(f"{region}_")
            or not isinstance(key, str)
            or not key
            or key_type not in {"NUMERIC", "TEXT"}
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_IDENTITY_INVALID"
            )
        canonical_key = _canonical_typed_id(key, key_type)
        if canonical_key != key:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_IDENTITY_NOT_CANONICAL"
            )
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_REVISION_INVALID"
            )
        if not isinstance(source_row, Mapping) or not isinstance(
            field_types, Mapping
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_TYPED_ROW_INVALID"
            )
        normalized_types: dict[str, str] = {}
        for raw_name, raw_type in field_types.items():
            if (
                not isinstance(raw_name, str)
                or raw_type not in _SOURCE_TYPES
            ):
                raise DtsV2SourceRepositoryError(
                    "DTS_V2_SOURCE_FIELD_TYPE_INVALID"
                )
            normalized_types[raw_name] = raw_type
        if any(not isinstance(field_name, str) for field_name in source_row):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_TYPED_ROW_INVALID"
            )
        missing_types = {
            field_name
            for field_name, field_value in source_row.items()
            if field_value is not None and field_name not in normalized_types
        }
        if missing_types or normalized_types.get("id") != key_type:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_FIELD_TYPE_EVIDENCE_INCOMPLETE"
            )
        if region == "dom" and any(
            field_name in source_row
            for field_name in DOMESTIC_STUDENT_ID_FIELDS
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DOM_RAW_STUDENT_ID_FORBIDDEN"
            )
        if not isinstance(position, Mapping):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_POSITION_INVALID"
            )
        if not isinstance(payload_hash, str) or _SHA256.fullmatch(
            payload_hash
        ) is None:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_PAYLOAD_HASH_INVALID"
            )
        if not isinstance(is_deleted, bool):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DELETION_FLAG_INVALID"
            )
        return cls(
            source_region=region,
            source_table=table,
            source_key=key,
            source_key_type=key_type,
            source_row_revision=revision,
            source_row=MappingProxyType(dict(source_row)),
            source_field_types=MappingProxyType(normalized_types),
            source_position=MappingProxyType(dict(position)),
            source_payload_hash=payload_hash,
            is_deleted=is_deleted,
        )

    @property
    def identity(self) -> SelectorSourceIdentity:
        return SelectorSourceIdentity(
            source_id=self.source_key,
            source_id_type=self.source_key_type,
            source_row_revision=self.source_row_revision,
        )

    def value(self, field_name: str) -> Any:
        return self.source_row.get(field_name)

    def typed_id(self, field_name: str) -> tuple[str, str] | None:
        value = self.source_row.get(field_name)
        if value is None:
            return None
        source_type = self._required_type(field_name)
        if source_type not in {"NUMERIC", "TEXT"}:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_TYPED_ID_EVIDENCE_INVALID"
            )
        return _canonical_typed_id(value, source_type), source_type

    def text_value(self, field_name: str) -> str | None:
        value = self.source_row.get(field_name)
        if value is None:
            return None
        if self._required_type(field_name) != "TEXT" or not isinstance(
            value, str
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_TEXT_VALUE_INVALID"
            )
        return value

    def datetime_value(self, field_name: str) -> datetime | None:
        value = self.source_row.get(field_name)
        if value is None:
            return None
        if self._required_type(field_name) not in {"TEXT", "TEMPORAL"}:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATETIME_EVIDENCE_INVALID"
            )
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(
                    value[:-1] + "+00:00" if value.endswith("Z") else value
                )
            except ValueError as exc:
                raise DtsV2SourceRepositoryError(
                    "DTS_V2_SOURCE_DATETIME_VALUE_INVALID"
                ) from exc
        else:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATETIME_VALUE_INVALID"
            )
        if parsed.utcoffset() is None:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATETIME_TIMEZONE_MISSING"
            )
        return parsed

    def date_value(self, field_name: str) -> date | None:
        value = self.source_row.get(field_name)
        if value is None:
            return None
        if self._required_type(field_name) not in {"TEXT", "TEMPORAL"}:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATE_EVIDENCE_INVALID"
            )
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise DtsV2SourceRepositoryError(
                    "DTS_V2_SOURCE_DATETIME_TIMEZONE_MISSING"
                )
            return value.date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATE_VALUE_INVALID"
            )
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            return self.datetime_value(field_name).date()  # type: ignore[union-attr]
        except ValueError as exc:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DATE_VALUE_INVALID"
            ) from exc

    def version_vector_entry(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "source_key": self.source_key,
            "source_key_type": self.source_key_type,
            "source_row_revision": self.source_row_revision,
            "source_payload_hash": self.source_payload_hash,
            "is_deleted": self.is_deleted,
        }

    def _required_type(self, field_name: str) -> str:
        source_type = self.source_field_types.get(field_name)
        if source_type not in _SOURCE_TYPES:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_FIELD_TYPE_EVIDENCE_MISSING"
            )
        return source_type


class DtsV2SourceRepository:
    """Read protected current rows from the caller's stable transaction snapshot.

    ``dts_source_rows`` is owned by the ingest runtime and intentionally
    SELECT-only for the application role.  The Domain worker supplies a
    repeatable-read snapshot; adding a PostgreSQL row lock here would
    incorrectly require UPDATE privilege on ingest-owned evidence.
    """

    def read_for_dependency(
        self,
        connection: Any,
        *,
        source_region: str,
        source_tables: Sequence[str],
        dependency_kind: str,
        dependency_value: str,
    ) -> tuple[DtsV2CurrentSourceRow, ...]:
        if source_region not in _REGIONS:
            raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_REGION_INVALID")
        tables = _source_tables(source_region, source_tables)
        if dependency_kind not in {
            "course_ids",
            "teacher_ids",
            "student_subjects",
            "label_ids",
            "category_ids",
        }:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DEPENDENCY_KIND_INVALID"
            )
        if not isinstance(dependency_value, str) or not dependency_value:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_DEPENDENCY_VALUE_INVALID"
            )
        dependency = json.dumps(
            {dependency_kind: [dependency_value]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        rows = connection.execute(
            text(
                """
                SELECT source_region,source_table,source_key,source_key_type,
                       source_row_revision,source_row,source_field_types,
                       source_position_v2,source_payload_hash,is_deleted,
                       provenance_state
                FROM public.dts_source_rows
                WHERE source_region=:source_region
                  AND source_table=ANY(CAST(:source_tables AS text[]))
                  AND provenance_state='V2_CONFIRMED'
                  AND dependency_keys @> CAST(:dependency AS jsonb)
                ORDER BY convert_to(source_table,'UTF8'),
                         convert_to(source_key_type,'UTF8'),
                         source_key_numeric NULLS LAST,
                         convert_to(COALESCE(source_key_text,''),'UTF8'),
                         source_row_revision
                """
            ),
            {
                "source_region": source_region,
                "source_tables": list(tables),
                "dependency": dependency,
            },
        ).mappings()
        return tuple(
            DtsV2CurrentSourceRow.from_database_row(row) for row in rows
        )

    def read_by_source_keys(
        self,
        connection: Any,
        *,
        source_region: str,
        source_table: str,
        source_keys: Sequence[str],
    ) -> tuple[DtsV2CurrentSourceRow, ...]:
        tables = _source_tables(source_region, (source_table,))
        if isinstance(source_keys, (str, bytes, bytearray)) or any(
            not isinstance(key, str) or not key for key in source_keys
        ):
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_IDENTITY_INVALID"
            )
        normalized_keys = tuple(sorted(set(source_keys)))
        if not normalized_keys:
            return ()
        rows = connection.execute(
            text(
                """
                SELECT source_region,source_table,source_key,source_key_type,
                       source_row_revision,source_row,source_field_types,
                       source_position_v2,source_payload_hash,is_deleted,
                       provenance_state
                FROM public.dts_source_rows
                WHERE source_region=:source_region
                  AND source_table=:source_table
                  AND source_key=ANY(CAST(:source_keys AS text[]))
                  AND provenance_state='V2_CONFIRMED'
                ORDER BY source_key_numeric NULLS LAST,
                         convert_to(COALESCE(source_key_text,''),'UTF8'),
                         source_row_revision
                """
            ),
            {
                "source_region": source_region,
                "source_table": tables[0],
                "source_keys": list(normalized_keys),
            },
        ).mappings()
        return tuple(
            DtsV2CurrentSourceRow.from_database_row(row) for row in rows
        )


def _source_tables(
    source_region: str,
    values: Sequence[str],
) -> tuple[str, ...]:
    if source_region not in _REGIONS:
        raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_REGION_INVALID")
    if isinstance(values, (str, bytes, bytearray)):
        raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_TABLES_INVALID")
    if not values or any(
        not isinstance(table, str)
        or not table.startswith(f"{source_region}_")
        for table in values
    ):
        raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_TABLES_INVALID")
    result = tuple(sorted(set(values)))
    return result


def _canonical_typed_id(value: Any, source_type: str) -> str:
    if source_type == "TEXT":
        if not isinstance(value, str) or not value:
            raise DtsV2SourceRepositoryError(
                "DTS_V2_SOURCE_TYPED_ID_INVALID"
            )
        return value
    if source_type != "NUMERIC" or isinstance(value, bool):
        raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_TYPED_ID_INVALID")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsV2SourceRepositoryError(
            "DTS_V2_SOURCE_TYPED_ID_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2SourceRepositoryError("DTS_V2_SOURCE_TYPED_ID_INVALID")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered
