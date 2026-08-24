"""Pure routing of the selected latest valid course complaint for DTS v2."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Literal, Mapping
import unicodedata

from .dts_business_rules_v2 import complaint_is_valid


TypedIdKind = Literal["NUMERIC", "TEXT"]
OutputType = Literal["TEACHER_TASK", "NOTIFICATION", "OPS_CASE"]


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMPLAINT_RULE_ID_RE = re.compile(
    r"complaint-rule:(?P<source_sha256>[0-9a-f]{64}):(?P<source_row_number>[1-9][0-9]*)"
)


class ComplaintRouteDisposition(str, Enum):
    NOT_VALID = "NOT_VALID"
    WAITING_COMPLETION = "WAITING_COMPLETION"
    PENDING_CATEGORY = "PENDING_CATEGORY"
    SOURCE_MISSING_CATEGORY = "SOURCE_MISSING_CATEGORY"
    ROUTED = "ROUTED"


@dataclass(frozen=True)
class ComplaintCategorySnapshotV2:
    """One typed snapshot of an authoritative DOM category dictionary row."""

    source_region: Literal["dom"]
    category_id: str
    category_id_type: TypedIdKind
    cate_cn_name: str | None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        if self.source_region != "dom":
            raise ValueError("complaint category dictionary must be DOM")
        category_type, category_id = _canonical_id(
            self.category_id_type,
            self.category_id,
        )
        if self.cate_cn_name is not None and not isinstance(
            self.cate_cn_name,
            str,
        ):
            raise ValueError("complaint category name is invalid")
        if type(self.is_deleted) is not bool:
            raise ValueError("complaint category deletion flag is invalid")
        object.__setattr__(self, "category_id_type", category_type)
        object.__setattr__(self, "category_id", category_id)


@dataclass(frozen=True)
class ComplaintCategoryRuleV2:
    category_l3_normalized: str
    severity_rank: int
    complaint_rule_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.category_l3_normalized, str)
            or not self.category_l3_normalized
            or _normalize_category_l3(self.category_l3_normalized)
            != self.category_l3_normalized
        ):
            raise ValueError("complaint rule category_l3_normalized is invalid")
        if (
            isinstance(self.severity_rank, bool)
            or not isinstance(self.severity_rank, int)
            or self.severity_rank not in {0, 1, 2, 3, 4}
        ):
            raise ValueError("complaint severity rank is invalid")
        if not isinstance(self.source_sha256, str) or not _SHA256_RE.fullmatch(
            self.source_sha256
        ):
            raise ValueError("complaint rule identity is invalid")
        rule_id_match = (
            _COMPLAINT_RULE_ID_RE.fullmatch(self.complaint_rule_id)
            if isinstance(self.complaint_rule_id, str)
            else None
        )
        if (
            rule_id_match is None
            or rule_id_match.group("source_sha256") != self.source_sha256
        ):
            raise ValueError("complaint rule identity is invalid")


@dataclass(frozen=True)
class ComplaintRoutingInputV2:
    source_region: Literal["dom", "ovs"]
    source_appoint_id: str
    source_appoint_id_type: TypedIdKind
    complaint_row: Mapping[str, object]
    child_id: str | None
    child_id_type: TypedIdKind | None
    grandson_id: str | None
    grandson_id_type: TypedIdKind | None
    completion_teacher_id: str | None
    completion_teacher_id_type: TypedIdKind | None
    completion_participation_seq: int | None

    def __post_init__(self) -> None:
        if self.source_region not in {"dom", "ovs"}:
            raise ValueError("source region is invalid")
        appoint_type, appoint_id = _canonical_id(
            self.source_appoint_id_type,
            self.source_appoint_id,
        )
        child_type, child_id = _canonical_optional_id(
            self.child_id,
            self.child_id_type,
            field_name="child",
        )
        object.__setattr__(self, "child_id_type", child_type)
        object.__setattr__(self, "child_id", child_id)
        if self.grandson_id is None:
            if self.grandson_id_type is not None:
                raise ValueError("grandson id type requires an id")
        elif self.grandson_id_type is None:
            raise ValueError("grandson id requires typed evidence")
        else:
            grandson_type, grandson_id = _canonical_id(
                self.grandson_id_type,
                self.grandson_id,
            )
            object.__setattr__(self, "grandson_id_type", grandson_type)
            object.__setattr__(self, "grandson_id", grandson_id)
        _require_complaint_category_matches_typed_identity(
            complaint_row=self.complaint_row,
            source_field="complaint_type_child",
            category_id=self.child_id,
            category_id_type=self.child_id_type,
        )
        _require_complaint_category_matches_typed_identity(
            complaint_row=self.complaint_row,
            source_field="complaint_type_grandson",
            category_id=self.grandson_id,
            category_id_type=self.grandson_id_type,
        )
        if self.completion_teacher_id is None:
            if (
                self.completion_teacher_id_type is not None
                or self.completion_participation_seq is not None
            ):
                raise ValueError("completion pointer is incomplete")
        else:
            if (
                self.completion_teacher_id_type is None
                or self.completion_participation_seq is None
                or isinstance(self.completion_participation_seq, bool)
                or not isinstance(self.completion_participation_seq, int)
                or self.completion_participation_seq < 1
            ):
                raise ValueError("completion pointer is incomplete")
            teacher_type, teacher_id = _canonical_id(
                self.completion_teacher_id_type,
                self.completion_teacher_id,
            )
            object.__setattr__(self, "completion_teacher_id_type", teacher_type)
            object.__setattr__(self, "completion_teacher_id", teacher_id)
        object.__setattr__(self, "source_appoint_id_type", appoint_type)
        object.__setattr__(self, "source_appoint_id", appoint_id)


@dataclass(frozen=True)
class ComplaintRoutingResultV2:
    disposition: ComplaintRouteDisposition
    rule_code: str | None = None
    output_type: OutputType | None = None
    task_code: str | None = None
    match_key: str | None = None
    output_key: str | None = None
    error_code: str | None = None
    complaint_rule_id: str | None = None
    source_sha256: str | None = None
    category_l2: str | None = None
    category_l3_normalized: str | None = None


def route_current_complaint_v2(
    complaint: ComplaintRoutingInputV2,
    *,
    category_rule: ComplaintCategoryRuleV2 | None,
    child_category: ComplaintCategorySnapshotV2 | None = None,
    grandson_category: ComplaintCategorySnapshotV2 | None = None,
) -> ComplaintRoutingResultV2:
    """Route only the already-selected latest valid complaint for one course."""

    if not complaint_is_valid(complaint.complaint_row):
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.NOT_VALID
        )
    child_name, child_error = _resolve_category_name(
        category_id=complaint.child_id,
        category_id_type=complaint.child_id_type,
        category=child_category,
    )
    if child_error is not None:
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.SOURCE_MISSING_CATEGORY,
            error_code=child_error,
        )
    if complaint.grandson_id is None or complaint.grandson_id_type is None:
        if grandson_category is not None:
            raise ValueError(
                "complaint grandson dictionary snapshot does not match source row"
            )
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.PENDING_CATEGORY,
            error_code="PENDING_DATA:COMPLAINT_CATEGORY_MISSING",
        )
    grandson_name, grandson_error = _resolve_category_name(
        category_id=complaint.grandson_id,
        category_id_type=complaint.grandson_id_type,
        category=grandson_category,
    )
    if grandson_error is not None:
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.SOURCE_MISSING_CATEGORY,
            error_code=grandson_error,
        )
    assert grandson_name is not None
    category_l3_normalized = _normalize_category_l3(grandson_name)
    if not category_l3_normalized:
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.SOURCE_MISSING_CATEGORY,
            error_code="SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND",
        )
    if category_rule is None:
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.PENDING_CATEGORY,
            error_code="PENDING_DATA:COMPLAINT_CATEGORY_RULE_MISSING",
            category_l2=child_name,
            category_l3_normalized=category_l3_normalized,
        )
    if category_rule.category_l3_normalized != category_l3_normalized:
        raise ValueError(
            "complaint rule does not match the normalized level-3 category"
        )
    if complaint.completion_teacher_id is None:
        return ComplaintRoutingResultV2(
            disposition=ComplaintRouteDisposition.WAITING_COMPLETION,
            error_code="WAITING_DEPENDENCY:COURSE_COMPLETION_REQUIRED",
            category_l2=child_name,
            category_l3_normalized=category_l3_normalized,
        )

    assert complaint.completion_participation_seq is not None
    assert complaint.completion_teacher_id is not None
    common = (
        f"{complaint.source_region}:{complaint.source_appoint_id}:"
        f"{complaint.completion_participation_seq}:{complaint.grandson_id}"
    )
    if child_name == "出席问题":
        rule_code = "TR-REL-ATTENDANCE"
        output_type: OutputType = "TEACHER_TASK"
        task_code = "P-REL-ATTENDANCE"
        output_key = (
            f"personalized:P-REL-ATTENDANCE:"
            f"{complaint.completion_teacher_id}"
        )
    elif child_name == "网络设备问题":
        rule_code = "TR-QUALITY-NETWORK-EQUIPMENT"
        output_type = "NOTIFICATION"
        task_code = None
        output_key = f"complaint-notification:{rule_code}:{common}"
    elif category_rule.severity_rank in {0, 1}:
        rule_code = "TR-FB-SEVERE-COMPLAINT"
        output_type = "OPS_CASE"
        task_code = None
        output_key = f"complaint-case:{rule_code}:{common}"
    else:
        rule_code = "TR-FB-GENERAL-COMPLAINT"
        output_type = "TEACHER_TASK"
        task_code = "P-FB-COMPLAINT"
        output_key = (
            f"personalized:P-FB-COMPLAINT:"
            f"{complaint.completion_teacher_id}:{complaint.grandson_id}"
        )
    return ComplaintRoutingResultV2(
        disposition=ComplaintRouteDisposition.ROUTED,
        rule_code=rule_code,
        output_type=output_type,
        task_code=task_code,
        match_key=f"complaint:{rule_code}:{common}",
        output_key=output_key,
        complaint_rule_id=category_rule.complaint_rule_id,
        source_sha256=category_rule.source_sha256,
        category_l2=child_name,
        category_l3_normalized=category_l3_normalized,
    )


def _resolve_category_name(
    *,
    category_id: str | None,
    category_id_type: TypedIdKind | None,
    category: ComplaintCategorySnapshotV2 | None,
) -> tuple[str | None, str | None]:
    if category_id is None or category_id_type is None:
        if category is not None:
            raise ValueError(
                "complaint category dictionary snapshot does not match source row"
            )
        return None, None
    if category is None or category.is_deleted:
        return None, "SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND"
    if (
        category.category_id_type != category_id_type
        or category.category_id != category_id
    ):
        raise ValueError(
            "complaint category dictionary snapshot does not match source row"
        )
    if category.cate_cn_name is None or category.cate_cn_name == "":
        return None, "SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND"
    return category.cate_cn_name, None


def _require_complaint_category_matches_typed_identity(
    *,
    complaint_row: Mapping[str, object],
    source_field: str,
    category_id: str | None,
    category_id_type: TypedIdKind | None,
) -> None:
    raw_category_id = complaint_row.get(source_field)
    if raw_category_id is None:
        if category_id is not None:
            raise ValueError("complaint category identity does not match source row")
        return
    if category_id is None or category_id_type is None:
        raise ValueError("complaint category identity does not match source row")
    raw_type, raw_id = _canonical_id(category_id_type, raw_category_id)
    if raw_type != category_id_type or raw_id != category_id:
        raise ValueError("complaint category identity does not match source row")


def _canonical_optional_id(
    value: str | None,
    source_type: TypedIdKind | None,
    *,
    field_name: str,
) -> tuple[TypedIdKind | None, str | None]:
    if value is None:
        if source_type is not None:
            raise ValueError(f"{field_name} id type requires an id")
        return None, None
    if source_type is None:
        raise ValueError(f"{field_name} id requires typed evidence")
    return _canonical_id(source_type, value)


def _normalize_category_l3(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return re.sub(r"\s+", " ", normalized)


def _canonical_id(source_type: str, value: object) -> tuple[TypedIdKind, str]:
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
