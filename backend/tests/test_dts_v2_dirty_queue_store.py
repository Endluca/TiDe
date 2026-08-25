from __future__ import annotations

import json

import pytest

from app.dts_v2_dirty_queue_store import (
    DirtyClaimV2,
    DirtyDependencyV2,
    DirtyKeyV2,
    DtsV2DirtyQueueError,
    DtsV2DirtyQueueStore,
)


class _Result:
    def __init__(self, value=None, rows=()):
        self._value = value
        self._rows = rows

    def scalar_one(self):
        return self._value

    def mappings(self):
        return iter(self._rows)


class _Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters or {}))
        return self.results.pop(0)


def test_dirty_key_enforces_exact_dom_student_token() -> None:
    token = "dom:v1:" + "a" * 64
    assert DirtyKeyV2("dom", "TEACHER_STUDENT", "7", token).key_part_2 == token
    with pytest.raises(DtsV2DirtyQueueError, match="TOKEN_INVALID"):
        DirtyKeyV2("dom", "TEACHER_STUDENT", "7", "dom:v1:raw")
    with pytest.raises(DtsV2DirtyQueueError, match="PART_INVALID"):
        DirtyKeyV2("ovs", "COURSE", "9", "-")
    with pytest.raises(DtsV2DirtyQueueError, match="PART_INVALID"):
        DirtyKeyV2("ovs", "COMPLAINT_CATEGORY", "82", "")


def test_store_only_calls_typed_database_commands() -> None:
    connection = _Connection(
        [
            _Result({"status": "ENQUEUED", "dirty_work_revision": 1}),
            _Result(
                rows=(
                    {
                        "source_region": "dom",
                        "key_type": "COURSE",
                        "key_part_1": "9001",
                        "key_part_2": "",
                        "lease_token": "lease-1",
                        "claimed_work_revision": 1,
                        "row_version": 2,
                    },
                )
            ),
            _Result({"status": "COMPLETED", "row_version": 3}),
        ]
    )
    store = DtsV2DirtyQueueStore()
    dirty_key = DirtyKeyV2("dom", "COURSE", "9001")
    assert store.enqueue_source_revision(
        connection,
        source_region="dom",
        source_table="dom_appoint",
        source_key="9001",
        source_row_revision=4,
        dirty_key=dirty_key,
    )["status"] == "ENQUEUED"
    claim = store.claim_domain(
        connection, worker_id="domain-1", batch_size=10, lease_seconds=60
    )[0]
    assert claim == DirtyClaimV2(dirty_key, "lease-1", 1, 2)
    assert store.complete_domain(connection, claim)["status"] == "COMPLETED"

    sql = "\n".join(call[0] for call in connection.calls)
    assert "enqueue_dirty_from_source_revision_v2" in sql
    assert "claim_domain_dirty_keys_v2" in sql
    assert "complete_domain_dirty_key_v2" in sql
    assert "INSERT INTO" not in sql
    assert "UPDATE public.dts_dirty" not in sql
    assert "DELETE FROM" not in sql


def test_dom_teacher_can_enqueue_only_its_exact_ovs_peer() -> None:
    connection = _Connection(
        [_Result({"status": "ENQUEUED", "dirty_work_revision": 1})]
    )
    peer = DirtyKeyV2("ovs", "TEACHER", "9")

    assert DtsV2DirtyQueueStore().enqueue_source_revision(
        connection,
        source_region="dom",
        source_table="dom_teacher",
        source_key="9",
        source_row_revision=1,
        dirty_key=peer,
    )["status"] == "ENQUEUED"
    assert (
        "enqueue_peer_teacher_dirty_from_source_revision_v2"
        in connection.calls[0][0]
    )

    with pytest.raises(DtsV2DirtyQueueError, match="REGION_MISMATCH"):
        DtsV2DirtyQueueStore().enqueue_source_revision(
            _Connection([]),
            source_region="dom",
            source_table="dom_appoint",
            source_key="9",
            source_row_revision=1,
            dirty_key=peer,
        )


def test_batch_enqueue_uses_one_ordered_database_call() -> None:
    connection = _Connection(
        [
            _Result(
                rows=(
                    {
                        "ordinal": 0,
                        "response": {
                            "status": "ENQUEUED",
                            "dirty_work_revision": 1,
                        },
                    },
                    {
                        "ordinal": 1,
                        "response": {
                            "status": "ENQUEUED",
                            "dirty_work_revision": 2,
                        },
                    },
                )
            )
        ]
    )
    results = DtsV2DirtyQueueStore().enqueue_source_revisions_batch(
        connection,
        commands=(
            {
                "source_region": "dom",
                "source_table": "dom_appoint",
                "source_key": "9001",
                "source_row_revision": 1,
                "dirty_key": DirtyKeyV2("dom", "COURSE", "9001"),
            },
            {
                "source_region": "dom",
                "source_table": "dom_teacher",
                "source_key": "9",
                "source_row_revision": 1,
                "dirty_key": DirtyKeyV2("ovs", "TEACHER", "9"),
            },
        ),
    )

    assert [result["status"] for result in results] == [
        "ENQUEUED",
        "ENQUEUED",
    ]
    assert len(connection.calls) == 1
    sql, parameters = connection.calls[0]
    assert "jsonb_to_recordset" in sql
    assert "enqueue_dirty_from_source_revision_v2" in sql
    assert "enqueue_peer_teacher_dirty_from_source_revision_v2" in sql
    payload = json.loads(parameters["commands"])
    assert [item["ordinal"] for item in payload] == [0, 1]
    assert [item["peer_teacher"] for item in payload] == [False, True]


@pytest.mark.parametrize("source_row_revision", (True, "1", None, 0))
def test_batch_enqueue_rejects_invalid_source_revision(
    source_row_revision: object,
) -> None:
    with pytest.raises(
        DtsV2DirtyQueueError,
        match="DIRTY_SOURCE_REFERENCE_INVALID",
    ):
        DtsV2DirtyQueueStore().enqueue_source_revisions_batch(
            _Connection([]),
            commands=(
                {
                    "source_region": "dom",
                    "source_table": "dom_appoint",
                    "source_key": "9001",
                    "source_row_revision": source_row_revision,
                    "dirty_key": DirtyKeyV2("dom", "COURSE", "9001"),
                },
            ),
        )


def test_wait_sorts_dependencies_and_rejects_duplicate_identity() -> None:
    claim = DirtyClaimV2(DirtyKeyV2("dom", "COURSE", "9001"), "lease", 2, 5)
    first = DirtyDependencyV2("TEACHER", "dom", "10", 3, "a" * 64)
    second = DirtyDependencyV2("SOURCE_ROW", "dom", "appoint:9001", 7, "b" * 64)
    connection = _Connection([_Result({"status": "WAITING_DEPENDENCY"})])
    result = DtsV2DirtyQueueStore().wait_domain(
        connection, claim, [first, second]
    )
    assert result["status"] == "WAITING_DEPENDENCY"
    payload = connection.calls[0][1]["dependencies"]
    assert payload.index("SOURCE_ROW") < payload.index("TEACHER")

    with pytest.raises(DtsV2DirtyQueueError, match="DUPLICATE"):
        DtsV2DirtyQueueStore().wait_domain(
            _Connection([]), claim, [first, first]
        )
