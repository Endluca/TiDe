from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.dts_absence_rules_v2 import (
    AbsenceMappingDisposition,
    AbsenceReasonFact,
    map_absence_reason_to_participation,
    select_current_absence_reasons,
)
from app.dts_child_selectors_v2 import (
    CloseCameraFact,
    ComplaintFact,
    DomGradingFact,
    SelectorSourceIdentity,
    select_current_close_camera_state,
    select_current_complaints,
    select_current_dom_grading,
)
from app.dts_complaint_routing_v2 import (
    ComplaintCategoryRuleV2,
    ComplaintCategorySnapshotV2,
    ComplaintRouteDisposition,
    ComplaintRoutingInputV2,
    route_current_complaint_v2,
)
from app.dts_course_participation import (
    AppointSnapshot,
    AppointSourceVersion,
    CompletionConflictStatus,
    CourseParticipationState,
    ParticipationRole,
    SourceEventReference,
    reduce_course_participations,
)
from app.dts_lesson_score_rules_v2 import (
    FrozenCompletionIdentityV2,
    PriorLessonComponentSettlementV2,
    SettlementAction,
    SettlementStatus,
    build_lesson_component_conditions_v2,
    plan_lesson_component_settlement_v2,
)
from app.dts_qa_rules_v2 import (
    CameraRouteDisposition,
    CameraRoutingInputV2,
    route_camera_off_v2,
)


SOURCE_APPOINT_ID = "9001"
TEACHER_A = "101"
TEACHER_B = "202"
TEACHER_C = "303"
BASE_TIME = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
COMPLAINT_RULE_SHA = "a" * 64


def _appoint_snapshot(
    teacher_id: str,
    *,
    status: str = "on",
    end_time: str | None = None,
) -> AppointSnapshot:
    return AppointSnapshot(
        teacher_id=teacher_id,
        teacher_id_type="NUMERIC",
        status=status,
        use_point="buy",
        end_time=end_time,
        student_token="dom:v1:" + "b" * 64,
        lesson_local_date="2026-08-22",
        lesson_local_time="18:00:00",
        is_peak=False,
    )


def _appoint_version(
    revision: int,
    operation: str,
    *,
    before: AppointSnapshot | None,
    after: AppointSnapshot | None,
    minutes_after_base: int,
) -> AppointSourceVersion:
    return AppointSourceVersion(
        source_row_revision=revision,
        source_ref=SourceEventReference(
            source_partition_epoch_id="dom-epoch-1",
            topic="dom-appoint-topic",
            partition=0,
            offset=revision,
            source_timestamp=BASE_TIME + timedelta(minutes=minutes_after_base),
        ),
        operation=operation,
        before=before,
        after=after,
    )


def _reduce(*versions: AppointSourceVersion):
    return reduce_course_participations(
        source_region="dom",
        source_appoint_id=SOURCE_APPOINT_ID,
        versions=versions,
    )


def _complaint_input(
    complaint: ComplaintFact,
    state: CourseParticipationState,
) -> ComplaintRoutingInputV2:
    return ComplaintRoutingInputV2(
        source_region="dom",
        source_appoint_id=SOURCE_APPOINT_ID,
        source_appoint_id_type="NUMERIC",
        complaint_row=complaint.as_business_row(),
        child_id=None,
        child_id_type=None,
        grandson_id=str(complaint.complaint_type_grandson),
        grandson_id_type="NUMERIC",
        completion_teacher_id=state.completion_teacher_id,
        completion_teacher_id_type=state.completion_teacher_id_type,
        completion_participation_seq=state.completion_participation_seq,
    )


def _route_complaint(
    complaint: ComplaintFact,
    state: CourseParticipationState,
):
    return route_current_complaint_v2(
        _complaint_input(complaint, state),
        category_rule=ComplaintCategoryRuleV2(
            category_l3_normalized="教学态度",
            severity_rank=2,
            complaint_rule_id=f"complaint-rule:{COMPLAINT_RULE_SHA}:1",
            source_sha256=COMPLAINT_RULE_SHA,
        ),
        grandson_category=ComplaintCategorySnapshotV2(
            source_region="dom",
            category_id="83",
            category_id_type="NUMERIC",
            cate_cn_name="教学态度",
            is_deleted=False,
        ),
    )


def _route_camera(
    camera_off: bool | None,
    state: CourseParticipationState,
):
    return route_camera_off_v2(
        CameraRoutingInputV2(
            source_region="dom",
            source_appoint_id=SOURCE_APPOINT_ID,
            source_appoint_id_type="NUMERIC",
            is_camera_off=camera_off,
            completion_teacher_id=state.completion_teacher_id,
            completion_teacher_id_type=state.completion_teacher_id_type,
            completion_participation_seq=state.completion_participation_seq,
        )
    )


def _score_identity(state: CourseParticipationState) -> FrozenCompletionIdentityV2:
    assert state.completion_teacher_id is not None
    assert state.completion_participation_seq is not None
    return FrozenCompletionIdentityV2(
        source_region="dom",
        source_appoint_id=SOURCE_APPOINT_ID,
        completion_participation_seq=state.completion_participation_seq,
        teacher_id=state.completion_teacher_id,
    )


def test_dom_substitution_pre_arrived_children_stay_with_frozen_completion() -> None:
    a_on = _appoint_snapshot(TEACHER_A)
    b_on = _appoint_snapshot(TEACHER_B)
    b_end = _appoint_snapshot(
        TEACHER_B,
        status="end",
        end_time="2026-08-22T18:30:00+08:00",
    )
    c_end = _appoint_snapshot(
        TEACHER_C,
        status="end",
        end_time="2026-08-22T18:30:00+08:00",
    )
    inserted = _appoint_version(
        1,
        "INSERT",
        before=None,
        after=a_on,
        minutes_after_base=1,
    )
    substituted = _appoint_version(
        2,
        "UPDATE",
        before=a_on,
        after=b_on,
        minutes_after_base=2,
    )
    completed = _appoint_version(
        3,
        "UPDATE",
        before=b_on,
        after=b_end,
        minutes_after_base=30,
    )
    post_end_change = _appoint_version(
        4,
        "UPDATE",
        before=b_end,
        after=c_end,
        minutes_after_base=31,
    )

    absence_reason = AbsenceReasonFact(
        source_reason_id="501",
        source_reason_id_type="NUMERIC",
        source_row_revision=1,
        teacher_id=TEACHER_A,
        teacher_id_type="NUMERIC",
        reason_type="No Notification",
        add_time=BASE_TIME + timedelta(minutes=1, seconds=30),
        source_timestamp=BASE_TIME + timedelta(minutes=1),
    )
    grading = DomGradingFact(
        identity=SelectorSourceIdentity("601", "NUMERIC", 1),
        use_point="buy",
        score=5,
        grading_type=None,
        update_time=BASE_TIME + timedelta(minutes=10),
    )
    complaint = ComplaintFact(
        identity=SelectorSourceIdentity("701", "NUMERIC", 1),
        complaint_type=13,
        complaint_type_grandson=83,
        approve="y",
        validity=1,
        add_time=BASE_TIME + timedelta(minutes=11),
        course_date=date(2026, 8, 22),
    )
    camera = CloseCameraFact(
        identity=SelectorSourceIdentity("801", "NUMERIC", 1)
    )

    selected_grading = select_current_dom_grading((grading,))
    selected_complaint = select_current_complaints((complaint,)).latest_valid
    camera_off = select_current_close_camera_state(
        (camera,),
        scope_complete=False,
    )
    assert selected_grading is not None
    assert selected_grading.classification == "POSITIVE"
    assert selected_complaint is complaint
    assert camera_off is True

    before_substitution = _reduce(inserted)
    early_absence = map_absence_reason_to_participation(
        absence_reason,
        before_substitution.state.participations,
    )
    assert early_absence.disposition == AbsenceMappingDisposition.PENDING_DATA

    before_end = _reduce(inserted, substituted)
    assert [
        (
            row.teacher_id,
            row.participation_status,
            row.participation_role,
            row.is_current,
        )
        for row in before_end.state.participations
    ] == [
        (TEACHER_A, "t_absent", ParticipationRole.NORMAL, False),
        (TEACHER_B, "on", ParticipationRole.NORMAL, True),
    ]
    selected_absence = select_current_absence_reasons(
        (absence_reason,),
        before_end.state.participations,
    ).for_participation(1)
    assert selected_absence is not None
    assert (
        selected_absence.teacher_id,
        selected_absence.participation_seq,
        selected_absence.task_code,
        selected_absence.no_notice,
    ) == (TEACHER_A, 1, "P-REL-ATTENDANCE", True)

    pre_end_complaint = _route_complaint(selected_complaint, before_end.state)
    pre_end_camera = _route_camera(camera_off, before_end.state)
    assert pre_end_complaint.disposition == ComplaintRouteDisposition.WAITING_COMPLETION
    assert pre_end_complaint.output_key is None
    assert pre_end_camera.disposition == CameraRouteDisposition.WAITING_COMPLETION
    assert pre_end_camera.teacher_id is None
    assert (
        before_end.state.completion_teacher_id,
        before_end.state.completion_participation_seq,
    ) == (None, None)
    # TODO(v2-domain): the score planner accepts only a FrozenCompletionIdentityV2
    # and has no WAITING/NO_PLAN result.  The non-forged pre-end assertion is that
    # the reducer exposes no completion identity from which a score can be planned.
    # TODO(v2-task-planner): absence selection exposes A + task_code, but no current
    # pure v2 API can prove that a task assignment was materialized.

    at_end = _reduce(inserted, substituted, completed)
    assert [
        (
            row.teacher_id,
            row.participation_status,
            row.participation_role,
            row.is_current,
        )
        for row in at_end.state.participations
    ] == [
        (TEACHER_A, "t_absent", ParticipationRole.NORMAL, False),
        (TEACHER_B, "end", ParticipationRole.COMPLETION, True),
    ]
    assert (
        at_end.state.completion_teacher_id,
        at_end.state.completion_teacher_id_type,
        at_end.state.completion_participation_seq,
    ) == (TEACHER_B, "NUMERIC", 2)

    conditions = build_lesson_component_conditions_v2(
        source_region="dom",
        grading_classification=selected_grading.classification,
        grading_evidence_complete=True,
        late=False,
        early=False,
        attendance_evidence_complete=True,
        completion_is_peak=at_end.state.completion_snapshot.is_peak,
        camera_off=camera_off,
        cpu_high=None,
        network_high=None,
    )
    praise_condition = next(
        item for item in conditions if item.component_code == "FEEDBACK_PRAISE"
    )
    end_identity = _score_identity(at_end.state)
    praise_award = plan_lesson_component_settlement_v2(
        identity=end_identity,
        condition=praise_condition,
        score_rule_version="score-v1",
        prior=None,
    )
    assert praise_award.action == SettlementAction.AWARD
    assert (
        praise_award.identity.teacher_id,
        praise_award.identity.completion_participation_seq,
        praise_award.score,
    ) == (TEACHER_B, 2, 5)

    end_complaint = _route_complaint(selected_complaint, at_end.state)
    end_camera = _route_camera(camera_off, at_end.state)
    assert end_complaint.disposition == ComplaintRouteDisposition.ROUTED
    assert end_complaint.match_key == (
        "complaint:TR-FB-GENERAL-COMPLAINT:dom:9001:2:83"
    )
    assert end_complaint.output_key == (
        f"personalized:P-FB-COMPLAINT:{TEACHER_B}:83"
    )
    assert end_camera.disposition == CameraRouteDisposition.ROUTED
    assert (
        end_camera.teacher_id,
        end_camera.teacher_id_type,
        end_camera.match_key,
    ) == (
        TEACHER_B,
        "NUMERIC",
        "camera-off:TR-QUALITY-CAMERA-OFF:dom:9001:2",
    )

    after_conflict = _reduce(inserted, substituted, completed, post_end_change)
    assert after_conflict.state.completion_conflict_status == (
        CompletionConflictStatus.PENDING
    )
    assert (
        after_conflict.state.current_teacher_id,
        after_conflict.state.completion_teacher_id,
        after_conflict.state.completion_participation_seq,
    ) == (TEACHER_C, TEACHER_B, 2)
    assert after_conflict.state.participations[-1].participation_role == (
        ParticipationRole.PENDING_CORRECTION
    )

    prior_award = PriorLessonComponentSettlementV2(
        identity=end_identity,
        component_code="FEEDBACK_PRAISE",
        status=SettlementStatus.AWARDED,
        award_generation=praise_award.award_generation,
        current_score=praise_award.score,
        score_rule_version=praise_award.score_rule_version,
        evidence_fingerprint=praise_award.evidence_fingerprint,
        current_award_score_entry_id="score-entry-dom-9001-p2-praise",
    )
    frozen_identity = _score_identity(after_conflict.state)
    praise_after_conflict = plan_lesson_component_settlement_v2(
        identity=frozen_identity,
        condition=praise_condition,
        score_rule_version="score-v1",
        prior=prior_award,
    )
    assert frozen_identity == end_identity
    assert praise_after_conflict.action == SettlementAction.NOOP
    assert praise_after_conflict.identity.teacher_id == TEACHER_B
    assert praise_after_conflict.award_idempotency_key is None

    conflict_complaint = _route_complaint(
        selected_complaint,
        after_conflict.state,
    )
    conflict_camera = _route_camera(camera_off, after_conflict.state)
    assert conflict_complaint.output_key == end_complaint.output_key
    assert conflict_complaint.match_key == end_complaint.match_key
    assert conflict_camera.teacher_id == TEACHER_B
    assert conflict_camera.match_key == end_camera.match_key
