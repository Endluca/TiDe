from __future__ import annotations

import base64
import json
import queue
from types import SimpleNamespace
from typing import Any

import pytest

from app import dts_java_transport
from app.dts_java_transport import (
    DtsJavaTransportError,
    OfficialJavaDtsTransport,
    java_child_environment,
    java_transport_command,
)
from app.dts_source_consumer import DtsConsumerSettings


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
        ready_initial_offset: int = 42,
        fail_processor: bool = False,
    ) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stdout = _FakeStdout(self.lines)
        self.stdin = _FakeStdin(self)
        self.returncode: int | None = None
        self.commands: list[dict[str, Any]] = []
        self.event_payload = event_payload
        self.ready_initial_offset = ready_initial_offset
        self.fail_processor = fail_processor
        self.terminated = False
        self.spawn_kwargs: dict[str, object] = {}

    def accept(self, message: dict[str, Any]) -> None:
        self.commands.append(message)
        message_type = message["type"]
        if message_type == "START":
            self.emit(
                {
                    "type": "READY",
                    "initial_offset": self.ready_initial_offset,
                    "committed_present": True,
                    "begin_offset": 0,
                    "end_offset": 100,
                }
            )
        elif message_type == "POLL":
            self.emit(
                {
                    "type": "EVENT",
                    "topic": "ovs-topic",
                    "partition": 0,
                    "offset": self.ready_initial_offset,
                    "payload_base64": base64.b64encode(
                        self.event_payload
                    ).decode("ascii"),
                }
            )
        elif message_type == "DURABLE_ACK":
            self.emit(
                {
                    "type": "COMMITTED",
                    "offset": message["offset"],
                    "next_offset": message["next_offset"],
                }
            )
            self.emit({"type": "BATCH_COMPLETE", "seen": 1})
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
    )


def _factory(process: _FakeProcess):
    def build(command: object, **kwargs: object) -> _FakeProcess:
        assert tuple(command) == ("java", "bridge")
        assert kwargs["stderr"] is None
        process.spawn_kwargs = dict(kwargs)
        return process

    return build


def test_command_is_injectable_without_credentials() -> None:
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


def test_database_write_precedes_ack_and_commit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    observed_commands_at_process: list[list[str]] = []

    class Processor:
        def process(self, event: object) -> object:
            del event
            observed_commands_at_process.append(
                [item["type"] for item in process.commands]
            )
            return SimpleNamespace(status="PROCESSED")

    change_event = SimpleNamespace(source_timestamp=1786550400)
    monkeypatch.setattr(
        dts_java_transport,
        "decode_dts_avro",
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
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    probe = transport.startup_probe()
    result = transport.run(max_messages=1, commit_offsets=True)
    transport.close()

    assert probe == {
        "status": "ok",
        "transport": "official_java",
        "partition": 0,
        "initial_offset": 42,
    }
    assert result == {
        "seen": 1,
        "processed": 1,
        "ignored": 0,
        "duplicates": 0,
        "committed": 1,
    }
    assert observed_commands_at_process == [["START", "POLL"]]
    assert [item["type"] for item in process.commands] == [
        "START",
        "POLL",
        "DURABLE_ACK",
        "CLOSE",
    ]
    assert process.commands[0]["resume_offset"] == 42
    assert process.commands[0]["password"] == "runtime-secret"
    child_env = process.spawn_kwargs["env"]
    assert isinstance(child_env, dict)
    assert not any(name.startswith("TIT_") for name in child_env)
    assert process.commands[2] == {
        "type": "DURABLE_ACK",
        "offset": 42,
        "next_offset": 43,
        "source_timestamp": 1786550400,
    }


def test_failed_database_transaction_never_acknowledges_java(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()

    class Processor:
        def process(self, event: object) -> object:
            del event
            raise RuntimeError("database transaction failed")

    change_event = SimpleNamespace(source_timestamp=1786550400)
    monkeypatch.setattr(dts_java_transport, "decode_dts_avro", lambda _: {})
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
        command=("java", "bridge"),
        process_factory=_factory(process),
    )
    transport.startup_probe()

    with pytest.raises(RuntimeError, match="database transaction failed"):
        transport.run(max_messages=1, commit_offsets=True)
    transport.close(force=True)

    assert [item["type"] for item in process.commands] == ["START", "POLL"]


def test_database_resume_offset_is_fail_closed() -> None:
    process = _FakeProcess(ready_initial_offset=41)
    transport = OfficialJavaDtsTransport(
        _settings(),
        SimpleNamespace(),  # type: ignore[arg-type]
        resume_offset=42,
        command=("java", "bridge"),
        process_factory=_factory(process),
    )

    with pytest.raises(
        DtsJavaTransportError,
        match="^DTS_OFFICIAL_JAVA_RESUME_OFFSET_MISMATCH$",
    ):
        transport.startup_probe()

    assert process.terminated is True
