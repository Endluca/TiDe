from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from app.config_models import (
    SCORE_POLICY_V10_PAYLOAD,
    ConfigKey,
    ConfigPublicationAuditRecord,
    ConfigVersionRecord,
)
from app.config_service import seed_default_configs
from app.database import engine, session_scope
from app.db_models import (
    ScoreAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from app.fixed_growth_baseline import ensure_fixed_growth_assignments
from app.score_policy_baseline import (
    CURRENT_VERSION_ID,
    reset_score_policy_v1,
)
from app.task_catalog import MANDATORY_TASK_CODES


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
EXPECTED_POINTS = {
    "G01": 3,
    "G02": 2,
    "G03": 2,
    "G04": 3,
    "G05": 3,
    "G06": 4,
    "G07": 3,
    "G08": 5,
    "G09": 5,
}


def test_reset_collapses_score_history_rebuilds_ledger_and_preserves_qualifications() -> None:
    seed_default_configs()
    teacher_id = "RESET-V1-TEACHER"
    with session_scope(engine) as session:
        current = session.scalar(
            select(ConfigVersionRecord).where(
                ConfigVersionRecord.config_key
                == ConfigKey.SCORE_GRADUATION.value,
                ConfigVersionRecord.status == "PUBLISHED",
            )
        )
        assert current is not None
        session.add(
            ConfigVersionRecord(
                version_id="CFG-SCORE-OLD-v10",
                config_key=ConfigKey.SCORE_GRADUATION.value,
                version_number=2,
                status="RETIRED",
                high_impact=True,
                payload=deepcopy(SCORE_POLICY_V10_PAYLOAD),
                validation_errors=[],
                source_version_id=current.version_id,
                created_by="old-creator",
                updated_by="old-publisher",
                validated_by="old-creator",
                published_by="old-publisher",
                retired_by="old-publisher",
                created_at=NOW,
                updated_at=NOW,
                validated_at=NOW,
                published_at=NOW,
                retired_at=NOW,
            )
        )
        session.add(
            ConfigPublicationAuditRecord(
                audit_id="CFGAUD-SCORE-OLD-v10",
                version_id="CFG-SCORE-OLD-v10",
                config_key=ConfigKey.SCORE_GRADUATION.value,
                action="RETIRED",
                actor_id="old-publisher",
                from_status="PUBLISHED",
                to_status="RETIRED",
                payload_hash="0" * 64,
                detail="old test history",
                occurred_at=NOW,
            )
        )
        session.add(
            TeacherRecord(
                teacher_id=teacher_id,
                camp_enrollment_id=f"CAMP-{teacher_id}",
                name="Reset V1 Teacher",
                country="PH",
                timezone="Asia/Manila",
                camp_day=30,
                graduation_state="GRADUATED",
                gold_qualified=True,
                total_score=220,
                graduation_threshold=100,
                data_mode="REAL",
                source_batch_id=None,
                source_snapshot_label=None,
                payload={
                    "teacher_id": teacher_id,
                    "graduation_qualified": True,
                    "gold_qualified": True,
                    "metric_inputs": {
                        "total_completed_cnt": 0,
                        "peak_completed_cnt": 0,
                        "peak_slot_cnt": 0,
                        "perfect_cnt": 0,
                        "feedback_praise_cnt": 0,
                        "feedback_favorite_cnt": 0,
                        "late_cnt": 0,
                        "early_cnt": 0,
                        "absent_cnt": 0,
                        "l0_complaint_cnt": 0,
                    },
                    "metric_provenance": {},
                    "dimensions": [],
                },
                created_at=NOW,
                updated_at=NOW,
            )
        )
        ensure_fixed_growth_assignments(
            session,
            [teacher_id],
            occurred_at=NOW,
        )
        assignments = list(
            session.scalars(
                select(TaskAssignmentRecord).where(
                    TaskAssignmentRecord.teacher_id == teacher_id
                )
            ).all()
        )
        completed = {"G01", "G08"}
        for assignment in assignments:
            if assignment.task_code in completed:
                assignment.status = "COMPLETED"
                assignment.completed_at = NOW
                assignment.status_changed_at = NOW
                assignment.updated_at = NOW
        session.flush()
        for task_code, old_score in {"G01": 4.0, "G08": 10.0}.items():
            assignment = next(
                item for item in assignments if item.task_code == task_code
            )
            session.add(
                ScoreEntryRecord(
                    score_entry_id=f"OLD-FIXED-{task_code}",
                    camp_enrollment_id=f"CAMP-{teacher_id}",
                    lesson_id=None,
                    teacher_id=teacher_id,
                    dimension="NEW_TEACHER_TASK",
                    entry_type="FIXED_TASK_AWARD",
                    delta_score=old_score,
                    reason_code=f"FIXED_GROWTH_COMPLETED:{task_code}",
                    evidence_status="CONFIRMED",
                    score_rule_version="fixed-task:v10:old",
                    occurred_at=NOW,
                    recorded_at=NOW,
                    reversal_of_score_entry_id=None,
                    task_assignment_id=assignment.assignment_id,
                    idempotency_key=(
                        f"fixed-task-award:{assignment.assignment_id}"
                    ),
                    payload={"source_mode": "SYSTEM_TASK_STATUS"},
                )
            )

    with session_scope(engine) as session:
        result = reset_score_policy_v1(session)

    assert result["prior_score_versions_deleted"] == 2
    assert result["fixed_entries_deleted"] == 2
    assert result["fixed_entries_rebuilt"] == 2
    assert result["qualifications_after"] == result["qualifications_before"]
    assert result["qualifications_after"]["graduated"] >= 1
    assert result["qualifications_after"]["gold"] >= 1

    with session_scope(engine) as session:
        versions = list(
            session.scalars(
                select(ConfigVersionRecord).where(
                    ConfigVersionRecord.config_key
                    == ConfigKey.SCORE_GRADUATION.value
                )
            ).all()
        )
        assert len(versions) == 1
        assert versions[0].version_id == CURRENT_VERSION_ID
        assert versions[0].version_number == 1
        assert versions[0].status == "PUBLISHED"
        assert versions[0].payload["policy_version"] == "v1"

        templates = list(
            session.scalars(
                select(TaskTemplateRecord).where(
                    TaskTemplateRecord.template_id.in_(
                        MANDATORY_TASK_CODES
                    )
                )
            ).all()
        )
        assert {
            item.template_id: item.payload["score_value"]
            for item in templates
        } == EXPECTED_POINTS

        entries = list(
            session.scalars(
                select(ScoreEntryRecord)
                .where(
                    ScoreEntryRecord.teacher_id == teacher_id,
                    ScoreEntryRecord.entry_type == "FIXED_TASK_AWARD",
                )
                .order_by(ScoreEntryRecord.reason_code)
            ).all()
        )
        assert {
            item.reason_code.rsplit(":", 1)[-1]: item.delta_score
            for item in entries
        } == {"G01": 3, "G08": 5}
        assert all(item.score_rule_version.startswith("fixed-task:v1:") for item in entries)

        account = session.scalar(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.teacher_id == teacher_id,
                ScoreAccountRecord.dimension == "NEW_TEACHER_TASK",
            )
        )
        teacher = session.get(TeacherRecord, teacher_id)
        assert account is not None
        assert account.current_score == 8
        assert account.payload["ledger_score"] == 8
        assert account.payload["score_config"]["version_id"] == CURRENT_VERSION_ID
        assert teacher is not None
        assert teacher.graduation_state == "GRADUATED"
        assert teacher.gold_qualified is True
        assert teacher.payload["score_policy_version"] == "v1"
