"""replace Free Trial and reorder the mandatory-growth catalog

Revision ID: 20260723_16_mandatory_catalog
Revises: 20260722_15_runtime_mock_cleanup
Create Date: 2026-07-23

G01-G10 are shared teacher-side route codes, not display-only sequence values.
This migration is therefore allowed only while every existing fixed assignment
is still ASSIGNED, no fixed-task score has been settled, and no downstream
execution row references an affected assignment.

The current product deliberately keeps one visible template row per task code.
After the safety checks, G04-G08 are updated in place so existing foreign keys
remain stable while the approved current catalog changes atomically.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_16_mandatory_catalog"
down_revision: Union[str, None] = "20260722_15_runtime_mock_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TaskDefinition = dict[str, Any]


OLD_DEFINITIONS: dict[str, TaskDefinition] = {
    "G04": {
        "ops_name_zh": "首课备课",
        "title": "Lesson Preparation",
        "score_value": 3,
        "stage": "DAY_1_7",
        "priority": "P1",
        "camp_day": 7,
        "event": "BEFORE_FIRST_LESSON",
        "why_template": "Lesson-preparation routines must be completed before your first lesson and no later than Day 7.",
        "how_summary": "Complete the preparation checklist, including lesson-material review and the required pre-class setup steps.",
        "completion_standard": "Return COMPLETED only after every required checklist item is confirmed by the teacher app.",
        "benefit": "Earn 3 mandatory-growth points once and complete the preparation component of first-lesson readiness.",
    },
    "G05": {
        "ops_name_zh": "TTP 入门",
        "title": "TTP Orientation",
        "score_value": 1,
        "stage": "DAY_8_14",
        "priority": "P2",
        "camp_day": 14,
        "event": None,
        "why_template": "You have entered Day 8-14 and TTP orientation is part of the required trial-camp learning path.",
        "how_summary": "Complete the TTP orientation module and its required checklist.",
        "completion_standard": "Return COMPLETED only after all required TTP orientation items are finished.",
        "benefit": "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    },
    "G06": {
        "ops_name_zh": "ME 文化与 PARSNIP",
        "title": "ME Culture & PARSNIP",
        "score_value": 2,
        "stage": "DAY_8_14",
        "priority": "P2",
        "camp_day": 14,
        "event": None,
        "why_template": "ME culture and PARSNIP boundaries are required learning during Day 8-14.",
        "how_summary": "Study the assigned culture and PARSNIP content, then complete the configured quiz or acknowledgement.",
        "completion_standard": "Return COMPLETED only after the required content and knowledge check pass.",
        "benefit": "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    },
    "G07": {
        "ops_name_zh": "可靠性培训",
        "title": "Reliability Training",
        "score_value": 2,
        "stage": "DAY_8_14",
        "priority": "P1",
        "camp_day": 14,
        "event": None,
        "why_template": "Reliability, attendance, late and early-leave rules are required learning during Day 8-14.",
        "how_summary": "Complete the reliability training and its attendance-rule knowledge check.",
        "completion_standard": "Return COMPLETED only after the required module is finished and the knowledge check passes.",
        "benefit": "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    },
    "G08": {
        "ops_name_zh": "Free Trial 培训",
        "title": "Free Trial Training",
        "score_value": 1,
        "stage": "DAY_8_14",
        "priority": "P2",
        "camp_day": 14,
        "event": None,
        "why_template": "Free Trial training is required for the applicable trial-lesson scenario during Day 8-14.",
        "how_summary": "Open the assigned external training and complete all required Free Trial content.",
        "completion_standard": "Return COMPLETED only after the trusted training system returns the required completion tag.",
        "benefit": "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    },
}


NEW_DEFINITIONS: dict[str, TaskDefinition] = {
    "G04": {
        "ops_name_zh": "不同类型学员应对",
        "title": "How to handle different types of students",
        "score_value": 1,
        "stage": "DAY_1_7",
        "priority": "P1",
        "camp_day": 7,
        "event": None,
        "why_template": "Learning how to respond to different student types is required during Day 1-7.",
        "how_summary": "Complete the assigned learning module on recognizing and responding to different types of students.",
        "completion_standard": "Return COMPLETED only after the required module and knowledge check are completed in the teacher app.",
        "benefit": "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    },
    "G05": OLD_DEFINITIONS["G04"],
    "G06": OLD_DEFINITIONS["G05"],
    "G07": OLD_DEFINITIONS["G06"],
    "G08": OLD_DEFINITIONS["G07"],
}


def _tables() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
    task_templates = sa.table(
        "task_templates",
        sa.column("row_id", sa.String()),
        sa.column("template_id", sa.String()),
        sa.column("template_version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("integration_mode", sa.String()),
        sa.column("external_task_template_code", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    task_assignments = sa.table(
        "task_assignments",
        sa.column("assignment_id", sa.String()),
        sa.column("task_code", sa.String()),
        sa.column("task_kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("priority", sa.String()),
        sa.column("why", sa.Text()),
        sa.column("row_version", sa.Integer()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    score_entries = sa.table(
        "score_entries",
        sa.column("entry_type", sa.String()),
    )
    return task_templates, task_assignments, score_entries


def _downstream_execution_count(
    connection: sa.Connection,
    assignment_ids: list[str],
) -> int:
    if connection.dialect.name != "postgresql" or not assignment_ids:
        return 0
    references = connection.execute(
        sa.text(
            """
            SELECT source_ns.nspname AS schema_name,
                   source_table.relname AS table_name,
                   source_column.attname AS column_name
            FROM pg_constraint constraint_row
            JOIN pg_class source_table
              ON source_table.oid = constraint_row.conrelid
            JOIN pg_namespace source_ns
              ON source_ns.oid = source_table.relnamespace
            JOIN LATERAL unnest(constraint_row.conkey)
              WITH ORDINALITY AS source_key(attnum, position)
              ON TRUE
            JOIN pg_attribute source_column
              ON source_column.attrelid = source_table.oid
             AND source_column.attnum = source_key.attnum
            WHERE constraint_row.contype = 'f'
              AND constraint_row.confrelid = 'public.task_assignments'::regclass
              AND source_ns.nspname <> 'public'
            """
        )
    ).mappings()
    quote = connection.dialect.identifier_preparer.quote
    total = 0
    for reference in references:
        statement = sa.text(
            "SELECT count(*) FROM "
            f"{quote(reference['schema_name'])}.{quote(reference['table_name'])} "
            f"WHERE {quote(reference['column_name'])} IN :assignment_ids"
        ).bindparams(sa.bindparam("assignment_ids", expanding=True))
        total += int(
            connection.execute(
                statement,
                {"assignment_ids": assignment_ids},
            ).scalar_one()
        )
    return total


def _preflight(
    connection: sa.Connection,
    task_assignments: sa.TableClause,
    score_entries: sa.TableClause,
) -> list[str]:
    fixed_awards = int(
        connection.execute(
            sa.select(sa.func.count())
            .select_from(score_entries)
            .where(score_entries.c.entry_type == "FIXED_TASK_AWARD")
        ).scalar_one()
    )
    if fixed_awards:
        raise RuntimeError(
            "mandatory catalog migration blocked: fixed-task scores already exist"
        )
    affected_assignments = connection.execute(
        sa.select(
            task_assignments.c.assignment_id,
            task_assignments.c.status,
        ).where(
            task_assignments.c.task_kind == "FIXED_GROWTH",
            task_assignments.c.task_code.in_(tuple(NEW_DEFINITIONS)),
        )
    ).mappings().all()
    unsafe = [
        row["assignment_id"]
        for row in affected_assignments
        if row["status"] != "ASSIGNED"
    ]
    if unsafe:
        raise RuntimeError(
            "mandatory catalog migration blocked: affected assignments have started"
        )
    assignment_ids = [row["assignment_id"] for row in affected_assignments]
    if _downstream_execution_count(connection, assignment_ids):
        raise RuntimeError(
            "mandatory catalog migration blocked: teacher-side execution facts exist"
        )
    return assignment_ids


def _updated_payload(
    current: dict[str, Any],
    task_code: str,
    definition: TaskDefinition,
) -> dict[str, Any]:
    payload = deepcopy(current)
    payload.update(
        template_id=task_code,
        stage=definition["stage"],
        ops_name_zh=definition["ops_name_zh"],
        title=definition["title"],
        why_template=definition["why_template"],
        how_summary=definition["how_summary"],
        completion_standard=definition["completion_standard"],
        benefit=definition["benefit"],
        priority=definition["priority"],
        due_rule={
            "type": "CAMP_DAY_OR_EVENT_DEADLINE",
            "camp_day": definition["camp_day"],
            "event": definition["event"],
            "fallback_hours": 168,
        },
        external_task_template_code=f"TIT.{task_code}",
        score_type="FIXED",
        score_value=definition["score_value"],
    )
    return payload


def _apply(
    definitions: dict[str, TaskDefinition],
    expected_titles: dict[str, str],
    *,
    actor: str,
    revision_delta: int,
) -> None:
    connection = op.get_bind()
    task_templates, task_assignments, score_entries = _tables()
    assignment_ids = _preflight(connection, task_assignments, score_entries)
    rows = connection.execute(
        sa.select(
            task_templates.c.row_id,
            task_templates.c.template_id,
            task_templates.c.status,
            task_templates.c.integration_mode,
            task_templates.c.payload,
        ).where(task_templates.c.template_id.in_(tuple(definitions)))
    ).mappings().all()
    by_code = {row["template_id"]: row for row in rows}
    if set(by_code) != set(definitions):
        raise RuntimeError(
            "mandatory catalog migration requires exactly one G04-G08 template row"
        )
    for task_code, row in by_code.items():
        payload = row["payload"] or {}
        if (
            row["row_id"] != f"{task_code}:v1"
            or row["status"] != "PUBLISHED"
            or row["integration_mode"] != "INBOUND_STATUS_ONLY"
            or payload.get("title") != expected_titles[task_code]
        ):
            raise RuntimeError(
                f"mandatory catalog migration found unexpected {task_code} state"
            )

    now = datetime.now(timezone.utc)
    if connection.dialect.name == "postgresql" and assignment_ids:
        op.execute(
            "ALTER TABLE public.task_assignments "
            "DISABLE TRIGGER trg_task_assignment_write"
        )
    try:
        for task_code, definition in definitions.items():
            row = by_code[task_code]
            connection.execute(
                task_templates.update()
                .where(task_templates.c.row_id == row["row_id"])
                .values(
                    revision=task_templates.c.revision + revision_delta,
                    external_task_template_code=f"TIT.{task_code}",
                    payload=_updated_payload(
                        row["payload"] or {},
                        task_code,
                        definition,
                    ),
                    updated_by=actor,
                    updated_at=now,
                )
            )
            connection.execute(
                task_assignments.update()
                .where(
                    task_assignments.c.task_kind == "FIXED_GROWTH",
                    task_assignments.c.task_code == task_code,
                )
                .values(
                    priority=definition["priority"],
                    why=definition["why_template"],
                    row_version=task_assignments.c.row_version + 1,
                    updated_by=actor,
                    updated_at=now,
                )
            )
    finally:
        if connection.dialect.name == "postgresql" and assignment_ids:
            op.execute(
                "ALTER TABLE public.task_assignments "
                "ENABLE TRIGGER trg_task_assignment_write"
            )


def upgrade() -> None:
    _apply(
        NEW_DEFINITIONS,
        {code: definition["title"] for code, definition in OLD_DEFINITIONS.items()},
        actor="SYSTEM_MIGRATION_20260723_16",
        revision_delta=1,
    )


def downgrade() -> None:
    _apply(
        OLD_DEFINITIONS,
        {code: definition["title"] for code, definition in NEW_DEFINITIONS.items()},
        actor="SYSTEM_MIGRATION_DOWNGRADE",
        revision_delta=-1,
    )
