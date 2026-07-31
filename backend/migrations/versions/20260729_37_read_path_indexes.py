"""add bounded read-path and pending-worker indexes

Revision ID: 20260729_37_read_perf
Revises: 20260729_36_perfect_score
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260729_37_read_perf"
down_revision: Union[str, None] = "20260729_36_perfect_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.create_index(
        "ix_lesson_dimension_score_teacher_lesson",
        "lesson_dimension_scores",
        ["teacher_id", "lesson_id"],
    )
    op.create_index(
        "ix_personalized_trigger_match_active_output",
        "personalized_trigger_matches",
        ["output_type", "output_id", "matched_at"],
        postgresql_where=sa.text("match_status <> 'SUPPRESSED'"),
    )
    op.create_index(
        "ix_outbox_pending_settlement_claim",
        "outbox_events",
        [
            "event_type",
            "aggregate_type",
            "available_at",
            "created_at",
            "outbox_id",
        ],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_audit_events_teacher_sequence",
        "audit_events",
        ["teacher_id", "sequence"],
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX ix_audit_events_structured_search_trgm
        ON audit_events
        USING gin (
            (
                event_id || ' ' || event_type || ' ' ||
                COALESCE(teacher_id, '') || ' ' ||
                COALESCE(task_id, '') || ' ' ||
                COALESCE(case_id, '') || ' ' ||
                actor_type
            ) gin_trgm_ops
        )
        """
    )

    # Retry requests currently have no deployed consumer. Keep those explicit
    # intents auditable without letting them masquerade as a worker backlog.
    # Store the prior values in the event payload so downgrade is exact and
    # cannot accidentally restore retry rows created after this migration.
    op.execute(
        """
        UPDATE outbox_events
        SET
            payload = jsonb_set(
                COALESCE(payload, '{}'::jsonb),
                '{_migration_20260729_37}',
                jsonb_build_object(
                    'previous_status', status,
                    'previous_last_error', last_error
                ),
                true
            ),
            status = 'PARKED',
            last_error = 'NO_OUTPUT_CONSUMER_CONFIGURED'
        WHERE status = 'PENDING'
          AND event_type = 'outbound_output.retry_requested.v1'
          AND NOT (
              COALESCE(payload, '{}'::jsonb)
              ? '_migration_20260729_37'
          )
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        UPDATE outbox_events
        SET
            status = COALESCE(
                payload #>> '{_migration_20260729_37,previous_status}',
                'PENDING'
            ),
            last_error = (
                payload #>>
                '{_migration_20260729_37,previous_last_error}'
            ),
            payload = payload - '_migration_20260729_37'
        WHERE event_type = 'outbound_output.retry_requested.v1'
          AND payload ? '_migration_20260729_37'
        """
    )
    op.drop_index(
        "ix_audit_events_structured_search_trgm",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_teacher_sequence",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_outbox_pending_settlement_claim",
        table_name="outbox_events",
    )
    op.drop_index(
        "ix_personalized_trigger_match_active_output",
        table_name="personalized_trigger_matches",
    )
    op.drop_index(
        "ix_lesson_dimension_score_teacher_lesson",
        table_name="lesson_dimension_scores",
    )
