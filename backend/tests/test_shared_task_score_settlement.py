from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib

from sqlalchemy import func, select

from app.config_service import seed_default_configs
from app.database import engine, session_scope
from app.db_models import (
    AuditEventRecord,
    DataImportBatchRecord,
    OutboxEventRecord,
    ScoreAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherMetricSnapshotRecord,
    TeacherRecord,
)
from app.shared_task_score_settlement import (
    ENTRY_TYPE,
    SharedTaskScoreSettlementWorker,
)


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
TASK_CODES = tuple(f"G{number:02d}" for number in range(1, 11))


def _teacher(
    teacher_id: str,
    *,
    untrusted_score: float | None = None,
    with_score_snapshot: bool = False,
) -> None:
    with session_scope(engine) as session:
        batch_id = f"BATCH-{teacher_id}" if with_score_snapshot else None
        if batch_id is not None:
            session.add(
                DataImportBatchRecord(
                    batch_id=batch_id,
                    source_kind="TEACHER_SNAPSHOT",
                    sync_mode="MANUAL_BASELINE",
                    source_system="TEST",
                    source_filename=f"{teacher_id}.xlsx",
                    source_uri=f"test://{teacher_id}",
                    source_sha256=hashlib.sha256(
                        teacher_id.encode("utf-8")
                    ).hexdigest(),
                    source_sheet="TEST",
                    snapshot_label="TEST",
                    data_mode="MIXED",
                    column_count=1,
                    row_count=1,
                    header=["teacher_id"],
                    status="COMPLETED",
                    imported_at=NOW,
                    payload={},
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        metric_inputs = {
            "capacity_score": 10,
            "new_teacher_task_score": 0,
            "mandatory_task_assignment_count": 0,
            "mandatory_task_completed_count": 0,
            "mandatory_task_expected_count": 10,
        }
        session.add(
            TeacherRecord(
                teacher_id=teacher_id,
                camp_enrollment_id=f"CAMP-{teacher_id}",
                name=f"Teacher {teacher_id}",
                country="PH",
                timezone="Asia/Manila",
                camp_day=5,
                graduation_state="IN_PROGRESS",
                total_score=72.8 if with_score_snapshot else 0,
                graduation_threshold=100,
                data_mode="MIXED" if with_score_snapshot else "REAL",
                source_batch_id=batch_id,
                source_snapshot_label="TEST",
                payload={
                    "teacher_id": teacher_id,
                    "data_mode": "MIXED" if with_score_snapshot else "REAL",
                    "metric_inputs": metric_inputs,
                    "metric_provenance": {},
                    "dimensions": [],
                    "raw_total_score": 72.8 if with_score_snapshot else 0,
                    "total_score": 72.8 if with_score_snapshot else 0,
                    "external_display_score": 72.8 if with_score_snapshot else 0,
                },
                created_at=NOW,
                updated_at=NOW,
            )
        )
        if batch_id is not None:
            session.add(
                TeacherMetricSnapshotRecord(
                    snapshot_id=f"{batch_id}:{teacher_id}",
                    batch_id=batch_id,
                    teacher_id=teacher_id,
                    snapshot_label="TEST",
                    source_row_number=2,
                    data_mode="MIXED",
                    score_rule_version="new_teacher_30d_20260723_v6",
                    score_policy_snapshot={},
                    score_policy_sha256="0" * 64,
                    real_name=f"Teacher {teacher_id}",
                    employment_status="on",
                    bu=None,
                    based_type=None,
                    teach_area_type=None,
                    onboard_date=None,
                    onboard_30d_end_date=None,
                    first_booked_date=None,
                    is_cpl_tesol=None,
                    is_self_introduce=None,
                    lessons_completed=0,
                    total_completed_cnt=0,
                    peak_completed_cnt=0,
                    peak_slot_cnt=40,
                    perfect_cnt=8,
                    on_time_completed_cnt=0,
                    feedback_praise_cnt=0,
                    feedback_favorite_cnt=0,
                    completed_again_student_15d_cnt=0,
                    late_cnt=0,
                    early_cnt=0,
                    real_absent_cnt=0,
                    severe_redline_event=False,
                    capacity_score=10,
                    new_teacher_task_score=0,
                    class_quality_no_issue_rate=0,
                    reliability_score=24,
                    user_feedback_score=26,
                    class_quality_score=12.8,
                    raw_total_score=72.8,
                    public_total_score=72.8,
                    metric_inputs=metric_inputs,
                    metric_provenance={},
                    raw_payload={},
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        if untrusted_score is not None:
            session.add(
                ScoreAccountRecord(
                    account_id=f"{teacher_id}:NEW_TEACHER_TASK",
                    teacher_id=teacher_id,
                    camp_enrollment_id=f"CAMP-{teacher_id}",
                    dimension="NEW_TEACHER_TASK",
                    current_score=untrusted_score,
                    minimum_score=0,
                    weight=0,
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
    seed_default_configs()


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


def test_g01_g09_and_all_ten_settle_to_4_10_and_30_with_explicit_cutover() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-ALL"
    _teacher(teacher_id, untrusted_score=17)
    assignment_ids = _assignments(
        teacher_id,
        completed={"G01", "G09"},
    )

    first = SharedTaskScoreSettlementWorker(engine).run_once(max_events=10)
    assert first["failed"] == 0
    assert first["settled"] == 2
    assert first["score_entries_created"] == 2
    assert _entry_scores(teacher_id) == {"G01": 4, "G09": 10}

    with session_scope(engine) as session:
        account = session.get(ScoreAccountRecord, f"{teacher_id}:NEW_TEACHER_TASK")
        assert account is not None
        assert account.current_score == 14
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
        assert audits[0].payload["ledger_score_at_cutover"] == 14

        for code in set(TASK_CODES) - {"G01", "G09"}:
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
    assert len(_entry_scores(teacher_id)) == 10
    assert sum(_entry_scores(teacher_id).values()) == 30
    with session_scope(engine) as session:
        account = session.get(ScoreAccountRecord, f"{teacher_id}:NEW_TEACHER_TASK")
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
    assert _entry_scores(teacher_id) == {"G01": 4}


def test_completion_persists_task_points_into_current_total_score_fields() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-PROJECTION"
    _teacher(teacher_id, with_score_snapshot=True)
    completed = set(TASK_CODES) - {"G01", "G04"}
    _assignments(teacher_id, completed=completed)

    result = SharedTaskScoreSettlementWorker(engine).run_once(max_events=20)

    assert result["failed"] == 0
    assert result["settled"] == 8
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        snapshot = session.get(
            TeacherMetricSnapshotRecord,
            f"BATCH-{teacher_id}:{teacher_id}",
        )
        assert teacher is not None
        assert snapshot is not None
        assert snapshot.new_teacher_task_score == 25
        assert snapshot.raw_total_score == 97.8
        assert snapshot.public_total_score == 97.8
        assert teacher.total_score == 97.8
        assert teacher.payload["raw_total_score"] == 97.8
        assert teacher.payload["external_display_score"] == 97.8
        assert teacher.payload["metric_inputs"][
            "mandatory_task_completed_count"
        ] == 8


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


def test_real_completion_settles_each_completed_assignment_without_waiting_for_all_ten() -> None:
    _prepare_config()
    teacher_id = "REAL-SCORE-WAIT"
    _teacher(teacher_id, untrusted_score=19)
    _assignments(teacher_id, codes=("G01",), completed={"G01"})

    result = SharedTaskScoreSettlementWorker(
        engine,
        retry_delay=timedelta(0),
    ).run_once(max_events=1)
    assert result["settled"] == 1
    assert _entry_scores(teacher_id) == {"G01": 4}
    with session_scope(engine) as session:
        account = session.get(ScoreAccountRecord, f"{teacher_id}:NEW_TEACHER_TASK")
        assert account is not None
        assert account.current_score == 4
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
    _assignments(teacher_id, completed={"G01", "G09"})

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
    assert _entry_scores(teacher_id) == {"G01": 4, "G09": 10}
    with session_scope(engine) as session:
        account = session.get(ScoreAccountRecord, f"{teacher_id}:NEW_TEACHER_TASK")
        assert account is not None and account.current_score == 14
        assert session.scalar(
            select(func.count()).select_from(ScoreEntryRecord).where(
                ScoreEntryRecord.teacher_id == teacher_id,
                ScoreEntryRecord.entry_type == ENTRY_TYPE,
            )
        ) == 2
