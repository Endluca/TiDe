"""remove effective runtime access inherited through legacy grants

Revision ID: 20260806_42_effective_acl
Revises: 20260806_41_runtime_acl_columns
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op


revision: str = "20260806_42_effective_acl"
down_revision: str | None = "20260806_41_runtime_acl_columns"
branch_labels: str | None = None
depends_on: str | None = None


MANAGED_RELATIONS: tuple[str, ...] = (
    "data_import_batches",
    "source_records",
    "teacher_source_wide",
    "lesson_source_wide",
    "teachers",
    "teacher_metric_snapshots",
    "lesson_facts",
    "complaint_category_rules",
    "personalized_trigger_matches",
    "lesson_dimension_scores",
    "score_accounts",
    "score_component_accounts",
    "score_entries",
    "task_templates",
    "task_assignments",
    "notifications",
    "notification_events",
    "ops_cases",
    "ops_decisions",
    "outbound_outputs",
    "outbox_events",
    "audit_events",
    "agent_decisions",
    "idempotency_records",
    "provider_calls",
    "config_versions",
    "config_publication_audits",
    "operator_accounts",
    "operator_role_grants",
    "operator_sessions",
    "teacher_scorecard_current",
    "teacher_lesson_score_current",
)

ROOT_RUNTIME_NO_ACCESS_VIEWS: tuple[str, ...] = (
    "teacher_scorecard_current",
    "teacher_lesson_score_current",
)


def _qualified(objects: tuple[str, ...]) -> str:
    return ",\n            ".join(f"public.{name}" for name in objects)


def _regclass_array(objects: tuple[str, ...]) -> str:
    entries = ", ".join(f"'public.{name}'::regclass" for name in objects)
    return f"ARRAY[{entries}]"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(MANAGED_RELATIONS)}
        FROM PUBLIC;

        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(ROOT_RUNTIME_NO_ACCESS_VIEWS)}
        FROM tit_growth_app;

        REVOKE ALL PRIVILEGES ON SEQUENCE
            public.audit_events_sequence_seq
        FROM PUBLIC;

        DO $effective_runtime_acl_assertions$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM unnest(
                    ARRAY[
                        'public.teacher_scorecard_current',
                        'public.teacher_lesson_score_current'
                    ]::text[]
                ) AS relation(name)
                WHERE has_table_privilege(
                    'tit_growth_app', relation.name, 'SELECT'
                )
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app still has a teacher-facing score view grant';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_class AS relations
                CROSS JOIN LATERAL aclexplode(
                    COALESCE(relations.relacl, '{{}}'::aclitem[])
                ) AS privileges
                WHERE relations.oid = ANY (
                    {_regclass_array(MANAGED_RELATIONS)}
                )
                  AND privileges.grantee = 0
            ) THEN
                RAISE EXCEPTION
                    'a managed public relation still grants privileges to PUBLIC';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_class AS sequences
                CROSS JOIN LATERAL aclexplode(
                    COALESCE(sequences.relacl, '{{}}'::aclitem[])
                ) AS privileges
                WHERE sequences.oid = 'public.audit_events_sequence_seq'::regclass
                  AND privileges.grantee = 0
            ) THEN
                RAISE EXCEPTION
                    'the audit sequence still grants privileges to PUBLIC';
            END IF;
        END
        $effective_runtime_acl_assertions$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Public grants are intentionally not recreated: their previous shape is
    # unknowable and restoring broad access would be an unsafe downgrade.
    return
