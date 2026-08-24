from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.dts_v2_domain_aggregate import build_domain_aggregate_identity_v2
from app.dts_v2_outbox_worker import DtsV2OutboxEvent
from app.dts_v2_teacher_student_outbox_processor import (
    DtsV2TeacherStudentOutboxProcessor,
    DtsV2TeacherStudentOutboxProcessorError,
    build_teacher_student_materialization_plan_v2,
)


DOM_STUDENT = "dom:v1:" + "a" * 64
UTC = timezone.utc


def _key() -> dict[str, str]:
    return {
        "source_region": "dom",
        "teacher_id": "100",
        "student_token": DOM_STUDENT,
    }


def _requirement(
    *,
    appoint_id: str = "9001",
    end_time: datetime | None = datetime(2026, 8, 20, tzinfo=UTC),
    status: str = "CONFIRMED_TRUE",
) -> dict[str, object]:
    observed_at = end_time + timedelta(hours=24) if end_time else None
    evidence: dict[str, tuple[object, str, str | None]] = {
        "PENDING": (None, "PENDING", None),
        "CONFIRMED_TRUE": (True, "CONFIRMED", None),
        "CONFIRMED_FALSE": (False, "CONFIRMED", None),
        "WAITING_HISTORY": (
            None,
            "HISTORY_INCOMPLETE",
            "PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE",
        ),
        "WAITING_EVIDENCE": (
            None,
            "SOURCE_MISSING",
            "SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING",
        ),
    }
    relation_state, evidence_status, error = evidence[status]
    return {
        "source_appoint_id": appoint_id,
        "appoint_id_type": "NUMERIC",
        "completion_participation_seq": 1,
        "completion_source_revision": 3,
        "completion_end_time": (
            end_time.isoformat().replace("+00:00", "Z") if end_time else None
        ),
        "observed_at": (
            observed_at.isoformat().replace("+00:00", "Z")
            if observed_at
            else None
        ),
        "observation_due": True if observed_at else None,
        "status": status,
        "relation_state": relation_state,
        "relation_evidence_status": evidence_status,
        "relation_error_code": error,
        "existing_observation_revision": None,
        "existing_observation_status": None,
        "requires_materialization": True,
    }


def _state(*requirements: dict[str, object]) -> dict[str, object]:
    selected = requirements[0]["source_appoint_id"] if requirements else None
    blacklist_scope = {
        "source_table": "dom_teacher_blacklist",
        "scope_kind": "CURRENT",
        "scope_level": None,
        "scope_key": None,
        "state": "UNKNOWN",
        "row_version": None,
        "active_snapshot_id": None,
        "active_fence_hash": None,
        "history_from": None,
        "history_through": None,
    }
    return {
        "protocol_version": "teacher-student-domain-v1",
        "relationship_current": {
            "teacher_id_type": "NUMERIC",
            "is_favorited": True,
            "favorite_evidence_status": "CONFIRMED",
            "is_blocked": None,
            "block_evidence_status": "SOURCE_MISSING",
            "last_business_effective_at": "2026-08-19T00:00:00Z",
            "latest_event_evidence_status": "CONFIRMED",
        },
        "relationship_evidence": {
            "current_row_version": 2,
            "last_event_sequence": 7,
            "last_source_partition_epoch_id": "epoch-1",
            "last_topic": "topic-1",
            "last_partition_id": 0,
            "last_offset_value": 6,
            "last_source_row_revision": 3,
        },
        "blacklist_threshold": {
            "protocol_version": "blacklist-threshold-evidence-v1",
            "source_region": "dom",
            "teacher_id": "100",
            "teacher_id_type": "NUMERIC",
            "threshold": 2,
            "threshold_state": "SOURCE_MISSING",
            "evidence_status": "SOURCE_MISSING",
            "source_collection_complete": False,
            "distinct_active_student_count": 0,
            "source_missing_student_count": 0,
            "active_student_token_set_hash": "a" * 64,
            "source_missing_student_token_set_hash": "b" * 64,
            "scope": dict(blacklist_scope),
        },
        "favorite_observation_requirements": list(requirements),
        "favorite_attribution_intent": {
            "disposition": "SELECTED" if selected else "NO_CANDIDATE",
            "action": "AWARD" if selected else "NONE",
            "selected_source_appoint_id": selected,
            "reason": None,
            "existing_status": None,
        },
        "scope_evidence": [
            blacklist_scope,
            {
                "source_table": "dom_teacher_favorite",
                "scope_kind": "HISTORY",
                "scope_level": "TEACHER",
                "scope_key": "100",
                "state": "COMPLETE",
                "row_version": 4,
                "active_snapshot_id": "snapshot-1",
                "active_fence_hash": "c" * 64,
                "history_from": "2026-08-01T00:00:00Z",
                "history_through": "2026-08-22T00:00:00Z",
            }
        ],
    }


def _event() -> DtsV2OutboxEvent:
    identity = build_domain_aggregate_identity_v2("TEACHER_STUDENT", _key())
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id=(
            "source_wide.changed.v2:TEACHER_STUDENT:aggregate:r1"
        ),
        aggregate_type="TEACHER_STUDENT",
        aggregate_id=identity.aggregate_id,
        event_type="source_wide.changed.v2",
        payload={"aggregate_key": _key(), "aggregate_revision": 1},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


def test_plan_keeps_relationship_and_observation_separate() -> None:
    future = _requirement(status="PENDING")
    future["observation_due"] = False
    plan = build_teacher_student_materialization_plan_v2(
        aggregate_key=_key(),
        aggregate_state=_state(future),
    )

    assert plan.is_favorited is True
    assert plan.is_blocked is None
    assert plan.blacklist_threshold.threshold_state == "SOURCE_MISSING"
    assert plan.observations[0].projection_status == "PENDING"
    assert plan.observations[0].has_persistable_identity is True
    assert plan.observations[0].observed_at == datetime(
        2026, 8, 21, tzinfo=UTC
    )


def test_blacklist_threshold_accepts_active_before_scope_is_complete() -> None:
    state = _state()
    threshold = state["blacklist_threshold"]
    assert isinstance(threshold, dict)
    threshold.update(
        {
            "threshold_state": "ACTIVE",
            "evidence_status": "CONFIRMED",
            "distinct_active_student_count": 2,
        }
    )

    plan = build_teacher_student_materialization_plan_v2(
        aggregate_key=_key(), aggregate_state=state
    )

    assert plan.blacklist_threshold.threshold_state == "ACTIVE"
    assert plan.blacklist_threshold.source_collection_complete is False


def test_blacklist_threshold_rejects_suppression_without_complete_evidence() -> None:
    state = _state()
    threshold = state["blacklist_threshold"]
    assert isinstance(threshold, dict)
    threshold.update(
        {
            "threshold_state": "SUPPRESSED",
            "evidence_status": "CONFIRMED",
        }
    )

    with pytest.raises(
        DtsV2TeacherStudentOutboxProcessorError,
        match="BLACKLIST_THRESHOLD_BOUNDARY_INVALID",
    ):
        build_teacher_student_materialization_plan_v2(
            aggregate_key=_key(), aggregate_state=state
        )


def test_missing_completion_time_is_not_invented() -> None:
    missing = _requirement(end_time=None, status="WAITING_EVIDENCE")
    plan = build_teacher_student_materialization_plan_v2(
        aggregate_key=_key(),
        aggregate_state=_state(missing),
    )

    assert plan.observations[0].completion_end_time is None
    assert plan.observations[0].observed_at is None
    assert plan.observations[0].has_persistable_identity is False


def test_observed_at_must_be_exactly_completion_plus_24_hours() -> None:
    bad = _requirement()
    bad["observed_at"] = "2026-08-21T00:00:01Z"
    with pytest.raises(
        DtsV2TeacherStudentOutboxProcessorError,
        match="OBSERVATION_TIME_MISMATCH",
    ):
        build_teacher_student_materialization_plan_v2(
            aggregate_key=_key(), aggregate_state=_state(bad)
        )


def test_dom_raw_student_id_and_false_unknown_are_rejected() -> None:
    raw_key = {**_key(), "student_token": "student-raw"}
    with pytest.raises(
        DtsV2TeacherStudentOutboxProcessorError,
        match="DOM_TOKEN_INVALID",
    ):
        build_teacher_student_materialization_plan_v2(
            aggregate_key=raw_key,
            aggregate_state=_state(_requirement()),
        )

    state = _state(_requirement())
    state["relationship_current"] = {
        **state["relationship_current"],  # type: ignore[index]
        "is_favorited": None,
        "favorite_evidence_status": "CONFIRMED",
    }
    with pytest.raises(
        DtsV2TeacherStudentOutboxProcessorError,
        match="FAVORITE_EVIDENCE_INVALID",
    ):
        build_teacher_student_materialization_plan_v2(
            aggregate_key=_key(), aggregate_state=state
        )


class _Reader:
    def read_current(self, connection: object, event: DtsV2OutboxEvent):
        del connection, event
        return SimpleNamespace(
            aggregate_key=_key(),
            aggregate_state=_state(_requirement()),
            current_revision=7,
            is_superseded_event=True,
        )


class _Materializer:
    def __init__(self) -> None:
        self.call: tuple[object, int, str] | None = None

    def apply_teacher_student_plan(
        self,
        connection: object,
        plan: object,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> dict[str, int]:
        del connection
        self.call = (plan, aggregate_revision, triggering_event_id)
        return {"observation_rows": 1, "score_entries": 0}


def test_processor_reads_current_hash_checked_snapshot_before_materializing() -> None:
    materializer = _Materializer()
    processor = DtsV2TeacherStudentOutboxProcessor(
        materializer=materializer,
        aggregate_reader=_Reader(),  # type: ignore[arg-type]
    )

    result = processor.process_event(object(), _event())  # type: ignore[arg-type]

    assert result == {
        "observation_rows": 1,
        "score_entries": 0,
        "superseded_events": 1,
    }
    assert materializer.call is not None
    _, revision, event_id = materializer.call
    assert revision == 7
    assert event_id == _event().event_id


def test_processor_fails_closed_for_non_teacher_student_event() -> None:
    event = DtsV2OutboxEvent(**{**_event().__dict__, "aggregate_type": "COURSE"})
    with pytest.raises(
        DtsV2TeacherStudentOutboxProcessorError,
        match="OUTBOX_EVENT_REQUIRED",
    ):
        DtsV2TeacherStudentOutboxProcessor(
            materializer=_Materializer()
        ).process_event(object(), event)  # type: ignore[arg-type]
