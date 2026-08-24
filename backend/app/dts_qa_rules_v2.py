"""Pure v2 QA current facts and camera-off notification routing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
from typing import Literal


TypedIdKind = Literal["NUMERIC", "TEXT"]
EvidenceStatus = Literal["CONFIRMED", "SOURCE_MISSING"]


class CameraRouteDisposition(str, Enum):
    INACTIVE = "INACTIVE"
    SOURCE_MISSING = "SOURCE_MISSING"
    WAITING_COMPLETION = "WAITING_COMPLETION"
    ROUTED = "ROUTED"


@dataclass(frozen=True)
class CourseQaCurrentV2:
    """QA current state while CPU/network have no approved source."""

    is_camera_off: bool | None
    camera_evidence_status: EvidenceStatus
    is_cpu_usage_high: None = None
    cpu_evidence_status: Literal["SOURCE_MISSING"] = "SOURCE_MISSING"
    is_network_delay_high: None = None
    network_evidence_status: Literal["SOURCE_MISSING"] = "SOURCE_MISSING"


def build_course_qa_current_v2(
    *,
    is_camera_off: bool | None,
) -> CourseQaCurrentV2:
    """Build QA facts without consulting the retired CPU/network source."""

    if is_camera_off is not None and type(is_camera_off) is not bool:
        raise ValueError("camera-off state must be bool or None")
    return CourseQaCurrentV2(
        is_camera_off=is_camera_off,
        camera_evidence_status=(
            "CONFIRMED" if is_camera_off is not None else "SOURCE_MISSING"
        ),
    )


@dataclass(frozen=True)
class CameraRoutingInputV2:
    source_region: Literal["dom", "ovs"]
    source_appoint_id: str
    source_appoint_id_type: TypedIdKind
    is_camera_off: bool | None
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
        if self.is_camera_off is not None and type(self.is_camera_off) is not bool:
            raise ValueError("camera-off state must be bool or None")
        if self.completion_teacher_id is None:
            if (
                self.completion_teacher_id_type is not None
                or self.completion_participation_seq is not None
            ):
                raise ValueError("completion pointer is incomplete")
        else:
            if (
                self.completion_teacher_id_type is None
                or type(self.completion_participation_seq) is not int
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
class CameraRoutingResultV2:
    disposition: CameraRouteDisposition
    teacher_id: str | None = None
    teacher_id_type: TypedIdKind | None = None
    match_key: str | None = None
    source_ref: str | None = None
    notification_id: str | None = None
    error_code: str | None = None


def route_camera_off_v2(
    camera: CameraRoutingInputV2,
) -> CameraRoutingResultV2:
    """Route camera-off only after the course has a frozen completion owner."""

    if camera.is_camera_off is None:
        return CameraRoutingResultV2(
            disposition=CameraRouteDisposition.SOURCE_MISSING,
            error_code="SOURCE_MISSING:CAMERA_CURRENT_SET_INCOMPLETE",
        )
    if camera.is_camera_off is False:
        return CameraRoutingResultV2(
            disposition=CameraRouteDisposition.INACTIVE,
        )
    if camera.completion_teacher_id is None:
        return CameraRoutingResultV2(
            disposition=CameraRouteDisposition.WAITING_COMPLETION,
            error_code="WAITING_DEPENDENCY:COURSE_COMPLETION_REQUIRED",
        )

    assert camera.completion_participation_seq is not None
    assert camera.completion_teacher_id_type is not None
    common = (
        f"{camera.source_region}:{camera.source_appoint_id}:"
        f"{camera.completion_participation_seq}"
    )
    match_key = f"camera-off:TR-QUALITY-CAMERA-OFF:{common}"
    source_ref = f"camera-notification:TR-QUALITY-CAMERA-OFF:{common}"
    notification_id = "v2notif:" + hashlib.sha256(
        source_ref.encode("utf-8")
    ).hexdigest()
    return CameraRoutingResultV2(
        disposition=CameraRouteDisposition.ROUTED,
        teacher_id=camera.completion_teacher_id,
        teacher_id_type=camera.completion_teacher_id_type,
        match_key=match_key,
        source_ref=source_ref,
        notification_id=notification_id,
    )


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
