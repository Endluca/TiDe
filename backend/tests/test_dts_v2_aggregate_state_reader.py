from __future__ import annotations

import hashlib
import json

import pytest

from app.dts_v2_aggregate_state_reader import (
    DtsV2AggregateStateReader,
    DtsV2AggregateStateReaderError,
)
from app.dts_v2_domain_aggregate import build_domain_aggregate_identity_v2
from app.dts_v2_outbox_worker import DtsV2OutboxEvent


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _event(revision: int = 2) -> DtsV2OutboxEvent:
    key = {"source_region": "dom", "source_appoint_id": "9001"}
    identity = build_domain_aggregate_identity_v2("COURSE", key)
    return DtsV2OutboxEvent(
        outbox_id="outbox:v2:" + "a" * 64,
        event_id="source_wide.changed.v2:COURSE:event:2",
        aggregate_type="COURSE",
        aggregate_id=identity.aggregate_id,
        event_type="source_wide.changed.v2",
        payload={"aggregate_key": key, "aggregate_revision": revision},
        payload_sha256="b" * 64,
        attempt_count=0,
        recovery_count=0,
        row_version=1,
    )


class _Mappings:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row

    def mappings(self) -> "_Mappings":
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self.row


class _Connection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.sql = ""

    def execute(self, statement: object, parameters: dict[str, object]):
        del parameters
        self.sql = str(statement)
        return _Mappings(self.row)


def _row(revision: int = 3) -> dict[str, object]:
    key = {"source_region": "dom", "source_appoint_id": "9001"}
    identity = build_domain_aggregate_identity_v2("COURSE", key)
    state = {"course": {"source_status": "end"}}
    return {
        "aggregate_type": "COURSE",
        "aggregate_id": identity.aggregate_id,
        "canonical_key": key,
        "canonical_key_sha256": identity.aggregate_id.rsplit(":", 1)[-1],
        "revision": revision,
        "aggregate_state": state,
        "aggregate_state_sha256": _hash(state),
    }


def test_reads_current_snapshot_and_marks_older_event_superseded() -> None:
    connection = _Connection(_row(revision=3))

    snapshot = DtsV2AggregateStateReader().read_current(
        connection, _event(revision=2)
    )

    assert snapshot.current_revision == 3
    assert snapshot.event_revision == 2
    assert snapshot.is_superseded_event is True
    assert snapshot.aggregate_state["course"]["source_status"] == "end"
    assert "FOR SHARE" not in connection.sql


def test_future_event_revision_fails_closed() -> None:
    with pytest.raises(
        DtsV2AggregateStateReaderError,
        match="REVISION_AHEAD",
    ):
        DtsV2AggregateStateReader().read_current(
            _Connection(_row(revision=3)), _event(revision=4)
        )


def test_state_hash_or_event_identity_mismatch_fails_closed() -> None:
    row = _row()
    row["aggregate_state_sha256"] = "f" * 64
    with pytest.raises(
        DtsV2AggregateStateReaderError,
        match="STATE_HASH_MISMATCH",
    ):
        DtsV2AggregateStateReader().read_current(
            _Connection(row), _event()
        )

    event = _event()
    bad = DtsV2OutboxEvent(
        **{**event.__dict__, "aggregate_id": "v2:COURSE:" + "0" * 64}
    )
    with pytest.raises(
        DtsV2AggregateStateReaderError,
        match="IDENTITY_MISMATCH",
    ):
        DtsV2AggregateStateReader().read_current(
            _Connection(_row()), bad
        )
