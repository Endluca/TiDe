"""Protected PostgreSQL command wrapper for COURSE trigger matches."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_course_trigger_plan import CourseTriggerPlanV2


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DtsV2TriggerMatchStoreError(RuntimeError):
    """The protected match command did not prove an exact plan application."""


class PostgresDtsV2TriggerMatchStore:
    def reconcile_course_matches(
        self,
        connection: Connection,
        plan: CourseTriggerPlanV2,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        if not isinstance(plan, CourseTriggerPlanV2):
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_PLAN_REQUIRED"
            )
        if type(aggregate_revision) is not int or aggregate_revision < 1:
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_AGGREGATE_REVISION_INVALID"
            )
        if type(projection_generation) is not int or projection_generation < 1:
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_PROJECTION_GENERATION_INVALID"
            )
        if not isinstance(triggering_event_id, str) or not triggering_event_id:
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_EVENT_ID_INVALID"
            )
        payload = _plan_payload(plan)
        canonical = _canonical_json(payload)
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        value = connection.execute(
            text(
                """
                SELECT public.reconcile_course_trigger_matches_v2(
                    :source_region,:source_appoint_id,
                    :expected_course_aggregate_revision,
                    :projection_generation,:triggering_event_id,
                    CAST(:match_plan AS jsonb),:expected_plan_sha256
                )
                """
            ),
            {
                "source_region": plan.source_region,
                "source_appoint_id": plan.source_appoint_id,
                "expected_course_aggregate_revision": aggregate_revision,
                "projection_generation": projection_generation,
                "triggering_event_id": triggering_event_id,
                "match_plan": canonical,
                "expected_plan_sha256": expected_hash,
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != {
            "plan_sha256",
            "counts",
        }:
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_COMMAND_RESULT_INVALID"
            )
        actual_hash = value.get("plan_sha256")
        if (
            not isinstance(actual_hash, str)
            or _SHA256.fullmatch(actual_hash) is None
            or actual_hash != expected_hash
        ):
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_COMMAND_HASH_MISMATCH"
            )
        return _counts(value.get("counts"))


def _plan_payload(plan: CourseTriggerPlanV2) -> Mapping[str, Any]:
    return {
        "protocol_version": "course-trigger-plan-v1",
        "source_region": plan.source_region,
        "source_appoint_id": plan.source_appoint_id,
        "affected_assignment_dedupe_keys": list(
            plan.affected_assignment_dedupe_keys
        ),
        "matches": [
            {
                "dedupe_key": item.dedupe_key,
                "match_kind": item.match_kind,
                "match_status": item.match_status,
                "output_type": item.output_type,
                "output_key": item.output_key,
                "teacher_id": item.teacher_id,
                "teacher_id_type": item.teacher_id_type,
                "source_region": item.source_region,
                "source_appoint_id": item.source_appoint_id,
                "participation_seq": item.participation_seq,
                "target_task_code": item.target_task_code,
                "assignment_dedupe_key": item.assignment_dedupe_key,
                "evidence_discriminator": item.evidence_discriminator,
                "evidence_discriminator_type": (
                    item.evidence_discriminator_type
                ),
                "seed_rule_rank": item.seed_rule_rank,
                "threshold_required": item.threshold_required,
                "teacher_execution_variant": (
                    item.teacher_execution_variant
                ),
                "plan_evidence": dict(item.plan_evidence),
                "plan_evidence_hash": item.plan_evidence_hash,
            }
            for item in plan.matches
        ],
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _counts(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise DtsV2TriggerMatchStoreError(
            "DTS_V2_TRIGGER_MATCH_COMMAND_RESULT_INVALID"
        )
    result: dict[str, int] = {}
    for name, count in value.items():
        if (
            not isinstance(name, str)
            or not name
            or type(count) is not int
            or count < 0
        ):
            raise DtsV2TriggerMatchStoreError(
                "DTS_V2_TRIGGER_MATCH_COMMAND_RESULT_INVALID"
            )
        result[name] = count
    return result


__all__ = [
    "DtsV2TriggerMatchStoreError",
    "PostgresDtsV2TriggerMatchStore",
]
