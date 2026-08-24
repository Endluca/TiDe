from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.dts_course_participation import SourceEventReference, reduce_course_participations
from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConsumerSettings,
    protect_domestic_student_ids,
)
from app.dts_source_contract_v2 import (
    V2_SOURCE_FIELD_WHITELIST,
    V2SourceRouteDecision,
    build_v2_source_route,
    with_v2_source_image_completeness,
)
from app.dts_v2_appoint_adapter import (
    V2AppointAdaptedVersion,
    V2AppointAdapterError,
    adapt_v2_appoint_route,
)
from dts_v2_test_profiles import SYNTHETIC_APPOINT_PROFILE_IDS


pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")


def _ref(revision: int, *, timestamp: datetime | None = None) -> SourceEventReference:
    return SourceEventReference(
        source_partition_epoch_id="epoch-1",
        topic="dom_appoint",
        partition=0,
        offset=revision,
        source_timestamp=timestamp
        or datetime(2026, 8, 22, 2, revision, tzinfo=timezone.utc),
    )


def _route(
    *,
    region: str = "dom",
    operation: str = "INSERT",
    source_key: str = "100",
    source_key_type: str = "NUMERIC",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    source_field_types: dict[str, str] | None = None,
    profile_id: str | None = None,
) -> V2SourceRouteDecision:
    return V2SourceRouteDecision(
        route_status="VERSIONED",
        source_table=f"{region}_appoint",
        operation=operation,
        source_key=source_key,
        source_key_type=source_key_type,
        source_schema_profile_id=profile_id
        or SYNTHETIC_APPOINT_PROFILE_IDS[f"{region}_appoint"],
        source_field_types=(
            source_field_types
            if source_field_types is not None
            else {"id": source_key_type, "t_id": "TEXT", "status": "TEXT"}
        ),
        before_row=before,
        after_row=after,
        protected_payload_hash="a" * 64,
    )


_RAW_DOM_APPOINT_FIELDS = V2_SOURCE_FIELD_WHITELIST["appoint"] - {
    "student_token"
}
_REAL_DOM_SETTINGS = DtsConsumerSettings(
    source_region="dom",
    broker_urls=("broker.invalid:9092",),
    topic="dom-topic",
    group_id="dom-v2-adapter-test",
    account="test-account",
    password="test-password",
    execution_region="cn",
    domestic_student_hmac_key="11" * 32,
)


def _raw_dom_appoint_row(
    teacher_id: str,
    *,
    status: str = "on",
    end_time: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        field_name: None for field_name in _RAW_DOM_APPOINT_FIELDS
    }
    row.update(
        {
            "id": 9001,
            "t_id": teacher_id,
            "s_id": "dom-student-1",
            "status": status,
            "end_time": end_time,
            "date": "2026-08-22",
            "time": "18:00:00",
            "week": 6,
        }
    )
    return row


def _real_dom_appoint_version(
    *,
    revision: int,
    operation: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> V2AppointAdaptedVersion:
    event = DtsChangeEvent(
        source_region="dom",
        topic="dom-topic",
        partition=0,
        offset=revision,
        record_id=revision,
        source_timestamp=1_787_353_200 + revision,
        source_txid=f"tx-{revision}",
        source_position=f"position-{revision}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="dom_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": "NUMERIC",
            "t_id": "TEXT",
            "s_id": "TEXT",
            "status": "TEXT",
            "end_time": "TEMPORAL",
            "date": "TEMPORAL",
            "time": "TEMPORAL",
            "week": "NUMERIC",
        },
    )
    complete = with_v2_source_image_completeness(event)
    protected = protect_domestic_student_ids(complete, _REAL_DOM_SETTINGS)
    return adapt_v2_appoint_route(
        build_v2_source_route(protected),
        source_region="dom",
        source_row_revision=revision,
        source_ref=_ref(revision),
    )


def test_status_on_teacher_change_replays_to_old_absent_and_new_current() -> None:
    first = adapt_v2_appoint_route(
        _route(
            after={
                "id": "100",
                "t_id": "A",
                "status": "on",
                "student_token": "dom:v1:" + "1" * 64,
            },
            source_field_types={
                "id": "NUMERIC",
                "t_id": "TEXT",
                "status": "TEXT",
                "student_token": "TEXT",
            },
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )
    changed = adapt_v2_appoint_route(
        _route(
            operation="UPDATE",
            before={
                "id": "100",
                "t_id": "A",
                "status": "on",
                "student_token": "dom:v1:" + "1" * 64,
            },
            after={
                "id": "100",
                "t_id": "B",
                "status": "on",
                "student_token": "dom:v1:" + "1" * 64,
            },
            source_field_types={
                "id": "NUMERIC",
                "t_id": "TEXT",
                "status": "TEXT",
                "student_token": "TEXT",
            },
        ),
        source_region="dom",
        source_row_revision=2,
        source_ref=_ref(2),
    )

    result = reduce_course_participations(
        source_region=first.source_region,
        source_appoint_id=first.source_appoint_id,
        versions=(first.source_version, changed.source_version),
    )

    assert [(row.teacher_id, row.participation_status, row.is_current) for row in result.state.participations] == [
        ("A", "t_absent", False),
        ("B", "on", True),
    ]


def test_real_protected_route_reaches_status_on_substitution_reducer() -> None:
    inserted = _real_dom_appoint_version(
        revision=1,
        operation="INSERT",
        before=None,
        after=_raw_dom_appoint_row("A"),
    )
    substituted = _real_dom_appoint_version(
        revision=2,
        operation="UPDATE",
        before=_raw_dom_appoint_row("A"),
        after=_raw_dom_appoint_row("B"),
    )
    result = reduce_course_participations(
        source_region="dom",
        source_appoint_id="9001",
        versions=(inserted.source_version, substituted.source_version),
    )

    assert inserted.source_key_type == "NUMERIC"
    assert inserted.source_appoint_id == "9001"
    assert inserted.source_version.after is not None
    assert str(inserted.source_version.after.student_token).startswith(
        "dom:v1:"
    )
    assert "dom-student-1" not in repr(inserted)
    assert [
        (row.teacher_id, row.participation_status, row.is_current)
        for row in result.state.participations
    ] == [("A", "t_absent", False), ("B", "on", True)]


def test_real_route_substitution_and_end_freezes_new_teacher() -> None:
    a_on = _raw_dom_appoint_row("A")
    b_end = _raw_dom_appoint_row(
        "B",
        status="end",
        end_time="2026-08-22T18:30:00+08:00",
    )
    inserted = _real_dom_appoint_version(
        revision=1,
        operation="INSERT",
        before=None,
        after=a_on,
    )
    ended = _real_dom_appoint_version(
        revision=2,
        operation="UPDATE",
        before=a_on,
        after=b_end,
    )

    result = reduce_course_participations(
        source_region="dom",
        source_appoint_id="9001",
        versions=(inserted.source_version, ended.source_version),
    )

    assert [
        (row.teacher_id, row.participation_status, row.is_current)
        for row in result.state.participations
    ] == [("A", "t_absent", False), ("B", "end", True)]
    assert result.state.completion_teacher_id == "B"
    assert result.state.completion_participation_seq == 2


def test_real_route_post_end_changes_never_reassign_frozen_teacher() -> None:
    a_end = _raw_dom_appoint_row(
        "A",
        status="end",
        end_time="2026-08-22T18:30:00+08:00",
    )
    a_on = _raw_dom_appoint_row(
        "A",
        status="on",
        end_time="2026-08-22T18:30:00+08:00",
    )
    b_on = _raw_dom_appoint_row(
        "B",
        status="on",
        end_time="2026-08-22T18:30:00+08:00",
    )
    versions = (
        _real_dom_appoint_version(
            revision=1,
            operation="INSERT",
            before=None,
            after=a_end,
        ).source_version,
        _real_dom_appoint_version(
            revision=2,
            operation="UPDATE",
            before=a_end,
            after=a_on,
        ).source_version,
        _real_dom_appoint_version(
            revision=3,
            operation="UPDATE",
            before=a_on,
            after=b_on,
        ).source_version,
    )

    result = reduce_course_participations(
        source_region="dom",
        source_appoint_id="9001",
        versions=versions,
    )

    frozen, pending = result.state.participations
    assert (frozen.teacher_id, frozen.participation_status) == ("A", "end")
    assert pending.teacher_id == "B"
    assert result.state.completion_teacher_id == "A"
    assert result.state.current_teacher_id == "B"
    assert result.state.completion_conflict_status.value == "PENDING"


def test_typed_teacher_ids_preserve_text_but_canonicalize_numeric() -> None:
    numeric = adapt_v2_appoint_route(
        _route(
            source_key="9",
            source_key_type="NUMERIC",
            after={"id": "9", "t_id": "009", "status": "on"},
            source_field_types={"id": "NUMERIC", "t_id": "NUMERIC", "status": "TEXT"},
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )
    text_teacher = adapt_v2_appoint_route(
        _route(
            source_key="10",
            source_key_type="NUMERIC",
            after={"id": "10", "t_id": "009", "status": "on"},
            source_field_types={"id": "NUMERIC", "t_id": "TEXT", "status": "TEXT"},
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )

    assert numeric.source_version.after is not None
    assert (
        numeric.source_appoint_id,
        numeric.source_key_type,
        numeric.source_version.after.teacher_id_type,
        numeric.source_version.after.teacher_id,
    ) == ("9", "NUMERIC", "NUMERIC", "9")
    assert text_teacher.source_version.after is not None
    assert (
        text_teacher.source_appoint_id,
        text_teacher.source_key_type,
        text_teacher.source_version.after.teacher_id_type,
        text_teacher.source_version.after.teacher_id,
    ) == ("10", "NUMERIC", "TEXT", "009")


def test_first_end_is_frozen_by_existing_reducer() -> None:
    on = adapt_v2_appoint_route(
        _route(after={"id": "100", "t_id": "A", "status": "on"}),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )
    end = adapt_v2_appoint_route(
        _route(
            operation="UPDATE",
            before={"id": "100", "t_id": "A", "status": "on"},
            after={"id": "100", "t_id": "A", "status": "end"},
        ),
        source_region="dom",
        source_row_revision=2,
        source_ref=_ref(2),
    )
    result = reduce_course_participations(
        source_region="dom",
        source_appoint_id="100",
        versions=(on.source_version, end.source_version),
    )

    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_teacher_id_type == "TEXT"
    assert result.state.completion_snapshot is not None
    assert result.state.completion_snapshot.teacher_id_type == "TEXT"
    assert result.state.participations[0].teacher_id_type == "TEXT"
    assert result.state.completion_source_revision == 2


def test_delete_maps_to_delete_version() -> None:
    deleted = adapt_v2_appoint_route(
        _route(
            operation="DELETE",
            before={"id": "100", "t_id": "A", "status": "on"},
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )

    assert deleted.source_version.operation == "DELETE"
    assert deleted.source_version.before is not None
    assert deleted.source_version.after is None


@pytest.mark.parametrize(
    "route, source_ref",
    [
        (
            _route(
                after={"id": "100", "t_id": "A", "status": "on"},
                profile_id="unverified-profile",
            ),
            _ref(1),
        ),
        (
            _route(
                after={"id": "100", "t_id": "A", "status": "on"},
                source_field_types={},
            ),
            _ref(1),
        ),
        (
            _route(
                after={"id": "100", "t_id": "A", "status": 1},
                source_field_types={"id": "NUMERIC", "t_id": "TEXT", "status": "TEXT"},
            ),
            _ref(1),
        ),
        (
            _route(
                after={"id": "100", "t_id": "A", "status": "on", "week": "weekday", "time": "18:00:00"},
                source_field_types={"id": "NUMERIC", "t_id": "TEXT", "status": "TEXT", "week": "TEXT", "time": "TEXT"},
            ),
            _ref(1),
        ),
    ],
)
def test_missing_evidence_or_illegal_fields_fail_closed(
    route: V2SourceRouteDecision,
    source_ref: SourceEventReference,
) -> None:
    with pytest.raises(V2AppointAdapterError):
        adapt_v2_appoint_route(
            route,
            source_region="dom",
            source_row_revision=1,
            source_ref=source_ref,
        )


def test_timestamp_must_be_present_and_timezone_aware() -> None:
    route = _route(after={"id": "100", "t_id": "A", "status": "on"})
    with pytest.raises(V2AppointAdapterError, match="SOURCE_TIMESTAMP_REQUIRED"):
        adapt_v2_appoint_route(
            route,
            source_region="dom",
            source_row_revision=1,
            source_ref=SourceEventReference(
                source_partition_epoch_id="epoch-1",
                topic="dom_appoint",
                partition=0,
                offset=1,
            ),
        )


@pytest.mark.parametrize(
    ("week", "lesson_time"),
    [
        (8, "18:00:00"),
        (-1, "18:00:00"),
        (1, "not-a-time"),
        (1, "18:00:00+08:00"),
    ],
)
def test_invalid_peak_inputs_remain_unknown(
    week: int,
    lesson_time: str,
) -> None:
    adapted = adapt_v2_appoint_route(
        _route(
            after={
                "id": "100",
                "t_id": "A",
                "status": "on",
                "week": week,
                "time": lesson_time,
            },
            source_field_types={
                "id": "NUMERIC",
                "t_id": "TEXT",
                "status": "TEXT",
                "week": "NUMERIC",
                "time": "TEXT",
            },
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )

    assert adapted.source_version.after is not None
    assert adapted.source_version.after.is_peak is None


def test_dom_adapter_accepts_only_protected_token_and_never_exposes_s_id() -> None:
    protected = adapt_v2_appoint_route(
        _route(
            after={
                "id": "100",
                "t_id": "A",
                "status": "on",
                "student_token": "dom:v1:" + "2" * 64,
            },
            source_field_types={
                "id": "NUMERIC",
                "t_id": "TEXT",
                "status": "TEXT",
                "student_token": "TEXT",
            },
        ),
        source_region="dom",
        source_row_revision=1,
        source_ref=_ref(1),
    )
    assert protected.source_version.after is not None
    assert protected.source_version.after.student_token == (
        "dom:v1:" + "2" * 64
    )
    assert "s_id" not in protected.source_version.after.__dict__

    with pytest.raises(V2AppointAdapterError, match="RAW_STUDENT_ID"):
        adapt_v2_appoint_route(
            _route(
                after={"id": "100", "t_id": "A", "status": "on", "s_id": "raw-student"},
                source_field_types={"id": "NUMERIC", "t_id": "TEXT", "status": "TEXT", "s_id": "TEXT"},
            ),
            source_region="dom",
            source_row_revision=1,
            source_ref=_ref(1),
        )
