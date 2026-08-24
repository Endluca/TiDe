from __future__ import annotations

import json
import queue
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app import dts_java_transport, dts_source_consumer
from app.dts_java_transport import (
    DtsJavaTransportError,
    JAVA_TRANSPORT_MAX_BATCH_MESSAGES,
    JAVA_TRANSPORT_PROTOCOL_VERSION,
    OfficialJavaDtsTransport,
    java_child_environment,
    java_transport_command,
)
from app.dts_source_consumer import (
    DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256,
    DtsConsumerSettings,
)


@pytest.mark.parametrize("max_messages", [0, 2_049])
def test_java_transport_rejects_batch_size_outside_protocol_limit(
    max_messages: int,
) -> None:
    transport = object.__new__(OfficialJavaDtsTransport)
    transport._started = True
    transport._closed = False

    with pytest.raises(ValueError, match="max_messages must be between"):
        transport.run(max_messages=max_messages, commit_offsets=True)

    assert JAVA_TRANSPORT_MAX_BATCH_MESSAGES == 2_048


def _event_record(
    *,
    offset: int = 42,
    source_timestamp: int | None = None,
) -> dict[str, Any]:
    timestamp = (
        1786550400 + offset
        if source_timestamp is None
        else source_timestamp
    )
    return {
        "id": offset,
        "sourceTimestamp": timestamp,
        "sourcePosition": f"lsn:{offset}",
        "safeSourcePosition": f"lsn:{offset}",
        "sourceTxid": f"tx-{offset}",
        "operation": "INSERT",
        "objectName": "public.ovs_appoint",
        "tags": {},
        "fields": ["id"],
        "beforeImages": None,
        "afterImages": [str(offset)],
    }


def _record_bytes(record: dict[str, Any]) -> int:
    return len(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


class _FakeStdout:
    def __init__(self, lines: queue.Queue[str | None]) -> None:
        self._lines = lines

    def readline(self) -> str:
        value = self._lines.get(timeout=2)
        return "" if value is None else value

    def close(self) -> None:
        pass


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self._process = process
        self._buffer = ""

    def write(self, value: str) -> int:
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._process.accept(json.loads(line))
        return len(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(
        self,
        *,
        event_record: dict[str, Any] | None = None,
        first_record_offset: int = 42,
        first_record_source_timestamp: int = 1786550400,
        avro_writer_schema_fingerprint: str | None = (
            DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256
        ),
        ready_protocol_version: int = JAVA_TRANSPORT_PROTOCOL_VERSION,
        poll_error_code: str | None = None,
    ) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stdout = _FakeStdout(self.lines)
        self.stdin = _FakeStdin(self)
        self.returncode: int | None = None
        self.commands: list[dict[str, Any]] = []
        self.event_record = event_record or _event_record(
            offset=first_record_offset,
            source_timestamp=first_record_source_timestamp,
        )
        self.first_record_offset = first_record_offset
        self.first_record_source_timestamp = first_record_source_timestamp
        self.avro_writer_schema_fingerprint = (
            avro_writer_schema_fingerprint
        )
        self.ready_protocol_version = ready_protocol_version
        self.poll_error_code = poll_error_code
        self.terminated = False
        self.spawn_kwargs: dict[str, object] = {}

    def accept(self, message: dict[str, Any]) -> None:
        self.commands.append(message)
        message_type = message["type"]
        if message_type == "START":
            checkpoint_timestamp = (
                message["resume_source_timestamp"]
                if message["resume_source_timestamp"] is not None
                else message["start_timestamp_seconds"]
            )
            ready = {
                "type": "READY",
                "first_record_offset": self.first_record_offset,
                "first_record_source_timestamp": (
                    self.first_record_source_timestamp
                ),
                "resume_checkpoint_present": (
                    message["resume_offset"] is not None
                ),
                "checkpoint_timestamp_seconds": checkpoint_timestamp,
                "transport": "official_dts_sdk",
                "subscribe_mode": "ASSIGN",
                "protocol_version": self.ready_protocol_version,
            }
            if self.avro_writer_schema_fingerprint is not None:
                ready["avro_writer_schema_fingerprint_sha256"] = (
                    self.avro_writer_schema_fingerprint
                )
            self.emit(ready)
        elif message_type == "POLL":
            if self.poll_error_code is not None:
                self.emit(
                    {
                        "type": "ERROR",
                        "error_code": self.poll_error_code,
                        "retriable": False,
                    }
                )
                return
            self.emit(
                {
                    "type": "EVENT",
                    "topic": "ovs-topic",
                    "partition": 0,
                    "offset": self.first_record_offset,
                    "source_timestamp": self.first_record_source_timestamp,
                    "record_bytes": _record_bytes(self.event_record),
                    "record": self.event_record,
                }
            )
            self.emit(
                {
                    "type": "BATCH_COMPLETE",
                    "seen": 1,
                    "batch_bytes": _record_bytes(self.event_record),
                    "transport_normalize_elapsed_ms": 0,
                }
            )
        elif message_type == "DURABLE_ACK_BATCH":
            acknowledgements = message["acks"]
            self.emit(
                {
                    "type": "SDK_CHECKPOINTS_ACCEPTED",
                    "seen": len(acknowledgements),
                    "advanced": sum(
                        acknowledgement["checkpoint_action"] == "ADVANCE"
                        for acknowledgement in acknowledgements
                    ),
                    "replayed": sum(
                        acknowledgement["checkpoint_action"] == "REPLAY"
                        for acknowledgement in acknowledgements
                    ),
                }
            )
        elif message_type == "CLOSE":
            self.emit({"type": "CLOSED"})
            self.lines.put(None)
            self.returncode = 0

    def emit(self, payload: dict[str, Any]) -> None:
        self.lines.put(json.dumps(payload) + "\n")

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 143
        self.lines.put(None)

    def kill(self) -> None:
        self.returncode = 137
        self.lines.put(None)


class _FakeBatchProcess(_FakeProcess):
    def __init__(
        self,
        *,
        offsets: tuple[int, ...],
        completed_seen: int | None = None,
    ) -> None:
        if not offsets:
            raise ValueError("offsets must not be empty")
        super().__init__(first_record_offset=offsets[0])
        self.offsets = offsets
        self.completed_seen = (
            len(offsets) if completed_seen is None else completed_seen
        )

    def accept(self, message: dict[str, Any]) -> None:
        if message["type"] != "POLL":
            super().accept(message)
            return
        self.commands.append(message)
        for offset in self.offsets:
            record = _event_record(offset=offset)
            self.emit(
                {
                    "type": "EVENT",
                    "topic": "ovs-topic",
                    "partition": 0,
                    "offset": offset,
                    "source_timestamp": record["sourceTimestamp"],
                    "record_bytes": _record_bytes(record),
                    "record": record,
                }
            )
        self.emit(
            {
                "type": "BATCH_COMPLETE",
                "seen": self.completed_seen,
                "batch_bytes": sum(
                    _record_bytes(_event_record(offset=offset))
                    for offset in self.offsets
                ),
                "transport_normalize_elapsed_ms": 0,
            }
        )


class _FakeLightweightProcess(_FakeProcess):
    def accept(self, message: dict[str, Any]) -> None:
        if message["type"] != "POLL":
            super().accept(message)
            return
        self.commands.append(message)
        self.emit(
            {
                "type": "EVENT",
                "topic": "ovs-topic",
                "partition": 0,
                "offset": self.first_record_offset,
                "source_timestamp": self.first_record_source_timestamp,
                "lightweight": True,
            }
        )
        self.emit(
            {
                "type": "BATCH_COMPLETE",
                "seen": 1,
                "batch_bytes": 0,
                "transport_normalize_elapsed_ms": 0,
            }
        )


def _settings() -> DtsConsumerSettings:
    return DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="ovs-topic",
        group_id="dtsgroup1234567890",
        account="consumer",
        password="runtime-secret",
        partition=0,
        execution_region="sg",
        start_timestamp_seconds=1786523400,
    )


def _factory(process: _FakeProcess):
    def build(command: object, **kwargs: object) -> _FakeProcess:
        assert tuple(command) == ("java", "bridge")
        assert kwargs["stderr"] is None
        process.spawn_kwargs = dict(kwargs)
        return process

    return build


def _patch_batch_event_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda *_args, **kwargs: SimpleNamespace(
            source_timestamp=1786550400 + int(kwargs["offset"]),
            offset=int(kwargs["offset"]),
        ),
    )
    monkeypatch.setattr(
        dts_java_transport,
        "prepare_change_event_for_ingest",
        lambda event, _settings: event,
    )


def _assert_batch_result(
    result: dict[str, int],
    *,
    requested_max_messages: int,
    seen: int,
    processed: int,
    ignored: int,
    duplicates: int,
    sdk_checkpoint_accepted: int,
    batch_bytes: int,
    durable_next_offset: int,
    transport_prefiltered: int = 0,
) -> None:
    assert result["db_elapsed_ms"] >= 0
    assert result["sdk_ack_elapsed_ms"] >= 0
    assert result["batch_elapsed_ms"] >= result["db_elapsed_ms"]
    assert result == {
        "requested_max_messages": requested_max_messages,
        "seen": seen,
        "processed": processed,
        "ignored": ignored,
        "duplicates": duplicates,
        "committed": 0,
        "transport_prefiltered": transport_prefiltered,
        "sdk_checkpoint_accepted": sdk_checkpoint_accepted,
        "batch_bytes": batch_bytes,
        "transport_normalize_elapsed_ms": (
            result["transport_normalize_elapsed_ms"]
        ),
        "event_normalize_elapsed_ms": result["event_normalize_elapsed_ms"],
        "db_elapsed_ms": result["db_elapsed_ms"],
        "sdk_ack_elapsed_ms": result["sdk_ack_elapsed_ms"],
        "batch_elapsed_ms": result["batch_elapsed_ms"],
        "durable_next_offset": durable_next_offset,
    }


def test_command_is_injectable_without_credentials() -> None:
    assert java_transport_command({}) == (
        "java",
        "-cp",
        "/deployments/config:/deployments/dts-transport.jar:"
        "/deployments/dts-diagnose.jar",
        "com.aliyun.dts.subscribe.clients.TitDtsTransportBridge",
    )
    assert java_transport_command(
        {"TIT_DTS_JAVA_TRANSPORT_COMMAND": "java -jar bridge.jar"}
    ) == ("java", "-jar", "bridge.jar")

    with pytest.raises(
        DtsJavaTransportError,
        match="^TIT_DTS_JAVA_TRANSPORT_COMMAND_INVALID$",
    ):
        java_transport_command(
            {"TIT_DTS_JAVA_TRANSPORT_COMMAND": "java 'unterminated"}
        )


def test_java_child_environment_excludes_database_and_hmac_secrets() -> None:
    child = java_child_environment(
        {
            "PATH": "/usr/bin",
            "JAVA_HOME": "/opt/java",
            "LANG": "C.UTF-8",
            "JAVA_TOOL_OPTIONS": "-Xmx256m",
            "TIT_DTS_INGEST_DB_PASSWORD": "database-secret",
            "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD": "hmac-secret",
            "TIT_DTS_PASSWORD": "kafka-secret",
        }
    )

    assert child == {
        "PATH": "/usr/bin",
        "JAVA_HOME": "/opt/java",
        "LANG": "C.UTF-8",
        "JAVA_TOOL_OPTIONS": "-Xmx256m",
    }


def test_database_write_precedes_ack_and_sdk_checkpoint_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    observed_commands_at_process: list[list[str]] = []

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            assert len(events) == 1
            observed_commands_at_process.append(
                [item["type"] for item in process.commands]
            )
            return (SimpleNamespace(status="PROCESSED"),)

    change_event = SimpleNamespace(source_timestamp=1786550400)
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda record, **kwargs: (
            change_event
            if record == process.event_record
            and kwargs
            == {
                "source_region": "ovs",
                "topic": "ovs-topic",
                "partition": 0,
                "offset": 42,
            }
            else (_ for _ in ()).throw(AssertionError("event contract"))
        ),
    )
    monkeypatch.setattr(
        dts_java_transport,
        "prepare_change_event_for_ingest",
        lambda event, settings: (
            event
            if settings.source_region == "ovs"
            else (_ for _ in ()).throw(AssertionError("privacy contract"))
        ),
    )
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    probe = transport.startup_probe()
    result = transport.run(max_messages=1, commit_offsets=True)
    transport.close()

    assert probe == {
        "status": "ok",
        "transport": "official_dts_sdk",
        "partition": 0,
        "first_record_offset": 42,
        "first_record_source_timestamp": 1786550400,
        "avro_writer_schema_compatible": True,
    }
    _assert_batch_result(
        result,
        requested_max_messages=1,
        seen=1,
        processed=1,
        ignored=0,
        duplicates=0,
        sdk_checkpoint_accepted=1,
        batch_bytes=_record_bytes(process.event_record),
        durable_next_offset=43,
    )
    assert observed_commands_at_process == [["START", "POLL"]]
    assert [item["type"] for item in process.commands] == [
        "START",
        "POLL",
        "DURABLE_ACK_BATCH",
        "CLOSE",
    ]
    assert process.commands[0]["resume_offset"] == 42
    assert process.commands[0]["resume_source_timestamp"] == 1786550300
    assert process.commands[0]["password"] == "runtime-secret"
    assert process.commands[0]["supported_table_names"] == sorted(
        f"ovs_{suffix}"
        for suffix in dts_source_consumer.SUPPORTED_TABLE_SUFFIXES_BY_REGION[
            "ovs"
        ]
    )
    assert process.commands[0]["data_operations"] == sorted(
        dts_source_consumer.DATA_OPERATIONS
    )
    assert process.commands[0]["control_operations"] == sorted(
        dts_source_consumer.CONTROL_OPERATIONS
    )
    assert process.commands[0]["lightweight_prefilter_enabled"] is False
    assert (
        process.commands[0]["protocol_version"]
        == JAVA_TRANSPORT_PROTOCOL_VERSION
    )
    assert process.commands[0]["source_field_whitelist"] == {
        f"ovs_{suffix}": sorted(
            dts_source_consumer.SOURCE_FIELD_WHITELIST[suffix]
        )
        for suffix in dts_source_consumer.SUPPORTED_TABLE_SUFFIXES_BY_REGION[
            "ovs"
        ]
    }
    child_env = process.spawn_kwargs["env"]
    assert isinstance(child_env, dict)
    assert not any(name.startswith("TIT_") for name in child_env)
    assert process.commands[2] == {
        "type": "DURABLE_ACK_BATCH",
        "acks": [
            {
                "offset": 42,
                "next_offset": 43,
                "source_timestamp": 1786550400,
                "checkpoint_action": "ADVANCE",
            }
        ],
    }


def test_lightweight_event_skips_avro_and_still_durably_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeLightweightProcess()
    observed: list[object] = []

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            assert len(events) == 1
            event = events[0]
            observed.append(event)
            assert isinstance(event, dts_source_consumer.DtsChangeEvent)
            assert event.operation == "NOOP"
            assert event.table_name is None
            assert event.offset == 42
            assert event.source_timestamp == 1786550400
            return (SimpleNamespace(status="IGNORED"),)

    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        lightweight_prefilter_enabled=True,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    transport.startup_probe()
    result = transport.run(max_messages=1, commit_offsets=True)
    transport.close()

    assert len(observed) == 1
    _assert_batch_result(
        result,
        requested_max_messages=1,
        seen=1,
        processed=0,
        ignored=1,
        duplicates=0,
        sdk_checkpoint_accepted=1,
        batch_bytes=0,
        durable_next_offset=43,
        transport_prefiltered=1,
    )
    assert process.commands[2] == {
        "type": "DURABLE_ACK_BATCH",
        "acks": [
            {
                "offset": 42,
                "next_offset": 43,
                "source_timestamp": 1786550400,
                "checkpoint_action": "ADVANCE",
            }
        ],
    }


@pytest.mark.parametrize(
    ("message", "error_code"),
    [
        (
            {
                "topic": "ovs-topic",
                "partition": 0,
                "offset": 42,
                "source_timestamp": 1786550400,
                "lightweight": True,
                "payload_base64": "YXY=",
            },
            "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID",
        ),
        (
            {
                "topic": "ovs-topic",
                "partition": 0,
                "offset": 42,
                "lightweight": True,
            },
            "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID",
        ),
    ],
    ids=["payload-present", "timestamp-missing"],
)
def test_malformed_lightweight_event_is_rejected(
    message: dict[str, Any],
    error_code: str,
) -> None:
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(_FakeProcess()),
    )

    with pytest.raises(DtsJavaTransportError, match=f"^{error_code}$"):
        transport._parse_event(message)


@pytest.mark.parametrize(
    "message",
    [
        {
            "topic": "ovs-topic",
            "partition": 0,
            "offset": 42,
            "source_timestamp": 1786550400,
            "record_bytes": 4,
            "payload_base64": "YXY=",
        },
        {
            "topic": "ovs-topic",
            "partition": 0,
            "offset": 42,
            "source_timestamp": 1786550400,
            "record_bytes": 0,
            "record": {},
        },
        {
            "topic": "ovs-topic",
            "partition": 0,
            "offset": 42,
            "record_bytes": 2,
            "record": {},
        },
    ],
    ids=["legacy-base64", "zero-bytes", "timestamp-missing"],
)
def test_malformed_structured_event_is_rejected(
    message: dict[str, Any],
) -> None:
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(_FakeProcess()),
    )

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID$|"
        "^DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID$",
    ):
        transport._parse_event(message)


def test_structured_record_timestamp_mismatch_never_reaches_database_or_ack(
) -> None:
    process = _FakeProcess(
        event_record=_event_record(source_timestamp=1786550399)
    )

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise AssertionError("timestamp mismatch must precede DB write")

    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_EVENT_TIMESTAMP_MISMATCH$",
    ):
        transport.run(max_messages=1, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_failed_database_transaction_never_acknowledges_java(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise RuntimeError("database transaction failed")

    change_event = SimpleNamespace(source_timestamp=1786550400)
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda *_args, **_kwargs: change_event,
    )
    monkeypatch.setattr(
        dts_java_transport,
        "prepare_change_event_for_ingest",
        lambda event, _settings: event,
    )
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(RuntimeError, match="database transaction failed"):
        transport.run(max_messages=1, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_three_events_use_one_database_batch_before_one_durable_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeBatchProcess(offsets=(42, 43, 44))
    observed_commands: list[list[str]] = []
    observed_offsets: list[tuple[int, ...]] = []

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            observed_commands.append(
                [item["type"] for item in process.commands]
            )
            observed_offsets.append(tuple(event.offset for event in events))
            return tuple(
                SimpleNamespace(status=status)
                for status in ("PROCESSED", "IGNORED", "PROCESSED")
            )

    _patch_batch_event_decoding(monkeypatch)
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    transport.startup_probe()
    result = transport.run(max_messages=3, commit_offsets=True)
    transport.close()

    assert observed_commands == [["START", "POLL"]]
    assert observed_offsets == [(42, 43, 44)]
    _assert_batch_result(
        result,
        requested_max_messages=3,
        seen=3,
        processed=2,
        ignored=1,
        duplicates=0,
        sdk_checkpoint_accepted=3,
        batch_bytes=sum(
            _record_bytes(_event_record(offset=offset))
            for offset in (42, 43, 44)
        ),
        durable_next_offset=45,
    )
    assert [item["type"] for item in process.commands] == [
        "START",
        "POLL",
        "DURABLE_ACK_BATCH",
        "CLOSE",
    ]
    assert process.commands[2] == {
        "type": "DURABLE_ACK_BATCH",
        "acks": [
            {
                "offset": offset,
                "next_offset": offset + 1,
                "source_timestamp": 1786550400 + offset,
                "checkpoint_action": "ADVANCE",
            }
            for offset in (42, 43, 44)
        ],
    }


def test_failed_database_batch_sends_zero_acknowledgements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeBatchProcess(offsets=(42, 43, 44))
    observed_offsets: list[tuple[int, ...]] = []

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            observed_offsets.append(tuple(event.offset for event in events))
            raise RuntimeError("database batch rolled back")

    _patch_batch_event_decoding(monkeypatch)
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(RuntimeError, match="database batch rolled back"):
        transport.run(max_messages=3, commit_offsets=True)
    transport.close(force=True)

    assert observed_offsets == [(42, 43, 44)]
    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_batch_replay_and_advances_preserve_checkpoint_order_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeBatchProcess(offsets=(41, 42, 43))

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            assert tuple(event.offset for event in events) == (41, 42, 43)
            return tuple(
                SimpleNamespace(status=status)
                for status in ("DUPLICATE", "PROCESSED", "IGNORED")
            )

    _patch_batch_event_decoding(monkeypatch)
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    transport.startup_probe()
    result = transport.run(max_messages=3, commit_offsets=True)
    transport.close()

    _assert_batch_result(
        result,
        requested_max_messages=3,
        seen=3,
        processed=1,
        ignored=1,
        duplicates=1,
        sdk_checkpoint_accepted=2,
        batch_bytes=sum(
            _record_bytes(_event_record(offset=offset))
            for offset in (41, 42, 43)
        ),
        durable_next_offset=44,
    )
    assert process.commands[2] == {
        "type": "DURABLE_ACK_BATCH",
        "acks": [
            {
                "offset": 41,
                "next_offset": 42,
                "source_timestamp": 1786550441,
                "checkpoint_action": "REPLAY",
            },
            {
                "offset": 42,
                "next_offset": 43,
                "source_timestamp": 1786550442,
                "checkpoint_action": "ADVANCE",
            },
            {
                "offset": 43,
                "next_offset": 44,
                "source_timestamp": 1786550443,
                "checkpoint_action": "ADVANCE",
            },
        ],
    }


def test_batch_count_mismatch_fails_before_database_and_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeBatchProcess(offsets=(42, 43, 44), completed_seen=2)

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise AssertionError("invalid batch must not reach database")

    _patch_batch_event_decoding(monkeypatch)
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_BATCH_COUNT_MISMATCH$",
    ):
        transport.run(max_messages=3, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_official_generated_union_payload_reaches_durable_ack() -> None:
    raw = {
        "id": 8202,
        "sourceTimestamp": 1786550400,
        "sourcePosition": "lsn:3",
        "safeSourcePosition": "lsn:3",
        "sourceTxid": "tx-3",
        "operation": "INSERT",
        "objectName": "public.ovs_appoint",
        "tags": {},
        "fields": ["id", "nullable_value"],
        "beforeImages": None,
        "afterImages": [
            {"precision": 20, "value": "8"},
            "NONE",
        ],
    }
    process = _FakeProcess(event_record=raw)
    observed_commands_at_process: list[list[str]] = []

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            assert len(events) == 1
            event = events[0]
            observed_commands_at_process.append(
                [item["type"] for item in process.commands]
            )
            assert event.source_timestamp == 1786550400
            assert event.table_name == "ovs_appoint"
            assert event.after == {"id": "8", "nullable_value": None}
            return (SimpleNamespace(status="PROCESSED"),)

    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    transport.startup_probe()
    result = transport.run(max_messages=1, commit_offsets=True)
    transport.close()

    assert observed_commands_at_process == [["START", "POLL"]]
    assert result["processed"] == 1
    assert result["sdk_checkpoint_accepted"] == 1
    assert [item["type"] for item in process.commands] == [
        "START",
        "POLL",
        "DURABLE_ACK_BATCH",
        "CLOSE",
    ]


def test_official_java_normalization_failure_never_reaches_database_or_ack() -> None:
    process = _FakeProcess(
        poll_error_code="DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED"
    )

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise AssertionError(
                "normalization failure must precede database write"
            )

    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED$",
    ):
        transport.run(max_messages=1, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_first_official_record_ahead_of_database_is_fail_closed() -> None:
    process = _FakeProcess(first_record_offset=43)
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_FIRST_RECORD_AHEAD_OF_DATABASE$",
    ):
        transport.startup_probe()

    assert process.terminated is True


@pytest.mark.parametrize(
    "fingerprint",
    [None, "0" * 64],
    ids=["missing", "mismatch"],
)
def test_official_writer_schema_mismatch_is_rejected_before_poll(
    fingerprint: str | None,
) -> None:
    process = _FakeProcess(
        avro_writer_schema_fingerprint=fingerprint,
    )
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_AVRO_SCHEMA_MISMATCH$",
    ):
        transport.startup_probe()

    assert process.terminated is True
    assert [item["type"] for item in process.commands] == ["START"]


def test_protocol_version_mismatch_is_rejected_before_poll() -> None:
    process = _FakeProcess(ready_protocol_version=1)
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_READY_IDENTITY_MISMATCH$",
    ):
        transport.startup_probe()

    assert process.terminated is True
    assert [item["type"] for item in process.commands] == ["START"]


def test_timestamp_replay_is_durable_but_does_not_advance_sdk_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(first_record_offset=41)

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            assert len(events) == 1
            return (SimpleNamespace(status="DUPLICATE"),)

    change_event = SimpleNamespace(source_timestamp=1786550400)
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda *_args, **_kwargs: change_event,
    )
    monkeypatch.setattr(
        dts_java_transport,
        "prepare_change_event_for_ingest",
        lambda event, _settings: event,
    )
    transport = OfficialJavaDtsTransport(
        _settings(),
        Processor(),  # type: ignore[arg-type]
        resume_offset=42,
        resume_source_timestamp=1786550300,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    transport.startup_probe()
    result = transport.run(max_messages=1, commit_offsets=True)
    transport.close()

    _assert_batch_result(
        result,
        requested_max_messages=1,
        seen=1,
        processed=0,
        ignored=0,
        duplicates=1,
        sdk_checkpoint_accepted=0,
        batch_bytes=_record_bytes(process.event_record),
        durable_next_offset=42,
    )
    assert process.commands[2] == {
        "type": "DURABLE_ACK_BATCH",
        "acks": [
            {
                "offset": 41,
                "next_offset": 42,
                "source_timestamp": 1786550400,
                "checkpoint_action": "REPLAY",
            }
        ],
    }


def test_first_official_record_initializes_empty_database_checkpoint() -> None:
    process = _FakeProcess(first_record_offset=17)
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=None,
        resume_source_timestamp=None,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    probe = transport.startup_probe()
    transport.close()

    assert probe["first_record_offset"] == 17
    assert process.commands[0]["resume_offset"] is None
    assert process.commands[0]["resume_source_timestamp"] is None


def test_resume_checkpoint_requires_offset_and_timestamp_together() -> None:
    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_DATABASE_CHECKPOINT_INCOMPLETE$",
    ):
        OfficialJavaDtsTransport(
            _settings(),
            SimpleNamespace(),  # type: ignore[arg-type]
            resume_offset=42,
            resume_source_timestamp=None,
        )


def test_empty_database_requires_a_configured_start_timestamp() -> None:
    settings = replace(_settings(), start_timestamp_seconds=None)

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_INITIAL_CHECKPOINT_REQUIRED$",
    ):
        OfficialJavaDtsTransport(
            settings,
            SimpleNamespace(),  # type: ignore[arg-type]
            resume_offset=None,
            resume_source_timestamp=None,
        )
