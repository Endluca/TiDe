"""remove Self-intro from the G01 teacher contract

Revision ID: 20260811_51_g01_tesol_only
Revises: 20260810_50_g04_sections
Create Date: 2026-08-11

G01 keeps its stable template identity, score and assignment lifecycle. This
controlled catalog update changes only the teacher-facing Why, How and
completion copy on ``G01:v1`` so that TESOL is its sole upstream status fact.
The legacy ``is_self_introduce`` source column remains available to the source
owner and operational projections, but the teacher runtime role no longer has
permission to read it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260811_51_g01_tesol_only"
down_revision: Union[str, None] = "20260810_50_g04_sections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COPY: dict[str, str] = {
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
}

NEW_COPY: dict[str, str] = {
    "why_template": "Complete the required TESOL status and learning evidence.",
    "how_summary": (
        "Confirm TESOL, pass all 61 questions, complete the Essay and submit "
        "the completion proof."
    ),
    "completion_standard": (
        "TESOL is complete, the 61-question check reaches 80%, the Essay is "
        "complete and the completion proof is submitted."
    ),
}


_templates = sa.table(
    "task_templates",
    sa.column("row_id", sa.String()),
    sa.column("template_id", sa.String()),
    sa.column("template_version", sa.Integer()),
    sa.column("status", sa.String()),
    sa.column("revision", sa.Integer()),
    sa.column("payload", sa.JSON()),
    sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


G01_STABLE_ROW_ID = "G01:v1"


UPGRADE_ALEMBIC_VERSION_WIDTH_SQL = """
ALTER TABLE public.alembic_version
ALTER COLUMN version_num TYPE varchar(64);
"""

DOWNGRADE_ALEMBIC_VERSION_WIDTH_SQL = """
ALTER TABLE public.alembic_version
ALTER COLUMN version_num TYPE varchar(32);
"""


UPGRADE_TEACHER_SOURCE_ACL_SQL = """
DO $g01_tesol_only_acl$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
    ) THEN
        REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide
        FROM tit_teacher_crud;
        REVOKE SELECT (
            tchr_id,
            is_cpl_tesol,
            is_self_introduce
        ) ON TABLE public.teacher_source_wide
        FROM tit_teacher_crud;
        GRANT SELECT (
            tchr_id,
            is_cpl_tesol
        ) ON TABLE public.teacher_source_wide
        TO tit_teacher_crud;

        IF has_table_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'SELECT'
        ) OR NOT has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'tchr_id',
            'SELECT'
        ) OR NOT has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'is_cpl_tesol',
            'SELECT'
        ) OR has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'is_self_introduce',
            'SELECT'
        ) OR has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'real_name',
            'SELECT'
        ) THEN
            RAISE EXCEPTION
                'tit_teacher_crud TESOL-only source privileges are invalid';
        END IF;
    END IF;
END
$g01_tesol_only_acl$;
"""


DOWNGRADE_TEACHER_SOURCE_ACL_SQL = """
DO $g01_tesol_only_acl_down$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
    ) THEN
        REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide
        FROM tit_teacher_crud;
        REVOKE SELECT (
            tchr_id,
            is_cpl_tesol,
            is_self_introduce
        ) ON TABLE public.teacher_source_wide
        FROM tit_teacher_crud;
        GRANT SELECT (
            tchr_id,
            is_cpl_tesol,
            is_self_introduce
        ) ON TABLE public.teacher_source_wide
        TO tit_teacher_crud;

        IF has_table_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'SELECT'
        ) OR NOT has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'tchr_id',
            'SELECT'
        ) OR NOT has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'is_cpl_tesol',
            'SELECT'
        ) OR NOT has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'is_self_introduce',
            'SELECT'
        ) OR has_column_privilege(
            'tit_teacher_crud',
            'public.teacher_source_wide',
            'real_name',
            'SELECT'
        ) THEN
            RAISE EXCEPTION
                'tit_teacher_crud restored G01 source privileges are invalid';
        END IF;
    END IF;
END
$g01_tesol_only_acl_down$;
"""


def _g01_row(bind: sa.engine.Connection) -> Mapping[str, Any]:
    rows = list(
        bind.execute(
            sa.select(
                _templates.c.row_id,
                _templates.c.template_id,
                _templates.c.template_version,
                _templates.c.status,
                _templates.c.revision,
                _templates.c.payload,
            ).where(_templates.c.row_id == G01_STABLE_ROW_ID)
        ).mappings()
    )
    if len(rows) != 1:
        raise RuntimeError(
            "G01 TESOL-only migration requires exactly one stable G01:v1 "
            "template row"
        )
    row = rows[0]
    payload = row["payload"]
    if (
        row["template_id"] != "G01"
        or row["template_version"] != 1
        or row["status"] != "PUBLISHED"
        or not isinstance(payload, Mapping)
        or payload.get("template_id") != "G01"
        or payload.get("category") != "MANDATORY_GROWTH"
        or payload.get("title") != "Profile & Credentials Completion"
        or payload.get("score_type") != "FIXED"
        or payload.get("score_value") != 3
    ):
        raise RuntimeError(
            "G01 TESOL-only migration requires the stable published "
            "three-point mandatory G01 template"
        )
    return row


def _apply_copy(
    copy: Mapping[str, str],
    *,
    expected_copy: Mapping[str, str],
    actor: str,
    revision_delta: int,
) -> None:
    bind = op.get_bind()
    row = _g01_row(bind)
    payload = dict(row["payload"] or {})
    current_copy = {field: payload.get(field) for field in expected_copy}
    if current_copy != dict(expected_copy):
        raise RuntimeError(
            "G01 TESOL-only migration found unreviewed teacher-facing copy drift"
        )

    payload.update(copy)
    result = bind.execute(
        sa.update(_templates)
        .where(
            _templates.c.row_id == G01_STABLE_ROW_ID,
            _templates.c.template_id == "G01",
            _templates.c.template_version == 1,
            _templates.c.status == "PUBLISHED",
        )
        .values(
            payload=payload,
            revision=_templates.c.revision + revision_delta,
            updated_by=actor,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        raise RuntimeError(
            "G01 TESOL-only migration did not update exactly one row"
        )


def _apply_teacher_source_acl(sql: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sql)


def _resize_alembic_version(sql: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sql)


def upgrade() -> None:
    # The following reviewed revision IDs are longer than the historical
    # varchar(32) ledger column. Resize before Alembic records rev54.
    _resize_alembic_version(UPGRADE_ALEMBIC_VERSION_WIDTH_SQL)
    _apply_copy(
        NEW_COPY,
        expected_copy=OLD_COPY,
        actor="SYSTEM_MIGRATION_20260811_51_G01_TESOL_ONLY",
        revision_delta=1,
    )
    _apply_teacher_source_acl(UPGRADE_TEACHER_SOURCE_ACL_SQL)


def downgrade() -> None:
    _apply_copy(
        OLD_COPY,
        expected_copy=NEW_COPY,
        actor="SYSTEM_MIGRATION_20260811_51_G01_TESOL_ONLY_DOWN",
        revision_delta=-1,
    )
    _apply_teacher_source_acl(DOWNGRADE_TEACHER_SOURCE_ACL_SQL)
    _resize_alembic_version(DOWNGRADE_ALEMBIC_VERSION_WIDTH_SQL)
