"""add teacher snapshot profile evidence fields

Revision ID: 20260722_11_teacher_profile
Revises: 20260722_10_shared_tasks
Create Date: 2026-07-22

The three fields are evidence only.  This migration deliberately does not
touch G01 assignments or score facts.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import re
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_11_teacher_profile"
down_revision: Union[str, None] = "20260722_10_shared_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_object(value: Any, *, field: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{field} must contain a JSON object") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{field} must contain a JSON object")
    return dict(value)


def _optional_date(value: Any, *, snapshot_id: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return date.fromisoformat(normalized)
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]", normalized):
            raise ValueError
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise RuntimeError(
            f"snapshot {snapshot_id}: first_booked_dt is not a strict ISO/Excel date"
        ) from exc


def _optional_boolean(value: Any, *, field: str, snapshot_id: str) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(int(value))
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    raise RuntimeError(
        f"snapshot {snapshot_id}: {field} must be an explicit boolean or controlled 0/1"
    )


def _profile_provenance(
    *,
    batch_id: str,
    first_booked_date: date | None,
    is_cpl_tesol: bool | None,
    is_self_introduce: bool | None,
) -> dict[str, Any]:
    return {
        "first_booked_date": {
            "source_mode": "REAL" if first_booked_date else "SOURCE_MISSING",
            "source_field": "first_booked_dt",
            "batch_id": batch_id,
            "note": (
                "Date-level first booked lesson value from the validated teacher snapshot; "
                "it does not prove that the lesson was completed."
                if first_booked_date
                else "The source workbook has no first booked lesson date for this teacher."
            ),
        },
        "is_cpl_tesol": {
            "source_mode": "REAL" if is_cpl_tesol is not None else "SOURCE_MISSING",
            "source_field": "is_cpl_tesol",
            "batch_id": batch_id,
            "note": (
                "Explicit upstream TESOL-completion evidence; it is not G01 completion."
                if is_cpl_tesol is not None
                else "The current 61-column workbook does not provide TESOL completion."
            ),
        },
        "is_self_introduce": {
            "source_mode": (
                "REAL" if is_self_introduce is not None else "SOURCE_MISSING"
            ),
            "source_field": "is_self_introduce",
            "batch_id": batch_id,
            "note": (
                "Explicit upstream self-introduction evidence; it is not G01 completion."
                if is_self_introduce is not None
                else "The current 61-column workbook does not provide self-introduction completion."
            ),
        },
    }


def upgrade() -> None:
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("first_booked_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("is_cpl_tesol", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "teacher_metric_snapshots",
        sa.Column("is_self_introduce", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_teacher_metric_snapshots_first_booked_date",
        "teacher_metric_snapshots",
        ["first_booked_date"],
        unique=False,
    )

    bind = op.get_bind()
    snapshots = sa.table(
        "teacher_metric_snapshots",
        sa.column("snapshot_id", sa.String()),
        sa.column("batch_id", sa.String()),
        sa.column("teacher_id", sa.String()),
        sa.column("raw_payload", sa.JSON()),
        sa.column("first_booked_date", sa.Date()),
        sa.column("is_cpl_tesol", sa.Boolean()),
        sa.column("is_self_introduce", sa.Boolean()),
    )
    teachers = sa.table(
        "teachers",
        sa.column("teacher_id", sa.String()),
        sa.column("source_batch_id", sa.String()),
        sa.column("payload", sa.JSON()),
    )

    rows = bind.execute(
        sa.select(
            snapshots.c.snapshot_id,
            snapshots.c.batch_id,
            snapshots.c.teacher_id,
            snapshots.c.raw_payload,
        )
    ).mappings().all()
    for row in rows:
        raw_payload = _json_object(
            row["raw_payload"], field=f"snapshot {row['snapshot_id']} raw_payload"
        )
        first_booked_date = _optional_date(
            raw_payload.get("first_booked_dt"), snapshot_id=row["snapshot_id"]
        )
        is_cpl_tesol = _optional_boolean(
            raw_payload.get("is_cpl_tesol"),
            field="is_cpl_tesol",
            snapshot_id=row["snapshot_id"],
        )
        is_self_introduce = _optional_boolean(
            raw_payload.get("is_self_introduce"),
            field="is_self_introduce",
            snapshot_id=row["snapshot_id"],
        )
        bind.execute(
            snapshots.update()
            .where(snapshots.c.snapshot_id == row["snapshot_id"])
            .values(
                first_booked_date=first_booked_date,
                is_cpl_tesol=is_cpl_tesol,
                is_self_introduce=is_self_introduce,
            )
        )

        teacher = bind.execute(
            sa.select(teachers.c.source_batch_id, teachers.c.payload).where(
                teachers.c.teacher_id == row["teacher_id"]
            )
        ).mappings().first()
        if teacher is None or teacher["source_batch_id"] != row["batch_id"]:
            continue
        teacher_payload = _json_object(
            teacher["payload"], field=f"teacher {row['teacher_id']} payload"
        )
        teacher_payload.update(
            first_booked_date=(
                first_booked_date.isoformat() if first_booked_date else None
            ),
            is_cpl_tesol=is_cpl_tesol,
            is_self_introduce=is_self_introduce,
        )
        provenance = teacher_payload.get("profile_provenance") or {}
        if not isinstance(provenance, dict):
            raise RuntimeError(
                f"teacher {row['teacher_id']} profile_provenance must be a JSON object"
            )
        provenance = dict(provenance)
        provenance.update(
            _profile_provenance(
                batch_id=row["batch_id"],
                first_booked_date=first_booked_date,
                is_cpl_tesol=is_cpl_tesol,
                is_self_introduce=is_self_introduce,
            )
        )
        teacher_payload["profile_provenance"] = provenance
        bind.execute(
            teachers.update()
            .where(teachers.c.teacher_id == row["teacher_id"])
            .values(payload=teacher_payload)
        )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_metric_snapshots_first_booked_date",
        table_name="teacher_metric_snapshots",
    )
    op.drop_column("teacher_metric_snapshots", "is_self_introduce")
    op.drop_column("teacher_metric_snapshots", "is_cpl_tesol")
    op.drop_column("teacher_metric_snapshots", "first_booked_date")
