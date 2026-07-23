"""make teacher-facing task reasons English

Revision ID: 20260724_20_task_why_en
Revises: 20260724_19_lesson_feedback
Create Date: 2026-07-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260724_20_task_why_en"
down_revision: Union[str, None] = "20260724_19_lesson_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_POSTGRES_UPDATE = """
UPDATE public.task_assignments
SET why = CASE task_code
        WHEN 'P-REL-MEMO' THEN
            'This task was assigned because one or more completed lessons had an unfilled Lesson Memo. Complete the Lesson Memo learning activity.'
        WHEN 'P-REL-ATTENDANCE' THEN
            'This task was assigned because one or more lessons contained an attendance signal such as absence, late arrival, or early departure. Complete the attendance training and quiz.'
        WHEN 'P-FB-NEGATIVE' THEN
            'This task was assigned because the same negative-feedback tag appeared in at least two different lessons. Complete the corresponding learning activity.'
        WHEN 'P-FB-COMPLAINT' THEN
            'This task was assigned because one or more lessons received a general complaint. Complete the corresponding complaint learning activity.'
        WHEN 'P-FB-BLACKLIST' THEN
            'This task was assigned because at least two different students blacklisted this teacher. Complete the assigned blacklist-prevention learning activity.'
        ELSE why
    END,
    updated_by = 'MIGRATION:TASK_WHY_ENGLISH',
    row_version = row_version + 1,
    updated_at = clock_timestamp()
WHERE task_kind = 'PERSONALIZED_IMPROVEMENT'
  AND why ~ U&'[\\4E00-\\9FFF]'
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Why is normally immutable. Temporarily remove only the write guard for
        # this controlled copy correction. The audit trigger remains active and
        # row_version advances; task status and completion facts do not change.
        op.execute(
            "DROP TRIGGER trg_task_assignment_write ON public.task_assignments"
        )
        op.execute(sa.text(_POSTGRES_UPDATE))
        op.execute(
            """
            CREATE TRIGGER trg_task_assignment_write
            BEFORE INSERT OR UPDATE ON public.task_assignments
            FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write()
            """
        )
        op.create_check_constraint(
            "ck_task_assignment_why_teacher_english",
            "task_assignments",
            "why !~ U&'[\\4E00-\\9FFF]'",
        )
        return

    op.execute(
        """
        UPDATE task_assignments
        SET why = CASE task_code
            WHEN 'P-REL-MEMO' THEN
                'This task was assigned because one or more completed lessons had an unfilled Lesson Memo. Complete the Lesson Memo learning activity.'
            WHEN 'P-REL-ATTENDANCE' THEN
                'This task was assigned because one or more lessons contained an attendance signal such as absence, late arrival, or early departure. Complete the attendance training and quiz.'
            WHEN 'P-FB-NEGATIVE' THEN
                'This task was assigned because the same negative-feedback tag appeared in at least two different lessons. Complete the corresponding learning activity.'
            WHEN 'P-FB-COMPLAINT' THEN
                'This task was assigned because one or more lessons received a general complaint. Complete the corresponding complaint learning activity.'
            WHEN 'P-FB-BLACKLIST' THEN
                'This task was assigned because at least two different students blacklisted this teacher. Complete the assigned blacklist-prevention learning activity.'
            ELSE why
        END
        WHERE task_kind = 'PERSONALIZED_IMPROVEMENT'
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "ck_task_assignment_why_teacher_english",
            "task_assignments",
            type_="check",
        )
    # The prior free-text reasons cannot be reconstructed safely. English copy
    # remains valid when the schema revision is downgraded.
