from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import permutations

import pytest

from app.dts_favorite_rules_v2 import (
    FavoriteCandidateDisposition,
    FavoriteCourseObservation,
    FavoriteInterval,
    FavoriteObservationStatus,
    FavoriteSourceRecord,
    evaluate_favorite_at,
    favorite_interval_from_source_record,
    favorite_observed_at,
    rebuild_favorite_relationship_current,
    relationship_change_requires_observation_rebuild,
    select_favorite_attribution_candidate,
)


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def _source(
    source_id: str,
    *,
    add_time: datetime | None = None,
    source_timestamp: datetime | None = None,
    deleted: bool = False,
) -> FavoriteSourceRecord:
    return FavoriteSourceRecord(
        source_id=source_id,
        source_id_type="NUMERIC",
        teacher_id="T1",
        teacher_id_type="TEXT",
        student_token="S1",
        add_time=add_time,
        source_timestamp=(
            source_timestamp if source_timestamp is not None else _at(22)
        ),
        is_deleted=deleted,
    )


def test_current_favorite_does_not_require_a_completed_course() -> None:
    result = rebuild_favorite_relationship_current(
        (_source("1", add_time=_at(20)),),
        scope_complete=False,
    )

    assert result.is_favorited is True
    assert result.effective_time_evidence_status == "CONFIRMED"


def test_numeric_teacher_identity_is_canonicalized() -> None:
    row = FavoriteSourceRecord(
        source_id="1.0",
        source_id_type="NUMERIC",
        teacher_id="009.0",
        teacher_id_type="NUMERIC",
        student_token="S1",
        add_time=_at(20),
        source_timestamp=_at(22),
    )

    assert row.source_id == "1"
    assert row.teacher_id == "9"


def test_relationship_rejects_missing_teacher_id_type() -> None:
    with pytest.raises(ValueError, match="teacher id type"):
        FavoriteSourceRecord(
            source_id="1",
            source_id_type="NUMERIC",
            teacher_id="9",
            teacher_id_type=None,  # type: ignore[arg-type]
            student_token="S1",
            add_time=_at(20),
            source_timestamp=_at(22),
        )


def test_relationship_pair_rejects_numeric_text_teacher_collision() -> None:
    numeric = FavoriteSourceRecord(
        source_id="1",
        source_id_type="NUMERIC",
        teacher_id="009.0",
        teacher_id_type="NUMERIC",
        student_token="S1",
        add_time=_at(20),
        source_timestamp=_at(22),
    )
    text = FavoriteSourceRecord(
        source_id="2",
        source_id_type="NUMERIC",
        teacher_id="9",
        teacher_id_type="TEXT",
        student_token="S1",
        add_time=_at(20),
        source_timestamp=_at(22),
    )

    with pytest.raises(ValueError, match="more than one teacher/student pair"):
        rebuild_favorite_relationship_current(
            (numeric, text),
            scope_complete=True,
        )


def test_deleting_one_duplicate_does_not_clear_another_current_favorite() -> None:
    result = rebuild_favorite_relationship_current(
        (
            _source("1", add_time=_at(20), deleted=True),
            _source("2", add_time=_at(21)),
        ),
        scope_complete=True,
    )

    assert result.is_favorited is True
    assert result.latest_business_effective_at == _at(22)


def test_empty_current_relation_is_false_only_for_complete_scope() -> None:
    assert rebuild_favorite_relationship_current((), scope_complete=False).is_favorited is None
    assert rebuild_favorite_relationship_current((), scope_complete=True).is_favorited is False


def test_current_crud_state_does_not_hide_missing_business_time() -> None:
    active_without_start = _source("1", add_time=None, source_timestamp=_at(20))
    active_result = rebuild_favorite_relationship_current(
        (active_without_start,), scope_complete=True
    )
    assert active_result.is_favorited is True
    assert active_result.effective_time_evidence_status == "SOURCE_MISSING"

    deleted_without_end = FavoriteSourceRecord(
        source_id="1",
        source_id_type="NUMERIC",
        teacher_id="T1",
        teacher_id_type="TEXT",
        student_token="S1",
        add_time=_at(20),
        source_timestamp=None,
        is_deleted=True,
    )
    deleted_result = rebuild_favorite_relationship_current(
        (deleted_without_end,), scope_complete=True
    )
    assert deleted_result.is_favorited is False
    assert deleted_result.effective_time_evidence_status == "SOURCE_MISSING"


def test_observation_is_exactly_24_hours_after_authoritative_completion() -> None:
    completion = _at(20, 10)
    assert favorite_observed_at(completion) == completion + timedelta(hours=24)


def test_favorite_interval_uses_left_closed_right_open_business_time() -> None:
    interval = FavoriteInterval(
        "T1", "TEXT", "S1", _at(20), _at(22), True, True
    )

    at_start = evaluate_favorite_at(
        (interval,), observed_at=_at(20), history_complete=True
    )
    at_end = evaluate_favorite_at(
        (interval,), observed_at=_at(22), history_complete=True
    )

    assert at_start.status == FavoriteObservationStatus.CONFIRMED_TRUE
    assert at_end.status == FavoriteObservationStatus.CONFIRMED_FALSE


def test_interval_rejects_missing_or_mixed_teacher_identity_type() -> None:
    with pytest.raises(ValueError, match="teacher id type"):
        FavoriteInterval(
            "9",
            None,  # type: ignore[arg-type]
            "S1",
            _at(20),
            _at(22),
            True,
            True,
        )

    numeric = FavoriteInterval(
        "009.0", "NUMERIC", "S1", _at(20), _at(22), True, True
    )
    text = FavoriteInterval(
        "9", "TEXT", "S1", _at(20), _at(22), True, True
    )
    with pytest.raises(ValueError, match="more than one pair"):
        evaluate_favorite_at(
            (numeric, text),
            observed_at=_at(21),
            history_complete=True,
        )


def test_missing_add_time_waits_even_when_source_timestamp_is_early() -> None:
    unknown_start = favorite_interval_from_source_record(
        _source("1", add_time=None, source_timestamp=_at(20))
    )

    result = evaluate_favorite_at(
        (unknown_start,), observed_at=_at(22), history_complete=True
    )

    assert result.status == FavoriteObservationStatus.WAITING_EVIDENCE
    assert result.error_code == "SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING"

    incomplete_history = evaluate_favorite_at(
        (unknown_start,), observed_at=_at(22), history_complete=False
    )
    assert incomplete_history.status == FavoriteObservationStatus.WAITING_EVIDENCE


def test_delete_without_source_timestamp_waits_for_effective_time() -> None:
    deleted = FavoriteSourceRecord(
        source_id="1",
        source_id_type="NUMERIC",
        teacher_id="T1",
        teacher_id_type="TEXT",
        student_token="S1",
        add_time=_at(20),
        source_timestamp=None,
        is_deleted=True,
    )

    interval = favorite_interval_from_source_record(deleted)
    result = evaluate_favorite_at(
        (interval,), observed_at=_at(22), history_complete=True
    )

    assert interval.end_at is None
    assert interval.end_evidence_confirmed is False
    assert result.status == FavoriteObservationStatus.WAITING_EVIDENCE


def test_unknown_end_after_a_future_start_cannot_cross_the_observation() -> None:
    interval = FavoriteInterval(
        "T1",
        "TEXT",
        "S1",
        _at(23),
        None,
        True,
        False,
    )

    result = evaluate_favorite_at(
        (interval,), observed_at=_at(22), history_complete=True
    )

    assert result.status == FavoriteObservationStatus.CONFIRMED_FALSE
    assert result.relation_state is False


def test_unknown_start_before_a_confirmed_end_cannot_cross_the_observation() -> None:
    interval = FavoriteInterval(
        "T1",
        "TEXT",
        "S1",
        None,
        _at(21),
        False,
        True,
    )

    result = evaluate_favorite_at(
        (interval,), observed_at=_at(22), history_complete=True
    )

    assert result.status == FavoriteObservationStatus.CONFIRMED_FALSE
    assert result.relation_state is False


def test_unknown_overlap_does_not_hide_another_confirmed_active_relation() -> None:
    confirmed = FavoriteInterval(
        "T1", "TEXT", "S1", _at(20), None, True, True
    )
    unknown = FavoriteInterval(
        "T1", "TEXT", "S1", None, None, False, False
    )

    result = evaluate_favorite_at(
        (confirmed, unknown), observed_at=_at(22), history_complete=True
    )

    assert result.status == FavoriteObservationStatus.CONFIRMED_TRUE
    assert result.relation_state is True


def test_delete_boundary_at_observation_is_already_not_favorited() -> None:
    deleted = _source(
        "1",
        add_time=_at(20),
        source_timestamp=_at(22),
        deleted=True,
    )

    result = evaluate_favorite_at(
        (favorite_interval_from_source_record(deleted),),
        observed_at=_at(22),
        history_complete=True,
    )

    assert result.status == FavoriteObservationStatus.CONFIRMED_FALSE


def test_history_incomplete_is_not_interpreted_as_false() -> None:
    result = evaluate_favorite_at((), observed_at=_at(22), history_complete=False)

    assert result.status == FavoriteObservationStatus.WAITING_HISTORY
    assert result.relation_state is None


def test_late_event_rebuilds_only_when_actual_effective_time_reaches_observation() -> None:
    observed = _at(22)

    assert relationship_change_requires_observation_rebuild(
        business_effective_at=_at(21), observed_at=observed
    ) is True
    assert relationship_change_requires_observation_rebuild(
        business_effective_at=observed, observed_at=observed
    ) is True
    assert relationship_change_requires_observation_rebuild(
        business_effective_at=_at(23), observed_at=observed
    ) is False
    assert relationship_change_requires_observation_rebuild(
        business_effective_at=None, observed_at=observed
    ) is None


def _observation(
    appoint_id: str,
    *,
    observed_at: datetime,
    appoint_id_type: str = "NUMERIC",
    teacher_id: str = "T1",
    teacher_id_type: str | None = "TEXT",
    status: FavoriteObservationStatus = FavoriteObservationStatus.CONFIRMED_TRUE,
) -> FavoriteCourseObservation:
    return FavoriteCourseObservation(
        source_region="dom",
        source_appoint_id=appoint_id,
        source_appoint_id_type=appoint_id_type,
        observation_revision=1,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,  # type: ignore[arg-type]
        student_token="S1",
        completion_participation_seq=1,
        observed_at=observed_at,
        status=status,
    )


def test_one_teacher_student_pair_selects_only_the_earliest_course() -> None:
    later = _observation("1", observed_at=_at(23))
    same_time_ten = _observation("10", observed_at=_at(22))
    same_time_nine = _observation("9", observed_at=_at(22))

    result = select_favorite_attribution_candidate(
        (later, same_time_ten, same_time_nine)
    )

    assert result.disposition == FavoriteCandidateDisposition.SELECTED
    assert result.selected is same_time_nine


def test_same_time_numeric_candidate_is_stable_across_arrival_orders() -> None:
    numeric_nine = _observation("9", observed_at=_at(22))
    numeric_ten = _observation("10", observed_at=_at(22))

    for order in permutations((numeric_ten, numeric_nine)):
        result = select_favorite_attribution_candidate(order)
        assert result.selected is numeric_nine


def test_same_time_text_candidate_uses_utf8_bytes_across_arrival_orders() -> None:
    text_nine = _observation(
        "9", observed_at=_at(22), appoint_id_type="TEXT"
    )
    text_ten = _observation(
        "10", observed_at=_at(22), appoint_id_type="TEXT"
    )

    for order in permutations((text_nine, text_ten)):
        result = select_favorite_attribution_candidate(order)
        assert result.selected is text_ten


def test_observation_requires_typed_teacher_identity() -> None:
    with pytest.raises(ValueError, match="teacher id type"):
        _observation(
            "1",
            observed_at=_at(22),
            teacher_id="9",
            teacher_id_type=None,
        )


def test_observation_rejects_noncanonical_source_region() -> None:
    with pytest.raises(ValueError, match="source region"):
        FavoriteCourseObservation(
            source_region="dmo",  # type: ignore[arg-type]
            source_appoint_id="1",
            source_appoint_id_type="NUMERIC",
            observation_revision=1,
            teacher_id="T1",
            teacher_id_type="TEXT",
            student_token="S1",
            completion_participation_seq=1,
            observed_at=_at(22),
            status=FavoriteObservationStatus.CONFIRMED_TRUE,
        )


def test_candidate_and_held_observation_reject_teacher_type_collision() -> None:
    numeric = _observation(
        "1",
        observed_at=_at(22),
        teacher_id="009.0",
        teacher_id_type="NUMERIC",
    )
    text = _observation(
        "2",
        observed_at=_at(23),
        teacher_id="9",
        teacher_id_type="TEXT",
    )

    with pytest.raises(ValueError, match="more than one pair"):
        select_favorite_attribution_candidate((numeric, text))
    with pytest.raises(ValueError, match="more than one pair"):
        select_favorite_attribution_candidate(
            (numeric,),
            held_observation=text,
        )


def test_held_attribution_does_not_switch_while_evidence_is_unknown() -> None:
    held = _observation(
        "1",
        observed_at=_at(21),
        status=FavoriteObservationStatus.WAITING_EVIDENCE,
    )
    alternate = _observation("2", observed_at=_at(22))

    result = select_favorite_attribution_candidate(
        (alternate,), held_observation=held
    )

    assert result.disposition == FavoriteCandidateDisposition.HOLD_CURRENT_ATTRIBUTION
    assert result.selected is held

    retrying = _observation(
        "1",
        observed_at=_at(21),
        status=FavoriteObservationStatus.RETRY,
    )
    retry_result = select_favorite_attribution_candidate(
        (alternate,), held_observation=retrying
    )
    assert retry_result.disposition == FavoriteCandidateDisposition.HOLD_CURRENT_ATTRIBUTION


def test_confirmed_held_observation_is_not_omitted_from_reselection() -> None:
    held = _observation("1", observed_at=_at(21))
    later = _observation("2", observed_at=_at(22))

    result = select_favorite_attribution_candidate(
        (later,), held_observation=held
    )

    assert result.disposition == FavoriteCandidateDisposition.SELECTED
    assert result.selected is held
