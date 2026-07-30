"""make notification title and body English database facts

Revision ID: 20260729_33_notification_copy
Revises: 20260728_32_mandatory_task_copy
Create Date: 2026-07-29

The teacher-facing title and body are migrated in place.  Raw evidence remains
unchanged in ``payload.evidence``.  PostgreSQL also receives a constraint that
rejects blank or Chinese teacher-facing notification copy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260729_33_notification_copy"
down_revision: Union[str, None] = "20260728_32_mandatory_task_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CONSTRAINT_NAME = "ck_notifications_teacher_copy_english"

_notifications = sa.table(
    "notifications",
    sa.column("notification_id", sa.String()),
    sa.column("payload", sa.JSON()),
)


def _contains_han(value: object) -> bool:
    return bool(_HAN_CHARACTER.search(str(value or "")))


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _english_title(value: object) -> str:
    title = str(value or "").strip()
    if title and not _contains_han(title):
        return title
    return "In-Class Quality Alert"


def _english_body(value: object) -> str:
    body = str(value or "").strip()
    if body and not _contains_han(body):
        return body

    sentences: list[str] = []
    if "网络设备类投诉" in body or "网络卡顿" in body:
        sentences.append(
            "This lesson received a network or device complaint. "
            "Review the evidence and improve the class setup."
        )

    issues: list[str] = []
    if "未开摄像头" in body:
        issues.append("the camera was off")
    if "CPU 占用过高" in body or "cpu占用过高" in body:
        issues.append("high CPU usage was detected")
    if "网络延迟过高" in body:
        issues.append("high network delay was detected")
    if issues:
        sentences.append(
            "This lesson had an in-class quality issue: "
            + ", ".join(issues)
            + ". Check and improve the class setup."
        )

    if sentences:
        return " ".join(sentences)
    return (
        "An in-class quality issue was detected. "
        "Review the evidence and improve the class setup."
    )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_notifications.c.notification_id, _notifications.c.payload)
    ).mappings()
    for row in rows:
        payload = _payload(row["payload"])
        payload["title"] = _english_title(payload.get("title"))
        payload["body"] = _english_body(payload.get("body"))
        bind.execute(
            sa.update(_notifications)
            .where(
                _notifications.c.notification_id == row["notification_id"]
            )
            .values(payload=payload)
        )

    if bind.dialect.name == "postgresql":
        op.create_check_constraint(
            _CONSTRAINT_NAME,
            "notifications",
            (
                "jsonb_typeof(payload) = 'object' "
                "AND length(btrim(payload->>'title')) > 0 "
                "AND length(btrim(payload->>'body')) > 0 "
                "AND (payload->>'title') !~ "
                "'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]' "
                "AND (payload->>'body') !~ "
                "'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]'"
            ),
            schema="public",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            _CONSTRAINT_NAME,
            "notifications",
            type_="check",
            schema="public",
        )
    # The original Chinese teacher-facing copy cannot be reconstructed safely.
