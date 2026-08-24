from __future__ import annotations

import hashlib
import json

import pytest

from app.dts_v2_non_task_output_store import (
    DtsV2NonTaskOutputStoreError,
    PostgresDtsV2NonTaskOutputStore,
)


_COUNTS = {
    "notifications_created": 1,
    "notifications_cancelled": 0,
    "notifications_restored": 0,
    "cases_created": 0,
    "cases_cancelled": 0,
    "cases_restored": 0,
    "matches_linked": 1,
    "unchanged": 0,
}


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        value = self.value
        if callable(value):
            value = value(params)
        if value is None:
            value = {
                "protocol_version": "course-non-task-output-v1",
                "command_sha256": params["expected_command_sha256"],
                "counts": _COUNTS,
            }
        return _Result(value)


def _call(connection):
    return PostgresDtsV2NonTaskOutputStore().reconcile_course_outputs(
        connection,
        source_region="dom",
        source_appoint_id="9001",
        aggregate_revision=7,
        projection_generation=3,
        triggering_event_id="source_wide.changed.v2:COURSE:event:7",
    )


def test_calls_only_the_protected_command_with_canonical_identity():
    connection = _Connection()

    assert _call(connection) == dict(sorted(_COUNTS.items()))

    sql, params = connection.calls[0]
    assert "reconcile_course_non_task_outputs_v2" in sql
    assert "INSERT " not in sql and "UPDATE " not in sql and "DELETE " not in sql
    basis = {
        "protocol_version": "course-non-task-output-command-v1",
        "source_region": "dom",
        "source_appoint_id": "9001",
        "aggregate_revision": 7,
        "projection_generation": 3,
        "triggering_event_id": "source_wide.changed.v2:COURSE:event:7",
    }
    expected_hash = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert params["expected_command_sha256"] == expected_hash


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("source_region", "cn", "SOURCE_REGION_INVALID"),
        ("source_appoint_id", "", "APPOINT_ID_INVALID"),
        ("aggregate_revision", 0, "AGGREGATE_REVISION_INVALID"),
        ("projection_generation", 0, "PROJECTION_GENERATION_INVALID"),
        ("triggering_event_id", "", "EVENT_ID_INVALID"),
    ],
)
def test_rejects_invalid_command_identity(field, value, code):
    kwargs = {
        "source_region": "dom",
        "source_appoint_id": "9001",
        "aggregate_revision": 7,
        "projection_generation": 3,
        "triggering_event_id": "event",
    }
    kwargs[field] = value
    with pytest.raises(DtsV2NonTaskOutputStoreError, match=code):
        PostgresDtsV2NonTaskOutputStore().reconcile_course_outputs(
            _Connection(), **kwargs
        )


def test_rejects_hash_or_count_shape_mismatch():
    with pytest.raises(DtsV2NonTaskOutputStoreError, match="HASH_MISMATCH"):
        _call(
            _Connection(
                {
                    "protocol_version": "course-non-task-output-v1",
                    "command_sha256": "0" * 64,
                    "counts": _COUNTS,
                }
            )
        )

    malformed = dict(_COUNTS)
    malformed.pop("unchanged")
    connection = _Connection(
        lambda params: {
            "protocol_version": "course-non-task-output-v1",
            "command_sha256": params["expected_command_sha256"],
            "counts": malformed,
        }
    )
    with pytest.raises(
        DtsV2NonTaskOutputStoreError,
        match="COMMAND_RESULT_INVALID",
    ):
        _call(connection)
