from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from typing import Any

import pytest

import app.dts_v2_favorite_runtime as runtime
from app.dts_v2_teacher_student_outbox_processor import (
    BlacklistThresholdMaterializationV2,
    FavoriteObservationMaterializationV2,
    TeacherStudentMaterializationPlanV2,
)


DOM_STUDENT = "dom:v1:" + "a" * 64
UTC = timezone.utc


def _claim() -> runtime.FavoriteObservationClaimV2:
    return runtime.FavoriteObservationClaimV2.from_database(
        {
            "source_region": "dom",
            "source_appoint_id": "9001",
            "observation_revision": 1,
            "teacher_id": "100",
            "teacher_id_type": "NUMERIC",
            "student_token": DOM_STUDENT,
            "completion_participation_seq": 2,
            "observed_at": "2026-08-21T00:00:00Z",
            "required_evidence_revision": 3,
            "claimed_evidence_revision": 3,
            "required_evidence_fingerprint": "a" * 64,
            "lease_owner": "favorite-worker-1",
            "lease_token": "lease-1",
            "row_version": 4,
        }
    )


def _observation(*, complete: bool = True):
    end = datetime(2026, 8, 20, tzinfo=UTC) if complete else None
    return FavoriteObservationMaterializationV2(
        source_region="dom",
        source_appoint_id="9001",
        appoint_id_type="NUMERIC" if complete else None,
        teacher_id="100",
        teacher_id_type="NUMERIC",
        student_token=DOM_STUDENT,
        completion_participation_seq=2,
        completion_source_revision=3,
        completion_end_time=end,
        observed_at=end + timedelta(hours=24) if end else None,
        projection_status="PENDING",
        projection_relation_state=None,
        projection_evidence_status="PENDING",
        projection_error_code=None,
        requires_materialization=True,
    )


def _plan(*observations) -> TeacherStudentMaterializationPlanV2:
    scope = {
        "source_table": "dom_teacher_blacklist",
        "scope_kind": "CURRENT",
        "scope_level": None,
        "scope_key": None,
        "state": "UNKNOWN",
        "row_version": None,
        "active_snapshot_id": None,
        "active_fence_hash": None,
        "history_from": None,
        "history_through": None,
    }
    evidence = {
        "protocol_version": "blacklist-threshold-evidence-v1",
        "source_region": "dom",
        "teacher_id": "100",
        "teacher_id_type": "NUMERIC",
        "threshold": 2,
        "threshold_state": "SOURCE_MISSING",
        "evidence_status": "SOURCE_MISSING",
        "source_collection_complete": False,
        "distinct_active_student_count": 0,
        "source_missing_student_count": 0,
        "active_student_token_set_hash": "a" * 64,
        "source_missing_student_token_set_hash": "b" * 64,
        "scope": scope,
    }
    return TeacherStudentMaterializationPlanV2(
        source_region="dom",
        teacher_id="100",
        teacher_id_type="NUMERIC",
        student_token=DOM_STUDENT,
        is_favorited=True,
        favorite_evidence_status="CONFIRMED",
        is_blocked=None,
        block_evidence_status="SOURCE_MISSING",
        blacklist_threshold=BlacklistThresholdMaterializationV2(
            threshold_state="SOURCE_MISSING",
            evidence_status="SOURCE_MISSING",
            source_collection_complete=False,
            distinct_active_student_count=0,
            source_missing_student_count=0,
            active_student_token_set_hash="a" * 64,
            source_missing_student_token_set_hash="b" * 64,
            evidence=evidence,
        ),
        observations=tuple(observations),
        projected_attribution_action="NONE",
        projected_attribution_course_id=None,
    )


class _Scalar:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _MaterializerConnection:
    def __init__(
        self,
        results: list[dict[str, Any]],
        *,
        projection_generation: int = 3,
    ) -> None:
        self.results = list(results)
        self.projection_generation = projection_generation
        self.sql: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    def execute(self, statement, parameters=None):
        self.sql.append(str(statement))
        self.parameters.append(dict(parameters or {}))
        if "dts_pipeline_control" in str(statement):
            return _Mappings(
                {
                    "mode": "V2_PRIMARY",
                    "projection_generation": self.projection_generation,
                }
            )
        if "pg_advisory_xact_lock" in str(statement):
            return _Scalar(None)
        if "reconcile_blacklist_threshold_v2" in str(statement):
            return _Scalar(
                {
                    "match_status": "SUPPRESSED",
                    "current_blocked_student_count": 0,
                    "student_token_set_hash": "a" * 64,
                }
            )
        return _Scalar(self.results.pop(0))


def test_claim_parser_requires_protected_dom_token_and_revision_vector() -> None:
    claim = _claim()
    assert claim.observed_at == datetime(2026, 8, 21, tzinfo=UTC)
    assert claim.claimed_evidence_revision == 3

    with pytest.raises(
        runtime.DtsV2FavoriteRuntimeError,
        match="CLAIM_DOM_TOKEN_INVALID",
    ):
        runtime.FavoriteObservationClaimV2.from_database(
            {**claim.__dict__, "student_token": "raw-student"}
        )


def test_materializer_only_creates_work_for_complete_frozen_identity() -> None:
    connection = _MaterializerConnection([{"status": "CREATED"}])
    result = runtime.PostgresDtsV2FavoriteMaterializer().apply_teacher_student_plan(
        connection,  # type: ignore[arg-type]
        _plan(_observation(), _observation(complete=False)),
        aggregate_revision=7,
        triggering_event_id="source_wide.changed.v2:TEACHER_STUDENT:x:r7",
    )

    assert result == {
        "observation_rows_created": 1,
        "observation_rows_requeued": 0,
        "observation_rows_unchanged": 0,
        "observation_rows_skipped_missing_evidence": 1,
        "attributions_held": 0,
    }
    materialize = next(
        index
        for index, sql in enumerate(connection.sql)
        if "materialize_favorite_observation_v2" in sql
    )
    assert connection.parameters[materialize]["projection_generation"] == 3
    assert connection.parameters[materialize]["observed_at"] == datetime(
        2026, 8, 21, tzinfo=UTC
    )
    assert result.affected_teacher_ids == ()
    blacklist = next(
        index
        for index, sql in enumerate(connection.sql)
        if "reconcile_blacklist_threshold_v2" in sql
    )
    blacklist_parameters = connection.parameters[blacklist]
    assert {
        key: value
        for key, value in blacklist_parameters.items()
        if key != "threshold_evidence"
    } == {
        "source_region": "dom",
        "teacher_id": "100",
        "aggregate_revision": 7,
        "projection_generation": 3,
        "triggering_event_id": (
            "source_wide.changed.v2:TEACHER_STUDENT:x:r7"
        ),
    }
    assert json.loads(
        blacklist_parameters["threshold_evidence"]
    )["threshold_state"] == "SOURCE_MISSING"


def test_materializer_never_uses_aggregate_revision_as_projection_generation() -> None:
    connection = _MaterializerConnection(
        [{"status": "CREATED"}], projection_generation=3
    )

    runtime.PostgresDtsV2FavoriteMaterializer().apply_teacher_student_plan(
        connection,  # type: ignore[arg-type]
        _plan(_observation()),
        aggregate_revision=17,
        triggering_event_id="source_wide.changed.v2:TEACHER_STUDENT:x:r17",
    )

    materialize = next(
        index
        for index, sql in enumerate(connection.sql)
        if "materialize_favorite_observation_v2" in sql
    )
    assert connection.parameters[materialize]["projection_generation"] == 3


def test_materializer_exposes_typed_attribution_outcome_without_polling() -> None:
    connection = _MaterializerConnection(
        [
            {
                "status": "REQUEUED",
                "attribution_held": False,
                "attribution_outcome": {
                    "action": "RESELECT",
                    "source_region": "dom",
                    "teacher_id": "100",
                    "student_token": DOM_STUDENT,
                    "previous_source_appoint_id": "8999",
                    "current_source_appoint_id": "9001",
                    "award_generation": 2,
                    "score_entry_ids": ["reversal-1", "award-2"],
                },
            }
        ]
    )

    result = runtime.PostgresDtsV2FavoriteMaterializer().apply_teacher_student_plan(
        connection,  # type: ignore[arg-type]
        _plan(_observation()),
        aggregate_revision=7,
        triggering_event_id="source_wide.changed.v2:TEACHER_STUDENT:x:r7",
    )

    assert result.affected_teacher_ids == ("100",)
    assert result.attribution_outcomes[0].action == "RESELECT"
    assert result.attribution_outcomes[0].score_entry_ids == (
        "reversal-1",
        "award-2",
    )


def test_score_changing_outcome_is_explicitly_rebuilder_relevant() -> None:
    outcome = runtime.FavoriteAttributionOutcomeV2.from_database(
        {
            "action": "AWARD",
            "source_region": "dom",
            "teacher_id": "100",
            "student_token": DOM_STUDENT,
            "previous_source_appoint_id": None,
            "current_source_appoint_id": "9001",
            "award_generation": 1,
            "score_entry_ids": ["award-1"],
        },
        source_region="dom",
        teacher_id="100",
        student_token=DOM_STUDENT,
    )

    assert outcome.affected_teacher_ids == ("100",)


class _Mappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _EvaluationConnection:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def execute(self, statement, parameters=None):
        del parameters
        if "pg_advisory_xact_lock" in str(statement):
            return _Scalar(None)
        return _Mappings(self.row)


def _course_row(*, conflict: str = "NONE") -> dict[str, Any]:
    return {
        "completion_participation_seq": 2,
        "completion_teacher_id": "100",
        "completion_teacher_id_type": "NUMERIC",
        "completion_student_token": DOM_STUDENT,
        "completion_end_time": datetime(2026, 8, 20, tzinfo=UTC),
        "completion_conflict_status": conflict,
        "completion_voided_at": None,
        "evidence_status": "CONFIRMED",
        "database_now": datetime(2026, 8, 22, tzinfo=UTC),
        "evidence_fingerprint": "b" * 64,
    }


def test_completion_conflict_waits_and_never_creates_a_new_award() -> None:
    evaluation = runtime.evaluate_favorite_observation_claim_v2(
        _EvaluationConnection(_course_row(conflict="PENDING")),  # type: ignore[arg-type]
        _claim(),
    )

    assert evaluation.status == "WAITING_EVIDENCE"
    assert evaluation.relation_state is None
    assert evaluation.relation_error_code == (
        "SOURCE_CONFLICT:COURSE_COMPLETION_PENDING"
    )


def test_history_incomplete_stays_waiting_unknown(monkeypatch) -> None:
    pair_scope = SimpleNamespace(
        complete=False,
        history_from=None,
        history_through=None,
    )
    monkeypatch.setattr(
        runtime,
        "_read_preferred_scopes",
        lambda *a, **k: {("dom_teacher_favorite", "HISTORY"): pair_scope},
    )
    monkeypatch.setattr(runtime, "_read_favorite_intervals", lambda *a, **k: ())
    monkeypatch.setattr(runtime, "_history_covers", lambda *a, **k: False)

    evaluation = runtime.evaluate_favorite_observation_claim_v2(
        _EvaluationConnection(_course_row()),  # type: ignore[arg-type]
        _claim(),
    )

    assert evaluation.status == "WAITING_HISTORY"
    assert evaluation.relation_state is None
    assert evaluation.relation_evidence_status == "HISTORY_INCOMPLETE"


def test_future_observation_is_rejected_even_after_an_invalid_claim() -> None:
    row = _course_row()
    row["database_now"] = datetime(2026, 8, 20, 23, 59, tzinfo=UTC)
    with pytest.raises(
        runtime.DtsV2FavoriteRuntimeError,
        match="OBSERVATION_NOT_DUE",
    ):
        runtime.evaluate_favorite_observation_claim_v2(
            _EvaluationConnection(row),  # type: ignore[arg-type]
            _claim(),
        )


def test_worker_configuration_rejects_host_controlled_invalid_batch() -> None:
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    worker = runtime.DtsV2FavoriteObservationWorker(engine)  # type: ignore[arg-type]
    with pytest.raises(
        runtime.DtsV2FavoriteRuntimeError,
        match="BATCH_SIZE_INVALID",
    ):
        worker.run_once(worker_id="worker-1", max_observations=0)


class _ModeConnection:
    def __init__(self, mode: str | None) -> None:
        self.mode = mode
        self.sql: list[str] = []

    def execute(self, statement, parameters=None):
        del parameters
        sql = str(statement)
        self.sql.append(sql)
        if "dts_pipeline_control" not in sql:
            raise AssertionError(f"favorite work ran outside V2_PRIMARY: {sql}")
        return _Scalar(self.mode)


class _Begin:
    def __init__(self, connection: _ModeConnection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False


class _ModeEngine:
    def __init__(self, mode: str | None) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.connection = _ModeConnection(mode)

    def begin(self):
        return _Begin(self.connection)


@pytest.mark.parametrize(
    "mode", ["V1_COMPAT_DUAL_CAPTURE", "ROLLED_BACK"]
)
def test_worker_is_inert_outside_v2_primary(mode: str) -> None:
    engine = _ModeEngine(mode)
    worker = runtime.DtsV2FavoriteObservationWorker(engine)  # type: ignore[arg-type]

    result = worker.run_once(worker_id="favorite-worker-1")

    assert set(result.values()) == {0}
    assert len(engine.connection.sql) == 1
    assert "dts_pipeline_control" in engine.connection.sql[0]


def test_worker_fails_closed_when_pipeline_control_is_missing() -> None:
    engine = _ModeEngine(None)
    worker = runtime.DtsV2FavoriteObservationWorker(engine)  # type: ignore[arg-type]

    with pytest.raises(
        runtime.DtsV2FavoriteRuntimeError,
        match="PIPELINE_CONTROL_INVALID",
    ):
        worker.run_once(worker_id="favorite-worker-1")


def test_production_guarded_worker_does_not_read_or_claim_outside_primary() -> None:
    engine = _ModeEngine("must-not-be-read")

    class _ClosedGuard:
        def acquire(self, connection, *, component):
            del connection
            assert component == "FAVORITE"
            return None

    worker = runtime.DtsV2FavoriteObservationWorker(
        engine,  # type: ignore[arg-type]
        primary_guard=_ClosedGuard(),
    )
    assert set(
        worker.run_once(worker_id="favorite-worker-1").values()
    ) == {0}
    assert engine.connection.sql == []


class _WorkerTransactionConnection:
    def __init__(self, transaction_id: int) -> None:
        self.transaction_id = transaction_id

    def execute(self, statement, parameters=None):
        del parameters
        sql = str(statement)
        if "dts_pipeline_control" in sql:
            return _Scalar("V2_PRIMARY")
        if "reap_expired_favorite_observations_v2" in sql:
            return _Scalar(
                {
                    "reaped": 0,
                    "retry": 0,
                    "dead": 0,
                    "stale_requeued": 0,
                }
            )
        raise AssertionError(f"unexpected worker SQL: {sql}")


class _WorkerTransaction:
    def __init__(self, engine: "_WorkerEngine") -> None:
        self.engine = engine
        self.connection = _WorkerTransactionConnection(
            len(engine.connections) + 1
        )

    def __enter__(self):
        self.engine.connections.append(self.connection)
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        del exc, traceback
        outcome = "ROLLBACK" if exc_type is not None else "COMMIT"
        self.engine.outcomes.append(
            (self.connection.transaction_id, outcome)
        )
        return False


class _WorkerEngine:
    def __init__(self) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.connections: list[_WorkerTransactionConnection] = []
        self.outcomes: list[tuple[int, str]] = []

    def begin(self):
        return _WorkerTransaction(self)


class _ProjectionRebuilder:
    def __init__(self) -> None:
        self.transaction_ids: list[int] = []

    def rebuild_after_favorite(self, connection, outcome):
        assert outcome.action == "AWARD"
        self.transaction_ids.append(connection.transaction_id)
        return {
            "lesson_score_results": 1,
            "score_accounts": 1,
            "teacher_qualifications": 1,
        }


def _award_completion() -> dict[str, Any]:
    return {
        "status": "CONFIRMED_TRUE",
        "score_entries_created": 1,
        "attributions_awarded": 1,
        "attributions_reversed": 0,
        "attributions_reselected": 0,
        "attribution_outcome": {
            "action": "AWARD",
            "source_region": "dom",
            "teacher_id": "100",
            "student_token": DOM_STUDENT,
            "previous_source_appoint_id": None,
            "current_source_appoint_id": "9001",
            "award_generation": 1,
            "score_entry_ids": ["favorite-award-1"],
        },
    }


def _confirmed_true_evaluation() -> runtime.FavoriteObservationEvaluationV2:
    return runtime.FavoriteObservationEvaluationV2(
        status="CONFIRMED_TRUE",
        relation_state=True,
        relation_evidence_status="CONFIRMED",
        relation_error_code=None,
        evidence_fingerprint="a" * 64,
    )


def test_score_change_and_projection_rebuild_share_the_completion_transaction(
    monkeypatch,
) -> None:
    engine = _WorkerEngine()
    rebuilder = _ProjectionRebuilder()
    worker = runtime.DtsV2FavoriteObservationWorker(
        engine,  # type: ignore[arg-type]
        projection_rebuilder=rebuilder,
    )
    monkeypatch.setattr(worker, "_claim", lambda **kwargs: (_claim(),))
    monkeypatch.setattr(
        runtime,
        "evaluate_favorite_observation_claim_v2",
        lambda *args, **kwargs: _confirmed_true_evaluation(),
    )
    completion_transaction_ids: list[int] = []

    def complete(connection, **kwargs):
        del kwargs
        completion_transaction_ids.append(connection.transaction_id)
        return _award_completion()

    monkeypatch.setattr(runtime, "_complete_claim", complete)

    result = worker.run_once(worker_id="favorite-worker-1")

    assert result["score_entries_created"] == 1
    assert completion_transaction_ids == rebuilder.transaction_ids
    assert engine.outcomes[-1] == (
        completion_transaction_ids[0],
        "COMMIT",
    )


def test_missing_projection_rebuilder_rolls_back_score_change_and_retries(
    monkeypatch,
) -> None:
    engine = _WorkerEngine()
    worker = runtime.DtsV2FavoriteObservationWorker(
        engine  # type: ignore[arg-type]
    )
    monkeypatch.setattr(worker, "_claim", lambda **kwargs: (_claim(),))
    monkeypatch.setattr(
        runtime,
        "evaluate_favorite_observation_claim_v2",
        lambda *args, **kwargs: _confirmed_true_evaluation(),
    )
    completion_transaction_ids: list[int] = []

    def complete(connection, **kwargs):
        del kwargs
        completion_transaction_ids.append(connection.transaction_id)
        return _award_completion()

    monkeypatch.setattr(runtime, "_complete_claim", complete)
    monkeypatch.setattr(
        runtime,
        "_fail_claim",
        lambda *args, **kwargs: {"status": "RETRY"},
    )

    result = worker.run_once(worker_id="favorite-worker-1")

    assert result["score_entries_created"] == 0
    assert result["retries"] == 1
    assert (
        completion_transaction_ids[0],
        "ROLLBACK",
    ) in engine.outcomes
