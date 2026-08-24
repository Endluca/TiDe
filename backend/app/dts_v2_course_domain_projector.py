"""COURSE dirty-key projection into the typed DTS v2 domain facts.

The caller owns one PostgreSQL transaction.  Appoint history is projected
first, then every course child is rebuilt from the protected current set.
Only the domain revision command may turn a semantic state change into an
Outbox event; source-row delivery order is never used as business ordering.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from typing import Any

from sqlalchemy import text

from .dts_absence_rules_v2 import (
    AbsenceReasonFact,
    select_current_absence_reasons,
)
from .dts_business_rules_v2 import complaint_is_valid
from .dts_child_selectors_v2 import (
    CloseCameraFact,
    ComplaintFact,
    DomGradingFact,
    select_current_close_camera_state,
    select_current_complaints,
    select_current_dom_grading,
)
from .dts_penalty_rules_v2 import (
    PenaltyParticipation,
    PenaltyRecord,
    aggregate_penalty_records,
    resolve_penalty_participation,
)
from .dts_teacher_aggregate_v2 import teacher_expected_source_region_v2
from .dts_v2_course_projector import (
    DtsV2CourseProjector,
    DtsV2CourseProjectorError,
)
from .dts_v2_dirty_queue_store import DirtyClaimV2, DirtyDependencyV2
from .dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from .dts_v2_domain_worker import DtsV2DomainDependencyPending
from .dts_v2_source_repository import (
    DtsV2CurrentSourceRow,
    DtsV2SourceRepository,
    DtsV2SourceRepositoryError,
)
from .dts_v2_teacher_domain_projector import (
    publish_regional_teacher_aggregate_v2,
)


_REGIONS = frozenset({"dom", "ovs"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RULE_ID = re.compile(
    r"^complaint-rule:(?P<sha>[0-9a-f]{64}):(?P<row>[1-9][0-9]*)$"
)
_COURSE_CHILD_SUFFIXES = (
    "user_teacher_grading",
    "grading_label_log",
    "complaint",
    "qa_task_close_camera_record",
)
_DOM_COURSE_CHILD_SUFFIXES = (
    "teacher_absent_reason",
    "teacher_penalty",
)
_DOMAIN_RULE_VERSION = "dts-course-domain-v1"


class DtsV2CourseDomainProjectorError(RuntimeError):
    """A COURSE dirty input cannot be projected without guessing."""


@dataclass(frozen=True)
class _Participation:
    participation_seq: int
    teacher_id: str
    teacher_id_type: str
    participation_status: str | None
    participation_role: str
    is_current: bool
    assigned_at: datetime | None
    ended_at: datetime | None
    absence_reason_detail: str | None
    no_notice: bool | None
    row_version: int
    absence_source_id: str | None = None
    absence_source_id_type: str | None = None
    absence_source_row_revision: int | None = None
    absence_selected_reason_type: str | None = None
    teacher_expected_source_region: str | None = None
    teacher_region_evidence_status: str = "SOURCE_MISSING"
    teacher_profile_source_row_revision: int | None = None
    teacher_profile_source_payload_hash: str | None = None


@dataclass(frozen=True)
class CourseTeacherRegionReconcileV2:
    course_changed: bool
    changed_participation_seqs: tuple[int, ...]
    affected_teacher_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ComplaintRule:
    complaint_rule_id: str
    source_sha256: str
    category_l3_normalized: str
    severity_rank: int


@dataclass(frozen=True)
class _ComplaintPlan:
    source: DtsV2CurrentSourceRow
    source_appoint_id: str
    values: Mapping[str, Any]
    selector_fact: ComplaintFact


@dataclass(frozen=True)
class _TriggerEvidence:
    source_row_revision: int | None
    source_position: Mapping[str, Any] | None
    coverage_identity: Mapping[str, Any]


class DtsV2CourseDomainProjector:
    """Process one claimed ``COURSE`` dirty key in its caller transaction."""

    def __init__(
        self,
        *,
        cutover_coverage_identity: Mapping[str, Any],
        course_projector: DtsV2CourseProjector | None = None,
        source_repository: DtsV2SourceRepository | None = None,
        revision_store: DtsV2DomainRevisionStore | None = None,
    ) -> None:
        if not isinstance(cutover_coverage_identity, Mapping) or not (
            cutover_coverage_identity
        ):
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_COVERAGE_IDENTITY_REQUIRED"
            )
        if "trigger" in cutover_coverage_identity:
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_TRIGGER_COVERAGE_RESERVED"
            )
        self.coverage_identity = dict(cutover_coverage_identity)
        self.course_projector = course_projector or DtsV2CourseProjector(
            enabled=True
        )
        self.sources = source_repository or DtsV2SourceRepository()
        self.revisions = revision_store or DtsV2DomainRevisionStore()

    def process_claim(
        self,
        connection: Any,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        """Rebuild one course and publish only semantic aggregate changes."""

        if claim.key.key_type != "COURSE" or claim.key.key_part_2 != "":
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_DIRTY_KEY_REQUIRED"
            )
        source_region = claim.key.source_region
        source_appoint_id = claim.key.key_part_1
        if source_region not in _REGIONS or not source_appoint_id:
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_IDENTITY_INVALID"
            )

        child_rows = self._read_course_children(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
        )
        try:
            course_projection = self.course_projector.project_until_current(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
            )
        except DtsV2CourseProjectorError as exc:
            if str(exc) not in {
                "DTS_V2_COURSE_SOURCE_CURRENT_REQUIRED",
                "DTS_V2_COURSE_SOURCE_HISTORY_REQUIRED",
            }:
                raise
            raise DtsV2DomainDependencyPending(
                [
                    _missing_course_dependency(
                        claim,
                        child_rows,
                        source_appoint_id=source_appoint_id,
                    )
                ]
            ) from exc

        course, participations = _read_course_bundle(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
        )
        if course is None:
            raise DtsV2DomainDependencyPending(
                [
                    _missing_course_dependency(
                        claim,
                        child_rows,
                        source_appoint_id=source_appoint_id,
                    )
                ]
            )

        child_rows = self._include_existing_moved_rows(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            rows=child_rows,
        )
        rows_by_table = _rows_by_table(child_rows)
        scope_states = _read_current_global_scope_states(
            connection,
            source_region=source_region,
            source_tables=_course_child_tables(source_region),
        )
        evidence = _read_claim_trigger_evidence(
            connection,
            claim,
            base_coverage_identity=self.coverage_identity,
        )

        counts: dict[str, int] = {
            "course_projection_revisions": (
                course_projection.applied_revision_count
            ),
            "label_fact_changes": 0,
            "complaint_fact_changes": 0,
            "course_fact_changes": 0,
            "participation_changes": 0,
            "participation_fact_changes": 0,
            "aggregate_events": 0,
            "teacher_aggregate_events": 0,
            "teacher_region_changes": 0,
        }
        affected_course_ids = {source_appoint_id}
        affected_label_ids: set[str] = set()
        changed_participations: set[int] = set()

        region_result = reconcile_course_teacher_region_evidence_v2(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            source_repository=self.sources,
        )
        counts["teacher_region_changes"] += int(
            region_result.course_changed
        ) + len(region_result.changed_participation_seqs)
        changed_participations.update(
            region_result.changed_participation_seqs
        )
        if region_result.course_changed or region_result.changed_participation_seqs:
            course, participations = _read_course_bundle(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
            )
            assert course is not None

        label_rows = rows_by_table.get(
            f"{source_region}_grading_label_log", ()
        )
        for row in label_rows:
            target_course = _required_course_id(row)
            self._require_target_course(
                connection,
                claim=claim,
                source=row,
                source_appoint_id=target_course,
            )
            label_id, label_id_type = _required_typed_id(row, "label_id")
            existing = _read_existing_label(
                connection,
                source_region=source_region,
                source_log_id=row.source_key,
            )
            changed = _upsert_label(
                connection,
                source=row,
                source_appoint_id=target_course,
                label_id=label_id,
                label_id_type=label_id_type,
            )
            if changed:
                counts["label_fact_changes"] += 1
                affected_course_ids.add(target_course)
                affected_label_ids.add(label_id)
                if existing is not None:
                    affected_course_ids.add(str(existing["source_appoint_id"]))
                    affected_label_ids.add(str(existing["label_id"]))

        complaint_rows = rows_by_table.get(
            f"{source_region}_complaint", ()
        )
        complaint_plans = self._complaint_plans(
            connection,
            source_region=source_region,
            rows=complaint_rows,
        )
        for plan in complaint_plans:
            self._require_target_course(
                connection,
                claim=claim,
                source=plan.source,
                source_appoint_id=plan.source_appoint_id,
            )
            existing = _read_existing_complaint(
                connection,
                source_region=source_region,
                source_complaint_id=plan.source.source_key,
            )
            if _upsert_complaint(connection, plan):
                counts["complaint_fact_changes"] += 1
                affected_course_ids.add(plan.source_appoint_id)
                if existing is not None:
                    affected_course_ids.add(str(existing["source_appoint_id"]))

        absence_changed = self._project_absence(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            participations=participations,
            rows=rows_by_table.get("dom_teacher_absent_reason", ()),
            scope_complete=scope_states.get(
                "dom_teacher_absent_reason"
            )
            == "COMPLETE",
        )
        counts["participation_changes"] += len(absence_changed)
        changed_participations.update(absence_changed)

        # Absence row-version updates are now visible to the state readers.
        course, participations = _read_course_bundle(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
        )
        assert course is not None
        penalty_changed = self._project_penalties(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            participations=participations,
            rows=rows_by_table.get("dom_teacher_penalty", ()),
            scope_complete=scope_states.get("dom_teacher_penalty")
            == "COMPLETE",
        )
        counts["participation_fact_changes"] += len(penalty_changed)
        changed_participations.update(penalty_changed)

        course_fact_values = self._build_course_fact(
            source_region=source_region,
            grading_rows=rows_by_table.get(
                f"{source_region}_user_teacher_grading", ()
            ),
            complaint_plans=complaint_plans,
            camera_rows=rows_by_table.get(
                f"{source_region}_qa_task_close_camera_record", ()
            ),
            scope_states=scope_states,
        )
        if _upsert_course_fact(
            connection,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            values=course_fact_values,
        ):
            counts["course_fact_changes"] += 1

        if course_projection.applied_revision_count:
            changed_participations.update(
                row.participation_seq for row in participations
            )

            # The conflict aggregate is the durable plan.  In V2_PRIMARY the
            # protected command must create/reuse the Case and fill the course
            # pointer in this same transaction; shadow/compat mode deliberately
            # leaves the pointer NULL.  Publish it before COURSE so the latter
            # snapshot can never advertise a pointer that has no Case.
            conflict_state = _completion_conflict_state(
                course,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
            )
            conflict_result = self._publish_result(
                connection,
                aggregate_type="COMPLETION_CONFLICT",
                aggregate_key={
                    "source_region": source_region,
                    "source_appoint_id": source_appoint_id,
                },
                aggregate_state=conflict_state,
                changed_fields=("completion_conflict",),
                evidence=evidence,
            )
            counts["aggregate_events"] += int(
                conflict_result.status == "CHANGED"
            )
            conflict_event_id = (
                conflict_result.event.event_id
                if conflict_result.event is not None
                else (
                    "source_wide.changed.v2:COMPLETION_CONFLICT:"
                    f"{conflict_result.identity.aggregate_id}:"
                    f"{conflict_result.aggregate_revision}"
                )
            )
            _reconcile_completion_conflict_case(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                expected_aggregate_revision=(
                    conflict_result.aggregate_revision
                ),
                triggering_event_id=conflict_event_id,
                expected_planned_case_id=(
                    conflict_state["completion_conflict"]["case_id"]
                ),
            )
            course, participations = _read_course_bundle(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
            )
            if course is None:
                raise DtsV2CourseDomainProjectorError(
                    "DTS_V2_COMPLETION_CONFLICT_RECONCILE_COURSE_MISSING"
                )

        for affected_course_id in sorted(affected_course_ids, key=_id_sort_key):
            state = _read_course_aggregate_state(
                connection,
                source_region=source_region,
                source_appoint_id=affected_course_id,
            )
            if state is None:
                continue
            counts["aggregate_events"] += self._publish(
                connection,
                aggregate_type="COURSE",
                aggregate_key={
                    "source_region": source_region,
                    "source_appoint_id": affected_course_id,
                },
                aggregate_state=state,
                changed_fields=(
                    "course",
                    "course_fact",
                    "labels",
                    "complaints",
                    "participations",
                ),
                evidence=evidence,
            )

        for label_id in sorted(affected_label_ids, key=_id_sort_key):
            state = _read_label_aggregate_state(
                connection,
                source_region=source_region,
                label_id=label_id,
            )
            counts["aggregate_events"] += self._publish(
                connection,
                aggregate_type="LABEL",
                aggregate_key={
                    "source_region": source_region,
                    "label_id": label_id,
                },
                aggregate_state=state,
                changed_fields=("course_labels",),
                evidence=evidence,
            )

        for participation_seq in sorted(changed_participations):
            state = _read_participation_aggregate_state(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                participation_seq=participation_seq,
            )
            if state is None:
                continue
            counts["aggregate_events"] += self._publish(
                connection,
                aggregate_type="PARTICIPATION",
                aggregate_key={
                    "source_region": source_region,
                    "source_appoint_id": source_appoint_id,
                    "participation_seq": participation_seq,
                },
                aggregate_state=state,
                changed_fields=("participation", "attendance", "penalty"),
                evidence=evidence,
            )

        # A course child such as grading/label/QA may carry appoint_id but no
        # teacher id.  Only after COURSE projection can the Domain owner map
        # the change to real participation teachers without guessing.  Rebuild
        # those regional TEACHER aggregates in this same transaction so their
        # feedback/reliability facts cannot lag behind COURSE current.
        for teacher_id in sorted(
            {row.teacher_id for row in participations},
            key=_id_sort_key,
        ):
            teacher_result = publish_regional_teacher_aggregate_v2(
                connection,
                source_region=source_region,
                teacher_id=teacher_id,
                source_row_revision=evidence.source_row_revision,
                source_position=evidence.source_position,
                cutover_coverage_identity=evidence.coverage_identity,
                source_repository=self.sources,
                revision_store=self.revisions,
            )
            changed = int(teacher_result.status == "CHANGED")
            counts["teacher_aggregate_events"] += changed
            counts["aggregate_events"] += changed

        return counts

    def _read_course_children(
        self,
        connection: Any,
        *,
        source_region: str,
        source_appoint_id: str,
    ) -> tuple[DtsV2CurrentSourceRow, ...]:
        return self.sources.read_for_dependency(
            connection,
            source_region=source_region,
            source_tables=_course_child_tables(source_region),
            dependency_kind="course_ids",
            dependency_value=source_appoint_id,
        )

    def _include_existing_moved_rows(
        self,
        connection: Any,
        *,
        source_region: str,
        source_appoint_id: str,
        rows: Sequence[DtsV2CurrentSourceRow],
    ) -> tuple[DtsV2CurrentSourceRow, ...]:
        result = {(row.source_table, row.source_key): row for row in rows}
        for source_table, typed_table, id_column in (
            (
                f"{source_region}_grading_label_log",
                "source_course_labels",
                "source_log_id",
            ),
            (
                f"{source_region}_complaint",
                "source_course_complaints",
                "source_complaint_id",
            ),
        ):
            source_keys = tuple(
                str(row[id_column])
                for row in connection.execute(
                    text(
                        f"""
                        SELECT {id_column}
                        FROM public.{typed_table}
                        WHERE source_region=:source_region
                          AND source_appoint_id=:source_appoint_id
                        ORDER BY {id_column}
                        FOR UPDATE
                        """
                    ),
                    {
                        "source_region": source_region,
                        "source_appoint_id": source_appoint_id,
                    },
                ).mappings()
            )
            if not source_keys:
                continue
            current = self.sources.read_by_source_keys(
                connection,
                source_region=source_region,
                source_table=source_table,
                source_keys=source_keys,
            )
            current_keys = {row.source_key for row in current}
            missing = set(source_keys) - current_keys
            if missing:
                raise DtsV2CourseDomainProjectorError(
                    "DTS_V2_COURSE_DOMAIN_TYPED_SOURCE_CURRENT_MISSING"
                )
            result.update(
                ((row.source_table, row.source_key), row) for row in current
            )
        return tuple(
            result[key]
            for key in sorted(
                result,
                key=lambda item: (item[0].encode("utf-8"), _id_sort_key(item[1])),
            )
        )

    def _require_target_course(
        self,
        connection: Any,
        *,
        claim: DirtyClaimV2,
        source: DtsV2CurrentSourceRow,
        source_appoint_id: str,
    ) -> None:
        exists = connection.execute(
            text(
                """
                SELECT 1
                FROM public.source_courses
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                """
            ),
            {
                "source_region": claim.key.source_region,
                "source_appoint_id": source_appoint_id,
            },
        ).scalar_one_or_none()
        if exists is not None:
            return
        raise DtsV2DomainDependencyPending(
            [
                _source_dependency(
                    source,
                    dependency_key=(
                        f"{claim.key.source_region}_appoint:"
                        f"{source_appoint_id}"
                    ),
                )
            ]
        )

    def _complaint_plans(
        self,
        connection: Any,
        *,
        source_region: str,
        rows: Sequence[DtsV2CurrentSourceRow],
    ) -> tuple[_ComplaintPlan, ...]:
        category_ids: set[str] = set()
        typed_ids_by_row: dict[str, dict[str, tuple[str, str] | None]] = {}
        for row in rows:
            values = {
                name: row.typed_id(name)
                for name in (
                    "complaint_type",
                    "complaint_type_child",
                    "complaint_type_grandson",
                )
            }
            typed_ids_by_row[row.source_key] = values
            category_ids.update(
                value[0] for value in values.values() if value is not None
            )
        category_rows = (
            self.sources.read_by_source_keys(
                connection,
                source_region="dom",
                source_table="dom_complaint_cate",
                source_keys=tuple(category_ids),
            )
            if category_ids
            else ()
        )
        categories = {
            (row.source_key_type, row.source_key): row
            for row in category_rows
            if not row.is_deleted
        }
        normalized_names = {
            normalized
            for row in category_rows
            if not row.is_deleted
            if (name := _optional_text(row, "cate_cn_name")) is not None
            if (normalized := _normalize_category(name))
        }
        rules = _read_complaint_rules(connection, normalized_names)

        plans: list[_ComplaintPlan] = []
        for row in rows:
            appoint_id = _required_course_id(row)
            ids = typed_ids_by_row[row.source_key]
            names: dict[str, str | None] = {}
            missing_category = False
            for field_name, typed_id in ids.items():
                if typed_id is None:
                    names[field_name] = None
                    continue
                category = categories.get((typed_id[1], typed_id[0]))
                if category is None:
                    names[field_name] = None
                    missing_category = True
                else:
                    names[field_name] = _optional_text(
                        category, "cate_cn_name"
                    )
                    if not names[field_name]:
                        missing_category = True

            raw_business = {
                "complaint_type": row.value("complaint_type"),
                "complaint_type_grandson": row.value(
                    "complaint_type_grandson"
                ),
                "approve": row.value("approve"),
                "validity": row.value("validity"),
            }
            is_valid = complaint_is_valid(raw_business)
            normalized_l3 = (
                _normalize_category(names["complaint_type_grandson"])
                if names["complaint_type_grandson"] is not None
                else None
            )
            matching_rules = rules.get(normalized_l3 or "", ())
            matched_rule = matching_rules[0] if len(matching_rules) == 1 else None
            if row.is_deleted:
                evidence_status = "CONFIRMED"
                evidence_error = None
            elif is_valid and ids["complaint_type_grandson"] is None:
                evidence_status = "PENDING_DATA"
                evidence_error = "PENDING_DATA:COMPLAINT_CATEGORY_MISSING"
            elif missing_category:
                evidence_status = "SOURCE_MISSING"
                evidence_error = "SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND"
            elif is_valid and not matching_rules:
                evidence_status = "PENDING_DATA"
                evidence_error = "PENDING_DATA:COMPLAINT_CATEGORY_RULE_MISSING"
            elif is_valid and len(matching_rules) != 1:
                evidence_status = "PENDING_DATA"
                evidence_error = "PENDING_DATA:COMPLAINT_CATEGORY_RULE_CONFLICT"
            else:
                evidence_status = "CONFIRMED"
                evidence_error = None

            source_teacher = _first_typed_id(
                row, ("tea_id", "teacher_id", "t_id")
            )
            values: dict[str, Any] = {
                **_typed_columns(
                    "source_complaint_id",
                    (row.source_key, row.source_key_type),
                ),
                "source_appoint_id": appoint_id,
                **_typed_columns("source_teacher_id", source_teacher),
                **_typed_columns("complaint_type", ids["complaint_type"]),
                **_typed_columns(
                    "complaint_type_child", ids["complaint_type_child"]
                ),
                **_typed_columns(
                    "complaint_type_grandson",
                    ids["complaint_type_grandson"],
                ),
                "approve": _optional_string(row.value("approve")),
                "validity": _optional_integer(row.value("validity")),
                "add_time": _optional_datetime(row, "add_time"),
                "course_date": _optional_date(row, "course_date"),
                "is_valid": is_valid,
                "complaint_rule_id": (
                    matched_rule.complaint_rule_id
                    if is_valid and matched_rule is not None
                    else None
                ),
                "source_sha256": (
                    matched_rule.source_sha256
                    if is_valid and matched_rule is not None
                    else None
                ),
                "severity_rank": (
                    matched_rule.severity_rank
                    if is_valid and matched_rule is not None
                    else None
                ),
                "category_l1_snapshot": names["complaint_type"],
                "category_l2_snapshot": names["complaint_type_child"],
                "category_l3_snapshot": names["complaint_type_grandson"],
                "category_l3_normalized": normalized_l3,
                "evidence_status": evidence_status,
                "evidence_error_code": evidence_error,
                "is_deleted": row.is_deleted,
                "source_version": _source_version(row),
                "source_position": dict(row.source_position),
                "source_row_revision": row.source_row_revision,
            }
            plans.append(
                _ComplaintPlan(
                    source=row,
                    source_appoint_id=appoint_id,
                    values=values,
                    selector_fact=ComplaintFact(
                        identity=row.identity,
                        complaint_type=row.value("complaint_type"),
                        complaint_type_grandson=row.value(
                            "complaint_type_grandson"
                        ),
                        approve=row.value("approve"),
                        validity=row.value("validity"),
                        add_time=_optional_datetime(row, "add_time"),
                        course_date=_optional_date(row, "course_date"),
                        is_deleted=row.is_deleted,
                    ),
                )
            )
        return tuple(plans)

    def _project_absence(
        self,
        connection: Any,
        *,
        source_region: str,
        source_appoint_id: str,
        participations: Sequence[_Participation],
        rows: Sequence[DtsV2CurrentSourceRow],
        scope_complete: bool,
    ) -> set[int]:
        if source_region != "dom":
            return set()
        reasons = tuple(_absence_reason(row) for row in rows)
        selection = select_current_absence_reasons(reasons, participations)
        if selection.pending:
            raise DtsV2DomainDependencyPending(
                [
                    _source_dependency(
                        _source_for_reason(rows, item.reason),
                        dependency_key=(
                            f"course-participations:{source_appoint_id}"
                        ),
                    )
                    for item in selection.pending
                ]
            )
        selected = {item.participation_seq: item for item in selection.selected}
        may_clear = bool(rows) or scope_complete
        changed: set[int] = set()
        for participation in participations:
            item = selected.get(participation.participation_seq)
            if item is not None:
                reason = item.reason.reason_type
                no_notice = item.no_notice
                absence_source_id = item.reason.source_reason_id
                absence_source_id_type = item.reason.source_reason_id_type
                absence_source_row_revision = item.reason.source_row_revision
                absence_selected_reason_type = item.reason.reason_type
            elif may_clear:
                reason = None
                no_notice = None
                absence_source_id = None
                absence_source_id_type = None
                absence_source_row_revision = None
                absence_selected_reason_type = None
            else:
                continue
            if _update_participation_absence(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                participation=participation,
                absence_reason_detail=reason,
                no_notice=no_notice,
                absence_source_id=absence_source_id,
                absence_source_id_type=absence_source_id_type,
                absence_source_row_revision=absence_source_row_revision,
                absence_selected_reason_type=absence_selected_reason_type,
            ):
                changed.add(participation.participation_seq)
        return changed

    def _project_penalties(
        self,
        connection: Any,
        *,
        source_region: str,
        source_appoint_id: str,
        participations: Sequence[_Participation],
        rows: Sequence[DtsV2CurrentSourceRow],
        scope_complete: bool,
    ) -> set[int]:
        mapped: dict[int, list[tuple[DtsV2CurrentSourceRow, PenaltyRecord]]] = {
            row.participation_seq: [] for row in participations
        }
        if source_region == "dom":
            penalty_participations = tuple(
                PenaltyParticipation(
                    participation_seq=row.participation_seq,
                    teacher_id=row.teacher_id,
                    teacher_id_type=row.teacher_id_type,
                    assigned_at=row.assigned_at,
                    ended_at=row.ended_at,
                    is_completion=row.participation_role == "COMPLETION",
                )
                for row in participations
            )
            for source in rows:
                if source.is_deleted:
                    continue
                record = _penalty_record(source)
                resolution = resolve_penalty_participation(
                    record, penalty_participations
                )
                if not resolution.is_mapped:
                    raise DtsV2DomainDependencyPending(
                        [
                            _source_dependency(
                                source,
                                dependency_key=(
                                    f"course-participations:"
                                    f"{source_appoint_id}"
                                ),
                            )
                        ]
                    )
                assert resolution.participation_seq is not None
                mapped[resolution.participation_seq].append((source, record))

        changed: set[int] = set()
        all_vector_rows = tuple(rows) if source_region == "dom" else ()
        for participation in participations:
            evidence_rows = mapped[participation.participation_seq]
            flags = aggregate_penalty_records(
                (record for _, record in evidence_rows),
                scope_complete=scope_complete if source_region == "dom" else False,
            )
            source_keys = [
                {
                    "source_key": source.source_key,
                    "source_key_type": source.source_key_type,
                }
                for source, _ in sorted(
                    evidence_rows,
                    key=lambda item: _source_row_sort_key(item[0]),
                )
            ]
            source_vector = _version_vector(
                "participation-penalty-current-v1",
                all_vector_rows,
                scope_states={
                    "dom_teacher_penalty": (
                        "COMPLETE" if scope_complete else "UNKNOWN"
                    )
                },
            )
            values = {
                "is_late": flags.is_late,
                "late_evidence_status": flags.late_evidence_status,
                "is_early": flags.is_early,
                "early_evidence_status": flags.early_evidence_status,
                "penalty_source_keys": source_keys,
                "penalty_source_keys_hash": _json_hash(source_keys),
                "source_version_vector": source_vector,
                "source_version_hash": _json_hash(source_vector),
            }
            if _upsert_participation_fact(
                connection,
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                participation_seq=participation.participation_seq,
                values=values,
            ):
                changed.add(participation.participation_seq)
        return changed

    def _build_course_fact(
        self,
        *,
        source_region: str,
        grading_rows: Sequence[DtsV2CurrentSourceRow],
        complaint_plans: Sequence[_ComplaintPlan],
        camera_rows: Sequence[DtsV2CurrentSourceRow],
        scope_states: Mapping[str, str],
    ) -> dict[str, Any]:
        grading_table = f"{source_region}_user_teacher_grading"
        grading_complete = scope_states.get(grading_table) == "COMPLETE"
        if source_region == "dom":
            grading_facts = tuple(_grading_fact(row) for row in grading_rows)
            selected_grading = select_current_dom_grading(grading_facts)
            if selected_grading is None:
                grading_id = None
                grading_classification = (
                    "UNCLASSIFIED" if grading_complete else "SOURCE_MISSING"
                )
                grading_evidence = (
                    "CONFIRMED" if grading_complete else "SOURCE_MISSING"
                )
                grading_error = (
                    None
                    if grading_complete
                    else "SOURCE_MISSING:GRADING_CURRENT_SET_INCOMPLETE"
                )
                negative_score = None
            else:
                grading_id = (
                    selected_grading.fact.identity.source_id,
                    selected_grading.fact.identity.source_id_type,
                )
                grading_classification = (
                    selected_grading.classification or "UNCLASSIFIED"
                )
                grading_evidence = "CONFIRMED"
                grading_error = None
                negative_score = _negative_score(selected_grading.fact)
        else:
            grading_id = None
            grading_classification = "SOURCE_MISSING"
            grading_evidence = "SOURCE_MISSING"
            grading_error = "SOURCE_MISSING:OVS_GRADING_RULE_NOT_DEFINED"
            negative_score = None

        complaint_table = f"{source_region}_complaint"
        complaint_complete = scope_states.get(complaint_table) == "COMPLETE"
        complaint_selection = select_current_complaints(
            plan.selector_fact for plan in complaint_plans
        )
        active_complaints = [
            plan for plan in complaint_plans if not plan.source.is_deleted
        ]
        latest_plan = (
            next(
                plan
                for plan in complaint_plans
                if complaint_selection.latest_valid is not None
                and plan.source.source_key
                == complaint_selection.latest_valid.identity.source_id
            )
            if complaint_selection.latest_valid is not None
            else None
        )
        has_complaint = (
            True
            if active_complaints
            else False
            if complaint_complete
            else None
        )
        has_valid_complaint = (
            True
            if complaint_selection.valid_facts
            else False
            if complaint_complete
            else None
        )
        if latest_plan is not None:
            latest_complaint_id = (
                latest_plan.source.source_key,
                latest_plan.source.source_key_type,
            )
            complaint_evidence = str(latest_plan.values["evidence_status"])
            complaint_error = latest_plan.values["evidence_error_code"]
            latest_l1 = latest_plan.values["category_l1_snapshot"]
            latest_l2 = latest_plan.values["category_l2_snapshot"]
            latest_l3 = latest_plan.values["category_l3_snapshot"]
        else:
            latest_complaint_id = None
            complaint_evidence = (
                "CONFIRMED" if complaint_complete else "SOURCE_MISSING"
            )
            complaint_error = (
                None
                if complaint_complete
                else "SOURCE_MISSING:COMPLAINT_CURRENT_SET_INCOMPLETE"
            )
            latest_l1 = latest_l2 = latest_l3 = None

        camera_table = f"{source_region}_qa_task_close_camera_record"
        camera_complete = scope_states.get(camera_table) == "COMPLETE"
        camera_state = select_current_close_camera_state(
            tuple(
                CloseCameraFact(identity=row.identity, is_deleted=row.is_deleted)
                for row in camera_rows
            ),
            scope_complete=camera_complete,
        )
        source_vector_rows = (
            *grading_rows,
            *(plan.source for plan in complaint_plans),
            *camera_rows,
        )
        source_vector = _version_vector(
            "course-fact-current-v1",
            source_vector_rows,
            scope_states=scope_states,
        )
        return {
            **_typed_columns("current_grading_source_id", grading_id),
            "grading_classification": grading_classification,
            "negative_score": negative_score,
            "grading_evidence_status": grading_evidence,
            "grading_error_code": grading_error,
            **_typed_columns(
                "latest_valid_complaint_id", latest_complaint_id
            ),
            "has_complaint": has_complaint,
            "has_valid_complaint": has_valid_complaint,
            "latest_category_l1_snapshot": latest_l1,
            "latest_category_l2_snapshot": latest_l2,
            "latest_category_l3_snapshot": latest_l3,
            "complaint_evidence_status": complaint_evidence,
            "complaint_error_code": complaint_error,
            "is_camera_off": camera_state,
            "camera_evidence_status": (
                "CONFIRMED" if camera_state is not None else "SOURCE_MISSING"
            ),
            "is_cpu_usage_high": None,
            "cpu_evidence_status": "SOURCE_MISSING",
            "is_network_delay_high": None,
            "network_evidence_status": "SOURCE_MISSING",
            "source_version_vector": source_vector,
            "source_version_hash": _json_hash(source_vector),
        }

    def _publish(
        self,
        connection: Any,
        *,
        aggregate_type: str,
        aggregate_key: Mapping[str, Any],
        aggregate_state: Mapping[str, Any],
        changed_fields: Sequence[str],
        evidence: _TriggerEvidence,
    ) -> int:
        result = self._publish_result(
            connection,
            aggregate_type=aggregate_type,
            aggregate_key=aggregate_key,
            aggregate_state=aggregate_state,
            changed_fields=changed_fields,
            evidence=evidence,
        )
        return 1 if result.status == "CHANGED" else 0

    def _publish_result(
        self,
        connection: Any,
        *,
        aggregate_type: str,
        aggregate_key: Mapping[str, Any],
        aggregate_state: Mapping[str, Any],
        changed_fields: Sequence[str],
        evidence: _TriggerEvidence,
    ) -> Any:
        return self.revisions.publish_change(
            connection,
            aggregate_type=aggregate_type,
            aggregate_key=aggregate_key,
            aggregate_state=aggregate_state,
            changed_fields=changed_fields,
            source_row_revision=evidence.source_row_revision,
            source_position=evidence.source_position,
            rule_version=_DOMAIN_RULE_VERSION,
            cutover_coverage_identity=evidence.coverage_identity,
        )


def _rows_by_table(
    rows: Sequence[DtsV2CurrentSourceRow],
) -> dict[str, tuple[DtsV2CurrentSourceRow, ...]]:
    grouped: dict[str, list[DtsV2CurrentSourceRow]] = {}
    for row in rows:
        grouped.setdefault(row.source_table, []).append(row)
    return {
        table: tuple(sorted(values, key=_source_row_sort_key))
        for table, values in grouped.items()
    }


def _course_child_tables(source_region: str) -> tuple[str, ...]:
    suffixes = list(_COURSE_CHILD_SUFFIXES)
    if source_region == "dom":
        suffixes.extend(_DOM_COURSE_CHILD_SUFFIXES)
    return tuple(f"{source_region}_{suffix}" for suffix in suffixes)


def _read_course_bundle(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
) -> tuple[Mapping[str, Any] | None, tuple[_Participation, ...]]:
    course = connection.execute(
        text(
            """
            SELECT source_status,current_teacher_id,current_teacher_id_type,
                   current_participation_seq,completion_teacher_id,
                   completion_teacher_id_type,completion_participation_seq,
                   completion_conflict_status,completion_conflict_case_id,
                   conflict_fingerprint,source_is_deleted,evidence_status,
                   appoint_evidence_status,
                   teacher_region_evidence_status,
                   last_applied_source_revision,row_version
            FROM public.source_courses
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
            FOR UPDATE
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
        },
    ).mappings().one_or_none()
    rows = connection.execute(
        text(
            """
            SELECT participation_seq,teacher_id,teacher_id_type,
                   participation_status,participation_role,is_current,
                   assigned_at,ended_at,absence_reason_detail,no_notice,
                   absence_source_id,absence_source_id_type,
                   absence_source_row_revision,absence_selected_reason_type,
                   teacher_expected_source_region,
                   teacher_region_evidence_status,
                   teacher_profile_source_row_revision,
                   teacher_profile_source_payload_hash,
                   row_version
            FROM public.source_course_participations
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
            ORDER BY participation_seq
            FOR UPDATE
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
        },
    ).mappings()
    return course, tuple(_participation(row) for row in rows)


def _participation(row: Mapping[str, Any]) -> _Participation:
    return _Participation(
        participation_seq=int(row["participation_seq"]),
        teacher_id=str(row["teacher_id"]),
        teacher_id_type=str(row["teacher_id_type"]),
        participation_status=row.get("participation_status"),
        participation_role=str(row["participation_role"]),
        is_current=bool(row["is_current"]),
        assigned_at=row.get("assigned_at"),
        ended_at=row.get("ended_at"),
        absence_reason_detail=row.get("absence_reason_detail"),
        no_notice=row.get("no_notice"),
        row_version=int(row["row_version"]),
        absence_source_id=row.get("absence_source_id"),
        absence_source_id_type=row.get("absence_source_id_type"),
        absence_source_row_revision=row.get("absence_source_row_revision"),
        absence_selected_reason_type=row.get(
            "absence_selected_reason_type"
        ),
        teacher_expected_source_region=row.get(
            "teacher_expected_source_region"
        ),
        teacher_region_evidence_status=str(
            row.get("teacher_region_evidence_status") or "SOURCE_MISSING"
        ),
        teacher_profile_source_row_revision=row.get(
            "teacher_profile_source_row_revision"
        ),
        teacher_profile_source_payload_hash=row.get(
            "teacher_profile_source_payload_hash"
        ),
    )


def reconcile_course_teacher_region_evidence_v2(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    source_repository: DtsV2SourceRepository,
) -> CourseTeacherRegionReconcileV2:
    """Freeze per-participation DOM profile proof and recompute course gate.

    A missing/deleted/malformed profile is never interpreted as DOM.  The
    reducer keeps appoint evidence separately, so repairing a teacher profile
    can clear a regional conflict without masking an independent appoint
    history conflict.
    """

    if source_region not in _REGIONS or not source_appoint_id:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_TEACHER_REGION_IDENTITY_INVALID"
        )
    course, participations = _read_course_bundle(
        connection,
        source_region=source_region,
        source_appoint_id=source_appoint_id,
    )
    if course is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_TEACHER_REGION_COURSE_MISSING"
        )
    teacher_ids = tuple(sorted({row.teacher_id for row in participations}, key=_id_sort_key))
    try:
        profile_rows = source_repository.read_by_source_keys(
            connection,
            source_region="dom",
            source_table="dom_teacher",
            source_keys=teacher_ids,
        )
    except DtsV2SourceRepositoryError as exc:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_TEACHER_PROFILE_EVIDENCE_INVALID"
        ) from exc
    profiles = {row.source_key: row for row in profile_rows}
    if len(profiles) != len(profile_rows):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_TEACHER_PROFILE_CURRENT_CONFLICT"
        )

    changed: list[int] = []
    statuses: list[str] = []
    for participation in participations:
        profile = profiles.get(participation.teacher_id)
        expected_region: str | None = None
        profile_revision: int | None = None
        profile_hash: str | None = None
        if profile is None:
            status = "SOURCE_MISSING"
        else:
            if profile.source_key_type != participation.teacher_id_type:
                raise DtsV2CourseDomainProjectorError(
                    "DTS_V2_COURSE_TEACHER_ID_TYPE_CONFLICT"
                )
            profile_revision = profile.source_row_revision
            profile_hash = profile.source_payload_hash
            if profile.is_deleted:
                status = "SOURCE_MISSING"
            else:
                try:
                    expected_region = teacher_expected_source_region_v2(
                        profile.text_value("course")
                    )
                except DtsV2SourceRepositoryError as exc:
                    raise DtsV2CourseDomainProjectorError(
                        "DTS_V2_COURSE_TEACHER_COURSE_EVIDENCE_INVALID"
                    ) from exc
                status = (
                    "SOURCE_MISSING"
                    if expected_region is None
                    else "CONFIRMED"
                    if expected_region == source_region
                    else "SOURCE_CONFLICT"
                )
        statuses.append(status)
        desired = (
            expected_region,
            status,
            profile_revision,
            profile_hash,
        )
        current = (
            participation.teacher_expected_source_region,
            participation.teacher_region_evidence_status,
            participation.teacher_profile_source_row_revision,
            participation.teacher_profile_source_payload_hash,
        )
        if current == desired:
            continue
        updated = connection.execute(
            text(
                """
                UPDATE public.source_course_participations
                SET teacher_expected_source_region=:expected_region,
                    teacher_region_evidence_status=:evidence_status,
                    teacher_profile_source_row_revision=:profile_revision,
                    teacher_profile_source_payload_hash=:profile_hash,
                    row_version=row_version+1
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND participation_seq=:participation_seq
                  AND row_version=:expected_row_version
                RETURNING row_version
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "participation_seq": participation.participation_seq,
                "expected_row_version": participation.row_version,
                "expected_region": expected_region,
                "evidence_status": status,
                "profile_revision": profile_revision,
                "profile_hash": profile_hash,
            },
        ).scalar_one_or_none()
        if updated is None:
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_TEACHER_REGION_CONCURRENT_UPDATE"
            )
        changed.append(participation.participation_seq)

    regional_status = (
        "SOURCE_CONFLICT"
        if "SOURCE_CONFLICT" in statuses
        else "SOURCE_MISSING"
        if not statuses or "SOURCE_MISSING" in statuses
        else "CONFIRMED"
    )
    appoint_status = str(course.get("appoint_evidence_status"))
    combined_status = _combined_course_evidence_status(
        appoint_status=appoint_status,
        teacher_region_status=regional_status,
    )
    course_changed = (
        course.get("teacher_region_evidence_status") != regional_status
        or course.get("evidence_status") != combined_status
    )
    if course_changed:
        updated = connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET teacher_region_evidence_status=:regional_status,
                    evidence_status=:combined_status,
                    row_version=row_version+1,
                    updated_at=clock_timestamp()
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND row_version=:expected_row_version
                RETURNING row_version
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "expected_row_version": int(course["row_version"]),
                "regional_status": regional_status,
                "combined_status": combined_status,
            },
        ).scalar_one_or_none()
        if updated is None:
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_TEACHER_REGION_CONCURRENT_UPDATE"
            )
    return CourseTeacherRegionReconcileV2(
        course_changed=course_changed,
        changed_participation_seqs=tuple(changed),
        affected_teacher_ids=teacher_ids,
    )


def _combined_course_evidence_status(
    *,
    appoint_status: str,
    teacher_region_status: str,
) -> str:
    allowed = {"CONFIRMED", "SOURCE_MISSING", "SOURCE_CONFLICT"}
    if appoint_status not in allowed or teacher_region_status not in allowed:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_EVIDENCE_STATUS_INVALID"
        )
    if "SOURCE_CONFLICT" in {appoint_status, teacher_region_status}:
        return "SOURCE_CONFLICT"
    if "SOURCE_MISSING" in {appoint_status, teacher_region_status}:
        return "SOURCE_MISSING"
    return "CONFIRMED"


def _missing_course_dependency(
    claim: DirtyClaimV2,
    rows: Sequence[DtsV2CurrentSourceRow],
    *,
    source_appoint_id: str,
) -> DirtyDependencyV2:
    key = f"{claim.key.source_region}_appoint:{source_appoint_id}"
    if rows:
        witness = max(rows, key=_source_row_sort_key)
        return _source_dependency(witness, dependency_key=key)
    fingerprint = _json_hash(
        {
            "dependency_type": "SOURCE_ROW",
            "dependency_region": claim.key.source_region,
            "dependency_key": key,
            "claimed_work_revision": claim.claimed_work_revision,
        }
    )
    return DirtyDependencyV2(
        "SOURCE_ROW",
        claim.key.source_region,
        key,
        claim.claimed_work_revision,
        fingerprint,
    )


def _source_dependency(
    source: DtsV2CurrentSourceRow,
    *,
    dependency_key: str,
) -> DirtyDependencyV2:
    return DirtyDependencyV2(
        "SOURCE_ROW",
        source.source_region,
        dependency_key,
        source.source_row_revision,
        source.source_payload_hash,
    )


def _read_current_global_scope_states(
    connection: Any,
    *,
    source_region: str,
    source_tables: Sequence[str],
) -> dict[str, str]:
    if not source_tables:
        return {}
    rows = connection.execute(
        text(
            """
            SELECT source_table,state
            FROM public.dts_source_scope_states
            WHERE source_region=:source_region
              AND source_table=ANY(CAST(:source_tables AS text[]))
              AND scope_kind='CURRENT'
              AND scope_level='GLOBAL'
              AND scope_key='*'
            ORDER BY source_table
            FOR SHARE
            """
        ),
        {
            "source_region": source_region,
            "source_tables": sorted(set(source_tables)),
        },
    ).mappings()
    return {str(row["source_table"]): str(row["state"]) for row in rows}


def _read_claim_trigger_evidence(
    connection: Any,
    claim: DirtyClaimV2,
    *,
    base_coverage_identity: Mapping[str, Any],
) -> _TriggerEvidence:
    latest = connection.execute(
        text(
            """
            SELECT input_kind,input_identity,input_revision,input_fingerprint,
                   dirty_work_revision
            FROM public.dts_dirty_key_inputs
            WHERE source_region=:source_region
              AND key_type=:key_type
              AND key_part_1=:key_part_1
              AND key_part_2=:key_part_2
              AND dirty_work_revision<=:claimed_work_revision
            ORDER BY dirty_work_revision DESC
            LIMIT 1
            """
        ),
        _claim_input_params(claim),
    ).mappings().one_or_none()
    if latest is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_CAUSAL_INPUT_REQUIRED"
        )
    latest_kind = latest.get("input_kind")
    if latest_kind == "CATALOG_REVISION":
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_CATALOG_TRIGGER_UNSUPPORTED"
        )
    if latest_kind == "TIME_RECHECK":
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_TIME_RECHECK_TRIGGER_FORBIDDEN"
        )

    row = connection.execute(
        text(
            """
            SELECT input_kind,input_identity,input_revision,input_fingerprint,
                   dirty_work_revision
            FROM public.dts_dirty_key_inputs
            WHERE source_region=:source_region
              AND key_type=:key_type
              AND key_part_1=:key_part_1
              AND key_part_2=:key_part_2
              AND dirty_work_revision<=:claimed_work_revision
              AND input_kind IN ('SOURCE_REVISION','SCOPE_REVISION')
            ORDER BY dirty_work_revision DESC
            LIMIT 1
            """
        ),
        _claim_input_params(claim),
    ).mappings().one_or_none()
    if row is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_CAUSAL_INPUT_REQUIRED"
        )
    revision = row.get("input_revision")
    identity = row.get("input_identity")
    fingerprint = row.get("input_fingerprint")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(identity, Mapping)
        or not isinstance(fingerprint, str)
        or _SHA256.fullmatch(fingerprint) is None
    ):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_TRIGGER_EVIDENCE_INVALID"
        )
    input_kind = row.get("input_kind")
    if input_kind == "SOURCE_REVISION":
        return _read_source_trigger_evidence(
            connection,
            identity=dict(identity),
            revision=revision,
            fingerprint=fingerprint,
            base_coverage_identity=base_coverage_identity,
        )
    if input_kind == "SCOPE_REVISION":
        return _read_scope_trigger_evidence(
            connection,
            identity=dict(identity),
            revision=revision,
            fingerprint=fingerprint,
            base_coverage_identity=base_coverage_identity,
        )
    raise DtsV2CourseDomainProjectorError(
        "DTS_V2_COURSE_DOMAIN_CAUSAL_INPUT_REQUIRED"
    )


def _claim_input_params(claim: DirtyClaimV2) -> dict[str, Any]:
    return {
        "source_region": claim.key.source_region,
        "key_type": claim.key.key_type,
        "key_part_1": claim.key.key_part_1,
        "key_part_2": claim.key.key_part_2,
        "claimed_work_revision": claim.claimed_work_revision,
    }


def _read_source_trigger_evidence(
    connection: Any,
    *,
    identity: Mapping[str, Any],
    revision: int,
    fingerprint: str,
    base_coverage_identity: Mapping[str, Any],
) -> _TriggerEvidence:
    if set(identity) != {"source_region", "source_table", "source_key"}:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TRIGGER_IDENTITY_INVALID"
        )
    row = connection.execute(
        text(
            """
            SELECT version.source_position,version.version_kind,
                   version.operation,version.protected_source_row_hash,
                   current_row.source_row_revision,
                   current_row.source_payload_hash,current_row.is_deleted,
                   current_row.provenance_state
            FROM public.dts_source_row_versions version
            JOIN public.dts_source_rows current_row
              ON current_row.source_region=version.source_region
             AND current_row.source_table=version.source_table
             AND current_row.source_key=version.source_key
            WHERE version.source_region=:source_region
              AND version.source_table=:source_table
              AND version.source_key=:source_key
              AND version.source_row_revision=:source_row_revision
            FOR SHARE OF version,current_row
            """
        ),
        {
            **dict(identity),
            "source_row_revision": revision,
        },
    ).mappings().one_or_none()
    if row is None or not isinstance(row.get("source_position"), Mapping):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TRIGGER_MISSING"
        )
    protected_hash = row.get("protected_source_row_hash")
    expected_fingerprint = _json_hash(
        {
            "protocol": "dirty-source-v1",
            "identity": dict(identity),
            "revision": revision,
            "version_kind": row.get("version_kind"),
            "operation": row.get("operation"),
            "is_deleted": row.get("is_deleted"),
            "protected_source_row_hash": protected_hash,
        }
    )
    if (
        row.get("provenance_state") != "V2_CONFIRMED"
        or row.get("source_row_revision") != revision
        or not isinstance(protected_hash, str)
        or _SHA256.fullmatch(protected_hash) is None
        or row.get("source_payload_hash") != protected_hash
        or fingerprint != expected_fingerprint
    ):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TRIGGER_CONFLICT"
        )
    trigger = {
        "input_kind": "SOURCE_REVISION",
        "input_identity": dict(identity),
        "input_revision": revision,
        "input_fingerprint": fingerprint,
        "source_payload_hash": protected_hash,
    }
    return _TriggerEvidence(
        revision,
        dict(row["source_position"]),
        {**dict(base_coverage_identity), "trigger": trigger},
    )


def _read_scope_trigger_evidence(
    connection: Any,
    *,
    identity: Mapping[str, Any],
    revision: int,
    fingerprint: str,
    base_coverage_identity: Mapping[str, Any],
) -> _TriggerEvidence:
    expected_identity_keys = {
        "source_region",
        "source_table",
        "scope_kind",
        "scope_level",
        "scope_key",
    }
    if set(identity) != expected_identity_keys:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SCOPE_TRIGGER_IDENTITY_INVALID"
        )
    row = connection.execute(
        text(
            """
            SELECT canonical_key,aggregate_state
            FROM public.domain_aggregate_revisions
            WHERE aggregate_type='SOURCE_SCOPE'
              AND canonical_key=CAST(:canonical_key AS jsonb)
            FOR SHARE
            """
        ),
        {"canonical_key": _json_dump(identity)},
    ).mappings().one_or_none()
    if row is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SCOPE_TRIGGER_MISSING"
        )
    canonical_key = row.get("canonical_key")
    state = row.get("aggregate_state")
    if canonical_key != identity or not isinstance(state, Mapping):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SCOPE_TRIGGER_CONFLICT"
        )
    state_keys = {
        "protocol",
        "source_region",
        "source_table",
        "scope_kind",
        "scope_level",
        "scope_key",
        "scope_row_version",
        "state",
        "active_snapshot_id",
        "active_epoch_id",
        "active_fence_hash",
    }
    if (
        set(state) != state_keys
        or state.get("protocol") != "source-scope-state-v1"
        or any(state.get(key) != identity[key] for key in expected_identity_keys)
        or state.get("scope_row_version") != revision
        or state.get("active_epoch_id") != state.get("active_snapshot_id")
    ):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SCOPE_TRIGGER_CONFLICT"
        )
    expected_fingerprint = _json_hash(
        {
            "protocol": "dirty-scope-v1",
            "identity": dict(identity),
            "scope_row_version": revision,
            "state": state.get("state"),
            "active_snapshot_id": state.get("active_snapshot_id"),
            "active_epoch_id": state.get("active_epoch_id"),
            "active_fence_hash": state.get("active_fence_hash"),
        }
    )
    active_fence_hash = state.get("active_fence_hash")
    if (
        fingerprint != expected_fingerprint
        or (
            active_fence_hash is not None
            and (
                not isinstance(active_fence_hash, str)
                or _SHA256.fullmatch(active_fence_hash) is None
            )
        )
    ):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SCOPE_TRIGGER_CONFLICT"
        )
    trigger = {
        "input_kind": "SCOPE_REVISION",
        "input_identity": dict(identity),
        "input_revision": revision,
        "input_fingerprint": fingerprint,
        "scope_state": state.get("state"),
        "active_snapshot_id": state.get("active_snapshot_id"),
        "active_fence_hash": active_fence_hash,
    }
    return _TriggerEvidence(
        None,
        None,
        {**dict(base_coverage_identity), "trigger": trigger},
    )


def _required_course_id(row: DtsV2CurrentSourceRow) -> str:
    value = row.typed_id("appoint_id")
    if value is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_CHILD_COURSE_REQUIRED"
        )
    return value[0]


def _required_typed_id(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> tuple[str, str]:
    value = row.typed_id(field_name)
    if value is None:
        raise DtsV2CourseDomainProjectorError(
            f"DTS_V2_COURSE_DOMAIN_{field_name.upper()}_REQUIRED"
        )
    return value


def _first_typed_id(
    row: DtsV2CurrentSourceRow,
    field_names: Sequence[str],
) -> tuple[str, str] | None:
    values = [row.typed_id(name) for name in field_names if row.value(name) is not None]
    if not values:
        return None
    if len(set(values)) != 1:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_TEACHER_ID_CONFLICT"
        )
    return values[0]


def _typed_columns(
    prefix: str,
    identity: tuple[str, str] | None,
) -> dict[str, Any]:
    if identity is None:
        return {
            prefix: None,
            f"{prefix}_type": None,
            f"{prefix}_numeric": None,
            f"{prefix}_text": None,
        }
    value, source_type = identity
    if source_type == "NUMERIC":
        numeric, source_text = Decimal(value), None
    elif source_type == "TEXT":
        numeric, source_text = None, value
    else:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_TYPED_ID_INVALID"
        )
    return {
        prefix: value,
        f"{prefix}_type": source_type,
        f"{prefix}_numeric": numeric,
        f"{prefix}_text": source_text,
    }


def _source_version(row: DtsV2CurrentSourceRow) -> dict[str, Any]:
    return {
        "protocol_version": "typed-source-current-v1",
        **row.version_vector_entry(),
    }


def _upsert_label(
    connection: Any,
    *,
    source: DtsV2CurrentSourceRow,
    source_appoint_id: str,
    label_id: str,
    label_id_type: str,
) -> bool:
    values = {
        "source_region": source.source_region,
        **_typed_columns(
            "source_log_id", (source.source_key, source.source_key_type)
        ),
        "source_appoint_id": source_appoint_id,
        **_typed_columns("label_id", (label_id, label_id_type)),
        "label_name_snapshot": _optional_text(source, "label_name"),
        "create_time": _optional_datetime(source, "create_time"),
        "dt": _optional_datetime(source, "dt"),
        "source_position": _json_dump(dict(source.source_position)),
        "source_row_revision": source.source_row_revision,
        "evidence_status": "CONFIRMED",
        "evidence_error_code": None,
        "is_deleted": source.is_deleted,
        "source_version": _json_dump(_source_version(source)),
    }
    columns = tuple(values)
    mutable = tuple(
        column
        for column in columns
        if column not in {"source_region", "source_log_id"}
    )
    result = connection.execute(
        text(
            f"""
            INSERT INTO public.source_course_labels ({','.join(columns)})
            VALUES ({','.join(_bind(column) for column in columns)})
            ON CONFLICT (source_region,source_log_id) DO UPDATE
            SET {','.join(f'{column}=EXCLUDED.{column}' for column in mutable)},
                updated_at=clock_timestamp()
            WHERE ROW({','.join(f'source_course_labels.{column}' for column in mutable)})
                  IS DISTINCT FROM ROW({','.join(f'EXCLUDED.{column}' for column in mutable)})
            RETURNING source_log_id
            """
        ),
        values,
    ).scalar_one_or_none()
    return result is not None


def _read_existing_label(
    connection: Any,
    *,
    source_region: str,
    source_log_id: str,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT source_appoint_id,label_id
            FROM public.source_course_labels
            WHERE source_region=:source_region
              AND source_log_id=:source_log_id
            FOR UPDATE
            """
        ),
        {"source_region": source_region, "source_log_id": source_log_id},
    ).mappings().one_or_none()


def _read_existing_complaint(
    connection: Any,
    *,
    source_region: str,
    source_complaint_id: str,
) -> Mapping[str, Any] | None:
    return connection.execute(
        text(
            """
            SELECT source_appoint_id
            FROM public.source_course_complaints
            WHERE source_region=:source_region
              AND source_complaint_id=:source_complaint_id
            FOR UPDATE
            """
        ),
        {
            "source_region": source_region,
            "source_complaint_id": source_complaint_id,
        },
    ).mappings().one_or_none()


def _upsert_complaint(connection: Any, plan: _ComplaintPlan) -> bool:
    values = {
        "source_region": plan.source.source_region,
        **dict(plan.values),
    }
    values["source_version"] = _json_dump(values["source_version"])
    values["source_position"] = _json_dump(values["source_position"])
    columns = tuple(values)
    mutable = tuple(
        column
        for column in columns
        if column not in {"source_region", "source_complaint_id"}
    )
    return (
        connection.execute(
            text(
                f"""
                INSERT INTO public.source_course_complaints ({','.join(columns)})
                VALUES ({','.join(_bind(column) for column in columns)})
                ON CONFLICT (source_region,source_complaint_id) DO UPDATE
                SET {','.join(f'{column}=EXCLUDED.{column}' for column in mutable)},
                    updated_at=clock_timestamp()
                WHERE ROW({','.join(f'source_course_complaints.{column}' for column in mutable)})
                      IS DISTINCT FROM ROW({','.join(f'EXCLUDED.{column}' for column in mutable)})
                RETURNING source_complaint_id
                """
            ),
            values,
        ).scalar_one_or_none()
        is not None
    )


def _read_complaint_rules(
    connection: Any,
    normalized_names: set[str],
) -> dict[str, tuple[_ComplaintRule, ...]]:
    if not normalized_names:
        return {}
    # Publication takes the same locks in shared/exclusive order.  Holding both
    # shared locks freezes one catalog generation for the full course rebuild.
    connection.execute(
        text(
            "SELECT pg_advisory_xact_lock_shared("
            "hashtextextended('tit:dts-v2-cutover',0))"
        )
    )
    connection.execute(
        text(
            "SELECT pg_advisory_xact_lock_shared(hashtextextended("
            "'tit:catalog:COMPLAINT_RULE_SET:ACTIVE_COMPLAINT_RULE_SET',0))"
        )
    )
    rows = connection.execute(
        text(
            """
            SELECT rule.rule_id,rule.source_sha256,
                   rule.category_l3_normalized,rule.severity_rank
            FROM public.complaint_rule_imports imported
            JOIN public.complaint_category_rules rule
              ON rule.source_sha256=imported.source_sha256
            WHERE imported.status='PUBLISHED'
              AND rule.category_l3_normalized=ANY(CAST(:names AS text[]))
            ORDER BY rule.category_l3_normalized,
                     rule.source_sha256,rule.source_row_number
            FOR SHARE OF imported,rule
            """
        ),
        {"names": sorted(normalized_names)},
    ).mappings()
    grouped: dict[str, list[_ComplaintRule]] = {}
    for row in rows:
        rule_id = str(row["rule_id"])
        source_sha256 = str(row["source_sha256"])
        normalized = str(row["category_l3_normalized"])
        severity = row["severity_rank"]
        match = _RULE_ID.fullmatch(rule_id)
        if (
            match is None
            or match.group("sha") != source_sha256
            or _SHA256.fullmatch(source_sha256) is None
            or isinstance(severity, bool)
            or not isinstance(severity, int)
            or severity not in {0, 1, 2, 3, 4}
        ):
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_COMPLAINT_RULE_INVALID"
            )
        grouped.setdefault(normalized, []).append(
            _ComplaintRule(rule_id, source_sha256, normalized, severity)
        )
    return {name: tuple(values) for name, values in grouped.items()}


def _absence_reason(row: DtsV2CurrentSourceRow) -> AbsenceReasonFact:
    teacher = row.typed_id("t_id")
    return AbsenceReasonFact(
        source_reason_id=row.source_key,
        source_reason_id_type=row.source_key_type,
        source_row_revision=row.source_row_revision,
        teacher_id=None if teacher is None else teacher[0],
        teacher_id_type=None if teacher is None else teacher[1],
        reason_type=_optional_text(row, "reason_type"),
        add_time=_optional_datetime(row, "add_time"),
        source_timestamp=_position_timestamp(row.source_position),
        is_deleted=row.is_deleted,
    )


def _source_for_reason(
    rows: Sequence[DtsV2CurrentSourceRow],
    reason: AbsenceReasonFact,
) -> DtsV2CurrentSourceRow:
    for row in rows:
        if row.source_key == reason.source_reason_id:
            return row
    raise AssertionError("absence source must be present")


def _update_participation_absence(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    participation: _Participation,
    absence_reason_detail: str | None,
    no_notice: bool | None,
    absence_source_id: str | None,
    absence_source_id_type: str | None,
    absence_source_row_revision: int | None,
    absence_selected_reason_type: str | None,
) -> bool:
    result = connection.execute(
        text(
            """
            UPDATE public.source_course_participations
            SET absence_reason_detail=:absence_reason_detail,
                no_notice=:no_notice,
                absence_source_id=:absence_source_id,
                absence_source_id_type=:absence_source_id_type,
                absence_source_row_revision=:absence_source_row_revision,
                absence_selected_reason_type=:absence_selected_reason_type,
                row_version=row_version+1
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
              AND participation_seq=:participation_seq
              AND row_version=:row_version
              AND ROW(
                    absence_reason_detail,no_notice,absence_source_id,
                    absence_source_id_type,absence_source_row_revision,
                    absence_selected_reason_type
                  ) IS DISTINCT FROM ROW(
                    :absence_reason_detail,:no_notice,:absence_source_id,
                    :absence_source_id_type,:absence_source_row_revision,
                    :absence_selected_reason_type
                  )
            RETURNING participation_seq
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
            "participation_seq": participation.participation_seq,
            "row_version": participation.row_version,
            "absence_reason_detail": absence_reason_detail,
            "no_notice": no_notice,
            "absence_source_id": absence_source_id,
            "absence_source_id_type": absence_source_id_type,
            "absence_source_row_revision": absence_source_row_revision,
            "absence_selected_reason_type": absence_selected_reason_type,
        },
    ).scalar_one_or_none()
    return result is not None


def _penalty_record(row: DtsV2CurrentSourceRow) -> PenaltyRecord:
    teacher = row.typed_id("t_id")
    return PenaltyRecord(
        teacher_id=None if teacher is None else teacher[0],
        teacher_id_type=None if teacher is None else teacher[1],
        lesson_start_time=row.value("lesson_start_time"),
        in_time=row.value("in_time"),
        out_time=row.value("out_time"),
        appeal_status=row.value("appeal_status"),
    )


def _upsert_participation_fact(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    participation_seq: int,
    values: Mapping[str, Any],
) -> bool:
    params = {
        "source_region": source_region,
        "source_appoint_id": source_appoint_id,
        "participation_seq": participation_seq,
        **values,
    }
    for column in (
        "penalty_source_keys",
        "source_version_vector",
    ):
        params[column] = _json_dump(params[column])
    mutable = tuple(values)
    return (
        connection.execute(
            text(
                f"""
                INSERT INTO public.source_participation_fact_current (
                    source_region,source_appoint_id,participation_seq,
                    {','.join(mutable)},row_version
                ) VALUES (
                    :source_region,:source_appoint_id,:participation_seq,
                    {','.join(_bind(column) for column in mutable)},1
                )
                ON CONFLICT (source_region,source_appoint_id,participation_seq)
                DO UPDATE SET
                    {','.join(f'{column}=EXCLUDED.{column}' for column in mutable)},
                    row_version=source_participation_fact_current.row_version+1,
                    updated_at=clock_timestamp()
                WHERE ROW({','.join(
                    f'source_participation_fact_current.{column}'
                    for column in mutable
                )})
                      IS DISTINCT FROM ROW({','.join(f'EXCLUDED.{column}' for column in mutable)})
                RETURNING participation_seq
                """
            ),
            params,
        ).scalar_one_or_none()
        is not None
    )


def _grading_fact(row: DtsV2CurrentSourceRow) -> DomGradingFact:
    return DomGradingFact(
        identity=row.identity,
        use_point=row.value("use_point"),
        score=row.value("score"),
        grading_type=row.value("type"),
        update_time=_optional_datetime(row, "update_time"),
        create_time=_optional_datetime(row, "create_time"),
        start_time=_optional_datetime(row, "start_time"),
        dt=_optional_datetime(row, "dt"),
        is_del=row.value("is_del"),
        is_deleted=row.is_deleted,
    )


def _negative_score(fact: DomGradingFact) -> Decimal | None:
    if (
        not isinstance(fact.use_point, str)
        or fact.use_point.strip().lower() != "buy"
        or fact.score is None
    ):
        return None
    try:
        score = Decimal(str(fact.score))
    except (InvalidOperation, ValueError) as exc:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_GRADING_SCORE_INVALID"
        ) from exc
    return score if score in {Decimal(1), Decimal(2)} else None


def _upsert_course_fact(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    values: Mapping[str, Any],
) -> bool:
    params = {
        "source_region": source_region,
        "source_appoint_id": source_appoint_id,
        **values,
    }
    params["source_version_vector"] = _json_dump(
        params["source_version_vector"]
    )
    mutable = tuple(values)
    return (
        connection.execute(
            text(
                f"""
                INSERT INTO public.source_course_fact_current (
                    source_region,source_appoint_id,{','.join(mutable)},row_version
                ) VALUES (
                    :source_region,:source_appoint_id,
                    {','.join(_bind(column) for column in mutable)},1
                )
                ON CONFLICT (source_region,source_appoint_id) DO UPDATE SET
                    {','.join(f'{column}=EXCLUDED.{column}' for column in mutable)},
                    row_version=source_course_fact_current.row_version+1,
                    updated_at=clock_timestamp()
                WHERE ROW({','.join(f'source_course_fact_current.{column}' for column in mutable)})
                      IS DISTINCT FROM ROW({','.join(f'EXCLUDED.{column}' for column in mutable)})
                RETURNING source_appoint_id
                """
            ),
            params,
        ).scalar_one_or_none()
        is not None
    )


def _read_course_aggregate_state(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
) -> Mapping[str, Any] | None:
    course = connection.execute(
        text(
            """
            SELECT student_token,lesson_local_date,lesson_local_time,
                   scheduled_start_at,end_time,source_status,
                   current_teacher_id,current_teacher_id_type,
                   current_participation_seq,is_peak,
                   completion_teacher_id,completion_teacher_id_type,
                   completion_participation_seq,completion_frozen_at,
                   completion_end_time,completion_student_token,
                   completion_is_peak,completion_lesson_local_date,
                   completion_lesson_local_time,completion_source_revision,
                   completion_conflict_status,completion_conflict_case_id,
                   conflict_fingerprint,source_is_deleted,evidence_status,
                   appoint_evidence_status,
                   teacher_region_evidence_status,
                   row_version
            FROM public.source_courses
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
            """
        ),
        {"source_region": source_region, "source_appoint_id": source_appoint_id},
    ).mappings().one_or_none()
    if course is None:
        return None
    fact = connection.execute(
        text(
            """
            SELECT current_grading_source_id,current_grading_source_id_type,
                   grading_classification,negative_score,
                   grading_evidence_status,grading_error_code,
                   latest_valid_complaint_id,
                   latest_valid_complaint_id_type,has_complaint,
                   has_valid_complaint,latest_category_l1_snapshot,
                   latest_category_l2_snapshot,latest_category_l3_snapshot,
                   complaint_evidence_status,complaint_error_code,
                   is_camera_off,camera_evidence_status,
                   is_cpu_usage_high,cpu_evidence_status,
                   is_network_delay_high,network_evidence_status,row_version
            FROM public.source_course_fact_current
            WHERE source_region=:source_region
              AND source_appoint_id=:source_appoint_id
            """
        ),
        {"source_region": source_region, "source_appoint_id": source_appoint_id},
    ).mappings().one_or_none()
    labels = list(
        connection.execute(
            text(
                """
                SELECT DISTINCT ON (label_id_type,label_id)
                       label_id,label_id_type,label_name_snapshot
                FROM public.source_course_labels
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND is_deleted=false
                ORDER BY label_id_type,label_id,
                         create_time DESC NULLS LAST,dt DESC NULLS LAST,
                         source_log_id_numeric DESC NULLS LAST,
                         convert_to(COALESCE(source_log_id_text,''),'UTF8') DESC,
                         source_row_revision DESC
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
            },
        ).mappings()
    )
    complaints = list(
        connection.execute(
            text(
                """
                SELECT source_complaint_id,source_complaint_id_type,is_valid,
                       complaint_type,complaint_type_type,
                       complaint_type_child,complaint_type_child_type,
                       complaint_type_grandson,complaint_type_grandson_type,
                       add_time,course_date,source_row_revision,
                       category_l1_snapshot,category_l2_snapshot,
                       category_l3_snapshot,evidence_status,
                       evidence_error_code,complaint_rule_id,source_sha256,
                       severity_rank
                FROM public.source_course_complaints
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND is_deleted=false
                ORDER BY source_complaint_id_type,
                         source_complaint_id_numeric NULLS LAST,
                         convert_to(COALESCE(source_complaint_id_text,''),'UTF8')
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
            },
        ).mappings()
    )
    participations = list(
        connection.execute(
            text(
                """
                SELECT p.participation_seq,p.teacher_id,p.teacher_id_type,
                       p.participation_status,p.participation_role,p.is_current,
                       p.assigned_at,p.assigned_at_evidence_status,p.ended_at,
                       p.absence_reason_detail,p.no_notice,
                       p.absence_source_id,p.absence_source_id_type,
                       p.absence_source_row_revision,
                       p.absence_selected_reason_type,
                       p.teacher_expected_source_region,
                       p.teacher_region_evidence_status,
                       p.teacher_profile_source_row_revision,
                       p.teacher_profile_source_payload_hash,
                       p.source_deleted,
                       p.assignment_source_row_revision,p.row_version,
                       f.is_late,f.late_evidence_status,
                       f.is_early,f.early_evidence_status,
                       f.row_version AS participation_fact_row_version
                FROM public.source_course_participations p
                LEFT JOIN public.source_participation_fact_current f
                  ON f.source_region=p.source_region
                 AND f.source_appoint_id=p.source_appoint_id
                 AND f.participation_seq=p.participation_seq
                WHERE p.source_region=:source_region
                  AND p.source_appoint_id=:source_appoint_id
                ORDER BY p.participation_seq
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
            },
        ).mappings()
    )
    return _json_safe(
        {
            "course": dict(course),
            "course_fact": None if fact is None else dict(fact),
            "labels": [dict(row) for row in labels],
            "complaints": [dict(row) for row in complaints],
            "participations": [dict(row) for row in participations],
        }
    )


def _read_label_aggregate_state(
    connection: Any,
    *,
    source_region: str,
    label_id: str,
) -> Mapping[str, Any]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (source_appoint_id)
                   source_appoint_id,label_id_type,label_name_snapshot
            FROM public.source_course_labels
            WHERE source_region=:source_region
              AND label_id=:label_id
              AND is_deleted=false
            ORDER BY source_appoint_id,
                     create_time DESC NULLS LAST,dt DESC NULLS LAST,
                     source_log_id_numeric DESC NULLS LAST,
                     convert_to(COALESCE(source_log_id_text,''),'UTF8') DESC,
                     source_row_revision DESC
            """
        ),
        {"source_region": source_region, "label_id": label_id},
    ).mappings()
    return _json_safe({"course_labels": [dict(row) for row in rows]})


def _read_participation_aggregate_state(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    participation_seq: int,
) -> Mapping[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT p.teacher_id,p.teacher_id_type,p.participation_status,
                   p.participation_role,p.is_current,p.absence_reason_detail,
                   p.no_notice,p.absence_source_id,p.absence_source_id_type,
                   p.absence_source_row_revision,
                   p.absence_selected_reason_type,
                   f.is_late,f.late_evidence_status,
                   f.is_early,f.early_evidence_status,
                   f.penalty_source_keys
            FROM public.source_course_participations p
            LEFT JOIN public.source_participation_fact_current f
              ON f.source_region=p.source_region
             AND f.source_appoint_id=p.source_appoint_id
             AND f.participation_seq=p.participation_seq
            WHERE p.source_region=:source_region
              AND p.source_appoint_id=:source_appoint_id
              AND p.participation_seq=:participation_seq
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
            "participation_seq": participation_seq,
        },
    ).mappings().one_or_none()
    return None if row is None else _json_safe({"participation": dict(row)})


def _completion_conflict_state(
    course: Mapping[str, Any],
    *,
    source_region: str,
    source_appoint_id: str,
) -> Mapping[str, Any]:
    status = course.get("completion_conflict_status")
    planned_case_id = (
        "course-completion-correction:"
        f"{source_region}:{source_appoint_id}"
        if status == "PENDING"
        else None
    )
    return _json_safe(
        {
            "completion_conflict": {
                "status": status,
                "case_id": planned_case_id,
                "fingerprint": course.get("conflict_fingerprint"),
                "completion_teacher_id": course.get("completion_teacher_id"),
                "completion_teacher_id_type": course.get(
                    "completion_teacher_id_type"
                ),
                "completion_participation_seq": course.get(
                    "completion_participation_seq"
                ),
            }
        }
    )


def _reconcile_completion_conflict_case(
    connection: Any,
    *,
    source_region: str,
    source_appoint_id: str,
    expected_aggregate_revision: int,
    triggering_event_id: str,
    expected_planned_case_id: str | None,
) -> Mapping[str, Any]:
    value = connection.execute(
        text(
            """
            SELECT public.reconcile_completion_conflict_case_v2(
                :source_region,:source_appoint_id,
                :expected_aggregate_revision,:triggering_event_id
            )
            """
        ),
        {
            "source_region": source_region,
            "source_appoint_id": source_appoint_id,
            "expected_aggregate_revision": expected_aggregate_revision,
            "triggering_event_id": triggering_event_id,
        },
    ).scalar_one()
    if not isinstance(value, Mapping) or set(value) != {"outcome", "case_id"}:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COMPLETION_CONFLICT_RECONCILE_RESULT_INVALID"
        )
    outcome = value.get("outcome")
    case_id = value.get("case_id")
    if expected_planned_case_id is None:
        valid = outcome == "NOT_PENDING" and case_id is None
    elif outcome == "SHADOW_ONLY":
        # First shadow capture has no visible Case.  ROLLED_BACK preserves a
        # canonical pointer created by an earlier PRIMARY epoch, but never
        # creates one for the new conflict.
        valid = case_id in {None, expected_planned_case_id}
    else:
        valid = (
            outcome in {"CREATED", "UPDATED", "UNCHANGED"}
            and case_id == expected_planned_case_id
        )
    if not valid:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COMPLETION_CONFLICT_RECONCILE_STATE_MISMATCH"
        )
    return dict(value)


def _optional_text(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> str | None:
    return row.text_value(field_name) if row.value(field_name) is not None else None


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_datetime(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> datetime | None:
    return (
        row.datetime_value(field_name)
        if row.value(field_name) is not None
        else None
    )


def _optional_date(
    row: DtsV2CurrentSourceRow,
    field_name: str,
) -> date | None:
    return row.date_value(field_name) if row.value(field_name) is not None else None


def _optional_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return int(number) if number.is_finite() and number == number.to_integral() else None


def _normalize_category(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip()
    return re.sub(r"\s+", " ", normalized)


def _position_timestamp(position: Mapping[str, Any]) -> datetime | None:
    raw = position.get("source_timestamp")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TIMESTAMP_INVALID"
        )
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TIMESTAMP_INVALID"
        ) from exc
    if parsed.utcoffset() is None:
        raise DtsV2CourseDomainProjectorError(
            "DTS_V2_COURSE_DOMAIN_SOURCE_TIMESTAMP_INVALID"
        )
    return parsed.astimezone(timezone.utc)


def _version_vector(
    protocol_version: str,
    rows: Sequence[DtsV2CurrentSourceRow],
    *,
    scope_states: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "protocol_version": protocol_version,
        "sources": [
            row.version_vector_entry()
            for row in sorted(rows, key=_source_row_sort_key)
        ],
        "scope_states": dict(sorted(scope_states.items())),
    }


def _source_row_sort_key(row: DtsV2CurrentSourceRow) -> tuple[Any, ...]:
    return (
        row.source_table.encode("utf-8"),
        row.source_key_type,
        _id_sort_key(row.source_key),
    )


def _id_sort_key(value: str) -> tuple[int, Any]:
    try:
        return 0, Decimal(value)
    except (InvalidOperation, ValueError):
        return 1, value.encode("utf-8")


def _bind(column: str) -> str:
    if column in {
        "source_position",
        "source_version",
        "source_version_vector",
        "penalty_source_keys",
    }:
        return f"CAST(:{column} AS jsonb)"
    return f":{column}"


def _json_dump(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


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
            raise DtsV2CourseDomainProjectorError(
                "DTS_V2_COURSE_DOMAIN_NAIVE_DATETIME_FORBIDDEN"
            )
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


__all__ = [
    "DtsV2CourseDomainProjector",
    "DtsV2CourseDomainProjectorError",
]
