"""Ledger-authoritative DTS v2 score and qualification projection client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_favorite_runtime import FavoriteAttributionOutcomeV2
from .dts_v2_runtime_guard import guarded_projection_generation


class DtsV2ScoreProjectionStoreError(RuntimeError):
    """A protected score rebuild did not prove an atomic current result."""


class PostgresDtsV2ScoreProjectionStore:
    """Invoke protected rebuild commands inside the caller transaction.

    PostgreSQL owns the score projection lock, dependency-vector comparison,
    ledger aggregation and irreversible qualification state.  This client only
    gives COURSE and favorite workers one typed transaction-local entrypoint.
    """

    def __init__(self, *, require_guarded_generation: bool = False) -> None:
        self.require_guarded_generation = require_guarded_generation

    def refresh_course_and_teachers(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        teacher_ids: Sequence[str],
        projection_generation: int,
    ) -> Mapping[str, int]:
        _identity(source_region, source_appoint_id)
        normalized_teachers = _teacher_ids(teacher_ids)
        _generation(projection_generation)
        counts = _call_counts(
            connection,
            """
            SELECT public.rebuild_lesson_score_result_v2(
                :source_region,:source_appoint_id,:projection_generation
            )
            """,
            {
                "source_region": source_region,
                "source_appoint_id": source_appoint_id,
                "projection_generation": projection_generation,
            },
        )
        for teacher_id in normalized_teachers:
            counts = _merge_counts(
                counts,
                self._refresh_teacher(
                    connection,
                    teacher_id=teacher_id,
                    projection_generation=projection_generation,
                ),
            )
        return counts

    def rebuild_after_favorite(
        self,
        connection: Connection,
        outcome: FavoriteAttributionOutcomeV2,
    ) -> Mapping[str, int]:
        if not isinstance(outcome, FavoriteAttributionOutcomeV2):
            raise DtsV2ScoreProjectionStoreError(
                "DTS_V2_FAVORITE_OUTCOME_REQUIRED"
            )
        generation = (
            guarded_projection_generation(connection)
            if self.require_guarded_generation
            else _primary_projection_generation(connection)
        )
        counts: Mapping[str, int] = {}
        course_ids = sorted(
            {
                value
                for value in (
                    outcome.previous_source_appoint_id,
                    outcome.current_source_appoint_id,
                )
                if value is not None
            }
        )
        for source_appoint_id in course_ids:
            counts = _merge_counts(
                counts,
                _call_counts(
                    connection,
                    """
                    SELECT public.rebuild_lesson_score_result_v2(
                        :source_region,:source_appoint_id,
                        :projection_generation
                    )
                    """,
                    {
                        "source_region": outcome.source_region,
                        "source_appoint_id": source_appoint_id,
                        "projection_generation": generation,
                    },
                ),
            )
        return _merge_counts(
            counts,
            self._refresh_teacher(
                connection,
                teacher_id=outcome.teacher_id,
                projection_generation=generation,
            ),
        )

    def refresh_teacher(
        self,
        connection: Connection,
        *,
        teacher_id: str,
        projection_generation: int,
    ) -> Mapping[str, int]:
        """Public single-teacher entrypoint used by the TEACHER materializer."""

        _generation(projection_generation)
        return self._refresh_teacher(
            connection,
            teacher_id=teacher_id,
            projection_generation=projection_generation,
        )

    def _refresh_teacher(
        self,
        connection: Connection,
        *,
        teacher_id: str,
        projection_generation: int,
    ) -> Mapping[str, int]:
        _required_text(teacher_id, "DTS_V2_SCORE_TEACHER_ID_INVALID")
        vector = connection.execute(
            text(
                "SELECT public.teacher_score_projection_vector_v2("
                ":teacher_id)"
            ),
            {"teacher_id": teacher_id},
        ).scalar_one()
        if not isinstance(vector, Mapping):
            raise DtsV2ScoreProjectionStoreError(
                "DTS_V2_SCORE_PROJECTION_VECTOR_INVALID"
            )
        return _call_counts(
            connection,
            """
            SELECT public.rebuild_teacher_score_and_qualification_v2(
                :teacher_id,CAST(:expected_vector AS jsonb),
                :projection_generation
            )
            """,
            {
                "teacher_id": teacher_id,
                "expected_vector": json.dumps(
                    dict(vector),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "projection_generation": projection_generation,
            },
        )


def _primary_projection_generation(connection: Connection) -> int:
    row = connection.execute(
        text(
            """
            SELECT mode,projection_generation
            FROM public.dts_pipeline_control
            WHERE control_id='PRIMARY'
            """
        )
    ).mappings().one_or_none()
    if row is None or row.get("mode") != "V2_PRIMARY":
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_PRIMARY_MODE_REQUIRED"
        )
    value = row.get("projection_generation")
    _generation(value)
    return int(value)


def _call_counts(
    connection: Connection,
    sql: str,
    parameters: Mapping[str, Any],
) -> dict[str, int]:
    value = connection.execute(text(sql), dict(parameters)).scalar_one()
    if not isinstance(value, Mapping):
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_REBUILD_RESULT_INVALID"
        )
    return _merge_counts(value)


def _merge_counts(*values: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for mapping in values:
        if not isinstance(mapping, Mapping):
            raise DtsV2ScoreProjectionStoreError(
                "DTS_V2_SCORE_REBUILD_RESULT_INVALID"
            )
        for name, count in mapping.items():
            if (
                not isinstance(name, str)
                or not name
                or type(count) is not int
                or count < 0
            ):
                raise DtsV2ScoreProjectionStoreError(
                    "DTS_V2_SCORE_REBUILD_RESULT_INVALID"
                )
            result[name] = result.get(name, 0) + count
    return result


def _teacher_ids(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_TEACHER_IDS_INVALID"
        )
    normalized = tuple(sorted(set(values)))
    if any(
        not isinstance(value, str) or not value or value.strip() != value
        for value in normalized
    ):
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_TEACHER_IDS_INVALID"
        )
    return normalized


def _identity(source_region: str, source_appoint_id: str) -> None:
    if source_region not in {"dom", "ovs"}:
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_SOURCE_REGION_INVALID"
        )
    _required_text(
        source_appoint_id, "DTS_V2_SCORE_SOURCE_APPOINT_ID_INVALID"
    )


def _required_text(value: Any, code: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DtsV2ScoreProjectionStoreError(code)


def _generation(value: Any) -> None:
    if type(value) is not int or value < 1:
        raise DtsV2ScoreProjectionStoreError(
            "DTS_V2_SCORE_PROJECTION_GENERATION_INVALID"
        )


__all__ = [
    "DtsV2ScoreProjectionStoreError",
    "PostgresDtsV2ScoreProjectionStore",
]
