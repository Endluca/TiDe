"""Global TEACHER Outbox reducer over the locked DOM and OVS aggregates.

TEACHER domain aggregates are deliberately regional.  A teacher-wide serving
row is global, so consuming either regional event must merge both regional
current aggregates.  A missing authoritative DOM teacher baseline or a peer
aggregate that has not arrived yet is settled as an explicit no-op; the later
baseline/peer revision emits a fresh event.  Malformed existing aggregates
still fail closed and are never treated as an empty data set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_direct_projector import schedule_slot_is_peak
from .dts_teacher_aggregate_v2 import (
    DtsTeacherAggregateV2Error,
    TeacherAggregateCoverageV2,
    TeacherFirstDateEvidenceV2,
    TeacherParticipationMetricV2,
    TeacherProfileFactV2,
    TeacherRelationshipMetricV2,
    TeacherScheduleMetricV2,
    TeacherSourceWideProjectionV2,
    rebuild_teacher_source_wide_v2,
)
from .dts_v2_domain_aggregate import (
    DtsV2DomainAggregateError,
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)
from .dts_v2_outbox_worker import DtsV2OutboxEvent


_REGIONS = ("dom", "ovs")
_EVIDENCE = frozenset(
    {"CONFIRMED", "CONFIRMED_EMPTY", "LEGACY_FROZEN", "SOURCE_MISSING"}
)


class DtsV2TeacherOutboxProcessorError(RuntimeError):
    """The global teacher snapshot cannot be proved from regional inputs."""


class _DtsV2TeacherBaselineMissing(DtsV2TeacherOutboxProcessorError):
    """No authoritative ``dom_teacher`` baseline exists for this teacher."""


class _DtsV2TeacherRegionalDependencyMissing(DtsV2TeacherOutboxProcessorError):
    """An authoritative teacher exists but its regional pair is not ready."""


@dataclass(frozen=True)
class RegionalTeacherAggregateV2:
    source_region: str
    aggregate_id: str
    aggregate_state: Mapping[str, Any]
    aggregate_state_sha256: str
    current_revision: int


@dataclass(frozen=True)
class TeacherAggregateBundleV2:
    teacher_id: str
    regions: Mapping[str, RegionalTeacherAggregateV2]
    triggering_region: str
    event_revision: int
    is_superseded_event: bool


@dataclass(frozen=True)
class TeacherAggregateCurrentBundleV2:
    teacher_id: str
    regions: Mapping[str, RegionalTeacherAggregateV2]


@dataclass(frozen=True)
class TeacherMaterializationPlanV2:
    teacher_id: str
    teacher_id_type: str
    business_date_beijing: date
    projection: TeacherSourceWideProjectionV2
    regional_revisions: Mapping[str, int]
    regional_state_sha256: Mapping[str, str]


class DtsV2TeacherMaterializer(Protocol):
    def apply_teacher_plan(
        self,
        connection: Connection,
        plan: TeacherMaterializationPlanV2,
        *,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class DtsV2TeacherAggregateBundleReader:
    """Read both regional current rows in one deterministic statement.

    The runtime role intentionally has read-only aggregate access.  The
    protected materializer subsequently locks and revalidates the exact
    revision/hash vector, so a concurrent aggregate advance fails closed
    without requiring direct UPDATE privilege merely for ``FOR SHARE``.
    """

    def read_current(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> TeacherAggregateBundleV2:
        if not isinstance(event, DtsV2OutboxEvent):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_AGGREGATE_EVENT_REQUIRED"
            )
        key = event.payload.get("aggregate_key")
        event_revision = event.payload.get("aggregate_revision")
        if not isinstance(key, Mapping) or type(event_revision) is not int:
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_AGGREGATE_EVENT_INVALID"
            )
        try:
            triggering = build_domain_aggregate_identity_v2("TEACHER", key)
        except DtsV2DomainAggregateError as exc:
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_AGGREGATE_EVENT_INVALID"
            ) from exc
        if triggering.aggregate_id != event.aggregate_id or event_revision < 1:
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_AGGREGATE_EVENT_INVALID"
            )
        teacher_id = str(triggering.aggregate_key["teacher_id"])
        triggering_region = str(triggering.aggregate_key["source_region"])
        current = self.read_current_for_teacher(connection, teacher_id)
        regions = current.regions
        trigger_revision = regions[triggering_region].current_revision
        if event_revision > trigger_revision:
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_EVENT_REVISION_AHEAD"
            )
        return TeacherAggregateBundleV2(
            teacher_id=teacher_id,
            regions=regions,
            triggering_region=triggering_region,
            event_revision=event_revision,
            is_superseded_event=event_revision < trigger_revision,
        )

    def read_current_for_teacher(
        self,
        connection: Connection,
        teacher_id: str,
    ) -> TeacherAggregateCurrentBundleV2:
        _required_text(teacher_id, "DTS_V2_TEACHER_ID_INVALID")
        identities = {
            region: build_domain_aggregate_identity_v2(
                "TEACHER", {"source_region": region, "teacher_id": teacher_id}
            )
            for region in _REGIONS
        }
        rows = list(
            connection.execute(
                text(
                    """
                    SELECT aggregate_type,aggregate_id,canonical_key,
                           canonical_key_sha256,revision,aggregate_state,
                           aggregate_state_sha256
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='TEACHER'
                      AND aggregate_id=ANY(CAST(:aggregate_ids AS text[]))
                    ORDER BY convert_to(aggregate_id,'UTF8')
                    """
                ),
                {
                    "aggregate_ids": sorted(
                        identity.aggregate_id for identity in identities.values()
                    )
                },
            ).mappings()
        )
        by_id = {str(row.get("aggregate_id")): row for row in rows}
        if len(by_id) != len(rows):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_REGIONAL_AGGREGATE_INVALID"
            )
        regions: dict[str, RegionalTeacherAggregateV2] = {}
        for region, identity in identities.items():
            row = by_id.get(identity.aggregate_id)
            if row is None:
                continue
            if row.get("aggregate_type") != "TEACHER":
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_REGIONAL_AGGREGATE_INVALID"
                )
            revision = row.get("revision")
            state = row.get("aggregate_state")
            if (
                row.get("canonical_key") != dict(identity.aggregate_key)
                or row.get("canonical_key_sha256")
                != identity.aggregate_id.rsplit(":", 1)[-1]
                or type(revision) is not int
                or revision < 1
                or not isinstance(state, Mapping)
            ):
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_REGIONAL_AGGREGATE_INVALID"
                )
            _, state_hash = canonical_domain_state_v2(state)
            if row.get("aggregate_state_sha256") != state_hash:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_REGIONAL_AGGREGATE_HASH_MISMATCH"
                )
            regions[region] = RegionalTeacherAggregateV2(
                source_region=region,
                aggregate_id=identity.aggregate_id,
                aggregate_state=dict(state),
                aggregate_state_sha256=state_hash,
                current_revision=revision,
            )

        dom = regions.get("dom")
        if dom is None:
            raise _DtsV2TeacherBaselineMissing(
                "DTS_V2_TEACHER_BASELINE_MISSING"
            )
        profile_status = dom.aggregate_state.get("profile_evidence_status")
        profile_snapshot = dom.aggregate_state.get("profile")
        if profile_status == "SOURCE_MISSING" and profile_snapshot is None:
            raise _DtsV2TeacherBaselineMissing(
                "DTS_V2_TEACHER_BASELINE_MISSING"
            )
        if (
            profile_status not in {"CONFIRMED", "CONFIRMED_TOMBSTONE"}
            or not isinstance(profile_snapshot, Mapping)
        ):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_PROFILE_EVIDENCE_MISSING"
            )
        if "ovs" not in regions:
            raise _DtsV2TeacherRegionalDependencyMissing(
                "DTS_V2_TEACHER_REGIONAL_DEPENDENCY_MISSING"
            )
        return TeacherAggregateCurrentBundleV2(
            teacher_id=teacher_id,
            regions=regions,
        )


class DtsV2TeacherOutboxProcessor:
    def __init__(
        self,
        *,
        materializer: DtsV2TeacherMaterializer,
        aggregate_reader: DtsV2TeacherAggregateBundleReader | None = None,
    ) -> None:
        self.materializer = materializer
        self.aggregate_reader = (
            aggregate_reader or DtsV2TeacherAggregateBundleReader()
        )

    @staticmethod
    def _read_legacy_first_dates(
        connection: Connection,
        teacher_id: str,
    ) -> Mapping[str, Any] | None:
        # The protected materializer owns the write lock and revalidates the
        # aggregate revision/hash vector before applying the plan.  This
        # compatibility read must therefore remain a plain SELECT: the shared
        # application role intentionally has no UPDATE privilege on
        # teacher_source_wide, while PostgreSQL row-locking SELECTs require it.
        return connection.execute(
            text(
                """
                SELECT first_open_slot_dt,first_booked_dt,first_completed_dt
                FROM public.teacher_source_wide
                WHERE tchr_id=:teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().one_or_none()

    def process_event(
        self,
        connection: Connection,
        event: DtsV2OutboxEvent,
    ) -> Mapping[str, int]:
        if (
            event.aggregate_type != "TEACHER"
            or event.event_type != "source_wide.changed.v2"
        ):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_OUTBOX_EVENT_REQUIRED"
            )
        try:
            bundle = self.aggregate_reader.read_current(connection, event)
        except _DtsV2TeacherBaselineMissing:
            # Auxiliary facts may legitimately arrive before dom_teacher.
            # Publishing this event is an auditable no-op.  A later
            # dom_teacher revision fans out fresh DOM and OVS aggregates and
            # therefore a new Outbox event; this event must not consume the
            # technical retry/dead-letter budget.
            return {"teacher_baseline_missing_skips": 1}
        except _DtsV2TeacherRegionalDependencyMissing:
            # The peer aggregate owns its own revision and Outbox event.  Do
            # not turn normal cross-region ordering into a failure loop: the
            # peer event will trigger materialization once the pair is present.
            return {"teacher_regional_dependency_waits": 1}
        legacy = self._read_legacy_first_dates(
            connection,
            bundle.teacher_id,
        )
        business_date = connection.execute(
            text(
                "SELECT (transaction_timestamp() AT TIME ZONE "
                "'Asia/Shanghai')::date"
            )
        ).scalar_one()
        if not isinstance(business_date, date):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_BUSINESS_DATE_INVALID"
            )
        plan = build_teacher_materialization_plan_v2(
            teacher_id=bundle.teacher_id,
            regional_states={
                region: snapshot.aggregate_state
                for region, snapshot in bundle.regions.items()
            },
            regional_revisions={
                region: snapshot.current_revision
                for region, snapshot in bundle.regions.items()
            },
            regional_state_sha256={
                region: snapshot.aggregate_state_sha256
                for region, snapshot in bundle.regions.items()
            },
            business_date_beijing=business_date,
            legacy_first_dates={} if legacy is None else dict(legacy),
        )
        result = self.materializer.apply_teacher_plan(
            connection,
            plan,
            triggering_event_id=event.event_id,
        )
        counts = {"superseded_events": int(bundle.is_superseded_event)}
        if not isinstance(result, Mapping):
            raise DtsV2TeacherOutboxProcessorError(
                "DTS_V2_TEACHER_MATERIALIZER_RESULT_INVALID"
            )
        for name, value in result.items():
            if not isinstance(name, str) or not name or type(value) is not int or value < 0:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_MATERIALIZER_RESULT_INVALID"
                )
            counts[name] = value
        return counts


def build_teacher_materialization_plan_v2(
    *,
    teacher_id: str,
    regional_states: Mapping[str, Mapping[str, Any]],
    regional_revisions: Mapping[str, int],
    business_date_beijing: date,
    legacy_first_dates: Mapping[str, Any],
    regional_state_sha256: Mapping[str, str] | None = None,
) -> TeacherMaterializationPlanV2:
    """Validate, merge and reduce the two regional teacher snapshots."""

    _required_text(teacher_id, "DTS_V2_TEACHER_ID_INVALID")
    if set(regional_states) != set(_REGIONS) or set(regional_revisions) != set(
        _REGIONS
    ):
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_REGIONAL_BUNDLE_INCOMPLETE"
        )
    if any(type(value) is not int or value < 1 for value in regional_revisions.values()):
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_REGIONAL_REVISION_INVALID"
        )
    states = {region: _state(regional_states[region]) for region in _REGIONS}
    calculated_state_hashes = {
        region: canonical_domain_state_v2(states[region])[1]
        for region in _REGIONS
    }
    if regional_state_sha256 is not None and dict(regional_state_sha256) != calculated_state_hashes:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_REGIONAL_STATE_HASH_MISMATCH"
        )
    dom, ovs = states["dom"], states["ovs"]
    profile_snapshot = dom.get("profile")
    if (
        dom.get("profile_evidence_status")
        not in {"CONFIRMED", "CONFIRMED_TOMBSTONE"}
        or not isinstance(profile_snapshot, Mapping)
        or ovs.get("profile") is not None
        or ovs.get("profile_evidence_status") != "CROSS_REGION_REFERENCE"
        or ovs.get("profile_reference")
        != {"source_region": "dom", "teacher_id": teacher_id}
    ):
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_PROFILE_EVIDENCE_MISSING"
        )
    profile_values, profile_types, profile_id_type = _snapshot_values(
        profile_snapshot,
        table="dom_teacher",
        source_key=teacher_id,
    )
    _assert_typed_teacher(
        profile_values.get("id"),
        profile_types.get("id", profile_id_type),
        teacher_id,
    )
    profile = TeacherProfileFactV2(
        teacher_id=teacher_id,
        real_name=_optional_text(profile_values.get("real_name")),
        center_type=profile_values.get("center_type"),
        is_full_time=profile_values.get("is_full_time"),
        course=profile_values.get("course"),
        employment_status=profile_values.get("status"),
        status_on_time=profile_values.get("status_on_time"),
        status_off_time=profile_values.get("status_off_time"),
        last_on_time=profile_values.get("last_on_time"),
        source_deleted=bool(profile_snapshot.get("source_deleted")),
    )
    onboard_date = _date_value(profile.status_on_time)
    coverage = _coverage(states, onboard_date, business_date_beijing)
    teacher_types = {profile_id_type}
    participations: list[TeacherParticipationMetricV2] = []
    relationships: list[TeacherRelationshipMetricV2] = []
    schedules: list[TeacherScheduleMetricV2] = []
    for region in _REGIONS:
        region_state = states[region]
        for raw in _sequence(region_state.get("participations"), "PARTICIPATIONS"):
            row = _mapping(raw, "PARTICIPATION")
            typed = _required_text(
                row.get("teacher_id_type"),
                "DTS_V2_TEACHER_PARTICIPATION_TYPE_MISSING",
            )
            _assert_typed_teacher(row.get("teacher_id"), typed, teacher_id)
            teacher_types.add(typed)
            course_evidence = row.get("course_evidence_status")
            if course_evidence == "SOURCE_CONFLICT" or row.get(
                "completion_conflict_status"
            ) == "PENDING":
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_COURSE_CONFLICT"
                )
            if course_evidence != "CONFIRMED":
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_COURSE_EVIDENCE_MISSING"
                )
            role = _required_text(
                row.get("participation_role"),
                "DTS_V2_TEACHER_PARTICIPATION_ROLE_INVALID",
            )
            completion = role == "COMPLETION"
            late_status = row.get("late_evidence_status")
            early_status = row.get("early_evidence_status")
            late = row.get("is_late")
            early = row.get("is_early")
            absent = row.get("participation_status") == "t_absent"
            grading = row.get("grading_classification")
            grading_status = row.get("grading_evidence_status")
            if region == "dom" and coverage.grading_current_complete:
                if grading not in {"POSITIVE", "NEGATIVE", "UNCLASSIFIED"}:
                    grading = "UNCLASSIFIED"
                grading_status = "CONFIRMED"
            complaint = row.get("has_complaint")
            valid_complaint = row.get("has_valid_complaint")
            complaint_status = row.get("complaint_evidence_status")
            if coverage.complaint_current_complete:
                complaint = bool(complaint)
                valid_complaint = bool(valid_complaint)
                complaint_status = "CONFIRMED"
            participations.append(
                TeacherParticipationMetricV2(
                    source_region=region,
                    source_appoint_id=_required_text(
                        row.get("source_appoint_id"),
                        "DTS_V2_TEACHER_APPOINT_ID_INVALID",
                    ),
                    participation_seq=_positive_int(row.get("participation_seq")),
                    teacher_id=teacher_id,
                    participation_role=role,
                    participation_status=_optional_text(
                        row.get("participation_status")
                    ),
                    source_deleted=bool(row.get("source_deleted")),
                    is_peak=_optional_bool(
                        row.get("completion_is_peak")
                        if completion
                        else row.get("is_peak")
                    ),
                    lesson_local_date=_date_value(
                        row.get("completion_lesson_local_date")
                        if completion
                        else row.get("lesson_local_date")
                    ),
                    completion_student_token=(
                        _optional_text(row.get("completion_student_token"))
                        if completion
                        else None
                    ),
                    is_late=_optional_bool(late),
                    late_evidence_status=str(late_status or "SOURCE_MISSING"),
                    is_early=_optional_bool(early),
                    early_evidence_status=str(early_status or "SOURCE_MISSING"),
                    is_no_notice=(
                        _optional_bool(row.get("no_notice")) if absent else False
                    ),
                    absence_evidence_status=(
                        str(row.get("absence_evidence_status") or "SOURCE_MISSING")
                        if absent
                        else "CONFIRMED"
                    ),
                    grading_classification=str(grading or "SOURCE_MISSING"),
                    grading_evidence_status=str(
                        grading_status or "SOURCE_MISSING"
                    ),
                    has_complaint=_optional_bool(complaint),
                    has_valid_complaint=_optional_bool(valid_complaint),
                    complaint_evidence_status=str(
                        complaint_status or "SOURCE_MISSING"
                    ),
                )
            )
        for raw in _sequence(region_state.get("relationships"), "RELATIONSHIPS"):
            row = _mapping(raw, "RELATIONSHIP")
            typed = _required_text(
                row.get("teacher_id_type"),
                "DTS_V2_TEACHER_RELATION_TYPE_MISSING",
            )
            teacher_types.add(typed)
            token = _required_text(
                row.get("student_token"),
                "DTS_V2_TEACHER_RELATION_TOKEN_INVALID",
            )
            relationships.append(
                TeacherRelationshipMetricV2(
                    source_region=region,
                    teacher_id=teacher_id,
                    student_token=token,
                    is_favorited=_optional_bool(row.get("is_favorited")),
                    favorite_evidence_status=str(
                        row.get("favorite_evidence_status") or "SOURCE_MISSING"
                    ),
                    is_blocked=_optional_bool(row.get("is_blocked")),
                    block_evidence_status=str(
                        row.get("block_evidence_status") or "SOURCE_MISSING"
                    ),
                )
            )
    teacher_area = _teacher_area(profile.course)
    for raw in _sequence(dom.get("schedules"), "SCHEDULES"):
        snapshot = _mapping(raw, "SCHEDULE")
        values, types, _ = _snapshot_values(
            snapshot,
            table="dom_teacher_class_schedule",
        )
        if values.get("teacher_id") is not None:
            typed = _required_text(
                types.get("teacher_id"),
                "DTS_V2_TEACHER_SCHEDULE_TYPE_MISSING",
            )
            _assert_typed_teacher(values.get("teacher_id"), typed, teacher_id)
            teacher_types.add(typed)
        schedule_date = _date_value(values.get("date"))
        time_slot = _integer_or_none(values.get("time_slot"))
        schedules.append(
            TeacherScheduleMetricV2(
                source_region="dom",
                source_schedule_id=_required_text(
                    snapshot.get("source_key"),
                    "DTS_V2_TEACHER_SCHEDULE_ID_INVALID",
                ),
                teacher_id=teacher_id,
                schedule_date=schedule_date,
                is_current_open=(
                    not bool(snapshot.get("source_deleted"))
                    and str(values.get("status") or "").casefold() == "on"
                ),
                is_regular=str(values.get("project_code") or "") == "1v1",
                is_peak=schedule_slot_is_peak(
                    teacher_area, schedule_date, time_slot
                ),
            )
        )
    if len(teacher_types) != 1:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_ID_TYPE_CONFLICT"
        )
    teacher_id_type = next(iter(teacher_types))
    first_dates = _first_dates(
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        states=states,
        participations=participations,
        legacy=legacy_first_dates,
    )
    is_cpl_tesol = _tesol_state(
        dom,
        coverage,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
    )
    try:
        projection = rebuild_teacher_source_wide_v2(
            profile=profile,
            participations=participations,
            relationships=relationships,
            schedules=schedules,
            coverage=coverage,
            first_dates=first_dates,
            business_date_beijing=business_date_beijing,
            is_cpl_tesol=is_cpl_tesol,
        )
    except DtsTeacherAggregateV2Error as exc:
        raise DtsV2TeacherOutboxProcessorError(str(exc)) from exc
    if any(value not in _EVIDENCE for value in projection.first_date_evidence.values()):
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_FIRST_DATE_EVIDENCE_INVALID"
        )
    return TeacherMaterializationPlanV2(
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        business_date_beijing=business_date_beijing,
        projection=projection,
        regional_revisions=dict(regional_revisions),
        regional_state_sha256=calculated_state_hashes,
    )


def _state(value: Any) -> Mapping[str, Any]:
    state = _mapping(value, "REGIONAL_STATE")
    if state.get("protocol_version") != "teacher-domain-v1":
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_PROTOCOL_VERSION_INVALID"
        )
    return state


def _coverage(
    states: Mapping[str, Mapping[str, Any]],
    onboard_date: date | None,
    business_date: date,
) -> TeacherAggregateCoverageV2:
    def current(region: str, suffix: str) -> bool:
        return _scope_complete(states[region], f"{region}_{suffix}", "CURRENT")

    def history(region: str, suffix: str) -> bool:
        return _scope_history_complete(
            states[region],
            f"{region}_{suffix}",
            onboard_date=onboard_date,
            business_date=business_date,
        )

    return TeacherAggregateCoverageV2(
        course_current_complete=all(current(region, "appoint") for region in _REGIONS),
        grading_current_complete=current("dom", "user_teacher_grading"),
        complaint_current_complete=all(current(region, "complaint") for region in _REGIONS),
        penalty_current_complete=current("dom", "teacher_penalty"),
        absence_current_complete=current("dom", "teacher_absent_reason"),
        relationship_current_complete=all(
            current(region, suffix)
            for region in _REGIONS
            for suffix in ("teacher_favorite", "teacher_blacklist")
        ),
        schedule_current_complete=current("dom", "teacher_class_schedule"),
        booked_history_complete=all(history(region, "appoint") for region in _REGIONS),
        completion_history_complete=all(history(region, "appoint") for region in _REGIONS),
        schedule_history_complete=history("dom", "teacher_class_schedule"),
    )


def _scope_complete(state: Mapping[str, Any], table: str, kind: str) -> bool:
    matches = [
        row
        for row in _sequence(state.get("scope_evidence"), "SCOPE")
        if isinstance(row, Mapping)
        and row.get("source_table") == table
        and row.get("scope_kind") == kind
    ]
    return len(matches) == 1 and matches[0].get("state") == "COMPLETE"


def _scope_history_complete(
    state: Mapping[str, Any],
    table: str,
    *,
    onboard_date: date | None,
    business_date: date,
) -> bool:
    if onboard_date is None:
        return False
    matches = [
        row
        for row in _sequence(state.get("scope_evidence"), "SCOPE")
        if isinstance(row, Mapping)
        and row.get("source_table") == table
        and row.get("scope_kind") == "HISTORY"
    ]
    if len(matches) != 1 or matches[0].get("state") != "COMPLETE":
        return False
    history_from = _date_value(matches[0].get("history_from"))
    history_through = _date_value(matches[0].get("history_through"))
    return (
        history_from is not None
        and history_through is not None
        and history_from <= onboard_date
        and history_through >= business_date
    )


def _first_dates(
    *,
    teacher_id: str,
    teacher_id_type: str,
    states: Mapping[str, Mapping[str, Any]],
    participations: Sequence[TeacherParticipationMetricV2],
    legacy: Mapping[str, Any],
) -> TeacherFirstDateEvidenceV2:
    booked: set[date] = set()
    opened: set[date] = set()
    completed: set[date] = set()
    for region in _REGIONS:
        history = _mapping(states[region].get("history"), "HISTORY")
        for raw in _sequence(history.get("appoint_versions"), "APPOINT_HISTORY"):
            row = _mapping(raw, "APPOINT_HISTORY_ROW")
            types = _mapping(row.get("source_field_types"), "FIELD_TYPES")
            for image_name in ("before_image", "after_image"):
                image = row.get(image_name)
                if image is None:
                    continue
                values = _mapping(image, "APPOINT_HISTORY_IMAGE")
                if types.get("t_id") != teacher_id_type:
                    raise DtsV2TeacherOutboxProcessorError(
                        "DTS_V2_TEACHER_ID_TYPE_CONFLICT"
                    )
                _assert_typed_teacher(values.get("t_id"), types.get("t_id"), teacher_id)
                value = _date_value(values.get("date"))
                if value is None:
                    raise DtsV2TeacherOutboxProcessorError(
                        "DTS_V2_TEACHER_BOOKED_DATE_EVIDENCE_INVALID"
                    )
                booked.add(value)
        if region == "dom":
            for raw in _sequence(history.get("schedule_versions"), "SCHEDULE_HISTORY"):
                row = _mapping(raw, "SCHEDULE_HISTORY_ROW")
                types = _mapping(row.get("source_field_types"), "FIELD_TYPES")
                for image_name in ("before_image", "after_image"):
                    image = row.get(image_name)
                    if image is None:
                        continue
                    values = _mapping(image, "SCHEDULE_HISTORY_IMAGE")
                    if types.get("teacher_id") != teacher_id_type:
                        raise DtsV2TeacherOutboxProcessorError(
                            "DTS_V2_TEACHER_ID_TYPE_CONFLICT"
                        )
                    _assert_typed_teacher(
                        values.get("teacher_id"), types.get("teacher_id"), teacher_id
                    )
                    if str(values.get("status") or "").casefold() != "on":
                        continue
                    value = _date_value(values.get("date"))
                    if value is None:
                        raise DtsV2TeacherOutboxProcessorError(
                            "DTS_V2_TEACHER_OPEN_DATE_EVIDENCE_INVALID"
                        )
                    opened.add(value)
        for raw in _sequence(states[region].get("participations"), "PARTICIPATIONS"):
            row = _mapping(raw, "PARTICIPATION")
            snapshot = row.get("initial_completion_snapshot")
            if snapshot is None:
                continue
            initial = _mapping(snapshot, "INITIAL_COMPLETION")
            typed = initial.get("teacher_id_type") or row.get("teacher_id_type")
            if typed not in {"NUMERIC", "TEXT"}:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_ID_TYPE_EVIDENCE_MISSING"
                )
            if typed != teacher_id_type:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_ID_TYPE_CONFLICT"
                )
            # Every participation joins the same course snapshot.  During a
            # substitution, the absent teacher therefore sees the completing
            # teacher's immutable snapshot too; it is evidence for B, not an
            # identity conflict for A.
            if _canonical_id(initial.get("teacher_id"), str(typed)) != teacher_id:
                continue
            if str(initial.get("status") or "").casefold() != "end":
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_INITIAL_COMPLETION_INVALID"
                )
            value = _date_value(initial.get("lesson_local_date"))
            if value is None:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_COMPLETION_DATE_EVIDENCE_INVALID"
                )
            completed.add(value)
    # Current approved frozen rows are useful when the immutable initial
    # snapshot predates the migration but the course fact was backfilled with
    # explicit completion evidence.
    completed.update(
        row.lesson_local_date
        for row in participations
        if row.participation_role == "COMPLETION"
        and row.lesson_local_date is not None
    )
    return TeacherFirstDateEvidenceV2(
        booked_dates=tuple(sorted(booked)),
        completed_dates=tuple(sorted(completed)),
        opened_slot_dates=tuple(sorted(opened)),
        legacy_first_booked_date=_date_value(legacy.get("first_booked_dt")),
        legacy_first_completed_date=_date_value(legacy.get("first_completed_dt")),
        legacy_first_open_slot_date=_date_value(legacy.get("first_open_slot_dt")),
    )


def _tesol_state(
    dom: Mapping[str, Any],
    coverage: TeacherAggregateCoverageV2,
    *,
    teacher_id: str,
    teacher_id_type: str,
) -> bool | None:
    found = False
    for raw in _sequence(dom.get("certifications"), "CERTIFICATIONS"):
        snapshot = _mapping(raw, "CERTIFICATION")
        values, types, _ = _snapshot_values(
            snapshot, table="dom_teacher_certification"
        )
        if values.get("teacher_id") is not None:
            if types.get("teacher_id") != teacher_id_type:
                raise DtsV2TeacherOutboxProcessorError(
                    "DTS_V2_TEACHER_ID_TYPE_CONFLICT"
                )
            _assert_typed_teacher(
                values.get("teacher_id"), types.get("teacher_id"), teacher_id
            )
        if bool(snapshot.get("source_deleted")):
            continue
        if str(values.get("certification_code")) == "16" and _integer_or_none(
            values.get("certification_status")
        ) == 1:
            found = True
    if found:
        return True
    return False if _scope_complete(dom, "dom_teacher_certification", "CURRENT") else None


def _snapshot_values(
    snapshot: Mapping[str, Any],
    *,
    table: str,
    source_key: str | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    if snapshot.get("source_table") != table:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_SOURCE_TABLE_INVALID"
        )
    key = _required_text(
        snapshot.get("source_key"), "DTS_V2_TEACHER_SOURCE_KEY_INVALID"
    )
    if source_key is not None and key != source_key:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_SOURCE_KEY_MISMATCH"
        )
    key_type = _required_text(
        snapshot.get("source_key_type"), "DTS_V2_TEACHER_SOURCE_TYPE_INVALID"
    )
    if key_type not in {"NUMERIC", "TEXT"}:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_SOURCE_TYPE_INVALID"
        )
    return (
        _mapping(snapshot.get("values"), "SOURCE_VALUES"),
        _mapping(snapshot.get("field_types"), "SOURCE_FIELD_TYPES"),
        key_type,
    )


def _assert_typed_teacher(value: Any, source_type: Any, expected: str) -> None:
    if source_type not in {"NUMERIC", "TEXT"}:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_ID_TYPE_EVIDENCE_MISSING"
        )
    canonical = _canonical_id(value, str(source_type))
    if canonical != expected:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_CROSS_REGION_IDENTITY_CONFLICT"
        )


def _canonical_id(value: Any, source_type: str) -> str:
    if source_type == "TEXT":
        return _required_text(value, "DTS_V2_TEACHER_TYPED_ID_INVALID")
    if source_type != "NUMERIC" or isinstance(value, bool) or value is None:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_TYPED_ID_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_TYPED_ID_INVALID"
        ) from exc
    if not number.is_finite():
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_TYPED_ID_INVALID"
        )
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _teacher_area(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.casefold()
    return "ovs" if "global_cn" in normalized or "global_pool" in normalized else "dom"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DtsV2TeacherOutboxProcessorError(
            f"DTS_V2_TEACHER_{label}_INVALID"
        )
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise DtsV2TeacherOutboxProcessorError(
            f"DTS_V2_TEACHER_{label}_INVALID"
        )
    return value


def _required_text(value: Any, error: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DtsV2TeacherOutboxProcessorError(error)
    return value


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _positive_int(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise DtsV2TeacherOutboxProcessorError(
            "DTS_V2_TEACHER_PARTICIPATION_SEQ_INVALID"
        )
    return value


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return int(number) if number == number.to_integral_value() else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
    except ValueError:
        return None


__all__ = [
    "TeacherAggregateCurrentBundleV2",
    "DtsV2TeacherAggregateBundleReader",
    "DtsV2TeacherMaterializer",
    "DtsV2TeacherOutboxProcessor",
    "DtsV2TeacherOutboxProcessorError",
    "RegionalTeacherAggregateV2",
    "TeacherAggregateBundleV2",
    "TeacherMaterializationPlanV2",
    "build_teacher_materialization_plan_v2",
]
