from __future__ import annotations

from collections import deque

import pytest

import app.dts_source_contract_v2 as source_contract
from app.dts_v2_course_projector import (
    DtsV2CourseProjectionResult,
    DtsV2CourseProjector,
    DtsV2CourseProjectorError,
    _adapt_contiguous_versions,
    _read_source_current,
)


class _Connection:
    def __init__(self, *, in_transaction: bool = True) -> None:
        self._in_transaction = in_transaction

    def in_transaction(self) -> bool:
        return self._in_transaction


class _MappingResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def mappings(self) -> _MappingResult:
        return self

    def one_or_none(self) -> object:
        return self._row


class _CapturingConnection:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: dict[str, object] = {}

    def execute(
        self,
        statement: object,
        parameters: dict[str, object],
    ) -> _MappingResult:
        self.statement = str(statement)
        self.parameters = parameters
        return _MappingResult({"source_key": "9001"})


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


def test_course_projection_uses_code_owned_event_contract_without_manifest(
    monkeypatch,
) -> None:
    monkeypatch.delitem(
        source_contract.V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
        "dom_appoint",
        raising=False,
    )
    monkeypatch.delitem(
        source_contract.V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
        "dom_appoint",
        raising=False,
    )

    assert _adapt_contiguous_versions(
        [],
        source_region="dom",
        source_table="dom_appoint",
        source_appoint_id="9001",
    ) == []


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


def test_source_current_read_does_not_lock_ingest_owned_row() -> None:
    connection = _CapturingConnection()

    row = _read_source_current(
        connection,  # type: ignore[arg-type]
        source_region="dom",
        source_table="dom_appoint",
        source_appoint_id="9001",
    )

    assert row == {"source_key": "9001"}
    assert "FROM public.dts_source_rows" in connection.statement
    assert "FOR UPDATE" not in connection.statement.upper()
    assert connection.parameters == {
        "source_region": "dom",
        "source_table": "dom_appoint",
        "source_key": "9001",
    }
