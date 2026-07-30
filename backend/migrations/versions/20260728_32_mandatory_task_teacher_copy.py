"""refresh the published mandatory-task teacher copy

Revision ID: 20260728_32_mandatory_task_copy
Revises: 20260728_31_task_why_evidence
Create Date: 2026-07-28

The current G01-G09 catalog keeps its codes, scores and assignment lifecycle.
Only the four teacher-facing copy fields are refreshed.  The existing
``benefit`` field is the single source of truth for "What you'll gain"; no
duplicate gain column is introduced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_32_mandatory_task_copy"
down_revision: Union[str, None] = "20260728_31_task_why_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COPY: dict[str, dict[str, str]] = {
    "G01": {
        "why_template": (
            "Complete the required profile statuses and TESOL learning evidence."
        ),
        "how_summary": (
            "Confirm Self-intro and TESOL, pass all 61 questions, complete the "
            "Essay and submit the completion proof."
        ),
        "completion_standard": (
            "Self-intro and TESOL are complete, the 61-question check reaches "
            "80%, the Essay is complete and the completion proof is submitted."
        ),
        "benefit": (
            "Your profile and required TESOL learning evidence are complete."
        ),
    },
    "G02": {
        "why_template": (
            "Learn the essential classroom and account-safety rules."
        ),
        "how_summary": (
            "Read the in-platform policy guide and complete its quiz."
        ),
        "completion_standard": (
            "The policy guide is confirmed and the quiz requirements pass."
        ),
        "benefit": "You can apply the core platform policies in class.",
    },
    "G03": {
        "why_template": (
            "Build practical responses for different learner needs."
        ),
        "how_summary": (
            "Complete the learning content configured by Jiahe."
        ),
        "completion_standard": (
            "Meet every requirement in the published Student Types configuration."
        ),
        "benefit": (
            "You can adapt your teaching to different learner types."
        ),
    },
    "G04": {
        "why_template": (
            "Complete lesson preparation and confirm that your teaching setup "
            "is ready before class."
        ),
        "how_summary": (
            "Confirm lesson preparation, check the camera, microphone and "
            "network, then take one teaching-environment photo."
        ),
        "completion_standard": (
            "Lesson preparation is confirmed, camera, microphone and network "
            "pass, and the teaching-environment photo passes AI review."
        ),
        "benefit": (
            "Your lesson preparation and pre-class setup are recorded as ready."
        ),
    },
    "G05": {
        "why_template": "Understand TTP and its key business scenarios.",
        "how_summary": (
            "Watch the in-platform TTP video and confirm every item in the "
            "learning checklist."
        ),
        "completion_standard": (
            "The TTP video is watched in full and every published checklist "
            "item is confirmed."
        ),
        "benefit": "You understand the key TTP workflow and commitments.",
    },
    "G06": {
        "why_template": "Learn cross-cultural classroom guidance.",
        "how_summary": "Complete the configured videos and quiz.",
        "completion_standard": (
            "All configured videos and quiz requirements pass."
        ),
        "benefit": "You can apply the culture guidance appropriately.",
    },
    "G07": {
        "why_template": "Strengthen dependable attendance habits.",
        "how_summary": "Complete the configured training and quiz.",
        "completion_standard": (
            "All configured training and quiz requirements pass."
        ),
        "benefit": "You have a clear reliability routine.",
    },
    "G08": {
        "why_template": "Learn the core Cocos teaching flow.",
        "how_summary": (
            "Complete the configured in-platform videos and quiz."
        ),
        "completion_standard": (
            "All configured videos and quiz requirements pass."
        ),
        "benefit": "You can prepare for a Cocos class.",
    },
    "G09": {
        "why_template": "Learn the fundamentals of SET teaching.",
        "how_summary": (
            "Watch the in-platform Mock video slot and complete the "
            "five-question Mock check."
        ),
        "completion_standard": (
            "The Mock video is watched in full and the five-question check "
            "reaches 80%."
        ),
        "benefit": "You understand the SET teaching foundation.",
    },
}


OLD_COPY: dict[str, dict[str, str]] = {
    "G01": {
        "why_template": (
            "Your trial-camp profile, self-introduction and required credentials "
            "must be completed as part of first-push readiness."
        ),
        "how_summary": (
            "Complete the self-introduction flow, register the required web-app "
            "profile, and submit all required credentials for review."
        ),
        "completion_standard": (
            "Return COMPLETED only after every required profile and credential "
            "item is present and the configured AI or human review has passed."
        ),
        "benefit": (
            "Earn 3 mandatory-growth points once. This completes one component "
            "of first-push readiness and counts toward the 30-point mandatory total."
        ),
    },
    "G02": {
        "why_template": (
            "Platform policies and compliance rules must be understood before "
            "first-push eligibility can be confirmed."
        ),
        "how_summary": (
            "Study the assigned platform-policy content and complete the "
            "required knowledge check."
        ),
        "completion_standard": (
            "Return COMPLETED only after all required policy modules are viewed "
            "and the configured quiz or acknowledgement passes."
        ),
        "benefit": (
            "Earn 2 mandatory-growth points once and complete the policy "
            "component of first-push readiness."
        ),
    },
    "G03": {
        "why_template": (
            "Learning how to respond to different student types is required "
            "during Day 1-7."
        ),
        "how_summary": (
            "Complete the assigned learning module on recognizing and "
            "responding to different types of students."
        ),
        "completion_standard": (
            "Return COMPLETED only after the required module and knowledge "
            "check are completed in the teacher app."
        ),
        "benefit": (
            "Earn 2 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
    "G04": {
        "why_template": (
            "Lesson preparation and a verified device and network check are "
            "both required before your first lesson and no later than Day 7."
        ),
        "how_summary": (
            "Complete the lesson-preparation checklist, then test your computer, "
            "network, microphone, speaker and camera in the trusted device-check entry."
        ),
        "completion_standard": (
            "Return COMPLETED only after every preparation item is confirmed "
            "and the trusted device and network check result is PASS."
        ),
        "benefit": (
            "Earn 3 mandatory-growth points once and complete both preparation "
            "and device-readiness requirements."
        ),
    },
    "G05": {
        "why_template": (
            "You have entered Day 8-14 and TTP orientation is part of the "
            "required trial-camp learning path."
        ),
        "how_summary": (
            "Complete the TTP orientation module and its required checklist."
        ),
        "completion_standard": (
            "Return COMPLETED only after all required TTP orientation items "
            "are finished."
        ),
        "benefit": (
            "Earn 3 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
    "G06": {
        "why_template": (
            "ME culture and PARSNIP boundaries are required learning during Day 8-14."
        ),
        "how_summary": (
            "Study the assigned culture and PARSNIP content, then complete the "
            "configured quiz or acknowledgement."
        ),
        "completion_standard": (
            "Return COMPLETED only after the required content and knowledge check pass."
        ),
        "benefit": (
            "Earn 4 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
    "G07": {
        "why_template": (
            "Reliability, attendance, late and early-leave rules are required "
            "learning during Day 8-14."
        ),
        "how_summary": (
            "Complete the reliability training and its attendance-rule knowledge check."
        ),
        "completion_standard": (
            "Return COMPLETED only after the required module is finished and "
            "the knowledge check passes."
        ),
        "benefit": (
            "Earn 3 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
    "G08": {
        "why_template": (
            "Cocos course training is a required Day 15-30 capability task."
        ),
        "how_summary": (
            "Complete the assigned Cocos training in the linked training system."
        ),
        "completion_standard": (
            "Return COMPLETED only after the trusted training system returns a "
            "valid Cocos completion tag."
        ),
        "benefit": (
            "Earn 5 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
    "G09": {
        "why_template": (
            "SET teaching fundamentals are required during Day 15-30."
        ),
        "how_summary": (
            "Complete the assigned SET fundamentals training in the linked training system."
        ),
        "completion_standard": (
            "Return COMPLETED only after the trusted training system returns a "
            "valid SET completion tag."
        ),
        "benefit": (
            "Earn 5 mandatory-growth points once; it counts toward the "
            "30-point mandatory total."
        ),
    },
}


_templates = sa.table(
    "task_templates",
    sa.column("template_id", sa.String()),
    sa.column("status", sa.String()),
    sa.column("revision", sa.Integer()),
    sa.column("payload", sa.JSON()),
    sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

_assignments = sa.table(
    "task_assignments",
    sa.column("assignment_id", sa.String()),
    sa.column("task_code", sa.String()),
    sa.column("task_kind", sa.String()),
    sa.column("why", sa.Text()),
    sa.column("row_version", sa.Integer()),
    sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _published_templates(
    bind: sa.engine.Connection,
) -> dict[str, Mapping[str, Any]]:
    rows = bind.execute(
        sa.select(
            _templates.c.template_id,
            _templates.c.status,
            _templates.c.revision,
            _templates.c.payload,
        ).where(_templates.c.template_id.in_(tuple(NEW_COPY)))
    ).mappings()
    result = {str(row["template_id"]): row for row in rows}
    if set(result) != set(NEW_COPY):
        missing = sorted(set(NEW_COPY) - set(result))
        raise RuntimeError(
            f"mandatory copy migration requires G01-G09; missing={missing}"
        )
    if any(row["status"] != "PUBLISHED" for row in result.values()):
        raise RuntimeError(
            "mandatory copy migration requires all G01-G09 templates to be PUBLISHED"
        )
    return result


def _apply_copy(
    copy: Mapping[str, Mapping[str, str]],
    *,
    actor: str,
    add_content_status: bool,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    templates = _published_templates(bind)
    now = datetime.now(timezone.utc)

    for task_code, fields in copy.items():
        payload = dict(templates[task_code]["payload"] or {})
        payload.update(fields)
        if add_content_status:
            payload["content_status"] = (
                "PENDING_JIAHE" if task_code == "G03" else "READY"
            )
        else:
            payload.pop("content_status", None)
        bind.execute(
            sa.update(_templates)
            .where(_templates.c.template_id == task_code)
            .values(
                payload=payload,
                revision=_templates.c.revision + revision_delta,
                updated_by=actor,
                updated_at=now,
            )
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER trg_task_assignment_write ON public.task_assignments"
        )

    for task_code, fields in copy.items():
        bind.execute(
            sa.update(_assignments)
            .where(
                _assignments.c.task_code == task_code,
                _assignments.c.task_kind == "FIXED_GROWTH",
            )
            .values(
                why=fields["why_template"],
                row_version=_assignments.c.row_version + 1,
                updated_by=actor,
                updated_at=now,
            )
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TRIGGER trg_task_assignment_write
            BEFORE INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write()
            """
        )


def upgrade() -> None:
    _apply_copy(
        NEW_COPY,
        actor="SYSTEM_MIGRATION_20260728_32",
        add_content_status=True,
        revision_delta=1,
    )


def downgrade() -> None:
    _apply_copy(
        OLD_COPY,
        actor="SYSTEM_MIGRATION_20260728_32_DOWN",
        add_content_status=False,
        revision_delta=-1,
    )
