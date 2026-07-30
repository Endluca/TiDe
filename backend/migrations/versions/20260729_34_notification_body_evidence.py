"""persist key notification evidence in the English body

Revision ID: 20260729_34_body_evidence
Revises: 20260729_33_notification_copy
Create Date: 2026-07-29

The display layer reads ``payload.body`` directly, so the compact English
evidence clause is stored in that same value.  Raw evidence remains available
in ``payload.evidence`` for audit and structured consumption.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260729_34_body_evidence"
down_revision: Union[str, None] = "20260729_33_notification_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT_NAME = "ck_notifications_teacher_copy_english"
_ANOMALIES_EN = {
    "未开摄像头": "camera off",
    "CPU 占用过高": "high CPU usage",
    "cpu占用过高": "high CPU usage",
    "网络延迟过高": "high network delay",
}
_COMPLAINTS_EN = {
    "网络卡顿": "Unstable Network",
    "麦克风没有声音/卡顿": "Microphone Audio Missing or Unstable",
}

_notifications = sa.table(
    "notifications",
    sa.column("notification_id", sa.String()),
    sa.column("payload", sa.JSON()),
)


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _evidence_clause(value: object) -> str:
    evidence = dict(value) if isinstance(value, Mapping) else {}
    parts: list[str] = []

    lesson_ids = _string_list(evidence.get("lesson_ids"))
    lesson_id = str(evidence.get("lesson_id") or "").strip()
    if not lesson_ids and lesson_id:
        lesson_ids = [lesson_id]
    if lesson_ids:
        parts.append("Lesson IDs: " + ", ".join(lesson_ids))

    complaint = str(evidence.get("complaint_level3") or "").strip()
    if complaint:
        parts.append(
            "complaint: "
            + _COMPLAINTS_EN.get(complaint, "complaint category recorded")
        )

    anomalies = [
        _ANOMALIES_EN.get(item, "in-class quality anomaly")
        for item in _string_list(evidence.get("anomalies"))
    ]
    anomalies = list(dict.fromkeys(anomalies))
    if anomalies:
        parts.append("quality anomalies: " + ", ".join(anomalies))

    if not parts:
        parts.append("notification source recorded")
    return "; ".join(parts)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_notifications.c.notification_id, _notifications.c.payload)
    ).mappings()
    for row in rows:
        payload = _payload(row["payload"])
        body = str(payload.get("body") or "").strip()
        if "Evidence:" not in body:
            payload["body"] = (
                f"{body.rstrip()} Evidence: "
                f"{_evidence_clause(payload.get('evidence'))}."
            )
            bind.execute(
                sa.update(_notifications)
                .where(
                    _notifications.c.notification_id
                    == row["notification_id"]
                )
                .values(payload=payload)
            )

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            _CONSTRAINT_NAME,
            "notifications",
            type_="check",
            schema="public",
        )
        op.create_check_constraint(
            _CONSTRAINT_NAME,
            "notifications",
            (
                "jsonb_typeof(payload) = 'object' "
                "AND length(btrim(payload->>'title')) > 0 "
                "AND length(btrim(payload->>'body')) > 0 "
                "AND position('Evidence:' in payload->>'body') > 0 "
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
    # Evidence clauses remain because their original absence cannot be
    # reconstructed safely.
