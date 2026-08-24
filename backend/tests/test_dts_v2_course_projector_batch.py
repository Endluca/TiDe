from __future__ import annotations

from collections import deque

import pytest

from app.dts_v2_course_projector import (
    DtsV2CourseProjectionResult,
    DtsV2CourseProjector,
    DtsV2CourseProjectorError,
)


class _Connection:
    def __init__(self, *, in_transaction: bool = True) -> None:
        self._in_transaction = in_transaction

    def in_transaction(self) -> bool:
        return self._in_transaction


class _Projector(DtsV2CourseProjector):
    def __init__(self, statuses: list[str]) -> None:
        super().__init__(enabled=True)
        self._statuses = deque(statuses)

    def project_next(
        self,
        connection: object,
        *,
        source_region: str,
        source_appoint_id: str,
    ) -> DtsV2CourseProjectionResult:
        del connection
        status = self._statuses.popleft()
        revision = 4 - len(self._statuses)
        return DtsV2CourseProjectionResult(
            status=status,
            source_region=source_region,
            source_appoint_id=source_appoint_id,
            source_row_revision=revision,
            participation_count=revision,
        )


def test_project_until_current_replays_every_coalesced_revision() -> None:
    result = _Projector(["APPLIED", "APPLIED", "APPLIED", "REPLAYED"]).project_until_current(
        _Connection(),  # type: ignore[arg-type]
        source_region="dom",
        source_appoint_id="9001",
    )

    assert result.source_region == "dom"
    assert result.source_appoint_id == "9001"
    assert result.source_row_revision == 4
    assert result.applied_revision_count == 3
    assert result.participation_count == 4


def test_project_until_current_requires_one_final_replay_proof() -> None:
    with pytest.raises(
        DtsV2CourseProjectorError,
        match="^DTS_V2_COURSE_PROJECTION_LIMIT_EXCEEDED$",
    ):
        _Projector(["APPLIED", "APPLIED"]).project_until_current(
            _Connection(),  # type: ignore[arg-type]
            source_region="dom",
            source_appoint_id="9001",
            max_revisions=1,
        )


@pytest.mark.parametrize("max_revisions", [0, -1, True, 1.5])
def test_project_until_current_rejects_invalid_limit(max_revisions: object) -> None:
    with pytest.raises(
        DtsV2CourseProjectorError,
        match="^DTS_V2_COURSE_MAX_REVISIONS_INVALID$",
    ):
        _Projector(["REPLAYED"]).project_until_current(
            _Connection(),  # type: ignore[arg-type]
            source_region="dom",
            source_appoint_id="9001",
            max_revisions=max_revisions,  # type: ignore[arg-type]
        )


def test_project_until_current_requires_open_transaction() -> None:
    with pytest.raises(
        DtsV2CourseProjectorError,
        match="^DTS_V2_COURSE_TRANSACTION_REQUIRED$",
    ):
        _Projector(["REPLAYED"]).project_until_current(
            _Connection(in_transaction=False),  # type: ignore[arg-type]
            source_region="dom",
            source_appoint_id="9001",
        )
