from __future__ import annotations

from types import SimpleNamespace

from app.migration_ownership import include_owned_object


def test_teacher_owned_support_ticket_objects_are_not_alembic_targets() -> None:
    table = SimpleNamespace(
        name="teacher_support_tickets",
        schema=None,
    )
    index = SimpleNamespace(table=table)

    assert (
        include_owned_object(
            table,
            "teacher_support_tickets",
            "table",
            True,
            None,
        )
        is False
    )
    assert (
        include_owned_object(
            index,
            "teacher_support_tickets_status_deadline_idx",
            "index",
            True,
            None,
        )
        is False
    )


def test_tide_owned_objects_remain_alembic_targets() -> None:
    table = SimpleNamespace(name="task_assignments", schema=None)

    assert (
        include_owned_object(
            table,
            "task_assignments",
            "table",
            True,
            None,
        )
        is True
    )

