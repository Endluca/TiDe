from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.dts_absence_rules_v2 import (
    AbsenceMappingDisposition,
    AbsenceReasonFact,
    map_absence_reason_to_participation,
    select_current_absence_reasons,
)


@dataclass(frozen=True)
class _Participation:
    participation_seq: int
    teacher_id: str
    teacher_id_type: str | None
    participation_status: str | None
    ended_at: datetime | None


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 22, hour, minute, tzinfo=timezone.utc)


def _reason(
    reason_id: str,
    *,
    teacher_id: str | None = "A",
    teacher_id_type: str | None = "TEXT",
    reason_type: str | None = "No Notification",
    add_time: datetime | None = None,
    source_timestamp: datetime | None = None,
    revision: int = 1,
    deleted: bool = False,
) -> AbsenceReasonFact:
    return AbsenceReasonFact(
        source_reason_id=reason_id,
        source_reason_id_type="NUMERIC",
        source_row_revision=revision,
        teacher_id=teacher_id,
        teacher_id_type=teacher_id_type,
        reason_type=reason_type,
        add_time=add_time,
        source_timestamp=source_timestamp,
        is_deleted=deleted,
    )


def _a_b_a_participations() -> tuple[_Participation, ...]:
    return (
        _Participation(1, "A", "TEXT", "t_absent", _at(10)),
        _Participation(2, "B", "TEXT", "t_absent", _at(11)),
        _Participation(3, "A", "TEXT", "t_absent", _at(12)),
    )


@pytest.mark.parametrize(
    ("reason_time", "expected_seq"),
    [
        (_at(9), 1),
        (_at(10), 1),
        (_at(10, 30), 1),
        (_at(12), 3),
        (_at(13), 3),
    ],
)
def test_absence_reason_maps_to_the_unique_a_b_a_participation(
    reason_time: datetime,
    expected_seq: int,
) -> None:
    result = map_absence_reason_to_participation(
        _reason("1", add_time=reason_time),
        _a_b_a_participations(),
    )

    assert result.disposition == AbsenceMappingDisposition.MATCHED
    assert result.participation_seq == expected_seq


def test_reason_early_or_late_uses_same_mapping_after_absence_materializes() -> None:
    reason = _reason("1", add_time=_at(9))

    before = map_absence_reason_to_participation(reason, ())
    after = map_absence_reason_to_participation(
        reason,
        _a_b_a_participations(),
    )

    assert before.disposition == AbsenceMappingDisposition.PENDING_DATA
    assert before.error_code == "PENDING_DATA:ABSENCE_PARTICIPATION_AMBIGUOUS"
    assert after.participation_seq == 1


def test_missing_business_time_falls_back_to_source_timestamp() -> None:
    result = map_absence_reason_to_participation(
        _reason("1", source_timestamp=_at(10, 30)),
        _a_b_a_participations(),
    )

    assert result.participation_seq == 1


def test_undated_matching_absence_keeps_reason_pending() -> None:
    participations = (
        _Participation(1, "A", "TEXT", "t_absent", _at(10)),
        _Participation(3, "A", "TEXT", "t_absent", None),
    )

    result = map_absence_reason_to_participation(
        _reason("1", add_time=_at(11)),
        participations,
    )

    assert result.disposition == AbsenceMappingDisposition.PENDING_DATA
    assert result.participation_seq is None


def test_teacher_type_is_part_of_absence_identity() -> None:
    result = map_absence_reason_to_participation(
        _reason(
            "1",
            teacher_id="9",
            teacher_id_type="NUMERIC",
            add_time=_at(11),
        ),
        (_Participation(1, "9", "TEXT", "t_absent", _at(10)),),
    )

    assert result.disposition == AbsenceMappingDisposition.PENDING_DATA


def test_numeric_teacher_identity_is_canonicalized_before_mapping() -> None:
    result = map_absence_reason_to_participation(
        _reason(
            "1",
            teacher_id="009.0",
            teacher_id_type="NUMERIC",
            add_time=_at(11),
        ),
        (_Participation(1, "9", "NUMERIC", "t_absent", _at(10)),),
    )

    assert result.disposition == AbsenceMappingDisposition.MATCHED
    assert result.participation_seq == 1


def test_latest_reason_even_null_suppresses_old_reason_and_delete_restores_next() -> None:
    participations = _a_b_a_participations()
    memo = _reason(
        "1",
        add_time=_at(10, 5),
        reason_type="Unfilled Lesson Memo",
    )
    cleared = _reason("2", add_time=_at(10, 6), reason_type=None)

    selected = select_current_absence_reasons(
        (memo, cleared),
        participations,
    ).for_participation(1)
    assert selected is not None
    assert selected.reason.source_reason_id == "2"
    assert selected.no_notice is None
    assert selected.task_code is None

    restored = select_current_absence_reasons(
        (
            memo,
            _reason(
                "2",
                add_time=_at(10, 6),
                reason_type=None,
                deleted=True,
            ),
        ),
        participations,
    ).for_participation(1)
    assert restored is not None
    assert restored.reason.source_reason_id == "1"
    assert restored.no_notice is False
    assert restored.task_code == "P-REL-MEMO"


def test_no_notification_selects_attendance_task_and_no_notice() -> None:
    selected = select_current_absence_reasons(
        (_reason("1", add_time=_at(10, 5)),),
        _a_b_a_participations(),
    ).for_participation(1)

    assert selected is not None
    assert selected.no_notice is True
    assert selected.task_code == "P-REL-ATTENDANCE"


def test_naive_reason_time_is_rejected_instead_of_guessing_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _reason("1", add_time=datetime(2026, 8, 22, 10, 0))
