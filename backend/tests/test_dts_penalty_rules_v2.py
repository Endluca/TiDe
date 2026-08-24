from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.dts_penalty_rules_v2 import (
    PENALTY_PARTICIPATION_AMBIGUOUS,
    PENALTY_PARTICIPATION_TEACHER_MISSING,
    PenaltyFlags,
    PenaltyParticipation,
    PenaltyRecord,
    aggregate_penalty_flags,
    aggregate_penalty_records,
    evaluate_penalty_record,
    resolve_penalty_participation,
)


UTC = timezone.utc
START = datetime(2026, 8, 22, 2, 0, tzinfo=UTC)


def participation(
    seq: int,
    teacher_id: str | None,
    *,
    teacher_id_type: str | None = "TEXT",
    assigned_at: object = START,
    ended_at: object = None,
    completion: bool = False,
) -> PenaltyParticipation:
    return PenaltyParticipation(
        participation_seq=seq,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        assigned_at=assigned_at,
        ended_at=ended_at,
        is_completion=completion,
    )


def penalty(
    *,
    teacher_id: str | None = "A",
    teacher_id_type: str | None = "TEXT",
    lesson_start_time: object = START,
    in_time: object = START,
    out_time: object = START + timedelta(minutes=30),
    appeal_status: object = 1,
) -> PenaltyRecord:
    return PenaltyRecord(
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        lesson_start_time=lesson_start_time,
        in_time=in_time,
        out_time=out_time,
        appeal_status=appeal_status,
    )


def test_completion_same_typed_teacher_wins_without_time_evidence() -> None:
    result = resolve_penalty_participation(
        penalty(lesson_start_time=None),
        (
            participation(1, "A", assigned_at=None),
            participation(2, "B", assigned_at=None),
            participation(3, "A", assigned_at=None, completion=True),
        ),
    )

    assert result.is_mapped is True
    assert result.participation_seq == 3
    assert result.pending_error is None


def test_completion_literal_with_different_type_does_not_match() -> None:
    result = resolve_penalty_participation(
        penalty(
            teacher_id="9",
            teacher_id_type="TEXT",
            lesson_start_time=START + timedelta(minutes=40),
        ),
        (
            participation(
                1,
                "9",
                teacher_id_type="NUMERIC",
                completion=True,
            ),
            participation(
                2,
                "9",
                teacher_id_type="TEXT",
                assigned_at=START + timedelta(minutes=30),
            ),
        ),
    )

    assert result.participation_seq == 2


def test_numeric_teacher_identity_compares_canonical_values() -> None:
    result = resolve_penalty_participation(
        penalty(
            teacher_id="009",
            teacher_id_type="NUMERIC",
            lesson_start_time=None,
        ),
        (
            participation(
                1,
                "9.0",
                teacher_id_type="NUMERIC",
                assigned_at=None,
                completion=True,
            ),
        ),
    )

    assert result.participation_seq == 1


@pytest.mark.parametrize(
    ("teacher_id", "teacher_id_type"),
    [(None, None), ("A", None), ("", "TEXT"), ("invalid", "NUMERIC")],
)
def test_missing_or_invalid_penalty_teacher_identity_stays_pending(
    teacher_id: str | None,
    teacher_id_type: str | None,
) -> None:
    result = resolve_penalty_participation(
        penalty(teacher_id=teacher_id, teacher_id_type=teacher_id_type),
        (participation(1, "A"),),
    )

    assert result.participation_seq is None
    assert result.pending_error == PENALTY_PARTICIPATION_TEACHER_MISSING


def test_interval_is_left_closed_and_right_open() -> None:
    rows = (
        participation(1, "A", ended_at=START + timedelta(minutes=30)),
        participation(
            2,
            "A",
            assigned_at=START + timedelta(minutes=30),
        ),
    )

    at_left = resolve_penalty_participation(
        penalty(lesson_start_time=START),
        rows,
    )
    at_boundary = resolve_penalty_participation(
        penalty(lesson_start_time=START + timedelta(minutes=30)),
        rows,
    )

    assert at_left.participation_seq == 1
    assert at_boundary.participation_seq == 2


def test_interval_comparison_uses_aware_instants_across_offsets() -> None:
    result = resolve_penalty_participation(
        penalty(lesson_start_time="2026-08-22T10:15:00+08:00"),
        (
            participation(
                1,
                "A",
                assigned_at="2026-08-22T02:00:00Z",
                ended_at="2026-08-22T02:30:00+00:00",
            ),
        ),
    )

    assert result.participation_seq == 1


@pytest.mark.parametrize(
    "lesson_start_time",
    [None, "invalid", "2026-08-22T10:00:00", datetime(2026, 8, 22, 10, 0)],
)
def test_missing_invalid_or_naive_lesson_time_is_ambiguous(
    lesson_start_time: object,
) -> None:
    result = resolve_penalty_participation(
        penalty(lesson_start_time=lesson_start_time),
        (participation(1, "A"),),
    )

    assert result.pending_error == PENALTY_PARTICIPATION_AMBIGUOUS


@pytest.mark.parametrize(
    "broken",
    [
        participation(1, "A", assigned_at=None),
        participation(1, "A", assigned_at="2026-08-22T02:00:00"),
        participation(1, "A", ended_at="invalid"),
        participation(1, "A", ended_at=START - timedelta(seconds=1)),
        participation(1, "A", teacher_id_type=None),
    ],
)
def test_incomplete_participation_evidence_is_ambiguous(
    broken: PenaltyParticipation,
) -> None:
    result = resolve_penalty_participation(penalty(), (broken,))

    assert result.pending_error == PENALTY_PARTICIPATION_AMBIGUOUS


def test_zero_or_multiple_interval_matches_are_ambiguous() -> None:
    gap = resolve_penalty_participation(
        penalty(lesson_start_time=START + timedelta(hours=1)),
        (
            participation(1, "A", ended_at=START + timedelta(minutes=30)),
        ),
    )
    overlap = resolve_penalty_participation(
        penalty(lesson_start_time=START + timedelta(minutes=15)),
        (
            participation(1, "A", ended_at=START + timedelta(hours=1)),
            participation(
                2,
                "A",
                assigned_at=START + timedelta(minutes=10),
            ),
        ),
    )

    assert gap.pending_error == PENALTY_PARTICIPATION_AMBIGUOUS
    assert overlap.pending_error == PENALTY_PARTICIPATION_AMBIGUOUS


def test_appeal_two_is_explicit_false_and_null_is_unknown() -> None:
    excluded = evaluate_penalty_record(
        penalty(
            appeal_status="2",
            lesson_start_time=None,
            in_time=None,
            out_time=None,
        )
    )
    unknown = evaluate_penalty_record(penalty(appeal_status=None))

    assert excluded == PenaltyFlags(False, False, "CONFIRMED", "CONFIRMED")
    assert unknown == PenaltyFlags(
        None,
        None,
        "SOURCE_MISSING",
        "SOURCE_MISSING",
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(29, False), (30, False), (30.000001, True)],
)
def test_late_threshold_is_strictly_greater_than_30_seconds(
    seconds: float,
    expected: bool,
) -> None:
    result = evaluate_penalty_record(
        penalty(in_time=START + timedelta(seconds=seconds))
    )

    assert result.is_late is expected


@pytest.mark.parametrize(
    ("seconds_early", "expected"),
    [(29, False), (30, False), (30.000001, True)],
)
def test_early_threshold_is_strictly_greater_than_30_seconds(
    seconds_early: float,
    expected: bool,
) -> None:
    result = evaluate_penalty_record(
        penalty(
            out_time=START
            + timedelta(minutes=30)
            - timedelta(seconds=seconds_early)
        )
    )

    assert result.is_early is expected


def test_late_and_early_time_evidence_are_independent_and_must_be_aware() -> None:
    result = evaluate_penalty_record(
        penalty(
            in_time="2026-08-22T02:00:31",
            out_time=START + timedelta(minutes=30),
        )
    )

    assert result.is_late is None
    assert result.late_evidence_status == "SOURCE_MISSING"
    assert result.is_early is False
    assert result.early_evidence_status == "CONFIRMED"


def test_aggregate_true_wins_unknown_per_dimension() -> None:
    result = aggregate_penalty_flags(
        (
            PenaltyFlags(True, None, "CONFIRMED", "SOURCE_MISSING"),
            PenaltyFlags(None, False, "SOURCE_MISSING", "CONFIRMED"),
        ),
        scope_complete=False,
    )

    assert result.is_late is True
    assert result.late_evidence_status == "CONFIRMED"
    assert result.is_early is None
    assert result.early_evidence_status == "SOURCE_MISSING"


@pytest.mark.parametrize(
    ("scope_complete", "expected"),
    [(True, False), (False, None)],
)
def test_false_requires_complete_scope(
    scope_complete: bool,
    expected: bool | None,
) -> None:
    result = aggregate_penalty_records(
        (penalty(appeal_status=2), penalty(appeal_status=1)),
        scope_complete=scope_complete,
    )

    assert result.is_late is expected
    assert result.is_early is expected


@pytest.mark.parametrize(
    ("scope_complete", "expected"),
    [(True, False), (False, None)],
)
def test_empty_set_is_false_only_with_complete_scope(
    scope_complete: bool,
    expected: bool | None,
) -> None:
    result = aggregate_penalty_records((), scope_complete=scope_complete)

    assert result.is_late is expected
    assert result.is_early is expected


def test_unknown_record_blocks_false_even_with_complete_scope() -> None:
    result = aggregate_penalty_records(
        (penalty(appeal_status=2), penalty(appeal_status=None)),
        scope_complete=True,
    )

    assert result.is_late is None
    assert result.is_early is None
