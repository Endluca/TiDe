from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.dts_blacklist_rules_v2 import (
    EVIDENCE_CONFIRMED,
    EVIDENCE_SOURCE_MISSING,
    RELATION_BLOCKED,
    RELATION_NOT_BLOCKED,
    RELATION_SOURCE_MISSING,
    THRESHOLD_ACTIVE,
    THRESHOLD_SOURCE_MISSING,
    THRESHOLD_SUPPRESSED,
    BlacklistSourceRecordV2,
    DtsBlacklistRuleError,
    TypedBlacklistIdV2,
    rebuild_blacklist_threshold_v2,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
TEACHER = TypedBlacklistIdV2("NUMERIC", "0007")


def _token(seed: str) -> str:
    return "dom:v1:" + seed * 64


def _record(
    source_id_value: int | str,
    student_token: str,
    **overrides: object,
) -> BlacklistSourceRecordV2:
    values: dict[str, object] = {
        "source_region": "dom",
        "source_id": TypedBlacklistIdV2("NUMERIC", source_id_value),
        "teacher_id": TEACHER,
        "student_token": student_token,
        "valid_start_time": AS_OF - timedelta(days=1),
        "is_valid_forever": True,
    }
    values.update(overrides)
    return BlacklistSourceRecordV2(**values)  # type: ignore[arg-type]


def _rebuild(
    records: list[BlacklistSourceRecordV2],
    **overrides: object,
):
    values: dict[str, object] = {
        "source_region": "dom",
        "teacher_id": TEACHER,
        "source_records": records,
        "business_as_of": AS_OF,
        "source_collection_complete": True,
    }
    values.update(overrides)
    return rebuild_blacklist_threshold_v2(**values)  # type: ignore[arg-type]


def test_threshold_counts_distinct_students_without_any_course_dependency() -> None:
    projected = _rebuild(
        [
            _record(1, _token("a")),
            _record(2, _token("b")),
        ]
    )

    assert projected.distinct_active_student_count == 2
    assert projected.threshold_state == THRESHOLD_ACTIVE
    assert projected.evidence_status == EVIDENCE_CONFIRMED
    assert projected.active_student_tokens == (_token("a"), _token("b"))
    assert not hasattr(projected, "assignment")


def test_multiple_source_rows_for_one_student_are_deduplicated() -> None:
    projected = _rebuild(
        [
            _record(1, _token("a")),
            _record(2, _token("a")),
        ]
    )

    assert projected.distinct_active_student_count == 1
    assert projected.threshold_state == THRESHOLD_SUPPRESSED
    assert len(projected.student_relations) == 1
    assert projected.student_relations[0].relation_state == RELATION_BLOCKED


def test_delete_one_overlapping_row_does_not_clear_the_other_row() -> None:
    projected = _rebuild(
        [
            _record(1, _token("a"), is_deleted=True),
            _record(2, _token("a")),
            _record(3, _token("b")),
        ]
    )

    assert projected.distinct_active_student_count == 2
    assert projected.threshold_state == THRESHOLD_ACTIVE
    relation = projected.student_relations[0]
    assert relation.student_token == _token("a")
    assert [item.canonical_value for item in relation.active_source_ids] == [
        "2"
    ]


def test_deleting_last_distinct_student_suppresses_the_threshold() -> None:
    active = _rebuild(
        [_record(1, _token("a")), _record(2, _token("b"))]
    )
    suppressed = _rebuild(
        [
            _record(1, _token("a")),
            _record(2, _token("b"), is_deleted=True),
        ]
    )

    assert active.threshold_state == THRESHOLD_ACTIVE
    assert suppressed.distinct_active_student_count == 1
    assert suppressed.threshold_state == THRESHOLD_SUPPRESSED


def test_start_time_uses_valid_start_then_add_then_source_timestamp() -> None:
    future = AS_OF + timedelta(hours=1)
    past = AS_OF - timedelta(hours=1)
    records = [
        _record(
            1,
            _token("a"),
            valid_start_time=future,
            add_time=past,
            source_timestamp=past,
        ),
        _record(
            2,
            _token("b"),
            valid_start_time=None,
            add_time=past,
            source_timestamp=future,
        ),
        _record(
            3,
            _token("c"),
            valid_start_time=None,
            add_time=None,
            source_timestamp=past,
        ),
    ]

    projected = _rebuild(records)
    states = {
        item.student_token: item.relation_state
        for item in projected.student_relations
    }

    assert states == {
        _token("a"): RELATION_NOT_BLOCKED,
        _token("b"): RELATION_BLOCKED,
        _token("c"): RELATION_BLOCKED,
    }
    assert projected.distinct_active_student_count == 2
    assert projected.threshold_state == THRESHOLD_ACTIVE


def test_permanent_flag_overrides_an_expired_end_time() -> None:
    projected = _rebuild(
        [
            _record(
                1,
                _token("a"),
                valid_end_time=AS_OF - timedelta(hours=1),
                is_valid_forever=True,
            )
        ]
    )

    assert projected.student_relations[0].relation_state == RELATION_BLOCKED


def test_ordinary_finite_interval_is_not_an_effective_blacklist() -> None:
    start = AS_OF
    end = AS_OF + timedelta(hours=1)
    record = _record(
        1,
        _token("a"),
        valid_start_time=start,
        valid_end_time=end,
        is_valid_forever=False,
    )

    at_start = _rebuild([record], business_as_of=start)
    before_end = _rebuild(
        [record],
        business_as_of=end - timedelta(microseconds=1),
    )
    at_end = _rebuild([record], business_as_of=end)

    assert at_start.student_relations[0].relation_state == RELATION_NOT_BLOCKED
    assert before_end.student_relations[0].relation_state == RELATION_NOT_BLOCKED
    assert at_end.student_relations[0].relation_state == RELATION_NOT_BLOCKED


def test_2999_end_year_is_the_only_end_time_sentinel_for_blacklist() -> None:
    sentinel = datetime(2999, 1, 1, tzinfo=UTC)
    projected = _rebuild(
        [
            _record(
                1,
                _token("a"),
                is_valid_forever=False,
                valid_end_time=sentinel,
            )
        ]
    )

    assert projected.student_relations[0].relation_state == RELATION_BLOCKED


def test_approved_permanent_relation_with_missing_start_stays_source_missing() -> None:
    record = _record(
        1,
        _token("a"),
        valid_start_time=None,
        add_time=None,
        source_timestamp=None,
    )
    projected = _rebuild([record])

    assert projected.student_relations[0].relation_state == (
        RELATION_SOURCE_MISSING
    )
    assert projected.threshold_state == THRESHOLD_SOURCE_MISSING
    assert projected.evidence_status == EVIDENCE_SOURCE_MISSING


@pytest.mark.parametrize(
    "record",
    [
        _record(
            1,
            _token("a"),
            is_valid_forever=False,
            valid_end_time=None,
        ),
        _record(
            1,
            _token("a"),
            is_valid_forever=False,
            valid_end_time=AS_OF - timedelta(days=2),
        ),
    ],
)
def test_non_permanent_non_sentinel_rows_are_confirmed_not_blocked(
    record: BlacklistSourceRecordV2,
) -> None:
    projected = _rebuild([record])

    assert projected.student_relations[0].relation_state == RELATION_NOT_BLOCKED
    assert projected.threshold_state == THRESHOLD_SUPPRESSED
    assert projected.evidence_status == EVIDENCE_CONFIRMED


def test_unknown_permanent_flag_does_not_make_a_finite_interval_valid() -> None:
    start = AS_OF - timedelta(days=1)
    end = AS_OF + timedelta(days=1)
    record = _record(
        1,
        _token("a"),
        valid_start_time=start,
        valid_end_time=end,
        is_valid_forever=None,
    )

    during = _rebuild([record])
    after = _rebuild(
        [record],
        business_as_of=end + timedelta(seconds=1),
    )

    assert during.student_relations[0].relation_state == RELATION_NOT_BLOCKED
    assert after.student_relations[0].relation_state == RELATION_NOT_BLOCKED


def test_active_overlap_wins_over_an_unknown_row_for_the_same_student() -> None:
    projected = _rebuild(
        [
            _record(1, _token("a")),
            _record(
                2,
                _token("a"),
                valid_start_time=None,
                add_time=None,
                source_timestamp=None,
            ),
        ]
    )

    relation = projected.student_relations[0]
    assert relation.relation_state == RELATION_BLOCKED
    assert len(relation.active_source_ids) == 1
    assert len(relation.source_missing_ids) == 1
    assert projected.threshold_state == THRESHOLD_SUPPRESSED


def test_unknown_distinct_student_prevents_false_threshold_suppression() -> None:
    projected = _rebuild(
        [
            _record(1, _token("a")),
            _record(
                2,
                _token("b"),
                valid_start_time=None,
                add_time=None,
                source_timestamp=None,
            ),
        ]
    )

    assert projected.distinct_active_student_count == 1
    assert projected.source_missing_student_tokens == (_token("b"),)
    assert projected.threshold_state == THRESHOLD_SOURCE_MISSING


def test_incomplete_source_collection_cannot_prove_below_threshold() -> None:
    incomplete = _rebuild(
        [],
        source_collection_complete=False,
    )
    complete = _rebuild([], source_collection_complete=True)

    assert incomplete.threshold_state == THRESHOLD_SOURCE_MISSING
    assert complete.threshold_state == THRESHOLD_SUPPRESSED


def test_two_confirmed_students_prove_active_even_if_collection_is_incomplete() -> None:
    projected = _rebuild(
        [_record(1, _token("a")), _record(2, _token("b"))],
        source_collection_complete=False,
    )

    assert projected.threshold_state == THRESHOLD_ACTIVE
    assert projected.evidence_status == EVIDENCE_CONFIRMED


def test_region_thresholds_are_rebuilt_independently() -> None:
    ovs = rebuild_blacklist_threshold_v2(
        source_region="ovs",
        teacher_id=TEACHER,
        source_records=[
            BlacklistSourceRecordV2(
                source_region="ovs",
                source_id=TypedBlacklistIdV2("TEXT", "row-a"),
                teacher_id=TEACHER,
                student_token="student-a",
                valid_start_time=AS_OF - timedelta(days=1),
                is_valid_forever=True,
            ),
            BlacklistSourceRecordV2(
                source_region="ovs",
                source_id=TypedBlacklistIdV2("TEXT", "row-b"),
                teacher_id=TEACHER,
                student_token="student-b",
                valid_start_time=AS_OF - timedelta(days=1),
                is_valid_forever=True,
            ),
        ],
        business_as_of=AS_OF,
        source_collection_complete=True,
    )
    dom = _rebuild([_record(1, _token("a"))])

    assert ovs.threshold_state == THRESHOLD_ACTIVE
    assert dom.threshold_state == THRESHOLD_SUPPRESSED


def test_typed_source_ids_do_not_collapse_numeric_and_text_identities() -> None:
    projected = _rebuild(
        [
            _record(9, _token("a")),
            _record(
                10,
                _token("a"),
                source_id=TypedBlacklistIdV2("TEXT", "9"),
            ),
        ]
    )

    relation = projected.student_relations[0]
    assert [
        (item.id_type, item.canonical_value)
        for item in relation.active_source_ids
    ] == [("NUMERIC", "9"), ("TEXT", "9")]


def test_numeric_source_id_canonicalization_detects_duplicate_identity() -> None:
    with pytest.raises(
        DtsBlacklistRuleError,
        match="^DTS_BLACKLIST_SOURCE_ID_DUPLICATE$",
    ):
        _rebuild(
            [
                _record("009", _token("a")),
                _record(9, _token("b")),
            ]
        )


def test_typed_teacher_identity_mismatch_fails_closed() -> None:
    with pytest.raises(
        DtsBlacklistRuleError,
        match="^DTS_BLACKLIST_RECORD_TEACHER_MISMATCH$",
    ):
        _rebuild(
            [
                _record(
                    1,
                    _token("a"),
                    teacher_id=TypedBlacklistIdV2("TEXT", "7"),
                )
            ]
        )


@pytest.mark.parametrize(
    ("field_name", "value", "error"),
    [
        (
            "business_as_of",
            datetime(2026, 8, 22, 12, 0),
            "DTS_BLACKLIST_BUSINESS_AS_OF_INVALID",
        ),
        (
            "valid_start_time",
            datetime(2026, 8, 21, 12, 0),
            "DTS_BLACKLIST_VALID_START_TIME_INVALID",
        ),
        (
            "valid_end_time",
            datetime(2026, 8, 23, 12, 0),
            "DTS_BLACKLIST_VALID_END_TIME_INVALID",
        ),
        (
            "add_time",
            datetime(2026, 8, 21, 12, 0),
            "DTS_BLACKLIST_ADD_TIME_INVALID",
        ),
        (
            "source_timestamp",
            datetime(2026, 8, 21, 12, 0),
            "DTS_BLACKLIST_SOURCE_TIMESTAMP_INVALID",
        ),
    ],
)
def test_all_business_times_must_be_timezone_aware(
    field_name: str,
    value: datetime,
    error: str,
) -> None:
    if field_name == "business_as_of":
        records = [_record(1, _token("a"))]
        overrides = {field_name: value}
    else:
        records = [_record(1, _token("a"), **{field_name: value})]
        overrides = {}

    with pytest.raises(DtsBlacklistRuleError, match=f"^{error}$"):
        _rebuild(records, **overrides)


@pytest.mark.parametrize("flag", [2, -1, "1", Decimal("0.5"), 1.0])
def test_invalid_permanent_flag_fails_closed(flag: object) -> None:
    with pytest.raises(
        DtsBlacklistRuleError,
        match="^DTS_BLACKLIST_PERMANENT_FLAG_INVALID$",
    ):
        _rebuild([_record(1, _token("a"), is_valid_forever=flag)])


def test_domestic_raw_or_malformed_student_identity_is_rejected() -> None:
    with pytest.raises(
        DtsBlacklistRuleError,
        match="^DTS_BLACKLIST_DOM_STUDENT_TOKEN_INVALID$",
    ):
        _rebuild([_record(1, "raw-dom-student-id")])
