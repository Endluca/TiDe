from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.dts_favorite_rules_v2 import FavoriteInterval
from app.dts_v2_dirty_queue_store import DirtyClaimV2, DirtyKeyV2
from app.dts_v2_source_repository import DtsV2CurrentSourceRow
import app.dts_v2_teacher_student_domain_projector as relation_domain


DOM_STUDENT = "dom:v1:" + "a" * 64
DOM_STUDENT_2 = "dom:v1:" + "b" * 64
UTC = timezone.utc


def _position(*, offset: int = 1) -> dict[str, Any]:
    return {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "numeric",
        "record_id": str(offset),
        "source_partition_epoch_id": "epoch-relation-test",
        "topic": "topic-relation-test",
        "partition_id": 0,
        "offset_value": offset,
    }


def _favorite_row(
    *,
    source_key: str = "1",
    add_time: str | None = "2026-08-20T00:00:00+00:00",
    is_deleted: bool = False,
) -> DtsV2CurrentSourceRow:
    row = {
        "id": int(source_key),
        "tea_id": 100,
        "student_token": DOM_STUDENT,
        "add_time": add_time,
    }
    return DtsV2CurrentSourceRow.from_database_row(
        {
            "source_region": "dom",
            "source_table": "dom_teacher_favorite",
            "source_key": source_key,
            "source_key_type": "NUMERIC",
            "source_row_revision": 1,
            "source_row": row,
            "source_field_types": {
                "id": "NUMERIC",
                "tea_id": "NUMERIC",
                "student_token": "TEXT",
                "add_time": "TEMPORAL",
            },
            "source_position_v2": _position(offset=int(source_key)),
            "source_payload_hash": f"{int(source_key):064x}",
            "is_deleted": is_deleted,
            "provenance_state": "V2_CONFIRMED",
        }
    )


def _blacklist_row(
    *,
    source_key: str = "2",
    student_token: str = DOM_STUDENT,
    valid_start_time: str | None = "2026-08-20T00:00:00+00:00",
    valid_end_time: str | None = None,
    is_valid_forever: int | None = 1,
) -> DtsV2CurrentSourceRow:
    return DtsV2CurrentSourceRow.from_database_row(
        {
            "source_region": "dom",
            "source_table": "dom_teacher_blacklist",
            "source_key": source_key,
            "source_key_type": "NUMERIC",
            "source_row_revision": 1,
            "source_row": {
                "id": int(source_key),
                "teacher_id": 100,
                "student_token": student_token,
                "valid_start_time": valid_start_time,
                "valid_end_time": valid_end_time,
                "is_valid_forever": is_valid_forever,
                "add_time": "2026-08-20T00:00:00+00:00",
            },
            "source_field_types": {
                "id": "NUMERIC",
                "teacher_id": "NUMERIC",
                "student_token": "TEXT",
                "valid_start_time": "TEMPORAL",
                "valid_end_time": "TEMPORAL",
                "is_valid_forever": "NUMERIC",
                "add_time": "TEMPORAL",
            },
            "source_position_v2": _position(offset=2),
            "source_payload_hash": f"{int(source_key):064x}",
            "is_deleted": False,
            "provenance_state": "V2_CONFIRMED",
        }
    )


def _pair() -> relation_domain._Pair:
    return relation_domain._Pair("dom", "NUMERIC", "100", DOM_STUDENT)


def _scope(
    table: str,
    kind: str,
    *,
    state: str,
    history_from: datetime | None = None,
    history_through: datetime | None = None,
) -> relation_domain._ScopeEvidence:
    return relation_domain._ScopeEvidence(
        source_table=table,
        scope_kind=kind,
        scope_level="TEACHER",
        scope_key="100",
        state=state,
        row_version=1,
        active_snapshot_id="snapshot-1" if state == "COMPLETE" else None,
        active_fence_hash="f" * 64 if state == "COMPLETE" else None,
        history_from=history_from,
        history_through=history_through,
    )


def _scopes(*, current_complete: bool) -> dict[tuple[str, str], Any]:
    result = {}
    for table in relation_domain._relationship_tables("dom"):
        result[(table, "CURRENT")] = _scope(
            table,
            "CURRENT",
            state="COMPLETE" if current_complete else "UNKNOWN",
        )
        result[(table, "HISTORY")] = _scope(
            table,
            "HISTORY",
            state="UNKNOWN",
        )
    return result


def _course(
    appoint_id: str,
    *,
    completion_end_time: datetime,
) -> relation_domain._FavoriteCourse:
    return relation_domain._FavoriteCourse(
        source_appoint_id=appoint_id,
        appoint_id_type="NUMERIC",
        completion_participation_seq=1,
        completion_teacher_id="100",
        completion_teacher_id_type="NUMERIC",
        completion_end_time=completion_end_time,
        completion_source_revision=3,
        completion_conflict_status="NONE",
        course_evidence_status="CONFIRMED",
        course_row_version=2,
    )


def test_current_favorite_and_block_do_not_require_an_end_course() -> None:
    current = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(_favorite_row(), _blacklist_row()),
        scopes=_scopes(current_complete=False),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert current.is_favorited is True
    assert current.is_blocked is True
    assert current.favorite_evidence_status == "CONFIRMED"
    assert current.block_evidence_status == "CONFIRMED"


def test_incomplete_current_scope_never_turns_absence_into_false() -> None:
    unknown = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(),
        scopes=_scopes(current_complete=False),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )
    confirmed_empty = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(),
        scopes=_scopes(current_complete=True),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert unknown.is_favorited is None
    assert unknown.is_blocked is None
    assert confirmed_empty.is_favorited is False
    assert confirmed_empty.is_blocked is False


def test_blacklist_teacher_threshold_keeps_complete_unknown_boundary() -> None:
    active = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(
            _blacklist_row(),
            _blacklist_row(source_key="3", student_token=DOM_STUDENT_2),
        ),
        scopes=_scopes(current_complete=False),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )
    source_missing = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(
            _blacklist_row(
                valid_start_time="3000-01-01T00:00:00+00:00",
                valid_end_time="2999-01-01T00:00:00+00:00",
                is_valid_forever=None,
            ),
        ),
        scopes=_scopes(current_complete=True),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert active.blacklist_threshold_state == "ACTIVE"
    assert active.blacklist_source_collection_complete is False
    assert active.blacklist_distinct_active_student_count == 2
    assert source_missing.blacklist_threshold_state == "SOURCE_MISSING"
    assert source_missing.blacklist_source_collection_complete is True
    assert source_missing.blacklist_source_missing_student_count == 1


def test_missing_add_time_updates_current_but_cannot_prove_historical_state() -> None:
    current = relation_domain._evaluate_current_relationship(
        pair=_pair(),
        rows=(_favorite_row(add_time=None),),
        scopes=_scopes(current_complete=True),
        business_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )
    assert current.is_favorited is True
    assert current.favorite_evidence_status == "SOURCE_MISSING"

    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    history_scope = _scope(
        "dom_teacher_favorite",
        "HISTORY",
        state="COMPLETE",
        history_from=datetime(2026, 8, 1, tzinfo=UTC),
        history_through=datetime(2026, 8, 30, tzinfo=UTC),
    )
    requirements = relation_domain._build_observation_requirements(
        pair=_pair(),
        courses=(_course("9", completion_end_time=end_time),),
        intervals=(
            FavoriteInterval(
                teacher_id="100",
                teacher_id_type="NUMERIC",
                student_token=DOM_STUDENT,
                start_at=None,
                end_at=None,
                start_evidence_confirmed=False,
                end_evidence_confirmed=True,
            ),
        ),
        favorite_history_scope=history_scope,
        existing_observations={},
        business_as_of=end_time + timedelta(hours=25),
    )

    assert requirements[0].observed_at == end_time + timedelta(hours=24)
    assert requirements[0].status == "WAITING_EVIDENCE"
    assert requirements[0].relation_state is None
    assert requirements[0].relation_error_code == (
        "SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING"
    )


def test_history_scope_must_cover_end_to_end_plus_24_hours() -> None:
    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    incomplete = _scope(
        "dom_teacher_favorite",
        "HISTORY",
        state="COMPLETE",
        history_from=end_time + timedelta(hours=1),
        history_through=end_time + timedelta(days=2),
    )
    requirements = relation_domain._build_observation_requirements(
        pair=_pair(),
        courses=(_course("9", completion_end_time=end_time),),
        intervals=(
            FavoriteInterval(
                teacher_id="100",
                teacher_id_type="NUMERIC",
                student_token=DOM_STUDENT,
                start_at=end_time - timedelta(days=1),
                end_at=None,
                start_evidence_confirmed=True,
                end_evidence_confirmed=True,
            ),
        ),
        favorite_history_scope=incomplete,
        existing_observations={},
        business_as_of=end_time + timedelta(hours=25),
    )

    assert requirements[0].status == "WAITING_HISTORY"
    assert requirements[0].relation_state is None


def test_unique_candidate_uses_end_plus_24h_then_numeric_appoint_order() -> None:
    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    history_scope = _scope(
        "dom_teacher_favorite",
        "HISTORY",
        state="COMPLETE",
        history_from=end_time - timedelta(days=2),
        history_through=end_time + timedelta(days=2),
    )
    requirements = relation_domain._build_observation_requirements(
        pair=_pair(),
        courses=(
            _course("10", completion_end_time=end_time),
            _course("9", completion_end_time=end_time),
        ),
        intervals=(
            FavoriteInterval(
                teacher_id="100",
                teacher_id_type="NUMERIC",
                student_token=DOM_STUDENT,
                start_at=end_time - timedelta(days=1),
                end_at=None,
                start_evidence_confirmed=True,
                end_evidence_confirmed=True,
            ),
        ),
        favorite_history_scope=history_scope,
        existing_observations={},
        business_as_of=end_time + timedelta(hours=25),
    )
    intent = relation_domain._build_attribution_intent(
        pair=_pair(),
        requirements=requirements,
        existing_attribution={
            "source_appoint_id": "10",
            "status": "AWARDED",
        },
    )

    assert all(row.status == "CONFIRMED_TRUE" for row in requirements)
    assert intent == {
        "disposition": "RESELECT_REQUIRED",
        "action": "RESELECT",
        "selected_source_appoint_id": "9",
        "reason": None,
        "existing_status": "AWARDED",
    }


def test_relation_start_after_observation_does_not_use_dts_arrival_time() -> None:
    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    observed_at = end_time + timedelta(hours=24)
    history_scope = _scope(
        "dom_teacher_favorite",
        "HISTORY",
        state="COMPLETE",
        history_from=end_time - timedelta(days=1),
        history_through=observed_at + timedelta(days=1),
    )
    requirements = relation_domain._build_observation_requirements(
        pair=_pair(),
        courses=(_course("9", completion_end_time=end_time),),
        intervals=(
            FavoriteInterval(
                teacher_id="100",
                teacher_id_type="NUMERIC",
                student_token=DOM_STUDENT,
                start_at=observed_at + timedelta(seconds=1),
                end_at=None,
                start_evidence_confirmed=True,
                end_evidence_confirmed=True,
            ),
        ),
        favorite_history_scope=history_scope,
        existing_observations={},
        business_as_of=observed_at + timedelta(hours=1),
    )
    assert requirements[0].status == "CONFIRMED_FALSE"
    assert requirements[0].relation_state is False


def test_existing_award_is_held_while_its_observation_is_unknown() -> None:
    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    requirement = relation_domain._ObservationRequirement(
        course=_course("9", completion_end_time=end_time),
        observed_at=end_time + timedelta(hours=24),
        observation_due=True,
        status="WAITING_HISTORY",
        relation_state=None,
        relation_evidence_status="HISTORY_INCOMPLETE",
        relation_error_code="PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE",
        existing_observation_revision=2,
        existing_observation_status="WAITING_HISTORY",
        requires_materialization=False,
    )
    intent = relation_domain._build_attribution_intent(
        pair=_pair(),
        requirements=(requirement,),
        existing_attribution={
            "source_appoint_id": "9",
            "status": "AWARDED",
        },
    )
    assert intent["action"] == "HOLD"
    assert intent["selected_source_appoint_id"] == "9"
    assert intent["reason"] == "REVALIDATION_PENDING"


def test_historical_correction_to_false_emits_reversal_intent() -> None:
    end_time = datetime(2026, 8, 20, tzinfo=UTC)
    requirement = relation_domain._ObservationRequirement(
        course=_course("9", completion_end_time=end_time),
        observed_at=end_time + timedelta(hours=24),
        observation_due=True,
        status="CONFIRMED_FALSE",
        relation_state=False,
        relation_evidence_status="CONFIRMED",
        relation_error_code=None,
        existing_observation_revision=3,
        existing_observation_status="CONFIRMED_FALSE",
        requires_materialization=False,
    )

    intent = relation_domain._build_attribution_intent(
        pair=_pair(),
        requirements=(requirement,),
        existing_attribution={
            "source_appoint_id": "9",
            "status": "AWARDED",
        },
    )

    assert intent == {
        "disposition": "REVERSAL_REQUIRED",
        "action": "REVERSE",
        "selected_source_appoint_id": None,
        "reason": None,
        "existing_status": "AWARDED",
    }


def test_favorite_event_missing_add_time_does_not_fallback_to_source_time() -> None:
    version = relation_domain._RelationshipVersion(
        source_region="dom",
        source_partition_epoch_id="epoch-1",
        topic="topic-1",
        partition_id=0,
        offset_value=1,
        source_table="dom_teacher_favorite",
        source_record_id="1",
        source_record_id_type="NUMERIC",
        source_row_revision=1,
        operation="INSERT",
        before_row=None,
        after_row={
            "id": 1,
            "tea_id": 100,
            "student_token": DOM_STUDENT,
            "add_time": None,
        },
        source_field_types={
            "id": "NUMERIC",
            "tea_id": "NUMERIC",
            "student_token": "TEXT",
            "add_time": "TEMPORAL",
        },
        source_timestamp=datetime(2026, 8, 20, tzinfo=UTC),
    )
    plan = relation_domain._relationship_event_plan(version)
    assert plan.effective_at is None
    assert plan.effective_time_evidence_status == "SOURCE_MISSING"


class _Repository:
    def read_for_dependency(self, connection, **kwargs):
        del connection, kwargs
        return ()

    def read_by_source_keys(self, connection, **kwargs):
        del connection, kwargs
        return ()


class _RevisionStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish_change(self, connection, **kwargs):
        del connection
        self.calls.append(dict(kwargs))
        return SimpleNamespace(status="CHANGED")


def test_process_claim_publishes_typed_teacher_student_aggregate(
    monkeypatch,
) -> None:
    claim = DirtyClaimV2(
        key=DirtyKeyV2("dom", "TEACHER_STUDENT", "100", DOM_STUDENT),
        lease_token="lease-relation",
        claimed_work_revision=2,
        row_version=3,
    )
    revisions = _RevisionStore()
    projector = relation_domain.DtsV2TeacherStudentDomainProjector(
        cutover_coverage_identity={"projection_mode": "SHADOW_BUILD"},
        source_repository=_Repository(),
        revision_store=revisions,
    )
    pair = _pair()
    current = relation_domain._CurrentRelationship(
        is_favorited=True,
        favorite_evidence_status="CONFIRMED",
        is_blocked=None,
        block_evidence_status="SOURCE_MISSING",
        blacklist_threshold_state="SOURCE_MISSING",
        blacklist_threshold_evidence_status="SOURCE_MISSING",
        blacklist_source_collection_complete=False,
        blacklist_distinct_active_student_count=0,
        blacklist_source_missing_student_count=0,
        blacklist_active_student_token_set_hash="a" * 64,
        blacklist_source_missing_student_token_set_hash="b" * 64,
    )
    evidence = SimpleNamespace(
        source_row_revision=1,
        source_position=_position(),
        coverage_identity={
            "projection_mode": "SHADOW_BUILD",
            "trigger": {
                "input_kind": "SOURCE_REVISION",
                "input_identity": {
                    "source_region": "dom",
                    "source_table": "dom_teacher_favorite",
                    "source_key": "1",
                },
                "input_revision": 1,
                "input_fingerprint": "b" * 64,
                "source_payload_hash": "a" * 64,
            },
        },
    )
    monkeypatch.setattr(
        relation_domain, "_read_claim_trigger_evidence", lambda *a, **k: evidence
    )
    monkeypatch.setattr(
        relation_domain,
        "_read_claim_relationship_source_identities",
        lambda *a, **k: set(),
    )
    monkeypatch.setattr(
        relation_domain,
        "_read_existing_event_source_identities",
        lambda *a, **k: set(),
    )
    monkeypatch.setattr(
        relation_domain, "_read_relationship_versions", lambda *a, **k: ()
    )
    monkeypatch.setattr(
        relation_domain,
        "_read_pair_teacher_types",
        lambda *a, **k: {"NUMERIC"},
    )
    monkeypatch.setattr(
        relation_domain,
        "_transaction_timestamp",
        lambda *a, **k: datetime(2026, 8, 22, tzinfo=UTC),
    )
    monkeypatch.setattr(
        relation_domain,
        "_read_preferred_scopes",
        lambda *a, **k: _scopes(current_complete=False),
    )
    monkeypatch.setattr(
        projector,
        "_read_pair_current_rows",
        lambda *a, **k: (),
    )
    monkeypatch.setattr(
        relation_domain,
        "_evaluate_current_relationship",
        lambda *a, **k: current,
    )
    monkeypatch.setattr(
        relation_domain,
        "_read_latest_pair_event",
        lambda *a, **k: {
            "event_sequence": 1,
            "source_partition_epoch_id": "epoch-1",
            "topic": "topic-1",
            "partition_id": 0,
            "offset_value": 1,
            "source_row_revision": 1,
            "effective_at": datetime(2026, 8, 20, tzinfo=UTC),
            "effective_time_evidence_status": "CONFIRMED",
        },
    )
    monkeypatch.setattr(
        relation_domain,
        "_upsert_relationship_current",
        lambda *a, **k: True,
    )
    aggregate_state = {
        "protocol_version": "teacher-student-domain-v1",
        "relationship_current": {"is_favorited": True},
        "favorite_observation_requirements": [],
        "favorite_attribution_intent": {"action": "NONE"},
    }
    monkeypatch.setattr(
        relation_domain,
        "_build_aggregate_state",
        lambda *a, **k: (aggregate_state, 0, False),
    )
    monkeypatch.setattr(
        relation_domain,
        "publish_regional_teacher_aggregate_v2",
        lambda *a, **k: SimpleNamespace(status="CHANGED"),
    )

    result = projector.process_claim(object(), claim)

    assert result["relationship_current_changes"] == 1
    assert result["aggregate_events"] == 2
    assert result["teacher_aggregate_events"] == 1
    assert len(revisions.calls) == 1
    call = revisions.calls[0]
    assert call["aggregate_type"] == "TEACHER_STUDENT"
    assert call["aggregate_key"] == {
        "source_region": "dom",
        "teacher_id": "100",
        "student_token": DOM_STUDENT,
    }
    assert call["aggregate_state"] == aggregate_state
    assert call["cutover_coverage_identity"] == evidence.coverage_identity


def test_dom_dirty_key_rejects_raw_student_identifier() -> None:
    projector = relation_domain.DtsV2TeacherStudentDomainProjector(
        cutover_coverage_identity={"projection_mode": "SHADOW_BUILD"}
    )
    claim = DirtyClaimV2(
        key=DirtyKeyV2("dom", "TEACHER_STUDENT", "100", DOM_STUDENT),
        lease_token="lease-relation",
        claimed_work_revision=1,
        row_version=1,
    )
    object.__setattr__(claim.key, "key_part_2", "raw-student")
    with pytest.raises(
        relation_domain.DtsV2TeacherStudentDomainProjectorError,
        match="DOM_TOKEN_INVALID",
    ):
        projector.process_claim(object(), claim)
