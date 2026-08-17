"""Official Java DTS transport controlled by the Python ingest process.

The Java child owns only Kafka/DTS transport.  Python remains the authority
for decoding, privacy protection, durable PostgreSQL writes, projection and
health.  The two processes exchange one JSON object per line so the child can
commit a Kafka offset only after Python confirms that the corresponding
database transaction is durable.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, IO

from .dts_source_consumer import (
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    build_change_event,
    decode_dts_avro,
    protect_domestic_student_ids,
)


JAVA_TRANSPORT_COMMAND_ENV = "TIT_DTS_JAVA_TRANSPORT_COMMAND"
DEFAULT_JAVA_TRANSPORT_COMMAND = (
    "java",
    "-cp",
    "/deployments/dts-transport.jar:/deployments/dts-diagnose.jar",
    "com.aliyun.dts.subscribe.clients.TitDtsTransportBridge",
)
JAVA_TRANSPORT_START_TIMEOUT_SECONDS = 305.0
JAVA_TRANSPORT_CLOSE_TIMEOUT_SECONDS = 5.0
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_EOF = object()
_JAVA_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
    }
)


class DtsJavaTransportError(DtsConfigurationError):
    """A safe, classified failure from the Java transport boundary."""

    def __init__(self, error_code: str, *, retriable: bool = False) -> None:
        if _ERROR_CODE_PATTERN.fullmatch(error_code) is None:
            error_code = "DTS_OFFICIAL_JAVA_TRANSPORT_ERROR_INVALID"
            retriable = False
        super().__init__(error_code)
        self.error_code = error_code
        self.retriable = retriable


@dataclass(frozen=True)
class _JavaEvent:
    payload: bytes
    topic: str
    partition: int
    offset: int


def java_transport_command(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Resolve the bridge command without putting credentials in argv."""

    values = os.environ if environ is None else environ
    raw = values.get(JAVA_TRANSPORT_COMMAND_ENV, "").strip()
    if not raw:
        return DEFAULT_JAVA_TRANSPORT_COMMAND
    try:
        command = tuple(shlex.split(raw))
    except ValueError as exc:
        raise DtsJavaTransportError(
            "TIT_DTS_JAVA_TRANSPORT_COMMAND_INVALID"
        ) from exc
    if not command or any("\x00" in part for part in command):
        raise DtsJavaTransportError(
            "TIT_DTS_JAVA_TRANSPORT_COMMAND_INVALID"
        )
    return command


def java_child_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only process-locale/JVM settings, never Pod application secrets."""

    values = os.environ if environ is None else environ
    return {
        name: value
        for name, value in values.items()
        if name in _JAVA_CHILD_ENV_ALLOWLIST
    }


class OfficialJavaDtsTransport:
    """Long-lived official Java transport with database-before-Kafka ACKs."""

    def __init__(
        self,
        settings: DtsConsumerSettings,
        processor: DtsEventProcessor,
        *,
        resume_offset: int | None,
        idle_timeout_ms: int = 10_000,
        command: Sequence[str] | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        if resume_offset is not None and (
            isinstance(resume_offset, bool)
            or not isinstance(resume_offset, int)
            or resume_offset < 0
        ):
            raise DtsJavaTransportError("DTS_DATABASE_OFFSET_INVALID")
        if (
            isinstance(idle_timeout_ms, bool)
            or not isinstance(idle_timeout_ms, int)
            or idle_timeout_ms < 1
        ):
            raise DtsJavaTransportError("DTS_IDLE_TIMEOUT_INVALID")
        self.settings = settings
        self.processor = processor
        self.resume_offset = resume_offset
        self.idle_timeout_ms = idle_timeout_ms
        self._command = tuple(command or java_transport_command())
        self._process_factory = process_factory
        self._monotonic = monotonic
        self._stop_requested = stop_requested or (lambda: False)
        self._process: Any | None = None
        self._messages: queue.Queue[object] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._initial_offset: int | None = None
        self._expected_offset: int | None = resume_offset

    def startup_probe(
        self,
        *,
        phase_callback: Callable[[dict[str, bool | int | str]], None]
        | None = None,
    ) -> dict[str, int | str]:
        """Start the child and resolve its initial offset without DB writes."""

        if self._started:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_ALREADY_STARTED"
            )
        if phase_callback is not None:
            phase_callback(
                {
                    "phase": "official_java_start",
                    "request_type": "START",
                    "status": "begin",
                }
            )
        self._spawn()
        try:
            self._send(
                {
                    "type": "START",
                    "broker_urls": list(self.settings.broker_urls),
                    "topic": self.settings.topic,
                    "group_id": self.settings.group_id,
                    "account": self.settings.account,
                    "password": self.settings.password,
                    "partition": self.settings.partition,
                    "resume_offset": self.resume_offset,
                    "start_timestamp_seconds": (
                        self.settings.start_timestamp_seconds
                    ),
                    "idle_timeout_ms": self.idle_timeout_ms,
                }
            )
            ready = self._receive(
                expected={"READY"},
                timeout_seconds=JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
            )
            initial_offset = self._required_non_negative_int(
                ready,
                "initial_offset",
            )
            if (
                self.resume_offset is not None
                and initial_offset != self.resume_offset
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_RESUME_OFFSET_MISMATCH"
                )
            committed_present = ready.get("committed_present")
            if not isinstance(committed_present, bool):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                )
            for field in ("begin_offset", "end_offset"):
                value = ready.get(field)
                if value is not None:
                    self._required_non_negative_int(ready, field)
            self._initial_offset = initial_offset
            self._expected_offset = initial_offset
            self._started = True
            if phase_callback is not None:
                phase_callback(
                    {
                        "phase": "official_java_start",
                        "request_type": "START",
                        "status": "ok",
                        "transport": "official_java",
                        "initial_offset": initial_offset,
                        "committed_present": committed_present,
                    }
                )
            return {
                "status": "ok",
                "transport": "official_java",
                "partition": self.settings.partition,
                "initial_offset": initial_offset,
            }
        except Exception:
            self.close(force=True)
            raise

    def run(self, *, max_messages: int, commit_offsets: bool) -> dict[str, int]:
        """Process one bounded child poll and commit only durable events."""

        if not self._started or self._closed:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_NOT_READY"
            )
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        if not commit_offsets:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_DURABLE_ACK_REQUIRED"
            )
        counters = {
            "seen": 0,
            "processed": 0,
            "ignored": 0,
            "duplicates": 0,
            "committed": 0,
        }
        self._send({"type": "POLL", "max_messages": max_messages})
        poll_timeout = max(
            JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
            self.idle_timeout_ms / 1000 + 5.0,
        )
        while True:
            message = self._receive(
                expected={"EVENT", "BATCH_COMPLETE"},
                timeout_seconds=poll_timeout,
            )
            if message["type"] == "BATCH_COMPLETE":
                child_seen = self._required_non_negative_int(message, "seen")
                if child_seen != counters["seen"]:
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_BATCH_COUNT_MISMATCH"
                    )
                return counters

            event = self._parse_event(message)
            if (
                self._expected_offset is not None
                and event.offset != self._expected_offset
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_EVENT_OFFSET_NOT_CONTIGUOUS"
                )
            record = decode_dts_avro(event.payload)
            change_event = build_change_event(
                record,
                source_region=self.settings.source_region,
                topic=event.topic,
                partition=event.partition,
                offset=event.offset,
            )
            change_event = protect_domestic_student_ids(
                change_event,
                self.settings,
            )
            # PostgresDtsEventSink.apply() commits its transaction before this
            # call returns.  Do not acknowledge Java before that boundary.
            result = self.processor.process(change_event)
            counters["seen"] += 1
            if result.status == "PROCESSED":
                counters["processed"] += 1
            elif result.status == "DUPLICATE":
                counters["duplicates"] += 1
            else:
                counters["ignored"] += 1

            next_offset = event.offset + 1
            self._send(
                {
                    "type": "DURABLE_ACK",
                    "offset": event.offset,
                    "next_offset": next_offset,
                    "source_timestamp": change_event.source_timestamp,
                }
            )
            committed = self._receive(
                expected={"COMMITTED"},
                timeout_seconds=JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
            )
            if (
                self._required_non_negative_int(committed, "offset")
                != event.offset
                or self._required_non_negative_int(
                    committed,
                    "next_offset",
                )
                != next_offset
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_COMMIT_MISMATCH"
                )
            counters["committed"] += 1
            self._expected_offset = next_offset

    def close(self, *, force: bool = False) -> None:
        """Close the protocol and reap the child without exposing stderr."""

        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if not force and process.poll() is None:
            try:
                self._send({"type": "CLOSE"})
                self._receive(
                    expected={"CLOSED"},
                    timeout_seconds=JAVA_TRANSPORT_CLOSE_TIMEOUT_SECONDS,
                    allow_closed=True,
                )
            except Exception:
                force = True
        if force and process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=JAVA_TRANSPORT_CLOSE_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, TimeoutError):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=JAVA_TRANSPORT_CLOSE_TIMEOUT_SECONDS)
        for stream_name in ("stdin", "stdout"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _spawn(self) -> None:
        try:
            process = self._process_factory(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # The bridge owns safe operational logging on stderr.  stdout
                # is exclusively the machine protocol.
                stderr=None,
                text=True,
                encoding="utf-8",
                bufsize=1,
                close_fds=True,
                env=java_child_environment(),
            )
        except (OSError, ValueError) as exc:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_START_FAILED"
            ) from exc
        if process.stdin is None or process.stdout is None:
            try:
                process.terminate()
            finally:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PIPE_REQUIRED"
                )
        self._process = process
        self._reader = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name="dts-java-transport-stdout",
            daemon=True,
        )
        self._reader.start()

    def _read_stdout(self, stdout: IO[str]) -> None:
        try:
            while True:
                line = stdout.readline()
                if line == "":
                    break
                self._messages.put(line)
        finally:
            self._messages.put(_EOF)

    def _send(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_UNAVAILABLE",
                retriable=True,
            )
        try:
            process.stdin.write(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_UNAVAILABLE",
                retriable=True,
            ) from exc

    def _receive(
        self,
        *,
        expected: set[str],
        timeout_seconds: float,
        allow_closed: bool = False,
    ) -> dict[str, Any]:
        deadline = self._monotonic() + timeout_seconds
        while True:
            if self._stop_requested() and not allow_closed:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_STOP_REQUESTED"
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_TIMEOUT",
                    retriable=True,
                )
            try:
                item = self._messages.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if item is _EOF:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_UNAVAILABLE",
                    retriable=True,
                )
            if not isinstance(item, str):  # pragma: no cover - internal guard
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                )
            try:
                message = json.loads(item)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                ) from exc
            if not isinstance(message, dict):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                )
            message_type = message.get("type")
            if message_type == "ERROR":
                code = message.get("error_code")
                retriable = message.get("retriable", False)
                if not isinstance(code, str) or not isinstance(retriable, bool):
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                    )
                raise DtsJavaTransportError(code, retriable=retriable)
            if not isinstance(message_type, str) or message_type not in expected:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                )
            return message

    def _parse_event(self, message: Mapping[str, Any]) -> _JavaEvent:
        topic = message.get("topic")
        if topic != self.settings.topic:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_TOPIC_MISMATCH"
            )
        partition = self._required_non_negative_int(message, "partition")
        if partition != self.settings.partition:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_PARTITION_MISMATCH"
            )
        offset = self._required_non_negative_int(message, "offset")
        payload_base64 = message.get("payload_base64")
        if not isinstance(payload_base64, str) or not payload_base64:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID"
            )
        try:
            payload = base64.b64decode(payload_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID"
            ) from exc
        if not payload:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID"
            )
        return _JavaEvent(
            payload=payload,
            topic=topic,
            partition=partition,
            offset=offset,
        )

    @staticmethod
    def _required_non_negative_int(
        payload: Mapping[str, Any],
        field: str,
    ) -> int:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
            )
        return value


__all__ = [
    "DEFAULT_JAVA_TRANSPORT_COMMAND",
    "DtsJavaTransportError",
    "OfficialJavaDtsTransport",
    "java_child_environment",
    "java_transport_command",
]
