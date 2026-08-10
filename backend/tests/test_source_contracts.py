from __future__ import annotations

from app.source_contracts import (
    LESSON_FIELD_DEPENDENCIES,
    LESSON_SOURCE_FIELDS,
    LESSON_SOURCE_TABLE,
    SOURCE_FIELD_DEPENDENCIES,
    TEACHER_CSV_FIELDS,
    TEACHER_FIELD_DEPENDENCIES,
    TEACHER_G01_STATUS_FIELDS,
    TEACHER_NO_DOWNSTREAM_FIELDS,
    TEACHER_SOURCE_FIELDS,
    TEACHER_SOURCE_TABLE,
    validate_source_contracts,
)


EXPECTED_TEACHER_CSV_FIELDS = (
    "tchr_id",
    "real_name",
    "tchr_group",
    "tchr_group_desc",
    "center_type_id",
    "center_type_desc",
    "bu",
    "based_type",
    "status",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_days",
    "job_month",
    "is_ft_hbt",
    "is_fte",
    "teach_area_type",
    "tchr_score",
    "onboard_date",
    "onboard_30d_end_date",
    "first_open_slot_dt",
    "first_booked_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "total_completed_cnt",
    "peak_completed_cnt",
    "absent_cnt",
    "late_cnt",
    "early_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "completed_again_student_15d_cnt",
    "feedback_total_eval_cnt",
    "feedback_praise_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_favorite_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "peak_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_rebook_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
)

EXPECTED_TEACHER_G01_STATUS_FIELDS = (
    "is_cpl_tesol",
    "is_self_introduce",
)

EXPECTED_TEACHER_SOURCE_FIELDS = (
    EXPECTED_TEACHER_CSV_FIELDS + EXPECTED_TEACHER_G01_STATUS_FIELDS
)

EXPECTED_LESSON_SOURCE_FIELDS = (
    "课程id",
    "上课日期",
    "上课时间",
    "是否高峰",
    "老师id",
    "学员id",
    "课程状态",
    "缺席原因明细",
    "迟到",
    "早退",
    "差评分",
    "差评标签",
    "投诉一级分类",
    "投诉二级分类",
    "投诉三级分类",
    "是否拉黑",
    "收藏",
    "好评标签",
    "评价详情",
    "未开摄像头",
    "cpu占用过高",
    "网络延迟过高",
    "假早退",
)

EXPECTED_TEACHER_NO_DOWNSTREAM_FIELDS = {
    "tchr_group",
    "tchr_group_desc",
    "center_type_id",
    "center_type_desc",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_month",
    "is_ft_hbt",
    "is_fte",
    "tchr_score",
    "first_open_slot_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "completed_again_student_15d_cnt",
    "feedback_total_eval_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_rebook_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
}


def test_source_field_contracts_preserve_csv_and_append_only_g01_facts() -> None:
    assert TEACHER_CSV_FIELDS == EXPECTED_TEACHER_CSV_FIELDS
    assert TEACHER_G01_STATUS_FIELDS == EXPECTED_TEACHER_G01_STATUS_FIELDS
    assert TEACHER_SOURCE_FIELDS == EXPECTED_TEACHER_SOURCE_FIELDS
    assert LESSON_SOURCE_FIELDS == EXPECTED_LESSON_SOURCE_FIELDS
    assert len(TEACHER_CSV_FIELDS) == 61
    assert len(TEACHER_SOURCE_FIELDS) == 63
    assert len(LESSON_SOURCE_FIELDS) == 23
    assert "是否复约" not in LESSON_SOURCE_FIELDS


def test_dependency_registry_covers_all_86_fields_without_extras() -> None:
    assert tuple(TEACHER_FIELD_DEPENDENCIES) == TEACHER_SOURCE_FIELDS
    assert set(TEACHER_FIELD_DEPENDENCIES) == set(TEACHER_SOURCE_FIELDS)
    assert set(LESSON_FIELD_DEPENDENCIES) == set(LESSON_SOURCE_FIELDS)
    assert len(TEACHER_FIELD_DEPENDENCIES) + len(LESSON_FIELD_DEPENDENCIES) == 86
    assert set(SOURCE_FIELD_DEPENDENCIES) == {
        TEACHER_SOURCE_TABLE,
        LESSON_SOURCE_TABLE,
    }
    validate_source_contracts()


def test_every_field_has_handlers_or_an_explicit_no_downstream_reason() -> None:
    for dependencies in SOURCE_FIELD_DEPENDENCIES.values():
        for dependency in dependencies.values():
            assert dependency.authority
            if dependency.handlers:
                assert dependency.recompute_scope != "NONE"
                assert dependency.no_downstream_reason is None
            else:
                assert dependency.recompute_scope == "NONE"
                assert dependency.no_downstream_reason


def test_no_downstream_fields_are_exact_and_do_not_emit_handlers() -> None:
    assert set(TEACHER_NO_DOWNSTREAM_FIELDS) == EXPECTED_TEACHER_NO_DOWNSTREAM_FIELDS
    actual_teacher_no_downstream = {
        field
        for field, dependency in TEACHER_FIELD_DEPENDENCIES.items()
        if not dependency.handlers
    }
    assert actual_teacher_no_downstream == EXPECTED_TEACHER_NO_DOWNSTREAM_FIELDS
    assert {
        field
        for field, dependency in LESSON_FIELD_DEPENDENCIES.items()
        if not dependency.handlers
    } == {"差评分"}


def test_teacher_scoring_and_gate_dependencies_are_field_level() -> None:
    assert TEACHER_FIELD_DEPENDENCIES["perfect_cnt"].handlers == ()
    assert "derived from lesson status" in (
        TEACHER_FIELD_DEPENDENCIES["perfect_cnt"].no_downstream_reason or ""
    )
    assert TEACHER_FIELD_DEPENDENCIES["feedback_praise_cnt"].recompute_scope == (
        "SINGLE_TEACHER_USER_FEEDBACK"
    )
    assert TEACHER_FIELD_DEPENDENCIES["peak_slot_cnt"].recompute_scope == (
        "SINGLE_TEACHER_CAPACITY"
    )
    assert TEACHER_FIELD_DEPENDENCIES["late_cnt"].handlers == (
        "TEACHER_QUALIFICATION",
    )
    for field in EXPECTED_TEACHER_G01_STATUS_FIELDS:
        assert TEACHER_FIELD_DEPENDENCIES[field].handlers == ("TEACHER_PROFILE",)
        assert TEACHER_FIELD_DEPENDENCIES[field].recompute_scope == (
            "SINGLE_TEACHER"
        )


def test_lesson_aggregate_rules_have_narrow_recompute_scopes() -> None:
    assert LESSON_FIELD_DEPENDENCIES["学员id"].recompute_scope == (
        "SINGLE_TEACHER_FEEDBACK_SETS"
    )
    assert LESSON_FIELD_DEPENDENCIES["差评标签"].recompute_scope == (
        "SINGLE_TEACHER_NEGATIVE_LABEL_SET"
    )
    assert LESSON_FIELD_DEPENDENCIES["是否拉黑"].recompute_scope == (
        "SINGLE_TEACHER_BLACKLIST_SET"
    )
    assert LESSON_FIELD_DEPENDENCIES["投诉三级分类"].handlers == (
        "LESSON_TRIGGER",
        "COMPLAINT_TEACHER",
        "TEACHER_QUALIFICATION",
    )
    assert LESSON_FIELD_DEPENDENCIES["未开摄像头"].recompute_scope == (
        "SINGLE_LESSON_AND_TEACHER_CLASS_QUALITY"
    )
    assert "TEACHER_RELIABILITY" in LESSON_FIELD_DEPENDENCIES["课程状态"].handlers
    assert "TEACHER_TOTAL" in LESSON_FIELD_DEPENDENCIES["迟到"].handlers
