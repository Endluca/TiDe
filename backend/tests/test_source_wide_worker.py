from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    ConfigKey,
    ConfigPublicationAuditRecord,
    ConfigVersionRecord,
)
from app.config_service import ConfigService
from app.database import SessionLocal, engine, session_scope
from app.db_models import (
    LessonScoreResultRecord,
    LessonSourceWideRecord,
    NotificationEventRecord,
    NotificationRecord,
    OpsCaseRecord,
    OpsDecisionRecord,
    OutboxEventRecord,
    PersonalizedTriggerMatchRecord,
    ScoreAccountRecord,
    ScoreComponentAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TeacherQualificationRecord,
    TeacherRecord,
    TeacherSourceWideRecord,
)
from app.source_contracts import LESSON_SOURCE_FIELDS, TEACHER_SOURCE_FIELDS
from app.source_test_seed import seed_source_test_data
from app.source_wide_worker import (
    CAPACITY_MILESTONE_REASON_CODE,
    EVENT_TYPE,
    SourceWideWorker,
)
from app.task_catalog import MANDATORY_TASK_CODES


_NOW = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
_TEST_TEACHER_IDS = {
    # Generic app fixtures are legacy-only and would correctly fail the
    # source-wide policy publication exercised in this module.  Remove them
    # as unrelated state so every projection target here has one contract.
    "T-1001",
    "T-1002",
    "T-1003",
    "T-1004",
    "TEST-SOURCE-TEACHER-001",
    "TEST-SOURCE-TEACHER-002",
    "SW-BASELINE",
    "SW-PROFILE",
    "SW-CAPACITY",
    "SW-QUALIFIED",
    "SW-FAILURE",
    "SW-G01-STATUS",
    "SW-POLICY-PUBLISH",
    "SW-POLICY-GRANT-GATED",
}


def _clean_worker_records() -> None:
    with session_scope(engine) as session:
        session.execute(delete(ConfigPublicationAuditRecord))
        session.execute(delete(ConfigVersionRecord))
        notification_ids = list(
            session.scalars(
                select(NotificationRecord.notification_id).where(
                    NotificationRecord.teacher_id.in_(_TEST_TEACHER_IDS)
                )
            ).all()
        )
        case_ids = list(
            session.scalars(
                select(OpsCaseRecord.case_id).where(
                    OpsCaseRecord.teacher_id.in_(_TEST_TEACHER_IDS)
                )
            ).all()
        )
        if notification_ids:
            session.execute(
                delete(NotificationEventRecord).where(
                    NotificationEventRecord.notification_id.in_(notification_ids)
                )
            )
        if case_ids:
            session.execute(
                delete(OpsDecisionRecord).where(
                    OpsDecisionRecord.case_id.in_(case_ids)
                )
            )
        for model in (
            PersonalizedTriggerMatchRecord,
            NotificationRecord,
            OpsCaseRecord,
            ScoreEntryRecord,
            ScoreComponentAccountRecord,
            ScoreAccountRecord,
            TeacherQualificationRecord,
            TaskAssignmentRecord,
        ):
            session.execute(
                delete(model).where(model.teacher_id.in_(_TEST_TEACHER_IDS))
            )
        session.execute(delete(LessonScoreResultRecord))
        session.execute(
            delete(OutboxEventRecord).where(
                OutboxEventRecord.event_type == EVENT_TYPE
            )
        )
        session.execute(delete(LessonSourceWideRecord))
        session.execute(delete(TeacherSourceWideRecord))
        session.execute(
            delete(TeacherRecord).where(
                TeacherRecord.teacher_id.in_(_TEST_TEACHER_IDS)
            )
        )


@pytest.fixture(autouse=True)
def _isolated_source_worker_records(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "true",
    )
    _clean_worker_records()
    yield
    _clean_worker_records()


def _teacher_source(
    teacher_id: str,
    *,
    name: str = "Source Worker Teacher",
    feedback_praise_cnt: int | None = 0,
    feedback_favorite_cnt: int | None = 0,
    peak_completed_cnt: int | None = 0,
    peak_slot_cnt: int | None = 0,
    late_cnt: int | None = 0,
    early_cnt: int | None = 0,
    absent_cnt: int | None = 0,
    is_cpl_tesol: bool | None = None,
    is_self_introduce: bool | None = None,
) -> TeacherSourceWideRecord:
    return TeacherSourceWideRecord(
        tchr_id=teacher_id,
        real_name=name,
        status="TEST-ACTIVE",
        job_days=1,
        total_completed_cnt=0,
        peak_completed_cnt=peak_completed_cnt,
        feedback_praise_cnt=feedback_praise_cnt,
        feedback_favorite_cnt=feedback_favorite_cnt,
        peak_slot_cnt=peak_slot_cnt,
        late_cnt=late_cnt,
        early_cnt=early_cnt,
        absent_cnt=absent_cnt,
        is_cpl_tesol=is_cpl_tesol,
        is_self_introduce=is_self_introduce,
    )


def _source_event(
    session,
    *,
    token: str,
    source_table: str,
    source_id: str,
    operation: str,
    changed_fields: list[str] | tuple[str, ...],
    old_teacher_id: str | None,
    new_teacher_id: str | None,
    offset_seconds: int = 0,
) -> OutboxEventRecord:
    aggregate_type = (
        "TEACHER_SOURCE_WIDE"
        if source_table == "teacher_source_wide"
        else "LESSON_SOURCE_WIDE"
    )
    occurred_at = _NOW + timedelta(seconds=offset_seconds)
    event = OutboxEventRecord(
        outbox_id=f"OUT-SW-{token}",
        event_id=f"EVT-SW-{token}",
        aggregate_type=aggregate_type,
        aggregate_id=source_id,
        event_type=EVENT_TYPE,
        payload={
            "source_table": source_table,
            "source_id": source_id,
            "operation": operation,
            "changed_fields": list(changed_fields),
            "old_teacher_id": old_teacher_id,
            "new_teacher_id": new_teacher_id,
        },
        status="PENDING",
        available_at=occurred_at,
        attempt_count=0,
        last_error=None,
        created_at=occurred_at,
        published_at=None,
    )
    session.add(event)
    return event


def _add_teacher_insert_event(
    session,
    teacher_id: str,
    *,
    token: str,
    offset_seconds: int = 0,
) -> None:
    _source_event(
        session,
        token=token,
        source_table="teacher_source_wide",
        source_id=teacher_id,
        operation="INSERT",
        changed_fields=TEACHER_SOURCE_FIELDS,
        old_teacher_id=None,
        new_teacher_id=teacher_id,
        offset_seconds=offset_seconds,
    )


def _fixed_assignments(session, teacher_id: str) -> list[TaskAssignmentRecord]:
    return list(
        session.scalars(
            select(TaskAssignmentRecord)
            .where(
                TaskAssignmentRecord.teacher_id == teacher_id,
                TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
            )
            .order_by(TaskAssignmentRecord.task_code)
        ).all()
    )


def test_teacher_insert_initializes_exactly_nine_assigned_fixed_tasks() -> None:
    teacher_id = "SW-BASELINE"
    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id))
        _add_teacher_insert_event(session, teacher_id, token="BASELINE")

    result = SourceWideWorker(engine).run_once(max_events=10)

    assert result["claimed"] == result["published"] == 1
    assert result["failed"] == 0
    assert result["teachers_created"] == 1
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assignments = _fixed_assignments(session, teacher_id)
        assert teacher is not None
        assert teacher.name == "Source Worker Teacher"
        assert len(assignments) == len(MANDATORY_TASK_CODES) == 9
        assert [item.task_code for item in assignments] == list(
            MANDATORY_TASK_CODES
        )
        assert {item.status for item in assignments} == {"ASSIGNED"}
        assert session.scalar(
            select(func.count()).select_from(NotificationRecord).where(
                NotificationRecord.teacher_id == teacher_id
            )
        ) == 0


def test_six_seed_events_build_current_scores_without_legacy_projections() -> None:
    seed_source_test_data(engine, apply=True)
    with session_scope(engine) as session:
        for offset, teacher_id in enumerate(
            ("TEST-SOURCE-TEACHER-001", "TEST-SOURCE-TEACHER-002")
        ):
            _add_teacher_insert_event(
                session,
                teacher_id,
                token=f"SEED-T-{offset + 1}",
                offset_seconds=offset,
            )
        lessons = list(
            session.scalars(
                select(LessonSourceWideRecord).order_by(
                    LessonSourceWideRecord.course_id
                )
            ).all()
        )
        for offset, lesson in enumerate(lessons, start=2):
            _source_event(
                session,
                token=f"SEED-L-{offset - 1}",
                source_table="lesson_source_wide",
                source_id=lesson.course_id,
                operation="INSERT",
                changed_fields=LESSON_SOURCE_FIELDS,
                old_teacher_id=None,
                new_teacher_id=lesson.teacher_id,
                offset_seconds=offset,
            )

    result = SourceWideWorker(engine).run_once(max_events=10)

    assert result["claimed"] == result["published"] == 6
    assert result["failed"] == 0
    assert result["teachers_created"] == 2
    assert result["teacher_refreshes"] == 2
    with session_scope(engine) as session:
        lesson_results = {
            item.lesson_id: item
            for item in session.scalars(
                select(LessonScoreResultRecord).order_by(
                    LessonScoreResultRecord.lesson_id
                )
            ).all()
        }
        assert set(lesson_results) == {
            "TEST-SOURCE-LESSON-001",
            "TEST-SOURCE-LESSON-002",
            "TEST-SOURCE-LESSON-003",
            "TEST-SOURCE-LESSON-004",
        }
        assert {
            lesson_id: item.lesson_total_score
            for lesson_id, item in lesson_results.items()
        } == {
            "TEST-SOURCE-LESSON-001": 18,
            "TEST-SOURCE-LESSON-002": 2,
            "TEST-SOURCE-LESSON-003": 6,
            "TEST-SOURCE-LESSON-004": 4,
        }
        assert {
            teacher_id: session.get(TeacherRecord, teacher_id).total_score
            for teacher_id in (
                "TEST-SOURCE-TEACHER-001",
                "TEST-SOURCE-TEACHER-002",
            )
        } == {
            "TEST-SOURCE-TEACHER-001": 30,
            "TEST-SOURCE-TEACHER-002": 10,
        }
        assert all(item.projection_revision == 1 for item in lesson_results.values())


def test_name_only_update_does_not_recalculate_score_projections() -> None:
    teacher_id = "SW-PROFILE"
    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id, name="Before Name"))
        _add_teacher_insert_event(session, teacher_id, token="PROFILE-INSERT")
    SourceWideWorker(engine).run_once(max_events=10)

    with session_scope(engine) as session:
        component_revisions = {
            item.component_code: item.projection_revision
            for item in session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == teacher_id
                )
            ).all()
        }
        account_versions = {
            item.dimension: item.version
            for item in session.scalars(
                select(ScoreAccountRecord).where(
                    ScoreAccountRecord.teacher_id == teacher_id
                )
            ).all()
        }
        total_before = session.get(TeacherRecord, teacher_id).total_score
        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        source.real_name = "After Name"
        _source_event(
            session,
            token="PROFILE-NAME",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["real_name"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )

    result = SourceWideWorker(engine).run_once(max_events=10)

    assert result["published"] == 1
    assert result["teacher_refreshes"] == 0
    assert result["lesson_result_changes"] == 0
    assert result["component_changes"] == 0
    assert result["account_changes"] == 0
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        assert teacher.name == "After Name"
        assert teacher.total_score == total_before
        assert {
            item.component_code: item.projection_revision
            for item in session.scalars(
                select(ScoreComponentAccountRecord).where(
                    ScoreComponentAccountRecord.teacher_id == teacher_id
                )
            ).all()
        } == component_revisions
        assert {
            item.dimension: item.version
            for item in session.scalars(
                select(ScoreAccountRecord).where(
                    ScoreAccountRecord.teacher_id == teacher_id
                )
            ).all()
        } == account_versions


def test_g01_status_update_preserves_three_states_without_score_recalculation() -> None:
    teacher_id = "SW-G01-STATUS"
    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id))
        _add_teacher_insert_event(session, teacher_id, token="G01-INSERT")
    SourceWideWorker(engine).run_once(max_events=10)

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        assert teacher.payload["is_cpl_tesol"] is None
        assert teacher.payload["is_self_introduce"] is None
        total_before = teacher.total_score

        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        source.is_cpl_tesol = False
        source.is_self_introduce = True
        _source_event(
            session,
            token="G01-STATUS",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["is_cpl_tesol", "is_self_introduce"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )

    result = SourceWideWorker(engine).run_once(max_events=10)

    assert result["published"] == 1
    assert result["teacher_refreshes"] == 0
    assert result["lesson_result_changes"] == 0
    assert result["component_changes"] == 0
    assert result["account_changes"] == 0
    assert result["qualification_changes"] == 0
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        assert teacher is not None
        assert teacher.payload["is_cpl_tesol"] is False
        assert teacher.payload["is_self_introduce"] is True
        assert teacher.total_score == total_before


def test_score_policy_publish_recalculates_source_wide_teacher_in_same_transaction() -> None:
    teacher_id = "SW-POLICY-PUBLISH"
    config_service = ConfigService(SessionLocal)
    first = config_service.create_draft(
        ConfigKey.SCORE_GRADUATION,
        actor_id="policy-creator-1",
        payload=deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]),
    )
    assert config_service.validate_version(
        first["version_id"], actor_id="policy-validator-1"
    )["valid"]
    config_service.publish_version(
        first["version_id"], actor_id="policy-publisher-1"
    )

    with session_scope(engine) as session:
        session.add(
            _teacher_source(
                teacher_id,
                feedback_praise_cnt=1,
            )
        )
        session.add(
            LessonSourceWideRecord(
                course_id="SW-POLICY-LESSON",
                lesson_date=_NOW.date(),
                lesson_time=_NOW.time().replace(tzinfo=None),
                teacher_id=teacher_id,
                student_id="SW-POLICY-STUDENT",
                lesson_status="end",
                is_peak=False,
                is_late=False,
                is_early=False,
                is_favorited=False,
                has_positive_feedback_tag=True,
                is_camera_off=False,
                is_cpu_usage_high=False,
                is_network_delay_high=False,
            )
        )
        _add_teacher_insert_event(
            session,
            teacher_id,
            token="POLICY-TEACHER",
        )
        _source_event(
            session,
            token="POLICY-LESSON",
            source_table="lesson_source_wide",
            source_id="SW-POLICY-LESSON",
            operation="INSERT",
            changed_fields=LESSON_SOURCE_FIELDS,
            old_teacher_id=None,
            new_teacher_id=teacher_id,
            offset_seconds=1,
        )
    assert SourceWideWorker(engine).run_once(max_events=10)["failed"] == 0

    with session_scope(engine) as session:
        task_account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        assert task_account is not None
        task_account.score_rule_version = "fixed-task:v1:ledger"
        task_account.payload = {
            "source_mode": "SYSTEM_TASK_STATUS",
            "settlement_contract": "shared-fixed-growth.v1",
            "ledger_entry_type": "FIXED_TASK_AWARD",
            "ledger_score": 0,
        }
        task_component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code == "G01",
            )
        )
        assert task_component is not None
        task_component.score_rule_version = "fixed-task:v1:ledger"
        task_component.payload = {
            **task_component.payload,
            "assignment_id": "ASG-SW-POLICY-G01",
            "projection_id": "SPR-TASK-SETTLEMENT",
            "projection_trigger": {
                "type": "TASK_STATUS_UPDATED",
                "ref": "ASG-SW-POLICY-G01",
            },
            "attribution_contract": "task-status-ledger-is-authoritative",
        }
        _source_event(
            session,
            token="POLICY-SOURCE-REFRESH",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["feedback_praise_cnt"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )
    assert SourceWideWorker(engine).run_once(max_events=10)["failed"] == 0
    with session_scope(engine) as session:
        task_account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        task_component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code == "G01",
            )
        )
        assert task_account is not None
        assert task_account.score_rule_version == "fixed-task:v1:ledger"
        assert task_account.payload["settlement_contract"] == "shared-fixed-growth.v1"
        assert task_component is not None
        assert task_component.score_rule_version == "fixed-task:v1:ledger"
        assert task_component.payload["assignment_id"] == "ASG-SW-POLICY-G01"
        assert task_component.payload["projection_id"] == "SPR-TASK-SETTLEMENT"

    replacement = config_service.create_draft(
        ConfigKey.SCORE_GRADUATION,
        actor_id="policy-creator-2",
        from_version_id=first["version_id"],
    )
    replacement_payload = deepcopy(replacement["payload"])
    replacement_payload["scoring_items"]["feedback_praise"][
        "points_per_unit"
    ] = 7
    config_service.update_draft(
        replacement["version_id"],
        replacement_payload,
        actor_id="policy-creator-2",
    )
    assert config_service.validate_version(
        replacement["version_id"], actor_id="policy-validator-2"
    )["valid"]
    published = config_service.publish_version(
        replacement["version_id"], actor_id="policy-publisher-2"
    )

    assert published["recalculation"]["teacher_count"] >= 1
    assert published["recalculation"]["source_wide_recalculation"][
        "teacher_count"
    ] == 1
    assert published["recalculation"]["score_rule_version"] == (
        replacement_payload["policy_version"]
    )
    source_versions = published["recalculation"]["source_versions"]
    assert source_versions["teacher_batch_ids"] == []
    assert source_versions["lesson_batch_ids"] == []
    assert source_versions["score_config_version_id"] == replacement[
        "version_id"
    ]
    assert len(source_versions["score_policy_sha256"]) == 64
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        lesson_result = session.get(
            LessonScoreResultRecord,
            "SW-POLICY-LESSON",
        )
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert teacher is not None and teacher.total_score == 13
        assert lesson_result is not None
        assert lesson_result.lesson_total_score == 13
        assert lesson_result.score_rule_version == replacement_payload["policy_version"]
        assert qualification is not None
        assert qualification.score_rule_version == replacement_payload["policy_version"]
        task_account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        assert task_account is not None
        assert task_account.score_rule_version == replacement_payload["policy_version"]
        assert task_account.payload["settlement_contract"] == "shared-fixed-growth.v1"
        assert task_account.payload["ledger_entry_type"] == "FIXED_TASK_AWARD"
        assert task_account.payload["score_config_version_id"] == replacement[
            "version_id"
        ]
        task_component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code == "G01",
            )
        )
        assert task_component is not None
        assert task_component.score_rule_version == replacement_payload[
            "policy_version"
        ]
        assert task_component.payload["assignment_id"] == "ASG-SW-POLICY-G01"
        assert task_component.payload["projection_id"] == "SPR-TASK-SETTLEMENT"
        assert task_component.payload["score_config_version_id"] == replacement[
            "version_id"
        ]


def test_score_policy_publish_updates_current_criteria_without_granting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "false",
    )
    teacher_id = "SW-POLICY-GRANT-GATED"
    config_service = ConfigService(SessionLocal)
    first = config_service.create_draft(
        ConfigKey.SCORE_GRADUATION,
        actor_id="gate-policy-creator-1",
        payload=deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]),
    )
    assert config_service.validate_version(
        first["version_id"], actor_id="gate-policy-validator-1"
    )["valid"]
    config_service.publish_version(
        first["version_id"], actor_id="gate-policy-publisher-1"
    )

    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id, feedback_praise_cnt=20))
        _add_teacher_insert_event(session, teacher_id, token="POLICY-GATE-INSERT")
    assert SourceWideWorker(engine).run_once(max_events=10)["failed"] == 0
    with session_scope(engine) as session:
        for assignment in _fixed_assignments(session, teacher_id):
            assignment.status = "COMPLETED"
            assignment.completed_at = _NOW

    replacement = config_service.create_draft(
        ConfigKey.SCORE_GRADUATION,
        actor_id="gate-policy-creator-2",
        from_version_id=first["version_id"],
    )
    replacement_payload = deepcopy(replacement["payload"])
    replacement_payload["scoring_items"]["feedback_praise"][
        "points_per_unit"
    ] = 6
    config_service.update_draft(
        replacement["version_id"],
        replacement_payload,
        actor_id="gate-policy-creator-2",
    )
    assert config_service.validate_version(
        replacement["version_id"], actor_id="gate-policy-validator-2"
    )["valid"]
    published = config_service.publish_version(
        replacement["version_id"], actor_id="gate-policy-publisher-2"
    )
    assert published["recalculation"]["source_wide_recalculation"][
        "teacher_count"
    ] == 1

    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert teacher is not None and teacher.total_score >= 100
        assert teacher.graduation_state == "IN_PROGRESS"
        assert qualification is not None
        assert qualification.graduation_criteria_met is True
        assert qualification.graduation_qualified is False
        assert qualification.graduation_qualified_at is None
        assert qualification.gate_results[
            "irreversible_qualification_grants_enabled"
        ] is False


def test_capacity_milestone_stays_awarded_and_duplicate_event_is_idempotent() -> None:
    teacher_id = "SW-CAPACITY"
    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id, peak_slot_cnt=40))
        _add_teacher_insert_event(session, teacher_id, token="CAPACITY-INSERT")
    SourceWideWorker(engine).run_once(max_events=10)

    with session_scope(engine) as session:
        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        source.peak_slot_cnt = 0
        _source_event(
            session,
            token="CAPACITY-DROP",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["peak_slot_cnt"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )
    SourceWideWorker(engine).run_once(max_events=10)

    with session_scope(engine) as session:
        component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code
                == "CAPACITY_PEAK_SLOT_40",
            )
        )
        account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "CAPACITY",
            )
        )
        assert component is not None
        assert account is not None
        component_revision = component.projection_revision
        account_version = account.version
        _source_event(
            session,
            token="CAPACITY-DUPLICATE",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["peak_slot_cnt"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )

    duplicate = SourceWideWorker(engine).run_once(max_events=10)

    assert duplicate["published"] == 1
    with session_scope(engine) as session:
        entries = list(
            session.scalars(
                select(ScoreEntryRecord).where(
                    ScoreEntryRecord.teacher_id == teacher_id,
                    ScoreEntryRecord.reason_code
                    == CAPACITY_MILESTONE_REASON_CODE,
                )
            ).all()
        )
        component = session.scalar(
            select(ScoreComponentAccountRecord).where(
                ScoreComponentAccountRecord.teacher_id == teacher_id,
                ScoreComponentAccountRecord.component_code
                == "CAPACITY_PEAK_SLOT_40",
            )
        )
        account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "CAPACITY",
            )
        )
        assert len(entries) == 1
        assert component is not None and component.current_score == 10
        assert account is not None and account.current_score == 10
        assert component.projection_revision == component_revision
        assert account.version == account_version


def test_source_teacher_delete_preserves_identity_tasks_and_earned_qualifications() -> None:
    teacher_id = "SW-QUALIFIED"
    with session_scope(engine) as session:
        session.add(
            _teacher_source(
                teacher_id,
                feedback_praise_cnt=0,
                peak_slot_cnt=40,
            )
        )
        _add_teacher_insert_event(session, teacher_id, token="QUALIFY-INSERT")
    SourceWideWorker(engine).run_once(max_events=10)

    with session_scope(engine) as session:
        assignments = _fixed_assignments(session, teacher_id)
        assert len(assignments) == 9
        for assignment in assignments:
            assignment.status = "COMPLETED"
            assignment.completed_at = _NOW
        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        source.feedback_praise_cnt = 40
        _source_event(
            session,
            token="QUALIFY-EARN",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["feedback_praise_cnt"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )
    earned = SourceWideWorker(engine).run_once(max_events=10)
    assert earned["failed"] == 0

    with session_scope(engine) as session:
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert qualification is not None
        assert qualification.graduation_criteria_met is True
        assert qualification.graduation_qualified is True
        assert qualification.gold_criteria_met is True
        assert qualification.gold_qualified is True
        graduation_at = qualification.graduation_qualified_at
        gold_at = qualification.gold_qualified_at
        source = session.get(TeacherSourceWideRecord, teacher_id)
        assert source is not None
        session.delete(source)
        _source_event(
            session,
            token="QUALIFY-DELETE",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="DELETE",
            changed_fields=TEACHER_SOURCE_FIELDS,
            old_teacher_id=teacher_id,
            new_teacher_id=None,
        )

    removed = SourceWideWorker(engine).run_once(max_events=10)

    assert removed["published"] == 1
    assert removed["failed"] == 0
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assignments = _fixed_assignments(session, teacher_id)
        assert teacher is not None
        assert teacher.payload["profile_source"]["status"] == "SOURCE_MISSING"
        assert len(assignments) == 9
        assert {item.status for item in assignments} == {"COMPLETED"}
        assert qualification is not None
        assert qualification.graduation_criteria_met is False
        assert qualification.graduation_qualified is True
        assert qualification.gold_criteria_met is False
        assert qualification.gold_qualified is True
        assert qualification.graduation_qualified_at == graduation_at
        assert qualification.gold_qualified_at == gold_at
        assert teacher.graduation_state == "GRADUATED"
        assert teacher.gold_qualified is True
        assert teacher.payload["metric_inputs"]["new_teacher_task_score"] == 30
        assert teacher.payload["metric_inputs"]["capacity_score"] == 10


def test_source_worker_gate_keeps_scores_and_current_criteria_without_granting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "false",
    )
    teacher_id = "SW-GRANT-GATED"
    with session_scope(engine) as session:
        session.add(_teacher_source(teacher_id, feedback_praise_cnt=40))
        _add_teacher_insert_event(session, teacher_id, token="GRANT-GATED-INSERT")
    assert SourceWideWorker(engine).run_once(max_events=10)["failed"] == 0

    with session_scope(engine) as session:
        for assignment in _fixed_assignments(session, teacher_id):
            assignment.status = "COMPLETED"
            assignment.completed_at = _NOW
        _source_event(
            session,
            token="GRANT-GATED-REFRESH",
            source_table="teacher_source_wide",
            source_id=teacher_id,
            operation="UPDATE",
            changed_fields=["feedback_praise_cnt"],
            old_teacher_id=teacher_id,
            new_teacher_id=teacher_id,
        )

    result = SourceWideWorker(engine).run_once(max_events=10)
    assert result["failed"] == 0
    with session_scope(engine) as session:
        teacher = session.get(TeacherRecord, teacher_id)
        qualification = session.get(TeacherQualificationRecord, teacher_id)
        assert teacher is not None and teacher.total_score >= 200
        assert teacher.graduation_state == "IN_PROGRESS"
        assert teacher.gold_qualified is False
        assert qualification is not None
        assert qualification.graduation_criteria_met is True
        assert qualification.gold_criteria_met is True
        assert qualification.graduation_qualified is False
        assert qualification.gold_qualified is False
        assert qualification.graduation_qualified_at is None
        assert qualification.gold_qualified_at is None
        assert qualification.gate_results[
            "irreversible_qualification_grants_enabled"
        ] is False


def test_projection_failure_rolls_back_and_dead_letters_without_partial_facts() -> None:
    teacher_id = "SW-FAILURE"
    with session_scope(engine) as session:
        session.add(
            _teacher_source(
                teacher_id,
                feedback_praise_cnt=-1,
            )
        )
        _add_teacher_insert_event(session, teacher_id, token="FAILURE")

    result = SourceWideWorker(
        engine,
        retry_delay=timedelta(0),
        max_retry_delay=timedelta(0),
        max_attempts=1,
        retry_jitter_ratio=0,
    ).run_once(max_events=1)

    assert result["claimed"] == 1
    assert result["published"] == 0
    assert result["failed"] == 1
    assert result["dead_lettered"] == 1
    with session_scope(engine) as session:
        event = session.get(OutboxEventRecord, "OUT-SW-FAILURE")
        assert event is not None
        assert event.status == "DEAD_LETTER"
        assert event.attempt_count == 1
        assert event.last_error == (
            "SourceWideProjectionError:"
            "SOURCE_COUNT_MUST_BE_NONNEGATIVE:feedback_praise_cnt"
        )
        assert session.get(TeacherRecord, teacher_id) is None
        assert _fixed_assignments(session, teacher_id) == []
        assert session.scalar(
            select(func.count()).select_from(ScoreEntryRecord).where(
                ScoreEntryRecord.teacher_id == teacher_id
            )
        ) == 0
