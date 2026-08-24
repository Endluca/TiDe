from __future__ import annotations

import json

import pytest

from app.dts_v2_domain_aggregate import (
    DtsV2DomainAggregateError,
    DtsV2DomainRevisionStore,
    build_domain_aggregate_identity_v2,
    build_domain_outbox_event_v2,
    canonical_domain_state_v2,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _Connection:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), dict(parameters)))
        return _ScalarResult(self.value)


def _source_position(*, offset: int = 8) -> dict[str, object]:
    return {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "numeric",
        "record_id": "9001",
        "source_partition_epoch_id": "epoch-domain-test",
        "topic": "topic-domain-test",
        "partition_id": 0,
        "offset_value": offset,
    }


def _source_coverage(*, revision: int = 8) -> dict[str, object]:
    return {
        "projection_mode": "SHADOW_BUILD",
        "projection_generation": 1,
        "trigger": {
            "input_kind": "SOURCE_REVISION",
            "input_identity": {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "source_key": "NUMERIC:9001",
            },
            "input_revision": revision,
            "input_fingerprint": "a" * 64,
            "source_payload_hash": "b" * 64,
        },
    }


def _scope_coverage(*, revision: int = 3) -> dict[str, object]:
    return {
        "projection_mode": "SHADOW_BUILD",
        "projection_generation": 1,
        "trigger": {
            "input_kind": "SCOPE_REVISION",
            "input_identity": {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "scope_kind": "CURRENT",
                "scope_level": "GLOBAL",
                "scope_key": "*",
            },
            "input_revision": revision,
            "input_fingerprint": "c" * 64,
            "scope_state": "COMPLETE",
            "active_snapshot_id": "snapshot-1",
            "active_fence_hash": "d" * 64,
        },
    }


def test_course_identity_and_outbox_event_are_canonical() -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_appoint_id": "9001", "source_region": "dom"},
    )
    same = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "9001"},
    )

    assert identity == same
    assert identity.canonical_key_json == (
        '{"source_appoint_id":"9001","source_region":"dom"}'
    )
    assert identity.aggregate_id.startswith("v2:COURSE:")
    assert len(identity.aggregate_id.rsplit(":", 1)[1]) == 64

    event = build_domain_outbox_event_v2(
        identity=identity,
        aggregate_revision=3,
        changed_fields=["status", "teacher_id", "status"],
        source_row_revision=12,
        source_position=_source_position(offset=88),
        rule_version="course-projection-v2",
        cutover_coverage_identity=_source_coverage(revision=12),
    )
    assert event.event_id == (
        "source_wide.changed.v2:COURSE:"
        f"{identity.aggregate_id}:3"
    )
    assert event.outbox_id.startswith("outbox:v2:")
    assert len(event.outbox_id) == 74
    assert event.event_type == "source_wide.changed.v2"
    assert event.payload["changed_fields"] == ["status", "teacher_id"]
    assert len(event.payload_sha256) == 64
    json.dumps(dict(event.payload), allow_nan=False)


@pytest.mark.parametrize(
    ("aggregate_type", "key"),
    [
        (
            "PARTICIPATION",
            {
                "source_region": "ovs",
                "source_appoint_id": "course-A",
                "participation_seq": 2,
            },
        ),
        ("TEACHER", {"source_region": "dom", "teacher_id": "7"}),
        (
            "TEACHER_STUDENT",
            {
                "source_region": "dom",
                "teacher_id": "7",
                "student_token": "dom:v1:" + "a" * 64,
            },
        ),
        ("LABEL", {"source_region": "dom", "label_id": "16"}),
        (
            "COMPLAINT_CATEGORY",
            {"source_region": "dom", "category_id": "82"},
        ),
        (
            "COMPLETION_CONFLICT",
            {"source_region": "ovs", "source_appoint_id": "abc"},
        ),
        (
            "SOURCE_SCOPE",
            {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "scope_kind": "CURRENT",
                "scope_level": "GLOBAL",
                "scope_key": "*",
            },
        ),
        ("TASK_PLAN", {"assignment_dedupe_key": "p:teacher:1"}),
    ],
)
def test_all_frozen_aggregate_key_shapes_are_accepted(
    aggregate_type: str,
    key: dict[str, object],
) -> None:
    identity = build_domain_aggregate_identity_v2(aggregate_type, key)
    assert identity.aggregate_type == aggregate_type


def test_dom_teacher_student_rejects_raw_or_malformed_student_identity() -> None:
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_AGGREGATE_DOM_STUDENT_TOKEN_INVALID",
    ):
        build_domain_aggregate_identity_v2(
            "TEACHER_STUDENT",
            {
                "source_region": "dom",
                "teacher_id": "7",
                "student_token": "10086",
            },
        )


def test_complaint_dictionary_identity_is_always_dom() -> None:
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_AGGREGATE_COMPLAINT_REGION_INVALID",
    ):
        build_domain_aggregate_identity_v2(
            "COMPLAINT_CATEGORY",
            {"source_region": "ovs", "category_id": "82"},
        )


def test_scope_identity_cannot_claim_global_completeness_for_teacher_key() -> None:
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_AGGREGATE_SCOPE_KEY_INVALID",
    ):
        build_domain_aggregate_identity_v2(
            "SOURCE_SCOPE",
            {
                "source_region": "ovs",
                "source_table": "ovs_appoint",
                "scope_kind": "HISTORY",
                "scope_level": "GLOBAL",
                "scope_key": "teacher-1",
            },
        )


@pytest.mark.parametrize(
    "forbidden_alias",
    [
        "student_id",
        "s_id",
        "sId",
        "stu_id",
        "stuId",
        "user_id",
        "userId",
        "rawStudentId",
        "Stu_ID",
    ],
)
def test_outbox_payload_rejects_raw_student_identifiers_recursively(
    forbidden_alias: str,
) -> None:
    identity = build_domain_aggregate_identity_v2(
        "TEACHER",
        {"source_region": "dom", "teacher_id": "7"},
    )
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_AGGREGATE_RAW_STUDENT_ID_FORBIDDEN",
    ):
        build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=1,
            changed_fields=["status"],
            source_row_revision=1,
            source_position=_source_position(),
            rule_version=None,
            cutover_coverage_identity={
                **_source_coverage(revision=1),
                "nested": [{forbidden_alias: "10086"}],
            },
        )


@pytest.mark.parametrize(
    ("source_row_revision", "source_position", "error"),
    [
        (None, _source_position(), "COVERAGE_TRIGGER_INVALID"),
        (True, _source_position(), "COVERAGE_TRIGGER_INVALID"),
        (1, None, "SOURCE_POSITION_INVALID"),
        (1, {"v": 1, "offset_value": 8}, "SOURCE_POSITION_INVALID"),
        (
            1,
            {**_source_position(), "partition_id": True},
            "SOURCE_POSITION_INVALID",
        ),
        (
            1,
            {**_source_position(), "record_id_type": "none"},
            "SOURCE_POSITION_INVALID",
        ),
    ],
)
def test_domain_outbox_requires_exact_concrete_source_provenance(
    source_row_revision: object,
    source_position: object,
    error: str,
) -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "9001"},
    )
    with pytest.raises(DtsV2DomainAggregateError, match=error):
        build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=1,
            changed_fields=["status"],
            source_row_revision=source_row_revision,  # type: ignore[arg-type]
            source_position=source_position,  # type: ignore[arg-type]
            rule_version="course-v2",
            cutover_coverage_identity=_source_coverage(revision=1),
        )


def test_scope_trigger_requires_and_emits_null_source_provenance_pair() -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "9001"},
    )
    event = build_domain_outbox_event_v2(
        identity=identity,
        aggregate_revision=2,
        changed_fields=["complaint_evidence_status"],
        source_row_revision=None,
        source_position=None,
        rule_version="course-v2",
        cutover_coverage_identity=_scope_coverage(),
    )
    assert event.payload["source_row_revision"] is None
    assert event.payload["source_position"] is None


def test_non_whitelisted_causal_trigger_fails_closed() -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "9001"},
    )
    coverage = _source_coverage(revision=1)
    coverage["trigger"] = {
        "input_kind": "CATALOG_REVISION",
        "input_identity": {"catalog_type": "COMPLAINT_RULE_SET"},
        "input_revision": 1,
        "input_fingerprint": "a" * 64,
    }
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="COVERAGE_TRIGGER_INVALID",
    ):
        build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=1,
            changed_fields=["status"],
            source_row_revision=None,
            source_position=None,
            rule_version="course-v2",
            cutover_coverage_identity=coverage,
        )


def test_causal_trigger_rejects_non_integer_number_before_shape_use() -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "9001"},
    )
    coverage = _source_coverage(revision=1)
    trigger = coverage["trigger"]
    assert isinstance(trigger, dict)
    coverage["trigger"] = {
        **trigger,
        "input_revision": 1.0,
    }
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_DOMAIN_JSON_NUMBER_INVALID",
    ):
        build_domain_outbox_event_v2(
            identity=identity,
            aggregate_revision=1,
            changed_fields=["status"],
            source_row_revision=1,
            source_position=_source_position(),
            rule_version="course-v2",
            cutover_coverage_identity=coverage,
        )


def test_semantic_state_hash_ignores_mapping_insertion_order() -> None:
    first = canonical_domain_state_v2({"status": "on", "teacher_id": "7"})
    second = canonical_domain_state_v2(
        {"teacher_id": "7", "status": "on"}
    )
    assert first == second


@pytest.mark.parametrize(
    "value",
    [
        {"ratio": 1.5},
        {"nested": [{"score": 1e20}]},
        {"negative_zero": -0.0},
    ],
)
def test_v2_domain_json_rejects_non_integer_numbers_recursively(
    value: dict[str, object],
) -> None:
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_DOMAIN_JSON_NUMBER_INVALID",
    ):
        canonical_domain_state_v2(value)


def test_v2_domain_json_keeps_boolean_distinct_from_integer() -> None:
    canonical, digest = canonical_domain_state_v2(
        {"flags": [True, False], "score": 100}
    )
    assert canonical == '{"flags":[true,false],"score":100}'
    assert len(digest) == 64


def test_revision_store_only_calls_owner_checked_database_command() -> None:
    identity = build_domain_aggregate_identity_v2(
        "COURSE", {"source_region": "dom", "source_appoint_id": "9001"}
    )
    event = build_domain_outbox_event_v2(
        identity=identity,
        aggregate_revision=2,
        changed_fields=["status"],
        source_row_revision=8,
        source_position=_source_position(),
        rule_version="course-v2",
        cutover_coverage_identity=_source_coverage(revision=8),
    )
    connection = _Connection(
        {
            "status": "CHANGED",
            "aggregate_id": identity.aggregate_id,
            "aggregate_revision": 2,
            "event_id": event.event_id,
            "outbox_id": event.outbox_id,
            "payload_sha256": event.payload_sha256,
        }
    )

    result = DtsV2DomainRevisionStore().publish_change(
        connection,
        aggregate_type="COURSE",
        aggregate_key={
            "source_appoint_id": "9001",
            "source_region": "dom",
        },
        aggregate_state={"status": "on"},
        changed_fields=["status"],
        source_row_revision=8,
        source_position=_source_position(),
        rule_version="course-v2",
        cutover_coverage_identity=_source_coverage(revision=8),
    )

    assert result.status == "CHANGED"
    assert result.event == event
    sql, parameters = connection.calls[0]
    assert "publish_domain_aggregate_revision_v2" in sql
    assert "INSERT INTO" not in sql
    assert "UPDATE " not in sql
    assert parameters["aggregate_state_sha256"] == canonical_domain_state_v2(
        {"status": "on"}
    )[1]


@pytest.mark.parametrize(
    ("aggregate_type", "aggregate_key"),
    [
        (
            "SOURCE_SCOPE",
            {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "scope_kind": "CURRENT",
                "scope_level": "GLOBAL",
                "scope_key": "*",
            },
        ),
        ("TASK_PLAN", {"assignment_dedupe_key": "task-plan-1"}),
    ],
)
def test_revision_store_rejects_independently_published_aggregate_types(
    aggregate_type: str,
    aggregate_key: dict[str, object],
) -> None:
    connection = _Connection(None)
    with pytest.raises(
        DtsV2DomainAggregateError,
        match="DTS_V2_DOMAIN_PUBLISHER_AGGREGATE_TYPE_INVALID",
    ):
        DtsV2DomainRevisionStore().publish_change(
            connection,
            aggregate_type=aggregate_type,
            aggregate_key=aggregate_key,
            aggregate_state={"status": "COMPLETE"},
            changed_fields=["status"],
            source_row_revision=None,
            source_position=None,
            rule_version=None,
            cutover_coverage_identity=_scope_coverage(),
        )
    assert connection.calls == []


def test_revision_store_verifies_unchanged_response_has_no_event() -> None:
    identity = build_domain_aggregate_identity_v2(
        "TEACHER", {"source_region": "ovs", "teacher_id": "7"}
    )
    connection = _Connection(
        {
            "status": "UNCHANGED",
            "aggregate_id": identity.aggregate_id,
            "aggregate_revision": 5,
            "event_id": None,
            "outbox_id": None,
            "payload_sha256": None,
        }
    )
    result = DtsV2DomainRevisionStore().publish_change(
        connection,
        aggregate_type="TEACHER",
        aggregate_key={"source_region": "ovs", "teacher_id": "7"},
        aggregate_state={"online_status": "EXISTING"},
        changed_fields=["online_status"],
        source_row_revision=9,
        source_position=_source_position(),
        rule_version=None,
        cutover_coverage_identity=_source_coverage(revision=9),
    )
    assert result.event is None
    assert result.aggregate_revision == 5


def test_revision_store_rejects_database_event_identity_drift() -> None:
    identity = build_domain_aggregate_identity_v2(
        "LABEL", {"source_region": "dom", "label_id": "16"}
    )
    connection = _Connection(
        {
            "status": "CHANGED",
            "aggregate_id": identity.aggregate_id,
            "aggregate_revision": 1,
            "event_id": "wrong",
            "outbox_id": "wrong",
            "payload_sha256": "0" * 64,
        }
    )
    with pytest.raises(DtsV2DomainAggregateError, match="EVENT_MISMATCH"):
        DtsV2DomainRevisionStore().publish_change(
            connection,
            aggregate_type="LABEL",
            aggregate_key={"source_region": "dom", "label_id": "16"},
            aggregate_state={"label_name": "good"},
            changed_fields=["label_name"],
            source_row_revision=1,
            source_position=_source_position(),
            rule_version=None,
            cutover_coverage_identity=_source_coverage(revision=1),
        )


@pytest.mark.parametrize(
    ("aggregate_type", "key", "error"),
    [
        ("COURSE", {"source_region": "dom"}, "KEY_SHAPE"),
        (
            "PARTICIPATION",
            {
                "source_region": "dom",
                "source_appoint_id": "1",
                "participation_seq": True,
            },
            "PARTICIPATION_SEQ",
        ),
        (
            "SOURCE_SCOPE",
            {
                "source_region": "dom",
                "source_table": "ovs_appoint",
                "scope_kind": "CURRENT",
                "scope_level": "GLOBAL",
                "scope_key": "*",
            },
            "TABLE_REGION_MISMATCH",
        ),
    ],
)
def test_invalid_aggregate_keys_fail_closed(
    aggregate_type: str,
    key: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(DtsV2DomainAggregateError, match=error):
        build_domain_aggregate_identity_v2(aggregate_type, key)
