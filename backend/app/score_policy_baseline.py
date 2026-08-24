"""Collapse the test score-policy history into the single approved v1 baseline.

This is an explicit test-environment repair path, not the normal configuration
publication workflow.  It updates the pinned mandatory-task templates, rebuilds
their score ledger from shared task status, replaces the score-config history
with one published v1 row, and refreshes every current score projection in one
transaction.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .config_models import (
    SCORE_POLICY_V1_PAYLOAD,
    ConfigKey,
    ConfigPublicationAuditRecord,
    ConfigStatus,
    ConfigVersionRecord,
)
from .config_service import validate_config_payload
from .db_models import (
    ScoreAccountRecord,
    ScoreEntryRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from .score_projection_lock import acquire_score_projection_lock
from .score_read_model import refresh_persisted_score_read_models
from .task_catalog import MANDATORY_TASK_CODES, task_template_seed_payloads


CURRENT_VERSION_ID = "CFG-SCORE_GRADUATION-0001-v1"
CURRENT_AUDIT_ID = "CFGAUD-SCORE-GRADUATION-RESET-v1"
FIXED_ENTRY_TYPE = "FIXED_TASK_AWARD"
TASK_DIMENSION = "NEW_TEACHER_TASK"
SYSTEM_SOURCE_MODE = "SYSTEM_TASK_STATUS"
MAXIMUM_TASK_SCORE = 30.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _deterministic_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _qualification_counts(session: Session) -> dict[str, int]:
    return {
        "graduated": int(
            session.scalar(
                select(func.count()).select_from(TeacherRecord).where(
                    TeacherRecord.graduation_state == "GRADUATED"
                )
            )
            or 0
        ),
        "gold": int(
            session.scalar(
                select(func.count()).select_from(TeacherRecord).where(
                    TeacherRecord.gold_qualified.is_(True)
                )
            )
            or 0
        ),
    }


def reset_score_policy_v1(
    session: Session,
    *,
    creator_actor_id: str = "system:score-v1-reset-creator",
    publisher_actor_id: str = "system:score-v1-reset-publisher",
) -> dict[str, Any]:
    """Make the approved policy the only score config and rebuild current data."""

    if creator_actor_id == publisher_actor_id:
        raise ValueError("score v1 reset requires different creator and publisher")

    acquire_score_projection_lock(session)
    now = _now()
    normalized_policy = validate_config_payload(
        ConfigKey.SCORE_GRADUATION,
        SCORE_POLICY_V1_PAYLOAD,
    )
    policy_hash = _canonical_hash(normalized_policy)
    approved_payloads = {
        str(item["template_id"]): item
        for item in task_template_seed_payloads()
        if str(item["template_id"]) in MANDATORY_TASK_CODES
    }
    approved_points = {
        code: float(payload["score_value"])
        for code, payload in approved_payloads.items()
    }
    if (
        set(approved_payloads) != set(MANDATORY_TASK_CODES)
        or sum(approved_points.values()) != MAXIMUM_TASK_SCORE
    ):
        raise RuntimeError("approved mandatory task catalog is not the 30-point G01-G09 set")

    templates = list(
        session.scalars(
            select(TaskTemplateRecord)
            .where(
                TaskTemplateRecord.template_id.in_(MANDATORY_TASK_CODES),
                TaskTemplateRecord.status == "PUBLISHED",
            )
            .order_by(TaskTemplateRecord.template_id)
            .with_for_update()
        ).all()
    )
    template_by_code = {item.template_id: item for item in templates}
    if set(template_by_code) != set(MANDATORY_TASK_CODES):
        missing = sorted(set(MANDATORY_TASK_CODES) - set(template_by_code))
        extra = sorted(set(template_by_code) - set(MANDATORY_TASK_CODES))
        raise RuntimeError(
            f"published mandatory template baseline mismatch: missing={missing}, extra={extra}"
        )

    for code in MANDATORY_TASK_CODES:
        record = template_by_code[code]
        approved = deepcopy(approved_payloads[code])
        next_revision = int(record.revision or 0) + 1
        next_payload = deepcopy(record.payload or {})
        next_payload.update(approved)
        next_payload.update(
            {
                "status": "PUBLISHED",
                "revision": next_revision,
                "updated_by": publisher_actor_id,
                "updated_at": now.isoformat(),
            }
        )
        record.output_type = str(approved["output_type"])
        record.execution_owner = str(approved["execution_owner"])
        record.integration_mode = str(approved["integration_mode"])
        record.external_task_template_code = str(
            approved["external_task_template_code"]
        )
        record.source_mode = str(approved["source_mode"])
        record.payload = next_payload
        record.revision = next_revision
        record.updated_by = publisher_actor_id
        record.updated_at = now

    prior_score_version_ids = list(
        session.scalars(
            select(ConfigVersionRecord.version_id)
            .where(
                ConfigVersionRecord.config_key
                == ConfigKey.SCORE_GRADUATION.value
            )
            .with_for_update()
        ).all()
    )
    if prior_score_version_ids:
        session.execute(
            update(ConfigVersionRecord)
            .where(
                ConfigVersionRecord.source_version_id.in_(
                    prior_score_version_ids
                )
            )
            .values(source_version_id=None)
        )
    session.execute(
        delete(ConfigPublicationAuditRecord).where(
            ConfigPublicationAuditRecord.config_key
            == ConfigKey.SCORE_GRADUATION.value
        )
    )
    session.execute(
        delete(ConfigVersionRecord).where(
            ConfigVersionRecord.config_key
            == ConfigKey.SCORE_GRADUATION.value
        )
    )
    session.flush()

    version = ConfigVersionRecord(
        version_id=CURRENT_VERSION_ID,
        config_key=ConfigKey.SCORE_GRADUATION.value,
        version_number=1,
        status=ConfigStatus.PUBLISHED.value,
        high_impact=True,
        payload=deepcopy(normalized_policy),
        validation_errors=[],
        source_version_id=None,
        created_by=creator_actor_id,
        updated_by=publisher_actor_id,
        validated_by=creator_actor_id,
        published_by=publisher_actor_id,
        retired_by=None,
        created_at=now,
        updated_at=now,
        validated_at=now,
        published_at=now,
        retired_at=None,
    )
    session.add(version)
    # There is no ORM relationship between these records.  Flush the parent
    # explicitly so PostgreSQL never observes the audit FK before v1 exists.
    session.flush()
    session.add(
        ConfigPublicationAuditRecord(
            audit_id=CURRENT_AUDIT_ID,
            version_id=CURRENT_VERSION_ID,
            config_key=ConfigKey.SCORE_GRADUATION.value,
            action="RESET_BASELINE",
            actor_id=publisher_actor_id,
            from_status=None,
            to_status=ConfigStatus.PUBLISHED.value,
            payload_hash=policy_hash,
            detail=(
                "Approved mandatory scores applied; prior test score-policy "
                "versions collapsed into the single v1 baseline."
            ),
            occurred_at=now,
        )
    )
    session.flush()

    qualifications_before = _qualification_counts(session)
    fixed_entries_deleted = int(
        session.scalar(
            select(func.count()).select_from(ScoreEntryRecord).where(
                ScoreEntryRecord.entry_type == FIXED_ENTRY_TYPE
            )
        )
        or 0
    )
    session.execute(
        delete(ScoreEntryRecord).where(
            ScoreEntryRecord.entry_type == FIXED_ENTRY_TYPE
        )
    )
    session.execute(
        delete(ScoreAccountRecord).where(
            ScoreAccountRecord.dimension == TASK_DIMENSION
        )
    )
    session.flush()

    completed_assignments = list(
        session.scalars(
            select(TaskAssignmentRecord)
            .where(
                TaskAssignmentRecord.task_code.in_(MANDATORY_TASK_CODES),
                TaskAssignmentRecord.task_kind == "FIXED_GROWTH",
                TaskAssignmentRecord.creator_system == "TRIGGER_CENTER",
                TaskAssignmentRecord.source_mode == "REAL",
                TaskAssignmentRecord.status == "COMPLETED",
            )
            .order_by(
                TaskAssignmentRecord.teacher_id,
                TaskAssignmentRecord.task_code,
            )
            .with_for_update()
        ).all()
    )
    teacher_ids = sorted({item.teacher_id for item in completed_assignments})
    teachers = {
        item.teacher_id: item
        for item in session.scalars(
            select(TeacherRecord).where(TeacherRecord.teacher_id.in_(teacher_ids))
        ).all()
    } if teacher_ids else {}
    if set(teachers) != set(teacher_ids):
        raise RuntimeError("completed mandatory assignment refers to a missing teacher")

    config_snapshot = {
        "config_key": ConfigKey.SCORE_GRADUATION.value,
        "version_id": CURRENT_VERSION_ID,
        "version_number": 1,
        "policy_version": "v1",
        "payload_sha256": policy_hash,
        "payload": deepcopy(normalized_policy),
    }
    task_rule_version = f"fixed-task:v1:{policy_hash[:12]}"
    for assignment in completed_assignments:
        if assignment.completed_at is None:
            raise RuntimeError(
                f"completed assignment {assignment.assignment_id} has no completed_at"
            )
        score = approved_points[assignment.task_code]
        teacher = teachers[assignment.teacher_id]
        session.add(
            ScoreEntryRecord(
                score_entry_id=_deterministic_id(
                    "FIXED-TASK-AWARD",
                    assignment.assignment_id,
                ),
                camp_enrollment_id=teacher.camp_enrollment_id,
                lesson_id=None,
                source_region=None,
                source_appoint_id=None,
                participation_seq=None,
                teacher_id=teacher.teacher_id,
                dimension=TASK_DIMENSION,
                entry_type=FIXED_ENTRY_TYPE,
                delta_score=score,
                reason_code=(
                    f"FIXED_GROWTH_COMPLETED:{assignment.task_code}"
                ),
                evidence_status="CONFIRMED",
                score_rule_version=task_rule_version,
                occurred_at=assignment.completed_at,
                recorded_at=now,
                reversal_of_score_entry_id=None,
                task_assignment_id=assignment.assignment_id,
                projection_origin="FIXED_TASK_LIVE",
                materialized_by_run_id=None,
                projection_generation=None,
                idempotency_key=(
                    f"fixed-task-award:{assignment.assignment_id}"
                ),
                payload={
                    "source_mode": SYSTEM_SOURCE_MODE,
                    "settlement_contract": "shared-fixed-growth.v1",
                    "assignment_id": assignment.assignment_id,
                    "task_code": assignment.task_code,
                    "template_version_id": assignment.template_version_id,
                    "score_value": score,
                    "score_config": deepcopy(config_snapshot),
                },
            )
        )
    session.flush()

    recalculation = refresh_persisted_score_read_models(
        session,
        trigger_type="SCORE_POLICY_PUBLISHED",
        trigger_ref=CURRENT_VERSION_ID,
        score_policy_payload=normalized_policy,
        score_config_version_id=CURRENT_VERSION_ID,
    )

    task_accounts = list(
        session.scalars(
            select(ScoreAccountRecord).where(
                ScoreAccountRecord.dimension == TASK_DIMENSION
            )
        ).all()
    )
    for account in task_accounts:
        payload = deepcopy(account.payload or {})
        payload.update(
            {
                "source_mode": SYSTEM_SOURCE_MODE,
                "settlement_contract": "shared-fixed-growth.v1",
                "ledger_entry_type": FIXED_ENTRY_TYPE,
                "maximum_points": MAXIMUM_TASK_SCORE,
                "ledger_score": float(account.current_score),
                "score_config": deepcopy(config_snapshot),
            }
        )
        account.score_rule_version = task_rule_version
        account.payload = payload
        account.updated_at = now

    qualifications_after = _qualification_counts(session)
    if (
        qualifications_after["graduated"] < qualifications_before["graduated"]
        or qualifications_after["gold"] < qualifications_before["gold"]
    ):
        raise RuntimeError("earned graduation or gold qualification was withdrawn")
    session.flush()

    return {
        "status": "RESET_TO_V1",
        "policy_version": "v1",
        "version_id": CURRENT_VERSION_ID,
        "prior_score_versions_deleted": len(prior_score_version_ids),
        "mandatory_template_scores": approved_points,
        "mandatory_template_score_total": sum(approved_points.values()),
        "fixed_entries_deleted": fixed_entries_deleted,
        "fixed_entries_rebuilt": len(completed_assignments),
        "task_accounts_rebuilt": len(task_accounts),
        "recalculated_teacher_count": int(
            recalculation.get("teacher_count") or 0
        ),
        "qualifications_before": qualifications_before,
        "qualifications_after": qualifications_after,
        "score_policy_sha256": policy_hash,
    }
