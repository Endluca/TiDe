from __future__ import annotations

import pytest

from app.dts_negative_label_rules_v2 import (
    FrozenNegativeLabelAssignmentV2,
    NegativeLabelCourseFact,
    NegativeLabelDisposition,
    NegativeLabelEvidence,
    PublishedTeacherPersonalizedCopyV2,
    evaluate_repeated_negative_labels,
    negative_label_course_match_key,
)


HASH_V1 = "1" * 64
HASH_V2 = "2" * 64


def _course(
    appoint_id: str,
    *,
    teacher_id: str | None = "T1",
    classification: str | None = "NEGATIVE",
    label_id: str = "7",
    label_id_type: str = "NUMERIC",
    label_name: str | None = "态度问题",
    labels: tuple[NegativeLabelEvidence, ...] | None = None,
    completion_participation_seq: int | None = 1,
) -> NegativeLabelCourseFact:
    return NegativeLabelCourseFact(
        source_region="dom",
        source_appoint_id=appoint_id,
        source_appoint_id_type="NUMERIC",
        completion_teacher_id=teacher_id,
        completion_teacher_id_type="TEXT" if teacher_id is not None else None,
        completion_participation_seq=(
            completion_participation_seq if teacher_id is not None else None
        ),
        grading_classification=classification,
        labels=(
            labels
            if labels is not None
            else (
                NegativeLabelEvidence(
                    label_id,
                    label_id_type,  # type: ignore[arg-type]
                    label_name,
                ),
            )
        ),
    )


def _payload(
    *,
    negative_label_to_en: dict[str, str] | None = None,
    negative_label_execution_variant: dict[str, str] | None = None,
    default_negative_execution_variant: str = "GENERAL",
) -> dict[str, object]:
    return {
        "complaint_title_prefix": "General Complaint - ",
        "negative_title_prefix": "Negative Feedback - ",
        "fallback_titles": {
            "complaint": "General Complaint - Complaint Category",
            "negative": "Negative Feedback - Feedback Pattern",
        },
        "complaint_category_to_en": {"迟到": "Late Arrival"},
        "negative_label_to_en": (
            negative_label_to_en
            if negative_label_to_en is not None
            else {
                "态度问题": "Attitude Problem",
                "普通问题": "General Feedback Pattern",
                "另一个展示名": "Another Feedback Pattern",
                "同一个展示名": "Same Feedback Pattern",
                "灯光过暗/亮": "Lighting Too Dark or Too Bright",
                "环境乱/灯光差": "Distracting Environment or Poor Lighting",
            }
        ),
        "negative_label_execution_variant": (
            negative_label_execution_variant
            if negative_label_execution_variant is not None
            else {
                "灯光过暗/亮": "TEACHING_ENVIRONMENT_PHOTO",
                "环境乱/灯光差": "TEACHING_ENVIRONMENT_PHOTO",
            }
        ),
        "default_negative_execution_variant": (
            default_negative_execution_variant
        ),
    }


def _copy(
    *,
    version_id: str = "copy-v1",
    version_number: int = 1,
    payload_hash: str = HASH_V1,
    payload: dict[str, object] | None = None,
    publication_status: str = "PUBLISHED",
) -> PublishedTeacherPersonalizedCopyV2:
    return PublishedTeacherPersonalizedCopyV2(
        config_key="teacher_personalized_copy",
        version_id=version_id,
        version_number=version_number,
        payload_hash=payload_hash,
        publication_status=publication_status,
        payload=payload if payload is not None else _payload(),
    )


def _evaluate(
    courses: tuple[NegativeLabelCourseFact, ...],
    *,
    configs: tuple[PublishedTeacherPersonalizedCopyV2, ...] | None = None,
    existing_assignments: tuple[FrozenNegativeLabelAssignmentV2, ...] = (),
):
    return evaluate_repeated_negative_labels(
        courses,
        copy_configs=configs if configs is not None else (_copy(),),
        existing_assignments=existing_assignments,
    )


def test_two_distinct_negative_courses_use_published_copy_mapping() -> None:
    (result,) = _evaluate((_course("1"), _course("2")))

    assert result.disposition == NegativeLabelDisposition.READY
    assert result.threshold_met is True
    assert result.teacher_execution_variant == "GENERAL"
    assert result.assignment_teacher_execution_variant == "GENERAL"
    assert result.blocker_code == "NONE"
    assert result.teacher_copy_version_id == "copy-v1"
    assert result.teacher_copy_version_number == 1
    assert result.teacher_copy_payload_hash == HASH_V1
    assert result.teacher_copy_candidate_version_ids == ("copy-v1",)
    assert [
        item.completion_participation_seq for item in result.contributions
    ] == [1, 1]
    assert {
        item.teacher_execution_variant for item in result.contributions
    } == {"GENERAL"}
    assert {
        item.mapped_label_english for item in result.contributions
    } == {"Attitude Problem"}
    assert {item.label_mapping_hit for item in result.contributions} == {True}
    assert {item.display_title for item in result.contributions} == {
        "Negative Feedback - Attitude Problem"
    }
    assert result.assignment_dedupe_key == "personalized:P-FB-NEGATIVE:T1:7"
    assert [
        negative_label_course_match_key(result, contribution)
        for contribution in result.contributions
    ] == [
        "negative-label:T1:7:dom:1",
        "negative-label:T1:7:dom:2",
    ]


def test_positive_unknown_and_uncompleted_courses_do_not_contribute() -> None:
    results = _evaluate(
        (
            _course("1", classification="POSITIVE"),
            _course("2", classification=None),
            _course("3", teacher_id=None),
        )
    )

    assert results == ()


def test_duplicate_label_logs_on_one_course_do_not_reach_threshold() -> None:
    duplicate = NegativeLabelEvidence("7.0", "NUMERIC", "态度问题")
    (result,) = _evaluate(
        (
            _course(
                "1",
                labels=(
                    NegativeLabelEvidence("7", "NUMERIC", "态度问题"),
                    duplicate,
                ),
            ),
        )
    )

    assert result.disposition == NegativeLabelDisposition.BELOW_THRESHOLD
    assert len(result.contributions) == 1


def test_missing_current_name_still_blocks_after_variant_mapping_succeeds() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_name=None),
            _course("2", label_name="态度问题"),
            _course("3", label_name="态度问题"),
        )
    )

    assert result.disposition == NegativeLabelDisposition.PENDING_LABEL_NAME
    assert result.pending_dedupe_key == "negative-label-name-missing:T1:7"
    assert result.blocker_code == "NEGATIVE_LABEL_NAME_MISSING"
    assert result.teacher_execution_variant is None
    assert [
        item.teacher_execution_variant for item in result.contributions
    ] == [None, "GENERAL", "GENERAL"]
    assert len(result.contributions) == 3

    (empty_name,) = _evaluate(
        (_course("1", label_name=""), _course("2", label_name="态度问题"))
    )
    assert empty_name.disposition == NegativeLabelDisposition.PENDING_LABEL_NAME


def test_exact_label_name_selects_copy_and_photo_variant() -> None:
    (photo,) = _evaluate(
        (
            _course("1", label_name="灯光过暗/亮"),
            _course("2", label_name="环境乱/灯光差"),
        )
    )
    (general,) = _evaluate(
        (
            _course("1", label_name="普通问题"),
            _course("2", label_name="另一个展示名"),
        )
    )

    assert photo.disposition == NegativeLabelDisposition.READY
    assert photo.teacher_execution_variant == "TEACHING_ENVIRONMENT_PHOTO"
    assert [item.display_title for item in photo.contributions] == [
        "Negative Feedback - Lighting Too Dark or Too Bright",
        "Negative Feedback - Distracting Environment or Poor Lighting",
    ]
    assert general.disposition == NegativeLabelDisposition.READY
    assert general.teacher_execution_variant == "GENERAL"


def test_current_set_rebuild_falls_below_threshold_when_one_course_disappears() -> None:
    (before,) = _evaluate((_course("1"), _course("2")))
    (after,) = _evaluate((_course("1"),))

    assert before.disposition == NegativeLabelDisposition.READY
    assert after.disposition == NegativeLabelDisposition.BELOW_THRESHOLD


def test_numeric_course_ids_are_canonical_and_sorted_numerically() -> None:
    (result,) = _evaluate((_course("10.0"), _course("009")))

    assert [item.source_appoint_id for item in result.contributions] == ["9", "10"]


def test_ovs_grading_is_persisted_but_never_uses_dom_negative_label_rule() -> None:
    ovs = NegativeLabelCourseFact(
        source_region="ovs",
        source_appoint_id="1",
        source_appoint_id_type="NUMERIC",
        completion_teacher_id="T1",
        completion_teacher_id_type="TEXT",
        completion_participation_seq=1,
        grading_classification="NEGATIVE",
        labels=(NegativeLabelEvidence("7", "NUMERIC", "态度问题"),),
    )

    assert _evaluate((ovs, ovs)) == ()


def test_current_completion_participation_is_preserved_as_task_evidence() -> None:
    (before,) = _evaluate(
        (
            _course("1", completion_participation_seq=1),
            _course("2", completion_participation_seq=1),
        )
    )
    (after,) = _evaluate(
        (
            _course("1", completion_participation_seq=3),
            _course("2", completion_participation_seq=1),
        )
    )

    assert [
        item.completion_participation_seq for item in before.contributions
    ] == [1, 1]
    assert [
        item.completion_participation_seq for item in after.contributions
    ] == [3, 1]
    assert before.assignment_dedupe_key == after.assignment_dedupe_key


def test_copy_version_change_changes_only_the_new_plan_variant() -> None:
    courses = (_course("1"), _course("2"))
    config_v1 = _copy()
    config_v2 = _copy(
        version_id="copy-v2",
        version_number=2,
        payload_hash=HASH_V2,
        payload=_payload(
            negative_label_execution_variant={
                "态度问题": "TEACHING_ENVIRONMENT_PHOTO"
            }
        ),
    )

    (v1,) = _evaluate(courses, configs=(config_v1,))
    (v2,) = _evaluate(courses, configs=(config_v2,))

    assert v1.teacher_execution_variant == "GENERAL"
    assert v1.teacher_copy_version_id == "copy-v1"
    assert v1.teacher_copy_payload_hash == HASH_V1
    assert v2.teacher_execution_variant == "TEACHING_ENVIRONMENT_PHOTO"
    assert v2.teacher_copy_version_id == "copy-v2"
    assert v2.teacher_copy_version_number == 2
    assert v2.teacher_copy_payload_hash == HASH_V2


def test_existing_assignment_variant_and_copy_version_never_change_on_replan() -> None:
    assignment = FrozenNegativeLabelAssignmentV2(
        assignment_dedupe_key="personalized:P-FB-NEGATIVE:T1:7",
        teacher_execution_variant="GENERAL",
        teacher_copy_version_id="copy-v1",
        teacher_copy_payload_hash=HASH_V1,
    )
    config_v2 = _copy(
        version_id="copy-v2",
        version_number=2,
        payload_hash=HASH_V2,
        payload=_payload(
            negative_label_execution_variant={
                "态度问题": "TEACHING_ENVIRONMENT_PHOTO"
            }
        ),
    )

    (result,) = _evaluate(
        (_course("1"), _course("2")),
        configs=(config_v2,),
        existing_assignments=(assignment,),
    )

    assert result.teacher_execution_variant == "TEACHING_ENVIRONMENT_PHOTO"
    assert result.teacher_copy_version_id == "copy-v2"
    assert result.existing_assignment is assignment
    assert result.assignment_teacher_execution_variant == "GENERAL"
    assert result.existing_assignment.teacher_copy_version_id == "copy-v1"
    assert result.existing_assignment.teacher_copy_payload_hash == HASH_V1


def test_missing_copy_config_fails_closed() -> None:
    courses = (_course("1"), _course("2"))

    (missing_config,) = _evaluate(courses, configs=())

    assert missing_config.disposition == (
        NegativeLabelDisposition.PENDING_COPY_CONFIG_MISSING
    )
    assert missing_config.blocker_code == "TASK_COPY_CONFIG_MISSING"
    assert missing_config.teacher_execution_variant is None
    assert missing_config.pending_dedupe_key is None


def test_unmapped_non_empty_name_uses_published_fallback_and_default() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_name="未配置标签"),
            _course("2", label_name="未配置标签"),
        )
    )

    assert result.disposition == NegativeLabelDisposition.READY
    assert result.teacher_execution_variant == "GENERAL"
    assert {item.label_mapping_hit for item in result.contributions} == {False}
    assert {item.mapped_label_english for item in result.contributions} == {None}
    assert {item.display_title for item in result.contributions} == {
        "Negative Feedback - Feedback Pattern"
    }


def test_title_prefix_and_fallback_are_read_from_the_published_payload() -> None:
    payload = _payload()
    payload["negative_title_prefix"] = "Configured Negative - "
    payload["fallback_titles"] = {
        "complaint": "Configured Complaint Fallback",
        "negative": "Configured Negative Fallback",
    }
    config = _copy(payload=payload)

    (mapped,) = _evaluate(
        (_course("1"), _course("2")),
        configs=(config,),
    )
    (unmapped,) = _evaluate(
        (
            _course("1", label_name="未配置标签"),
            _course("2", label_name="未配置标签"),
        ),
        configs=(config,),
    )

    assert {item.display_title for item in mapped.contributions} == {
        "Configured Negative - Attitude Problem"
    }
    assert {item.display_title for item in unmapped.contributions} == {
        "Configured Negative Fallback"
    }


def test_multiple_published_copy_versions_fail_closed_as_config_conflict() -> None:
    config_v1 = _copy()
    config_v2 = _copy(
        version_id="copy-v2",
        version_number=2,
        payload_hash=HASH_V2,
    )

    (result,) = _evaluate(
        (_course("1"), _course("2")),
        configs=(config_v2, config_v1),
    )

    assert result.disposition == (
        NegativeLabelDisposition.PENDING_COPY_CONFIG_CONFLICT
    )
    assert result.blocker_code == "TASK_COPY_CONFIG_CONFLICT"
    assert result.teacher_copy_candidate_version_ids == ("copy-v1", "copy-v2")
    assert result.teacher_copy_version_id is None
    assert result.teacher_execution_variant is None


def test_same_label_id_with_name_variant_conflict_fails_closed() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_name="灯光过暗/亮"),
            _course("2", label_name="普通问题"),
        )
    )

    assert result.disposition == (
        NegativeLabelDisposition.PENDING_LABEL_VARIANT_CONFLICT
    )
    assert result.blocker_code == "NEGATIVE_LABEL_VARIANT_CONFLICT"
    assert result.teacher_copy_version_id == "copy-v1"
    assert result.teacher_execution_variant is None
    assert result.pending_dedupe_key == (
        "negative-label-variant-conflict:T1:7"
    )
    assert [
        item.teacher_execution_variant for item in result.contributions
    ] == ["TEACHING_ENVIRONMENT_PHOTO", "GENERAL"]


def test_missing_name_takes_priority_over_variant_conflict() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_name=None),
            _course("2", label_name="灯光过暗/亮"),
            _course("3", label_name="普通问题"),
        )
    )

    assert result.disposition == NegativeLabelDisposition.PENDING_LABEL_NAME
    assert result.blocker_code == "NEGATIVE_LABEL_NAME_MISSING"
    assert result.pending_dedupe_key == "negative-label-name-missing:T1:7"


def test_same_display_name_with_different_label_ids_uses_name_mapping() -> None:
    same_name_labels = (
        NegativeLabelEvidence("7", "NUMERIC", "同一个展示名"),
        NegativeLabelEvidence("8", "NUMERIC", "同一个展示名"),
    )

    results = _evaluate(
        (
            _course("1", labels=same_name_labels),
            _course("2", labels=same_name_labels),
        )
    )

    assert [(item.label_id, item.teacher_execution_variant) for item in results] == [
        ("7", "GENERAL"),
        ("8", "GENERAL"),
    ]
    assert {
        contribution.display_title
        for item in results
        for contribution in item.contributions
    } == {"Negative Feedback - Same Feedback Pattern"}


def test_typed_label_id_remains_identity_but_does_not_select_copy() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_id="7", label_id_type="TEXT"),
            _course("2", label_id="7", label_id_type="TEXT"),
        )
    )

    assert result.disposition == NegativeLabelDisposition.READY
    assert result.label_id_type == "TEXT"
    assert result.label_id == "7"
    assert result.teacher_execution_variant == "GENERAL"


@pytest.mark.parametrize(
    "overrides",
    [
        {"publication_status": "DRAFT"},
        {"version_id": ""},
        {"version_number": 0},
        {"payload_hash": "not-a-sha"},
    ],
)
def test_copy_config_requires_published_version_and_hash(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "version_id": "copy-v1",
        "version_number": 1,
        "payload_hash": HASH_V1,
        "publication_status": "PUBLISHED",
    }
    values.update(overrides)

    with pytest.raises(ValueError, match="teacher personalized copy"):
        _copy(**values)  # type: ignore[arg-type]


def test_payload_rejects_unapproved_execution_variant() -> None:
    payload = _payload(
        negative_label_execution_variant={"态度问题": "PHOTO"}
    )

    with pytest.raises(ValueError, match="execution variant"):
        _copy(payload=payload)


def test_payload_rejects_non_general_default_variant() -> None:
    payload = _payload(
        default_negative_execution_variant="TEACHING_ENVIRONMENT_PHOTO"
    )

    with pytest.raises(ValueError, match="default variant"):
        _copy(payload=payload)


def test_payload_rejects_execution_mapping_without_approved_english_copy() -> None:
    payload = _payload(
        negative_label_to_en={"态度问题": "Attitude Problem"},
        negative_label_execution_variant={
            "灯光过暗/亮": "TEACHING_ENVIRONMENT_PHOTO"
        },
    )

    with pytest.raises(ValueError, match="requires English copy"):
        _copy(payload=payload)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_payload_requires_exact_published_schema(mutation: str) -> None:
    payload = _payload()
    if mutation == "missing":
        payload.pop("default_negative_execution_variant")
    else:
        payload["label_variant_mappings"] = []

    with pytest.raises(ValueError, match="payload schema"):
        _copy(payload=payload)


def test_label_lookup_is_exact_and_does_not_trim() -> None:
    (result,) = _evaluate(
        (
            _course("1", label_name=" 灯光过暗/亮"),
            _course("2", label_name=" 灯光过暗/亮"),
        )
    )

    assert result.disposition == NegativeLabelDisposition.READY
    assert result.teacher_execution_variant == "GENERAL"
    assert {item.label_mapping_hit for item in result.contributions} == {False}
    assert {item.display_title for item in result.contributions} == {
        "Negative Feedback - Feedback Pattern"
    }
