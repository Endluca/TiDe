from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.config_service import seed_default_configs
from app.database import engine, session_scope
from app.db_models import (
    AuditEventRecord,
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherQualificationRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from app.fixed_growth_baseline import ensure_fixed_growth_assignments
from app.shared_task_score_settlement import (
    ENTRY_TYPE,
    SOURCE_WIDE_SNAPSHOT_LABEL,
    SharedTaskScoreSettlementWorker,
)
from app.source_contracts import TEACHER_SOURCE_FIELDS
from app.source_wide_worker import (
    EVENT_TYPE as SOURCE_WIDE_EVENT_TYPE,
    SourceWideWorker,
)
from app.task_catalog import MANDATORY_TASK_CODES


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
TASK_CODES = MANDATORY_TASK_CODES


@pytest.fixture(autouse=True)
def _enable_qualification_grants_for_existing_contract_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "true",
    )


def test_valid_ledger_read_does_not_require_score_entry_update_privilege() -> None:
    class _Rows:
        @staticmethod
        def all() -> list[tuple[object, object]]:
            return []

    class _CapturingSession:
        statement = None

        def execute(self, statement):
            self.statement = statement
            return _Rows()

    session = _CapturingSession()

    assert SharedTaskScoreSettlementWorker._valid_ledger_score(
        session,
        teacher_id="TEACHER-READ-ONLY-LEDGER",
        expected_points={},
    ) == 0
    assert session.statement is not None
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE" not in sql.upper()


def test_worker_never_requests_update_locks_on_shared_assignments() -> None:
    violating_statements: list[str] = []

    def inspect_orm_statement(orm_execute_state) -> None:
        if not orm_execute_state.is_select:
            return
        sql = str(
            orm_execute_state.statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).upper()
        if "TASK_ASSIGNMENTS" in sql and "FOR UPDATE" in sql:
            violating_statements.append(sql)

    _prepare_config()
    teacher_id = "REAL-SCORE-READ-ONLY-ASSIGNMENTS"
    _teacher(teacher_id)
    _assignments(teacher_id, completed={"G01"})

    event.listen(Session, "do_orm_execute", inspect_orm_statement)
    try:
        result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=1)
    finally:
        event.remove(Session, "do_orm_execute", inspect_orm_statement)

    assert result["settled"] == 1
    assert violating_statements == []


def test_v2_primary_keeps_fixed_task_ledger_but_defers_teacher_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-V2-PRIMARY"
    _teacher(teacher_id)
    assignments = _assignments(teacher_id, completed={"G01"})
    worker = SharedTaskScoreSettlementWorker(engine)
    refreshes: list[dict[str, str]] = []
    monkeypatch.setattr(
        worker,
        "_dts_pipeline_mode",
        lambda _session: "V2_PRIMARY",
    )
    monkeypatch.setattr(
        worker,
        "_enqueue_v2_teacher_refresh",
        lambda _session, **values: refreshes.append(values),
    )

    result = worker.run_once(max_events=1)

    assert result["settled"] == 1
    assert result["score_entries_created"] == 1
    assert refreshes == [
        {
            "teacher_id": teacher_id,
            "assignment_id": assignments["G01"],
            "triggering_outbox_id": (
                f"OUTBOX-{assignments['G01']}-initial"
            ),
        }
    ]
    with session_scope(engine) as session:
        account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert account is not None and account.current_score == 3
        assert teacher is not None and teacher.total_score == 0
        assert qualification is not None
        assert qualification.graduation_qualified is False


def _teacher(
    teacher_id: str,
    *,
    untrusted_score: float | None = None,
    initial_total_score: float = 0,
    source_wide: bool = True,
) -> None:
    with session_scope(engine) as session:
        if source_wide:
            session.add(
                TeacherSourceWideRecord(
                    tchr_id=teacher_id,
                    real_name=f"Teacher {teacher_id}",
                    status="TEST-ACTIVE",
                    job_days=5,
                )
            )
        metric_inputs = {
            "capacity_score": 10,
            "new_teacher_task_score": 0,
            "mandatory_task_assignment_count": 0,
            "mandatory_task_completed_count": 0,
            "mandatory_task_expected_count": 9,
        }
        session.add(
            TeacherRecord(
                teacher_id=teacher_id,
                camp_enrollment_id=f"CAMP-{teacher_id}",
                name=f"Teacher {teacher_id}",
                country="PH",
                timezone="Asia/Manila",
                camp_day=5,
                graduation_state="IN_CAMP",
                total_score=initial_total_score,
                graduation_threshold=100,
                data_mode="REAL",
                source_snapshot_label=(
                    SOURCE_WIDE_SNAPSHOT_LABEL if source_wide else "TEST"
                ),
                payload={
                    "teacher_id": teacher_id,
                    "data_mode": "REAL",
                    "metric_inputs": metric_inputs,
                    "metric_provenance": {},
                    "dimensions": [],
                    "raw_total_score": initial_total_score,
                    "total_score": initial_total_score,
                    "external_display_score": initial_total_score,
                    "score_projection_scope": (
                        SOURCE_WIDE_SNAPSHOT_LABEL if source_wide else "TEST"
                    ),
                },
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            TeacherQualificationRecord(
                teacher_id=teacher_id,
                graduation_criteria_met=False,
                graduation_qualified=False,
                graduation_qualified_at=None,
                gold_criteria_met=False,
                gold_qualified=False,
                gold_qualified_at=None,
                score_rule_version="test-source-wide",
                gate_results={
                    "source_teacher_present": source_wide,
                    "mandatory_task_assignment_count": 0,
                    "mandatory_task_completed_count": 0,
                    "mandatory_task_expected_count": len(TASK_CODES),
                    "l0_complaint_count": 0,
                    "l0_complaint_evidence_status": "CONFIRMED",
                    "late_count": 0,
                    "early_count": 0,
                    "absent_count": 0,
                    "attendance_evidence_status": "CONFIRMED",
                    "raw_total_score": initial_total_score,
                },
                revision=1,
                calculated_at=NOW,
            )
        )
        if untrusted_score is not None:
            session.add(
                ScoreAccountRecord(
                    teacher_id=teacher_id,
                    dimension="NEW_TEACHER_TASK",
                    current_score=untrusted_score,
                    score_rule_version="historical-import",
                    version=1,
                    updated_at=NOW,
                    payload={"source_mode": "REAL_IMPORT"},
                )
            )


def _assignments(
    teacher_id: str,
    *,
    codes: tuple[str, ...] = TASK_CODES,
    completed: set[str] | None = None,
    source_mode: str = "REAL",
) -> dict[str, str]:
    completed = completed or set()
    assignment_ids: dict[str, str] = {}
    with session_scope(engine) as session:
        for code in codes:
            template = session.get(TaskTemplateRecord, f"{code}:v1")
            assert template is not None
            assignment_id = f"ASSIGN-{teacher_id}-{code}"
            assignment_ids[code] = assignment_id
            is_completed = code in completed
            session.add(
                TaskAssignmentRecord(
                    assignment_id=assignment_id,
                    teacher_id=teacher_id,
                    task_code=code,
                    template_version_id=template.row_id,
                    task_kind="FIXED_GROWTH",
                    creator_system="TRIGGER_CENTER",
                    status="COMPLETED" if is_completed else "ASSIGNED",
                    priority=str(template.payload["priority"]),
                    why=str(template.payload["why_template"]),
                    due_at=None,
                    timezone_used=None,
                    timezone_source=None,
                    timezone_verified_at=None,
                    status_reason_code=None,
                    source_mode=source_mode,
                    dedupe_key=f"fixed:{teacher_id}:{code}",
                    created_by="TRIGGER_CENTER_TEST",
                    updated_by="TRIGGER_CENTER_TEST",
                    row_version=2 if is_completed else 1,
                    assigned_at=NOW,
                    status_changed_at=NOW,
                    completed_at=NOW if is_completed else None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            if is_completed:
                _add_event(
                    session,
                    assignment_id=assignment_id,
                    teacher_id=teacher_id,
                    task_code=code,
                    source_mode=source_mode,
                    suffix="initial",
                )
    return assignment_ids


def _add_event(
    session,
    *,
    assignment_id: str,
    teacher_id: str,
    task_code: str,
    source_mode: str,
    suffix: str,
    to_status: str = "COMPLETED",
) -> str:
    event_id = f"OUTBOX-{assignment_id}-{suffix}"
    session.add(
        OutboxEventRecord(
            outbox_id=event_id,
            event_id=event_id,
            aggregate_type="TASK_ASSIGNMENT",
            aggregate_id=assignment_id,
            event_type="task.assignment_changed.shared",
            payload={
                "schema_version": "task_assignment_changed.shared",
                "assignment_id": assignment_id,
                "teacher_id": teacher_id,
                "task_code": task_code,
                "task_kind": "FIXED_GROWTH",
                "from_status": "ASSIGNED",
                "to_status": to_status,
                "completed_at": NOW.isoformat() if to_status == "COMPLETED" else None,
                "row_version": 2,
                "source_mode": source_mode,
            },
            status="PENDING",
            available_at=NOW,
            attempt_count=0,
            last_error=None,
            created_at=NOW,
            published_at=None,
        )
    )
    return event_id


def _prepare_config() -> None:
    # The global test service seeds four unrelated demo teachers.  Policy
    # publication now rejects mixed legacy/current projections, so make those
    # fixture-only rows satisfy the same source-wide contract before publishing.
    with session_scope(engine) as session:
        teachers = list(session.scalars(select(TeacherRecord)).all())
        for teacher in teachers:
            teacher.source_snapshot_label = SOURCE_WIDE_SNAPSHOT_LABEL
            if session.get(TeacherSourceWideRecord, teacher.teacher_id) is None:
                session.add(
                    TeacherSourceWideRecord(
                        tchr_id=teacher.teacher_id,
                        real_name=teacher.name,
                        status="TEST-ACTIVE",
                    )
                )
        ensure_fixed_growth_assignments(
            session,
            [teacher.teacher_id for teacher in teachers],
            actor_id="TEST:SHARED_TASK_SETTLEMENT",
            occurred_at=NOW,
        )
    seed_default_configs()


def _complete_existing_assignments(
    teacher_id: str,
    codes: set[str],
    *,
    suffix: str,
) -> None:
    with session_scope(engine) as session:
        assignments = {
            item.task_code: item
            for item in session.scalars(
                select(TaskAssignmentRecord).where(
                    TaskAssignmentRecord.teacher_id == teacher_id,
                    TaskAssignmentRecord.task_code.in_(codes),
                )
            ).all()
        }
        assert set(assignments) == codes
        for code in sorted(codes):
            assignment = assignments[code]
            assignment.status = "COMPLETED"
            assignment.completed_at = NOW
            assignment.status_changed_at = NOW
            assignment.row_version = int(assignment.row_version or 0) + 1
            _add_event(
                session,
                assignment_id=assignment.assignment_id,
                teacher_id=teacher_id,
                task_code=code,
                source_mode="REAL",
                suffix=f"{suffix}-{code}",
            )


def _entry_scores(teacher_id: str) -> dict[str, float]:
    with session_scope(engine) as session:
        rows = session.execute(
            select(ScoreEntryRecord, TaskAssignmentRecord)
            .join(
                TaskAssignmentRecord,
                TaskAssignmentRecord.assignment_id == ScoreEntryRecord.task_assignment_id,
            )
            .where(
                ScoreEntryRecord.teacher_id == teacher_id,
                ScoreEntryRecord.entry_type == ENTRY_TYPE,
            )
        ).all()
        return {assignment.task_code: entry.delta_score for entry, assignment in rows}


def test_g01_g08_and_all_current_tasks_settle_to_3_5_and_30_with_explicit_cutover() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-ALL"
    _teacher(teacher_id, untrusted_score=17)
    assignment_ids = _assignments(
        teacher_id,
        completed={"G01", "G08"},
    )

    first = SharedTaskScoreSettlementWorker(engine).run_once(max_events=10)
    assert first["failed"] == 0
    assert first["settled"] == 2
    assert first["score_entries_created"] == 2
    assert _entry_scores(teacher_id) == {"G01": 3, "G08": 5}

    with session_scope(engine) as session:
        account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        assert account is not None
        assert account.current_score == 8
        assert account.payload["source_mode"] == "SYSTEM_TASK_STATUS"
        assert account.payload["score_config"]["version_id"]
        assert len(account.payload["score_config"]["payload_sha256"]) == 64
        audits = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.teacher_id == teacher_id,
                AuditEventRecord.event_type == "score.account.cutover.shared_tasks.v1",
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].payload["previous_untrusted_score"] == 17
        assert audits[0].payload["ledger_score_at_cutover"] == 8

        for code in set(TASK_CODES) - {"G01", "G08"}:
            assignment = session.get(TaskAssignmentRecord, assignment_ids[code])
            assert assignment is not None
            assignment.status = "COMPLETED"
            assignment.completed_at = NOW
            assignment.status_changed_at = NOW
            assignment.row_version = 2
            _add_event(
                session,
                assignment_id=assignment.assignment_id,
                teacher_id=teacher_id,
                task_code=code,
                source_mode="REAL",
                suffix="complete",
            )

    second = SharedTaskScoreSettlementWorker(engine).run_once(max_events=20)
    assert second["failed"] == 0
    assert len(_entry_scores(teacher_id)) == 9
    assert sum(_entry_scores(teacher_id).values()) == 30
    with session_scope(engine) as session:
        account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        assert account is not None and account.current_score == 30
        assert session.scalar(
            select(func.count()).select_from(AuditEventRecord).where(
                AuditEventRecord.teacher_id == teacher_id,
                AuditEventRecord.event_type == "score.account.cutover.shared_tasks.v1",
            )
        ) == 1


def test_duplicate_completion_event_is_idempotent() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-DUP"
    _teacher(teacher_id)
    assignment_ids = _assignments(teacher_id, completed={"G01"})
    worker = SharedTaskScoreSettlementWorker(engine)
    assert worker.run_once(max_events=10)["score_entries_created"] == 1

    with session_scope(engine) as session:
        _add_event(
            session,
            assignment_id=assignment_ids["G01"],
            teacher_id=teacher_id,
            task_code="G01",
            source_mode="REAL",
            suffix="duplicate",
        )
    repeated = worker.run_once(max_events=10)
    assert repeated["failed"] == 0
    assert repeated["score_entries_created"] == 0
    assert _entry_scores(teacher_id) == {"G01": 3}


def test_non_source_wide_teacher_retries_then_dead_letters_without_snapshot_read() -> None:
    _prepare_config()
    teacher_id = "LEGACY-TEACHER-REJECTED"
    _teacher(teacher_id, source_wide=False)
    assignment_ids = _assignments(
        teacher_id,
        codes=("G01",),
        completed={"G01"},
    )
    legacy_snapshot_reads: list[str] = []

    def capture_legacy_snapshot_read(orm_execute_state) -> None:
        if not orm_execute_state.is_select:
            return
        sql = str(
            orm_execute_state.statement.compile(
                dialect=postgresql.dialect(),
            )
        ).lower()
        if "teacher_metric_snapshots" in sql:
            legacy_snapshot_reads.append(sql)

    worker = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
        max_retry_delay=timedelta(0),
        max_attempts=2,
        retry_jitter_ratio=0,
    )
    event.listen(Session, "do_orm_execute", capture_legacy_snapshot_read)
    try:
        first = worker.run_once(max_events=1)
        second = worker.run_once(max_events=1)
    finally:
        event.remove(Session, "do_orm_execute", capture_legacy_snapshot_read)

    assert first["failed"] == 1
    assert first["dead_lettered"] == 0
    assert second["failed"] == 1
    assert second["dead_lettered"] == 1
    assert legacy_snapshot_reads == []
    assert _entry_scores(teacher_id) == {}
    with session_scope(engine) as session:
        outbox = session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.aggregate_id
                == assignment_ids["G01"]
            )
        )
        assert outbox is not None
        assert outbox.status == "DEAD_LETTER"
        assert outbox.attempt_count == 2
        assert outbox.last_error == (
            "SettlementDataError:SOURCE_WIDE_TEACHER_EXPECTED"
        )
        assert session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        ) is None


def test_source_wide_label_without_source_row_is_an_explicit_retryable_failure() -> None:
    _prepare_config()
    teacher_id = "SOURCE-WIDE-ROW-MISSING"
    _teacher(teacher_id)
    assignment_ids = _assignments(
        teacher_id,
        codes=("G01",),
        completed={"G01"},
    )
    with session_scope(engine) as session:
        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        session.delete(source)

    result = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
        retry_jitter_ratio=0,
    ).run_once(max_events=1)

    assert result["failed"] == 1
    assert result["dead_lettered"] == 0
    assert _entry_scores(teacher_id) == {}
    with session_scope(engine) as session:
        outbox = session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.aggregate_id
                == assignment_ids["G01"]
            )
        )
        assert outbox is not None
        assert outbox.status == "PENDING"
        assert outbox.attempt_count == 1
        assert outbox.last_error == (
            "SettlementDataError:SOURCE_WIDE_TEACHER_SOURCE_NOT_FOUND"
        )


def test_completion_persists_task_points_into_current_total_score_fields() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-PROJECTION"
    _teacher(teacher_id, initial_total_score=72.8)
    completed = set(TASK_CODES) - {"G01", "G03"}
    _assignments(teacher_id, completed=completed)

    result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=20)

    assert result["failed"] == 0
    assert result["settled"] == 7
    assert result["projection_refreshes"] == 1
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        assert teacher.total_score == 97.8
        assert teacher.payload["raw_total_score"] == 97.8
        assert teacher.payload["external_display_score"] == 97.8
        assert teacher.payload["metric_inputs"][
            "mandatory_task_completed_count"
        ] == 7


def test_task_events_are_coalesced_without_rebuilding_lesson_scores() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-INCREMENTAL"
    _teacher(teacher_id, initial_total_score=72.8)
    _assignments(teacher_id, completed={"G01", "G08"})
    original_updated_at = NOW - timedelta(days=1)
    with session_scope(engine) as session:
        session.add(
            LessonSourceWideRecord(
                source_region="ovs",
                course_id="LESSON-KEEP",
                teacher_id=teacher_id,
                lesson_status="COMPLETED",
            )
        )
        session.add(
            LessonScoreResultRecord(
                lesson_source_region="ovs",
                lesson_id="LESSON-KEEP",
                reliability_score=4,
                user_feedback_score=0,
                class_quality_score=0,
                lesson_total_score=4,
                dimensions={"marker": "must-survive-task-settlement"},
                score_rule_version="lesson-policy-existing",
                projection_revision=41,
                calculated_at=original_updated_at,
            )
        )

    result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=10)

    assert result["settled"] == 2
    assert result["projection_refreshes"] == 1
    with session_scope(engine) as session:
        lesson_score = session.get(
            LessonScoreResultRecord,
            ("ovs", "LESSON-KEEP"),
        )
        assert lesson_score is not None
        assert lesson_score.projection_revision == 41
        assert lesson_score.score_rule_version == "lesson-policy-existing"
        assert lesson_score.dimensions == {
            "marker": "must-survive-task-settlement"
        }
        assert lesson_score.calculated_at.replace(tzinfo=timezone.utc) == (
            original_updated_at
        )
        components = list(
            session.scalars(
                select(ScoreComponentAccountRecord)
                .where(
                    ScoreComponentAccountRecord.teacher_id == teacher_id,
                    ScoreComponentAccountRecord.dimension
                    == "NEW_TEACHER_TASK",
                )
                .order_by(ScoreComponentAccountRecord.component_code)
            ).all()
        )
        assert [item.component_code for item in components] == list(
            TASK_CODES
        )
        assert {
            item.component_code: item.current_score
            for item in components
            if item.current_score
        } == {"G01": 3, "G08": 5}


@pytest.mark.parametrize(
    ("grant_gate", "expected_qualified"),
    [("true", True), ("false", False)],
)
def test_incremental_task_projection_respects_irreversible_qualification_gate(
    monkeypatch: pytest.MonkeyPatch,
    grant_gate: str,
    expected_qualified: bool,
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        grant_gate,
    )
    _prepare_config()
    teacher_id = f"REAL-SCORE-QUALIFICATION-{grant_gate.upper()}"
    _teacher(teacher_id, initial_total_score=72.8)
    provenance = {
        key: {
            "source_mode": "REAL",
            "source_field": key,
            "batch_id": f"BATCH-{teacher_id}",
        }
        for key in (
            "peak_slot_cnt",
            "capacity_score",
            "perfect_cnt",
            "peak_completed_cnt",
            "feedback_praise_cnt",
            "feedback_favorite_cnt",
            "total_completed_cnt",
            "late_cnt",
            "early_cnt",
            "absent_cnt",
            "l0_complaint_cnt",
        )
    }
    metric_inputs = {
        "peak_slot_cnt": 40,
        "capacity_milestone_achieved": True,
        "capacity_score": 10,
        "perfect_cnt": 5,
        "peak_completed_cnt": 2,
        "feedback_praise_cnt": 4,
        "feedback_favorite_cnt": 2,
        "total_completed_cnt": 10,
        "late_cnt": 0,
        "early_cnt": 0,
        "absent_cnt": 0,
        "l0_complaint_cnt": 0,
        "new_teacher_task_score": 0,
        "mandatory_task_assignment_count": 0,
        "mandatory_task_completed_count": 0,
        "mandatory_task_expected_count": 9,
    }
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        payload = dict(teacher.payload)
        payload.update(
            metric_inputs=metric_inputs,
            metric_provenance=provenance,
            raw_total_score=70,
            total_score=70,
            external_display_score=70,
            graduation_qualified=False,
            gold_qualified=False,
            dimensions=[
                {"code": "CAPACITY", "score": 10},
                {"code": "RELIABILITY", "score": 24},
                {"code": "USER_FEEDBACK", "score": 30},
                {"code": "CLASS_QUALITY", "score": 6},
                {"code": "NEW_TEACHER_TASK", "score": 0},
            ],
        )
        teacher.payload = payload
        teacher.total_score = 70
        session.add(
            ScoreAccountRecord(
                teacher_id=teacher_id,
                dimension="CLASS_QUALITY",
                current_score=6,
                score_rule_version="current-quality",
                version=1,
                updated_at=NOW,
                payload={"source_mode": "DERIVED_REAL"},
            )
        )
    _assignments(teacher_id, completed=set(TASK_CODES))

    result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=20)

    assert result["settled"] == 9
    assert result["projection_refreshes"] == 1
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        assert teacher.total_score == 100
        assert teacher.graduation_state == (
            "GRADUATED" if expected_qualified else "IN_CAMP"
        )
        assert teacher.payload["graduation_state"] == teacher.graduation_state
        assert teacher.payload["graduation_criteria_met"] is True
        assert teacher.payload["graduation_qualified"] is expected_qualified
        assert teacher.payload[
            "irreversible_qualification_grants_enabled"
        ] is expected_qualified
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert qualification is not None
        assert qualification.graduation_criteria_met is True
        assert qualification.graduation_qualified is expected_qualified


def test_source_wide_task_settlement_is_targeted_and_survives_source_refresh() -> None:
    _prepare_config()
    teacher_id = "SOURCE-WIDE-TASK-QUALIFICATION"
    with session_scope(engine) as session:
        session.add(
            TeacherSourceWideRecord(
                tchr_id=teacher_id,
                real_name="Source Wide Task Teacher",
                status="TEST-ACTIVE",
                job_days=1,
                total_completed_cnt=0,
                peak_completed_cnt=5,
                feedback_praise_cnt=11,
                feedback_favorite_cnt=4,
                peak_slot_cnt=0,
                late_cnt=0,
                early_cnt=0,
                absent_cnt=0,
            )
        )
        session.add(
            OutboxEventRecord(
                outbox_id=f"OUT-SOURCE-{teacher_id}-INSERT",
                event_id=f"EVT-SOURCE-{teacher_id}-INSERT",
                aggregate_type="TEACHER_SOURCE_WIDE",
                aggregate_id=teacher_id,
                event_type=SOURCE_WIDE_EVENT_TYPE,
                payload={
                    "source_table": "teacher_source_wide",
                    "source_id": teacher_id,
                    "operation": "INSERT",
                    "changed_fields": list(TEACHER_SOURCE_FIELDS),
                    "old_teacher_id": None,
                    "new_teacher_id": teacher_id,
                },
                status="PENDING",
                available_at=NOW,
                attempt_count=0,
                last_error=None,
                created_at=NOW,
                published_at=None,
            )
        )

    source_result = SourceWideWorker(engine).run_once(max_events=10)
    assert source_result["published"] == 1
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert teacher is not None and teacher.total_score == 65
        favorite_component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code
                == "FEEDBACK_FAVORITE",
            )
        )
        assert favorite_component is not None
        assert favorite_component.current_score == 0
        assert favorite_component.reconciliation_status == "SOURCE_MISSING"
        assert qualification is not None
        assert qualification.graduation_criteria_met is False
        assert qualification.graduation_qualified is False

    forbidden_task_path_reads: list[str] = []

    def capture_forbidden_reads(orm_execute_state) -> None:
        if not orm_execute_state.is_select:
            return
        sql = str(
            orm_execute_state.statement.compile(
                dialect=postgresql.dialect(),
            )
        ).lower()
        if any(
            table in sql
            for table in (
                "lesson_source_wide",
                "lesson_score_results",
                "lesson_facts",
                "teacher_metric_snapshots",
            )
        ):
            forbidden_task_path_reads.append(sql)

    worker = SharedTaskScoreSettlementWorker(engine)

    def settle_without_lesson_scan(*, max_events: int = 20) -> dict:
        event.listen(Session, "do_orm_execute", capture_forbidden_reads)
        try:
            return worker.run_once(max_events=max_events)
        finally:
            event.remove(
                Session,
                "do_orm_execute",
                capture_forbidden_reads,
            )

    _complete_existing_assignments(teacher_id, {"G01"}, suffix="FIRST")
    first = settle_without_lesson_scan()
    assert first["settled"] == 1
    with session_scope(engine) as session:
        assert session.get(TeacherRecord, teacher_id).total_score == 68

        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        source.feedback_praise_cnt = 12
        session.add(
            OutboxEventRecord(
                outbox_id=f"OUT-SOURCE-{teacher_id}-PRAISE",
                event_id=f"EVT-SOURCE-{teacher_id}-PRAISE",
                aggregate_type="TEACHER_SOURCE_WIDE",
                aggregate_id=teacher_id,
                event_type=SOURCE_WIDE_EVENT_TYPE,
                payload={
                    "source_table": "teacher_source_wide",
                    "source_id": teacher_id,
                    "operation": "UPDATE",
                    "changed_fields": ["feedback_praise_cnt"],
                    "old_teacher_id": teacher_id,
                    "new_teacher_id": teacher_id,
                },
                status="PENDING",
                available_at=NOW,
                attempt_count=0,
                last_error=None,
                created_at=NOW,
                published_at=None,
            )
        )

    assert SourceWideWorker(engine).run_once(max_events=10)["published"] == 1
    with session_scope(engine) as session:
        assert session.get(TeacherRecord, teacher_id).total_score == 73

    _complete_existing_assignments(teacher_id, {"G02"}, suffix="SECOND")
    second = settle_without_lesson_scan()
    assert second["settled"] == 1
    with session_scope(engine) as session:
        assert session.get(TeacherRecord, teacher_id).total_score == 75
        task_account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        assert task_account is not None and task_account.current_score == 5

    remaining = set(TASK_CODES) - {"G01", "G02"}
    _complete_existing_assignments(teacher_id, remaining, suffix="REMAINING")
    final = settle_without_lesson_scan()
    assert final["settled"] == len(remaining)
    assert forbidden_task_path_reads == []

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        task_account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        task_components = list(
            session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == teacher_id,
                    ScoreComponentAccountRecord.dimension
                    == "NEW_TEACHER_TASK",
                )
            ).all()
        )
        assert teacher is not None and teacher.total_score == 100
        assert teacher.graduation_state == "GRADUATED"
        assert qualification is not None
        assert qualification.graduation_criteria_met is True
        assert qualification.graduation_qualified is True
        assert qualification.graduation_score_locked == 100
        assert qualification.gate_results[
            "mandatory_task_completed_count"
        ] == 9
        assert task_account is not None and task_account.current_score == 30
        assert task_account.payload["settlement_contract"] == (
            "shared-fixed-growth.v1"
        )
        assert len(task_components) == 9
        assert sum(item.current_score for item in task_components) == 30
        qualification_revision = qualification.revision
        component_revisions = {
            item.component_code: item.projection_revision
            for item in task_components
        }
        account_version = task_account.version
        teacher_updated_at = teacher.updated_at
        g01 = next(item for item in task_components if item.component_code == "G01")
        g01_assignment_id = str(g01.payload["assignment_id"])

    with session_scope(engine) as session:
        _add_event(
            session,
            assignment_id=g01_assignment_id,
            teacher_id=teacher_id,
            task_code="G01",
            source_mode="REAL",
            suffix="DUPLICATE-AFTER-FINAL",
        )
    duplicate = settle_without_lesson_scan()
    assert duplicate["settled"] == 1
    assert duplicate["score_entries_created"] == 0
    assert forbidden_task_path_reads == []
    with session_scope(engine) as session:
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        task_account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        teacher = session.get(TeacherRecord, teacher_id)
        assert qualification is not None
        assert qualification.revision == qualification_revision
        assert task_account is not None
        assert task_account.version == account_version
        assert teacher is not None and teacher.updated_at == teacher_updated_at
        assert {
            item.component_code: item.projection_revision
            for item in session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == teacher_id,
                    ScoreComponentAccountRecord.dimension
                    == "NEW_TEACHER_TASK",
                )
            ).all()
        } == component_revisions


def test_mock_and_non_completed_events_are_consumed_without_score() -> None:
    _prepare_config()
    mock_teacher = "MOCK-SCORE-SKIP"
    _teacher(mock_teacher)
    _assignments(
        mock_teacher,
        codes=("G01",),
        completed={"G01"},
        source_mode="MOCK",
    )

    real_teacher = "REAL-SCORE-NONCOMPLETE"
    _teacher(real_teacher)
    assignment_ids = _assignments(real_teacher, codes=("G01",))
    with session_scope(engine) as session:
        _add_event(
            session,
            assignment_id=assignment_ids["G01"],
            teacher_id=real_teacher,
            task_code="G01",
            source_mode="REAL",
            suffix="viewed",
            to_status="VIEWED",
        )

    result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=10)
    assert result["failed"] == 0
    assert result["skipped_non_real"] == 1
    assert result["skipped_non_completed"] == 1
    assert _entry_scores(mock_teacher) == {}
    assert _entry_scores(real_teacher) == {}
    with session_scope(engine) as session:
        statuses = set(
            session.scalars(
                select(OutboxEventRecord.status).where(
                    OutboxEventRecord.aggregate_id.in_(
                        [
                            f"ASSIGN-{mock_teacher}-G01",
                            f"ASSIGN-{real_teacher}-G01",
                        ]
                    )
                )
            ).all()
        )
        assert statuses == {"PUBLISHED"}


def test_real_completion_settles_each_completed_assignment_without_waiting_for_all_tasks() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-WAIT"
    _teacher(teacher_id, untrusted_score=19)
    _assignments(teacher_id, codes=("G01",), completed={"G01"})

    result = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
    ).run_once(max_events=1)
    assert result["settled"] == 1
    assert _entry_scores(teacher_id) == {"G01": 3}
    with session_scope(engine) as session:
        account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        assert account is not None
        assert account.current_score == 3
        assert account.payload["source_mode"] == "SYSTEM_TASK_STATUS"
        event = session.scalar(
            select(OutboxEventRecord).where(
                OutboxEventRecord.aggregate_id == f"ASSIGN-{teacher_id}-G01"
            )
        )
        assert event is not None
        assert event.status == "PUBLISHED"
        assert event.last_error is None
        assert session.scalar(
            select(func.count()).select_from(AuditEventRecord).where(
                AuditEventRecord.teacher_id == teacher_id,
                AuditEventRecord.event_type == "score.account.cutover.shared_tasks.v1",
            )
        ) == 1


def test_two_workers_cannot_duplicate_fixed_task_awards() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-CONCURRENT"
    _teacher(teacher_id)
    _assignments(teacher_id, completed={"G01", "G08"})

    def run_worker() -> dict:
        return SharedTaskScoreSettlementWorker(
            engine,
            retry_delay=timedelta(0),
        ).run_once(max_events=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: run_worker(), range(2)))
    # SQLite serializes writes globally while PostgreSQL uses the explicit
    # teacher/account locks.  A transient loser stays PENDING and is retried.
    retry = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
    ).run_once(max_events=10)
    assert first["claimed"] + second["claimed"] + retry["claimed"] >= 2
    assert _entry_scores(teacher_id) == {"G01": 3, "G08": 5}
    with session_scope(engine) as session:
        account = session.get(
            ScoreAccountRecord,
            (teacher_id, "NEW_TEACHER_TASK"),
        )
        assert account is not None and account.current_score == 8
        assert session.scalar(
            select(func.count()).select_from(ScoreEntryRecord).where(
                ScoreEntryRecord.teacher_id == teacher_id,
                ScoreEntryRecord.entry_type == ENTRY_TYPE,
            )
        ) == 2


def test_poison_event_is_not_hot_looped_and_moves_to_dead_letter() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-DEAD-LETTER"
    _teacher(teacher_id)
    assignment_ids = _assignments(teacher_id, codes=("G01",))
    with session_scope(engine) as session:
        event_id = _add_event(
            session,
            assignment_id=assignment_ids["G01"],
            teacher_id=teacher_id,
            task_code="G01",
            source_mode="REAL",
            suffix="invalid-status",
            to_status="NOT_A_LEGAL_STATUS",
        )

    worker = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
        max_retry_delay=timedelta(0),
        max_attempts=2,
        retry_jitter_ratio=0,
    )
    first = worker.run_once(max_events=10)
    assert first["failed"] == 1
    assert first["dead_lettered"] == 0
    with session_scope(engine) as session:
        event = session.get(OutboxEventRecord, event_id)
        assert event is not None
        assert event.status == "PENDING"
        assert event.attempt_count == 1

    second = worker.run_once(max_events=10)
    assert second["failed"] == 1
    assert second["dead_lettered"] == 1
    with session_scope(engine) as session:
        event = session.get(OutboxEventRecord, event_id)
        assert event is not None
        assert event.status == "DEAD_LETTER"
        assert event.attempt_count == 2
        assert event.published_at is None
        assert event.last_error is not None

    assert worker.run_once(max_events=10)["claimed"] == 0


def test_same_teacher_events_batch_assignment_reads_without_n_plus_one() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-BATCH-LOCKS"
    _teacher(teacher_id)
    _assignments(teacher_id, completed=set(TASK_CODES))
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = SharedTaskScoreSettlementWorker(engine).run_once(
            max_events=len(TASK_CODES)
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert result["settled"] == len(TASK_CODES)
    assignment_id_batch_reads = [
        statement
        for statement in statements
        if "FROM task_assignments" in statement
        and "task_assignments.assignment_id IN" in statement
    ]
    assert len(assignment_id_batch_reads) == 1


def test_systemic_group_failure_records_each_event_once(
    monkeypatch,
) -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-GROUP-FAILURE"
    _teacher(teacher_id)
    _assignments(teacher_id, completed=set(TASK_CODES))
    worker = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(minutes=5),
        retry_jitter_ratio=0,
    )

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("systemic projection failure")

    monkeypatch.setattr(
        worker,
        "_settle_eligible_assignment",
        fail_projection,
    )
    result = worker.run_once(max_events=len(TASK_CODES))

    assert result["failed"] == len(TASK_CODES)
    assert result["claimed"] == len(TASK_CODES)
    with session_scope(engine) as session:
        failed_events = session.scalars(
            select(OutboxEventRecord).where(
                OutboxEventRecord.aggregate_type == "TASK_ASSIGNMENT",
                OutboxEventRecord.aggregate_id.like(
                    f"ASSIGN-{teacher_id}-%"
                ),
            )
        ).all()
        assert len(failed_events) == len(TASK_CODES)
        assert {item.attempt_count for item in failed_events} == {1}
        assert {item.status for item in failed_events} == {"PENDING"}
        assert {item.last_error for item in failed_events} == {
            "RuntimeError"
        }
