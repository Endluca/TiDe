from __future__ import annotations

from datetime import date

import pytest

from app.dts_teacher_aggregate_v2 import (
    DtsTeacherAggregateV2Error,
    TeacherAggregateCoverageV2,
    TeacherFirstDateEvidenceV2,
    TeacherParticipationMetricV2,
    TeacherProfileFactV2,
    TeacherRelationshipMetricV2,
    TeacherScheduleMetricV2,
    rebuild_teacher_source_wide_v2,
    teacher_expected_source_region_v2,
)


def _coverage(**overrides: bool) -> TeacherAggregateCoverageV2:
    values = {
        "course_current_complete": True,
        "grading_current_complete": True,
        "complaint_current_complete": True,
        "penalty_current_complete": True,
        "absence_current_complete": True,
        "relationship_current_complete": True,
        "schedule_current_complete": True,
        "booked_history_complete": True,
        "completion_history_complete": True,
        "schedule_history_complete": True,
    }
    values.update(overrides)
    return TeacherAggregateCoverageV2(**values)


def _profile(teacher_id: str = "B") -> TeacherProfileFactV2:
    return TeacherProfileFactV2(
        teacher_id=teacher_id,
        real_name="Teacher B",
        center_type=1,
        is_full_time=5,
        course="global_cn_pool",
        employment_status="on",
        status_on_time="2026-08-01T09:00:00+08:00",
    )


def _participation(
    *,
    appoint_id: str,
    teacher_id: str = "B",
    seq: int = 1,
    role: str = "NORMAL",
    status: str | None = "on",
    peak: bool | None = False,
    lesson_date: date | None = date(2026, 8, 10),
    student_token: str | None = None,
    late: bool | None = False,
    early: bool | None = False,
    penalty_evidence: str = "CONFIRMED",
    grading: str = "UNCLASSIFIED",
    grading_evidence: str = "CONFIRMED",
    complaint: bool | None = False,
    valid_complaint: bool | None = False,
    complaint_evidence: str = "CONFIRMED",
    no_notice: bool | None = False,
    absence_evidence: str = "CONFIRMED",
) -> TeacherParticipationMetricV2:
    return TeacherParticipationMetricV2(
        source_region="dom",
        source_appoint_id=appoint_id,
        participation_seq=seq,
        teacher_id=teacher_id,
        participation_role=role,
        participation_status=status,
        source_deleted=False,
        is_peak=peak,
        lesson_local_date=lesson_date,
        completion_student_token=student_token,
        is_late=late,
        late_evidence_status=penalty_evidence,
        is_early=early,
        early_evidence_status=penalty_evidence,
        is_no_notice=no_notice,
        absence_evidence_status=absence_evidence,
        grading_classification=grading,
        grading_evidence_status=grading_evidence,
        has_complaint=complaint,
        has_valid_complaint=valid_complaint,
        complaint_evidence_status=complaint_evidence,
    )


@pytest.mark.parametrize(
    ("course", "expected"),
    [
        ("global_cn", "ovs"),
        ("Global_CN Pool", "ovs"),
        ("global_pool", "ovs"),
        ("adult_english", "dom"),
        ("  HBT  ", "dom"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_teacher_course_maps_to_expected_source_region_fail_closed(
    course: object, expected: str | None
) -> None:
    assert teacher_expected_source_region_v2(course) == expected


def test_rebuilds_confirmed_teacher_counts_rates_and_identity() -> None:
    completed = _participation(
        appoint_id="9001",
        role="COMPLETION",
        status="end",
        peak=True,
        student_token="dom:v1:" + "1" * 64,
        grading="POSITIVE",
        complaint=True,
        valid_complaint=True,
    )
    booked = _participation(appoint_id="9002")
    relationships = (
        TeacherRelationshipMetricV2(
            source_region="dom",
            teacher_id="B",
            student_token="dom:v1:" + "1" * 64,
            is_favorited=True,
            favorite_evidence_status="CONFIRMED",
            is_blocked=False,
            block_evidence_status="CONFIRMED",
        ),
        TeacherRelationshipMetricV2(
            source_region="dom",
            teacher_id="B",
            student_token="dom:v1:" + "2" * 64,
            is_favorited=True,
            favorite_evidence_status="CONFIRMED",
            is_blocked=True,
            block_evidence_status="CONFIRMED",
        ),
    )
    schedules = (
        TeacherScheduleMetricV2(
            source_region="dom",
            source_schedule_id="s1",
            teacher_id="B",
            schedule_date=date(2026, 8, 2),
            is_current_open=True,
            is_regular=True,
            is_peak=True,
        ),
        TeacherScheduleMetricV2(
            source_region="dom",
            source_schedule_id="s2",
            teacher_id="B",
            schedule_date=date(2026, 8, 2),
            is_current_open=True,
            is_regular=False,
            is_peak=False,
        ),
        TeacherScheduleMetricV2(
            source_region="dom",
            source_schedule_id="s3",
            teacher_id="B",
            schedule_date=date(2026, 8, 3),
            is_current_open=False,
            is_regular=True,
            is_peak=True,
        ),
    )

    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(completed, booked),
        relationships=relationships,
        schedules=schedules,
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(
            booked_dates=(date(2026, 8, 10),),
            completed_dates=(date(2026, 8, 10),),
            opened_slot_dates=(date(2026, 8, 2),),
            legacy_first_booked_date=date(2026, 8, 5),
        ),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=True,
    )

    values = result.values
    assert values["center_type_desc"] == "CBT"
    assert values["bu"] == "HBT"
    assert values["teach_area_type"] == "ovs"
    assert values["online_status"] == "NEW"
    assert values["onboard_30d_end_date"] == date(2026, 8, 30)
    assert values["total_booked_cnt"] == 2
    assert values["peak_booked_cnt"] == 1
    assert values["total_completed_cnt"] == 1
    assert values["peak_completed_cnt"] == 1
    assert values["perfect_cnt"] == 1
    assert values["feedback_total_eval_cnt"] == 1
    assert values["feedback_praise_cnt"] == 1
    assert values["feedback_negative_cnt"] == 0
    assert values["feedback_complaint_cnt"] == 1
    assert values["feedback_valid_complaint_cnt"] == 1
    assert values["feedback_favorite_cnt"] == 2
    assert values["feedback_block_cnt"] == 1
    assert values["feedback_favorite_rate"] is None
    assert values["feedback_block_rate"] is None
    assert values["total_slot_cnt"] == 2
    assert values["reg_slot_cnt"] == 1
    assert values["peak_slot_cnt"] == 1
    assert values["slot_days"] == 1
    assert values["peak_slot_days"] == 1
    assert values["capacity_avg_completed_per_day"] == pytest.approx(1 / 30)
    assert values["capacity_peak_slot_rate"] == 0.5
    assert values["feedback_eval_rate"] == 1.0
    assert values["first_booked_dt"] == date(2026, 8, 5)
    assert (
        result.first_date_evidence["first_booked_dt_evidence_status"]
        == "LEGACY_FROZEN"
    )


def test_substituted_absent_teacher_keeps_booked_and_absent_not_completed() -> None:
    absent = _participation(
        appoint_id="9001",
        teacher_id="A",
        role="NORMAL",
        status="t_absent",
        no_notice=True,
    )

    result = rebuild_teacher_source_wide_v2(
        profile=_profile("A"),
        participations=(absent,),
        relationships=(),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(
            booked_dates=(date(2026, 8, 10),),
        ),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=False,
    )

    assert result.values["total_booked_cnt"] == 1
    assert result.values["absent_cnt"] == 1
    assert result.values["no_notice_cnt"] == 1
    assert result.values["total_completed_cnt"] == 0
    assert result.values["reliability_absent_rate"] == 1.0


def test_lifetime_course_facts_are_not_filtered_by_the_onboarding_30_days() -> None:
    old_completion = _participation(
        appoint_id="old-course",
        role="COMPLETION",
        status="end",
        lesson_date=date(2026, 6, 1),
        student_token="dom:v1:" + "3" * 64,
        grading="POSITIVE",
    )

    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(old_completion,),
        relationships=(),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(
            booked_dates=(date(2026, 6, 1),),
            completed_dates=(date(2026, 6, 1),),
        ),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=False,
    )

    assert result.values["total_booked_cnt"] == 1
    assert result.values["total_completed_cnt"] == 1
    assert result.values["feedback_praise_cnt"] == 1
    # Only this explicitly onboarding-scoped metric applies the first-30-day
    # window; it must not erase lifetime course facts above.
    assert result.values["capacity_avg_completed_per_day"] == 0.0


def test_unknown_penalty_evidence_does_not_guess_perfect_or_rates() -> None:
    completed = _participation(
        appoint_id="9001",
        role="COMPLETION",
        status="end",
        student_token="dom:v1:" + "1" * 64,
        late=None,
        early=False,
        penalty_evidence="SOURCE_MISSING",
    )

    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(completed,),
        relationships=(),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=None,
    )

    assert result.values["late_cnt"] is None
    assert result.values["early_cnt"] is None
    assert result.values["anomaly_cnt"] is None
    assert result.values["perfect_cnt"] is None
    assert result.values["reliability_late_rate"] is None
    assert result.metric_evidence["perfect_cnt"] == "SOURCE_MISSING"


def test_relationship_counts_keep_favorite_and_block_evidence_independent() -> None:
    relationship = TeacherRelationshipMetricV2(
        source_region="dom",
        teacher_id="B",
        student_token="dom:v1:" + "4" * 64,
        is_favorited=None,
        favorite_evidence_status="SOURCE_MISSING",
        is_blocked=False,
        block_evidence_status="CONFIRMED",
    )

    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(),
        relationships=(relationship,),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=False,
    )

    assert result.values["feedback_favorite_cnt"] is None
    assert result.values["feedback_block_cnt"] == 0


def test_complete_empty_relationship_set_is_confirmed_zero() -> None:
    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(),
        relationships=(),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=False,
    )

    assert result.values["feedback_favorite_cnt"] == 0
    assert result.values["feedback_block_cnt"] == 0
    assert result.metric_evidence["feedback_favorite_cnt"] == "CONFIRMED"
    assert result.metric_evidence["feedback_block_cnt"] == "CONFIRMED"


def test_incomplete_scope_keeps_empty_counts_unknown() -> None:
    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(),
        relationships=(),
        schedules=(),
        coverage=_coverage(
            course_current_complete=False,
            relationship_current_complete=False,
            schedule_current_complete=False,
            booked_history_complete=False,
            completion_history_complete=False,
            schedule_history_complete=False,
        ),
        first_dates=TeacherFirstDateEvidenceV2(),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=None,
    )

    assert result.values["total_booked_cnt"] is None
    assert result.values["feedback_favorite_cnt"] is None
    assert result.values["total_slot_cnt"] is None
    assert result.values["reliability_absent_rate"] is None
    assert result.values["first_booked_dt"] is None
    assert (
        result.first_date_evidence["first_booked_dt_evidence_status"]
        == "SOURCE_MISSING"
    )


def test_confirmed_empty_history_is_distinct_from_missing_history() -> None:
    result = rebuild_teacher_source_wide_v2(
        profile=_profile(),
        participations=(),
        relationships=(),
        schedules=(),
        coverage=_coverage(),
        first_dates=TeacherFirstDateEvidenceV2(),
        business_date_beijing=date(2026, 8, 22),
        is_cpl_tesol=False,
    )

    assert result.values["first_booked_dt"] is None
    assert (
        result.first_date_evidence["first_booked_dt_evidence_status"]
        == "CONFIRMED_EMPTY"
    )


def test_rejects_mixed_teacher_facts() -> None:
    with pytest.raises(
        DtsTeacherAggregateV2Error,
        match="PARTICIPATION_TEACHER_MISMATCH",
    ):
        rebuild_teacher_source_wide_v2(
            profile=_profile("A"),
            participations=(_participation(appoint_id="9001", teacher_id="B"),),
            relationships=(),
            schedules=(),
            coverage=_coverage(),
            first_dates=TeacherFirstDateEvidenceV2(),
            business_date_beijing=date(2026, 8, 22),
            is_cpl_tesol=False,
        )


def test_rejects_duplicate_rows_created_by_an_accidental_join() -> None:
    duplicate = _participation(appoint_id="9001")
    with pytest.raises(
        DtsTeacherAggregateV2Error,
        match="PARTICIPATION_DUPLICATE",
    ):
        rebuild_teacher_source_wide_v2(
            profile=_profile(),
            participations=(duplicate, duplicate),
            relationships=(),
            schedules=(),
            coverage=_coverage(),
            first_dates=TeacherFirstDateEvidenceV2(),
            business_date_beijing=date(2026, 8, 22),
            is_cpl_tesol=False,
        )
