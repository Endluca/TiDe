"""LABEL and COMPLAINT_CATEGORY dirty-key projectors for DTS v2.

Reference rows are deliberately projected as their own aggregates.  Course
facts keep the source-time label/category snapshots, while these aggregates
carry the current reference definition and the exact normalized facts that
must be reconsidered when that definition changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from .dts_v2_course_domain_projector import _read_claim_trigger_evidence
from .dts_v2_dirty_queue_store import DirtyClaimV2
from .dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from .dts_v2_complaint_category_fanout import (
    PostgresDtsV2ComplaintCategoryFanout,
)
from .dts_v2_source_repository import (
    DtsV2CurrentSourceRow,
    DtsV2SourceRepository,
)


_REGIONS = frozenset({"dom", "ovs"})


class DtsV2ReferenceDomainProjectorError(RuntimeError):
    """A reference dirty key cannot be projected without guessing."""


class DtsV2LabelDomainProjector:
    """Publish one current grading-label definition and its course usages."""

    def __init__(
        self,
        *,
        cutover_coverage_identity: Mapping[str, Any],
        source_repository: DtsV2SourceRepository | None = None,
        revision_store: DtsV2DomainRevisionStore | None = None,
    ) -> None:
        self.coverage_identity = _coverage_identity(cutover_coverage_identity)
        self.sources = source_repository or DtsV2SourceRepository()
        self.revisions = revision_store or DtsV2DomainRevisionStore()

    def process_claim(
        self,
        connection: Any,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        if claim.key.key_type != "LABEL" or claim.key.key_part_2 != "":
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_LABEL_DIRTY_KEY_REQUIRED"
            )
        region = claim.key.source_region
        label_id = claim.key.key_part_1
        if region not in _REGIONS or not label_id:
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_LABEL_IDENTITY_INVALID"
            )
        source = _one_reference_source(
            self.sources,
            connection,
            source_region=region,
            source_table=f"{region}_grading_label",
            source_key=label_id,
            conflict_code="DTS_V2_LABEL_SOURCE_CURRENT_CONFLICT",
        )
        definition_evidence = _reference_evidence(
            connection,
            source_region=region,
            source_table=f"{region}_grading_label",
            source=source,
        )
        state = {
            "definition": (
                None
                if source is None
                else _reference_definition(
                    source,
                    allowed_fields=(
                        "label_name",
                        "label_name_en",
                        "type",
                        "version",
                        "status",
                    ),
                )
            ),
            "definition_evidence_status": definition_evidence,
            "course_labels": _read_label_usages(
                connection,
                source_region=region,
                label_id=label_id,
            ),
        }
        evidence = _read_claim_trigger_evidence(
            connection,
            claim,
            base_coverage_identity=self.coverage_identity,
        )
        result = self.revisions.publish_change(
            connection,
            aggregate_type="LABEL",
            aggregate_key={"source_region": region, "label_id": label_id},
            aggregate_state=_json_safe(state),
            changed_fields=("definition", "course_labels"),
            source_row_revision=evidence.source_row_revision,
            source_position=evidence.source_position,
            rule_version="dts-label-domain-v1",
            cutover_coverage_identity=evidence.coverage_identity,
        )
        return {"aggregate_events": int(result.status == "CHANGED")}


class DtsV2ComplaintCategoryDomainProjector:
    """Publish one DOM complaint category and every linked complaint fact."""

    def __init__(
        self,
        *,
        cutover_coverage_identity: Mapping[str, Any],
        source_repository: DtsV2SourceRepository | None = None,
        revision_store: DtsV2DomainRevisionStore | None = None,
        course_fanout: PostgresDtsV2ComplaintCategoryFanout | None = None,
    ) -> None:
        self.coverage_identity = _coverage_identity(cutover_coverage_identity)
        self.sources = source_repository or DtsV2SourceRepository()
        self.revisions = revision_store or DtsV2DomainRevisionStore()
        self.course_fanout = (
            course_fanout or PostgresDtsV2ComplaintCategoryFanout()
        )

    def process_claim(
        self,
        connection: Any,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        if (
            claim.key.key_type != "COMPLAINT_CATEGORY"
            or claim.key.source_region != "dom"
            or claim.key.key_part_2 != ""
        ):
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_COMPLAINT_CATEGORY_DIRTY_KEY_REQUIRED"
            )
        category_id = claim.key.key_part_1
        if not category_id:
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_COMPLAINT_CATEGORY_IDENTITY_INVALID"
            )
        source = _one_reference_source(
            self.sources,
            connection,
            source_region="dom",
            source_table="dom_complaint_cate",
            source_key=category_id,
            conflict_code="DTS_V2_COMPLAINT_CATEGORY_SOURCE_CURRENT_CONFLICT",
        )
        definition_evidence = _reference_evidence(
            connection,
            source_region="dom",
            source_table="dom_complaint_cate",
            source=source,
        )
        state = {
            "definition": (
                None
                if source is None
                else _reference_definition(
                    source,
                    allowed_fields=(
                        "cate_parent",
                        "cate_level",
                        "cate_cn_name",
                        "cate_en_name",
                        "status",
                    ),
                )
            ),
            "definition_evidence_status": definition_evidence,
            "linked_complaints": _read_category_usages(
                connection,
                category_id=category_id,
            ),
        }
        evidence = _read_claim_trigger_evidence(
            connection,
            claim,
            base_coverage_identity=self.coverage_identity,
        )
        result = self.revisions.publish_change(
            connection,
            aggregate_type="COMPLAINT_CATEGORY",
            aggregate_key={"source_region": "dom", "category_id": category_id},
            aggregate_state=_json_safe(state),
            changed_fields=("definition", "linked_complaints"),
            source_row_revision=evidence.source_row_revision,
            source_position=evidence.source_position,
            rule_version="dts-complaint-category-domain-v1",
            cutover_coverage_identity=evidence.coverage_identity,
        )
        if result.status == "CHANGED":
            if result.event is None:
                raise DtsV2ReferenceDomainProjectorError(
                    "DTS_V2_COMPLAINT_CATEGORY_EVENT_REQUIRED"
                )
            self.course_fanout.enqueue_linked_courses(
                connection,
                category_id=category_id,
                aggregate_revision=result.aggregate_revision,
                triggering_event_id=result.event.event_id,
            )
        return {"aggregate_events": int(result.status == "CHANGED")}


def _coverage_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value or "trigger" in value:
        raise DtsV2ReferenceDomainProjectorError(
            "DTS_V2_REFERENCE_COVERAGE_IDENTITY_REQUIRED"
        )
    return dict(value)


def _one_reference_source(
    repository: DtsV2SourceRepository,
    connection: Any,
    *,
    source_region: str,
    source_table: str,
    source_key: str,
    conflict_code: str,
) -> DtsV2CurrentSourceRow | None:
    rows = repository.read_by_source_keys(
        connection,
        source_region=source_region,
        source_table=source_table,
        source_keys=(source_key,),
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise DtsV2ReferenceDomainProjectorError(conflict_code)
    row = rows[0]
    if (
        row.source_region != source_region
        or row.source_table != source_table
        or row.source_key != source_key
    ):
        raise DtsV2ReferenceDomainProjectorError(
            f"{conflict_code}_IDENTITY_MISMATCH"
        )
    return row


def _reference_evidence(
    connection: Any,
    *,
    source_region: str,
    source_table: str,
    source: DtsV2CurrentSourceRow | None,
) -> str:
    if source is not None:
        return "CONFIRMED_TOMBSTONE" if source.is_deleted else "CONFIRMED"
    rows = connection.execute(
        text(
            """
            SELECT state
            FROM public.dts_source_scope_states
            WHERE source_region=:source_region
              AND source_table=:source_table
              AND scope_kind='CURRENT'
              AND scope_level='GLOBAL'
              AND scope_key='*'
            FOR SHARE
            """
        ),
        {"source_region": source_region, "source_table": source_table},
    ).mappings()
    row = next(iter(rows), None)
    if row is not None and row.get("state") == "COMPLETE":
        return "CONFIRMED_EMPTY"
    return "SOURCE_MISSING"


def _reference_definition(
    source: DtsV2CurrentSourceRow,
    *,
    allowed_fields: tuple[str, ...],
) -> Mapping[str, Any]:
    values: dict[str, Any] = {}
    for field in allowed_fields:
        value = source.source_row.get(field)
        if value is not None and field not in source.source_field_types:
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_REFERENCE_FIELD_TYPE_EVIDENCE_MISSING"
            )
        values[field] = value
    return {
        "source_id": source.source_key,
        "source_id_type": source.source_key_type,
        "source_deleted": source.is_deleted,
        "source_row_revision": source.source_row_revision,
        "source_payload_hash": source.source_payload_hash,
        "values": values,
    }


def _read_label_usages(
    connection: Any,
    *,
    source_region: str,
    label_id: str,
) -> list[Mapping[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT source_appoint_id,source_log_id,source_log_id_type,
                   label_id_type,label_name_snapshot,create_time,dt,
                   evidence_status,is_deleted,source_row_revision
            FROM public.source_course_labels
            WHERE source_region=:source_region AND label_id=:label_id
            ORDER BY convert_to(source_appoint_id,'UTF8'),
                     convert_to(source_log_id_type,'UTF8'),
                     source_log_id_numeric NULLS LAST,
                     convert_to(COALESCE(source_log_id_text,''),'UTF8')
            """
        ),
        {"source_region": source_region, "label_id": label_id},
    ).mappings()
    return [dict(row) for row in rows]


def _read_category_usages(
    connection: Any,
    *,
    category_id: str,
) -> list[Mapping[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT source_region,source_appoint_id,source_complaint_id,
                   source_complaint_id_type,complaint_type,
                   complaint_type_type,complaint_type_child,
                   complaint_type_child_type,complaint_type_grandson,
                   complaint_type_grandson_type,is_valid,complaint_rule_id,
                   source_sha256,severity_rank,category_l1_snapshot,
                   category_l2_snapshot,category_l3_snapshot,evidence_status,
                   is_deleted,source_row_revision
            FROM public.source_course_complaints
            WHERE complaint_type=:category_id
               OR complaint_type_child=:category_id
               OR complaint_type_grandson=:category_id
            ORDER BY convert_to(source_region,'UTF8'),
                     convert_to(source_appoint_id,'UTF8'),
                     convert_to(source_complaint_id_type,'UTF8'),
                     source_complaint_id_numeric NULLS LAST,
                     convert_to(COALESCE(source_complaint_id_text,''),'UTF8')
            """
        ),
        {"category_id": category_id},
    ).mappings()
    return [dict(row) for row in rows]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered in {"", "-0"} else rendered
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise DtsV2ReferenceDomainProjectorError(
                "DTS_V2_REFERENCE_NAIVE_DATETIME_FORBIDDEN"
            )
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


__all__ = [
    "DtsV2ComplaintCategoryDomainProjector",
    "DtsV2LabelDomainProjector",
    "DtsV2ReferenceDomainProjectorError",
]
