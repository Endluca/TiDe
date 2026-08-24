from __future__ import annotations

import hashlib

import pytest

from app.dts_qa_rules_v2 import (
    CameraRouteDisposition,
    CameraRoutingInputV2,
    build_course_qa_current_v2,
    route_camera_off_v2,
)


def _camera(
    *,
    is_camera_off: bool | None,
    teacher_id: str | None = "007",
) -> CameraRoutingInputV2:
    return CameraRoutingInputV2(
        source_region="dom",
        source_appoint_id="009.0",
        source_appoint_id_type="NUMERIC",
        is_camera_off=is_camera_off,
        completion_teacher_id=teacher_id,
        completion_teacher_id_type="NUMERIC" if teacher_id is not None else None,
        completion_participation_seq=2 if teacher_id is not None else None,
    )


@pytest.mark.parametrize("camera_state", [True, False, None])
def test_cpu_and_network_remain_source_missing_until_a_new_source_is_approved(
    camera_state: bool | None,
) -> None:
    current = build_course_qa_current_v2(is_camera_off=camera_state)

    assert current.is_camera_off is camera_state
    assert current.camera_evidence_status == (
        "CONFIRMED" if camera_state is not None else "SOURCE_MISSING"
    )
    assert current.is_cpu_usage_high is None
    assert current.cpu_evidence_status == "SOURCE_MISSING"
    assert current.is_network_delay_high is None
    assert current.network_evidence_status == "SOURCE_MISSING"


def test_camera_event_is_kept_before_end_but_output_waits_for_completion() -> None:
    result = route_camera_off_v2(
        _camera(is_camera_off=True, teacher_id=None)
    )

    assert result.disposition == CameraRouteDisposition.WAITING_COMPLETION
    assert result.error_code == "WAITING_DEPENDENCY:COURSE_COMPLETION_REQUIRED"
    assert result.match_key is None


def test_camera_off_routes_to_the_frozen_completion_participation() -> None:
    result = route_camera_off_v2(_camera(is_camera_off=True))
    source_ref = "camera-notification:TR-QUALITY-CAMERA-OFF:dom:9:2"

    assert result.disposition == CameraRouteDisposition.ROUTED
    assert result.teacher_id == "7"
    assert result.teacher_id_type == "NUMERIC"
    assert result.match_key == "camera-off:TR-QUALITY-CAMERA-OFF:dom:9:2"
    assert result.source_ref == source_ref
    assert result.notification_id == "v2notif:" + hashlib.sha256(
        source_ref.encode("utf-8")
    ).hexdigest()


def test_false_and_unknown_camera_states_never_create_an_output() -> None:
    inactive = route_camera_off_v2(_camera(is_camera_off=False))
    missing = route_camera_off_v2(_camera(is_camera_off=None))

    assert inactive.disposition == CameraRouteDisposition.INACTIVE
    assert inactive.source_ref is None
    assert missing.disposition == CameraRouteDisposition.SOURCE_MISSING
    assert missing.source_ref is None


@pytest.mark.parametrize("invalid_seq", [True, 0, -1, 1.0, "1"])
def test_completion_pointer_requires_a_strict_positive_integer(
    invalid_seq: object,
) -> None:
    with pytest.raises(ValueError, match="completion pointer"):
        CameraRoutingInputV2(
            source_region="ovs",
            source_appoint_id="A-1",
            source_appoint_id_type="TEXT",
            is_camera_off=True,
            completion_teacher_id="T1",
            completion_teacher_id_type="TEXT",
            completion_participation_seq=invalid_seq,  # type: ignore[arg-type]
        )


def test_typed_course_identity_does_not_trim_or_retag_text_ids() -> None:
    result = route_camera_off_v2(
        CameraRoutingInputV2(
            source_region="ovs",
            source_appoint_id="009",
            source_appoint_id_type="TEXT",
            is_camera_off=True,
            completion_teacher_id="T1",
            completion_teacher_id_type="TEXT",
            completion_participation_seq=1,
        )
    )

    assert result.match_key == "camera-off:TR-QUALITY-CAMERA-OFF:ovs:009:1"
