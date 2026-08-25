from __future__ import annotations

from datetime import date

import pytest

from app.dts_v2_domain_aggregate import (
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)
from app.dts_v2_outbox_worker import DtsV2OutboxEvent
from app.dts_v2_teacher_outbox_processor import (
    DtsV2TeacherOutboxProcessor,
    DtsV2TeacherOutboxProcessorError,
    build_teacher_materialization_plan_v2,
)


TEACHER_ID = "7"


def _scope(table: str, kind: str, *, complete: bool = True) -> dict[str, object]:
    return {
        "source_table": table,
        "scope_kind": kind,
        "scope_level": "TEACHER",
        "scope_key": TEACHER_ID,
        "state": "COMPLETE" if complete else "STALE",
        "row_version": 1,
        "active_snapshot_id": "scope-1",
        "snapshot_fence_hash": "a" * 64,
        "history_from": "2026-01-01T00:00:00Z" if kind == "HISTORY" else None,
        "history_through": "2026-08-22T23:59:59Z" if kind == "HISTORY" else None,
    }


def _scopes(region: str) -> list[dict[str, object]]:
    suffixes = [
        "appoint",
        "complaint",
        "teacher_favorite",
        "teacher_blacklist",
    ]
    if region == "dom":
        suffixes.extend(
            [
                "user_teacher_grading",
                "teacher_penalty",
                "teacher_absent_reason",
                "teacher_class_schedule",
                "teacher_certification",
            ]
        )
    return [
        _scope(f"{region}_{suffix}", kind)
        for suffix in suffixes
        for kind in ("CURRENT", "HISTORY")
    ]


def _profile() -> dict[str, object]:
    values = {
        "id": 7,
        "real_name": "Teacher Seven",
        "center_type": 1,
        "is_full_time": 5,
        "course": "global_cn_pool",
        "status": "on",
        "status_on_time": "2026-01-01T09:00:00+08:00",
        "status_off_time": None,
        "last_on_time": None,
    }
    return {
        "source_table": "dom_teacher",
        "source_key": TEACHER_ID,
        "source_key_type": "NUMERIC",
        "source_row_revision": 3,
        "source_payload_hash": "a" * 64,
        "source_deleted": False,
        "values": values,
        "field_types": {
            "id": "NUMERIC",
            "real_name": "TEXT",
            "center_type": "NUMERIC",
            "is_full_time": "NUMERIC",
            "course": "TEXT",
            "status": "TEXT",
            "status_on_time": "TEMPORAL",
        },
    }


def _participation(
    region: str,
    appoint_id: str,
    *,
    lesson_date: str,
    grading: str | None,
) -> dict[str, object]:
    token = ("dom:v1:" + "b" * 64) if region == "dom" else "ovs-student-1"
    return {
        "source_appoint_id": appoint_id,
        "participation_seq": 1,
        "teacher_id": TEACHER_ID,
        "teacher_id_type": "NUMERIC",
        "participation_status": "end",
        "participation_role": "COMPLETION",
        "source_deleted": False,
        "completion_is_peak": False,
        "completion_lesson_local_date": lesson_date,
        "completion_student_token": token,
        "completion_conflict_status": "NONE",
        "course_evidence_status": "CONFIRMED",
        "is_late": False,
        "late_evidence_status": "CONFIRMED",
        "is_early": False,
        "early_evidence_status": "CONFIRMED",
        "no_notice": False,
        "grading_classification": grading,
        "grading_evidence_status": (
            "CONFIRMED" if grading is not None else "SOURCE_MISSING"
        ),
        "has_complaint": False,
        "has_valid_complaint": False,
        "complaint_evidence_status": "CONFIRMED",
        "initial_completion_snapshot": {
            "teacher_id": 7,
            "teacher_id_type": "NUMERIC",
            "status": "end",
            "lesson_local_date": lesson_date,
        },
    }


def _history(region: str, appoint_id: str, lesson_date: str) -> dict[str, object]:
    return {
        "source_table": f"{region}_appoint",
        "source_key": appoint_id,
        "source_key_type": "NUMERIC",
        "source_row_revision": 1,
        "source_field_types": {
            "t_id": "NUMERIC",
            "date": "TEMPORAL",
            "status": "TEXT",
        },
        "before_image": None,
        "after_image": {
            "t_id": 7,
            "status": "on",
            "date": lesson_date,
            "end_time": None,
        },
    }


def _states() -> dict[str, dict[str, object]]:
    dom_part = _participation(
        "dom", "9001", lesson_date="2026-07-10", grading="POSITIVE"
    )
    ovs_part = _participation(
        "ovs", "9002", lesson_date="2026-08-10", grading=None
    )
    dom = {
        "protocol_version": "teacher-domain-v1",
        "profile": _profile(),
        "profile_evidence_status": "CONFIRMED",
        "profile_reference": {"source_region": "dom", "teacher_id": TEACHER_ID},
        "certifications": [
            {
                "source_table": "dom_teacher_certification",
                "source_key": "81",
                "source_key_type": "NUMERIC",
                "source_deleted": False,
                "values": {
                    "id": 81,
                    "teacher_id": 7,
                    "certification_code": "16",
                    "certification_status": 1,
                },
                "field_types": {
                    "id": "NUMERIC",
                    "teacher_id": "NUMERIC",
                    "certification_code": "TEXT",
                    "certification_status": "NUMERIC",
                },
            }
        ],
        "schedules": [],
        "participations": [dom_part],
        "relationships": [],
        "history": {
            "appoint_versions": [_history("dom", "9001", "2026-07-10")],
            "schedule_versions": [
                {
                    "source_table": "dom_teacher_class_schedule",
                    "source_key": "s1",
                    "source_key_type": "NUMERIC",
                    "source_row_revision": 1,
                    "source_field_types": {
                        "teacher_id": "NUMERIC",
                        "status": "TEXT",
                        "date": "TEMPORAL",
                    },
                    "before_image": None,
                    "after_image": {
                        "teacher_id": 7,
                        "status": "on",
                        "date": "2026-01-03",
                    },
                }
            ],
        },
        "scope_evidence": _scopes("dom"),
    }
    ovs = {
        "protocol_version": "teacher-domain-v1",
        "profile": None,
        "profile_evidence_status": "CROSS_REGION_REFERENCE",
        "profile_reference": {"source_region": "dom", "teacher_id": TEACHER_ID},
        "certifications": [],
        "schedules": [],
        "participations": [ovs_part],
        "relationships": [],
        "history": {
            "appoint_versions": [_history("ovs", "9002", "2026-08-10")],
            "schedule_versions": [],
        },
        "scope_evidence": _scopes("ovs"),
    }
    return {"dom": dom, "ovs": ovs}


def _aggregate_row(
    region: str,
    state: dict[str, object],
    *,
    state_hash: str | None = None,
) -> dict[str, object]:
    identity = build_domain_aggregate_identity_v2(
        "TEACHER",
        {"source_region": region, "teacher_id": TEACHER_ID},
    )
    return {
        "aggregate_type": "TEACHER",
        "aggregate_id": identity.aggregate_id,
        "canonical_key": dict(identity.aggregate_key),
        "canonical_key_sha256": identity.aggregate_id.rsplit(":", 1)[-1],
        "revision": 1,
        "aggregate_state": state,
        "aggregate_state_sha256": (
            canonical_domain_state_v2(state)[1]
            if state_hash is None
            else state_hash
        ),
    }


def _event(region: str = "dom") -> DtsV2OutboxEvent:
    identity = build_domain_aggregate_identity_v2(
        "TEACHER",
        {"source_region": region, "teacher_id": TEACHER_ID},
    )
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id=f"source_wide.changed.v2:{identity.aggregate_id}:1",
        aggregate_type="TEACHER",
        aggregate_id=identity.aggregate_id,
        event_type="source_wide.changed.v2",
        payload={
            "aggregate_key": dict(identity.aggregate_key),
            "aggregate_revision": 1,
        },
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


class _AggregateRows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> "_AggregateRows":
        return self

    def __iter__(self):
        return iter(self.rows)


class _AggregateConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def execute(self, statement, parameters):
        assert "FROM public.domain_aggregate_revisions" in str(statement)
        assert len(parameters["aggregate_ids"]) == 2
        return _AggregateRows(self.rows)


class _UnexpectedMaterializer:
    def apply_teacher_plan(self, connection, plan, *, triggering_event_id):
        del connection, plan, triggering_event_id
        raise AssertionError("materializer must not run for an unmet dependency")


def test_missing_dom_teacher_baseline_is_an_auditable_noop() -> None:
    missing_baseline_state = {
        "protocol_version": "teacher-domain-v1",
        "profile": None,
        "profile_evidence_status": "SOURCE_MISSING",
        "profile_reference": {
            "source_region": "dom",
            "teacher_id": TEACHER_ID,
        },
    }
    processor = DtsV2TeacherOutboxProcessor(
        materializer=_UnexpectedMaterializer(),
    )

    result = processor.process_event(
        _AggregateConnection(
            [_aggregate_row("dom", missing_baseline_state)]
        ),  # type: ignore[arg-type]
        _event(),
    )

    assert result == {"teacher_baseline_missing_skips": 1}


def test_missing_peer_region_waits_without_materializing() -> None:
    processor = DtsV2TeacherOutboxProcessor(
        materializer=_UnexpectedMaterializer(),
    )

    result = processor.process_event(
        _AggregateConnection(
            [_aggregate_row("dom", _states()["dom"])]
        ),  # type: ignore[arg-type]
        _event(),
    )

    assert result == {"teacher_regional_dependency_waits": 1}


def test_malformed_existing_aggregate_is_not_hidden_as_dependency_wait() -> None:
    processor = DtsV2TeacherOutboxProcessor(
        materializer=_UnexpectedMaterializer(),
    )

    with pytest.raises(
        DtsV2TeacherOutboxProcessorError,
        match="DTS_V2_TEACHER_REGIONAL_AGGREGATE_HASH_MISMATCH",
    ):
        processor.process_event(
            _AggregateConnection(
                [
                    _aggregate_row(
                        "dom",
                        _states()["dom"],
                        state_hash="0" * 64,
                    )
                ]
            ),  # type: ignore[arg-type]
            _event(),
        )


def test_global_teacher_plan_merges_regions_and_keeps_all_course_facts() -> None:
    plan = build_teacher_materialization_plan_v2(
        teacher_id=TEACHER_ID,
        regional_states=_states(),
        regional_revisions={"dom": 11, "ovs": 7},
        business_date_beijing=date(2026, 8, 22),
        legacy_first_dates={},
    )

    values = plan.projection.values
    assert values["total_booked_cnt"] == 2
    assert values["total_completed_cnt"] == 2
    # The July completion is far outside day 0-29 but still remains a course
    # fact; only the explicitly 30-day capacity average excludes it.
    assert values["capacity_avg_completed_per_day"] == 0
    assert values["feedback_total_eval_cnt"] == 1
    assert values["feedback_praise_cnt"] == 1
    assert values["late_cnt"] == 0
    assert values["early_cnt"] == 0
    assert values["no_notice_cnt"] == 0
    assert values["feedback_favorite_cnt"] == 0
    assert values["feedback_block_cnt"] == 0
    assert values["is_cpl_tesol"] is True
    assert values["first_booked_dt"] == date(2026, 7, 10)
    assert values["first_completed_dt"] == date(2026, 7, 10)
    assert values["first_open_slot_dt"] == date(2026, 1, 3)
    assert plan.regional_revisions == {"dom": 11, "ovs": 7}
    assert plan.business_date_beijing == date(2026, 8, 22)


def test_complete_penalty_scope_does_not_override_unknown_row_evidence() -> None:
    states = _states()
    participation = states["dom"]["participations"][0]  # type: ignore[index]
    participation["is_late"] = None  # type: ignore[index]
    participation["late_evidence_status"] = "SOURCE_MISSING"  # type: ignore[index]

    plan = build_teacher_materialization_plan_v2(
        teacher_id=TEACHER_ID,
        regional_states=states,
        regional_revisions={"dom": 1, "ovs": 1},
        business_date_beijing=date(2026, 8, 22),
        legacy_first_dates={},
    )

    assert plan.projection.values["late_cnt"] is None
    assert plan.projection.values["early_cnt"] == 0
    assert plan.projection.values["perfect_cnt"] is None
    assert plan.projection.metric_evidence["late_cnt"] == "SOURCE_MISSING"


def test_complete_relationship_scope_keeps_per_field_unknown_evidence() -> None:
    states = _states()
    states["dom"]["relationships"] = [
        {
            "teacher_id_type": "NUMERIC",
            "student_token": "dom:v1:" + "c" * 64,
            "is_favorited": None,
            "favorite_evidence_status": "SOURCE_MISSING",
            "is_blocked": False,
            "block_evidence_status": "CONFIRMED",
        }
    ]

    plan = build_teacher_materialization_plan_v2(
        teacher_id=TEACHER_ID,
        regional_states=states,
        regional_revisions={"dom": 1, "ovs": 1},
        business_date_beijing=date(2026, 8, 22),
        legacy_first_dates={},
    )

    assert plan.projection.values["feedback_favorite_cnt"] is None
    assert plan.projection.values["feedback_block_cnt"] == 0
    assert (
        plan.projection.metric_evidence["feedback_favorite_cnt"]
        == "SOURCE_MISSING"
    )
    assert plan.projection.metric_evidence["feedback_block_cnt"] == "CONFIRMED"


def test_complete_absence_scope_does_not_turn_unknown_no_notice_false() -> None:
    states = _states()
    participation = states["dom"]["participations"][0]  # type: ignore[index]
    participation["participation_role"] = "NORMAL"  # type: ignore[index]
    participation["participation_status"] = "t_absent"  # type: ignore[index]
    participation["no_notice"] = None  # type: ignore[index]
    participation["absence_evidence_status"] = "SOURCE_MISSING"  # type: ignore[index]

    plan = build_teacher_materialization_plan_v2(
        teacher_id=TEACHER_ID,
        regional_states=states,
        regional_revisions={"dom": 1, "ovs": 1},
        business_date_beijing=date(2026, 8, 22),
        legacy_first_dates={},
    )

    assert plan.projection.values["absent_cnt"] == 1
    assert plan.projection.values["no_notice_cnt"] is None
    assert plan.projection.metric_evidence["no_notice_cnt"] == "SOURCE_MISSING"


def test_first_date_empty_is_not_inferred_without_complete_history() -> None:
    states = _states()
    states["dom"]["history"] = {
        "appoint_versions": [],
        "schedule_versions": [],
    }
    states["ovs"]["history"] = {
        "appoint_versions": [],
        "schedule_versions": [],
    }
    states["dom"]["participations"] = []
    states["ovs"]["participations"] = []
    scopes = states["dom"]["scope_evidence"]
    assert isinstance(scopes, list)
    for row in scopes:
        if row["source_table"] == "dom_teacher_class_schedule" and row["scope_kind"] == "HISTORY":
            row["state"] = "STALE"

    plan = build_teacher_materialization_plan_v2(
        teacher_id=TEACHER_ID,
        regional_states=states,
        regional_revisions={"dom": 1, "ovs": 1},
        business_date_beijing=date(2026, 8, 22),
        legacy_first_dates={},
    )

    assert plan.projection.values["first_open_slot_dt"] is None
    assert (
        plan.projection.first_date_evidence[
            "first_open_slot_dt_evidence_status"
        ]
        == "SOURCE_MISSING"
    )


def test_cross_region_teacher_type_conflict_fails_closed() -> None:
    states = _states()
    ovs_part = states["ovs"]["participations"][0]  # type: ignore[index]
    ovs_part["teacher_id_type"] = "TEXT"  # type: ignore[index]
    ovs_part["teacher_id"] = "7"  # type: ignore[index]

    with pytest.raises(
        DtsV2TeacherOutboxProcessorError,
        match="ID_TYPE_CONFLICT",
    ):
        build_teacher_materialization_plan_v2(
            teacher_id=TEACHER_ID,
            regional_states=states,
            regional_revisions={"dom": 1, "ovs": 1},
            business_date_beijing=date(2026, 8, 22),
            legacy_first_dates={},
        )


class _LegacyMappings:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row

    def mappings(self) -> _LegacyMappings:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self.row


class _LegacyConnection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def execute(self, statement, parameters):
        self.sql = str(statement)
        self.parameters = dict(parameters)
        return _LegacyMappings(self.row)


def test_legacy_first_dates_read_never_requests_a_row_lock() -> None:
    expected = {
        "first_open_slot_dt": date(2026, 1, 3),
        "first_booked_dt": date(2026, 7, 10),
        "first_completed_dt": date(2026, 7, 10),
    }
    connection = _LegacyConnection(expected)

    actual = DtsV2TeacherOutboxProcessor._read_legacy_first_dates(
        connection,  # type: ignore[arg-type]
        TEACHER_ID,
    )

    assert actual == expected
    assert "FROM public.teacher_source_wide" in connection.sql
    assert "FOR SHARE" not in connection.sql
    assert "FOR UPDATE" not in connection.sql
    assert connection.parameters == {"teacher_id": TEACHER_ID}
