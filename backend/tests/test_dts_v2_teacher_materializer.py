from __future__ import annotations

from datetime import date
import json
from typing import Any

import pytest

from app.dts_teacher_aggregate_v2 import TeacherSourceWideProjectionV2
from app.dts_v2_teacher_materializer import (
    DtsV2TeacherMaterializerError,
    PostgresDtsV2TeacherMaterializer,
)
from app.dts_v2_teacher_outbox_processor import TeacherMaterializationPlanV2


class _Result:
    def __init__(self, *, scalar: Any = None, rows=(), row=None):
        self.scalar = scalar
        self.rows = list(rows)
        self.row = row

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.row


class _Connection:
    def __init__(self, *, generation: int | None = 9, readback_generation: int = 9):
        self.generation = generation
        self.readback_generation = readback_generation
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        if "dts_v2_runtime_primary_guard_v1" in sql:
            return _Result(scalar=self.generation)
        if "tit.dts_v2_mode" in sql:
            return _Result(
                scalar=(
                    "V2_PRIMARY"
                    if self.generation is not None
                    else "V1_COMPAT_DUAL_CAPTURE"
                )
            )
        if "tit.dts_v2_projection_generation" in sql:
            return _Result(scalar=self.generation)
        if "materialize_teacher_source_wide_v2" in sql:
            return _Result(
                scalar={
                    "teacher_source_changes": 1,
                    "teacher_identity_changes": 1,
                    "capacity_score_entries": 0,
                }
            )
        if "FROM public.teacher_source_wide" in sql:
            return _Result(
                row={
                    "v2_dom_aggregate_revision": 11,
                    "v2_ovs_aggregate_revision": 7,
                    "v2_projection_generation": self.readback_generation,
                    "v2_row_version": 2,
                }
            )
        raise AssertionError(sql)


class _Scores:
    def __init__(self):
        self.call = None

    def refresh_teacher(self, connection, *, teacher_id, projection_generation):
        del connection
        self.call = (teacher_id, projection_generation)
        return {"score_account_changes": 2}


def _plan() -> TeacherMaterializationPlanV2:
    return TeacherMaterializationPlanV2(
        teacher_id="7",
        teacher_id_type="NUMERIC",
        business_date_beijing=date(2026, 8, 22),
        projection=TeacherSourceWideProjectionV2(
            values={
                "tchr_id": "7",
                "onboard_date": date(2026, 8, 1),
                "peak_slot_cnt": 39,
                "online_status": "NEW",
                "online_status_evidence_status": "CONFIRMED",
            },
            first_date_evidence={
                "first_open_slot_dt_evidence_status": "SOURCE_MISSING",
                "first_booked_dt_evidence_status": "SOURCE_MISSING",
                "first_completed_dt_evidence_status": "SOURCE_MISSING",
            },
            metric_evidence={"peak_slot_cnt": "CONFIRMED"},
        ),
        regional_revisions={"dom": 11, "ovs": 7},
        regional_state_sha256={"dom": "a" * 64, "ovs": "b" * 64},
    )


def test_teacher_materializer_uses_guard_generation_and_refreshes_same_tx() -> None:
    connection = _Connection()
    scores = _Scores()

    result = PostgresDtsV2TeacherMaterializer(
        score_refresher=scores
    ).apply_teacher_plan(
        connection,  # type: ignore[arg-type]
        _plan(),
        triggering_event_id="evt-1",
    )

    assert result == {
        "teacher_source_changes": 1,
        "teacher_identity_changes": 1,
        "capacity_score_entries": 0,
        "score_account_changes": 2,
    }
    assert scores.call == ("7", 9)
    assert "dts_pipeline_control" not in "\n".join(sql for sql, _ in connection.calls)
    materialize = next(
        values
        for sql, values in connection.calls
        if "materialize_teacher_source_wide_v2" in sql
    )
    assert materialize["projection_generation"] == 9
    assert materialize["dom_revision"] == 11
    assert materialize["ovs_revision"] == 7
    payload = json.loads(materialize["payload"])
    assert payload["values"]["onboard_date"] == "2026-08-01"
    assert payload["regional_state_sha256"] == {
        "dom": "a" * 64,
        "ovs": "b" * 64,
    }


def test_teacher_materializer_fails_when_primary_guard_is_closed() -> None:
    with pytest.raises(
        DtsV2TeacherMaterializerError,
        match="PRIMARY_MODE_REQUIRED",
    ):
        PostgresDtsV2TeacherMaterializer(
            score_refresher=_Scores()
        ).apply_teacher_plan(
            _Connection(generation=None),  # type: ignore[arg-type]
            _plan(),
            triggering_event_id="evt-1",
        )


def test_teacher_materializer_requires_generation_readback() -> None:
    with pytest.raises(
        DtsV2TeacherMaterializerError,
        match="SERVING_READBACK_MISMATCH",
    ):
        PostgresDtsV2TeacherMaterializer(
            score_refresher=_Scores()
        ).apply_teacher_plan(
            _Connection(readback_generation=8),  # type: ignore[arg-type]
            _plan(),
            triggering_event_id="evt-1",
        )
