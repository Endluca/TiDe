"""Atomic TEACHER SourceWide compatibility and score materialization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import json
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    PostgresDtsV2PrimaryTransactionGuard,
)
from .dts_v2_teacher_outbox_processor import TeacherMaterializationPlanV2


class DtsV2TeacherMaterializerError(RuntimeError):
    """A protected teacher materialization did not prove its serving row."""


class DtsV2TeacherScoreRefresher(Protocol):
    def refresh_teacher(
        self,
        connection: Connection,
        *,
        teacher_id: str,
        projection_generation: int,
    ) -> Mapping[str, int]: ...


class PostgresDtsV2TeacherMaterializer:
    """Materialize global teacher state and refresh score in the caller tx."""

    def __init__(
        self,
        *,
        score_refresher: DtsV2TeacherScoreRefresher,
        primary_guard: DtsV2PrimaryTransactionGuard | None = None,
    ) -> None:
        if not callable(getattr(score_refresher, "refresh_teacher", None)):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_SCORE_REFRESHER_REQUIRED"
            )
        self.score_refresher = score_refresher
        self.primary_guard = primary_guard or PostgresDtsV2PrimaryTransactionGuard()

    def apply_teacher_plan(
        self,
        connection: Connection,
        plan: TeacherMaterializationPlanV2,
        *,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        if not isinstance(plan, TeacherMaterializationPlanV2):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_PLAN_REQUIRED"
            )
        if not isinstance(plan.business_date_beijing, date):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_BUSINESS_DATE_INVALID"
            )
        if not isinstance(triggering_event_id, str) or not triggering_event_id:
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_EVENT_ID_INVALID"
            )
        revisions = plan.regional_revisions
        state_hashes = plan.regional_state_sha256
        if (
            set(revisions) != {"dom", "ovs"}
            or any(type(value) is not int or value < 1 for value in revisions.values())
            or set(state_hashes) != {"dom", "ovs"}
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in state_hashes.values()
            )
        ):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_REGIONAL_REVISION_INVALID"
            )
        generation = self.primary_guard.acquire(connection, component="OUTBOX")
        if generation is None:
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_PRIMARY_MODE_REQUIRED"
            )
        if type(generation) is not int or generation < 1:
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_PROJECTION_GENERATION_INVALID"
            )
        payload = {
            "v": 1,
            "teacher_id": plan.teacher_id,
            "teacher_id_type": plan.teacher_id_type,
            "regional_state_sha256": dict(state_hashes),
            "values": _json_safe(plan.projection.values),
            "first_date_evidence": dict(plan.projection.first_date_evidence),
            "metric_evidence": dict(plan.projection.metric_evidence),
        }
        raw = connection.execute(
            text(
                """
                SELECT public.materialize_teacher_source_wide_v2(
                  CAST(:payload AS jsonb),:dom_revision,:ovs_revision,
                  :projection_generation,:event_id
                )
                """
            ),
            {
                "payload": json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ),
                "dom_revision": revisions["dom"],
                "ovs_revision": revisions["ovs"],
                "projection_generation": generation,
                "event_id": triggering_event_id,
            },
        ).scalar_one()
        counts = _counts(raw)
        readback = connection.execute(
            text(
                """
                SELECT v2_dom_aggregate_revision,v2_ovs_aggregate_revision,
                       v2_projection_generation,v2_row_version
                FROM public.teacher_source_wide
                WHERE tchr_id=:teacher_id
                """
            ),
            {"teacher_id": plan.teacher_id},
        ).mappings().one_or_none()
        if (
            readback is None
            or readback.get("v2_dom_aggregate_revision") != revisions["dom"]
            or readback.get("v2_ovs_aggregate_revision") != revisions["ovs"]
            or readback.get("v2_projection_generation") != generation
            or type(readback.get("v2_row_version")) is not int
            or readback["v2_row_version"] < 1
        ):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_SERVING_READBACK_MISMATCH"
            )
        refreshed = self.score_refresher.refresh_teacher(
            connection,
            teacher_id=plan.teacher_id,
            projection_generation=generation,
        )
        return _merge_counts(counts, refreshed)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise DtsV2TeacherMaterializerError(
            "DTS_V2_TEACHER_MATERIALIZE_RESULT_INVALID"
        )
    return _merge_counts(value)


def _merge_counts(*values: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise DtsV2TeacherMaterializerError(
                "DTS_V2_TEACHER_MATERIALIZE_RESULT_INVALID"
            )
        for name, count in value.items():
            if not isinstance(name, str) or not name or type(count) is not int or count < 0:
                raise DtsV2TeacherMaterializerError(
                    "DTS_V2_TEACHER_MATERIALIZE_RESULT_INVALID"
                )
            result[name] = result.get(name, 0) + count
    return result


__all__ = [
    "DtsV2TeacherMaterializerError",
    "DtsV2TeacherScoreRefresher",
    "PostgresDtsV2TeacherMaterializer",
]
