"""switch to the shared task assignment ledger

Revision ID: 20260722_10_shared_tasks
Revises: 20260721_09_capacity_peak_v4
Create Date: 2026-07-22

This migration is intentionally destructive for the retired V1/V2 task
transport data.  It keeps the ten approved G01-G10 template versions byte for
byte, preserves score/audit facts, and replaces the old assignment projection
with the database-enforced shared ledger used by both backend services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_10_shared_tasks"
down_revision: Union[str, None] = "20260721_09_capacity_peak_v4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FIXED_TASK_CODES = tuple(f"G{index:02d}" for index in range(1, 11))
FIXED_TASK_CODES_SQL = ", ".join(f"'{code}'" for code in FIXED_TASK_CODES)
SOURCE_MODES_SQL = "'REAL', 'DERIVED_REAL', 'MOCK', 'MOCK_SIMULATION', 'MOCK_PROXY'"

# Frozen here on purpose: a brand-new database runs every Alembic migration
# before any seed command.  Importing the mutable runtime catalog would make an
# old migration change meaning when application code changes.
CANONICAL_G_TASKS = (
    (
        "G01",
        "资料与资质完善",
        "Profile & Credentials Completion",
        4,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
        "Your trial-camp profile, self-introduction and required credentials must be completed as part of first-push readiness.",
        "Complete the self-introduction flow, register the required web-app profile, and submit all required credentials for review.",
        "Return COMPLETED only after every required profile and credential item is present and the configured AI or human review has passed.",
        "Earn 4 mandatory-growth points once. This completes one component of first-push readiness and counts toward the 30-point mandatory total.",
    ),
    (
        "G02",
        "设备与网络检测",
        "Device & Network Check",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_LESSON",
        "A verified device and network check is required before your first lesson and no later than Day 7.",
        "Open the trusted device-check entry, test your computer, network, microphone, speaker and camera, then follow any repair guidance.",
        "Return COMPLETED only after the trusted check result is PASS. An approved exception must return WAIVED, not COMPLETED.",
        "Earn 3 mandatory-growth points once and complete the device component of first-lesson readiness.",
    ),
    (
        "G03",
        "平台政策学习",
        "Platform Policies",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
        "Platform policies and compliance rules must be understood before first-push eligibility can be confirmed.",
        "Study the assigned platform-policy content and complete the required knowledge check.",
        "Return COMPLETED only after all required policy modules are viewed and the configured quiz or acknowledgement passes.",
        "Earn 3 mandatory-growth points once and complete the policy component of first-push readiness.",
    ),
    (
        "G04",
        "首课备课",
        "Lesson Preparation",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_LESSON",
        "Lesson-preparation routines must be completed before your first lesson and no later than Day 7.",
        "Complete the preparation checklist, including lesson-material review and the required pre-class setup steps.",
        "Return COMPLETED only after every required checklist item is confirmed by the teacher app.",
        "Earn 3 mandatory-growth points once and complete the preparation component of first-lesson readiness.",
    ),
    (
        "G05",
        "TTP 入门",
        "TTP Orientation",
        1,
        "DAY_8_14",
        "P2",
        14,
        None,
        "You have entered Day 8-14 and TTP orientation is part of the required trial-camp learning path.",
        "Complete the TTP orientation module and its required checklist.",
        "Return COMPLETED only after all required TTP orientation items are finished.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
    (
        "G06",
        "ME 文化与 PARSNIP",
        "ME Culture & PARSNIP",
        2,
        "DAY_8_14",
        "P2",
        14,
        None,
        "ME culture and PARSNIP boundaries are required learning during Day 8-14.",
        "Study the assigned culture and PARSNIP content, then complete the configured quiz or acknowledgement.",
        "Return COMPLETED only after the required content and knowledge check pass.",
        "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    (
        "G07",
        "可靠性培训",
        "Reliability Training",
        2,
        "DAY_8_14",
        "P1",
        14,
        None,
        "Reliability, attendance, late and early-leave rules are required learning during Day 8-14.",
        "Complete the reliability training and its attendance-rule knowledge check.",
        "Return COMPLETED only after the required module is finished and the knowledge check passes.",
        "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    (
        "G08",
        "Free Trial 培训",
        "Free Trial Training",
        1,
        "DAY_8_14",
        "P2",
        14,
        None,
        "Free Trial training is required for the applicable trial-lesson scenario during Day 8-14.",
        "Open the assigned external training and complete all required Free Trial content.",
        "Return COMPLETED only after the trusted training system returns the required completion tag.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
    (
        "G09",
        "Cocos 课程培训",
        "Cocos Course Training",
        10,
        "DAY_15_30",
        "P2",
        30,
        None,
        "Cocos course training is a required Day 15-30 capability task.",
        "Complete the assigned Cocos training in the linked training system.",
        "Return COMPLETED only after the trusted training system returns a valid Cocos completion tag.",
        "Earn 10 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    (
        "G10",
        "SET 教学基础",
        "SET Teaching Fundamentals",
        1,
        "DAY_15_30",
        "P2",
        30,
        None,
        "SET teaching fundamentals are required during Day 15-30.",
        "Complete the assigned SET fundamentals training in the linked training system.",
        "Return COMPLETED only after the trusted training system returns a valid SET completion tag.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
)


def _scalar(bind: sa.Connection, statement: str) -> int:
    return int(bind.execute(sa.text(statement)).scalar_one())


def _ensure_canonical_g_templates(bind: sa.Connection) -> None:
    existing_count = _scalar(
        bind,
        f"SELECT count(*) FROM task_output_templates_v2 "
        f"WHERE template_id IN ({FIXED_TASK_CODES_SQL})",
    )
    if existing_count == 10:
        return
    if existing_count != 0:
        raise RuntimeError(
            f"partial G01-G10 catalog cannot be repaired automatically: {existing_count}/10"
        )

    table = sa.table(
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
        sa.column("payload", sa.JSON()),
        sa.column("created_by", sa.String()),
        sa.column("updated_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    rows = []
    for (
        task_code,
        ops_name_zh,
        title,
        score_value,
        stage,
        priority,
        camp_day,
        deadline_event,
        why_template,
        how_summary,
        completion_standard,
        benefit,
    ) in CANONICAL_G_TASKS:
        payload = {
            "template_id": task_code,
            "template_version": 1,
            "status": "PUBLISHED",
            "revision": 1,
            "output_type": "TEACHER_TASK",
            "audience": "TEACHER",
            "owner": "TIT_GROWTH_OPS",
            "execution_owner": "TEACHER_APP",
            "integration_mode": "INBOUND_STATUS_ONLY",
            "category": "MANDATORY_GROWTH",
            "dimension": "NEW_TEACHER_TASK",
            "stage": stage,
            "ops_name_zh": ops_name_zh,
            "content_locale": "en",
            "title": title,
            "why_template": why_template,
            "how_summary": how_summary,
            "completion_standard": completion_standard,
            "benefit": benefit,
            "help_ref": "teacher-support://task-help",
            "priority": priority,
            "due_rule": {
                "type": "CAMP_DAY_OR_EVENT_DEADLINE",
                "camp_day": camp_day,
                "event": deadline_event,
                "fallback_hours": 168,
            },
            "appeal_mode": "HUMAN_REVIEW",
            "external_task_template_code": f"TIT.{task_code}",
            "action_url": None,
            "accepted_callback_statuses": ["COMPLETED"],
            "score_type": "FIXED",
            "score_value": score_value,
            "source_mode": "MOCK",
            "source_refs": ["contracts/教师端共享任务表契约.md"],
            "created_by": "SYSTEM_MIGRATION_20260722",
            "updated_by": "SYSTEM_MIGRATION_20260722",
            "created_at": now_iso,
            "updated_at": now_iso,
            "published_by": "SYSTEM_MIGRATION_20260722",
            "published_at": now_iso,
        }
        rows.append(
            {
                "row_id": f"{task_code}:v1",
                "template_id": task_code,
                "template_version": 1,
                "status": "PUBLISHED",
                "revision": 1,
                "output_type": "TEACHER_TASK",
                "execution_owner": "TEACHER_APP",
                "integration_mode": "INBOUND_STATUS_ONLY",
                "external_task_template_code": f"TIT.{task_code}",
                "source_mode": "MOCK",
                "payload": payload,
                "created_by": "SYSTEM_MIGRATION_20260722",
                "updated_by": "SYSTEM_MIGRATION_20260722",
                "created_at": now,
                "updated_at": now,
            }
        )
    bind.execute(table.insert(), rows)


def _g_template_fingerprints(bind: sa.Connection) -> dict[str, str]:
    rows = bind.execute(
        sa.text(
            f"""
            SELECT row_id,
                   encode(
                       sha256(convert_to(to_jsonb(t)::text, 'UTF8')),
                       'hex'
                   ) AS fingerprint
            FROM task_output_templates_v2 AS t
            WHERE template_id IN ({FIXED_TASK_CODES_SQL})
            ORDER BY row_id
            """
        )
    ).mappings()
    return {str(row["row_id"]): str(row["fingerprint"]) for row in rows}


def _validate_source_catalog(bind: sa.Connection) -> dict[str, str]:
    bind.exec_driver_sql(
        f"""
            DO $validate$
            DECLARE
                invalid_fixed_count integer;
                untrusted_fixed_count integer;
            BEGIN
                IF (
                    SELECT count(*)
                    FROM task_output_templates_v2
                    WHERE template_id IN ({FIXED_TASK_CODES_SQL})
                ) <> 10 OR EXISTS (
                    SELECT 1
                    FROM task_output_templates_v2
                    WHERE template_id IN ({FIXED_TASK_CODES_SQL})
                      AND (
                          template_version <> 1
                          OR row_id <> template_id || ':v1'
                          OR status <> 'PUBLISHED'
                          OR integration_mode <> 'INBOUND_STATUS_ONLY'
                      )
                ) OR EXISTS (
                    SELECT expected.task_code
                    FROM (
                        VALUES
                            ('G01'), ('G02'), ('G03'), ('G04'), ('G05'),
                            ('G06'), ('G07'), ('G08'), ('G09'), ('G10')
                    ) AS expected(task_code)
                    LEFT JOIN task_output_templates_v2 AS template
                      ON template.template_id = expected.task_code
                     AND template.template_version = 1
                    WHERE template.row_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'shared task migration requires exactly one published INBOUND_STATUS_ONLY v1 row for each G01-G10'
                        USING ERRCODE = '23514';
                END IF;

                SELECT count(*)
                INTO invalid_fixed_count
                FROM fixed_task_instances_v2 AS instance
                LEFT JOIN task_output_templates_v2 AS template
                  ON template.row_id = instance.template_row_id
                WHERE instance.task_code NOT IN ({FIXED_TASK_CODES_SQL})
                   OR instance.latest_status <> 'COMPLETED'
                   OR instance.template_version <> 1
                   OR template.row_id IS NULL
                   OR template.template_id <> instance.task_code
                   OR template.template_version <> 1
                   OR template.status <> 'PUBLISHED'
                   OR template.integration_mode <> 'INBOUND_STATUS_ONLY'
                   OR nullif(btrim(template.payload->>'why_template'), '') IS NULL
                   OR template.payload->>'priority' NOT IN ('P0', 'P1', 'P2', 'P3');

                IF invalid_fixed_count > 0 THEN
                    RAISE EXCEPTION
                        'shared task migration found %% fixed-task rows whose G:v1 template or COMPLETED semantics are invalid',
                        invalid_fixed_count
                        USING ERRCODE = '23514';
                END IF;

                SELECT count(*)
                INTO untrusted_fixed_count
                FROM fixed_task_instances_v2 AS instance
                WHERE COALESCE(instance.payload->>'source_mode', '')
                          NOT IN ({SOURCE_MODES_SQL})
                  AND upper(instance.source_system)
                          NOT IN ('TEACHER_APP', 'TIT_TEACHER_APP');

                IF untrusted_fixed_count > 0 THEN
                    RAISE EXCEPTION
                        'shared task migration found %% fixed-task rows without a trusted source_mode or teacher-app source_system',
                        untrusted_fixed_count
                        USING ERRCODE = '23514';
                END IF;
            END
            $validate$;
        """
    )
    fingerprints = _g_template_fingerprints(bind)
    if set(fingerprints) != {f"{code}:v1" for code in FIXED_TASK_CODES}:
        raise RuntimeError("G01-G10 template row identifiers are not the approved v1 set")
    return fingerprints


def _clear_retired_task_data(bind: sa.Connection) -> None:
    # Remove transport/callback facts first.  Existing audit rows and every
    # score entry are intentionally outside this cleanup.
    bind.exec_driver_sql(
        """
            DELETE FROM notification_events;
            DELETE FROM notifications;
            DELETE FROM task_runtime_events;
            DELETE FROM task_executions;
            DELETE FROM provider_calls;

            DELETE FROM ops_decisions
            WHERE case_id IN (
                SELECT case_id FROM ops_cases WHERE task_id IS NOT NULL
            );
            DELETE FROM ops_cases WHERE task_id IS NOT NULL;

            DELETE FROM outbox_events AS event
            WHERE event.aggregate_type IN (
                    'TASK', 'TASK_TEMPLATE', 'TASK_PUBLICATION',
                    'TRIGGER_POLICY', 'OUTBOUND_OUTPUT'
                  )
               ;

            DELETE FROM outbound_outputs
            WHERE task_id IS NOT NULL
               OR output_type = 'TEACHER_TASK'
               OR source_type = 'TRIGGER_POLICY_V2'
               OR payload->>'is_preview' = 'true';

            DELETE FROM task_status_callbacks_v2;
            DELETE FROM task_publications_v2;
            DELETE FROM trigger_policies_v2;

            DELETE FROM idempotency_records
            WHERE scope IN (
                    'TASK_DEDUPE',
                    'COMMAND_KEY',
                    'COMMAND_EVENT',
                    'NOTIFICATION_EVENT',
                    'PROVIDER_EVENT',
                    'TRIGGER_OUTPUT_V2_PREVIEW',
                    'TRIGGER_OUTPUT_V2_CREATE',
                    'TRIGGER_POLICY_V2_CREATE',
                    'TRIGGER_POLICY_V2_COPY',
                    'TASK_SCORE_V2_TEMPLATE_CAMP'
                  )
               OR (
                    scope IN ('TASK_TEMPLATE_V2_CREATE', 'TASK_TEMPLATE_V2_COPY')
                    AND COALESCE(resource_id, '') NOT IN (
                        SELECT row_id
                        FROM task_output_templates_v2
                        WHERE template_id IN (
                            'G01', 'G02', 'G03', 'G04', 'G05',
                            'G06', 'G07', 'G08', 'G09', 'G10'
                        )
                          AND template_version = 1
                    )
                  );
        """
    )


def _replace_assignment_table() -> None:
    op.drop_constraint(
        "task_executions_task_id_fkey",
        "task_executions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "task_runtime_events_task_id_fkey",
        "task_runtime_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "notifications_task_id_fkey",
        "notifications",
        type_="foreignkey",
    )
    op.drop_table("task_assignments")

    op.create_table(
        "task_assignments",
        sa.Column(
            "assignment_id",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("task_code", sa.String(length=64), nullable=False),
        sa.Column("template_version_id", sa.String(length=160), nullable=False),
        sa.Column("task_kind", sa.String(length=32), nullable=False),
        sa.Column("creator_system", sa.String(length=32), nullable=False),
        sa.Column("trigger_evaluation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'ASSIGNED'"),
        ),
        sa.Column("priority", sa.String(length=4), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone_used", sa.String(length=64), nullable=True),
        sa.Column("timezone_source", sa.String(length=32), nullable=True),
        sa.Column("timezone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason_code", sa.String(length=128), nullable=True),
        sa.Column("result_code", sa.String(length=128), nullable=True),
        sa.Column("source_mode", sa.String(length=24), nullable=False),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("CURRENT_USER"),
        ),
        sa.Column(
            "updated_by",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("CURRENT_USER"),
        ),
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "task_kind IN ('FIXED_GROWTH', 'PERSONALIZED_IMPROVEMENT')",
            name="ck_task_assignment_kind",
        ),
        sa.CheckConstraint(
            "creator_system IN ('TEACHER_APP', 'TRIGGER_CENTER')",
            name="ck_task_assignment_creator",
        ),
        sa.CheckConstraint(
            "status IN ('ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'SUBMITTED', "
            "'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')",
            name="ck_task_assignment_status",
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_task_assignment_priority",
        ),
        sa.CheckConstraint(
            f"source_mode IN ({SOURCE_MODES_SQL})",
            name="ck_task_assignment_source_mode",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_task_assignment_row_version",
        ),
        sa.CheckConstraint(
            f"((task_code IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'FIXED_GROWTH' AND creator_system = 'TEACHER_APP') OR "
            f"(task_code NOT IN ({FIXED_TASK_CODES_SQL}) "
            "AND task_kind = 'PERSONALIZED_IMPROVEMENT' "
            "AND creator_system = 'TRIGGER_CENTER'))",
            name="ck_task_assignment_owner_consistency",
        ),
        sa.CheckConstraint(
            "task_kind <> 'FIXED_GROWTH' OR "
            "dedupe_key = 'fixed:' || teacher_id || ':' || task_code",
            name="ck_task_assignment_fixed_dedupe",
        ),
        sa.CheckConstraint(
            "(due_at IS NULL AND timezone_used IS NULL AND timezone_source IS NULL "
            "AND timezone_verified_at IS NULL) OR "
            "(due_at IS NOT NULL AND timezone_used IS NOT NULL AND timezone_source IS NOT NULL "
            "AND timezone_verified_at IS NOT NULL)",
            name="ck_task_assignment_due_timezone",
        ),
        sa.CheckConstraint(
            "result_code IS NULL",
            name="ck_task_assignment_result_code",
        ),
        sa.CheckConstraint(
            "status NOT IN ('FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED') "
            "OR status_reason_code IS NOT NULL",
            name="ck_task_assignment_required_reason",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="ck_task_assignment_completed_at",
        ),
        sa.CheckConstraint(
            "btrim(why) <> '' AND btrim(dedupe_key) <> '' "
            "AND btrim(created_by) <> '' AND btrim(updated_by) <> ''",
            name="ck_task_assignment_required_text",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.teacher_id"],
            name="task_assignments_teacher_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["task_output_templates_v2.row_id"],
            name="task_assignments_template_version_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint("dedupe_key", name="uq_task_assignment_dedupe"),
    )
    op.create_index(
        "uq_task_assignment_fixed_teacher_task",
        "task_assignments",
        ["teacher_id", "task_code"],
        unique=True,
        postgresql_where=sa.text("task_kind = 'FIXED_GROWTH'"),
    )
    op.create_index(
        "ix_task_assignment_teacher_status_priority",
        "task_assignments",
        ["teacher_id", "status", "priority"],
    )
    op.create_index(
        "ix_task_assignment_due_at",
        "task_assignments",
        ["due_at"],
    )
    op.create_index(
        "ix_task_assignment_template_version",
        "task_assignments",
        ["template_version_id"],
    )

    op.create_foreign_key(
        "task_executions_task_id_fkey",
        "task_executions",
        "task_assignments",
        ["task_id"],
        ["assignment_id"],
    )
    op.create_foreign_key(
        "task_runtime_events_task_id_fkey",
        "task_runtime_events",
        "task_assignments",
        ["task_id"],
        ["assignment_id"],
    )
    op.create_foreign_key(
        "notifications_task_id_fkey",
        "notifications",
        "task_assignments",
        ["task_id"],
        ["assignment_id"],
    )


def _migrate_fixed_tasks(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        f"""
            WITH ranked AS (
                SELECT instance.*,
                       template.payload AS template_payload,
                       template.source_mode AS template_source_mode,
                       row_number() OVER (
                           PARTITION BY instance.teacher_id, instance.task_code
                           ORDER BY instance.completed_at,
                                    instance.created_at,
                                    instance.fixed_task_instance_id
                       ) AS canonical_rank
                FROM fixed_task_instances_v2 AS instance
                JOIN task_output_templates_v2 AS template
                  ON template.row_id = instance.template_row_id
                WHERE instance.task_code IN ({FIXED_TASK_CODES_SQL})
                  AND instance.latest_status = 'COMPLETED'
                  AND instance.template_version = 1
            )
            INSERT INTO task_assignments (
                assignment_id,
                teacher_id,
                task_code,
                template_version_id,
                task_kind,
                creator_system,
                trigger_evaluation_id,
                status,
                priority,
                why,
                due_at,
                timezone_used,
                timezone_source,
                timezone_verified_at,
                status_reason_code,
                result_code,
                source_mode,
                dedupe_key,
                created_by,
                updated_by,
                row_version,
                assigned_at,
                status_changed_at,
                completed_at,
                created_at,
                updated_at
            )
            SELECT
                fixed_task_instance_id,
                teacher_id,
                task_code,
                template_row_id,
                'FIXED_GROWTH',
                'TEACHER_APP',
                NULL,
                'COMPLETED',
                template_payload->>'priority',
                template_payload->>'why_template',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                CASE
                    WHEN payload->>'source_mode' IN ({SOURCE_MODES_SQL})
                        THEN payload->>'source_mode'
                    ELSE 'REAL'
                END,
                'fixed:' || teacher_id || ':' || task_code,
                left('MIGRATION:' || source_system, 128),
                left('MIGRATION:' || source_system, 128),
                1,
                created_at,
                completed_at,
                completed_at,
                created_at,
                updated_at
            FROM ranked
            WHERE canonical_rank = 1;

            WITH ranked AS (
                SELECT instance.*,
                       first_value(instance.fixed_task_instance_id) OVER (
                           PARTITION BY instance.teacher_id, instance.task_code
                           ORDER BY instance.completed_at,
                                    instance.created_at,
                                    instance.fixed_task_instance_id
                       ) AS canonical_assignment_id,
                       row_number() OVER (
                           PARTITION BY instance.teacher_id, instance.task_code
                           ORDER BY instance.completed_at,
                                    instance.created_at,
                                    instance.fixed_task_instance_id
                       ) AS canonical_rank
                FROM fixed_task_instances_v2 AS instance
            ), archived AS (
                SELECT
                    ranked.*,
                    jsonb_build_object(
                        'schema_version', 'shared_task_fixed_migration.v1',
                        'legacy_table', 'fixed_task_instances_v2',
                        'legacy_id', fixed_task_instance_id,
                        'source_ref', 'fixed_task_instances_v2:' || fixed_task_instance_id,
                        'canonical_assignment_id', canonical_assignment_id,
                        'is_canonical', canonical_rank = 1,
                        'source_system', source_system,
                        'external_task_id', external_task_id,
                        'task_code', task_code,
                        'legacy_status', latest_status,
                        'legacy_sequence', latest_sequence,
                        'legacy_payload', payload,
                        'legacy_events', COALESCE(
                            (
                                SELECT jsonb_agg(
                                    jsonb_build_object(
                                        'provider_event_id', event.provider_event_id,
                                        'status', event.status,
                                        'occurred_at', event.occurred_at,
                                        'sequence', event.sequence,
                                        'result_code', event.result_code,
                                        'result_version', event.result_version,
                                        'signature_sha256', event.signature_sha256,
                                        'payload_sha256', event.payload_sha256,
                                        'payload', event.payload
                                    )
                                    ORDER BY event.sequence, event.provider_event_id
                                )
                                FROM fixed_task_status_events_v2 AS event
                                WHERE event.fixed_task_instance_id = ranked.fixed_task_instance_id
                            ),
                            '[]'::jsonb
                        )
                    ) AS archive_payload
                FROM ranked
            )
            INSERT INTO audit_events (
                event_id,
                event_type,
                teacher_id,
                task_id,
                case_id,
                occurred_at,
                actor_type,
                payload_hash,
                payload
            )
            SELECT
                'TA-MIG-' || encode(
                    sha256(convert_to(fixed_task_instance_id, 'UTF8')),
                    'hex'
                ),
                CASE
                    WHEN canonical_rank = 1
                        THEN 'task.fixed_legacy_migrated.shared.v1'
                    ELSE 'task.fixed_legacy_duplicate_archived.shared.v1'
                END,
                teacher_id,
                canonical_assignment_id,
                NULL,
                clock_timestamp(),
                'SYSTEM',
                encode(
                    sha256(convert_to(archive_payload::text, 'UTF8')),
                    'hex'
                ),
                archive_payload
            FROM archived;

            DELETE FROM fixed_task_status_events_v2;
            DELETE FROM fixed_task_instances_v2;
        """
    )


def _retire_old_catalog(bind: sa.Connection, fingerprints: dict[str, str]) -> None:
    bind.exec_driver_sql(
        f"""
            DELETE FROM task_templates;
            DELETE FROM task_output_templates_v2
            WHERE template_id NOT IN ({FIXED_TASK_CODES_SQL})
               OR template_version <> 1;
        """
    )
    if _g_template_fingerprints(bind) != fingerprints:
        raise RuntimeError("G01-G10 template rows changed during the shared-task migration")

    checks = {
        "approved G template rows": (
            f"SELECT count(*) FROM task_output_templates_v2 "
            f"WHERE template_id IN ({FIXED_TASK_CODES_SQL}) AND template_version = 1 "
            "AND status = 'PUBLISHED' AND integration_mode = 'INBOUND_STATUS_ONLY'"
        ),
        "legacy V1 templates": "SELECT count(*) FROM task_templates",
        "trigger policies": "SELECT count(*) FROM trigger_policies_v2",
        "task publications": "SELECT count(*) FROM task_publications_v2",
        "task callbacks": "SELECT count(*) FROM task_status_callbacks_v2",
        "legacy fixed instances": "SELECT count(*) FROM fixed_task_instances_v2",
        "legacy fixed events": "SELECT count(*) FROM fixed_task_status_events_v2",
        "legacy task executions": "SELECT count(*) FROM task_executions",
        "legacy task runtime events": "SELECT count(*) FROM task_runtime_events",
        "legacy task notifications": "SELECT count(*) FROM notifications",
        "non-fixed shared assignments": (
            "SELECT count(*) FROM task_assignments "
            "WHERE task_kind <> 'FIXED_GROWTH' OR task_code NOT IN "
            f"({FIXED_TASK_CODES_SQL})"
        ),
    }
    actual = {label: _scalar(bind, statement) for label, statement in checks.items()}
    if actual["approved G template rows"] != 10:
        raise RuntimeError(f"expected 10 approved G templates after cleanup, got {actual}")
    unexpected = {
        label: count
        for label, count in actual.items()
        if label != "approved G template rows" and count != 0
    }
    if unexpected:
        raise RuntimeError(f"retired task data remains after cleanup: {unexpected}")


def _create_assignment_guards(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        f"""
            CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write_v1()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE
                template_code text;
                template_status text;
                template_integration_mode text;
            BEGIN
                SELECT template_id, status, integration_mode
                INTO template_code, template_status, template_integration_mode
                FROM public.task_output_templates_v2
                WHERE row_id = NEW.template_version_id;

                IF NOT FOUND THEN
                    RAISE EXCEPTION 'task template version %% does not exist', NEW.template_version_id
                        USING ERRCODE = '23503';
                END IF;
                IF template_code <> NEW.task_code OR template_status <> 'PUBLISHED' THEN
                    RAISE EXCEPTION 'task %%, template %%, and published status are inconsistent',
                        NEW.task_code, NEW.template_version_id
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.task_kind = 'FIXED_GROWTH'
                   AND template_integration_mode <> 'INBOUND_STATUS_ONLY' THEN
                    RAISE EXCEPTION 'fixed task %% must use an inbound-only template', NEW.task_code
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.task_kind = 'PERSONALIZED_IMPROVEMENT'
                   AND template_integration_mode <> 'OUTBOUND_MANAGED' THEN
                    RAISE EXCEPTION 'personalized task %% must use a trigger-center template', NEW.task_code
                        USING ERRCODE = '23514';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    NEW.row_version := 1;
                    NEW.updated_at := clock_timestamp();
                    IF NEW.status <> 'ASSIGNED' THEN
                        RAISE EXCEPTION 'new task assignments must start at ASSIGNED'
                            USING ERRCODE = '23514';
                    END IF;
                    IF current_user = 'tit_teacher_crud' THEN
                        IF NEW.task_kind <> 'FIXED_GROWTH'
                           OR NEW.creator_system <> 'TEACHER_APP'
                           OR NEW.task_code NOT IN ({FIXED_TASK_CODES_SQL}) THEN
                            RAISE EXCEPTION 'teacher app may create G01-G10 fixed tasks only'
                                USING ERRCODE = '42501';
                        END IF;
                        NEW.created_by := current_user;
                        NEW.updated_by := current_user;
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status IN ('COMPLETED', 'EXPIRED', 'WAIVED', 'CANCELLED') THEN
                    RAISE EXCEPTION 'terminal task assignment %% is immutable', OLD.assignment_id
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
                   OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                   OR NEW.task_code IS DISTINCT FROM OLD.task_code
                   OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
                   OR NEW.task_kind IS DISTINCT FROM OLD.task_kind
                   OR NEW.creator_system IS DISTINCT FROM OLD.creator_system
                   OR NEW.trigger_evaluation_id IS DISTINCT FROM OLD.trigger_evaluation_id
                   OR NEW.why IS DISTINCT FROM OLD.why
                   OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
                   OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'immutable task assignment identity/content fields cannot change'
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.row_version <> OLD.row_version THEN
                    RAISE EXCEPTION 'stale or caller-modified row_version for assignment %%', OLD.assignment_id
                        USING ERRCODE = '40001';
                END IF;

                IF current_user = 'tit_teacher_crud' THEN
                    IF NEW.status IN ('WAIVED', 'CANCELLED')
                       AND NEW.status IS DISTINCT FROM OLD.status THEN
                        RAISE EXCEPTION 'teacher app cannot waive or cancel assignments'
                            USING ERRCODE = '42501';
                    END IF;
                    NEW.updated_by := current_user;
                END IF;

                IF NEW.status IS DISTINCT FROM OLD.status THEN
                    IF NEW.status_changed_at IS NULL
                       OR NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at
                       OR NEW.status_changed_at < OLD.status_changed_at THEN
                        RAISE EXCEPTION 'status change requires a monotonic status_changed_at'
                            USING ERRCODE = '23514';
                    END IF;

                    IF NOT (
                        (OLD.status = 'ASSIGNED' AND NEW.status IN (
                            'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                            'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                        )) OR
                        (OLD.status = 'VIEWED' AND NEW.status IN (
                            'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                            'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                        )) OR
                        (OLD.status = 'IN_PROGRESS' AND NEW.status IN (
                            'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                            'EXPIRED', 'WAIVED', 'CANCELLED'
                        )) OR
                        (OLD.status = 'SUBMITTED' AND NEW.status IN (
                            'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                            'EXPIRED', 'WAIVED', 'CANCELLED'
                        )) OR
                        (OLD.status = 'UNDER_REVIEW' AND NEW.status IN (
                            'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                        )) OR
                        (OLD.status = 'FAILED' AND NEW.status IN (
                            'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED'
                        ))
                    ) THEN
                        RAISE EXCEPTION 'invalid task status transition: %% -> %%', OLD.status, NEW.status
                            USING ERRCODE = '23514';
                    END IF;

                    IF NEW.status = 'COMPLETED' THEN
                        NEW.completed_at := COALESCE(
                            NEW.completed_at,
                            NEW.status_changed_at,
                            clock_timestamp()
                        );
                    END IF;
                ELSIF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
                    RAISE EXCEPTION 'status_changed_at cannot change without a status transition'
                        USING ERRCODE = '23514';
                END IF;

                IF OLD.completed_at IS NOT NULL
                   AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                    RAISE EXCEPTION 'completed_at is immutable once set'
                        USING ERRCODE = '23514';
                END IF;

                NEW.row_version := OLD.row_version + 1;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END
            $function$;

            CREATE TRIGGER trg_task_assignment_write_v1
            BEFORE INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW
            EXECUTE FUNCTION public.enforce_task_assignment_write_v1();

            CREATE OR REPLACE FUNCTION public.reject_task_assignment_delete_v1()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $function$
            BEGIN
                RAISE EXCEPTION 'task assignment %% cannot be deleted', OLD.assignment_id
                    USING ERRCODE = '42501';
            END
            $function$;

            CREATE TRIGGER trg_task_assignment_reject_delete_v1
            BEFORE DELETE ON public.task_assignments
            FOR EACH ROW
            EXECUTE FUNCTION public.reject_task_assignment_delete_v1();

            CREATE OR REPLACE FUNCTION public.audit_task_assignment_write_v1()
            RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE
                actor_name text := COALESCE(
                    NULLIF(current_setting('role', true), 'none'),
                    session_user
                );
                audit_payload jsonb;
                internal_payload jsonb;
                audit_event_id text;
                internal_event_id text;
            BEGIN
                audit_payload := jsonb_build_object(
                    'schema_version', 'shared_task_assignment_audit.v1',
                    'assignment_id', NEW.assignment_id,
                    'operation', TG_OP,
                    'db_actor', actor_name,
                    'updated_by', NEW.updated_by,
                    'before', CASE
                        WHEN TG_OP = 'UPDATE' THEN jsonb_build_object(
                            'status', OLD.status,
                            'status_reason_code', OLD.status_reason_code,
                            'result_code', OLD.result_code,
                            'completed_at', OLD.completed_at,
                            'row_version', OLD.row_version
                        )
                        ELSE NULL
                    END,
                    'after', jsonb_build_object(
                        'status', NEW.status,
                        'status_reason_code', NEW.status_reason_code,
                        'result_code', NEW.result_code,
                        'completed_at', NEW.completed_at,
                        'row_version', NEW.row_version
                    )
                );
                audit_event_id := 'TA-AUD-' || encode(
                    sha256(convert_to(
                        NEW.assignment_id || ':' || NEW.row_version::text || ':audit',
                        'UTF8'
                    )),
                    'hex'
                );

                INSERT INTO public.audit_events (
                    event_id,
                    event_type,
                    teacher_id,
                    task_id,
                    case_id,
                    occurred_at,
                    actor_type,
                    payload_hash,
                    payload
                ) VALUES (
                    audit_event_id,
                    CASE
                        WHEN TG_OP = 'INSERT' THEN 'task.assignment.created.shared.v1'
                        ELSE 'task.assignment.updated.shared.v1'
                    END,
                    NEW.teacher_id,
                    NEW.assignment_id,
                    NULL,
                    clock_timestamp(),
                    CASE
                        WHEN actor_name = 'tit_teacher_crud' THEN 'TEACHER_APP'
                        ELSE 'SYSTEM'
                    END,
                    encode(sha256(convert_to(audit_payload::text, 'UTF8')), 'hex'),
                    audit_payload
                );

                IF TG_OP = 'UPDATE' AND (
                    NEW.status IS DISTINCT FROM OLD.status
                    OR NEW.status_reason_code IS DISTINCT FROM OLD.status_reason_code
                    OR NEW.result_code IS DISTINCT FROM OLD.result_code
                    OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
                ) THEN
                    internal_payload := jsonb_build_object(
                        'schema_version', 'task_assignment_changed.shared.v1',
                        'assignment_id', NEW.assignment_id,
                        'teacher_id', NEW.teacher_id,
                        'task_code', NEW.task_code,
                        'task_kind', NEW.task_kind,
                        'from_status', OLD.status,
                        'to_status', NEW.status,
                        'status_reason_code', NEW.status_reason_code,
                        'result_code', NEW.result_code,
                        'completed_at', NEW.completed_at,
                        'row_version', NEW.row_version
                    );
                    internal_event_id := 'TA-OUT-' || encode(
                        sha256(convert_to(
                            NEW.assignment_id || ':' || NEW.row_version::text || ':outbox',
                            'UTF8'
                        )),
                        'hex'
                    );
                    INSERT INTO public.outbox_events (
                        outbox_id,
                        event_id,
                        aggregate_type,
                        aggregate_id,
                        event_type,
                        payload,
                        status,
                        available_at,
                        attempt_count,
                        last_error,
                        created_at,
                        published_at
                    ) VALUES (
                        internal_event_id,
                        internal_event_id,
                        'TASK_ASSIGNMENT',
                        NEW.assignment_id,
                        'task.assignment_changed.shared.v1',
                        internal_payload,
                        'PENDING',
                        clock_timestamp(),
                        0,
                        NULL,
                        clock_timestamp(),
                        NULL
                    );
                END IF;
                RETURN NULL;
            END
            $function$;

            CREATE TRIGGER trg_task_assignment_audit_v1
            AFTER INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW
            EXECUTE FUNCTION public.audit_task_assignment_write_v1();

            REVOKE ALL ON FUNCTION public.enforce_task_assignment_write_v1() FROM PUBLIC;
            REVOKE ALL ON FUNCTION public.reject_task_assignment_delete_v1() FROM PUBLIC;
            REVOKE ALL ON FUNCTION public.audit_task_assignment_write_v1() FROM PUBLIC;
        """
    )


def _restrict_teacher_role(bind: sa.Connection) -> None:
    bind.exec_driver_sql(
        """
            DO $permissions$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
                ) THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM tit_teacher_crud';
                    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM tit_teacher_crud';
                    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM tit_teacher_crud';
                    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM tit_teacher_crud';
                    EXECUTE 'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                    EXECUTE 'GRANT SELECT ON TABLE public.task_assignments TO tit_teacher_crud';
                    EXECUTE 'GRANT SELECT ON TABLE public.task_output_templates_v2 TO tit_teacher_crud';
                    EXECUTE 'GRANT INSERT (
                        teacher_id,
                        task_code,
                        template_version_id,
                        task_kind,
                        creator_system,
                        priority,
                        why,
                        due_at,
                        timezone_used,
                        timezone_source,
                        timezone_verified_at,
                        source_mode,
                        dedupe_key
                    ) ON TABLE public.task_assignments TO tit_teacher_crud';
                    EXECUTE 'GRANT UPDATE (
                        status,
                        status_reason_code,
                        result_code,
                        status_changed_at,
                        completed_at,
                        updated_by
                    ) ON TABLE public.task_assignments TO tit_teacher_crud';
                END IF;
            END
            $permissions$;
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    score_count_before = _scalar(bind, "SELECT count(*) FROM score_entries")
    audit_count_before = _scalar(bind, "SELECT count(*) FROM audit_events")
    fixed_count_before = _scalar(bind, "SELECT count(*) FROM fixed_task_instances_v2")
    _ensure_canonical_g_templates(bind)
    fingerprints = _validate_source_catalog(bind)

    _clear_retired_task_data(bind)
    _replace_assignment_table()
    _migrate_fixed_tasks(bind)
    _retire_old_catalog(bind, fingerprints)
    _create_assignment_guards(bind)
    _restrict_teacher_role(bind)

    score_count_after = _scalar(bind, "SELECT count(*) FROM score_entries")
    audit_count_after = _scalar(bind, "SELECT count(*) FROM audit_events")
    if score_count_after != score_count_before:
        raise RuntimeError(
            f"score_entries changed during shared task migration: "
            f"{score_count_before} -> {score_count_after}"
        )
    if audit_count_after != audit_count_before + fixed_count_before:
        raise RuntimeError(
            "shared task migration must preserve all existing audit rows and append "
            "exactly one archive event per retired fixed-task instance"
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260722_10_shared_tasks deletes retired task transport data and cannot "
        "safely reconstruct it; restore a pre-migration database backup instead"
    )
