"""add locked Peak-slot capacity milestone and retire supply-task scoring

Revision ID: 20260721_09_capacity_peak_v4
Revises: 20260721_08_inbound_fixed_tasks
Create Date: 2026-07-21
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260721_09_capacity_peak_v4"
down_revision: Union[str, None] = "20260721_08_inbound_fixed_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)
SCORE_RULE_VERSION = "new_teacher_30d_20260721_v4"
PREVIOUS_SCORE_RULE_VERSION = "new_teacher_30d_20260720"
MILESTONE_ID = "CAPACITY_PEAK_SLOT_40"
MILESTONE_REASON_CODE = "CAPACITY_MILESTONE_ACHIEVED"
MILESTONE_SETTLEMENT_MODE = "FIRST_ACHIEVEMENT_LOCKED"
SUPPLY_TEMPLATE_IDS = ("S01", "S02", "S03", "S04", "S05")

V3_SCORE_POLICY: dict[str, Any] = {
    "policy_version": "v3",
    "scoring_items": {
        "capacity": {"maximum_points": 10.0},
        "new_teacher_tasks": {"maximum_points": 30.0},
        "feedback_praise": {"points_per_unit": 5.0},
        "feedback_favorite": {"points_per_unit": 5.0},
        "feedback_rebook_15d": {"points_per_unit": 8.0},
        "reliability_on_time": {"points_per_unit": 2.0},
        "reliability_peak": {"points_per_unit": 1.0},
        "classroom_quality": {
            "points_per_unit": 2.0,
            "default_achievement_rate": 0.8,
            "source_mode": "MOCK_SIMULATION",
        },
    },
    "thresholds": {
        "graduation_raw_score": 100.0,
        "gold_raw_score": 200.0,
        "graduation_external_score": 100.0,
        "gold_external_score": 200.0,
    },
    "hard_gates": {
        "graduation": {
            "minimum_base_score": 30.0,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score_exclusive": 0.0,
            "minimum_reliability_score_exclusive": 0.0,
            "allow_severe_redline": False,
        },
        "gold": {
            "required_base_score": 40.0,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score": 20.0,
            "maximum_late_count": 1,
            "maximum_early_count": 0,
            "maximum_real_absent_count": 0,
        },
    },
    "graduation_effect": "IMMEDIATE_ON_CRITERIA",
}
V4_SCORE_POLICY = deepcopy(V3_SCORE_POLICY)
V4_SCORE_POLICY["policy_version"] = "v4"
V4_SCORE_POLICY["scoring_items"]["capacity"] = {
    "milestone_id": MILESTONE_ID,
    "metric": "peak_slot_cnt",
    "operator": "GTE",
    "threshold": 40,
    "score_value": 10.0,
    "maximum_points": 10.0,
    "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
}


def _policy_sha256(policy: dict[str, Any]) -> str:
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


V3_SCORE_POLICY_SHA256 = _policy_sha256(V3_SCORE_POLICY)
V4_SCORE_POLICY_SHA256 = _policy_sha256(V4_SCORE_POLICY)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return deepcopy(value) if isinstance(value, dict) else {}


def _nonnegative_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    else:
        normalized = str(value).strip()
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError(f"peak_slot_cnt must be a non-negative integer, got {value!r}")
        parsed = int(normalized)
    if parsed < 0:
        raise ValueError(f"peak_slot_cnt cannot be negative, got {value!r}")
    return parsed


def _time_key(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError:
            return 0.0
    if not isinstance(value, datetime):
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).timestamp()


def _milestone_idempotency_key(teacher_id: str) -> str:
    return f"CAPACITY_MILESTONE:{MILESTONE_ID}:{teacher_id}"


def _milestone_score_entry_id(teacher_id: str) -> str:
    digest = hashlib.sha256(_milestone_idempotency_key(teacher_id).encode("utf-8")).hexdigest()
    return f"CAPM-{digest[:32]}"


def _append_score_entry_payload(payload: dict[str, Any], entry: dict[str, Any]) -> None:
    entries = list(payload.get("score_entries") or [])
    entry_id = entry["score_entry_id"]
    if not any(
        isinstance(item, dict) and item.get("score_entry_id") == entry_id
        for item in entries
    ):
        entries.append(deepcopy(entry))
    payload["score_entries"] = entries


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _capacity_provenance(
    batch_id: str,
    *,
    peak_slot_value_missing: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    peak = {
        "source_mode": "SOURCE_MISSING" if peak_slot_value_missing else "REAL",
        "source_field": "peak_slot_cnt",
        "batch_id": batch_id,
        "note": (
            "Source peak_slot_cnt is empty; it is normalized to 0 for scoring and "
            "must not be presented as observed zero."
            if peak_slot_value_missing
            else "Direct teacher-wide Peak-slot count from the validated 61-column snapshot."
        ),
    }
    capacity = {
        "source_mode": "DERIVED_REAL",
        "source_field": "peak_slot_cnt",
        "source_fields": ["peak_slot_cnt"],
        "batch_id": batch_id,
        "note": (
            "CAPACITY_PEAK_SLOT_40 awards 10 points at the first observed "
            "peak_slot_cnt >= 40 and then remains locked; later corrections do not reverse it."
        ),
    }
    return peak, capacity


def _capacity_dimension(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "CAPACITY",
        "label": "供给达标（Peak slots）",
        "score": float(inputs["capacity_score"]),
        "minimum": 0,
        "weight": 0,
        "data_mode": "DERIVED_REAL",
        "score_rule_version": SCORE_RULE_VERSION,
        "components": [
            {
                "code": MILESTONE_ID,
                "metric": "peak_slot_cnt",
                "value": int(inputs["peak_slot_cnt"]),
                "operator": "GTE",
                "threshold": 40,
                "score": float(inputs["capacity_score"]),
                "maximum_points": 10,
                "milestone_achieved": bool(inputs["capacity_milestone_achieved"]),
                "currently_meets_threshold": bool(
                    inputs["capacity_milestone_currently_meets_threshold"]
                ),
                "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
                "source_mode": "DERIVED_REAL",
            }
        ],
    }


def _replace_capacity_dimension(payload: dict[str, Any], inputs: dict[str, Any]) -> None:
    dimensions = list(payload.get("dimensions") or [])
    replacement = _capacity_dimension(inputs)
    for index, item in enumerate(dimensions):
        if isinstance(item, dict) and item.get("code") == "CAPACITY":
            dimensions[index] = replacement
            break
    else:
        dimensions.append(replacement)
    payload["dimensions"] = dimensions


def _eligibility(
    *,
    raw_total_score: float,
    user_feedback_score: float,
    reliability_score: float,
    inputs: dict[str, Any],
) -> tuple[bool, bool]:
    graduation = bool(
        raw_total_score >= 100
        and float(inputs.get("new_teacher_task_score", 0)) >= 30
        and float(inputs.get("total_completed_cnt", 0)) >= 10
        and user_feedback_score > 0
        and reliability_score > 0
        and not bool(inputs.get("severe_redline_event", False))
    )
    gold = bool(
        graduation
        and raw_total_score >= 200
        and float(inputs.get("capacity_score", 0))
        + float(inputs.get("new_teacher_task_score", 0))
        == 40
        and float(inputs.get("total_completed_cnt", 0)) >= 10
        and user_feedback_score >= 20
        and float(inputs.get("late_cnt", 0)) <= 1
        and float(inputs.get("early_cnt", 0)) <= 0
        and float(inputs.get("real_absent_cnt", 0)) <= 0
    )
    return graduation, gold


def _upgrade_metric_snapshots(
    bind: Any,
    now: datetime,
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    snapshots = sa.table(
        "teacher_metric_snapshots",
        sa.column("snapshot_id", sa.String()),
        sa.column("batch_id", sa.String()),
        sa.column("teacher_id", sa.String()),
        sa.column("peak_slot_cnt", sa.Integer()),
        sa.column("capacity_score", sa.Float()),
        sa.column("reliability_score", sa.Float()),
        sa.column("user_feedback_score", sa.Float()),
        sa.column("raw_total_score", sa.Float()),
        sa.column("public_total_score", sa.Float()),
        sa.column("metric_inputs", JSON_VALUE),
        sa.column("metric_provenance", JSON_VALUE),
        sa.column("raw_payload", JSON_VALUE),
        sa.column("score_rule_version", sa.String()),
        sa.column("score_policy_snapshot", JSON_VALUE),
        sa.column("score_policy_sha256", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    batches = sa.table(
        "data_import_batches",
        sa.column("batch_id", sa.String()),
        sa.column("imported_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    score_entries = sa.table(
        "score_entries",
        sa.column("teacher_id", sa.String()),
        sa.column("dimension", sa.String()),
        sa.column("reason_code", sa.String()),
    )
    batch_times = {
        str(row["batch_id"]): row["imported_at"] or row["created_at"]
        for row in bind.execute(sa.select(batches)).mappings()
    }
    existing_ledger_teachers = {
        str(row["teacher_id"])
        for row in bind.execute(
            sa.select(score_entries.c.teacher_id).where(
                score_entries.c.dimension == "CAPACITY",
                score_entries.c.reason_code == MILESTONE_REASON_CODE,
            )
        ).mappings()
    }
    rows_by_teacher: dict[str, list[Any]] = {}
    for row in bind.execute(sa.select(snapshots)).mappings().all():
        rows_by_teacher.setdefault(str(row["teacher_id"]), []).append(row)

    upgraded: dict[tuple[str, str], dict[str, Any]] = {}
    first_achievements: dict[str, dict[str, Any]] = {}
    for teacher_id, teacher_rows in rows_by_teacher.items():
        teacher_rows.sort(
            key=lambda row: (
                _time_key(batch_times.get(str(row["batch_id"]))),
                _time_key(row["created_at"]),
                str(row["snapshot_id"]),
            )
        )
        parsed_rows: list[tuple[Any, dict[str, Any], dict[str, Any], int, bool, bool]] = []
        for row in teacher_rows:
            inputs = _json_dict(row["metric_inputs"])
            raw_payload = _json_dict(row["raw_payload"])
            peak_slot_count = _nonnegative_int(raw_payload.get("peak_slot_cnt"))
            prior_v4_lock = bool(inputs.get("capacity_milestone_achieved", False)) and (
                str(row["score_rule_version"]) == SCORE_RULE_VERSION
                or _json_dict(row["score_policy_snapshot"]).get("policy_version") == "v4"
            )
            parsed_rows.append(
                (
                    row,
                    inputs,
                    raw_payload,
                    peak_slot_count,
                    raw_payload.get("peak_slot_cnt") in (None, ""),
                    prior_v4_lock,
                )
            )

        has_snapshot_achievement = any(
            peak_slot_count >= 40 or prior_v4_lock
            for _, _, _, peak_slot_count, _, prior_v4_lock in parsed_rows
        )
        force_existing_ledger_lock = (
            teacher_id in existing_ledger_teachers and not has_snapshot_achievement
        )
        locked = False
        for index, (
            row,
            inputs,
            raw_payload,
            peak_slot_count,
            peak_slot_value_missing,
            prior_v4_lock,
        ) in enumerate(parsed_rows):
            currently_meets = peak_slot_count >= 40
            first_lock_transition = bool(
                not locked
                and (
                    currently_meets
                    or prior_v4_lock
                    or (force_existing_ledger_lock and index == 0)
                )
            )
            if first_lock_transition:
                locked = True
                occurred_at = (
                    batch_times.get(str(row["batch_id"]))
                    or row["created_at"]
                    or now
                )
                first_achievements[teacher_id] = {
                    "teacher_id": teacher_id,
                    "batch_id": str(row["batch_id"]),
                    "snapshot_id": str(row["snapshot_id"]),
                    "peak_slot_cnt": peak_slot_count,
                    "occurred_at": occurred_at,
                    "source_mode": (
                        "DERIVED_REAL" if currently_meets else "MIGRATED_LOCK_STATE"
                    ),
                }
            achieved = locked
            capacity_score = 10.0 if achieved else 0.0
            old_capacity_score = float(row["capacity_score"] or 0)
            raw_total_score = round(
                float(row["raw_total_score"] or 0) - old_capacity_score + capacity_score,
                2,
            )
            inputs.update(
                peak_slot_cnt=peak_slot_count,
                capacity_score=capacity_score,
                capacity_milestone_id=MILESTONE_ID,
                capacity_milestone_achieved=achieved,
                capacity_milestone_currently_meets_threshold=currently_meets,
                capacity_milestone_settlement_mode=MILESTONE_SETTLEMENT_MODE,
            )
            provenance = _json_dict(row["metric_provenance"])
            peak_provenance, capacity_provenance = _capacity_provenance(
                str(row["batch_id"]),
                peak_slot_value_missing=peak_slot_value_missing,
            )
            provenance["peak_slot_cnt"] = peak_provenance
            provenance["capacity_score"] = capacity_provenance
            values = {
                "peak_slot_cnt": peak_slot_count,
                "capacity_score": capacity_score,
                "raw_total_score": raw_total_score,
                "public_total_score": round(min(raw_total_score, 200.0), 2),
                "metric_inputs": inputs,
                "metric_provenance": provenance,
                "score_rule_version": SCORE_RULE_VERSION,
                "score_policy_snapshot": deepcopy(V4_SCORE_POLICY),
                "score_policy_sha256": V4_SCORE_POLICY_SHA256,
                "updated_at": now,
            }
            bind.execute(
                snapshots.update()
                .where(snapshots.c.snapshot_id == row["snapshot_id"])
                .values(**values)
            )
            upgraded[(str(row["batch_id"]), teacher_id)] = {
                **values,
                "reliability_score": float(row["reliability_score"] or 0),
                "user_feedback_score": float(row["user_feedback_score"] or 0),
            }
    return upgraded, first_achievements


def _upgrade_capacity_ledger(
    bind: Any,
    now: datetime,
    first_achievements: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not first_achievements:
        return {}
    teachers = sa.table(
        "teachers",
        sa.column("teacher_id", sa.String()),
        sa.column("camp_enrollment_id", sa.String()),
    )
    entries = sa.table(
        "score_entries",
        sa.column("score_entry_id", sa.String()),
        sa.column("camp_enrollment_id", sa.String()),
        sa.column("lesson_id", sa.String()),
        sa.column("teacher_id", sa.String()),
        sa.column("dimension", sa.String()),
        sa.column("entry_type", sa.String()),
        sa.column("delta_score", sa.Float()),
        sa.column("reason_code", sa.String()),
        sa.column("evidence_status", sa.String()),
        sa.column("score_rule_version", sa.String()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
        sa.column("recorded_at", sa.DateTime(timezone=True)),
        sa.column("reversal_of_score_entry_id", sa.String()),
        sa.column("idempotency_key", sa.String()),
        sa.column("payload", JSON_VALUE),
    )
    teacher_rows = {
        str(row["teacher_id"]): row
        for row in bind.execute(
            sa.select(teachers).where(
                teachers.c.teacher_id.in_(set(first_achievements))
            )
        ).mappings()
    }
    expected_keys = {
        _milestone_idempotency_key(teacher_id)
        for teacher_id in first_achievements
    }
    existing_by_teacher: dict[str, Any] = {}
    for row in bind.execute(
        sa.select(entries).where(
            sa.or_(
                sa.and_(
                    entries.c.dimension == "CAPACITY",
                    entries.c.reason_code == MILESTONE_REASON_CODE,
                    entries.c.teacher_id.in_(set(first_achievements)),
                ),
                entries.c.idempotency_key.in_(expected_keys),
            )
        )
    ).mappings():
        existing_by_teacher.setdefault(str(row["teacher_id"]), row)

    payload_by_teacher: dict[str, dict[str, Any]] = {}
    for teacher_id, achievement in first_achievements.items():
        teacher = teacher_rows.get(teacher_id)
        if teacher is None:
            raise RuntimeError(
                f"cannot settle {MILESTONE_ID} for missing teacher {teacher_id!r}"
            )
        existing = existing_by_teacher.get(teacher_id)
        if existing is not None:
            payload = _json_dict(existing["payload"])
            payload.update(
                score_entry_id=str(existing["score_entry_id"]),
                camp_enrollment_id=str(existing["camp_enrollment_id"]),
                lesson_id=existing["lesson_id"],
                teacher_id=teacher_id,
                dimension=str(existing["dimension"]),
                entry_type=str(existing["entry_type"]),
                delta_score=float(existing["delta_score"]),
                reason_code=str(existing["reason_code"]),
                evidence_status=str(existing["evidence_status"]),
                score_rule_version=str(existing["score_rule_version"]),
                occurred_at=(
                    _iso(existing["occurred_at"])
                    if existing["occurred_at"] is not None
                    else None
                ),
                reversal_of_score_entry_id=existing[
                    "reversal_of_score_entry_id"
                ],
                idempotency_key=str(existing["idempotency_key"]),
            )
            payload_by_teacher[teacher_id] = payload
            continue

        occurred_at = achievement["occurred_at"]
        entry_id = _milestone_score_entry_id(teacher_id)
        idempotency_key = _milestone_idempotency_key(teacher_id)
        payload = {
            "score_entry_id": entry_id,
            "camp_enrollment_id": str(teacher["camp_enrollment_id"]),
            "lesson_id": None,
            "teacher_id": teacher_id,
            "dimension": "CAPACITY",
            "entry_type": "MILESTONE_ACHIEVEMENT",
            "delta_score": 10.0,
            "reason_code": MILESTONE_REASON_CODE,
            "evidence_status": "CONFIRMED",
            "score_rule_version": SCORE_RULE_VERSION,
            "occurred_at": _iso(occurred_at),
            "reversal_of_score_entry_id": None,
            "idempotency_key": idempotency_key,
            "milestone_id": MILESTONE_ID,
            "metric": "peak_slot_cnt",
            "operator": "GTE",
            "threshold": 40,
            "observed_value": int(achievement["peak_slot_cnt"]),
            "source_batch_id": achievement["batch_id"],
            "source_snapshot_id": achievement["snapshot_id"],
            "source_mode": achievement["source_mode"],
            "settlement_mode": MILESTONE_SETTLEMENT_MODE,
        }
        bind.execute(
            entries.insert().values(
                score_entry_id=entry_id,
                camp_enrollment_id=teacher["camp_enrollment_id"],
                lesson_id=None,
                teacher_id=teacher_id,
                dimension="CAPACITY",
                entry_type="MILESTONE_ACHIEVEMENT",
                delta_score=10.0,
                reason_code=MILESTONE_REASON_CODE,
                evidence_status="CONFIRMED",
                score_rule_version=SCORE_RULE_VERSION,
                occurred_at=occurred_at,
                recorded_at=now,
                reversal_of_score_entry_id=None,
                idempotency_key=idempotency_key,
                payload=deepcopy(payload),
            )
        )
        payload_by_teacher[teacher_id] = payload
    return payload_by_teacher


def _upgrade_current_teachers(
    bind: Any,
    now: datetime,
    snapshots: dict[tuple[str, str], dict[str, Any]],
    milestone_entries: dict[str, dict[str, Any]],
) -> None:
    teachers = sa.table(
        "teachers",
        sa.column("teacher_id", sa.String()),
        sa.column("source_batch_id", sa.String()),
        sa.column("graduation_state", sa.String()),
        sa.column("total_score", sa.Float()),
        sa.column("payload", JSON_VALUE),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(teachers).where(teachers.c.source_batch_id.is_not(None))
    ).mappings():
        snapshot = snapshots.get((str(row["source_batch_id"]), str(row["teacher_id"])))
        if snapshot is None:
            continue
        payload = _json_dict(row["payload"])
        milestone_entry = milestone_entries.get(str(row["teacher_id"]))
        if milestone_entry is not None:
            _append_score_entry_payload(payload, milestone_entry)
        inputs = deepcopy(snapshot["metric_inputs"])
        raw_total_score = float(snapshot["raw_total_score"])
        graduation, gold = _eligibility(
            raw_total_score=raw_total_score,
            user_feedback_score=float(snapshot["user_feedback_score"]),
            reliability_score=float(snapshot["reliability_score"]),
            inputs=inputs,
        )
        payload.update(
            metric_inputs=inputs,
            metric_provenance=deepcopy(snapshot["metric_provenance"]),
            capacity_milestone={
                "milestone_id": MILESTONE_ID,
                "metric": "peak_slot_cnt",
                "operator": "GTE",
                "threshold": 40,
                "current_value": inputs["peak_slot_cnt"],
                "currently_meets_threshold": inputs[
                    "capacity_milestone_currently_meets_threshold"
                ],
                "achieved": inputs["capacity_milestone_achieved"],
                "score": inputs["capacity_score"],
                "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
            },
            raw_total_score=raw_total_score,
            total_score=raw_total_score,
            external_display_score=float(snapshot["public_total_score"]),
            score_rule_version=SCORE_RULE_VERSION,
            score_policy_version="v4",
            score_policy_sha256=V4_SCORE_POLICY_SHA256,
            graduation_criteria_met=graduation,
            gold_criteria_met=gold,
            score_tier="GOLD" if gold else "GRADUATED" if graduation else "IN_PROGRESS",
            updated_at=_iso(now),
        )
        _replace_capacity_dimension(payload, inputs)
        graduation_state = (
            "GRADUATED"
            if str(row["graduation_state"]) == "GRADUATED" or graduation
            else str(row["graduation_state"])
        )
        payload["graduation_state"] = graduation_state
        bind.execute(
            teachers.update()
            .where(teachers.c.teacher_id == row["teacher_id"])
            .values(
                total_score=raw_total_score,
                graduation_state=graduation_state,
                payload=payload,
                updated_at=now,
            )
        )


def _upgrade_capacity_accounts(
    bind: Any,
    now: datetime,
    snapshots: dict[tuple[str, str], dict[str, Any]],
) -> None:
    teachers = sa.table(
        "teachers",
        sa.column("teacher_id", sa.String()),
        sa.column("source_batch_id", sa.String()),
    )
    current_batch_by_teacher = {
        str(row["teacher_id"]): str(row["source_batch_id"])
        for row in bind.execute(
            sa.select(teachers).where(teachers.c.source_batch_id.is_not(None))
        ).mappings()
    }
    accounts = sa.table(
        "score_accounts",
        sa.column("account_id", sa.String()),
        sa.column("teacher_id", sa.String()),
        sa.column("dimension", sa.String()),
        sa.column("current_score", sa.Float()),
        sa.column("score_rule_version", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("payload", JSON_VALUE),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(accounts).where(accounts.c.dimension == "CAPACITY")
    ).mappings():
        teacher_id = str(row["teacher_id"])
        batch_id = current_batch_by_teacher.get(teacher_id)
        snapshot = snapshots.get((batch_id, teacher_id)) if batch_id else None
        if snapshot is None:
            continue
        dimension = _capacity_dimension(snapshot["metric_inputs"])
        bind.execute(
            accounts.update()
            .where(accounts.c.account_id == row["account_id"])
            .values(
                current_score=float(snapshot["capacity_score"]),
                score_rule_version=SCORE_RULE_VERSION,
                version=int(row["version"] or 0) + 1,
                payload=dimension,
                updated_at=now,
            )
        )


def _upgrade_batches(bind: Any, now: datetime, batch_ids: set[str]) -> None:
    batches = sa.table(
        "data_import_batches",
        sa.column("batch_id", sa.String()),
        sa.column("payload", JSON_VALUE),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(batches).where(batches.c.batch_id.in_(batch_ids))
    ).mappings():
        payload = _json_dict(row["payload"])
        validation = _json_dict(payload.get("validation"))
        validation.update(
            score_rule_version=SCORE_RULE_VERSION,
            score_policy_sha256=V4_SCORE_POLICY_SHA256,
            capacity_milestone_id=MILESTONE_ID,
            capacity_milestone_settlement_mode="FIRST_ACHIEVEMENT_LOCKED",
        )
        payload["validation"] = validation
        bind.execute(
            batches.update()
            .where(batches.c.batch_id == row["batch_id"])
            .values(payload=payload, updated_at=now)
        )


def _upgrade_supply_templates_and_policies(bind: Any, now: datetime) -> None:
    templates = sa.table(
        "task_output_templates_v2",
        sa.column("row_id", sa.String()),
        sa.column("template_id", sa.String()),
        sa.column("template_version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("output_type", sa.String()),
        sa.column("execution_owner", sa.String()),
        sa.column("integration_mode", sa.String()),
        sa.column("external_task_template_code", sa.String()),
        sa.column("source_mode", sa.String()),
        sa.column("payload", JSON_VALUE),
        sa.column("created_by", sa.String()),
        sa.column("updated_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(templates).where(
            templates.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
            templates.c.template_version == 1,
        )
    ).mappings():
        template_id = str(row["template_id"])
        existing_v2 = bind.execute(
            sa.select(templates.c.row_id).where(
                templates.c.template_id == template_id,
                templates.c.template_version == 2,
            )
        ).scalar_one_or_none()
        payload_v1 = _json_dict(row["payload"])
        previous_status = str(row["status"])
        payload_v1.update(
            status="RETIRED",
            revision=int(row["revision"] or 0) + 1,
            updated_by="SYSTEM_MIGRATION_20260721",
            updated_at=_iso(now),
            migration_previous_status=previous_status,
        )
        bind.execute(
            templates.update()
            .where(templates.c.row_id == row["row_id"])
            .values(
                status="RETIRED",
                revision=int(row["revision"] or 0) + 1,
                payload=payload_v1,
                updated_by="SYSTEM_MIGRATION_20260721",
                updated_at=now,
            )
        )
        if existing_v2 is not None:
            continue
        payload_v2 = _json_dict(row["payload"])
        payload_v2.update(
            template_version=2,
            status="PUBLISHED",
            revision=2,
            category="PERSONALIZED_IMPROVEMENT",
            score_type="ZERO",
            score_value=0,
            benefit=(
                "This personalized improvement task adds 0 points. Capacity points "
                "come only from the locked Peak-slot milestone."
            ),
            source_version=1,
            created_by="SYSTEM_MIGRATION_20260721",
            updated_by="SYSTEM_MIGRATION_20260721",
            created_at=_iso(now),
            updated_at=_iso(now),
            published_at=_iso(now),
            published_by="SYSTEM_MIGRATION_20260721",
        )
        bind.execute(
            templates.insert().values(
                row_id=f"{template_id}:v2",
                template_id=template_id,
                template_version=2,
                status="PUBLISHED",
                revision=2,
                output_type=row["output_type"],
                execution_owner=row["execution_owner"],
                integration_mode=row["integration_mode"],
                external_task_template_code=row["external_task_template_code"],
                source_mode=row["source_mode"],
                payload=payload_v2,
                created_by="SYSTEM_MIGRATION_20260721",
                updated_by="SYSTEM_MIGRATION_20260721",
                created_at=now,
                updated_at=now,
            )
        )

    policies = sa.table(
        "trigger_policies_v2",
        sa.column("row_id", sa.String()),
        sa.column("trigger_rule_id", sa.String()),
        sa.column("policy_version", sa.Integer()),
        sa.column("template_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("signal_code", sa.String()),
        sa.column("output_type", sa.String()),
        sa.column("template_version", sa.Integer()),
        sa.column("source_mode", sa.String()),
        sa.column("manual_gate", sa.Boolean()),
        sa.column("payload", JSON_VALUE),
        sa.column("created_by", sa.String()),
        sa.column("updated_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(policies).where(
            policies.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
            policies.c.template_version == 1,
            policies.c.policy_version == 1,
        )
    ).mappings():
        rule_id = str(row["trigger_rule_id"])
        existing_v2 = bind.execute(
            sa.select(policies.c.row_id).where(
                policies.c.trigger_rule_id == rule_id,
                policies.c.policy_version == 2,
            )
        ).scalar_one_or_none()
        previous_status = str(row["status"])
        payload_v1 = _json_dict(row["payload"])
        payload_v1.update(
            status="RETIRED",
            revision=int(row["revision"] or 0) + 1,
            updated_by="SYSTEM_MIGRATION_20260721",
            updated_at=_iso(now),
            migration_previous_status=previous_status,
        )
        bind.execute(
            policies.update()
            .where(policies.c.row_id == row["row_id"])
            .values(
                status="RETIRED",
                revision=int(row["revision"] or 0) + 1,
                payload=payload_v1,
                updated_by="SYSTEM_MIGRATION_20260721",
                updated_at=now,
            )
        )
        if existing_v2 is not None:
            continue
        payload_v2 = _json_dict(row["payload"])
        payload_v2.update(
            policy_version=2,
            template_version=2,
            status=previous_status,
            revision=1,
            source_version=1,
            created_by="SYSTEM_MIGRATION_20260721",
            updated_by="SYSTEM_MIGRATION_20260721",
            created_at=_iso(now),
            updated_at=_iso(now),
        )
        bind.execute(
            policies.insert().values(
                row_id=f"{rule_id}:v2",
                trigger_rule_id=rule_id,
                policy_version=2,
                status=previous_status,
                revision=1,
                signal_code=row["signal_code"],
                output_type=row["output_type"],
                template_id=row["template_id"],
                template_version=2,
                source_mode=row["source_mode"],
                manual_gate=row["manual_gate"],
                payload=payload_v2,
                created_by="SYSTEM_MIGRATION_20260721",
                updated_by="SYSTEM_MIGRATION_20260721",
                created_at=now,
                updated_at=now,
            )
        )


def upgrade() -> None:
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column(
            "peak_slot_cnt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    snapshots, first_achievements = _upgrade_metric_snapshots(bind, now)
    milestone_entries = _upgrade_capacity_ledger(bind, now, first_achievements)
    _upgrade_current_teachers(bind, now, snapshots, milestone_entries)
    _upgrade_capacity_accounts(bind, now, snapshots)
    _upgrade_batches(bind, now, {batch_id for batch_id, _ in snapshots})
    _upgrade_supply_templates_and_policies(bind, now)


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    policies = sa.table(
        "trigger_policies_v2",
        sa.column("row_id", sa.String()),
        sa.column("trigger_rule_id", sa.String()),
        sa.column("policy_version", sa.Integer()),
        sa.column("template_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("payload", JSON_VALUE),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(
        policies.delete().where(
            policies.c.policy_version == 2,
            policies.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
        )
    )
    for row in bind.execute(
        sa.select(policies).where(
            policies.c.policy_version == 1,
            policies.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
        )
    ).mappings():
        payload = _json_dict(row["payload"])
        previous_status = payload.pop("migration_previous_status", None)
        if previous_status is None:
            continue
        payload.update(
            status=previous_status,
            revision=int(row["revision"] or 0) + 1,
            updated_by="SYSTEM_MIGRATION_DOWNGRADE",
            updated_at=_iso(now),
        )
        bind.execute(
            policies.update()
            .where(policies.c.row_id == row["row_id"])
            .values(
                status=previous_status,
                revision=int(row["revision"] or 0) + 1,
                payload=payload,
                updated_by="SYSTEM_MIGRATION_DOWNGRADE",
                updated_at=now,
            )
        )

    templates = sa.table(
        "task_output_templates_v2",
        sa.column("row_id", sa.String()),
        sa.column("template_id", sa.String()),
        sa.column("template_version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("payload", JSON_VALUE),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(
        templates.delete().where(
            templates.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
            templates.c.template_version == 2,
        )
    )
    for row in bind.execute(
        sa.select(templates).where(
            templates.c.template_id.in_(SUPPLY_TEMPLATE_IDS),
            templates.c.template_version == 1,
        )
    ).mappings():
        payload = _json_dict(row["payload"])
        previous_status = payload.pop("migration_previous_status", None)
        if previous_status is None:
            continue
        payload.update(
            status=previous_status,
            revision=int(row["revision"] or 0) + 1,
            updated_by="SYSTEM_MIGRATION_DOWNGRADE",
            updated_at=_iso(now),
        )
        bind.execute(
            templates.update()
            .where(templates.c.row_id == row["row_id"])
            .values(
                status=previous_status,
                revision=int(row["revision"] or 0) + 1,
                payload=payload,
                updated_by="SYSTEM_MIGRATION_DOWNGRADE",
                updated_at=now,
            )
        )

    # The downgrade restores the historical v3 fixed-capacity assumption. It
    # intentionally does not create reversal ledger entries; this is schema
    # rollback only and must never be used as a business score operation.
    snapshots = sa.table(
        "teacher_metric_snapshots",
        sa.column("snapshot_id", sa.String()),
        sa.column("capacity_score", sa.Float()),
        sa.column("raw_total_score", sa.Float()),
        sa.column("public_total_score", sa.Float()),
        sa.column("metric_inputs", JSON_VALUE),
        sa.column("metric_provenance", JSON_VALUE),
        sa.column("score_rule_version", sa.String()),
        sa.column("score_policy_snapshot", JSON_VALUE),
        sa.column("score_policy_sha256", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(sa.select(snapshots)).mappings():
        inputs = _json_dict(row["metric_inputs"])
        for key in (
            "peak_slot_cnt",
            "capacity_milestone_id",
            "capacity_milestone_achieved",
            "capacity_milestone_currently_meets_threshold",
            "capacity_milestone_settlement_mode",
        ):
            inputs.pop(key, None)
        old_capacity = float(row["capacity_score"] or 0)
        inputs["capacity_score"] = 10.0
        raw_total = round(float(row["raw_total_score"] or 0) - old_capacity + 10.0, 2)
        provenance = _json_dict(row["metric_provenance"])
        provenance.pop("peak_slot_cnt", None)
        provenance["capacity_score"] = {
            "source_mode": "MOCK",
            "source_field": None,
            "batch_id": None,
            "note": "Historical v3 fixed 10-point capacity assumption.",
        }
        bind.execute(
            snapshots.update()
            .where(snapshots.c.snapshot_id == row["snapshot_id"])
            .values(
                capacity_score=10.0,
                raw_total_score=raw_total,
                public_total_score=min(raw_total, 200.0),
                metric_inputs=inputs,
                metric_provenance=provenance,
                score_rule_version=PREVIOUS_SCORE_RULE_VERSION,
                score_policy_snapshot=deepcopy(V3_SCORE_POLICY),
                score_policy_sha256=V3_SCORE_POLICY_SHA256,
                updated_at=now,
            )
        )
    op.drop_column("teacher_metric_snapshots", "peak_slot_cnt")
