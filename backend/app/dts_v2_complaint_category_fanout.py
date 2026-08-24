"""Protected reverse fan-out from one DOM complaint category to COURSE keys."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COUNT_FIELDS = frozenset({"courses_seen", "courses_enqueued", "courses_noop"})


class DtsV2ComplaintCategoryFanoutError(RuntimeError):
    """Complaint-category fan-out could not prove an exact catalog revision."""


class PostgresDtsV2ComplaintCategoryFanout:
    def enqueue_linked_courses(
        self,
        connection: Connection,
        *,
        category_id: str,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        if (
            not isinstance(category_id, str)
            or not category_id
            or category_id.strip() != category_id
        ):
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_ID_INVALID"
            )
        if type(aggregate_revision) is not int or aggregate_revision < 1:
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_REVISION_INVALID"
            )
        if (
            not isinstance(triggering_event_id, str)
            or not triggering_event_id
            or triggering_event_id.strip() != triggering_event_id
        ):
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_EVENT_ID_INVALID"
            )
        request: dict[str, Any] = {
            "protocol_version": "complaint-category-course-fanout-command-v1",
            "category_id": category_id,
            "aggregate_revision": aggregate_revision,
            "triggering_event_id": triggering_event_id,
        }
        expected_hash = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        value = connection.execute(
            text(
                """
                SELECT public.enqueue_complaint_category_course_fanout_v2(
                    :category_id,:expected_aggregate_revision,
                    :triggering_event_id,:expected_command_sha256
                )
                """
            ),
            {
                "category_id": category_id,
                "expected_aggregate_revision": aggregate_revision,
                "triggering_event_id": triggering_event_id,
                "expected_command_sha256": expected_hash,
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != {
            "protocol_version",
            "command_sha256",
            "counts",
        }:
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_FANOUT_RESULT_INVALID"
            )
        if value.get("protocol_version") != "complaint-category-course-fanout-v1":
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_FANOUT_RESULT_INVALID"
            )
        actual_hash = value.get("command_sha256")
        if (
            not isinstance(actual_hash, str)
            or _SHA256.fullmatch(actual_hash) is None
            or actual_hash != expected_hash
        ):
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_FANOUT_HASH_MISMATCH"
            )
        counts = value.get("counts")
        if not isinstance(counts, Mapping) or set(counts) != _COUNT_FIELDS:
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_FANOUT_RESULT_INVALID"
            )
        result: dict[str, int] = {}
        for name in sorted(_COUNT_FIELDS):
            count = counts.get(name)
            if type(count) is not int or count < 0:
                raise DtsV2ComplaintCategoryFanoutError(
                    "DTS_V2_COMPLAINT_CATEGORY_FANOUT_RESULT_INVALID"
                )
            result[name] = count
        if result["courses_seen"] != (
            result["courses_enqueued"] + result["courses_noop"]
        ):
            raise DtsV2ComplaintCategoryFanoutError(
                "DTS_V2_COMPLAINT_CATEGORY_FANOUT_COUNT_MISMATCH"
            )
        return result


__all__ = [
    "DtsV2ComplaintCategoryFanoutError",
    "PostgresDtsV2ComplaintCategoryFanout",
]
