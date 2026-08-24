from __future__ import annotations

import pytest

from app.dts_v2_complaint_category_fanout import (
    DtsV2ComplaintCategoryFanoutError,
    PostgresDtsV2ComplaintCategoryFanout,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, counts=None):
        self.counts = counts or {
            "courses_seen": 2,
            "courses_enqueued": 1,
            "courses_noop": 1,
        }
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return _Result(
            {
                "protocol_version": "complaint-category-course-fanout-v1",
                "command_sha256": params["expected_command_sha256"],
                "counts": self.counts,
            }
        )


def test_calls_only_protected_cross_region_fanout_command():
    connection = _Connection()
    result = PostgresDtsV2ComplaintCategoryFanout().enqueue_linked_courses(
        connection,
        category_id="82",
        aggregate_revision=4,
        triggering_event_id="source_wide.changed.v2:COMPLAINT_CATEGORY:event:4",
    )

    assert result == {
        "courses_enqueued": 1,
        "courses_noop": 1,
        "courses_seen": 2,
    }
    sql, params = connection.calls[0]
    assert "enqueue_complaint_category_course_fanout_v2" in sql
    assert "INSERT " not in sql and "UPDATE " not in sql and "DELETE " not in sql
    assert len(params["expected_command_sha256"]) == 64


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"category_id": ""}, "CATEGORY_ID_INVALID"),
        ({"aggregate_revision": 0}, "REVISION_INVALID"),
        ({"triggering_event_id": ""}, "EVENT_ID_INVALID"),
    ],
)
def test_rejects_invalid_identity(kwargs, code):
    values = {
        "category_id": "82",
        "aggregate_revision": 4,
        "triggering_event_id": "event",
    }
    values.update(kwargs)
    with pytest.raises(DtsV2ComplaintCategoryFanoutError, match=code):
        PostgresDtsV2ComplaintCategoryFanout().enqueue_linked_courses(
            _Connection(), **values
        )


def test_rejects_inconsistent_counts():
    with pytest.raises(
        DtsV2ComplaintCategoryFanoutError,
        match="COUNT_MISMATCH",
    ):
        PostgresDtsV2ComplaintCategoryFanout().enqueue_linked_courses(
            _Connection(
                {
                    "courses_seen": 3,
                    "courses_enqueued": 1,
                    "courses_noop": 1,
                }
            ),
            category_id="82",
            aggregate_revision=4,
            triggering_event_id="event",
        )
