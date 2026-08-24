"""Pure repeated-negative-label task aggregation for DTS v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from types import MappingProxyType
from typing import Literal, cast


TypedIdKind = Literal["NUMERIC", "TEXT"]
TeacherExecutionVariant = Literal["GENERAL", "TEACHING_ENVIRONMENT_PHOTO"]
CopyBlockerCode = Literal[
    "NONE",
    "TASK_COPY_CONFIG_MISSING",
    "TASK_COPY_CONFIG_CONFLICT",
    "NEGATIVE_LABEL_NAME_MISSING",
    "NEGATIVE_LABEL_VARIANT_CONFLICT",
]

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COPY_CONFIG_KEY = "teacher_personalized_copy"
_COPY_PAYLOAD_KEYS = frozenset(
    {
        "complaint_title_prefix",
        "negative_title_prefix",
        "fallback_titles",
        "complaint_category_to_en",
        "negative_label_to_en",
        "negative_label_execution_variant",
        "default_negative_execution_variant",
    }
)
_EXECUTION_VARIANTS = frozenset({"GENERAL", "TEACHING_ENVIRONMENT_PHOTO"})


class NegativeLabelDisposition(str, Enum):
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    PENDING_LABEL_NAME = "PENDING_LABEL_NAME"
    PENDING_LABEL_VARIANT_CONFLICT = "PENDING_LABEL_VARIANT_CONFLICT"
    PENDING_COPY_CONFIG_MISSING = "PENDING_COPY_CONFIG_MISSING"
    PENDING_COPY_CONFIG_CONFLICT = "PENDING_COPY_CONFIG_CONFLICT"
    READY = "READY"


@dataclass(frozen=True)
class _ParsedTeacherCopyPayload:
    payload: Mapping[str, object]
    negative_title_prefix: str
    negative_fallback_title: str
    negative_label_to_en: Mapping[str, str]
    negative_label_execution_variant: Mapping[str, TeacherExecutionVariant]
    default_negative_execution_variant: TeacherExecutionVariant


@dataclass(frozen=True)
class PublishedTeacherPersonalizedCopyV2:
    """One validated published ``teacher_personalized_copy`` payload.

    The published payload is keyed by the exact source ``label_name``.  It is
    deliberately not converted into a label-id mapping: ``label_id`` remains
    the stable task identity while names select copy and execution routing.
    """

    config_key: str
    version_id: str
    version_number: int
    payload_hash: str
    publication_status: str
    payload: Mapping[str, object] = field(repr=False)
    negative_title_prefix: str = field(init=False)
    negative_fallback_title: str = field(init=False)
    negative_label_to_en: Mapping[str, str] = field(init=False, repr=False)
    negative_label_execution_variant: Mapping[
        str,
        TeacherExecutionVariant,
    ] = field(init=False, repr=False)
    default_negative_execution_variant: TeacherExecutionVariant = field(
        init=False
    )

    def __post_init__(self) -> None:
        if self.config_key != _COPY_CONFIG_KEY:
            raise ValueError("teacher personalized copy config key is invalid")
        if not isinstance(self.version_id, str) or not self.version_id:
            raise ValueError("teacher personalized copy version id is invalid")
        if type(self.version_number) is not int or self.version_number < 1:
            raise ValueError("teacher personalized copy version number is invalid")
        if not isinstance(self.payload_hash, str) or not _SHA256_RE.fullmatch(
            self.payload_hash
        ):
            raise ValueError("teacher personalized copy payload hash is invalid")
        if self.publication_status != "PUBLISHED":
            raise ValueError("teacher personalized copy must be published")
        parsed = _parse_teacher_copy_payload(self.payload)
        object.__setattr__(self, "payload", parsed.payload)
        object.__setattr__(
            self,
            "negative_title_prefix",
            parsed.negative_title_prefix,
        )
        object.__setattr__(
            self,
            "negative_fallback_title",
            parsed.negative_fallback_title,
        )
        object.__setattr__(
            self,
            "negative_label_to_en",
            parsed.negative_label_to_en,
        )
        object.__setattr__(
            self,
            "negative_label_execution_variant",
            parsed.negative_label_execution_variant,
        )
        object.__setattr__(
            self,
            "default_negative_execution_variant",
            parsed.default_negative_execution_variant,
        )


@dataclass(frozen=True)
class FrozenNegativeLabelAssignmentV2:
    assignment_dedupe_key: str
    teacher_execution_variant: TeacherExecutionVariant
    teacher_copy_version_id: str
    teacher_copy_payload_hash: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.assignment_dedupe_key, str)
            or not self.assignment_dedupe_key
        ):
            raise ValueError("assignment dedupe key is invalid")
        if self.teacher_execution_variant not in {
            "GENERAL",
            "TEACHING_ENVIRONMENT_PHOTO",
        }:
            raise ValueError("assignment execution variant is invalid")
        if not isinstance(self.teacher_copy_version_id, str) or not (
            self.teacher_copy_version_id
        ):
            raise ValueError("assignment copy version is invalid")
        if not isinstance(
            self.teacher_copy_payload_hash,
            str,
        ) or not _SHA256_RE.fullmatch(self.teacher_copy_payload_hash):
            raise ValueError("assignment copy payload hash is invalid")


@dataclass(frozen=True)
class NegativeLabelEvidence:
    label_id: str
    label_id_type: TypedIdKind
    label_name: str | None

    def __post_init__(self) -> None:
        label_type, label_id = _canonical_id(self.label_id_type, self.label_id)
        if self.label_name is not None and not isinstance(self.label_name, str):
            raise ValueError("label_name must be text or None")
        object.__setattr__(self, "label_id_type", label_type)
        object.__setattr__(self, "label_id", label_id)
        if self.label_name == "":
            object.__setattr__(self, "label_name", None)


@dataclass(frozen=True)
class NegativeLabelCourseFact:
    source_region: Literal["dom", "ovs"]
    source_appoint_id: str
    source_appoint_id_type: TypedIdKind
    completion_teacher_id: str | None
    completion_teacher_id_type: TypedIdKind | None
    completion_participation_seq: int | None
    grading_classification: str | None
    labels: tuple[NegativeLabelEvidence, ...]

    def __post_init__(self) -> None:
        if self.source_region not in {"dom", "ovs"}:
            raise ValueError("source region is invalid")
        appoint_type, appoint_id = _canonical_id(
            self.source_appoint_id_type,
            self.source_appoint_id,
        )
        if self.completion_teacher_id is None:
            if self.completion_teacher_id_type is not None:
                raise ValueError("completion teacher type requires teacher id")
            if self.completion_participation_seq is not None:
                raise ValueError(
                    "completion participation requires completion teacher"
                )
        elif self.completion_teacher_id_type is None:
            raise ValueError("completion teacher id requires typed evidence")
        elif (
            type(self.completion_participation_seq) is not int
            or self.completion_participation_seq < 1
        ):
            raise ValueError(
                "completion teacher requires a positive participation seq"
            )
        else:
            teacher_type, teacher_id = _canonical_id(
                self.completion_teacher_id_type,
                self.completion_teacher_id,
            )
            object.__setattr__(self, "completion_teacher_id_type", teacher_type)
            object.__setattr__(self, "completion_teacher_id", teacher_id)
        if self.grading_classification not in {None, "POSITIVE", "NEGATIVE"}:
            raise ValueError("grading classification is invalid")
        object.__setattr__(self, "source_appoint_id_type", appoint_type)
        object.__setattr__(self, "source_appoint_id", appoint_id)

    @property
    def course_key(self) -> tuple[str, TypedIdKind, str]:
        return (
            self.source_region,
            self.source_appoint_id_type,
            self.source_appoint_id,
        )


@dataclass(frozen=True)
class NegativeLabelCourseContribution:
    source_region: str
    source_appoint_id: str
    source_appoint_id_type: TypedIdKind
    completion_participation_seq: int
    label_name: str | None
    mapped_label_english: str | None
    label_mapping_hit: bool | None
    display_title: str | None
    teacher_execution_variant: TeacherExecutionVariant | None

    @property
    def course_key(self) -> tuple[str, TypedIdKind, str]:
        return (
            self.source_region,
            self.source_appoint_id_type,
            self.source_appoint_id,
        )


@dataclass(frozen=True)
class NegativeLabelTaskEvaluation:
    teacher_id: str
    teacher_id_type: TypedIdKind
    label_id: str
    label_id_type: TypedIdKind
    disposition: NegativeLabelDisposition
    contributions: tuple[NegativeLabelCourseContribution, ...]
    teacher_execution_variant: TeacherExecutionVariant | None
    assignment_dedupe_key: str
    pending_dedupe_key: str | None
    blocker_code: CopyBlockerCode
    teacher_copy_version_id: str | None
    teacher_copy_version_number: int | None
    teacher_copy_payload_hash: str | None
    teacher_copy_candidate_version_ids: tuple[str, ...]
    existing_assignment: FrozenNegativeLabelAssignmentV2 | None

    @property
    def threshold_met(self) -> bool:
        return len(self.contributions) >= 2

    @property
    def assignment_teacher_execution_variant(
        self,
    ) -> TeacherExecutionVariant | None:
        """Return the immutable existing value or the new-plan configured value."""

        if self.existing_assignment is not None:
            return self.existing_assignment.teacher_execution_variant
        return self.teacher_execution_variant


def evaluate_repeated_negative_labels(
    courses: Iterable[NegativeLabelCourseFact],
    *,
    copy_configs: Iterable[PublishedTeacherPersonalizedCopyV2],
    existing_assignments: Iterable[FrozenNegativeLabelAssignmentV2] = (),
) -> tuple[NegativeLabelTaskEvaluation, ...]:
    """Group negative labels using one explicit published copy configuration."""

    configs = tuple(copy_configs)
    if any(
        not isinstance(config, PublishedTeacherPersonalizedCopyV2)
        for config in configs
    ):
        raise ValueError("teacher personalized copy config is invalid")
    candidate_version_ids = tuple(
        sorted(
            (config.version_id for config in configs),
            key=lambda value: value.encode("utf-8"),
        )
    )
    selected_config = configs[0] if len(configs) == 1 else None
    assignments_by_key: dict[str, FrozenNegativeLabelAssignmentV2] = {}
    for assignment in existing_assignments:
        if not isinstance(assignment, FrozenNegativeLabelAssignmentV2):
            raise ValueError("existing negative-label assignment is invalid")
        if assignment.assignment_dedupe_key in assignments_by_key:
            raise ValueError("existing negative-label assignment is duplicated")
        assignments_by_key[assignment.assignment_dedupe_key] = assignment

    grouped: dict[
        tuple[TypedIdKind, str, TypedIdKind, str],
        dict[tuple[str, TypedIdKind, str], NegativeLabelCourseContribution],
    ] = {}
    for course in courses:
        if (
            course.source_region != "dom"
            or course.grading_classification != "NEGATIVE"
            or course.completion_teacher_id is None
            or course.completion_teacher_id_type is None
            or course.completion_participation_seq is None
        ):
            continue
        seen_labels: set[tuple[TypedIdKind, str]] = set()
        for label in course.labels:
            label_key = (label.label_id_type, label.label_id)
            if label_key in seen_labels:
                continue
            seen_labels.add(label_key)
            group_key = (
                course.completion_teacher_id_type,
                course.completion_teacher_id,
                label.label_id_type,
                label.label_id,
            )
            grouped.setdefault(group_key, {})[course.course_key] = (
                NegativeLabelCourseContribution(
                    source_region=course.source_region,
                    source_appoint_id=course.source_appoint_id,
                    source_appoint_id_type=course.source_appoint_id_type,
                    completion_participation_seq=(
                        course.completion_participation_seq
                    ),
                    label_name=label.label_name,
                    mapped_label_english=None,
                    label_mapping_hit=None,
                    display_title=None,
                    teacher_execution_variant=None,
                )
            )

    evaluations: list[NegativeLabelTaskEvaluation] = []
    for group_key in sorted(grouped, key=_group_sort_key):
        teacher_type, teacher_id, label_type, label_id = group_key
        contributions = tuple(
            sorted(grouped[group_key].values(), key=_contribution_sort_key)
        )
        assignment_key = f"personalized:P-FB-NEGATIVE:{teacher_id}:{label_id}"
        disposition = NegativeLabelDisposition.BELOW_THRESHOLD
        variant: TeacherExecutionVariant | None = None
        pending_key: str | None = None
        blocker_code: CopyBlockerCode = "NONE"
        if len(contributions) >= 2:
            if not configs:
                disposition = NegativeLabelDisposition.PENDING_COPY_CONFIG_MISSING
                blocker_code = "TASK_COPY_CONFIG_MISSING"
            elif len(configs) != 1:
                disposition = NegativeLabelDisposition.PENDING_COPY_CONFIG_CONFLICT
                blocker_code = "TASK_COPY_CONFIG_CONFLICT"
            else:
                assert selected_config is not None
                contributions = tuple(
                    _apply_negative_copy(item, selected_config)
                    for item in contributions
                )
            if blocker_code == "NONE" and any(
                item.label_name is None for item in contributions
            ):
                disposition = NegativeLabelDisposition.PENDING_LABEL_NAME
                pending_key = (
                    f"negative-label-name-missing:{teacher_id}:{label_id}"
                )
                blocker_code = "NEGATIVE_LABEL_NAME_MISSING"
            elif blocker_code == "NONE":
                variants = {
                    item.teacher_execution_variant for item in contributions
                }
                if None in variants:
                    raise AssertionError("non-empty label name has no copy variant")
                if len(variants) != 1:
                    disposition = (
                        NegativeLabelDisposition.PENDING_LABEL_VARIANT_CONFLICT
                    )
                    pending_key = (
                        f"negative-label-variant-conflict:{teacher_id}:{label_id}"
                    )
                    blocker_code = "NEGATIVE_LABEL_VARIANT_CONFLICT"
                else:
                    variant = cast(TeacherExecutionVariant, next(iter(variants)))
                    disposition = NegativeLabelDisposition.READY
        evaluations.append(
            NegativeLabelTaskEvaluation(
                teacher_id=teacher_id,
                teacher_id_type=teacher_type,
                label_id=label_id,
                label_id_type=label_type,
                disposition=disposition,
                contributions=contributions,
                teacher_execution_variant=variant,
                assignment_dedupe_key=assignment_key,
                pending_dedupe_key=pending_key,
                blocker_code=blocker_code,
                teacher_copy_version_id=(
                    selected_config.version_id
                    if selected_config is not None
                    else None
                ),
                teacher_copy_version_number=(
                    selected_config.version_number
                    if selected_config is not None
                    else None
                ),
                teacher_copy_payload_hash=(
                    selected_config.payload_hash
                    if selected_config is not None
                    else None
                ),
                teacher_copy_candidate_version_ids=candidate_version_ids,
                existing_assignment=assignments_by_key.get(assignment_key),
            )
        )
    return tuple(evaluations)


def negative_label_course_match_key(
    evaluation: NegativeLabelTaskEvaluation,
    contribution: NegativeLabelCourseContribution,
) -> str:
    return (
        f"negative-label:{evaluation.teacher_id}:{evaluation.label_id}:"
        f"{contribution.source_region}:{contribution.source_appoint_id}"
    )


def _apply_negative_copy(
    contribution: NegativeLabelCourseContribution,
    config: PublishedTeacherPersonalizedCopyV2,
) -> NegativeLabelCourseContribution:
    label_name = contribution.label_name
    if label_name is None:
        return contribution
    mapped_english = config.negative_label_to_en.get(label_name)
    mapping_hit = mapped_english is not None
    display_title = (
        f"{config.negative_title_prefix}{mapped_english}"
        if mapped_english is not None
        else config.negative_fallback_title
    )
    variant = config.negative_label_execution_variant.get(
        label_name,
        config.default_negative_execution_variant,
    )
    return replace(
        contribution,
        mapped_label_english=mapped_english,
        label_mapping_hit=mapping_hit,
        display_title=display_title,
        teacher_execution_variant=variant,
    )


def _parse_teacher_copy_payload(
    payload: Mapping[str, object],
) -> _ParsedTeacherCopyPayload:
    if not isinstance(payload, Mapping) or set(payload) != _COPY_PAYLOAD_KEYS:
        raise ValueError("teacher personalized copy payload schema is invalid")

    complaint_title_prefix = _copy_text(
        payload["complaint_title_prefix"],
        field_name="complaint_title_prefix",
    )
    negative_title_prefix = _copy_text(
        payload["negative_title_prefix"],
        field_name="negative_title_prefix",
    )
    fallback_titles_raw = payload["fallback_titles"]
    if not isinstance(fallback_titles_raw, Mapping) or set(
        fallback_titles_raw
    ) != {"complaint", "negative"}:
        raise ValueError("teacher personalized copy fallback titles are invalid")
    fallback_titles = {
        "complaint": _copy_text(
            fallback_titles_raw["complaint"],
            field_name="fallback_titles.complaint",
        ),
        "negative": _copy_text(
            fallback_titles_raw["negative"],
            field_name="fallback_titles.negative",
        ),
    }
    complaint_category_to_en = _copy_text_mapping(
        payload["complaint_category_to_en"],
        field_name="complaint_category_to_en",
    )
    negative_label_to_en = _copy_text_mapping(
        payload["negative_label_to_en"],
        field_name="negative_label_to_en",
    )
    variants_raw = _copy_text_mapping(
        payload["negative_label_execution_variant"],
        field_name="negative_label_execution_variant",
    )
    if any(value not in _EXECUTION_VARIANTS for value in variants_raw.values()):
        raise ValueError("teacher personalized copy execution variant is invalid")
    if not set(variants_raw).issubset(negative_label_to_en):
        raise ValueError(
            "teacher personalized copy execution mapping requires English copy"
        )
    default_variant_raw = _copy_text(
        payload["default_negative_execution_variant"],
        field_name="default_negative_execution_variant",
    )
    if default_variant_raw != "GENERAL":
        raise ValueError("teacher personalized copy default variant is invalid")
    default_variant = cast(TeacherExecutionVariant, default_variant_raw)
    variants = {
        key: cast(TeacherExecutionVariant, value)
        for key, value in variants_raw.items()
    }
    frozen_payload: Mapping[str, object] = MappingProxyType(
        {
            "complaint_title_prefix": complaint_title_prefix,
            "negative_title_prefix": negative_title_prefix,
            "fallback_titles": MappingProxyType(fallback_titles),
            "complaint_category_to_en": MappingProxyType(
                complaint_category_to_en
            ),
            "negative_label_to_en": MappingProxyType(negative_label_to_en),
            "negative_label_execution_variant": MappingProxyType(variants),
            "default_negative_execution_variant": default_variant,
        }
    )
    return _ParsedTeacherCopyPayload(
        payload=frozen_payload,
        negative_title_prefix=negative_title_prefix,
        negative_fallback_title=fallback_titles["negative"],
        negative_label_to_en=MappingProxyType(negative_label_to_en),
        negative_label_execution_variant=MappingProxyType(variants),
        default_negative_execution_variant=default_variant,
    )


def _copy_text(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value == ""
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    ):
        raise ValueError(f"teacher personalized copy {field_name} is invalid")
    return value


def _copy_text_mapping(value: object, *, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"teacher personalized copy {field_name} is invalid")
    result: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = _copy_text(key, field_name=f"{field_name}.key")
        normalized_value = _copy_text(
            item,
            field_name=f"{field_name}.{normalized_key}",
        )
        result[normalized_key] = normalized_value
    return result


def _canonical_id(source_type: str, value: str) -> tuple[TypedIdKind, str]:
    normalized_type = source_type.upper() if isinstance(source_type, str) else ""
    if normalized_type == "TEXT":
        if not isinstance(value, str) or value == "":
            raise ValueError("text id is invalid")
        return "TEXT", value
    if normalized_type != "NUMERIC" or isinstance(value, bool):
        raise ValueError("id type is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric id is invalid") from exc
    if not number.is_finite():
        raise ValueError("numeric id is invalid")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "NUMERIC", "0" if rendered in {"", "-0"} else rendered


def _typed_sort_value(source_type: TypedIdKind, value: str) -> Decimal | bytes:
    return Decimal(value) if source_type == "NUMERIC" else value.encode("utf-8")


def _group_sort_key(
    key: tuple[TypedIdKind, str, TypedIdKind, str],
) -> tuple[object, ...]:
    teacher_type, teacher_id, label_type, label_id = key
    return (
        teacher_type,
        _typed_sort_value(teacher_type, teacher_id),
        label_type,
        _typed_sort_value(label_type, label_id),
    )


def _contribution_sort_key(
    contribution: NegativeLabelCourseContribution,
) -> tuple[object, ...]:
    return (
        contribution.source_region,
        contribution.source_appoint_id_type,
        _typed_sort_value(
            contribution.source_appoint_id_type,
            contribution.source_appoint_id,
        ),
    )
