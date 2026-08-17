from __future__ import annotations

import base64
import io
import json
import queue
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastavro import schemaless_writer

from app import dts_java_transport, dts_source_consumer
from app.dts_java_transport import (
    DtsJavaTransportError,
    OfficialJavaDtsTransport,
    java_child_environment,
    java_transport_command,
)
from app.dts_source_consumer import (
    DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256,
    DtsConsumerSettings,
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
        event_payload: bytes = b"avro",
        first_record_offset: int = 42,
        first_record_source_timestamp: int = 1786550400,
        avro_writer_schema_fingerprint: str | None = (
            DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256
        ),
        poll_error_code: str | None = None,
    ) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stdout = _FakeStdout(self.lines)
        self.stdin = _FakeStdin(self)
        self.returncode: int | None = None
        self.commands: list[dict[str, Any]] = []
        self.event_payload = event_payload
        self.first_record_offset = first_record_offset
        self.first_record_source_timestamp = first_record_source_timestamp
        self.avro_writer_schema_fingerprint = (
            avro_writer_schema_fingerprint
        )
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
                    "payload_base64": base64.b64encode(
                        self.event_payload
                    ).decode("ascii"),
                }
            )
            self.emit({"type": "BATCH_COMPLETE", "seen": 1})
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
            self.emit(
                {
                    "type": "EVENT",
                    "topic": "ovs-topic",
                    "partition": 0,
                    "offset": offset,
                    "payload_base64": base64.b64encode(b"avro").decode(
                        "ascii"
                    ),
                }
            )
        self.emit(
            {"type": "BATCH_COMPLETE", "seen": self.completed_seen}
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
        "decode_dts_sdk_1_4_avro",
        lambda _: {},
    )
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
        "protect_domestic_student_ids",
        lambda event, _settings: event,
    )


def _assert_batch_result(
    result: dict[str, int],
    *,
    seen: int,
    processed: int,
    ignored: int,
    duplicates: int,
    sdk_checkpoint_accepted: int,
    batch_bytes: int,
    durable_next_offset: int,
) -> None:
    assert result["db_elapsed_ms"] >= 0
    assert result["sdk_ack_elapsed_ms"] >= 0
    assert result["batch_elapsed_ms"] >= result["db_elapsed_ms"]
    assert result == {
        "seen": seen,
        "processed": processed,
        "ignored": ignored,
        "duplicates": duplicates,
        "committed": 0,
        "sdk_checkpoint_accepted": sdk_checkpoint_accepted,
        "batch_bytes": batch_bytes,
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
        "decode_dts_sdk_1_4_avro",
        lambda payload: {"payload": payload},
    )
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda record, **kwargs: (
            change_event
            if record == {"payload": b"avro"}
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
        "protect_domestic_student_ids",
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
        seen=1,
        processed=1,
        ignored=0,
        duplicates=0,
        sdk_checkpoint_accepted=1,
        batch_bytes=4,
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
        "decode_dts_sdk_1_4_avro",
        lambda _: {},
    )
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda *_args, **_kwargs: change_event,
    )
    monkeypatch.setattr(
        dts_java_transport,
        "protect_domestic_student_ids",
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
        seen=3,
        processed=2,
        ignored=1,
        duplicates=0,
        sdk_checkpoint_accepted=3,
        batch_bytes=12,
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
        seen=3,
        processed=1,
        ignored=1,
        duplicates=1,
        sdk_checkpoint_accepted=2,
        batch_bytes=12,
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
        "version": 1,
        "id": 8202,
        "sourceTimestamp": 1786550400,
        "sourcePosition": "lsn:3",
        "safeSourcePosition": "lsn:3",
        "sourceTxid": "tx-3",
        "source": {"sourceType": "PostgreSQL", "version": "14"},
        "operation": "INSERT",
        "objectName": "public.ovs_appoint",
        "processTimestamps": None,
        "tags": {},
        "fields": [
            {"name": "id", "dataTypeNumber": 20},
            {"name": "nullable_value", "dataTypeNumber": 12},
        ],
        "beforeImages": None,
        "afterImages": [
            (
                "com.alibaba.dts.formats.avro.Integer",
                {"precision": 20, "value": "8"},
            ),
            ("com.alibaba.dts.formats.avro.EmptyObject", "NONE"),
        ],
    }
    payload = io.BytesIO()
    schemaless_writer(
        payload,
        dts_source_consumer._parsed_dts_sdk_1_4_avro_writer_schema(),
        raw,
    )
    process = _FakeProcess(event_payload=payload.getvalue())
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


def test_official_java_encoding_failure_never_reaches_database_or_ack() -> None:
    process = _FakeProcess(
        poll_error_code="DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED"
    )

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise AssertionError("encoding failure must precede database write")

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
        match="^DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED$",
    ):
        transport.run(max_messages=1, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_single_object_header_leak_never_reaches_database_or_ack() -> None:
    # The Java bridge must strip Avro's C3 01 + fingerprint envelope before
    # sending the schemaless datum expected by the Python reader.
    process = _FakeProcess(
        event_payload=b"\xc3\x01" + (b"\x00" * 8) + b"not-a-datum"
    )

    class Processor:
        def process_batch(self, events: tuple[object, ...]) -> tuple[object, ...]:
            del events
            raise AssertionError("invalid envelope must fail before DB write")

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
        dts_source_consumer.DtsRecordError,
        match="^DTS_AVRO_DECODE_FAILED$",
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
        "decode_dts_sdk_1_4_avro",
        lambda _: {},
    )
    monkeypatch.setattr(
        dts_java_transport,
        "build_change_event",
        lambda *_args, **_kwargs: change_event,
    )
    monkeypatch.setattr(
        dts_java_transport,
        "protect_domestic_student_ids",
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
        seen=1,
        processed=0,
        ignored=0,
        duplicates=1,
        sdk_checkpoint_accepted=0,
        batch_bytes=4,
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
