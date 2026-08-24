"""Regional TEACHER dirty-key projection for DTS v2.

The aggregate is a deterministic input bundle, not a counter delta.  The
SourceWide worker rebuilds the global teacher row from these regional bundles
and therefore never guesses whether an absent source set means zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from .dts_v2_dirty_queue_store import DirtyClaimV2
from .dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from .dts_v2_source_repository import (
    DtsV2CurrentSourceRow,
    DtsV2SourceRepository,
)


_REGIONS = frozenset({"dom", "ovs"})
_DOM_AUX_TABLES = (
    "dom_teacher_certification",
    "dom_teacher_class_schedule",
)
_REGIONAL_SCOPE_SUFFIXES = (
    "appoint",
    "user_teacher_grading",
    "complaint",
    "teacher_favorite",
    "teacher_blacklist",
)


class DtsV2TeacherDomainProjectorError(RuntimeError):
    """A teacher dirty key cannot be rebuilt without typed evidence."""


def _read_claim_trigger_evidence(*args: Any, **kwargs: Any) -> Any:
    """Lazy compatibility bridge that keeps COURSE/TEACHER imports acyclic."""

    from .dts_v2_course_domain_projector import (
        _read_claim_trigger_evidence as read_evidence,
    )

    return read_evidence(*args, **kwargs)


class DtsV2TeacherDomainProjector:
    """Publish one complete regional input bundle for a teacher."""

    def __init__(
        self,
        *,
        cutover_coverage_identity: Mapping[str, Any],
        source_repository: DtsV2SourceRepository | None = None,
        revision_store: DtsV2DomainRevisionStore | None = None,
    ) -> None:
        if (
            not isinstance(cutover_coverage_identity, Mapping)
            or not cutover_coverage_identity
            or "trigger" in cutover_coverage_identity
        ):
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_COVERAGE_IDENTITY_REQUIRED"
            )
        self.coverage_identity = dict(cutover_coverage_identity)
        self.sources = source_repository or DtsV2SourceRepository()
        self.revisions = revision_store or DtsV2DomainRevisionStore()

    def process_claim(
        self,
        connection: Any,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        if claim.key.key_type != "TEACHER" or claim.key.key_part_2 != "":
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_DIRTY_KEY_REQUIRED"
            )
        region = claim.key.source_region
        teacher_id = claim.key.key_part_1
        if region not in _REGIONS or not teacher_id:
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_IDENTITY_INVALID"
            )

        evidence = _read_claim_trigger_evidence(
            connection,
            claim,
            base_coverage_identity=self.coverage_identity,
        )
        teacher_targets: set[tuple[str, str]] = {(region, teacher_id)}
        course_events = 0
        teacher_region_changes = 0
        if region == "dom" and _is_dom_teacher_profile_trigger(
            evidence.coverage_identity,
            teacher_id=teacher_id,
        ):
            # Profile ``course`` is the sole authority for a teacher's
            # expected source region.  Recheck every retained participation
            # before publishing TEACHER, so a superseded Outbox event can
            # never materialize the new profile with stale course eligibility.
            from .dts_v2_course_domain_projector import (
                _read_course_aggregate_state,
                reconcile_course_teacher_region_evidence_v2,
            )

            related_courses = _read_teacher_course_identities(
                connection,
                teacher_id=teacher_id,
            )
            for course_region, source_appoint_id in related_courses:
                reconciled = reconcile_course_teacher_region_evidence_v2(
                    connection,
                    source_region=course_region,
                    source_appoint_id=source_appoint_id,
                    source_repository=self.sources,
                )
                teacher_region_changes += int(
                    reconciled.course_changed
                ) + len(reconciled.changed_participation_seqs)
                teacher_targets.update(
                    (course_region, affected_teacher_id)
                    for affected_teacher_id in reconciled.affected_teacher_ids
                )
                if not (
                    reconciled.course_changed
                    or reconciled.changed_participation_seqs
                ):
                    continue
                course_state = _read_course_aggregate_state(
                    connection,
                    source_region=course_region,
                    source_appoint_id=source_appoint_id,
                )
                if course_state is None:
                    raise DtsV2TeacherDomainProjectorError(
                        "DTS_V2_TEACHER_REGION_COURSE_STATE_MISSING"
                    )
                course_result = self.revisions.publish_change(
                    connection,
                    aggregate_type="COURSE",
                    aggregate_key={
                        "source_region": course_region,
                        "source_appoint_id": source_appoint_id,
                    },
                    aggregate_state=course_state,
                    changed_fields=("teacher_region_evidence",),
                    source_row_revision=evidence.source_row_revision,
                    source_position=evidence.source_position,
                    rule_version="dts-course-domain-v1",
                    cutover_coverage_identity=evidence.coverage_identity,
                )
                course_events += int(course_result.status == "CHANGED")
            # DOM owns the profile, but both regional TEACHER aggregates are
            # mandatory inputs to the global materializer even with no OVS
            # business rows.
            teacher_targets.update({("dom", teacher_id), ("ovs", teacher_id)})

        teacher_events = 0
        for target_region, target_teacher_id in sorted(teacher_targets):
            result = publish_regional_teacher_aggregate_v2(
                connection,
                source_region=target_region,
                teacher_id=target_teacher_id,
                source_row_revision=evidence.source_row_revision,
                source_position=evidence.source_position,
                cutover_coverage_identity=evidence.coverage_identity,
                source_repository=self.sources,
                revision_store=self.revisions,
            )
            teacher_events += int(result.status == "CHANGED")
        return {
            "aggregate_events": course_events + teacher_events,
            "course_aggregate_events": course_events,
            "teacher_aggregate_events": teacher_events,
            "teacher_region_changes": teacher_region_changes,
        }


def _is_dom_teacher_profile_trigger(
    coverage_identity: Mapping[str, Any],
    *,
    teacher_id: str,
) -> bool:
    trigger = coverage_identity.get("trigger")
    if not isinstance(trigger, Mapping):
        return False
    identity = trigger.get("input_identity")
    if not isinstance(identity, Mapping):
        return False
    if (
        identity.get("source_region") != "dom"
        or identity.get("source_table") != "dom_teacher"
    ):
        return False
    kind = trigger.get("input_kind")
    if kind == "SOURCE_REVISION":
        if identity.get("source_key") != teacher_id:
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_PROFILE_TRIGGER_IDENTITY_CONFLICT"
            )
        return True
    if kind == "SCOPE_REVISION":
        scope_level = identity.get("scope_level")
        scope_key = identity.get("scope_key")
        if scope_level == "TEACHER" and scope_key != teacher_id:
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_PROFILE_TRIGGER_IDENTITY_CONFLICT"
            )
        return scope_level in {"TEACHER", "GLOBAL"}
    return False


def _read_teacher_course_identities(
    connection: Any,
    *,
    teacher_id: str,
) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT source_region,source_appoint_id
            FROM public.source_course_participations
            WHERE teacher_id=:teacher_id
            ORDER BY source_region,convert_to(source_appoint_id,'UTF8')
            """
        ),
        {"teacher_id": teacher_id},
    ).mappings()
    return tuple(
        (str(row["source_region"]), str(row["source_appoint_id"]))
        for row in rows
    )


def publish_regional_teacher_aggregate_v2(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
    source_row_revision: int | None,
    source_position: Mapping[str, Any] | None,
    cutover_coverage_identity: Mapping[str, Any],
    source_repository: DtsV2SourceRepository,
    revision_store: DtsV2DomainRevisionStore,
) -> Any:
    """Rebuild and publish one regional TEACHER state in the caller tx.

    COURSE and TEACHER_STUDENT projectors call this after updating their owned
    normalized facts.  That keeps TEACHER revisions causally closed without
    letting the Outbox runtime mutate domain aggregates or guessing a teacher
    from a child source row that has no teacher column.
    """

    state = build_regional_teacher_aggregate_state_v2(
        connection,
        source_region=source_region,
        teacher_id=teacher_id,
        source_repository=source_repository,
    )
    return revision_store.publish_change(
        connection,
        aggregate_type="TEACHER",
        aggregate_key={
            "source_region": source_region,
            "teacher_id": teacher_id,
        },
        aggregate_state=state,
        changed_fields=(
            "profile",
            "certifications",
            "schedules",
            "participations",
            "relationships",
            "history",
            "scope_evidence",
        ),
        source_row_revision=source_row_revision,
        source_position=source_position,
        rule_version="dts-teacher-domain-v1",
        cutover_coverage_identity=cutover_coverage_identity,
    )


def build_regional_teacher_aggregate_state_v2(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
    source_repository: DtsV2SourceRepository,
) -> Mapping[str, Any]:
    if source_region not in _REGIONS or not teacher_id:
        raise DtsV2TeacherDomainProjectorError(
            "DTS_V2_TEACHER_IDENTITY_INVALID"
        )
    profile, auxiliary = _read_teacher_sources(
        connection,
        source_region=source_region,
        teacher_id=teacher_id,
        source_repository=source_repository,
    )
    scope_evidence = _read_teacher_scope_evidence(
        connection,
        source_region=source_region,
        teacher_id=teacher_id,
    )
    return _json_safe(
        {
            "protocol_version": "teacher-domain-v1",
            "profile": None if profile is None else _source_snapshot(profile),
            "profile_evidence_status": _profile_evidence(
                source_region=source_region,
                profile=profile,
                scope_evidence=scope_evidence,
            ),
            "profile_reference": {
                "source_region": "dom",
                "teacher_id": teacher_id,
            },
            "certifications": [
                _source_snapshot(row)
                for row in auxiliary
                if row.source_table == "dom_teacher_certification"
            ],
            "schedules": [
                _source_snapshot(row)
                for row in auxiliary
                if row.source_table == "dom_teacher_class_schedule"
            ],
            "participations": _read_teacher_participations(
                connection,
                source_region=source_region,
                teacher_id=teacher_id,
            ),
            "relationships": _read_teacher_relationships(
                connection,
                source_region=source_region,
                teacher_id=teacher_id,
            ),
            "history": _read_teacher_history_evidence(
                connection,
                source_region=source_region,
                teacher_id=teacher_id,
            ),
            "scope_evidence": scope_evidence,
        }
    )


def _read_teacher_sources(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
    source_repository: DtsV2SourceRepository,
) -> tuple[DtsV2CurrentSourceRow | None, tuple[DtsV2CurrentSourceRow, ...]]:
    if source_region != "dom":
        return None, ()
    profile_rows = source_repository.read_by_source_keys(
        connection,
        source_region="dom",
        source_table="dom_teacher",
        source_keys=(teacher_id,),
    )
    if len(profile_rows) > 1:
        raise DtsV2TeacherDomainProjectorError(
            "DTS_V2_TEACHER_PROFILE_CURRENT_CONFLICT"
        )
    profile = profile_rows[0] if profile_rows else None
    auxiliary = source_repository.read_for_dependency(
        connection,
        source_region="dom",
        source_tables=_DOM_AUX_TABLES,
        dependency_kind="teacher_ids",
        dependency_value=teacher_id,
    )
    return profile, tuple(auxiliary)


def _profile_evidence(
    *,
    source_region: str,
    profile: DtsV2CurrentSourceRow | None,
    scope_evidence: Sequence[Mapping[str, Any]],
) -> str:
    if source_region != "dom":
        return "CROSS_REGION_REFERENCE"
    if profile is not None:
        return "CONFIRMED_TOMBSTONE" if profile.is_deleted else "CONFIRMED"
    for row in scope_evidence:
        if (
            row.get("source_table") == "dom_teacher"
            and row.get("scope_kind") == "CURRENT"
            and row.get("state") == "COMPLETE"
        ):
            return "CONFIRMED_EMPTY"
    return "SOURCE_MISSING"


def _source_snapshot(source: DtsV2CurrentSourceRow) -> Mapping[str, Any]:
    return {
        "source_table": source.source_table,
        "source_key": source.source_key,
        "source_key_type": source.source_key_type,
        "source_row_revision": source.source_row_revision,
        "source_payload_hash": source.source_payload_hash,
        "source_deleted": source.is_deleted,
        "values": dict(source.source_row),
        "field_types": dict(source.source_field_types),
    }


def _teacher_scope_tables(source_region: str) -> tuple[str, ...]:
    tables = [f"{source_region}_{suffix}" for suffix in _REGIONAL_SCOPE_SUFFIXES]
    if source_region == "dom":
        tables.extend(
            (
                "dom_teacher",
                "dom_teacher_absent_reason",
                "dom_teacher_penalty",
                "dom_teacher_certification",
                "dom_teacher_class_schedule",
            )
        )
    return tuple(sorted(set(tables)))


def _read_teacher_scope_evidence(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
) -> list[Mapping[str, Any]]:
    tables = _teacher_scope_tables(source_region)
    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (state.source_table,state.scope_kind)
                   state.source_table,state.scope_kind,state.scope_level,
                   state.scope_key,state.state,state.row_version,
                   state.active_snapshot_id,snapshot.snapshot_fence_hash,
                   snapshot.history_from,snapshot.history_through
            FROM public.dts_source_scope_states state
            LEFT JOIN public.dts_source_scope_snapshots snapshot
              ON snapshot.snapshot_id=state.active_snapshot_id
             AND snapshot.source_region=state.source_region
             AND snapshot.source_table=state.source_table
             AND snapshot.scope_kind=state.scope_kind
             AND snapshot.scope_level=state.scope_level
             AND snapshot.scope_key=state.scope_key
            WHERE state.source_region=:source_region
              AND state.source_table=ANY(CAST(:source_tables AS text[]))
              AND state.scope_kind IN ('CURRENT','HISTORY')
              AND ((state.scope_level='TEACHER'
                    AND state.scope_key=:teacher_id)
                   OR (state.scope_level='GLOBAL' AND state.scope_key='*'))
            ORDER BY state.source_table,state.scope_kind,
                     CASE state.scope_level WHEN 'TEACHER' THEN 0 ELSE 1 END
            FOR SHARE OF state
            """
        ),
        {
            "source_region": source_region,
            "source_tables": list(tables),
            "teacher_id": teacher_id,
        },
    ).mappings()
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row["source_table"]), str(row["scope_kind"]))
        # SQL orders TEACHER before GLOBAL; preserve the most specific scope.
        by_key.setdefault(key, dict(row))
    for table in tables:
        for kind in ("CURRENT", "HISTORY"):
            by_key.setdefault(
                (table, kind),
                {
                    "source_table": table,
                    "scope_kind": kind,
                    "scope_level": None,
                    "scope_key": None,
                    "state": "UNKNOWN",
                    "row_version": None,
                    "active_snapshot_id": None,
                    "snapshot_fence_hash": None,
                    "history_from": None,
                    "history_through": None,
                },
            )
    return [by_key[key] for key in sorted(by_key)]


def _read_teacher_participations(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
) -> list[Mapping[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT p.source_appoint_id,p.participation_seq,p.teacher_id,
                   p.teacher_id_type,
                   p.participation_status,p.participation_role,p.is_current,
                   p.assigned_at,p.assigned_at_evidence_status,p.ended_at,
                   p.absence_reason_detail,p.no_notice,
                   p.absence_source_id,p.absence_source_id_type,
                   p.absence_source_row_revision,
                   p.absence_selected_reason_type,
                   CASE
                     WHEN p.participation_status='t_absent'
                          AND p.no_notice IS NULL THEN 'SOURCE_MISSING'
                     ELSE 'CONFIRMED'
                   END AS absence_evidence_status,
                   p.teacher_expected_source_region,
                   p.teacher_region_evidence_status,
                   p.teacher_profile_source_row_revision,
                   p.teacher_profile_source_payload_hash,p.source_deleted,
                   p.assignment_source_row_revision,p.row_version,
                   c.student_token,c.lesson_local_date,c.lesson_local_time,
                   c.scheduled_start_at,c.end_time,c.source_status,c.is_peak,
                   c.completion_participation_seq,c.completion_teacher_id,
                   c.completion_end_time,c.completion_student_token,
                   c.completion_is_peak,c.completion_lesson_local_date,
                   c.initial_completion_snapshot,
                   c.completion_conflict_status,c.source_is_deleted,
                   c.evidence_status AS course_evidence_status,
                   c.appoint_evidence_status AS appoint_evidence_status,
                   c.teacher_region_evidence_status AS
                     course_teacher_region_evidence_status,
                   c.row_version AS course_row_version,
                   pf.is_late,pf.late_evidence_status,pf.is_early,
                   pf.early_evidence_status,pf.penalty_source_keys,
                   cf.grading_classification,cf.grading_evidence_status,
                   cf.has_complaint,cf.has_valid_complaint,
                   cf.complaint_evidence_status,cf.is_camera_off,
                   cf.camera_evidence_status
            FROM public.source_course_participations p
            JOIN public.source_courses c
              ON c.source_region=p.source_region
             AND c.source_appoint_id=p.source_appoint_id
            LEFT JOIN public.source_participation_fact_current pf
              ON pf.source_region=p.source_region
             AND pf.source_appoint_id=p.source_appoint_id
             AND pf.participation_seq=p.participation_seq
            LEFT JOIN public.source_course_fact_current cf
              ON cf.source_region=p.source_region
             AND cf.source_appoint_id=p.source_appoint_id
            WHERE p.source_region=:source_region AND p.teacher_id=:teacher_id
            ORDER BY convert_to(p.source_appoint_id,'UTF8'),p.participation_seq
            FOR SHARE OF p,c
            """
        ),
        {"source_region": source_region, "teacher_id": teacher_id},
    ).mappings()
    return [dict(row) for row in rows]


def _read_teacher_relationships(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
) -> list[Mapping[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT relationship.teacher_id_type,relationship.student_token,
                   relationship.is_favorited,relationship.is_blocked,
                   evidence.aggregate_state #>>
                     '{relationship_current,favorite_evidence_status}'
                     AS favorite_evidence_status,
                   evidence.aggregate_state #>>
                     '{relationship_current,block_evidence_status}'
                     AS block_evidence_status,
                   relationship.last_business_effective_at,
                   relationship.effective_time_evidence_status,
                   relationship.last_event_sequence,
                   relationship.last_source_row_revision,
                   relationship.row_version
            FROM public.teacher_student_relationship_current relationship
            LEFT JOIN public.domain_aggregate_revisions evidence
              ON evidence.aggregate_type='TEACHER_STUDENT'
             AND evidence.canonical_key=jsonb_build_object(
                   'source_region',relationship.source_region,
                   'teacher_id',relationship.teacher_id,
                   'student_token',relationship.student_token
                 )
            WHERE relationship.source_region=:source_region
              AND relationship.teacher_id=:teacher_id
            ORDER BY convert_to(relationship.student_token,'UTF8')
            FOR SHARE OF relationship
            """
        ),
        {"source_region": source_region, "teacher_id": teacher_id},
    ).mappings()
    return [dict(row) for row in rows]


def _read_teacher_history_evidence(
    connection: Any,
    *,
    source_region: str,
    teacher_id: str,
) -> Mapping[str, list[Mapping[str, Any]]]:
    """Return append-only first-date candidates with typed field evidence.

    Both images are retained because a teacher substitution or a slot close
    can leave the only evidence of an earlier assignment/open state in the
    ``before`` image.  Student identifiers and unrelated source fields are not
    copied into the teacher aggregate.
    """

    appoint_rows = connection.execute(
        text(
            """
            SELECT source_table,source_key,source_key_type,
                   source_row_revision,source_field_types,
                   CASE WHEN before_row->>'t_id'=:teacher_id THEN
                     jsonb_build_object(
                       't_id',before_row->'t_id','status',before_row->'status',
                       'date',before_row->'date','end_time',before_row->'end_time'
                     ) END AS before_image,
                   CASE WHEN after_row->>'t_id'=:teacher_id THEN
                     jsonb_build_object(
                       't_id',after_row->'t_id','status',after_row->'status',
                       'date',after_row->'date','end_time',after_row->'end_time'
                     ) END AS after_image
            FROM public.dts_source_row_versions
            WHERE source_region=:source_region
              AND source_table=:source_table
              AND (before_row->>'t_id'=:teacher_id
                   OR after_row->>'t_id'=:teacher_id)
            ORDER BY convert_to(source_key,'UTF8'),
                     source_row_revision NULLS FIRST,
                     convert_to(source_partition_epoch_id,'UTF8'),
                     convert_to(topic,'UTF8'),partition_id,offset_value
            FOR SHARE
            """
        ),
        {
            "source_region": source_region,
            "source_table": f"{source_region}_appoint",
            "teacher_id": teacher_id,
        },
    ).mappings()
    schedule_rows: Sequence[Mapping[str, Any]] = ()
    if source_region == "dom":
        schedule_rows = connection.execute(
            text(
                """
                SELECT source_table,source_key,source_key_type,
                       source_row_revision,source_field_types,
                       CASE WHEN before_row->>'teacher_id'=:teacher_id THEN
                         jsonb_build_object(
                           'teacher_id',before_row->'teacher_id',
                           'status',before_row->'status',
                           'date',before_row->'date'
                         ) END AS before_image,
                       CASE WHEN after_row->>'teacher_id'=:teacher_id THEN
                         jsonb_build_object(
                           'teacher_id',after_row->'teacher_id',
                           'status',after_row->'status',
                           'date',after_row->'date'
                         ) END AS after_image
                FROM public.dts_source_row_versions
                WHERE source_region='dom'
                  AND source_table='dom_teacher_class_schedule'
                  AND (before_row->>'teacher_id'=:teacher_id
                       OR after_row->>'teacher_id'=:teacher_id)
                ORDER BY convert_to(source_key,'UTF8'),
                         source_row_revision NULLS FIRST,
                         convert_to(source_partition_epoch_id,'UTF8'),
                         convert_to(topic,'UTF8'),partition_id,offset_value
                FOR SHARE
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings()
    return {
        "appoint_versions": [dict(row) for row in appoint_rows],
        "schedule_versions": [dict(row) for row in schedule_rows],
    }


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
            raise DtsV2TeacherDomainProjectorError(
                "DTS_V2_TEACHER_NAIVE_DATETIME_FORBIDDEN"
            )
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


__all__ = [
    "build_regional_teacher_aggregate_state_v2",
    "DtsV2TeacherDomainProjector",
    "DtsV2TeacherDomainProjectorError",
    "publish_regional_teacher_aggregate_v2",
]
