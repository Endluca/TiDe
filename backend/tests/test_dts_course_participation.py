from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.dts_course_participation import (
    AppointSnapshot,
    AppointSourceVersion,
    AssignmentEventPhase,
    CompletionCorrectionDecision,
    CompletionConflictStatus,
    CompletionDecisionType,
    CorrectionDecisionHistoryError,
    DuplicateSourceRowRevisionError,
    EvidenceStatus,
    NonContiguousSourceRowRevisionError,
    ParticipationRole,
    ReductionDisposition,
    SourceEventReference,
    reduce_course_participations,
)


def snapshot(
    teacher_id: str | None,
    status: str | None = "on",
    *,
    teacher_id_type: str | None = None,
    use_point: str | None = "buy",
    end_time: str | None = None,
) -> AppointSnapshot:
    return AppointSnapshot(
        teacher_id=teacher_id,
        status=status,
        teacher_id_type=teacher_id_type,
        use_point=use_point,
        end_time=end_time,
    )


def complete_end_snapshot(
    teacher_id: str | None,
    *,
    teacher_id_type: str | None = None,
    status: str = "end",
    end_time: str = "2026-08-22T10:00:00+08:00",
) -> AppointSnapshot:
    return AppointSnapshot(
        teacher_id=teacher_id,
        status=status,
        teacher_id_type=teacher_id_type,
        use_point="buy",
        end_time=end_time,
        student_token="dom:v1:student-1",
        lesson_local_date="2026-08-22",
        lesson_local_time="10:00:00",
        is_peak=False,
    )


_SOURCE_TIMESTAMP_UNSET = object()


def version(
    revision: int,
    operation: str,
    *,
    before: AppointSnapshot | None = None,
    after: AppointSnapshot | None = None,
    phase: AssignmentEventPhase = AssignmentEventPhase.AFTER,
    epoch_id: str = "epoch-1",
    topic: str = "dom_appoint",
    partition: int = 0,
    offset: int | None = None,
    source_timestamp: datetime | None | object = _SOURCE_TIMESTAMP_UNSET,
) -> AppointSourceVersion:
    if source_timestamp is _SOURCE_TIMESTAMP_UNSET:
        effective_source_timestamp: datetime | None = datetime(
            2026, 8, 22, 2, 0, tzinfo=timezone.utc
        ) + timedelta(seconds=revision)
    else:
        if source_timestamp is not None and not isinstance(
            source_timestamp,
            datetime,
        ):
            raise TypeError("source_timestamp test value is invalid")
        effective_source_timestamp = source_timestamp
    return AppointSourceVersion(
        source_row_revision=revision,
        source_ref=SourceEventReference(
            source_partition_epoch_id=epoch_id,
            topic=topic,
            partition=partition,
            offset=revision if offset is None else offset,
            source_timestamp=effective_source_timestamp,
        ),
        operation=operation,
        before=before,
        after=after,
        assignment_phase=phase,
    )


def reduce(
    *versions: AppointSourceVersion,
    scope_complete: bool = False,
    decisions: tuple[CompletionCorrectionDecision, ...] = (),
):
    return reduce_course_participations(
        source_region="dom",
        source_appoint_id="9001",
        versions=versions,
        scope_complete=scope_complete,
        decisions=decisions,
    )


def correction_decision(
    decision_type: CompletionDecisionType,
    *,
    revision: int,
    target_seq: int | None = None,
    completion_snapshot: AppointSnapshot | None = None,
) -> CompletionCorrectionDecision:
    return CompletionCorrectionDecision(
        decision_id=f"decision-{revision}-{decision_type.value}",
        decision_type=decision_type,
        expected_source_revision=revision,
        target_participation_seq=target_seq,
        completion_snapshot=completion_snapshot,
    )


def test_t53_a_to_b_to_a_creates_three_participations_in_revision_order() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    result = reduce(
        # Deliberately unordered: source_row_revision is the sole apply order.
        version(3, "UPDATE", before=b_on, after=a_on),
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_on),
    )

    rows = result.state.participations
    assert [row.teacher_id for row in rows] == ["A", "B", "A"]
    assert [row.participation_status for row in rows] == [
        "t_absent",
        "t_absent",
        "on",
    ]
    assert [row.is_current for row in rows] == [False, False, True]
    assert rows[0].ended_at is not None
    assert rows[1].ended_at is not None
    assert rows[2].ended_at is None
    assert all(row.assigned_at is not None for row in rows)
    assert result.state.current_participation_seq == 3
    assert [item.source_row_revision for item in result.versions] == [1, 2, 3]


def test_t54_a_to_null_to_b_does_not_create_a_null_teacher_row() -> None:
    a_on = snapshot("A")
    no_teacher = snapshot(None)
    b_on = snapshot("B")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=no_teacher),
        version(3, "UPDATE", before=no_teacher, after=b_on),
    )

    rows = result.state.participations
    assert [(row.participation_seq, row.teacher_id) for row in rows] == [
        (1, "A"),
        (2, "B"),
    ]
    assert rows[0].participation_status == "t_absent"
    assert rows[0].ended_at is not None
    assert rows[1].is_current is True
    assert result.state.current_teacher_id == "B"


def test_same_teacher_literal_with_different_source_types_is_a_substitution() -> None:
    numeric = snapshot("9", teacher_id_type="NUMERIC")
    text = snapshot("9", teacher_id_type="TEXT")

    result = reduce(
        version(1, "INSERT", after=numeric),
        version(2, "UPDATE", before=numeric, after=text),
    )

    assert [
        (
            row.teacher_id_type,
            row.teacher_id,
            row.participation_status,
            row.is_current,
        )
        for row in result.state.participations
    ] == [
        ("NUMERIC", "9", "t_absent", False),
        ("TEXT", "9", "on", True),
    ]
    assert result.state.current_teacher_id == "9"
    assert result.state.current_teacher_id_type == "TEXT"


def test_typed_completion_transfer_uses_the_target_teacher_identity() -> None:
    numeric_end = complete_end_snapshot("9", teacher_id_type="NUMERIC")
    text_end = complete_end_snapshot("9", teacher_id_type="TEXT")
    invalid_transfer = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=2,
        target_seq=2,
        completion_snapshot=numeric_end,
    )

    with pytest.raises(
        CorrectionDecisionHistoryError,
        match="TRANSFER target or completion snapshot is invalid",
    ):
        reduce(
            version(1, "INSERT", after=numeric_end),
            version(2, "UPDATE", before=numeric_end, after=text_end),
            decisions=(invalid_transfer,),
        )

    transfer = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=2,
        target_seq=2,
        completion_snapshot=text_end,
    )
    result = reduce(
        version(1, "INSERT", after=numeric_end),
        version(2, "UPDATE", before=numeric_end, after=text_end),
        decisions=(transfer,),
    )

    old, target = result.state.participations
    assert (old.teacher_id_type, old.teacher_id) == ("NUMERIC", "9")
    assert (target.teacher_id_type, target.teacher_id) == ("TEXT", "9")
    assert target.participation_role == ParticipationRole.COMPLETION
    assert result.state.completion_teacher_id == "9"
    assert result.state.completion_teacher_id_type == "TEXT"


def test_resolved_conflict_signature_includes_teacher_id_type() -> None:
    numeric_end = complete_end_snapshot("9", teacher_id_type="NUMERIC")
    text_end = complete_end_snapshot("9", teacher_id_type="TEXT")
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )

    result = reduce(
        version(1, "INSERT", after=numeric_end),
        version(2, "UPDATE", before=numeric_end, after=text_end),
        version(3, "UPDATE", before=text_end, after=numeric_end),
        decisions=(keep,),
    )

    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING
    assert result.state.completion_teacher_id == "9"
    assert result.state.completion_teacher_id_type == "NUMERIC"
    assert [row.teacher_id_type for row in result.state.participations] == [
        "NUMERIC",
        "TEXT",
        "NUMERIC",
    ]


def test_t55_teacher_transfer_and_end_in_one_update_freezes_new_teacher() -> None:
    a_on = snapshot("A", "on", end_time=None)
    b_end = complete_end_snapshot("B")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_end),
    )

    first, second = result.state.participations
    assert first.teacher_id == "A"
    assert first.participation_status == "t_absent"
    assert first.participation_role == ParticipationRole.NORMAL
    assert second.teacher_id == "B"
    assert second.participation_status == "end"
    assert second.participation_role == ParticipationRole.COMPLETION
    assert result.state.completion_participation_seq == 2
    assert result.state.completion_teacher_id == "B"


@pytest.mark.parametrize("teacher_id", ["A", None])
def test_t56_insert_end_freezes_only_when_teacher_exists(
    teacher_id: str | None,
) -> None:
    end = complete_end_snapshot(teacher_id)
    result = reduce(version(1, "INSERT", after=end))

    assert result.state.initial_completion_snapshot == end
    if teacher_id is not None:
        assert result.state.completion_teacher_id == teacher_id
        assert result.state.completion_participation_seq == 1
        assert result.state.completion_conflict_status == CompletionConflictStatus.NONE
        assert result.state.evidence_status == EvidenceStatus.CONFIRMED
    else:
        assert result.state.participations == ()
        assert result.state.completion_teacher_id is None
        assert result.state.completion_participation_seq is None
        assert (
            result.state.completion_conflict_status
            == CompletionConflictStatus.PENDING
        )
        assert result.state.evidence_status == EvidenceStatus.SOURCE_MISSING
        assert result.state.correction_case_key is not None


@pytest.mark.parametrize(
    "missing_field",
    [
        "end_time",
        "student_token",
        "is_peak",
        "lesson_local_date",
        "lesson_local_time",
    ],
)
def test_t56_incomplete_end_snapshot_still_freezes_teacher_as_source_missing(
    missing_field: str,
) -> None:
    incomplete = replace(complete_end_snapshot("A"), **{missing_field: None})
    result = reduce(version(1, "INSERT", after=incomplete))

    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_participation_seq == 1
    assert result.state.participations[0].participation_role == ParticipationRole.COMPLETION
    assert result.state.completion_snapshot == incomplete
    assert result.state.evidence_status == EvidenceStatus.SOURCE_MISSING
    assert result.state.completion_conflict_status == CompletionConflictStatus.NONE


def test_t56a_end_without_teacher_never_auto_freezes_later_teacher() -> None:
    empty_end = complete_end_snapshot(None)
    b_end = complete_end_snapshot("B")
    b_on = complete_end_snapshot("B", status="on")
    result = reduce(
        version(1, "INSERT", after=empty_end),
        version(2, "UPDATE", before=empty_end, after=b_end),
        version(3, "UPDATE", before=b_end, after=b_on),
        version(4, "DELETE", before=b_on),
    )

    assert result.state.initial_completion_snapshot == empty_end
    assert result.state.completion_teacher_id is None
    assert result.state.completion_participation_seq is None
    assert len(result.state.participations) == 1
    pending = result.state.participations[0]
    assert pending.teacher_id == "B"
    assert pending.participation_role == ParticipationRole.PENDING_CORRECTION
    assert pending.is_current is False
    assert pending.source_deleted is True
    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING
    assert result.state.evidence_status == EvidenceStatus.SOURCE_MISSING
    case_keys = {
        item.correction_case_key
        for item in result.versions
        if item.correction_case_key is not None
    }
    assert case_keys == {result.state.correction_case_key}


def test_t57_post_end_changes_keep_frozen_completion_and_one_case() -> None:
    a_end = complete_end_snapshot("A")
    a_on = complete_end_snapshot("A", status="on")
    b_end = complete_end_snapshot(
        "B", end_time="2026-08-22T10:30:00+08:00"
    )
    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=a_on),
        version(3, "UPDATE", before=a_on, after=a_end),
        version(4, "UPDATE", before=a_end, after=b_end),
        version(5, "DELETE", before=b_end),
    )

    assert result.state.initial_completion_snapshot == a_end
    assert result.state.completion_snapshot == a_end
    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_participation_seq == 1
    completion, pending = result.state.participations
    assert completion.participation_role == ParticipationRole.COMPLETION
    assert completion.participation_status == "end"
    assert pending.teacher_id == "B"
    assert pending.participation_role == ParticipationRole.PENDING_CORRECTION
    assert pending.source_deleted is True
    assert result.state.source_is_deleted is True
    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING
    case_keys = {
        item.correction_case_key
        for item in result.versions
        if item.correction_case_key is not None
    }
    assert case_keys == {result.state.correction_case_key}


def test_post_end_same_teacher_status_change_preserves_completion_end() -> None:
    a_end = complete_end_snapshot("A")
    a_on = complete_end_snapshot("A", status="on")

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=a_on),
    )

    completion = result.state.participations[0]
    assert result.state.current_snapshot == a_on
    assert completion.is_current is True
    assert completion.participation_role == ParticipationRole.COMPLETION
    assert completion.participation_status == "end"
    assert result.state.completion_snapshot == a_end
    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING


def test_t57b_keep_decision_survives_rebuild_and_irrelevant_update() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    b_free = replace(b_end, use_point="free")
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=b_end),
        version(3, "UPDATE", before=b_end, after=b_free),
        decisions=(keep,),
    )

    frozen, rejected = result.state.participations
    assert frozen.teacher_id == "A"
    assert frozen.participation_role == ParticipationRole.COMPLETION
    assert rejected.teacher_id == "B"
    assert rejected.participation_role == ParticipationRole.REJECTED_CORRECTION
    assert result.state.completion_teacher_id == "A"
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_KEEP
    )
    assert result.state.conflict_resolved_against_revision == 2
    assert result.applied_decision_ids == (keep.decision_id,)


def test_t57b_new_teacher_after_keep_reopens_same_conflict() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    c_end = complete_end_snapshot("C")
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=b_end),
        version(3, "UPDATE", before=b_end, after=c_end),
        decisions=(keep,),
    )

    assert [
        row.participation_role for row in result.state.participations
    ] == [
        ParticipationRole.COMPLETION,
        ParticipationRole.REJECTED_CORRECTION,
        ParticipationRole.PENDING_CORRECTION,
    ]
    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING


def test_t57c_update_completion_snapshot_keeps_teacher_and_initial_evidence() -> None:
    initial = complete_end_snapshot("A")
    corrected = complete_end_snapshot(
        "A",
        end_time="2026-08-22T10:30:00+08:00",
    )
    decision = correction_decision(
        CompletionDecisionType.UPDATE_COMPLETION_SNAPSHOT,
        revision=2,
        completion_snapshot=corrected,
    )

    result = reduce(
        version(1, "INSERT", after=initial),
        version(2, "UPDATE", before=initial, after=corrected),
        decisions=(decision,),
    )

    assert result.state.initial_completion_snapshot == initial
    assert result.state.completion_snapshot == corrected
    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_source_revision == 2
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_UPDATE
    )


def test_t57d_transfer_completion_changes_role_and_owner_once() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    transfer = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=2,
        target_seq=2,
        completion_snapshot=b_end,
    )

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=b_end),
        decisions=(transfer,),
    )

    old, target = result.state.participations
    assert old.participation_role == ParticipationRole.SUPERSEDED_COMPLETION
    assert target.participation_role == ParticipationRole.COMPLETION
    assert result.state.initial_completion_snapshot == a_end
    assert result.state.completion_participation_seq == 2
    assert result.state.completion_teacher_id == "B"
    assert result.state.completion_snapshot == b_end
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_TRANSFER
    )


def test_t57e_void_completion_clears_current_owner_but_keeps_history() -> None:
    a_end = complete_end_snapshot("A")
    void = correction_decision(
        CompletionDecisionType.VOID_COMPLETION,
        revision=2,
    )

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "DELETE", before=a_end),
        decisions=(void,),
    )

    historical = result.state.participations[0]
    assert historical.participation_role == ParticipationRole.VOIDED_COMPLETION
    assert historical.source_deleted is True
    assert result.state.initial_completion_snapshot == a_end
    assert result.state.completion_participation_seq is None
    assert result.state.completion_teacher_id is None
    assert result.state.completion_snapshot is None
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_VOID
    )


def test_t57f_keep_then_new_difference_can_transfer_current_rejected_row() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    b_changed = complete_end_snapshot(
        "B",
        end_time="2026-08-22T10:30:00+08:00",
    )
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )
    transfer = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=3,
        target_seq=2,
        completion_snapshot=b_changed,
    )

    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=b_end),
        version(3, "UPDATE", before=b_end, after=b_changed),
        decisions=(keep, transfer),
    )

    old, target = result.state.participations
    assert old.participation_role == ParticipationRole.SUPERSEDED_COMPLETION
    assert target.participation_role == ParticipationRole.COMPLETION
    assert target.is_current is True
    assert result.state.completion_participation_seq == 2
    assert result.state.completion_teacher_id == "B"
    assert result.state.completion_snapshot == b_changed
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_TRANSFER
    )


def test_t75_void_then_new_end_requires_and_allows_transfer() -> None:
    a_end = complete_end_snapshot("A")
    a_on = replace(a_end, status="on")
    void = correction_decision(
        CompletionDecisionType.VOID_COMPLETION,
        revision=2,
    )
    transfer = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=3,
        target_seq=1,
        completion_snapshot=a_end,
    )

    without_transfer = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=a_on),
        version(3, "UPDATE", before=a_on, after=a_end),
        decisions=(void,),
    )
    assert without_transfer.state.completion_participation_seq is None
    assert (
        without_transfer.state.completion_conflict_status
        == CompletionConflictStatus.PENDING
    )
    assert (
        without_transfer.state.participations[0].participation_role
        == ParticipationRole.VOIDED_COMPLETION
    )

    restored = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=a_on),
        version(3, "UPDATE", before=a_on, after=a_end),
        decisions=(void, transfer),
    )
    assert restored.state.completion_participation_seq == 1
    assert restored.state.completion_teacher_id == "A"
    assert (
        restored.state.participations[0].participation_role
        == ParticipationRole.COMPLETION
    )
    assert (
        restored.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_TRANSFER
    )


def test_transfer_cannot_select_a_historical_rejected_row() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    c_end = complete_end_snapshot("C")
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )
    transfer_old_b = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=3,
        target_seq=2,
        completion_snapshot=b_end,
    )

    with pytest.raises(
        CorrectionDecisionHistoryError,
        match="^TRANSFER target or completion snapshot is invalid$",
    ):
        reduce(
            version(1, "INSERT", after=a_end),
            version(2, "UPDATE", before=a_end, after=b_end),
            version(3, "UPDATE", before=b_end, after=c_end),
            decisions=(keep, transfer_old_b),
        )


def test_new_difference_can_transfer_back_to_current_superseded_row() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    a_end = complete_end_snapshot("A")
    a_changed = replace(
        a_end,
        end_time="2026-08-22T10:15:00+08:00",
    )
    a_changed_again = replace(
        a_end,
        end_time="2026-08-22T10:30:00+08:00",
    )
    b_completion = replace(a_changed, teacher_id="B")
    transfer_to_history = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=4,
        target_seq=2,
        completion_snapshot=b_completion,
    )
    transfer_back_to_current = correction_decision(
        CompletionDecisionType.TRANSFER_COMPLETION,
        revision=5,
        target_seq=3,
        completion_snapshot=a_changed_again,
    )

    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_on),
        version(3, "UPDATE", before=b_on, after=a_end),
        version(4, "UPDATE", before=a_end, after=a_changed),
        version(
            5,
            "UPDATE",
            before=a_changed,
            after=a_changed_again,
        ),
        decisions=(transfer_to_history, transfer_back_to_current),
    )

    assert result.state.current_participation_seq == 3
    assert result.state.completion_participation_seq == 3
    assert result.state.completion_teacher_id == "A"
    assert result.state.participations[1].participation_role == (
        ParticipationRole.SUPERSEDED_COMPLETION
    )
    assert result.state.participations[2].participation_role == (
        ParticipationRole.COMPLETION
    )
    assert (
        result.state.completion_conflict_status
        == CompletionConflictStatus.RESOLVED_TRANSFER
    )


def test_unknown_completion_decision_type_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="^unsupported completion correction decision type$",
    ):
        CompletionCorrectionDecision(
            decision_id="bad-decision",
            decision_type="BOGUS",  # type: ignore[arg-type]
            expected_source_revision=1,
        )


def test_end_without_teacher_requires_transfer_or_void_not_keep() -> None:
    empty_end = complete_end_snapshot(None)
    b_end = complete_end_snapshot("B")
    keep = correction_decision(
        CompletionDecisionType.KEEP_FROZEN_COMPLETION,
        revision=2,
    )

    with pytest.raises(
        CorrectionDecisionHistoryError,
        match="^KEEP requires an existing frozen completion$",
    ):
        reduce(
            version(1, "INSERT", after=empty_end),
            version(2, "UPDATE", before=empty_end, after=b_end),
            decisions=(keep,),
        )


def test_t57a_pre_end_delete_and_restore_never_revives_old_participation() -> None:
    a_on = snapshot("A")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "DELETE", before=a_on),
        version(3, "INSERT", after=a_on),
    )

    old, restored = result.state.participations
    assert old.participation_seq == 1
    assert old.participation_status == "on"
    assert old.participation_status != "t_absent"
    assert old.is_current is False
    assert old.source_deleted is True
    assert restored.participation_seq == 2
    assert restored.teacher_id == "A"
    assert restored.is_current is True
    assert restored.source_deleted is False
    assert result.state.current_participation_seq == 2


def test_t70c_first_update_builds_before_then_after_participations() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    result = reduce(
        version(1, "UPDATE", before=a_on, after=b_on, offset=7)
    )

    first, second = result.state.participations
    assert first.teacher_id == "A"
    assert first.assignment_source_row_revision == 1
    assert first.assignment_source_partition_epoch_id == "epoch-1"
    assert first.assignment_event_topic == "dom_appoint"
    assert first.assignment_event_partition == 0
    assert first.assignment_event_offset == 7
    assert first.assignment_event_phase == AssignmentEventPhase.BEFORE
    assert first.assigned_at_evidence_status == EvidenceStatus.SOURCE_MISSING
    assert first.assigned_at is None
    assert first.ended_at is not None
    assert first.participation_status == "t_absent"
    assert second.teacher_id == "B"
    assert second.assignment_source_row_revision == 1
    assert second.assignment_source_partition_epoch_id == "epoch-1"
    assert second.assignment_event_topic == "dom_appoint"
    assert second.assignment_event_partition == 0
    assert second.assignment_event_offset == 7
    assert second.assignment_event_phase == AssignmentEventPhase.AFTER
    assert second.assigned_at is not None
    assert result.versions[0].created_participation_seqs == (1, 2)


@pytest.mark.parametrize(
    ("scope_complete", "expected"),
    [
        (False, ReductionDisposition.WAITING_DEPENDENCY),
        (True, ReductionDisposition.SOURCE_CONFLICT),
    ],
)
def test_t70c_incomplete_first_update_waits_then_conflicts_after_scope_complete(
    scope_complete: bool,
    expected: ReductionDisposition,
) -> None:
    result = reduce(
        version(1, "UPDATE", before=None, after=snapshot("B")),
        scope_complete=scope_complete,
    )

    assert result.state.participations == ()
    assert result.state.last_applied_source_revision is None
    assert result.state.blocked_source_revision == 1
    assert result.versions[0].disposition == expected
    assert result.versions[0].emit_outbox is False


def test_first_no_teacher_noop_update_still_emits_course_change() -> None:
    no_teacher = snapshot(None)

    result = reduce(
        version(1, "UPDATE", before=no_teacher, after=no_teacher)
    )

    assert result.state.current_snapshot == no_teacher
    assert result.state.participations == ()
    assert result.versions[0].disposition == ReductionDisposition.APPLIED
    assert result.versions[0].emit_outbox is True


def test_t70d_adjacent_duplicate_transition_is_semantic_replay() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_on),
        version(3, "UPDATE", before=a_on, after=b_on),
    )

    assert [row.teacher_id for row in result.state.participations] == ["A", "B"]
    replay = result.versions[-1]
    assert replay.disposition == ReductionDisposition.SEMANTIC_REPLAY
    assert replay.created_participation_seqs == ()
    assert replay.emit_outbox is False
    assert result.state.last_applied_source_revision == 3


def test_t70d_mismatched_before_and_different_after_is_source_conflict() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    c_on = snapshot("C")
    d_on = snapshot("D")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_on),
        version(3, "UPDATE", before=c_on, after=d_on),
    )

    assert [row.teacher_id for row in result.state.participations] == ["A", "B"]
    assert result.state.current_teacher_id == "B"
    assert result.state.last_applied_source_revision == 2
    assert result.state.blocked_source_revision == 3
    assert result.state.evidence_status == EvidenceStatus.SOURCE_CONFLICT
    assert result.versions[-1].disposition == ReductionDisposition.SOURCE_CONFLICT
    assert result.versions[-1].emit_outbox is False


def test_t70d_a_to_b_to_a_to_b_creates_a_new_b_participation() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    result = reduce(
        version(1, "INSERT", after=a_on),
        version(2, "UPDATE", before=a_on, after=b_on),
        version(3, "UPDATE", before=b_on, after=a_on),
        version(4, "UPDATE", before=a_on, after=b_on),
    )

    assert [row.teacher_id for row in result.state.participations] == [
        "A",
        "B",
        "A",
        "B",
    ]
    assert result.state.current_participation_seq == 4
    assert result.versions[-1].disposition == ReductionDisposition.APPLIED


@pytest.mark.parametrize(
    ("status", "use_point"),
    [
        ("on", "buy"),
        ("cancel", "free"),
        ("custom", None),
        (None, "free"),
    ],
)
def test_reducer_does_not_filter_appoint_status_or_use_point(
    status: str | None,
    use_point: str | None,
) -> None:
    source = snapshot("A", status, use_point=use_point)
    result = reduce(version(1, "INSERT", after=source))

    assert result.state.current_snapshot == source
    assert result.state.participations[0].participation_status == status


def test_post_end_use_point_only_update_does_not_open_correction() -> None:
    before = complete_end_snapshot("A")
    after = replace(before, use_point="free")

    result = reduce(
        version(1, "INSERT", after=before),
        version(2, "UPDATE", before=before, after=after),
    )

    assert result.state.current_snapshot == after
    assert result.state.completion_snapshot == before
    assert result.state.completion_conflict_status == CompletionConflictStatus.NONE
    assert result.state.correction_case_key is None


def test_post_end_return_to_frozen_fields_does_not_bypass_pending_decision() -> None:
    a_end = complete_end_snapshot("A")
    b_end = complete_end_snapshot("B")
    result = reduce(
        version(1, "INSERT", after=a_end),
        version(2, "UPDATE", before=a_end, after=b_end),
        version(3, "UPDATE", before=b_end, after=a_end),
    )

    assert result.state.current_teacher_id == "A"
    assert result.state.completion_teacher_id == "A"
    assert result.state.completion_participation_seq == 1
    assert result.state.completion_conflict_status == CompletionConflictStatus.PENDING
    assert [
        row.participation_role for row in result.state.participations
    ] == [
        ParticipationRole.COMPLETION,
        ParticipationRole.PENDING_CORRECTION,
        ParticipationRole.PENDING_CORRECTION,
    ]


def test_duplicate_source_row_revision_is_rejected() -> None:
    with pytest.raises(DuplicateSourceRowRevisionError):
        reduce(
            version(1, "INSERT", after=snapshot("A")),
            version(1, "INSERT", after=snapshot("B")),
        )


@pytest.mark.parametrize("revisions", [(2,), (1, 3)])
def test_non_contiguous_source_row_revision_is_rejected(
    revisions: tuple[int, ...],
) -> None:
    rows = tuple(
        version(revision, "INSERT", after=snapshot("A"))
        for revision in revisions
    )

    with pytest.raises(NonContiguousSourceRowRevisionError):
        reduce(*rows)


def test_snapshot_diff_assignment_time_is_not_fabricated() -> None:
    result = reduce(
        version(
            1,
            "INSERT",
            after=snapshot("A"),
            phase=AssignmentEventPhase.SNAPSHOT_DIFF,
            source_timestamp=None,
        )
    )

    participation = result.state.participations[0]
    assert participation.assignment_event_phase == AssignmentEventPhase.SNAPSHOT_DIFF
    assert participation.assigned_at is None
    assert (
        participation.assigned_at_evidence_status
        == EvidenceStatus.SOURCE_MISSING
    )


def test_snapshot_diff_rejects_a_fabricated_source_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="^SNAPSHOT_DIFF must not fabricate a source timestamp$",
    ):
        version(
            1,
            "INSERT",
            after=snapshot("A"),
            phase=AssignmentEventPhase.SNAPSHOT_DIFF,
        )


def test_source_version_rejects_reducer_derived_or_unknown_phase() -> None:
    with pytest.raises(
        ValueError,
        match="^BEFORE is derived by the reducer and is not a source phase$",
    ):
        version(
            1,
            "UPDATE",
            before=snapshot("A"),
            after=snapshot("B"),
            phase=AssignmentEventPhase.BEFORE,
        )

    with pytest.raises(
        ValueError,
        match="^unsupported appoint assignment phase$",
    ):
        AppointSourceVersion(
            source_row_revision=1,
            source_ref=SourceEventReference(
                source_partition_epoch_id="epoch-1",
                topic="dom_appoint",
                partition=0,
                offset=1,
            ),
            operation="INSERT",
            before=None,
            after=snapshot("A"),
            assignment_phase="BOGUS",  # type: ignore[arg-type]
        )


def test_snapshot_diff_teacher_change_keeps_assignment_interval_unknown() -> None:
    a_on = snapshot("A")
    b_on = snapshot("B")
    result = reduce(
        version(
            1,
            "INSERT",
            after=a_on,
            phase=AssignmentEventPhase.SNAPSHOT_DIFF,
            source_timestamp=None,
        ),
        version(
            2,
            "UPDATE",
            before=a_on,
            after=b_on,
            phase=AssignmentEventPhase.SNAPSHOT_DIFF,
            source_timestamp=None,
        ),
    )

    old, current = result.state.participations
    assert old.ended_at is None
    assert current.assigned_at is None
    assert current.assignment_event_phase == AssignmentEventPhase.SNAPSHOT_DIFF
