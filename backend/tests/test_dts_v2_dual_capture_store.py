from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app import dts_v2_shadow_source_writer as shadow_source_writer
from app.dts_ingest_store import DtsIngestDatabaseSettings
from app.dts_source_consumer import (
    DirtyKeySet,
    DtsChangeEvent,
    DtsConsumerSettings,
    prepare_change_event_for_ingest,
)
from app.dts_v2_dual_capture_store import (
    DtsV2DualCaptureStoreError,
    PostgresDtsV2DualCaptureSink,
    _event_payload_hash,
)
from app.dts_v2_dirty_queue_store import DirtyKeyV2
from app.dts_v2_shadow_source_writer import (
    DtsV2ShadowSourceWriteResult,
    DtsV2ShadowSourceWriter,
    DtsV2ShadowSourceWriterError,
)


EPOCH_ID = "broker:ovs:topic-dual:0:generation:opening"
CONSUMER_GROUP = "tit-v2-dual-test"
CONTROL_GROUP = "tit-v2-dual-control-fleet"


class _Transaction:
    def __init__(self, engine: "_Engine") -> None:
        self.engine = engine
        self.connection = object()

    def __enter__(self) -> object:
        self.engine.begins += 1
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> bool:
        del exc, traceback
        if exc_type is None:
            self.engine.commits += 1
        else:
            self.engine.rollbacks += 1
        return False


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0

    def begin(self) -> _Transaction:
        return _Transaction(self)

    def dispose(self) -> None:
        return None


class _Writer:
    enabled = True

    def __init__(
        self,
        calls: list[tuple[Any, ...]],
        *,
        replay_offsets: frozenset[int] = frozenset(),
        fail_offset: int | None = None,
    ) -> None:
        self.calls = calls
        self.replay_offsets = replay_offsets
        self.fail_offset = fail_offset

    def apply_cdc(
        self,
        connection: object,
        event: DtsChangeEvent,
        source_partition_epoch_id: str,
    ) -> DtsV2ShadowSourceWriteResult:
        del connection
        self.calls.append(("v2", event.offset, source_partition_epoch_id))
        if event.offset == self.fail_offset:
            raise DtsV2ShadowSourceWriterError(
                "DTS_V2_SHADOW_CURRENT_REQUIRED_FOR_SPARSE_EVENT"
            )
        return DtsV2ShadowSourceWriteResult(
            status=(
                "REPLAYED"
                if event.offset in self.replay_offsets
                else "APPLIED"
            ),
            source_region=event.source_region,
            source_table=event.table_name or "",
            source_key="7001",
            source_key_type="NUMERIC",
            source_row_revision=event.offset + 1,
            protected_payload_hash="a" * 64,
            dirty_keys=(
                DirtyKeyV2("ovs", "COURSE", "7001"),
                DirtyKeyV2("ovs", "TEACHER", str(80 + event.offset)),
                DirtyKeyV2("ovs", "TEACHER", str(81 + event.offset)),
            ),
        )


class _BatchWriter(_Writer):
    def prepare_batch(
        self,
        connection: object,
        events: tuple[DtsChangeEvent, ...],
        source_partition_epoch_id: str,
    ) -> object:
        del connection
        context = object()
        self.calls.append(
            (
                "prepare-batch",
                tuple(event.offset for event in events),
                source_partition_epoch_id,
                context,
            )
        )
        return context

    def apply_cdc(
        self,
        connection: object,
        event: DtsChangeEvent,
        source_partition_epoch_id: str,
        *,
        batch_context: object | None = None,
    ) -> DtsV2ShadowSourceWriteResult:
        assert batch_context is not None
        return super().apply_cdc(
            connection,
            event,
            source_partition_epoch_id,
        )


class _DeferredBatchContext:
    def __init__(self) -> None:
        self.deferred_offsets: set[int] = set()


class _DeferringBatchWriter(_Writer):
    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        super().__init__(calls)
        self.context = _DeferredBatchContext()

    def prepare_batch(
        self,
        connection: object,
        events: tuple[DtsChangeEvent, ...],
        source_partition_epoch_id: str,
    ) -> _DeferredBatchContext:
        del connection
        self.calls.append(
            (
                "prepare-batch",
                tuple(event.offset for event in events),
                source_partition_epoch_id,
            )
        )
        return self.context

    def apply_cdc(
        self,
        connection: object,
        event: DtsChangeEvent,
        source_partition_epoch_id: str,
        *,
        batch_context: _DeferredBatchContext | None = None,
    ) -> DtsV2ShadowSourceWriteResult:
        assert batch_context is self.context
        self.context.deferred_offsets.add(event.offset)
        return super().apply_cdc(
            connection,
            event,
            source_partition_epoch_id,
        )

    def flush_batch(
        self,
        connection: object,
        batch_context: _DeferredBatchContext,
    ) -> None:
        del connection
        assert batch_context is self.context
        self.calls.append(
            ("flush-source-batch", tuple(sorted(batch_context.deferred_offsets)))
        )


class _QueueStore:
    def __init__(
        self,
        calls: list[tuple[Any, ...]],
        *,
        fail_revision: int | None = None,
    ) -> None:
        self.calls = calls
        self.fail_revision = fail_revision

    def enqueue_source_revision(
        self,
        connection: object,
        *,
        source_region: str,
        source_table: str,
        source_key: str,
        source_row_revision: int,
        dirty_key: DirtyKeyV2,
    ) -> dict[str, Any]:
        del connection
        self.calls.append(
            (
                "enqueue",
                source_row_revision,
                dirty_key.source_region,
                dirty_key.key_type,
                dirty_key.key_part_1,
                dirty_key.key_part_2,
            )
        )
        if source_row_revision == self.fail_revision:
            raise RuntimeError("synthetic-enqueue-failure")
        assert (source_region, source_table, source_key) == (
            "ovs",
            "ovs_appoint",
            "7001",
        )
        return {"status": "ENQUEUED"}


class _BatchQueueStore(_QueueStore):
    def enqueue_source_revisions_batch(
        self,
        connection: object,
        *,
        commands: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> tuple[dict[str, str], ...]:
        del connection
        self.calls.append(
            (
                "enqueue-batch",
                tuple(
                    (
                        command["source_row_revision"],
                        command["dirty_key"].key_type,
                        command["dirty_key"].key_part_1,
                    )
                    for command in commands
                ),
            )
        )
        return tuple({"status": "ENQUEUED"} for _command in commands)


class _UnitSink(PostgresDtsV2DualCaptureSink):
    def __init__(
        self,
        engine: _Engine,
        writer: _Writer,
        calls: list[tuple[Any, ...]],
        *,
        duplicate_offsets: frozenset[int] = frozenset(),
        initial_next_offset: int = 0,
        queue_store: _QueueStore,
        fail_ledger_offset: int | None = None,
        fail_checkpoint_offset: int | None = None,
    ) -> None:
        super().__init__(
            DtsIngestDatabaseSettings(host="unused.invalid", password="x"),
            source_region="ovs",
            source_partition_epoch_id=EPOCH_ID,
            consumer_group=CONSUMER_GROUP,
            control_group=CONTROL_GROUP,
            source_profile_manifest_sha256="a" * 64,
            engine=engine,  # type: ignore[arg-type]
            v2_writer=writer,
            dirty_queue_store=queue_store,
        )
        self.calls = calls
        self.duplicate_offsets = duplicate_offsets
        self.initial_next_offset = initial_next_offset
        self.fail_ledger_offset = fail_ledger_offset
        self.fail_checkpoint_offset = fail_checkpoint_offset

    def _validate_runtime(self) -> None:
        self.calls.append(("runtime",))

    def _validate_dual_capture_state(
        self,
        connection: object,
        *,
        source_region: str,
        topic: str,
        partition: int,
        lock_checkpoint: bool,
    ) -> dict[str, Any]:
        del connection
        self.calls.append(
            (
                "state",
                source_region,
                topic,
                partition,
                lock_checkpoint,
            )
        )
        return {
            "next_offset": self.initial_next_offset,
            "source_timestamp": 0,
            "checkpoint_row_version": 11,
        }

    def _apply_transaction(
        self,
        connection: object,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
    ) -> bool:
        del connection, event, dirty_keys
        raise AssertionError("dual capture must never call legacy persistence")

    def _was_missing_current_ignored(
        self,
        connection: object,
        *,
        event: DtsChangeEvent,
    ) -> bool:
        del connection, event
        return False

    def _write_or_validate_ledger(
        self,
        connection: object,
        *,
        event: DtsChangeEvent,
        source_position: dict[str, Any],
        event_payload_hash: str,
        route_status: str,
        dirty_key_count: int,
        issue_codes: tuple[str, ...],
    ) -> bool:
        del connection
        if event.offset == self.fail_ledger_offset:
            raise RuntimeError("synthetic-ledger-failure")
        duplicate = event.offset in self.duplicate_offsets
        self.calls.append(
            (
                "ledger",
                event.offset,
                duplicate,
                route_status,
                dirty_key_count,
                issue_codes,
                source_position["source_partition_epoch_id"],
                len(event_payload_hash),
            )
        )
        return duplicate

    def _advance_or_validate_checkpoint(
        self,
        connection: object,
        *,
        event: DtsChangeEvent,
        duplicate: bool,
        expected_checkpoint_row_version: int,
        expected_next_offset: int,
    ) -> int:
        del connection
        if event.offset == self.fail_checkpoint_offset:
            raise RuntimeError("synthetic-checkpoint-failure")
        self.calls.append(
            (
                "checkpoint-v2",
                event.offset,
                duplicate,
                expected_checkpoint_row_version,
                expected_next_offset,
            )
        )
        return (
            expected_checkpoint_row_version
            if duplicate
            else expected_checkpoint_row_version + 1
        )


class _BatchUnitSink(_UnitSink):
    def _write_new_ledger_batch(
        self,
        connection: object,
        records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> None:
        del connection
        self.calls.append(
            (
                "ledger-batch",
                tuple(record["offset"] for record in records),
            )
        )

    def _advance_batch_checkpoint(
        self,
        connection: object,
        *,
        event: DtsChangeEvent,
        expected_checkpoint_row_version: int,
        next_checkpoint_row_version: int,
        expected_next_offset: int,
        advanced_next_offset: int,
    ) -> None:
        del connection
        self.calls.append(
            (
                "checkpoint-batch",
                event.offset,
                expected_checkpoint_row_version,
                next_checkpoint_row_version,
                expected_next_offset,
                advanced_next_offset,
            )
        )


def _event(
    offset: int,
    *,
    topic: str = "topic-dual",
    partition: int = 0,
    operation: str = "UPDATE",
    table_name: str | None = "ovs_appoint",
) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region="ovs",
        topic=topic,
        partition=partition,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=1_787_500_000 + offset,
        source_txid=f"tx-{offset}",
        source_position=f"opaque-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name=table_name,
        before={"id": 7001, "t_id": 80 + offset},
        after={"id": 7001, "t_id": 81 + offset},
        source_field_types={"id": "NUMERIC", "t_id": "NUMERIC"},
    )


def _sink(
    *,
    replay_offsets: frozenset[int] = frozenset(),
    duplicate_offsets: frozenset[int] = frozenset(),
    fail_offset: int | None = None,
    fail_enqueue_revision: int | None = None,
    fail_ledger_offset: int | None = None,
    fail_checkpoint_offset: int | None = None,
    initial_next_offset: int = 0,
) -> tuple[_UnitSink, _Engine, list[tuple[Any, ...]]]:
    calls: list[tuple[Any, ...]] = []
    engine = _Engine()
    writer = _Writer(
        calls,
        replay_offsets=replay_offsets,
        fail_offset=fail_offset,
    )
    queue_store = _QueueStore(
        calls,
        fail_revision=fail_enqueue_revision,
    )
    return (
        _UnitSink(
            engine,
            writer,
            calls,
            duplicate_offsets=duplicate_offsets,
            initial_next_offset=initial_next_offset,
            queue_store=queue_store,
            fail_ledger_offset=fail_ledger_offset,
            fail_checkpoint_offset=fail_checkpoint_offset,
        ),
        engine,
        calls,
    )


def test_batch_uses_one_transaction_and_never_calls_legacy_dirty_upsert() -> None:
    sink, engine, calls = _sink()

    result = sink.apply_batch(
        (
            (_event(0), DirtyKeySet(course_ids=frozenset({"7001"})), None),
            (_event(1), DirtyKeySet(course_ids=frozenset({"7001"})), None),
        )
    )

    assert result == (False, False)
    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 1, 0)
    assert calls[:3] == [
        ("runtime",),
        ("state", "ovs", "topic-dual", 0, True),
        ("v2", 0, EPOCH_ID),
    ]
    first_ledger = next(index for index, call in enumerate(calls) if call[0] == "ledger")
    assert [call[0] for call in calls[3:first_ledger]] == [
        "enqueue",
        "enqueue",
        "enqueue",
    ]
    assert calls[first_ledger][:6] == (
        "ledger",
        0,
        False,
        "PROCESSED",
        3,
        (),
    )
    assert calls[first_ledger + 1] == (
        "checkpoint-v2",
        0,
        False,
        11,
        0,
    )
    second_v2 = calls.index(("v2", 1, EPOCH_ID))
    assert second_v2 > first_ledger
    assert calls[-1] == ("checkpoint-v2", 1, False, 12, 1)


def test_batch_capable_writer_defers_ledger_and_checkpoint_once() -> None:
    calls: list[tuple[Any, ...]] = []
    engine = _Engine()
    writer = _BatchWriter(calls)
    sink = _BatchUnitSink(
        engine,
        writer,
        calls,
        queue_store=_QueueStore(calls),
    )

    events = tuple(
        (
            _event(offset, operation="HEARTBEAT", table_name=None),
            DirtyKeySet(ignored_reason="CONTROL_RECORD"),
            None,
        )
        for offset in range(100)
    )
    assert sink.apply_batch(events) == (False,) * 100

    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 1, 0)
    assert sum(call[0] == "prepare-batch" for call in calls) == 1
    assert sum(call[0] == "ledger-batch" for call in calls) == 1
    assert sum(call[0] == "checkpoint-batch" for call in calls) == 1
    assert not any(call[0] == "ledger" for call in calls)
    assert not any(call[0] == "checkpoint-v2" for call in calls)
    assert calls[-1] == (
        "checkpoint-batch",
        99,
        11,
        111,
        0,
        100,
    )


def test_deferred_source_flushes_before_dirty_ledger_and_checkpoint() -> None:
    calls: list[tuple[Any, ...]] = []
    engine = _Engine()
    writer = _DeferringBatchWriter(calls)
    sink = _BatchUnitSink(
        engine,
        writer,
        calls,
        queue_store=_BatchQueueStore(calls),
    )

    assert sink.apply_batch(
        tuple(
            (_event(offset), DirtyKeySet(), None)
            for offset in range(2)
        )
    ) == (False, False)

    ordered_calls = [call[0] for call in calls]
    assert ordered_calls.index("flush-source-batch") < ordered_calls.index(
        "enqueue-batch"
    )
    assert ordered_calls.index("enqueue-batch") < ordered_calls.index(
        "ledger-batch"
    )
    assert ordered_calls.index("ledger-batch") < ordered_calls.index(
        "checkpoint-batch"
    )
    assert not any(call[0] == "enqueue" for call in calls)
    assert calls[ordered_calls.index("flush-source-batch")] == (
        "flush-source-batch",
        (0, 1),
    )
    assert len(calls[ordered_calls.index("enqueue-batch")][1]) == 6
    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 1, 0)


def test_exact_replay_requires_source_version_and_ledger_and_does_not_enqueue() -> None:
    sink, engine, calls = _sink(
        replay_offsets=frozenset({3}),
        duplicate_offsets=frozenset({3}),
        initial_next_offset=4,
    )

    assert sink.apply(_event(3), DirtyKeySet(), None) is True

    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 1, 0)
    assert any(call[:3] == ("ledger", 3, True) for call in calls)
    assert ("checkpoint-v2", 3, True, 11, 4) in calls
    assert not any(call[0] == "enqueue" for call in calls)


def test_enqueue_failure_rolls_back_source_ledger_and_checkpoint_batch() -> None:
    sink, engine, calls = _sink(fail_enqueue_revision=2)

    with pytest.raises(RuntimeError, match="synthetic-enqueue-failure"):
        sink.apply_batch(
            (
                (_event(0), DirtyKeySet(), None),
                (_event(1), DirtyKeySet(), None),
            )
        )

    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 0, 1)
    assert ("v2", 1, EPOCH_ID) in calls
    assert not any(call[:2] == ("ledger", 1) for call in calls)


def test_writer_failure_adds_only_safe_failed_event_context() -> None:
    sink, engine, _calls = _sink(fail_offset=1)

    with pytest.raises(DtsV2ShadowSourceWriterError) as caught:
        sink.apply_batch(
            (
                (_event(0), DirtyKeySet(), None),
                (_event(1), DirtyKeySet(), None),
            )
        )

    error = caught.value
    assert error.safe_source_region == "ovs"
    assert error.safe_source_table == "ovs_appoint"
    assert error.safe_source_operation == "UPDATE"
    assert error.safe_source_offset == 1
    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 0, 1)


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_sparse_non_course_change_without_current_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    before = {"id": 901, "status": "off"}
    event = DtsChangeEvent(
        source_region="dom",
        topic="dom-topic",
        partition=0,
        offset=20_740_311,
        record_id=20_740_311,
        source_timestamp=1_786_982_400,
        source_txid="tx-schedule",
        source_position="opaque-schedule",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="dom_teacher_class_schedule",
        before=before,
        after=(
            {"id": 901, "status": "on"}
            if operation == "UPDATE"
            else None
        ),
        source_field_types={"id": "NUMERIC", "status": "TEXT"},
    )
    protected_event = prepare_change_event_for_ingest(
        event,
        DtsConsumerSettings(
            source_region="dom",
            execution_region="cn",
            broker_urls=("broker.invalid:18003",),
            topic="dom-topic",
            group_id="dom-test-group",
            account="dom-test-account",
            password="unused",
            domestic_student_hmac_key="a" * 64,
        ),
    )

    class Connection:
        @staticmethod
        def in_transaction() -> bool:
            return True

    monkeypatch.setattr(
        shadow_source_writer,
        "_require_broker_epoch",
        lambda *_args, **_kwargs: {"status": "ACTIVE"},
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_source_table_for_cdc",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_read_version_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_read_current_for_update",
        lambda *_args, **_kwargs: None,
    )

    result = DtsV2ShadowSourceWriter(enabled=True).apply_cdc(
        Connection(),  # type: ignore[arg-type]
        protected_event,
        EPOCH_ID,
    )

    assert result.status == "IGNORED_MISSING_CURRENT"
    assert result.source_table == "dom_teacher_class_schedule"
    assert result.source_key == "901"
    assert result.source_row_revision is None
    assert result.dirty_keys == ()


def test_shadow_batch_missing_current_uses_one_prefetch_and_no_event_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = tuple(_event(offset) for offset in range(100))
    calls: list[str] = []

    class _Rows:
        @staticmethod
        def mappings() -> tuple[()]:
            return ()

    class _Connection:
        @staticmethod
        def in_transaction() -> bool:
            return True

        @staticmethod
        def execute(*_args: Any, **_kwargs: Any) -> _Rows:
            calls.append("current-prefetch")
            return _Rows()

    monkeypatch.setattr(
        shadow_source_writer,
        "_require_broker_epoch",
        lambda *_args, **_kwargs: {"status": "ACTIVE"},
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_source_table_for_cdc",
        lambda *_args, **_kwargs: calls.append("table-lock"),
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_identities",
        lambda *_args, **_kwargs: calls.append("identity-lock-batch"),
    )
    for forbidden in (
        "_lock_identity",
        "_read_version_identity",
        "_read_current_for_update",
    ):
        monkeypatch.setattr(
            shadow_source_writer,
            forbidden,
            lambda *_args, _name=forbidden, **_kwargs: pytest.fail(
                f"unexpected per-event SQL path: {_name}"
            ),
        )

    writer = DtsV2ShadowSourceWriter(enabled=True)
    connection = _Connection()
    context = writer.prepare_batch(connection, events, EPOCH_ID)
    assert context is not None

    results = tuple(
        writer.apply_cdc(
            connection,  # type: ignore[arg-type]
            event,
            EPOCH_ID,
            batch_context=context,
        )
        for event in events
    )

    assert {result.status for result in results} == {
        "IGNORED_MISSING_CURRENT"
    }
    assert calls == [
        "table-lock",
        "identity-lock-batch",
        "current-prefetch",
    ]


def test_shadow_batch_repeated_identity_keeps_ordered_event_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        _event(0, operation="INSERT"),
        _event(1, operation="UPDATE"),
    )

    class _Rows:
        @staticmethod
        def mappings() -> tuple[()]:
            return ()

    class _Connection:
        @staticmethod
        def execute(*_args: Any, **_kwargs: Any) -> _Rows:
            return _Rows()

    monkeypatch.setattr(
        shadow_source_writer,
        "_require_broker_epoch",
        lambda *_args, **_kwargs: {"status": "ACTIVE"},
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_source_table_for_cdc",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        shadow_source_writer,
        "_lock_identities",
        lambda *_args, **_kwargs: None,
    )

    context = DtsV2ShadowSourceWriter(enabled=True).prepare_batch(
        _Connection(),  # type: ignore[arg-type]
        events,
        EPOCH_ID,
    )

    assert context is not None
    assert context.deferred_candidate_offsets == frozenset()


@pytest.mark.parametrize(
    ("sink_options", "error"),
    (
        ({"fail_ledger_offset": 0}, "synthetic-ledger-failure"),
        ({"fail_checkpoint_offset": 0}, "synthetic-checkpoint-failure"),
    ),
)
def test_ledger_or_checkpoint_failure_rolls_back_whole_transition(
    sink_options: dict[str, int],
    error: str,
) -> None:
    sink, engine, calls = _sink(**sink_options)

    with pytest.raises(RuntimeError, match=error):
        sink.apply(_event(0), DirtyKeySet(), None)

    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 0, 1)
    assert ("v2", 0, EPOCH_ID) in calls
    assert sum(call[0] == "enqueue" for call in calls) == 3


def test_control_record_skips_source_and_dirty_but_records_ignored_route() -> None:
    sink, engine, calls = _sink()
    control = _event(
        0,
        operation="HEARTBEAT",
        table_name=None,
    )

    assert sink.apply(control, DirtyKeySet(ignored_reason="CONTROL_RECORD"), None) is False

    assert (engine.begins, engine.commits, engine.rollbacks) == (1, 1, 0)
    assert not any(call[0] == "v2" for call in calls)
    assert not any(call[0] == "enqueue" for call in calls)
    ledger = next(call for call in calls if call[0] == "ledger")
    assert ledger[:6] == (
        "ledger",
        0,
        False,
        "IGNORED",
        0,
        ("CONTROL_RECORD",),
    )


def test_ledger_hash_commits_sdk_field_type_number_evidence() -> None:
    first = _event(0)
    second = DtsChangeEvent(
        **{
            **first.__dict__,
            "source_field_type_numbers": {"id": 20, "t_id": 21},
        }
    )

    first_hash = _event_payload_hash(
        first,
        source_partition_epoch_id=EPOCH_ID,
        protected_source_hash="a" * 64,
    )
    second_hash = _event_payload_hash(
        second,
        source_partition_epoch_id=EPOCH_ID,
        protected_source_hash="a" * 64,
    )

    assert first_hash != second_hash


def test_direct_and_invalid_batch_shape_fail_before_any_write() -> None:
    sink, engine, _calls = _sink()

    with pytest.raises(
        DtsV2DualCaptureStoreError,
        match="DTS_V2_DUAL_CAPTURE_DIRECT_FORBIDDEN",
    ):
        sink.enable_direct_projection(object())
    with pytest.raises(
        DtsV2DualCaptureStoreError,
        match="DTS_V2_DUAL_CAPTURE_BATCH_STREAM_MISMATCH",
    ):
        sink.apply_batch(
            (
                (_event(0), DirtyKeySet(), None),
                (_event(1, partition=1), DirtyKeySet(), None),
            )
        )
    with pytest.raises(
        DtsV2DualCaptureStoreError,
        match="DTS_V2_DUAL_CAPTURE_BATCH_OFFSET_ORDER_INVALID",
    ):
        sink.apply_batch(
            (
                (_event(1), DirtyKeySet(), None),
                (_event(1), DirtyKeySet(), None),
            )
        )
    assert engine.begins == 0


@pytest.mark.parametrize(
    (
        "source_region",
        "epoch_id",
        "consumer_group",
        "error_code",
    ),
    (
        (
            "",
            EPOCH_ID,
            CONSUMER_GROUP,
            "DTS_V2_DUAL_CAPTURE_SOURCE_REGION_REQUIRED",
        ),
        (
            "ovs",
            " ",
            CONSUMER_GROUP,
            "DTS_V2_DUAL_CAPTURE_EPOCH_ID_REQUIRED",
        ),
        (
            "ovs",
            EPOCH_ID,
            "",
            "DTS_V2_DUAL_CAPTURE_CONSUMER_GROUP_REQUIRED",
        ),
    ),
)
def test_constructor_requires_explicit_v2_identity(
    source_region: str,
    epoch_id: str,
    consumer_group: str,
    error_code: str,
) -> None:
    with pytest.raises(DtsV2DualCaptureStoreError, match=error_code):
        PostgresDtsV2DualCaptureSink(
            DtsIngestDatabaseSettings(host="unused.invalid", password="x"),
            source_region=source_region,
            source_partition_epoch_id=epoch_id,
            consumer_group=consumer_group,
            engine=_Engine(),  # type: ignore[arg-type]
            v2_writer=_Writer([]),
        )


def test_single_pipeline_requires_profile_manifest_identity() -> None:
    settings = DtsIngestDatabaseSettings(host="unused.invalid", password="x")
    with pytest.raises(
        DtsV2DualCaptureStoreError,
        match="^DTS_V2_SOURCE_PROFILE_MANIFEST_SHA256_REQUIRED$",
    ):
        PostgresDtsV2DualCaptureSink(
            settings,
            pipeline_mode="V2_PRIMARY",
            source_region="ovs",
            source_partition_epoch_id=EPOCH_ID,
            consumer_group=CONSUMER_GROUP,
            engine=_Engine(),  # type: ignore[arg-type]
            v2_writer=_Writer([]),
        )
