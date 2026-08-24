"""Deterministic business-output intents derived from one COURSE plan.

This reducer does not create assignments, notifications, or Cases.  It emits
the complete current match set for one source course.  The database-backed
match owner can then suppress disappeared matches, rebuild the affected
TASK_PLAN aggregate, and let the dedicated Planner materialize exactly once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from .dts_business_rules_v2 import absence_task_code
from .dts_v2_course_source_wide_plan import (
    CourseParticipationProjectionV2,
    CourseSourceWidePlanV2,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DtsV2CourseTriggerPlanError(ValueError):
    """The course facts cannot support a canonical output intent."""


@dataclass(frozen=True)
class CourseTriggerMatchV2:
    dedupe_key: str
    match_kind: str
    match_status: str
    output_type: str
    output_key: str | None
    teacher_id: str
    teacher_id_type: str
    source_region: str
    source_appoint_id: str
    participation_seq: int
    target_task_code: str | None
    assignment_dedupe_key: str | None
    evidence_discriminator: str
    evidence_discriminator_type: str
    seed_rule_rank: int
    threshold_required: int
    teacher_execution_variant: str | None
    plan_evidence: Mapping[str, Any] = field(repr=False)
    plan_evidence_hash: str


@dataclass(frozen=True)
class CourseTriggerPlanV2:
    source_region: str
    source_appoint_id: str
    matches: tuple[CourseTriggerMatchV2, ...]
    affected_assignment_dedupe_keys: tuple[str, ...]


def build_course_trigger_plan_v2(
    course: CourseSourceWidePlanV2,
) -> CourseTriggerPlanV2:
    if not isinstance(course, CourseSourceWidePlanV2):
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_PLAN_REQUIRED"
        )
    matches: list[CourseTriggerMatchV2] = []
    for participation in course.participation_rows:
        matches.extend(_absence_matches(participation))
    completion = _completion_row(course)
    if completion is not None:
        matches.extend(_negative_label_matches(completion))
        complaint = _complaint_match(completion)
        if complaint is not None:
            matches.append(complaint)
        camera = _camera_match(completion)
        if camera is not None:
            matches.append(camera)

    by_key: dict[str, CourseTriggerMatchV2] = {}
    for match in matches:
        previous = by_key.setdefault(match.dedupe_key, match)
        if previous != match:
            raise DtsV2CourseTriggerPlanError(
                "DTS_V2_COURSE_TRIGGER_MATCH_CONFLICT"
            )
    ordered = tuple(
        by_key[key] for key in sorted(by_key, key=lambda value: value.encode("utf-8"))
    )
    assignment_keys = tuple(
        sorted(
            {
                match.assignment_dedupe_key
                for match in ordered
                if match.assignment_dedupe_key is not None
            },
            key=lambda value: value.encode("utf-8"),
        )
    )
    return CourseTriggerPlanV2(
        source_region=course.source_region,
        source_appoint_id=course.source_appoint_id,
        matches=ordered,
        affected_assignment_dedupe_keys=assignment_keys,
    )


def _absence_matches(
    participation: CourseParticipationProjectionV2,
) -> tuple[CourseTriggerMatchV2, ...]:
    if (
        participation.source_deleted
        or participation.participation_role not in {"NORMAL", "COMPLETION"}
        or participation.participation_status != "t_absent"
    ):
        return ()
    task_code = absence_task_code(participation.absence_reason_detail)
    if task_code is None:
        return ()
    if (
        participation.absence_source_id is None
        or participation.absence_source_id_type not in {"NUMERIC", "TEXT"}
        or participation.absence_source_row_revision is None
        or participation.absence_source_row_revision < 1
        or participation.absence_selected_reason_type
        != participation.absence_reason_detail
    ):
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_ABSENCE_PROVENANCE_REQUIRED"
        )
    rule_code = (
        "TR-REL-LESSON-MEMO"
        if task_code == "P-REL-MEMO"
        else "TR-REL-ATTENDANCE"
    )
    evidence = {
        "protocol_version": "course-trigger-evidence-v1",
        "rule_code": rule_code,
        "source_region": participation.source_region,
        "source_appoint_id": participation.source_appoint_id,
        "participation_seq": participation.participation_seq,
        "teacher_id": participation.teacher_id,
        "teacher_id_type": participation.teacher_id_type,
        "reason_type": participation.absence_reason_detail,
        "no_notice": participation.no_notice,
        "absence_source_id": participation.absence_source_id,
        "absence_source_id_type": participation.absence_source_id_type,
        "absence_source_row_revision": (
            participation.absence_source_row_revision
        ),
    }
    common = (
        f"{participation.source_region}:{participation.source_appoint_id}:"
        f"{participation.participation_seq}"
    )
    return (
        _match(
            dedupe_key=f"absence:{rule_code}:{common}",
            match_kind=(
                "ABSENCE_P_REL_MEMO"
                if task_code == "P-REL-MEMO"
                else "ABSENCE_P_REL_ATTENDANCE"
            ),
            output_type="TEACHER_TASK",
            output_key=(
                f"personalized:{task_code}:{participation.teacher_id}"
            ),
            participation=participation,
            target_task_code=task_code,
            assignment_dedupe_key=(
                f"personalized:{task_code}:{participation.teacher_id}"
            ),
            evidence_discriminator=participation.absence_source_id,
            evidence_discriminator_type=(
                participation.absence_source_id_type
            ),
            seed_rule_rank=10,
            threshold_required=1,
            evidence=evidence,
        ),
    )


def _negative_label_matches(
    completion: CourseParticipationProjectionV2,
) -> tuple[CourseTriggerMatchV2, ...]:
    if (
        completion.source_region != "dom"
        or completion.grading_classification != "NEGATIVE"
        or completion.grading_evidence_status != "CONFIRMED"
    ):
        return ()
    result: list[CourseTriggerMatchV2] = []
    seen: set[tuple[str, str]] = set()
    for raw_label in completion.labels:
        label_id = raw_label.get("label_id")
        label_type = raw_label.get("label_id_type")
        label_name = raw_label.get("label_name_snapshot")
        if (
            not isinstance(label_id, str)
            or not label_id
            or label_type not in {"NUMERIC", "TEXT"}
            or (label_name is not None and not isinstance(label_name, str))
        ):
            raise DtsV2CourseTriggerPlanError(
                "DTS_V2_COURSE_TRIGGER_LABEL_INVALID"
            )
        identity = (str(label_type), label_id)
        if identity in seen:
            continue
        seen.add(identity)
        assignment_key = (
            f"personalized:P-FB-NEGATIVE:{completion.teacher_id}:{label_id}"
        )
        match_status = "MATCHED" if label_name else "PENDING_DATA"
        evidence = {
            "protocol_version": "course-trigger-evidence-v1",
            "source_region": completion.source_region,
            "source_appoint_id": completion.source_appoint_id,
            "participation_seq": completion.participation_seq,
            "teacher_id": completion.teacher_id,
            "teacher_id_type": completion.teacher_id_type,
            "label_id": label_id,
            "label_id_type": label_type,
            "label_name": label_name,
            "grading_source_id": completion.current_grading_source_id,
            "grading_source_id_type": completion.current_grading_source_id_type,
            "grading_classification": completion.grading_classification,
            "blocker_code": (
                "NONE" if match_status == "MATCHED" else "NEGATIVE_LABEL_NAME_MISSING"
            ),
        }
        result.append(
            _match(
                dedupe_key=(
                    f"negative-label:{completion.teacher_id}:{label_id}:"
                    f"{completion.source_region}:{completion.source_appoint_id}"
                ),
                match_kind=(
                    "NEGATIVE_LABEL_COURSE"
                    if match_status == "MATCHED"
                    else "NEGATIVE_LABEL_NAME_MISSING"
                ),
                match_status=match_status,
                output_type=(
                    "TEACHER_TASK"
                    if match_status == "MATCHED"
                    else "PENDING_DATA"
                ),
                output_key=(assignment_key if match_status == "MATCHED" else None),
                participation=completion,
                target_task_code="P-FB-NEGATIVE",
                assignment_dedupe_key=assignment_key,
                evidence_discriminator=label_id,
                evidence_discriminator_type=str(label_type),
                seed_rule_rank=10,
                threshold_required=2,
                evidence=evidence,
            )
        )
    return tuple(result)


def _complaint_match(
    completion: CourseParticipationProjectionV2,
) -> CourseTriggerMatchV2 | None:
    complaint_id = completion.latest_valid_complaint_id
    complaint_type = completion.latest_valid_complaint_id_type
    if complaint_id is None or complaint_type is None:
        return None
    candidates = [
        item
        for item in completion.complaints
        if item.get("source_complaint_id") == complaint_id
        and item.get("source_complaint_id_type") == complaint_type
    ]
    if len(candidates) != 1:
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_LATEST_COMPLAINT_INVALID"
        )
    complaint = candidates[0]
    if complaint.get("is_valid") is not True:
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_LATEST_COMPLAINT_NOT_VALID"
        )
    if completion.complaint_evidence_status != "CONFIRMED":
        return None
    grandson_id = complaint.get("complaint_type_grandson")
    grandson_type = complaint.get("complaint_type_grandson_type")
    child_name = complaint.get("category_l2_snapshot")
    category_l3 = complaint.get("category_l3_snapshot")
    severity = complaint.get("severity_rank")
    rule_id = complaint.get("complaint_rule_id")
    source_sha256 = complaint.get("source_sha256")
    if grandson_id is None or grandson_type is None:
        return None
    if (
        not isinstance(grandson_id, str)
        or grandson_type not in {"NUMERIC", "TEXT"}
        or not isinstance(child_name, str)
        or not child_name
        or not isinstance(category_l3, str)
        or not category_l3
        or type(severity) is not int
        or not 0 <= severity <= 4
        or not isinstance(rule_id, str)
        or not rule_id
        or not isinstance(source_sha256, str)
        or _SHA256.fullmatch(source_sha256) is None
    ):
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_COMPLAINT_EVIDENCE_INVALID"
        )
    common = (
        f"{completion.source_region}:{completion.source_appoint_id}:"
        f"{completion.participation_seq}:{grandson_id}"
    )
    task_code: str | None
    assignment_key: str | None
    if child_name == "出席问题":
        rule_code = "TR-REL-ATTENDANCE"
        output_type = "TEACHER_TASK"
        task_code = "P-REL-ATTENDANCE"
        assignment_key = (
            f"personalized:P-REL-ATTENDANCE:{completion.teacher_id}"
        )
        output_key = assignment_key
        match_kind = "COMPLAINT_ATTENDANCE"
        rank = 20
    elif child_name == "网络设备问题":
        rule_code = "TR-QUALITY-NETWORK-EQUIPMENT"
        output_type = "NOTIFICATION"
        task_code = None
        assignment_key = None
        output_key = f"complaint-notification:{rule_code}:{common}"
        match_kind = "COMPLAINT_NETWORK_NOTIFICATION"
        rank = 10
    elif severity in {0, 1}:
        rule_code = "TR-FB-SEVERE-COMPLAINT"
        output_type = "OPS_CASE"
        task_code = None
        assignment_key = None
        output_key = f"complaint-case:{rule_code}:{common}"
        match_kind = "SEVERE_COMPLAINT"
        rank = 10
    else:
        rule_code = "TR-FB-GENERAL-COMPLAINT"
        output_type = "TEACHER_TASK"
        task_code = "P-FB-COMPLAINT"
        assignment_key = (
            f"personalized:P-FB-COMPLAINT:{completion.teacher_id}:{grandson_id}"
        )
        output_key = assignment_key
        match_kind = "GENERAL_COMPLAINT"
        rank = 10
    evidence = {
        "protocol_version": "course-trigger-evidence-v1",
        "rule_code": rule_code,
        "source_region": completion.source_region,
        "source_appoint_id": completion.source_appoint_id,
        "participation_seq": completion.participation_seq,
        "teacher_id": completion.teacher_id,
        "teacher_id_type": completion.teacher_id_type,
        "source_complaint_id": complaint_id,
        "source_complaint_id_type": complaint_type,
        "complaint_type_grandson": grandson_id,
        "complaint_type_grandson_type": grandson_type,
        "category_l2": child_name,
        "category_l3": category_l3,
        "severity_rank": severity,
        "complaint_rule_id": rule_id,
        "source_sha256": source_sha256,
    }
    return _match(
        dedupe_key=f"complaint:{rule_code}:{common}",
        match_kind=match_kind,
        output_type=output_type,
        output_key=output_key,
        participation=completion,
        target_task_code=task_code,
        assignment_dedupe_key=assignment_key,
        # The category ID belongs in the output/assignment key, but canonical
        # seed identity is the selected source complaint row itself.
        evidence_discriminator=complaint_id,
        evidence_discriminator_type=str(complaint_type),
        seed_rule_rank=rank,
        threshold_required=1,
        evidence=evidence,
    )


def _camera_match(
    completion: CourseParticipationProjectionV2,
) -> CourseTriggerMatchV2 | None:
    if completion.camera_evidence_status != "CONFIRMED":
        return None
    if completion.is_camera_off is not True:
        return None
    common = (
        f"{completion.source_region}:{completion.source_appoint_id}:"
        f"{completion.participation_seq}"
    )
    source_ref = f"camera-notification:TR-QUALITY-CAMERA-OFF:{common}"
    evidence = {
        "protocol_version": "course-trigger-evidence-v1",
        "rule_code": "TR-QUALITY-CAMERA-OFF",
        "source_region": completion.source_region,
        "source_appoint_id": completion.source_appoint_id,
        "participation_seq": completion.participation_seq,
        "teacher_id": completion.teacher_id,
        "teacher_id_type": completion.teacher_id_type,
        "is_camera_off": True,
    }
    return _match(
        dedupe_key=f"camera-off:TR-QUALITY-CAMERA-OFF:{common}",
        match_kind="CAMERA_OFF_NOTIFICATION",
        output_type="NOTIFICATION",
        output_key=source_ref,
        participation=completion,
        target_task_code=None,
        assignment_dedupe_key=None,
        evidence_discriminator=str(completion.participation_seq),
        evidence_discriminator_type="NUMERIC",
        seed_rule_rank=10,
        threshold_required=1,
        evidence=evidence,
    )


def _completion_row(
    course: CourseSourceWidePlanV2,
) -> CourseParticipationProjectionV2 | None:
    rows = [row for row in course.participation_rows if row.valid_for_scoring]
    if not rows:
        return None
    if len(rows) != 1:
        raise DtsV2CourseTriggerPlanError(
            "DTS_V2_COURSE_TRIGGER_COMPLETION_CONFLICT"
        )
    return rows[0]


def _match(
    *,
    dedupe_key: str,
    match_kind: str,
    output_type: str,
    output_key: str | None,
    participation: CourseParticipationProjectionV2,
    target_task_code: str | None,
    assignment_dedupe_key: str | None,
    evidence_discriminator: str,
    evidence_discriminator_type: str,
    seed_rule_rank: int,
    threshold_required: int,
    evidence: Mapping[str, Any],
    match_status: str = "MATCHED",
) -> CourseTriggerMatchV2:
    canonical = json.dumps(
        dict(evidence),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CourseTriggerMatchV2(
        dedupe_key=dedupe_key,
        match_kind=match_kind,
        match_status=match_status,
        output_type=output_type,
        output_key=output_key,
        teacher_id=participation.teacher_id,
        teacher_id_type=participation.teacher_id_type,
        source_region=participation.source_region,
        source_appoint_id=participation.source_appoint_id,
        participation_seq=participation.participation_seq,
        target_task_code=target_task_code,
        assignment_dedupe_key=assignment_dedupe_key,
        evidence_discriminator=evidence_discriminator,
        evidence_discriminator_type=evidence_discriminator_type,
        seed_rule_rank=seed_rule_rank,
        threshold_required=threshold_required,
        teacher_execution_variant=None,
        plan_evidence=dict(evidence),
        plan_evidence_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "CourseTriggerMatchV2",
    "CourseTriggerPlanV2",
    "DtsV2CourseTriggerPlanError",
    "build_course_trigger_plan_v2",
]
