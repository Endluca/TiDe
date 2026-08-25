"""Official Java DTS transport controlled by the Python ingest process.

The Java child owns only Kafka/DTS transport.  Python remains the authority
for decoding, privacy protection, durable PostgreSQL writes, projection and
health.  The two processes exchange one JSON object per line so the child can
accept an official SDK checkpoint only after Python confirms that the
corresponding database transaction is durable.  This protocol never claims a
synchronous Kafka broker commit.
"""

from __future__ import annotations

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
    CONTROL_OPERATIONS,
    DATA_OPERATIONS,
    DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256,
    SOURCE_FIELD_WHITELIST,
    SUPPORTED_TABLE_SUFFIXES_BY_REGION,
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsChangeEvent,
    DtsEventProcessor,
    build_change_event,
    prepare_change_event_for_ingest,
)


JAVA_TRANSPORT_COMMAND_ENV = "TIT_DTS_JAVA_TRANSPORT_COMMAND"
DEFAULT_JAVA_TRANSPORT_COMMAND = (
    "java",
    "-cp",
    "/deployments/config:/deployments/dts-transport.jar:"
    "/deployments/dts-diagnose.jar",
    "com.aliyun.dts.subscribe.clients.TitDtsTransportBridge",
)
JAVA_TRANSPORT_START_TIMEOUT_SECONDS = 305.0
JAVA_TRANSPORT_CLOSE_TIMEOUT_SECONDS = 15.0
JAVA_TRANSPORT_PROTOCOL_VERSION = 2
JAVA_TRANSPORT_MAX_BATCH_MESSAGES = 2_048
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
    record: Mapping[str, Any] | None
    record_bytes: int
    topic: str
    partition: int
    offset: int
    source_timestamp: int | None = None
    lightweight: bool = False


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
        resume_source_timestamp: int | None,
        lightweight_prefilter_enabled: bool = False,
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
        if resume_source_timestamp is not None and (
            isinstance(resume_source_timestamp, bool)
            or not isinstance(resume_source_timestamp, int)
            or resume_source_timestamp < 0
        ):
            raise DtsJavaTransportError(
                "DTS_DATABASE_CHECKPOINT_TIMESTAMP_INVALID"
            )
        if (resume_offset is None) != (resume_source_timestamp is None):
            raise DtsJavaTransportError("DTS_DATABASE_CHECKPOINT_INCOMPLETE")
        if not isinstance(lightweight_prefilter_enabled, bool):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_PREFILTER_CONFIGURATION_INVALID"
            )
        if (
            resume_offset is None
            and settings.start_timestamp_seconds is None
        ):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_INITIAL_CHECKPOINT_REQUIRED"
            )
        if (
            isinstance(idle_timeout_ms, bool)
            or not isinstance(idle_timeout_ms, int)
            or idle_timeout_ms < 1
        ):
            raise DtsJavaTransportError("DTS_IDLE_TIMEOUT_INVALID")
        self.settings = settings
        self.processor = processor
        self.resume_offset = resume_offset
        self.resume_source_timestamp = resume_source_timestamp
        self.lightweight_prefilter_enabled = lightweight_prefilter_enabled
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
        self._expected_offset: int | None = resume_offset

    def startup_probe(
        self,
        *,
        phase_callback: Callable[[dict[str, bool | int | str]], None]
        | None = None,
    ) -> dict[str, int | str]:
        """Start the child and inspect its first official SDK record."""

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
                    "resume_source_timestamp": self.resume_source_timestamp,
                    "start_timestamp_seconds": (
                        self.settings.start_timestamp_seconds
                    ),
                    "idle_timeout_ms": self.idle_timeout_ms,
                    "protocol_version": JAVA_TRANSPORT_PROTOCOL_VERSION,
                    "supported_table_names": sorted(
                        f"{self.settings.source_region}_{suffix}"
                        for suffix in SUPPORTED_TABLE_SUFFIXES_BY_REGION[
                            self.settings.source_region
                        ]
                    ),
                    "source_field_whitelist": {
                        f"{self.settings.source_region}_{suffix}": sorted(
                            SOURCE_FIELD_WHITELIST[suffix]
                        )
                        for suffix in SUPPORTED_TABLE_SUFFIXES_BY_REGION[
                            self.settings.source_region
                        ]
                    },
                    "data_operations": sorted(DATA_OPERATIONS),
                    "control_operations": sorted(CONTROL_OPERATIONS),
                    "lightweight_prefilter_enabled": (
                        self.lightweight_prefilter_enabled
                    ),
                }
            )
            ready = self._receive(
                expected={"READY"},
                timeout_seconds=JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
            )
            first_record_offset = self._required_non_negative_int(
                ready,
                "first_record_offset",
            )
            first_record_source_timestamp = self._required_non_negative_int(
                ready,
                "first_record_source_timestamp",
            )
            resume_checkpoint_present = ready.get("resume_checkpoint_present")
            if (
                not isinstance(resume_checkpoint_present, bool)
                or resume_checkpoint_present != (self.resume_offset is not None)
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_RESUME_CHECKPOINT_MISMATCH"
                )
            if (
                ready.get("transport") != "official_dts_sdk"
                or ready.get("subscribe_mode") != "ASSIGN"
                or ready.get("protocol_version")
                != JAVA_TRANSPORT_PROTOCOL_VERSION
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_READY_IDENTITY_MISMATCH"
                )
            avro_writer_schema_fingerprint = ready.get(
                "avro_writer_schema_fingerprint_sha256"
            )
            if (
                not isinstance(avro_writer_schema_fingerprint, str)
                or avro_writer_schema_fingerprint
                != DTS_SDK_1_4_AVRO_WRITER_SCHEMA_SHA256
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_AVRO_SCHEMA_MISMATCH"
                )
            if self.resume_source_timestamp is not None:
                checkpoint_timestamp_seconds = self._required_non_negative_int(
                    ready,
                    "checkpoint_timestamp_seconds",
                )
                if (
                    checkpoint_timestamp_seconds
                    != self.resume_source_timestamp
                ):
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_RESUME_TIMESTAMP_MISMATCH"
                    )
            if (
                self.resume_offset is not None
                and first_record_offset > self.resume_offset
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_FIRST_RECORD_AHEAD_OF_DATABASE"
                )
            if self.resume_offset is None:
                self._expected_offset = first_record_offset
            self._started = True
            if phase_callback is not None:
                phase_callback(
                    {
                        "phase": "official_java_start",
                        "request_type": "START",
                        "status": "ok",
                        "transport": "official_dts_sdk",
                        "first_record_offset": first_record_offset,
                        "first_record_source_timestamp": (
                            first_record_source_timestamp
                        ),
                        "resume_checkpoint_present": (
                            resume_checkpoint_present
                        ),
                        "avro_writer_schema_compatible": True,
                    }
                )
            return {
                "status": "ok",
                "transport": "official_dts_sdk",
                "partition": self.settings.partition,
                "first_record_offset": first_record_offset,
                "first_record_source_timestamp": (
                    first_record_source_timestamp
                ),
                "avro_writer_schema_compatible": True,
            }
        except Exception:
            self.close(force=True)
            raise

    def run(self, *, max_messages: int, commit_offsets: bool) -> dict[str, int]:
        """Process one bounded batch and ACK only after its DB commit.

        Java streams individual EVENT frames to keep the line protocol
        bounded, then closes the batch with BATCH_COMPLETE.  The official SDK
        decodes Avro once; Java emits only the normalized structured record.
        Python validates it and protects every domestic identifier before SQL.
        The sink commits the ordered batch atomically; only then is one
        batched acknowledgement sent back to the official SDK bridge.
        """

        if not self._started or self._closed:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_NOT_READY"
            )
        if max_messages < 1 or max_messages > JAVA_TRANSPORT_MAX_BATCH_MESSAGES:
            raise ValueError(
                "max_messages must be between 1 and "
                f"{JAVA_TRANSPORT_MAX_BATCH_MESSAGES}"
            )
        if not commit_offsets:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_DURABLE_ACK_REQUIRED"
            )
        counters = {
            "requested_max_messages": max_messages,
            "seen": 0,
            "processed": 0,
            "ignored": 0,
            "duplicates": 0,
            "committed": 0,
            "transport_prefiltered": 0,
            "sdk_checkpoint_accepted": 0,
            "batch_bytes": 0,
            "transport_normalize_elapsed_ms": 0,
            "event_normalize_elapsed_ms": 0,
            "db_elapsed_ms": 0,
            "sdk_ack_elapsed_ms": 0,
            "batch_elapsed_ms": 0,
            "durable_next_offset": (
                0 if self._expected_offset is None else self._expected_offset
            ),
        }
        batch_started = self._monotonic()
        self._send({"type": "POLL", "max_messages": max_messages})
        poll_timeout = max(
            JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
            self.idle_timeout_ms / 1000 + 5.0,
        )
        events: list[_JavaEvent] = []
        changes = []
        acknowledgements: list[dict[str, int | str]] = []
        working_expected_offset = self._expected_offset
        event_normalize_elapsed_seconds = 0.0
        while True:
            message = self._receive(
                expected={"EVENT", "BATCH_COMPLETE"},
                timeout_seconds=poll_timeout,
            )
            if message["type"] == "BATCH_COMPLETE":
                child_seen = self._required_non_negative_int(message, "seen")
                if child_seen != len(events):
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_BATCH_COUNT_MISMATCH"
                    )
                child_batch_bytes = message.get("batch_bytes")
                if child_batch_bytes is not None and (
                    isinstance(child_batch_bytes, bool)
                    or not isinstance(child_batch_bytes, int)
                    or child_batch_bytes != counters["batch_bytes"]
                ):
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_BATCH_BYTES_MISMATCH"
                    )
                counters["transport_normalize_elapsed_ms"] = (
                    self._required_non_negative_int(
                        message,
                        "transport_normalize_elapsed_ms",
                    )
                )
                break

            event = self._parse_event(message)
            expected_offset = working_expected_offset
            if expected_offset is None:  # pragma: no cover - READY sets this
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
                )
            if event.offset > expected_offset:
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_EVENT_OFFSET_NOT_CONTIGUOUS"
                )
            replay = event.offset < expected_offset
            if event.lightweight:
                if event.source_timestamp is None:  # pragma: no cover - parser guard
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_EVENT_TIMESTAMP_INVALID"
                    )
                change_event = DtsChangeEvent(
                    source_region=self.settings.source_region,
                    topic=event.topic,
                    partition=event.partition,
                    offset=event.offset,
                    record_id=event.offset,
                    source_timestamp=event.source_timestamp,
                    source_txid="",
                    source_position=str(event.offset),
                    operation="NOOP",
                    database_name=None,
                    schema_name=None,
                    table_name=None,
                    before=None,
                    after=None,
                )
                counters["transport_prefiltered"] += 1
            else:
                if event.record is None:  # pragma: no cover - parser guard
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID"
                    )
                normalize_started = self._monotonic()
                change_event = build_change_event(
                    event.record,
                    source_region=self.settings.source_region,
                    topic=event.topic,
                    partition=event.partition,
                    offset=event.offset,
                )
                change_event = prepare_change_event_for_ingest(
                    change_event,
                    self.settings,
                )
                if change_event.source_timestamp != event.source_timestamp:
                    raise DtsJavaTransportError(
                        "DTS_OFFICIAL_JAVA_EVENT_TIMESTAMP_MISMATCH"
                    )
                event_normalize_elapsed_seconds += (
                    self._monotonic() - normalize_started
                )
            checkpoint_action = "REPLAY" if replay else "ADVANCE"
            next_offset = expected_offset if replay else event.offset + 1
            events.append(event)
            changes.append(change_event)
            counters["batch_bytes"] += event.record_bytes
            acknowledgements.append(
                {
                    "offset": event.offset,
                    "next_offset": next_offset,
                    "source_timestamp": change_event.source_timestamp,
                    "checkpoint_action": checkpoint_action,
                }
            )
            if not replay:
                working_expected_offset = next_offset

        counters["event_normalize_elapsed_ms"] = max(
            0,
            int(event_normalize_elapsed_seconds * 1000),
        )

        if not events:
            counters["batch_elapsed_ms"] = max(
                0,
                int((self._monotonic() - batch_started) * 1000),
            )
            return counters

        database_started = self._monotonic()
        # The production sink commits the entire ordered batch before this
        # call returns.  No Java checkpoint request is sent on any exception.
        results = tuple(self.processor.process_batch(tuple(changes)))
        database_finished = self._monotonic()
        if len(results) != len(events):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_BATCH_RESULT_COUNT_MISMATCH"
            )
        counters["db_elapsed_ms"] = max(
            0,
            int((database_finished - database_started) * 1000),
        )
        consume_batch_metrics = getattr(
            self.processor,
            "consume_batch_metrics",
            None,
        )
        if callable(consume_batch_metrics):
            batch_metrics = dict(consume_batch_metrics())
            if any(
                not isinstance(key, str)
                or not key.startswith("db_")
                or key in counters
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for key, value in batch_metrics.items()
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_BATCH_METRICS_INVALID"
                )
            counters.update(batch_metrics)
        for result, acknowledgement in zip(
            results,
            acknowledgements,
        ):
            replay = acknowledgement["checkpoint_action"] == "REPLAY"
            if replay and result.status != "DUPLICATE":
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_REPLAY_NOT_DURABLE"
                )
            counters["seen"] += 1
            if result.status == "PROCESSED":
                counters["processed"] += 1
            elif result.status == "DUPLICATE":
                counters["duplicates"] += 1
            else:
                counters["ignored"] += 1

        sdk_ack_started = self._monotonic()
        self._send(
            {
                "type": "DURABLE_ACK_BATCH",
                "acks": acknowledgements,
            }
        )
        accepted = self._receive(
            expected={"SDK_CHECKPOINTS_ACCEPTED"},
            timeout_seconds=JAVA_TRANSPORT_START_TIMEOUT_SECONDS,
        )
        replayed = sum(
            acknowledgement["checkpoint_action"] == "REPLAY"
            for acknowledgement in acknowledgements
        )
        advanced = len(acknowledgements) - replayed
        if (
            self._required_non_negative_int(accepted, "seen")
            != len(acknowledgements)
            or self._required_non_negative_int(accepted, "advanced")
            != advanced
            or self._required_non_negative_int(accepted, "replayed")
            != replayed
        ):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_CHECKPOINT_ACK_MISMATCH"
            )
        sdk_ack_finished = self._monotonic()
        counters["sdk_ack_elapsed_ms"] = max(
            0,
            int((sdk_ack_finished - sdk_ack_started) * 1000),
        )
        counters["sdk_checkpoint_accepted"] = advanced
        if working_expected_offset is None:  # pragma: no cover - READY guard
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
            )
        self._expected_offset = working_expected_offset
        counters["durable_next_offset"] = working_expected_offset
        counters["batch_elapsed_ms"] = max(
            0,
            int((sdk_ack_finished - batch_started) * 1000),
        )
        return counters

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
        lightweight = message.get("lightweight", False)
        if not isinstance(lightweight, bool):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_TRANSPORT_PROTOCOL_INVALID"
            )
        if lightweight:
            if (
                message.get("record") is not None
                or message.get("record_bytes") is not None
                or message.get("payload_base64") is not None
            ):
                raise DtsJavaTransportError(
                    "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID"
                )
            return _JavaEvent(
                record=None,
                record_bytes=0,
                topic=topic,
                partition=partition,
                offset=offset,
                source_timestamp=self._required_non_negative_int(
                    message,
                    "source_timestamp",
                ),
                lightweight=True,
            )
        if message.get("payload_base64") is not None:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID"
            )
        record = message.get("record")
        if not isinstance(record, Mapping):
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID"
            )
        record_bytes = self._required_non_negative_int(
            message,
            "record_bytes",
        )
        if record_bytes < 2:
            raise DtsJavaTransportError(
                "DTS_OFFICIAL_JAVA_EVENT_RECORD_INVALID"
            )
        return _JavaEvent(
            record=record,
            record_bytes=record_bytes,
            topic=topic,
            partition=partition,
            offset=offset,
            source_timestamp=self._required_non_negative_int(
                message,
                "source_timestamp",
            ),
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
    "JAVA_TRANSPORT_PROTOCOL_VERSION",
    "JAVA_TRANSPORT_MAX_BATCH_MESSAGES",
    "DtsJavaTransportError",
    "OfficialJavaDtsTransport",
    "java_child_environment",
    "java_transport_command",
]
