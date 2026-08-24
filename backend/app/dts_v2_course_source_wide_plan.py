"""Pure COURSE aggregate reduction for DTS v2 downstream materialization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .dts_lesson_score_rules_v2 import (
    LessonComponentConditionV2,
    build_lesson_component_conditions_v2,
)


_VISIBLE_ROLES = frozenset({"NORMAL", "COMPLETION"})
_REGIONS = frozenset({"dom", "ovs"})


class DtsV2CourseSourceWidePlanError(ValueError):
    """A COURSE aggregate cannot be reduced without guessing."""


@dataclass(frozen=True)
class CourseParticipationProjectionV2:
    source_region: str
    source_appoint_id: str
    participation_seq: int
    teacher_id: str
    teacher_id_type: str
    participation_role: str
    participation_status: str | None
    is_current: bool
    visible_to_teacher: bool
    valid_for_scoring: bool
    source_deleted: bool
    participation_row_version: int
    participation_fact_row_version: int | None
    lesson_local_date: str | None
    lesson_local_time: str | None
    scheduled_start_at: str | None
    completion_end_time: str | None
    student_token: str | None
    is_peak: bool | None
    absence_reason_detail: str | None
    no_notice: bool | None
    is_late: bool | None
    late_evidence_status: str
    is_early: bool | None
    early_evidence_status: str
    is_perfect: bool | None
    grading_classification: str
    grading_evidence_status: str
    current_grading_source_id: str | None
    current_grading_source_id_type: str | None
    negative_score: str | None
    labels: tuple[Mapping[str, Any], ...]
    complaints: tuple[Mapping[str, Any], ...]
    has_complaint: bool | None
    has_valid_complaint: bool | None
    complaint_evidence_status: str
    latest_valid_complaint_id: str | None
    latest_valid_complaint_id_type: str | None
    complaint_category_l1: str | None
    complaint_category_l2: str | None
    complaint_category_l3: str | None
    is_camera_off: bool | None
    camera_evidence_status: str
    is_cpu_usage_high: None
    cpu_evidence_status: str
    is_network_delay_high: None
    network_evidence_status: str
    evidence_status: str
    score_conditions: tuple[LessonComponentConditionV2, ...]
    absence_source_id: str | None = None
    absence_source_id_type: str | None = None
    absence_source_row_revision: int | None = None
    absence_selected_reason_type: str | None = None
    teacher_expected_source_region: str | None = None
    teacher_region_evidence_status: str = "SOURCE_MISSING"
    teacher_profile_source_row_revision: int | None = None
    teacher_profile_source_payload_hash: str | None = None


@dataclass(frozen=True)
class CourseSourceWidePlanV2:
    source_region: str
    source_appoint_id: str
    source_deleted: bool
    course_row_version: int
    course_fact_row_version: int | None
    source_status: str | None
    current_teacher_id: str | None
    current_participation_seq: int | None
    lesson_local_date: str | None
    lesson_local_time: str | None
    scheduled_start_at: str | None
    student_token: str | None
    is_peak: bool | None
    completion_conflict_status: str
    completion_teacher_id: str | None
    completion_participation_seq: int | None
    participation_rows: tuple[CourseParticipationProjectionV2, ...]
    affected_teacher_ids: tuple[str, ...]
    favorite_observation_required: bool
    favorite_observation_blocker: str | None


def build_course_source_wide_plan_v2(
    *,
    source_region: str,
    source_appoint_id: str,
    aggregate_state: Mapping[str, Any],
) -> CourseSourceWidePlanV2:
    """Build all teacher-visible participation rows for one source course.

    The reducer does not persist score entries or favorite observations.  It
    makes those requirements explicit so the transactional SourceWide owner
    can write them together with its result rows.
    """

    if source_region not in _REGIONS or not _nonempty(source_appoint_id):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_IDENTITY_INVALID"
        )
    if not isinstance(aggregate_state, Mapping):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_STATE_INVALID"
        )
    course = _required_mapping(aggregate_state, "course")
    course_evidence = _course_evidence_status(course)
    fact = aggregate_state.get("course_fact")
    if fact is not None and not isinstance(fact, Mapping):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_FACT_INVALID"
        )
    labels = _mapping_sequence(aggregate_state.get("labels"), "LABELS")
    complaints = _mapping_sequence(
        aggregate_state.get("complaints"), "COMPLAINTS"
    )
    participations = _mapping_sequence(
        aggregate_state.get("participations"), "PARTICIPATIONS"
    )

    source_deleted = _required_bool(course, "source_is_deleted")
    course_row_version = _positive_int(
        course.get("row_version"),
        "DTS_V2_COURSE_SOURCE_WIDE_COURSE_REVISION_INVALID",
    )
    course_fact_row_version = (
        None
        if fact is None
        else _positive_int(
            fact.get("row_version"),
            "DTS_V2_COURSE_SOURCE_WIDE_FACT_REVISION_INVALID",
        )
    )
    conflict_status = course.get("completion_conflict_status", "NONE")
    if conflict_status not in {
        "NONE",
        "PENDING",
        "RESOLVED_KEEP",
        "RESOLVED_UPDATE",
        "RESOLVED_TRANSFER",
        "RESOLVED_VOID",
    }:
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_CONFLICT_STATUS_INVALID"
        )
    completion_seq = _optional_positive_int(
        course.get("completion_participation_seq"),
        "DTS_V2_COURSE_SOURCE_WIDE_COMPLETION_SEQ_INVALID",
    )
    completion_teacher = _optional_text(
        course.get("completion_teacher_id"),
        "DTS_V2_COURSE_SOURCE_WIDE_COMPLETION_TEACHER_INVALID",
    )
    if (completion_seq is None) is not (completion_teacher is None):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_COMPLETION_POINTER_INVALID"
        )
    current_seq = _optional_positive_int(
        course.get("current_participation_seq"),
        "DTS_V2_COURSE_SOURCE_WIDE_CURRENT_SEQ_INVALID",
    )
    current_teacher = _optional_text(
        course.get("current_teacher_id"),
        "DTS_V2_COURSE_SOURCE_WIDE_CURRENT_TEACHER_INVALID",
    )
    if (current_seq is None) is not (current_teacher is None):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_CURRENT_POINTER_INVALID"
        )

    plan_rows: list[CourseParticipationProjectionV2] = []
    identities: set[int] = set()
    for participation in participations:
        seq = _positive_int(
            participation.get("participation_seq"),
            "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_SEQ_INVALID",
        )
        if seq in identities:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_DUPLICATE"
            )
        identities.add(seq)
        role = participation.get("participation_role")
        if role not in {
            "NORMAL",
            "COMPLETION",
            "PENDING_CORRECTION",
            "REJECTED_CORRECTION",
            "SUPERSEDED_COMPLETION",
            "VOIDED_COMPLETION",
        }:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_ROLE_INVALID"
            )
        teacher_id = _required_text(
            participation,
            "teacher_id",
            "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_TEACHER_INVALID",
        )
        teacher_id_type = participation.get("teacher_id_type")
        if teacher_id_type not in {"NUMERIC", "TEXT"}:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_TEACHER_TYPE_INVALID"
            )
        participation_deleted = _required_bool(
            participation, "source_deleted"
        )
        participation_row_version = _positive_int(
            participation.get("row_version"),
            "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_REVISION_INVALID",
        )
        participation_fact_row_version = _optional_positive_int(
            participation.get("participation_fact_row_version"),
            "DTS_V2_COURSE_SOURCE_WIDE_PARTICIPATION_FACT_REVISION_INVALID",
        )
        visible = role in _VISIBLE_ROLES and not participation_deleted
        is_current = _required_bool(participation, "is_current")
        teacher_region = _teacher_region_provenance(
            source_region=source_region,
            participation=participation,
        )
        valid_for_scoring = bool(
            not source_deleted
            and visible
            and role == "COMPLETION"
            and completion_seq == seq
            and completion_teacher == teacher_id
            and course_evidence == "CONFIRMED"
            and teacher_region[1] == "CONFIRMED"
        )
        late = _optional_bool(participation.get("is_late"), "LATE")
        early = _optional_bool(participation.get("is_early"), "EARLY")
        late_evidence = _evidence_status(
            participation.get("late_evidence_status"), "LATE"
        )
        early_evidence = _evidence_status(
            participation.get("early_evidence_status"), "EARLY"
        )
        is_perfect = _is_perfect(
            valid_for_scoring=valid_for_scoring,
            late=late,
            early=early,
            late_evidence=late_evidence,
            early_evidence=early_evidence,
        )
        absence_provenance = _absence_provenance(
            source_region=source_region,
            participation=participation,
        )
        grading = _course_fact_text(
            fact, "grading_classification", default="SOURCE_MISSING"
        )
        if grading not in {
            "POSITIVE",
            "NEGATIVE",
            "UNCLASSIFIED",
            "SOURCE_MISSING",
        }:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_GRADING_INVALID"
            )
        grading_evidence = _course_fact_text(
            fact, "grading_evidence_status", default="SOURCE_MISSING"
        )
        camera = _course_fact_optional_bool(fact, "is_camera_off")
        camera_evidence = _course_fact_text(
            fact, "camera_evidence_status", default="SOURCE_MISSING"
        )
        conditions = (
            build_lesson_component_conditions_v2(
                source_region=source_region,  # type: ignore[arg-type]
                grading_classification=(
                    grading if grading in {"POSITIVE", "NEGATIVE"} else None
                ),
                grading_evidence_complete=grading_evidence == "CONFIRMED",
                late=late,
                early=early,
                attendance_evidence_complete=(
                    late_evidence == "CONFIRMED"
                    and early_evidence == "CONFIRMED"
                ),
                completion_is_peak=_optional_bool(
                    course.get("completion_is_peak"), "COMPLETION_PEAK"
                ),
                camera_off=camera,
                cpu_high=None,
                network_high=None,
            )
            if valid_for_scoring
            else ()
        )
        plan_rows.append(
            CourseParticipationProjectionV2(
                source_region=source_region,
                source_appoint_id=source_appoint_id,
                participation_seq=seq,
                teacher_id=teacher_id,
                teacher_id_type=str(teacher_id_type),
                participation_role=str(role),
                participation_status=_optional_text(
                    participation.get("participation_status"),
                    "DTS_V2_COURSE_SOURCE_WIDE_STATUS_INVALID",
                ),
                is_current=is_current,
                visible_to_teacher=visible,
                valid_for_scoring=valid_for_scoring,
                source_deleted=source_deleted or participation_deleted,
                participation_row_version=participation_row_version,
                participation_fact_row_version=participation_fact_row_version,
                lesson_local_date=_optional_iso_text(
                    course.get("completion_lesson_local_date")
                    if valid_for_scoring
                    else course.get("lesson_local_date")
                ),
                lesson_local_time=_optional_iso_text(
                    course.get("completion_lesson_local_time")
                    if valid_for_scoring
                    else course.get("lesson_local_time")
                ),
                scheduled_start_at=_optional_iso_text(
                    course.get("scheduled_start_at")
                ),
                completion_end_time=(
                    _optional_iso_text(course.get("completion_end_time"))
                    if valid_for_scoring
                    else None
                ),
                student_token=(
                    _optional_text(
                        course.get("completion_student_token"),
                        "DTS_V2_COURSE_SOURCE_WIDE_STUDENT_TOKEN_INVALID",
                    )
                    if valid_for_scoring
                    else _optional_text(
                        course.get("student_token"),
                        "DTS_V2_COURSE_SOURCE_WIDE_STUDENT_TOKEN_INVALID",
                    )
                ),
                is_peak=(
                    _optional_bool(
                        course.get("completion_is_peak"), "COMPLETION_PEAK"
                    )
                    if valid_for_scoring
                    else _optional_bool(course.get("is_peak"), "PEAK")
                ),
                absence_reason_detail=_optional_text(
                    participation.get("absence_reason_detail"),
                    "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_INVALID",
                ),
                no_notice=_optional_bool(
                    participation.get("no_notice"), "NO_NOTICE"
                ),
                is_late=late,
                late_evidence_status=late_evidence,
                is_early=early,
                early_evidence_status=early_evidence,
                is_perfect=is_perfect,
                grading_classification=grading,
                grading_evidence_status=grading_evidence,
                current_grading_source_id=_course_fact_optional_text(
                    fact, "current_grading_source_id"
                ),
                current_grading_source_id_type=_course_fact_optional_id_type(
                    fact, "current_grading_source_id_type"
                ),
                negative_score=_optional_iso_text(
                    None if fact is None else fact.get("negative_score")
                ),
                labels=labels,
                complaints=complaints,
                has_complaint=_course_fact_optional_bool(
                    fact, "has_complaint"
                ),
                has_valid_complaint=_course_fact_optional_bool(
                    fact, "has_valid_complaint"
                ),
                complaint_evidence_status=_course_fact_text(
                    fact,
                    "complaint_evidence_status",
                    default="SOURCE_MISSING",
                ),
                latest_valid_complaint_id=_course_fact_optional_text(
                    fact, "latest_valid_complaint_id"
                ),
                latest_valid_complaint_id_type=_course_fact_optional_id_type(
                    fact, "latest_valid_complaint_id_type"
                ),
                complaint_category_l1=_course_fact_optional_text(
                    fact, "latest_category_l1_snapshot"
                ),
                complaint_category_l2=_course_fact_optional_text(
                    fact, "latest_category_l2_snapshot"
                ),
                complaint_category_l3=_course_fact_optional_text(
                    fact, "latest_category_l3_snapshot"
                ),
                is_camera_off=camera,
                camera_evidence_status=camera_evidence,
                is_cpu_usage_high=None,
                cpu_evidence_status="SOURCE_MISSING",
                is_network_delay_high=None,
                network_evidence_status="SOURCE_MISSING",
                evidence_status=_overall_evidence_status(
                    course=course,
                    fact=fact,
                    participation=participation,
                    valid_for_scoring=valid_for_scoring,
                ),
                score_conditions=tuple(conditions),
                absence_source_id=absence_provenance[0],
                absence_source_id_type=absence_provenance[1],
                absence_source_row_revision=absence_provenance[2],
                absence_selected_reason_type=absence_provenance[3],
                teacher_expected_source_region=teacher_region[0],
                teacher_region_evidence_status=teacher_region[1],
                teacher_profile_source_row_revision=teacher_region[2],
                teacher_profile_source_payload_hash=teacher_region[3],
            )
        )

    if completion_seq is not None:
        completion_rows = [
            row
            for row in plan_rows
            if row.participation_seq == completion_seq
            and row.teacher_id == completion_teacher
            and row.participation_role == "COMPLETION"
        ]
        if len(completion_rows) != 1:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_COMPLETION_ROW_INVALID"
            )

    favorite_required, favorite_blocker = _favorite_requirement(
        course=course,
        course_evidence_status=course_evidence,
        source_deleted=source_deleted,
        completion_conflict_status=str(conflict_status),
        completion_seq=completion_seq,
        completion_teacher=completion_teacher,
    )
    return CourseSourceWidePlanV2(
        source_region=source_region,
        source_appoint_id=source_appoint_id,
        source_deleted=source_deleted,
        course_row_version=course_row_version,
        course_fact_row_version=course_fact_row_version,
        source_status=_optional_text(
            course.get("source_status"),
            "DTS_V2_COURSE_SOURCE_WIDE_STATUS_INVALID",
        ),
        current_teacher_id=current_teacher,
        current_participation_seq=current_seq,
        lesson_local_date=_optional_iso_text(course.get("lesson_local_date")),
        lesson_local_time=_optional_iso_text(course.get("lesson_local_time")),
        scheduled_start_at=_optional_iso_text(course.get("scheduled_start_at")),
        student_token=_optional_text(
            course.get("student_token"),
            "DTS_V2_COURSE_SOURCE_WIDE_STUDENT_TOKEN_INVALID",
        ),
        is_peak=_optional_bool(course.get("is_peak"), "PEAK"),
        completion_conflict_status=str(conflict_status),
        completion_teacher_id=completion_teacher,
        completion_participation_seq=completion_seq,
        participation_rows=tuple(sorted(plan_rows, key=lambda row: row.participation_seq)),
        affected_teacher_ids=tuple(
            sorted({row.teacher_id for row in plan_rows})
        ),
        favorite_observation_required=favorite_required,
        favorite_observation_blocker=favorite_blocker,
    )


def _favorite_requirement(
    *,
    course: Mapping[str, Any],
    course_evidence_status: str,
    source_deleted: bool,
    completion_conflict_status: str,
    completion_seq: int | None,
    completion_teacher: str | None,
) -> tuple[bool, str | None]:
    if source_deleted:
        return False, "COURSE_DELETED"
    if course_evidence_status == "SOURCE_CONFLICT":
        return False, "SOURCE_CONFLICT:TEACHER_REGION_OR_APPOINT"
    if course_evidence_status != "CONFIRMED":
        return False, "SOURCE_MISSING:TEACHER_REGION_OR_APPOINT"
    if completion_conflict_status == "PENDING":
        return False, "COMPLETION_CONFLICT_PENDING"
    if completion_seq is None or completion_teacher is None:
        return False, "COMPLETION_NOT_FROZEN"
    if _optional_iso_text(course.get("completion_end_time")) is None:
        return False, "SOURCE_MISSING:COMPLETION_END_TIME"
    if _optional_text(
        course.get("completion_student_token"),
        "DTS_V2_COURSE_SOURCE_WIDE_STUDENT_TOKEN_INVALID",
    ) is None:
        return False, "SOURCE_MISSING:COMPLETION_STUDENT_TOKEN"
    return True, None


def _course_evidence_status(course: Mapping[str, Any]) -> str:
    values = (
        course.get("appoint_evidence_status"),
        course.get("teacher_region_evidence_status"),
        course.get("evidence_status"),
    )
    if any(
        value not in {"CONFIRMED", "SOURCE_MISSING", "SOURCE_CONFLICT"}
        for value in values
    ):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_COURSE_EVIDENCE_INVALID"
        )
    appoint, teacher_region, combined = values
    expected = (
        "SOURCE_CONFLICT"
        if "SOURCE_CONFLICT" in {appoint, teacher_region}
        else "SOURCE_MISSING"
        if "SOURCE_MISSING" in {appoint, teacher_region}
        else "CONFIRMED"
    )
    if combined != expected:
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_COURSE_EVIDENCE_CONFLICT"
        )
    return str(combined)


def _teacher_region_provenance(
    *,
    source_region: str,
    participation: Mapping[str, Any],
) -> tuple[str | None, str, int | None, str | None]:
    status = participation.get("teacher_region_evidence_status")
    expected = participation.get("teacher_expected_source_region")
    revision = _optional_positive_int(
        participation.get("teacher_profile_source_row_revision"),
        "DTS_V2_COURSE_SOURCE_WIDE_TEACHER_PROFILE_REVISION_INVALID",
    )
    payload_hash = participation.get("teacher_profile_source_payload_hash")
    if payload_hash is not None and (
        not isinstance(payload_hash, str)
        or len(payload_hash) != 64
        or any(character not in "0123456789abcdef" for character in payload_hash)
    ):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_TEACHER_PROFILE_HASH_INVALID"
        )
    proof_grouped = (revision is None) is (payload_hash is None)
    if status == "SOURCE_MISSING":
        if expected is not None or not proof_grouped:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_TEACHER_REGION_PROOF_INVALID"
            )
    elif status in {"CONFIRMED", "SOURCE_CONFLICT"}:
        if (
            expected not in _REGIONS
            or revision is None
            or payload_hash is None
            or (status == "CONFIRMED") != (expected == source_region)
        ):
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_TEACHER_REGION_PROOF_INVALID"
            )
    else:
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_TEACHER_REGION_STATUS_INVALID"
        )
    return (
        None if expected is None else str(expected),
        str(status),
        revision,
        None if payload_hash is None else str(payload_hash),
    )


def _is_perfect(
    *,
    valid_for_scoring: bool,
    late: bool | None,
    early: bool | None,
    late_evidence: str,
    early_evidence: str,
) -> bool | None:
    if not valid_for_scoring:
        return False
    if (
        late_evidence != "CONFIRMED"
        or early_evidence != "CONFIRMED"
        or late is None
        or early is None
    ):
        return None
    return late is False and early is False


def _overall_evidence_status(
    *,
    course: Mapping[str, Any],
    fact: Mapping[str, Any] | None,
    participation: Mapping[str, Any],
    valid_for_scoring: bool,
) -> str:
    if course.get("evidence_status") == "SOURCE_CONFLICT":
        return "SOURCE_CONFLICT"
    if course.get("evidence_status") != "CONFIRMED":
        return "SOURCE_MISSING"
    if not valid_for_scoring:
        return "CONFIRMED"
    if fact is None:
        return "SOURCE_MISSING"
    statuses = (
        fact.get("grading_evidence_status"),
        fact.get("complaint_evidence_status"),
        fact.get("camera_evidence_status"),
        fact.get("cpu_evidence_status"),
        fact.get("network_evidence_status"),
        participation.get("late_evidence_status"),
        participation.get("early_evidence_status"),
    )
    return "CONFIRMED" if all(value == "CONFIRMED" for value in statuses) else "SOURCE_MISSING"


def _absence_provenance(
    *,
    source_region: str,
    participation: Mapping[str, Any],
) -> tuple[str | None, str | None, int | None, str | None]:
    source_id = _optional_text(
        participation.get("absence_source_id"),
        "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_SOURCE_ID_INVALID",
    )
    source_type = participation.get("absence_source_id_type")
    source_revision = _optional_positive_int(
        participation.get("absence_source_row_revision"),
        "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_SOURCE_REVISION_INVALID",
    )
    selected_reason = _optional_text(
        participation.get("absence_selected_reason_type"),
        "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_SELECTED_REASON_INVALID",
    )
    detail = _optional_text(
        participation.get("absence_reason_detail"),
        "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_INVALID",
    )
    present = source_id is not None
    if (
        (source_type is not None) != present
        or (source_revision is not None) != present
        or source_type not in ({"NUMERIC", "TEXT"} if present else {None})
        or selected_reason != detail
        or (present and source_region != "dom")
    ):
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_PROVENANCE_INVALID"
        )
    if present and source_type == "NUMERIC":
        try:
            numeric = Decimal(source_id)
        except (InvalidOperation, ValueError):
            numeric = None
        if numeric is None or not numeric.is_finite() or format(
            numeric.normalize(), "f"
        ) != source_id:
            raise DtsV2CourseSourceWidePlanError(
                "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_PROVENANCE_INVALID"
            )
    if detail is not None and not present:
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_ABSENCE_PROVENANCE_REQUIRED"
        )
    return source_id, source_type, source_revision, selected_reason


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{key.upper()}_INVALID"
        )
    return result


def _mapping_sequence(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ) or any(not isinstance(item, Mapping) for item in value):
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{name}_INVALID"
        )
    return tuple(dict(item) for item in value)


def _positive_int(value: Any, code: str) -> int:
    if type(value) is not int or value < 1:
        raise DtsV2CourseSourceWidePlanError(code)
    return value


def _optional_positive_int(value: Any, code: str) -> int | None:
    return None if value is None else _positive_int(value, code)


def _required_bool(value: Mapping[str, Any], key: str) -> bool:
    result = value.get(key)
    if type(result) is not bool:
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{key.upper()}_INVALID"
        )
    return result


def _optional_bool(value: Any, name: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{name}_INVALID"
        )
    return value


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


def _required_text(value: Mapping[str, Any], key: str, code: str) -> str:
    result = value.get(key)
    if not _nonempty(result):
        raise DtsV2CourseSourceWidePlanError(code)
    return str(result)


def _optional_text(value: Any, code: str) -> str | None:
    if value is None:
        return None
    if not _nonempty(value):
        raise DtsV2CourseSourceWidePlanError(code)
    return str(value)


def _optional_iso_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DtsV2CourseSourceWidePlanError(
            "DTS_V2_COURSE_SOURCE_WIDE_TEMPORAL_INVALID"
        )
    return value


def _evidence_status(value: Any, name: str) -> str:
    if value not in {"CONFIRMED", "SOURCE_MISSING"}:
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{name}_EVIDENCE_INVALID"
        )
    return str(value)


def _course_fact_text(
    fact: Mapping[str, Any] | None,
    key: str,
    *,
    default: str,
) -> str:
    if fact is None or fact.get(key) is None:
        return default
    value = fact.get(key)
    if not isinstance(value, str) or not value:
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{key.upper()}_INVALID"
        )
    return value


def _course_fact_optional_bool(
    fact: Mapping[str, Any] | None,
    key: str,
) -> bool | None:
    return None if fact is None else _optional_bool(fact.get(key), key.upper())


def _course_fact_optional_text(
    fact: Mapping[str, Any] | None,
    key: str,
) -> str | None:
    return (
        None
        if fact is None
        else _optional_text(
            fact.get(key),
            f"DTS_V2_COURSE_SOURCE_WIDE_{key.upper()}_INVALID",
        )
    )


def _course_fact_optional_id_type(
    fact: Mapping[str, Any] | None,
    key: str,
) -> str | None:
    if fact is None or fact.get(key) is None:
        return None
    value = fact.get(key)
    if value not in {"NUMERIC", "TEXT"}:
        raise DtsV2CourseSourceWidePlanError(
            f"DTS_V2_COURSE_SOURCE_WIDE_{key.upper()}_INVALID"
        )
    return str(value)


__all__ = [
    "CourseParticipationProjectionV2",
    "CourseSourceWidePlanV2",
    "DtsV2CourseSourceWidePlanError",
    "build_course_source_wide_plan_v2",
]
