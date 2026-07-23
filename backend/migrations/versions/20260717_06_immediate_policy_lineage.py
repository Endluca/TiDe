"""align imported teacher lineage to the locked immediate-graduation policy

Revision ID: 20260717_06_policy_lineage
Revises: 20260717_05_score_snapshot
Create Date: 2026-07-17
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_06_policy_lineage"
down_revision: Union[str, None] = "20260717_05_score_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCORE_RULE_VERSION = "new_teacher_30d_20260717"
IMMEDIATE_SCORE_POLICY: dict[str, Any] = {
    "policy_version": "v2",
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
        "gold_raw_score": 660.0,
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
PREVIOUS_SETTLEMENT_POLICY: dict[str, Any] = {
    "policy_version": "v2",
    "scoring_items": {
        "capacity": {"maximum_points": 10},
        "new_teacher_tasks": {"maximum_points": 30},
        "feedback_praise": {"points_per_unit": 5},
        "feedback_favorite": {"points_per_unit": 5},
        "feedback_rebook_15d": {"points_per_unit": 8},
        "reliability_on_time": {"points_per_unit": 2},
        "reliability_peak": {"points_per_unit": 1},
        "classroom_quality": {
            "points_per_unit": 2,
            "default_achievement_rate": 0.8,
            "source_mode": "MOCK_SIMULATION",
        },
    },
    "thresholds": {
        "graduation_raw_score": 100,
        "gold_raw_score": 660,
        "graduation_external_score": 100,
        "gold_external_score": 200,
    },
    "hard_gates": {
        "graduation": {
            "minimum_base_score": 30,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score_exclusive": 0,
            "minimum_reliability_score_exclusive": 0,
            "allow_severe_redline": False,
        },
        "gold": {
            "required_base_score": 40,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score": 20,
            "maximum_late_count": 1,
            "maximum_early_count": 0,
            "maximum_real_absent_count": 0,
        },
    },
    "settlement_window_hours": 72,
}


def _policy_sha256(policy: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


IMMEDIATE_SCORE_POLICY_SHA256 = _policy_sha256(IMMEDIATE_SCORE_POLICY)
PREVIOUS_SETTLEMENT_POLICY_SHA256 = _policy_sha256(PREVIOUS_SETTLEMENT_POLICY)


def _policy_expression(policy: dict[str, Any], dialect_name: str) -> str:
    policy_json = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("'", "''")
    # SQLAlchemy TextClause recognizes colons as bind markers even inside a
    # quoted JSON literal. Escaping renders literal colons in online/offline SQL.
    policy_json = policy_json.replace(":", r"\:")
    if dialect_name == "postgresql":
        return f"CAST('{policy_json}' AS JSONB)"
    return f"'{policy_json}'"


def _apply_policy(policy: dict[str, Any], policy_sha256: str) -> None:
    dialect_name = op.get_bind().dialect.name
    policy_expression = _policy_expression(policy, dialect_name)
    op.execute(
        sa.text(
            "UPDATE teacher_metric_snapshots SET "
            f"score_rule_version = '{SCORE_RULE_VERSION}', "
            f"score_policy_snapshot = {policy_expression}, "
            f"score_policy_sha256 = '{policy_sha256}', "
            "updated_at = CURRENT_TIMESTAMP"
        )
    )

    if dialect_name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE teachers AS t SET payload = "
                "jsonb_set(jsonb_set(COALESCE(t.payload, CAST('{}' AS JSONB)), "
                "'{score_rule_version}', "
                f"to_jsonb(CAST('{SCORE_RULE_VERSION}' AS TEXT)), true), "
                "'{score_policy_sha256}', "
                f"to_jsonb(CAST('{policy_sha256}' AS TEXT)), true), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.teacher_id = t.teacher_id AND s.batch_id = t.source_batch_id)"
            )
        )
        op.execute(
            sa.text(
                "UPDATE data_import_batches AS b SET payload = jsonb_set("
                "COALESCE(b.payload, CAST('{}' AS JSONB)), '{validation}', "
                "(CASE WHEN jsonb_typeof(b.payload->'validation') = 'object' "
                "THEN b.payload->'validation' ELSE CAST('{}' AS JSONB) END) || "
                "jsonb_build_object("
                f"'score_rule_version', '{SCORE_RULE_VERSION}', "
                f"'score_policy_sha256', '{policy_sha256}'), true), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.batch_id = b.batch_id)"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE teachers SET payload = json_set(payload, "
                f"'$.score_rule_version', '{SCORE_RULE_VERSION}', "
                f"'$.score_policy_sha256', '{policy_sha256}'), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.teacher_id = teachers.teacher_id "
                "AND s.batch_id = teachers.source_batch_id)"
            )
        )
        op.execute(
            sa.text(
                "UPDATE data_import_batches SET payload = json_set(payload, "
                f"'$.validation.score_rule_version', '{SCORE_RULE_VERSION}', "
                f"'$.validation.score_policy_sha256', '{policy_sha256}'), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.batch_id = data_import_batches.batch_id)"
            )
        )


def _graduate_current_eligible_teachers() -> None:
    dialect_name = op.get_bind().dialect.name
    eligibility = (
        "s.raw_total_score >= 100 "
        "AND (s.capacity_score + s.new_teacher_task_score) >= 30 "
        "AND s.total_completed_cnt >= 10 "
        "AND s.user_feedback_score > 0 "
        "AND s.reliability_score > 0 "
        "AND s.severe_redline_event = false"
    )
    if dialect_name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE teachers AS t SET graduation_state = 'GRADUATED', "
                "payload = jsonb_set(t.payload, '{graduation_state}', "
                "to_jsonb(CAST('GRADUATED' AS TEXT)), true), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.teacher_id = t.teacher_id AND s.batch_id = t.source_batch_id "
                f"AND {eligibility})"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE teachers SET graduation_state = 'GRADUATED', "
                "payload = json_set(payload, '$.graduation_state', 'GRADUATED'), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE EXISTS (SELECT 1 FROM teacher_metric_snapshots AS s "
                "WHERE s.teacher_id = teachers.teacher_id "
                "AND s.batch_id = teachers.source_batch_id "
                f"AND {eligibility})"
            )
        )


def upgrade() -> None:
    _apply_policy(IMMEDIATE_SCORE_POLICY, IMMEDIATE_SCORE_POLICY_SHA256)
    _graduate_current_eligible_teachers()


def downgrade() -> None:
    # Graduation is a terminal business fact. A schema downgrade must not
    # silently revoke it, so only the policy lineage returns to the v05 value.
    _apply_policy(PREVIOUS_SETTLEMENT_POLICY, PREVIOUS_SETTLEMENT_POLICY_SHA256)
