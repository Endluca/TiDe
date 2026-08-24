"""Pure reducer for the DTS appoint teacher-participation state machine.

The reducer deliberately has no database, runtime-setting, or legacy-wide-table
dependency.  It consumes complete appoint row versions and rebuilds the course
and participation state in ``source_row_revision`` order.  Persistence and
Outbox publication belong to the future domain projector.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Iterable


class ParticipationRole(str, Enum):
    NORMAL = "NORMAL"
    COMPLETION = "COMPLETION"
    PENDING_CORRECTION = "PENDING_CORRECTION"
    REJECTED_CORRECTION = "REJECTED_CORRECTION"
    SUPERSEDED_COMPLETION = "SUPERSEDED_COMPLETION"
    VOIDED_COMPLETION = "VOIDED_COMPLETION"


class AssignmentEventPhase(str, Enum):
    SNAPSHOT_DIFF = "SNAPSHOT_DIFF"
    BEFORE = "BEFORE"
    AFTER = "AFTER"


class EvidenceStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


class CompletionConflictStatus(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    RESOLVED_KEEP = "RESOLVED_KEEP"
    RESOLVED_UPDATE = "RESOLVED_UPDATE"
    RESOLVED_TRANSFER = "RESOLVED_TRANSFER"
    RESOLVED_VOID = "RESOLVED_VOID"


class CompletionDecisionType(str, Enum):
    KEEP_FROZEN_COMPLETION = "KEEP_FROZEN_COMPLETION"
    UPDATE_COMPLETION_SNAPSHOT = "UPDATE_COMPLETION_SNAPSHOT"
    TRANSFER_COMPLETION = "TRANSFER_COMPLETION"
    VOID_COMPLETION = "VOID_COMPLETION"


class ReductionDisposition(str, Enum):
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    SEMANTIC_REPLAY = "SEMANTIC_REPLAY"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


@dataclass(frozen=True)
class SourceEventReference:
    """Stable DTS event identity copied into every created participation."""

    source_partition_epoch_id: str
    topic: str
    partition: int
    offset: int
    source_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source_partition_epoch_id:
            raise ValueError("source_partition_epoch_id must not be empty")
        if not self.topic:
            raise ValueError("topic must not be empty")
        if self.partition < 0:
            raise ValueError("partition must be >= 0")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")
        if (
            self.source_timestamp is not None
            and self.source_timestamp.utcoffset() is None
        ):
            raise ValueError("source_timestamp must be timezone-aware")


@dataclass(frozen=True)
class AppointSnapshot:
    """Canonical, fully merged appoint fields used by the state machine.

    ``teacher_id=None`` means that a complete source image explicitly contains
    a NULL teacher.  A missing/incomplete UPDATE before image is represented by
    ``AppointSourceVersion.before=None`` instead, so the reducer never guesses
    whether an old teacher existed. ``teacher_id_type`` preserves the source
    union family; ``None`` is reserved for NULL teachers and pure reducer tests
    that have not crossed the v2 source adapter.
    """

    teacher_id: str | None
    status: str | None
    teacher_id_type: str | None = None
    use_point: str | None = None
    end_time: str | None = None
    student_token: str | None = None
    lesson_local_date: str | None = None
    lesson_local_time: str | None = None
    is_peak: bool | None = None

    def __post_init__(self) -> None:
        if self.teacher_id is None:
            if self.teacher_id_type is not None:
                raise ValueError("NULL teacher_id must not carry type evidence")
            return
        if self.teacher_id_type not in {None, "NUMERIC", "TEXT"}:
            raise ValueError("teacher_id_type must be NUMERIC, TEXT, or None")


@dataclass(frozen=True)
class AppointSourceVersion:
    """One complete appoint row version after sparse before/after merging."""

    source_row_revision: int
    source_ref: SourceEventReference
    operation: str
    before: AppointSnapshot | None
    after: AppointSnapshot | None
    assignment_phase: AssignmentEventPhase = AssignmentEventPhase.AFTER

    def __post_init__(self) -> None:
        operation = self.operation.upper()
        object.__setattr__(self, "operation", operation)
        if self.source_row_revision < 1:
            raise ValueError("source_row_revision must be >= 1")
        if operation not in {"INSERT", "UPDATE", "DELETE"}:
            raise ValueError(f"unsupported appoint operation: {operation}")
        if not isinstance(self.assignment_phase, AssignmentEventPhase):
            raise ValueError("unsupported appoint assignment phase")
        if self.assignment_phase == AssignmentEventPhase.BEFORE:
            raise ValueError(
                "BEFORE is derived by the reducer and is not a source phase"
            )
        if operation == "INSERT" and self.before is not None:
            raise ValueError("INSERT must not contain a before snapshot")
        if operation in {"INSERT", "UPDATE"} and self.after is None:
            raise ValueError(f"{operation} requires an after snapshot")
        if operation == "DELETE" and self.after is not None:
            raise ValueError("DELETE must not contain an after snapshot")
        if (
            self.assignment_phase == AssignmentEventPhase.SNAPSHOT_DIFF
            and self.source_ref.source_timestamp is not None
        ):
            raise ValueError(
                "SNAPSHOT_DIFF must not fabricate a source timestamp"
            )


@dataclass(frozen=True)
class CourseParticipation:
    participation_seq: int
    teacher_id: str
    teacher_id_type: str | None
    participation_status: str | None
    participation_role: ParticipationRole
    is_current: bool
    source_deleted: bool
    assigned_at: datetime | None
    ended_at: datetime | None
    assignment_source_partition_epoch_id: str
    assignment_event_topic: str
    assignment_event_partition: int
    assignment_event_offset: int
    assignment_source_row_revision: int
    assignment_event_phase: AssignmentEventPhase
    assigned_at_evidence_status: EvidenceStatus


@dataclass(frozen=True)
class CompletionCorrectionDecision:
    """One already-authorized immutable correction decision.

    Concurrency checks, operator identity, request hashes, and score/Case
    writes belong to the database command.  The reducer consumes only a
    decision that was accepted against an exact source revision.
    """

    decision_id: str
    decision_type: CompletionDecisionType
    expected_source_revision: int
    target_participation_seq: int | None = None
    completion_snapshot: AppointSnapshot | None = None

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise ValueError("decision_id must not be empty")
        if self.expected_source_revision < 1:
            raise ValueError("expected_source_revision must be >= 1")
        if not isinstance(self.decision_type, CompletionDecisionType):
            raise ValueError(
                "unsupported completion correction decision type"
            )
        if self.decision_type == CompletionDecisionType.KEEP_FROZEN_COMPLETION:
            valid_shape = (
                self.target_participation_seq is None
                and self.completion_snapshot is None
            )
        elif (
            self.decision_type
            == CompletionDecisionType.UPDATE_COMPLETION_SNAPSHOT
        ):
            valid_shape = (
                self.target_participation_seq is None
                and self.completion_snapshot is not None
            )
        elif self.decision_type == CompletionDecisionType.TRANSFER_COMPLETION:
            valid_shape = (
                self.target_participation_seq is not None
                and self.target_participation_seq >= 1
                and self.completion_snapshot is not None
            )
        elif self.decision_type == CompletionDecisionType.VOID_COMPLETION:
            valid_shape = (
                self.target_participation_seq is None
                and self.completion_snapshot is None
            )
        else:  # pragma: no cover - guarded by the enum check above.
            valid_shape = False
        if not valid_shape:
            raise ValueError("completion correction decision shape is invalid")


@dataclass(frozen=True)
class VersionReduction:
    source_row_revision: int
    disposition: ReductionDisposition
    created_participation_seqs: tuple[int, ...] = ()
    emit_outbox: bool = False
    correction_case_key: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class CourseParticipationState:
    source_region: str
    source_appoint_id: str
    participations: tuple[CourseParticipation, ...]
    current_snapshot: AppointSnapshot | None
    source_is_deleted: bool
    current_participation_seq: int | None
    completion_participation_seq: int | None
    completion_teacher_id: str | None
    completion_teacher_id_type: str | None
    initial_completion_snapshot: AppointSnapshot | None
    initial_completion_source_revision: int | None
    completion_snapshot: AppointSnapshot | None
    completion_source_revision: int | None
    completion_conflict_status: CompletionConflictStatus
    correction_case_key: str | None
    conflict_resolved_against_revision: int | None
    last_correction_decision_id: str | None
    resolved_conflict_signature: tuple[object, ...] | None
    evidence_status: EvidenceStatus
    last_applied_source_revision: int | None
    blocked_source_revision: int | None

    @property
    def current_teacher_id(self) -> str | None:
        participation = self.participation(self.current_participation_seq)
        return participation.teacher_id if participation is not None else None

    @property
    def current_teacher_id_type(self) -> str | None:
        participation = self.participation(self.current_participation_seq)
        return (
            participation.teacher_id_type
            if participation is not None
            else None
        )

    def participation(self, participation_seq: int | None) -> CourseParticipation | None:
        if participation_seq is None:
            return None
        return next(
            (
                row
                for row in self.participations
                if row.participation_seq == participation_seq
            ),
            None,
        )


@dataclass(frozen=True)
class ParticipationReduction:
    state: CourseParticipationState
    versions: tuple[VersionReduction, ...]
    applied_decision_ids: tuple[str, ...] = ()


class DuplicateSourceRowRevisionError(ValueError):
    """Raised when one reduction input contains two versions of one revision."""


class NonContiguousSourceRowRevisionError(ValueError):
    """Raised when an immutable per-row revision history has a gap."""


class CorrectionDecisionHistoryError(ValueError):
    """Raised when accepted correction history cannot replay deterministically."""


class _Reducer:
    def __init__(self, *, source_region: str, source_appoint_id: str) -> None:
        self.source_region = source_region
        self.source_appoint_id = source_appoint_id
        self.participations: list[CourseParticipation] = []
        self.current_snapshot: AppointSnapshot | None = None
        self.source_is_deleted = False
        self.current_participation_seq: int | None = None
        self.completion_participation_seq: int | None = None
        self.completion_teacher_id: str | None = None
        self.completion_teacher_id_type: str | None = None
        self.initial_completion_snapshot: AppointSnapshot | None = None
        self.initial_completion_source_revision: int | None = None
        self.completion_snapshot: AppointSnapshot | None = None
        self.completion_source_revision: int | None = None
        self.completion_conflict_status = CompletionConflictStatus.NONE
        self.correction_case_key: str | None = None
        self.conflict_resolved_against_revision: int | None = None
        self.last_correction_decision_id: str | None = None
        self.resolved_conflict_signature: tuple[object, ...] | None = None
        self.evidence_status = EvidenceStatus.CONFIRMED
        self.last_applied_source_revision: int | None = None
        self.blocked_source_revision: int | None = None
        self.last_transition: tuple[
            AppointSnapshot | None, AppointSnapshot | None
        ] | None = None
        self.version_results: list[VersionReduction] = []
        self.applied_decision_ids: list[str] = []

    def reduce(
        self,
        versions: Iterable[AppointSourceVersion],
        *,
        scope_complete: bool,
        decisions: Iterable[CompletionCorrectionDecision],
    ) -> ParticipationReduction:
        ordered = sorted(versions, key=lambda item: item.source_row_revision)
        revisions = [item.source_row_revision for item in ordered]
        duplicate = next(
            (
                revision
                for index, revision in enumerate(revisions[1:], start=1)
                if revision == revisions[index - 1]
            ),
            None,
        )
        if duplicate is not None:
            raise DuplicateSourceRowRevisionError(
                f"duplicate source_row_revision: {duplicate}"
            )
        expected_revision = 1
        for revision in revisions:
            if revision != expected_revision:
                raise NonContiguousSourceRowRevisionError(
                    "source_row_revision history must start at 1 and be "
                    f"contiguous; expected {expected_revision}, got {revision}"
                )
            expected_revision += 1

        ordered_decisions = sorted(
            decisions,
            key=lambda item: item.expected_source_revision,
        )
        decision_ids: set[str] = set()
        decision_by_revision: dict[int, CompletionCorrectionDecision] = {}
        for decision in ordered_decisions:
            if decision.decision_id in decision_ids:
                raise CorrectionDecisionHistoryError(
                    f"duplicate correction decision id: {decision.decision_id}"
                )
            if decision.expected_source_revision in decision_by_revision:
                raise CorrectionDecisionHistoryError(
                    "multiple correction decisions target source revision "
                    f"{decision.expected_source_revision}"
                )
            decision_ids.add(decision.decision_id)
            decision_by_revision[decision.expected_source_revision] = decision

        for version in ordered:
            if not self._apply_version(version, scope_complete=scope_complete):
                break
            decision = decision_by_revision.get(version.source_row_revision)
            if decision is not None:
                self._apply_completion_decision(decision)

        if len(self.applied_decision_ids) != len(ordered_decisions):
            unapplied = [
                decision.decision_id
                for decision in ordered_decisions
                if decision.decision_id not in self.applied_decision_ids
            ]
            raise CorrectionDecisionHistoryError(
                "correction decisions could not be replayed: "
                + ",".join(unapplied)
            )

        state = self._state()
        self._assert_invariants(state)
        return ParticipationReduction(
            state=state,
            versions=tuple(self.version_results),
            applied_decision_ids=tuple(self.applied_decision_ids),
        )

    def _apply_version(
        self,
        version: AppointSourceVersion,
        *,
        scope_complete: bool,
    ) -> bool:
        created: list[int] = []
        seeded_from_before = False

        if self.current_snapshot is None:
            if version.operation == "INSERT":
                self._apply_new_row(version.after, version, created)
                self._record_applied(version, created)
                return True
            if version.before is None:
                self._record_blocked(version, scope_complete=scope_complete)
                return False
            self._seed_from_before(version.before, version, created)
            seeded_from_before = True

        preflight = self._preflight(version)
        if preflight is not None:
            if seeded_from_before and preflight == ReductionDisposition.NOOP:
                self._advance(version)
                self.version_results.append(
                    VersionReduction(
                        source_row_revision=version.source_row_revision,
                        disposition=ReductionDisposition.APPLIED,
                        created_participation_seqs=tuple(created),
                        emit_outbox=True,
                        correction_case_key=self.correction_case_key,
                    )
                )
                return True
            if preflight in {
                ReductionDisposition.NOOP,
                ReductionDisposition.SEMANTIC_REPLAY,
            }:
                self._advance(version)
                self.version_results.append(
                    VersionReduction(
                        source_row_revision=version.source_row_revision,
                        disposition=preflight,
                        emit_outbox=False,
                        correction_case_key=self.correction_case_key,
                    )
                )
                return True
            self.evidence_status = EvidenceStatus.SOURCE_CONFLICT
            self.blocked_source_revision = version.source_row_revision
            self.version_results.append(
                VersionReduction(
                    source_row_revision=version.source_row_revision,
                    disposition=ReductionDisposition.SOURCE_CONFLICT,
                    emit_outbox=False,
                    correction_case_key=self.correction_case_key,
                    detail="before image does not match the rebuilt current state",
                )
            )
            return False

        if version.operation == "INSERT":
            self._apply_new_row(version.after, version, created)
        elif version.operation == "UPDATE":
            if self.source_is_deleted:
                self._apply_new_row(version.after, version, created)
            elif self.initial_completion_snapshot is not None:
                self._apply_post_completion_update(version.after, version, created)
            else:
                self._apply_pre_completion_update(version.after, version, created)
        else:
            self._apply_delete(version)

        self._record_applied(version, created)
        return True

    def _preflight(
        self, version: AppointSourceVersion
    ) -> ReductionDisposition | None:
        before = version.before
        after = version.after

        if version.operation == "INSERT":
            if self.source_is_deleted:
                return None
            if (
                after == self.current_snapshot
                and self.last_transition == (None, after)
            ):
                return ReductionDisposition.SEMANTIC_REPLAY
            return ReductionDisposition.SOURCE_CONFLICT

        if before is None:
            return ReductionDisposition.SOURCE_CONFLICT

        if version.operation == "DELETE":
            if self.source_is_deleted:
                if (
                    before == self.current_snapshot
                    and self.last_transition == (before, None)
                ):
                    return ReductionDisposition.SEMANTIC_REPLAY
                return ReductionDisposition.SOURCE_CONFLICT
            if before != self.current_snapshot:
                return ReductionDisposition.SOURCE_CONFLICT
            return None

        if before != self.current_snapshot:
            if (
                not self.source_is_deleted
                and after == self.current_snapshot
                and self.last_transition == (before, after)
            ):
                return ReductionDisposition.SEMANTIC_REPLAY
            return ReductionDisposition.SOURCE_CONFLICT

        if not self.source_is_deleted and after == self.current_snapshot:
            return ReductionDisposition.NOOP
        return None

    def _seed_from_before(
        self,
        snapshot: AppointSnapshot,
        version: AppointSourceVersion,
        created: list[int],
    ) -> None:
        self.current_snapshot = snapshot
        self.source_is_deleted = False
        if snapshot.teacher_id is not None:
            created.append(
                self._create_participation(
                    snapshot=snapshot,
                    version=version,
                    phase=AssignmentEventPhase.BEFORE,
                    role=ParticipationRole.NORMAL,
                    assigned_evidence=EvidenceStatus.SOURCE_MISSING,
                )
            )
        if snapshot.status == "end":
            self._observe_initial_end(snapshot, version.source_row_revision)

    def _apply_new_row(
        self,
        snapshot: AppointSnapshot | None,
        version: AppointSourceVersion,
        created: list[int],
    ) -> None:
        assert snapshot is not None
        had_completion_evidence = self.initial_completion_snapshot is not None
        self.current_snapshot = snapshot
        self.source_is_deleted = False
        role = (
            ParticipationRole.PENDING_CORRECTION
            if had_completion_evidence
            else ParticipationRole.NORMAL
        )
        if snapshot.teacher_id is not None:
            created.append(
                self._create_participation(
                    snapshot=snapshot,
                    version=version,
                    phase=version.assignment_phase,
                    role=role,
                    assigned_evidence=EvidenceStatus.CONFIRMED,
                )
            )
        if had_completion_evidence:
            self._ensure_completion_correction()
        elif snapshot.status == "end":
            self._observe_initial_end(snapshot, version.source_row_revision)
        else:
            self.evidence_status = EvidenceStatus.CONFIRMED

    def _apply_pre_completion_update(
        self,
        after: AppointSnapshot | None,
        version: AppointSourceVersion,
        created: list[int],
    ) -> None:
        assert after is not None
        assert self.current_snapshot is not None
        old_teacher = self._teacher_identity(self.current_snapshot)
        new_teacher = self._teacher_identity(after)

        if old_teacher == new_teacher:
            if self.current_participation_seq is not None:
                self._replace_participation(
                    self.current_participation_seq,
                    participation_status=after.status,
                    source_deleted=False,
                )
        else:
            if self.current_participation_seq is not None:
                self._replace_participation(
                    self.current_participation_seq,
                    participation_status="t_absent",
                    is_current=False,
                    ended_at=version.source_ref.source_timestamp,
                )
                self.current_participation_seq = None
            if after.teacher_id is not None:
                created.append(
                    self._create_participation(
                        snapshot=after,
                        version=version,
                        phase=version.assignment_phase,
                        role=ParticipationRole.NORMAL,
                        assigned_evidence=EvidenceStatus.CONFIRMED,
                    )
                )

        self.current_snapshot = after
        self.source_is_deleted = False
        if after.status == "end":
            self._observe_initial_end(after, version.source_row_revision)
        else:
            self.evidence_status = EvidenceStatus.CONFIRMED

    def _apply_post_completion_update(
        self,
        after: AppointSnapshot | None,
        version: AppointSourceVersion,
        created: list[int],
    ) -> None:
        assert after is not None
        assert self.current_snapshot is not None
        old_teacher = self._teacher_identity(self.current_snapshot)
        new_teacher = self._teacher_identity(after)

        if old_teacher == new_teacher:
            if self.current_participation_seq is not None:
                current = self.participation(self.current_participation_seq)
                assert current is not None
                changes: dict[str, object] = {"source_deleted": False}
                # Once one participation has been frozen as the completion
                # owner, an ordinary source correction may change appoint's
                # current status but must not rewrite that immutable course
                # fact away from ``end``.  The source snapshot still records
                # the new value and opens the correction Case below.
                if current.participation_role != ParticipationRole.COMPLETION:
                    changes["participation_status"] = after.status
                self._replace_participation(
                    self.current_participation_seq,
                    **changes,
                )
        else:
            if self.current_participation_seq is not None:
                self._replace_participation(
                    self.current_participation_seq,
                    is_current=False,
                    ended_at=version.source_ref.source_timestamp,
                )
                self.current_participation_seq = None
            if after.teacher_id is not None:
                created.append(
                    self._create_participation(
                        snapshot=after,
                        version=version,
                        phase=version.assignment_phase,
                        role=ParticipationRole.PENDING_CORRECTION,
                        assigned_evidence=EvidenceStatus.CONFIRMED,
                    )
                )

        self.current_snapshot = after
        self.source_is_deleted = False
        if old_teacher != new_teacher or self._completion_fields_conflict(after):
            self._ensure_completion_correction()

    def _apply_delete(self, version: AppointSourceVersion) -> None:
        if self.current_participation_seq is not None:
            changes: dict[str, object] = {
                "is_current": False,
                "source_deleted": True,
                "ended_at": version.source_ref.source_timestamp,
            }
            self._replace_participation(self.current_participation_seq, **changes)
        self.current_participation_seq = None
        self.source_is_deleted = True
        if self.initial_completion_snapshot is not None:
            self._ensure_completion_correction()
        else:
            self.evidence_status = EvidenceStatus.CONFIRMED

    def _observe_initial_end(
        self,
        snapshot: AppointSnapshot,
        source_row_revision: int,
    ) -> None:
        if self.initial_completion_snapshot is not None:
            return
        self.initial_completion_snapshot = snapshot
        self.initial_completion_source_revision = source_row_revision
        if snapshot.teacher_id is None or self.current_participation_seq is None:
            self.evidence_status = EvidenceStatus.SOURCE_MISSING
            self._ensure_completion_correction(missing_completion_teacher=True)
            return

        self.completion_participation_seq = self.current_participation_seq
        self.completion_teacher_id = snapshot.teacher_id
        self.completion_teacher_id_type = snapshot.teacher_id_type
        self.completion_snapshot = snapshot
        self.completion_source_revision = source_row_revision
        self._replace_participation(
            self.current_participation_seq,
            participation_status=snapshot.status,
            participation_role=ParticipationRole.COMPLETION,
        )
        self.evidence_status = (
            EvidenceStatus.CONFIRMED
            if self._completion_snapshot_is_complete(snapshot)
            else EvidenceStatus.SOURCE_MISSING
        )

    def _ensure_completion_correction(
        self, *, missing_completion_teacher: bool = False
    ) -> None:
        current_signature = self._current_conflict_signature()
        if (
            self.completion_conflict_status != CompletionConflictStatus.PENDING
            and self.resolved_conflict_signature == current_signature
        ):
            return
        self.completion_conflict_status = CompletionConflictStatus.PENDING
        if self.correction_case_key is None:
            self.correction_case_key = (
                "course-completion-correction:"
                f"{self.source_region}:{self.source_appoint_id}"
            )
        if (
            missing_completion_teacher
            or self.completion_snapshot is None
            or not self._completion_snapshot_is_complete(self.completion_snapshot)
        ):
            self.evidence_status = EvidenceStatus.SOURCE_MISSING
        else:
            self.evidence_status = EvidenceStatus.SOURCE_CONFLICT

    def _apply_completion_decision(
        self,
        decision: CompletionCorrectionDecision,
    ) -> None:
        if self.last_applied_source_revision != decision.expected_source_revision:
            raise CorrectionDecisionHistoryError(
                "correction decision source revision is not current"
            )
        if self.completion_conflict_status != CompletionConflictStatus.PENDING:
            raise CorrectionDecisionHistoryError(
                "correction decision requires a pending completion conflict"
            )

        decision_type = decision.decision_type
        if decision_type == CompletionDecisionType.KEEP_FROZEN_COMPLETION:
            if self.completion_participation_seq is None:
                raise CorrectionDecisionHistoryError(
                    "KEEP requires an existing frozen completion"
                )
            self._reject_pending_corrections(
                through_revision=decision.expected_source_revision
            )
            resolved_status = CompletionConflictStatus.RESOLVED_KEEP
        elif (
            decision_type
            == CompletionDecisionType.UPDATE_COMPLETION_SNAPSHOT
        ):
            snapshot = decision.completion_snapshot
            if (
                snapshot is None
                or self.completion_participation_seq is None
                or self._teacher_identity(snapshot)
                != self._completion_teacher_identity()
                or snapshot.status != "end"
            ):
                raise CorrectionDecisionHistoryError(
                    "UPDATE must keep the frozen teacher and end status"
                )
            self.completion_snapshot = snapshot
            self.completion_source_revision = decision.expected_source_revision
            self._replace_participation(
                self.completion_participation_seq,
                participation_status="end",
            )
            self._reject_pending_corrections(
                through_revision=decision.expected_source_revision
            )
            resolved_status = CompletionConflictStatus.RESOLVED_UPDATE
        elif decision_type == CompletionDecisionType.TRANSFER_COMPLETION:
            target_seq = decision.target_participation_seq
            snapshot = decision.completion_snapshot
            target = self.participation(target_seq)
            if (
                target_seq is None
                or target is None
                or snapshot is None
                or self._teacher_identity(snapshot)
                != self._participation_teacher_identity(target)
                or snapshot.status != "end"
                or target.assignment_source_row_revision
                > decision.expected_source_revision
                or not self._is_valid_transfer_target(target)
            ):
                raise CorrectionDecisionHistoryError(
                    "TRANSFER target or completion snapshot is invalid"
                )
            old_completion_seq = self.completion_participation_seq
            if old_completion_seq is not None:
                self._replace_participation(
                    old_completion_seq,
                    participation_role=(
                        ParticipationRole.SUPERSEDED_COMPLETION
                    ),
                )
            self._reject_pending_corrections(
                through_revision=decision.expected_source_revision,
                except_participation_seq=target_seq,
            )
            self._replace_participation(
                target_seq,
                participation_status="end",
                participation_role=ParticipationRole.COMPLETION,
            )
            self.completion_participation_seq = target_seq
            self.completion_teacher_id = target.teacher_id
            self.completion_teacher_id_type = target.teacher_id_type
            self.completion_snapshot = snapshot
            self.completion_source_revision = decision.expected_source_revision
            resolved_status = CompletionConflictStatus.RESOLVED_TRANSFER
        elif decision_type == CompletionDecisionType.VOID_COMPLETION:
            old_completion_seq = self.completion_participation_seq
            if old_completion_seq is not None:
                self._replace_participation(
                    old_completion_seq,
                    participation_role=ParticipationRole.VOIDED_COMPLETION,
                )
            self._reject_pending_corrections(
                through_revision=decision.expected_source_revision
            )
            self.completion_participation_seq = None
            self.completion_teacher_id = None
            self.completion_teacher_id_type = None
            self.completion_snapshot = None
            self.completion_source_revision = None
            resolved_status = CompletionConflictStatus.RESOLVED_VOID
        else:  # pragma: no cover - construction rejects unknown decisions.
            raise CorrectionDecisionHistoryError(
                "unsupported completion correction decision type"
            )

        self.completion_conflict_status = resolved_status
        self.conflict_resolved_against_revision = (
            decision.expected_source_revision
        )
        self.last_correction_decision_id = decision.decision_id
        self.resolved_conflict_signature = self._current_conflict_signature()
        self.evidence_status = (
            EvidenceStatus.CONFIRMED
            if resolved_status == CompletionConflictStatus.RESOLVED_VOID
            or (
                self.completion_snapshot is not None
                and self._completion_snapshot_is_complete(
                    self.completion_snapshot
                )
            )
            else EvidenceStatus.SOURCE_MISSING
        )
        self.applied_decision_ids.append(decision.decision_id)

    def _is_valid_transfer_target(
        self,
        target: CourseParticipation,
    ) -> bool:
        if target.participation_role in {
            ParticipationRole.NORMAL,
            ParticipationRole.PENDING_CORRECTION,
        }:
            return True

        # KEEP/VOID can close a Case while the same source assignment remains
        # current.  A later, genuinely newer source difference reopens the
        # Case without creating another participation row.  That exact current
        # row must be selectable again, while arbitrary historical terminal
        # rows remain ineligible.
        return (
            target.participation_seq == self.current_participation_seq
            and target.is_current
            and target.participation_role
            in {
                ParticipationRole.REJECTED_CORRECTION,
                ParticipationRole.SUPERSEDED_COMPLETION,
                ParticipationRole.VOIDED_COMPLETION,
            }
        )

    def _reject_pending_corrections(
        self,
        *,
        through_revision: int,
        except_participation_seq: int | None = None,
    ) -> None:
        for participation in tuple(self.participations):
            if (
                participation.participation_seq != except_participation_seq
                and participation.participation_role
                == ParticipationRole.PENDING_CORRECTION
                and participation.assignment_source_row_revision
                <= through_revision
            ):
                self._replace_participation(
                    participation.participation_seq,
                    participation_role=ParticipationRole.REJECTED_CORRECTION,
                )

    def participation(
        self,
        participation_seq: int | None,
    ) -> CourseParticipation | None:
        if participation_seq is None:
            return None
        return next(
            (
                row
                for row in self.participations
                if row.participation_seq == participation_seq
            ),
            None,
        )

    def _current_conflict_signature(self) -> tuple[object, ...]:
        return (
            self.source_is_deleted,
            None
            if self.current_snapshot is None
            else self._completion_conflict_fields(self.current_snapshot),
            None
            if self.completion_snapshot is None
            else self._completion_conflict_fields(self.completion_snapshot),
        )

    @staticmethod
    def _teacher_identity(
        snapshot: AppointSnapshot,
    ) -> tuple[str | None, str] | None:
        if snapshot.teacher_id is None:
            return None
        return snapshot.teacher_id_type, snapshot.teacher_id

    @staticmethod
    def _participation_teacher_identity(
        participation: CourseParticipation,
    ) -> tuple[str | None, str]:
        return participation.teacher_id_type, participation.teacher_id

    def _completion_teacher_identity(
        self,
    ) -> tuple[str | None, str] | None:
        if self.completion_teacher_id is None:
            return None
        return self.completion_teacher_id_type, self.completion_teacher_id

    def _completion_fields_conflict(self, source: AppointSnapshot) -> bool:
        frozen = self.completion_snapshot
        if frozen is None:
            return True
        return self._completion_conflict_fields(source) != (
            self._completion_conflict_fields(frozen)
        )

    @staticmethod
    def _completion_conflict_fields(
        snapshot: AppointSnapshot,
    ) -> tuple[object, ...]:
        # appoint.use_point is deliberately absent: it never changes a frozen
        # completion owner or completion snapshot.
        return (
            _Reducer._teacher_identity(snapshot),
            snapshot.status,
            snapshot.end_time,
            snapshot.student_token,
            snapshot.lesson_local_date,
            snapshot.lesson_local_time,
            snapshot.is_peak,
        )

    @staticmethod
    def _completion_snapshot_is_complete(snapshot: AppointSnapshot) -> bool:
        return all(
            value is not None
            for value in (
                snapshot.teacher_id,
                snapshot.end_time,
                snapshot.student_token,
                snapshot.is_peak,
                snapshot.lesson_local_date,
                snapshot.lesson_local_time,
            )
        )

    def _create_participation(
        self,
        *,
        snapshot: AppointSnapshot,
        version: AppointSourceVersion,
        phase: AssignmentEventPhase,
        role: ParticipationRole,
        assigned_evidence: EvidenceStatus,
    ) -> int:
        assert snapshot.teacher_id is not None
        if self.current_participation_seq is not None:
            raise AssertionError("cannot create a second current participation")
        participation_seq = len(self.participations) + 1
        assigned_at = (
            version.source_ref.source_timestamp
            if phase == AssignmentEventPhase.AFTER
            else None
        )
        effective_assigned_evidence = (
            assigned_evidence
            if assigned_at is not None
            else EvidenceStatus.SOURCE_MISSING
        )
        self.participations.append(
            CourseParticipation(
                participation_seq=participation_seq,
                teacher_id=snapshot.teacher_id,
                teacher_id_type=snapshot.teacher_id_type,
                participation_status=snapshot.status,
                participation_role=role,
                is_current=True,
                source_deleted=False,
                assigned_at=assigned_at,
                ended_at=None,
                assignment_source_partition_epoch_id=(
                    version.source_ref.source_partition_epoch_id
                ),
                assignment_event_topic=version.source_ref.topic,
                assignment_event_partition=version.source_ref.partition,
                assignment_event_offset=version.source_ref.offset,
                assignment_source_row_revision=version.source_row_revision,
                assignment_event_phase=phase,
                assigned_at_evidence_status=effective_assigned_evidence,
            )
        )
        self.current_participation_seq = participation_seq
        return participation_seq

    def _replace_participation(self, participation_seq: int, **changes: object) -> None:
        index = next(
            index
            for index, row in enumerate(self.participations)
            if row.participation_seq == participation_seq
        )
        self.participations[index] = replace(self.participations[index], **changes)

    def _record_applied(
        self, version: AppointSourceVersion, created: list[int]
    ) -> None:
        self._advance(version)
        self.version_results.append(
            VersionReduction(
                source_row_revision=version.source_row_revision,
                disposition=ReductionDisposition.APPLIED,
                created_participation_seqs=tuple(created),
                emit_outbox=True,
                correction_case_key=self.correction_case_key,
            )
        )

    def _advance(self, version: AppointSourceVersion) -> None:
        self.last_applied_source_revision = version.source_row_revision
        self.last_transition = (version.before, version.after)

    def _record_blocked(
        self,
        version: AppointSourceVersion,
        *,
        scope_complete: bool,
    ) -> None:
        disposition = (
            ReductionDisposition.SOURCE_CONFLICT
            if scope_complete
            else ReductionDisposition.WAITING_DEPENDENCY
        )
        self.evidence_status = (
            EvidenceStatus.SOURCE_CONFLICT
            if scope_complete
            else EvidenceStatus.SOURCE_MISSING
        )
        self.blocked_source_revision = version.source_row_revision
        self.version_results.append(
            VersionReduction(
                source_row_revision=version.source_row_revision,
                disposition=disposition,
                emit_outbox=False,
                detail="first UPDATE/DELETE has no complete before image",
            )
        )

    def _state(self) -> CourseParticipationState:
        return CourseParticipationState(
            source_region=self.source_region,
            source_appoint_id=self.source_appoint_id,
            participations=tuple(self.participations),
            current_snapshot=self.current_snapshot,
            source_is_deleted=self.source_is_deleted,
            current_participation_seq=self.current_participation_seq,
            completion_participation_seq=self.completion_participation_seq,
            completion_teacher_id=self.completion_teacher_id,
            completion_teacher_id_type=self.completion_teacher_id_type,
            initial_completion_snapshot=self.initial_completion_snapshot,
            initial_completion_source_revision=self.initial_completion_source_revision,
            completion_snapshot=self.completion_snapshot,
            completion_source_revision=self.completion_source_revision,
            completion_conflict_status=self.completion_conflict_status,
            correction_case_key=self.correction_case_key,
            conflict_resolved_against_revision=(
                self.conflict_resolved_against_revision
            ),
            last_correction_decision_id=self.last_correction_decision_id,
            resolved_conflict_signature=self.resolved_conflict_signature,
            evidence_status=self.evidence_status,
            last_applied_source_revision=self.last_applied_source_revision,
            blocked_source_revision=self.blocked_source_revision,
        )

    @staticmethod
    def _assert_invariants(state: CourseParticipationState) -> None:
        seqs = [row.participation_seq for row in state.participations]
        if seqs != list(range(1, len(seqs) + 1)):
            raise AssertionError("participation_seq must be contiguous and increasing")

        current_rows = [row for row in state.participations if row.is_current]
        if state.current_participation_seq is None:
            if current_rows:
                raise AssertionError("current pointer is empty but a current row exists")
        elif (
            len(current_rows) != 1
            or current_rows[0].participation_seq
            != state.current_participation_seq
            or state.current_snapshot is None
            or _Reducer._teacher_identity(state.current_snapshot)
            != _Reducer._participation_teacher_identity(current_rows[0])
        ):
            raise AssertionError(
                "current pointer, snapshot, and participation disagree"
            )

        completion_rows = [
            row
            for row in state.participations
            if row.participation_role == ParticipationRole.COMPLETION
        ]
        if state.completion_participation_seq is None:
            if completion_rows:
                raise AssertionError(
                    "completion pointer is empty but a completion row exists"
                )
            if state.completion_teacher_id is not None:
                raise AssertionError("completion teacher requires a completion pointer")
            if state.completion_teacher_id_type is not None:
                raise AssertionError(
                    "completion teacher type requires a completion pointer"
                )
        elif (
            len(completion_rows) != 1
            or completion_rows[0].participation_seq
            != state.completion_participation_seq
            or completion_rows[0].teacher_id != state.completion_teacher_id
            or completion_rows[0].teacher_id_type
            != state.completion_teacher_id_type
            or state.completion_snapshot is None
            or _Reducer._teacher_identity(state.completion_snapshot)
            != _Reducer._participation_teacher_identity(completion_rows[0])
            or completion_rows[0].participation_status != "end"
        ):
            raise AssertionError(
                "completion pointer, role, teacher, and end status must "
                "identify one row"
            )


def reduce_course_participations(
    *,
    source_region: str,
    source_appoint_id: str,
    versions: Iterable[AppointSourceVersion],
    scope_complete: bool = False,
    decisions: Iterable[CompletionCorrectionDecision] = (),
) -> ParticipationReduction:
    """Rebuild one course's participation state from immutable row versions.

    Input order is intentionally ignored.  The only business application order
    is ``source_row_revision``.  Processing stops at the first unresolved or
    conflicting version so later versions cannot be applied on a guessed base.
    """

    if source_region not in {"dom", "ovs"}:
        raise ValueError("source_region must be 'dom' or 'ovs'")
    if not source_appoint_id:
        raise ValueError("source_appoint_id must not be empty")
    return _Reducer(
        source_region=source_region,
        source_appoint_id=source_appoint_id,
    ).reduce(
        versions,
        scope_complete=scope_complete,
        decisions=decisions,
    )
