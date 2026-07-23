"""freeze the complete score policy on teacher metric snapshots

Revision ID: 20260717_05_score_snapshot
Revises: 20260717_04_teacher_metrics
Create Date: 2026-07-17
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717_05_score_snapshot"
down_revision: Union[str, None] = "20260717_04_teacher_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCORE_RULE_VERSION = "new_teacher_30d_20260717"
SCORE_POLICY_SNAPSHOT = {
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
SCORE_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        SCORE_POLICY_SNAPSHOT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def upgrade() -> None:
    json_value = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("score_rule_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("score_policy_snapshot", json_value, nullable=True),
    )
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("score_policy_sha256", sa.String(length=64), nullable=True),
    )

    dialect_name = op.get_bind().dialect.name
    policy_json = json.dumps(
        SCORE_POLICY_SNAPSHOT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("'", "''")
    # SQLAlchemy TextClause treats every colon as a bind marker even inside a
    # quoted JSON literal. Escaping here renders literal colons in both online
    # execution and Alembic's offline SQL mode.
    policy_json = policy_json.replace(":", r"\:")
    policy_expression = (
        f"CAST('{policy_json}' AS JSONB)"
        if dialect_name == "postgresql"
        else f"'{policy_json}'"
    )
    op.execute(
        sa.text(
            "UPDATE teacher_metric_snapshots SET "
            f"score_rule_version = '{SCORE_RULE_VERSION}', "
            f"score_policy_snapshot = {policy_expression}, "
            f"score_policy_sha256 = '{SCORE_POLICY_SHA256}'"
        )
    )

    # The already-imported v04 batch used UTC only to satisfy the typed
    # non-null storage column. Correct its business payload without touching
    # rows whose provenance says a timezone really existed.
    if dialect_name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE teachers "
                "SET payload = jsonb_set(payload, '{timezone}', 'null'::jsonb, true) "
                "WHERE data_mode = 'MIXED' "
                "AND payload->'profile_provenance'->'timezone'->>'source_mode' = "
                "'SOURCE_MISSING'"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE teachers "
                "SET payload = json_set(payload, '$.timezone', json('null')) "
                "WHERE data_mode = 'MIXED' "
                "AND json_extract(payload, "
                "'$.profile_provenance.timezone.source_mode') = 'SOURCE_MISSING'"
            )
        )

    if dialect_name == "sqlite":
        with op.batch_alter_table("teacher_metric_snapshots") as batch_op:
            batch_op.alter_column("score_rule_version", nullable=False)
            batch_op.alter_column("score_policy_snapshot", nullable=False)
            batch_op.alter_column("score_policy_sha256", nullable=False)
    else:
        op.alter_column("teacher_metric_snapshots", "score_rule_version", nullable=False)
        op.alter_column("teacher_metric_snapshots", "score_policy_snapshot", nullable=False)
        op.alter_column("teacher_metric_snapshots", "score_policy_sha256", nullable=False)
    op.create_index(
        "ix_teacher_metric_snapshots_score_policy_sha256",
        "teacher_metric_snapshots",
        ["score_policy_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_metric_snapshots_score_policy_sha256",
        table_name="teacher_metric_snapshots",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("teacher_metric_snapshots") as batch_op:
            batch_op.drop_column("score_policy_sha256")
            batch_op.drop_column("score_policy_snapshot")
            batch_op.drop_column("score_rule_version")
    else:
        op.drop_column("teacher_metric_snapshots", "score_policy_sha256")
        op.drop_column("teacher_metric_snapshots", "score_policy_snapshot")
        op.drop_column("teacher_metric_snapshots", "score_rule_version")
