"""Protected materialization client for task-less COURSE outputs.

Trigger matching and output lifecycle run in the caller's COURSE transaction.
The database command owns notification/Case rows and their audit events; this
client only validates the command identity and exact result shape.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
NON_TASK_OUTPUT_REGPROCEDURE = (
    "public.reconcile_course_non_task_outputs_v2("
    "text,text,bigint,bigint,text,text)"
)
_COUNT_FIELDS = frozenset(
    {
        "notifications_created",
        "notifications_cancelled",
        "notifications_restored",
        "cases_created",
        "cases_cancelled",
        "cases_restored",
        "matches_linked",
        "unchanged",
    }
)


class DtsV2NonTaskOutputStoreError(RuntimeError):
    """The protected output command did not prove the requested transition."""


class PostgresDtsV2NonTaskOutputStore:
    def reconcile_course_outputs(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        request = _request(
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        canonical = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        value = connection.execute(
            text(
                """
                SELECT public.reconcile_course_non_task_outputs_v2(
                    :source_region,:source_appoint_id,
                    :expected_course_aggregate_revision,
                    :projection_generation,:triggering_event_id,
                    :expected_command_sha256
                )
                """
            ),
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "expected_course_aggregate_revision": aggregate_revision,
                "projection_generation": projection_generation,
                "triggering_event_id": triggering_event_id,
                "expected_command_sha256": expected_hash,
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != {
            "protocol_version",
            "command_sha256",
            "counts",
        }:
            raise DtsV2NonTaskOutputStoreError(
                "DTS_V2_NON_TASK_OUTPUT_COMMAND_RESULT_INVALID"
            )
        if value.get("protocol_version") != "course-non-task-output-v1":
            raise DtsV2NonTaskOutputStoreError(
                "DTS_V2_NON_TASK_OUTPUT_COMMAND_RESULT_INVALID"
            )
        actual_hash = value.get("command_sha256")
        if (
            not isinstance(actual_hash, str)
            or _SHA256.fullmatch(actual_hash) is None
            or actual_hash != expected_hash
        ):
            raise DtsV2NonTaskOutputStoreError(
                "DTS_V2_NON_TASK_OUTPUT_COMMAND_HASH_MISMATCH"
            )
        return _counts(value.get("counts"))


def _request(
    *,
    source_region: str,
    source_appoint_id: str,
    aggregate_revision: int,
    projection_generation: int,
    triggering_event_id: str,
) -> dict[str, Any]:
    if source_region not in {"dom", "ovs"}:
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_SOURCE_REGION_INVALID"
        )
    if (
        not isinstance(source_appoint_id, str)
        or not source_appoint_id
        or source_appoint_id.strip() != source_appoint_id
    ):
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_APPOINT_ID_INVALID"
        )
    if type(aggregate_revision) is not int or aggregate_revision < 1:
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_AGGREGATE_REVISION_INVALID"
        )
    if type(projection_generation) is not int or projection_generation < 1:
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_PROJECTION_GENERATION_INVALID"
        )
    if (
        not isinstance(triggering_event_id, str)
        or not triggering_event_id
        or triggering_event_id.strip() != triggering_event_id
    ):
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_EVENT_ID_INVALID"
        )
    return {
        "protocol_version": "course-non-task-output-command-v1",
        "source_region": source_region,
        "source_appoint_id": source_appoint_id,
        "aggregate_revision": aggregate_revision,
        "projection_generation": projection_generation,
        "triggering_event_id": triggering_event_id,
    }


def _counts(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != _COUNT_FIELDS:
        raise DtsV2NonTaskOutputStoreError(
            "DTS_V2_NON_TASK_OUTPUT_COMMAND_RESULT_INVALID"
        )
    result: dict[str, int] = {}
    for name in sorted(_COUNT_FIELDS):
        count = value.get(name)
        if type(count) is not int or count < 0:
            raise DtsV2NonTaskOutputStoreError(
                "DTS_V2_NON_TASK_OUTPUT_COMMAND_RESULT_INVALID"
            )
        result[name] = count
    return result


__all__ = [
    "NON_TASK_OUTPUT_REGPROCEDURE",
    "DtsV2NonTaskOutputStoreError",
    "PostgresDtsV2NonTaskOutputStore",
]
