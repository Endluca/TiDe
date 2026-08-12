"""Refresh the persisted score model from the current source-wide contract.

The historical snapshot/fact projector was intentionally retired.  A policy
publication or explicit full rebuild must now cover only teachers whose
``teachers.source_snapshot_label`` is ``SOURCE_WIDE_CURRENT``.  Mixing an old
teacher projection into the same rebuild is a contract error, not a reason to
fall back to retired tables.

The caller owns the transaction.  This module acquires the shared projection
lock and delegates to the source-wide projector without committing, so policy
publication and its full recalculation remain atomic.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    ConfigKey,
    ConfigStatus,
    ConfigVersionRecord,
    ScoreGraduationConfig,
)
from .db_models import TeacherRecord
from .qualification_award_gate import (
    irreversible_qualification_grants_enabled,
)
from .score_projection_lock import acquire_score_projection_lock


SOURCE_SNAPSHOT_LABEL = "SOURCE_WIDE_CURRENT"


class ScoreProjectionSourceContractError(RuntimeError):
    """The selected projection contains teachers outside the current source contract."""

    error_code = "SCORE_PROJECTION_REQUIRES_SOURCE_WIDE_CURRENT"

    def __init__(self, teacher_ids: Iterable[str]) -> None:
        normalized = tuple(sorted({str(item) for item in teacher_ids}))
        self.teacher_ids = normalized
        super().__init__(
            f"{self.error_code}:count={len(normalized)}:"
            f"teacher_ids={list(normalized[:10])}"
        )


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _score_config(
    session: Session,
    payload: dict[str, Any] | None,
    version_id: str | None,
) -> tuple[dict[str, Any], str]:
    if payload is not None:
        normalized = ScoreGraduationConfig.model_validate(payload).model_dump(
            mode="json"
        )
        return (
            normalized,
            version_id or f"EXPLICIT:{normalized['policy_version']}",
        )

    record = session.scalar(
        select(ConfigVersionRecord)
        .where(
            ConfigVersionRecord.config_key == ConfigKey.SCORE_GRADUATION.value,
            ConfigVersionRecord.status == ConfigStatus.PUBLISHED.value,
        )
        .order_by(ConfigVersionRecord.version_number.desc())
    )
    if record is None:
        normalized = ScoreGraduationConfig.model_validate(
            deepcopy(DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION])
        ).model_dump(mode="json")
        return (
            normalized,
            f"DEFAULT_EMPTY_DATABASE:{normalized['policy_version']}",
        )
    normalized = ScoreGraduationConfig.model_validate(record.payload).model_dump(
        mode="json"
    )
    return normalized, record.version_id


def refresh_persisted_score_read_models(
    session: Session,
    *,
    trigger_type: str,
    trigger_ref: str | None = None,
    teacher_ids: Iterable[str] | None = None,
    score_policy_payload: dict[str, Any] | None = None,
    score_config_version_id: str | None = None,
) -> dict[str, Any]:
    """Refresh only ``SOURCE_WIDE_CURRENT`` teachers in the caller transaction.

    ``trigger_type`` and ``trigger_ref`` remain part of the public command
    contract used by policy publication and operational rebuild scripts.  The
    source-wide projector records source/policy lineage directly, so they do
    not select an alternate persistence implementation here.
    """

    del trigger_type, trigger_ref
    # Validate the fail-closed grant gate even when a policy publication has
    # no current teachers to rebuild. Actual awards are gated again at the
    # shared qualification persistence boundary.
    irreversible_qualification_grants_enabled()
    acquire_score_projection_lock(session)

    selected_ids = sorted({str(item) for item in teacher_ids or []})
    teacher_query = select(TeacherRecord)
    if selected_ids:
        teacher_query = teacher_query.where(
            TeacherRecord.teacher_id.in_(selected_ids)
        )
    teachers = list(
        session.scalars(
            teacher_query.order_by(TeacherRecord.teacher_id)
        ).all()
    )
    if selected_ids and len(teachers) != len(selected_ids):
        found = {item.teacher_id for item in teachers}
        raise RuntimeError(
            "SCORE_PROJECTION_TEACHERS_MISSING:"
            f"{sorted(set(selected_ids) - found)[:10]}"
        )

    incompatible_teacher_ids = [
        item.teacher_id
        for item in teachers
        if item.source_snapshot_label != SOURCE_SNAPSHOT_LABEL
    ]
    if incompatible_teacher_ids:
        raise ScoreProjectionSourceContractError(incompatible_teacher_ids)

    policy_payload, config_version_id = _score_config(
        session,
        score_policy_payload,
        score_config_version_id,
    )
    policy = ScoreGraduationConfig.model_validate(policy_payload)

    # Imported lazily to keep this command boundary acyclic.  The delegated
    # function uses the same Session and never commits, which is what makes a
    # policy publication plus full recalculation one atomic transaction.
    from .source_wide_worker import refresh_source_wide_score_read_models

    current_teacher_ids = [item.teacher_id for item in teachers]
    if current_teacher_ids:
        source_wide_recalculation = refresh_source_wide_score_read_models(
            session,
            teacher_ids=current_teacher_ids,
            score_policy_payload=policy_payload,
            score_config_version_id=config_version_id,
            acquire_lock=False,
        )
    else:
        # An empty list means "all teachers" to the lower-level primitive.
        # Do not pass it through after this transaction has established that
        # the full rebuild target is empty.
        source_wide_recalculation = {
            "teacher_count": 0,
            "lesson_result_changes": 0,
            "component_changes": 0,
            "account_changes": 0,
            "qualification_changes": 0,
            "score_rule_version": policy.policy_version,
        }

    return {
        "projection_id": f"SPR-{uuid4().hex}",
        "teacher_count": len(teachers),
        "lesson_score_state_count": int(
            source_wide_recalculation["lesson_result_changes"]
        ),
        "component_account_count": int(
            source_wide_recalculation["component_changes"]
        ),
        "score_rule_version": policy.policy_version,
        "source_wide_recalculation": source_wide_recalculation,
        "source_versions": {
            "teacher_batch_ids": [],
            "lesson_batch_ids": [],
            "score_config_version_id": config_version_id,
            "score_policy_sha256": _canonical_hash(policy_payload),
        },
    }


__all__ = [
    "SOURCE_SNAPSHOT_LABEL",
    "ScoreProjectionSourceContractError",
    "refresh_persisted_score_read_models",
]
