from __future__ import annotations

import errno
import hashlib
import hmac
import io
import json
import logging
import socket
from datetime import time
from threading import RLock
from types import SimpleNamespace

import pytest
from fastavro import schemaless_writer

from app.dts_source_consumer import (
    DOMESTIC_STUDENT_HMAC_DOMAIN,
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaShadowConsumer,
    DtsRecordError,
    InMemoryShadowSink,
    _KafkaConnectionTrace,
    _parsed_avro_schema,
    _run_kafka_startup_phase,
    assert_domestic_event_protected,
    build_change_event,
    decode_dts_avro,
    derive_penalty_flags,
    is_peak_lesson,
    project_appoint_candidate,
    probe_broker_tcp,
    protect_domestic_student_ids,
    reduce_latest_complaints,
    route_dirty_keys,
    safe_kafka_error_diagnostic,
    teacher_matches_region,
)


@pytest.fixture
def successful_broker_tcp_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dts_source_consumer as consumer_module

    monkeypatch.setattr(
        consumer_module,
        "probe_broker_tcp",
        lambda brokers: {"status": "ok", "broker_count": len(brokers)},
    )


@pytest.fixture
def bypass_kafka_protocol_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep offset-policy tests independent from protocol-stage fakes."""

    monkeypatch.setattr(
        DtsKafkaShadowConsumer,
        "_probe_kafka_protocol",
        lambda *_args, **_kwargs: None,
    )


def record(
    *,
    operation: str = "UPDATE",
    object_name: str | None = "tide_source_ovs.public.ovs_appoint",
    fields: list[str] | None = None,
    before: list[object] | None = None,
    after: list[object] | None = None,
) -> dict[str, object]:
    field_names = fields or []
    return {
        "id": 9001,
        "sourceTimestamp": 1786342560,
        "sourcePosition": "lsn:1",
        "safeSourcePosition": "lsn:1",
        "sourceTxid": "tx-1",
        "operation": operation,
        "objectName": object_name,
        "tags": {},
        "fields": [
            {"name": field_name, "dataTypeNumber": 0}
            for field_name in field_names
        ],
        "beforeImages": before,
        "afterImages": after,
    }


def event_from_record(raw: dict[str, object], *, offset: int = 10):
    return build_change_event(
        raw,
        source_region="ovs",
        topic="topic-v2",
        partition=0,
        offset=offset,
    )


def without_phase_elapsed(
    payloads: list[dict[str, bool | int | str]],
) -> list[dict[str, bool | int | str]]:
    return [
        {key: value for key, value in payload.items() if key != "elapsed_ms"}
        for payload in payloads
    ]


class _FakeKafkaFuture:
    def __init__(self, *, exception: BaseException | None = None) -> None:
        self.is_done = True
        self.exception = exception

    def failed(self) -> bool:
        return self.exception is not None


class _FakeKafkaConnection:
    def __init__(
        self,
        owner: "_FakeKafkaStartupConsumer",
        node_id: object,
    ) -> None:
        self.owner = owner
        self.node_id = node_id
        self.config = {"request_timeout_ms": 15_000}

    def close(self, *, error: Exception | None = None) -> None:
        client = self.owner._client
        trace = client.connection_trace
        if trace is not None:
            trace.record_failure(self.node_id, error)
            trace.record(self.node_id, "disconnected")
        client._failed_nodes.add(self.node_id)


class _FakeKafkaCluster:
    def __init__(self, owner: "_FakeKafkaStartupConsumer") -> None:
        self.owner = owner
        self.partitions_for_topic_calls: list[str] = []
        self.leader_for_partition_calls: list[object] = []
        self.broker_metadata_calls: list[object] = []

    def brokers(self) -> tuple[object, ...]:
        return (SimpleNamespace(nodeId=self.owner.advertised_node),)

    def partitions_for_topic(self, topic: str) -> set[int]:
        self.partitions_for_topic_calls.append(topic)
        if self.owner.fail_at == "partition_check":
            return set()
        return {0}

    def leader_for_partition(self, partition: object) -> object:
        self.leader_for_partition_calls.append(partition)
        return self.owner.advertised_node

    def broker_metadata(self, node_id: object) -> object | None:
        self.broker_metadata_calls.append(node_id)
        if node_id != self.owner.advertised_node:
            return None
        return SimpleNamespace(nodeId=node_id)


class _FakeKafkaClient:
    def __init__(self, owner: "_FakeKafkaStartupConsumer") -> None:
        self.owner = owner
        self.config = {"request_timeout_ms": 15_000}
        self._conns = {
            owner.bootstrap_node: _FakeKafkaConnection(
                owner,
                owner.bootstrap_node,
            ),
            owner.advertised_node: _FakeKafkaConnection(
                owner,
                owner.advertised_node,
            ),
            owner.coordinator_node: _FakeKafkaConnection(
                owner,
                owner.coordinator_node,
            ),
        }
        self.cluster = _FakeKafkaCluster(owner)
        self.set_topics_calls: list[list[str]] = []
        self.poll_calls: list[dict[str, object]] = []
        self.is_ready_calls: list[tuple[object, bool]] = []
        self.maybe_connect_calls: list[tuple[object, bool]] = []
        self.init_connect_calls: list[object] = []
        self.private_poll_calls: list[float] = []
        self.fire_completed_calls = 0
        self._ready_nodes: set[object] = set(owner.reused_nodes)
        self._failed_nodes: set[object] = set()
        self._connecting_node: object | None = None
        self._lock = RLock()
        self._closed = False
        self.connection_trace: object | None = None

    def least_loaded_node(self) -> object:
        return self.owner.bootstrap_node

    def set_topics(self, topics: list[str]) -> _FakeKafkaFuture:
        self.set_topics_calls.append(topics)
        if self.owner.fail_at == "topic_metadata":
            from kafka.errors import KafkaTimeoutError

            return _FakeKafkaFuture(
                exception=KafkaTimeoutError(self.owner.exception_secret)
            )
        return _FakeKafkaFuture()

    def is_ready(self, node_id: object, *, metadata_priority: bool) -> bool:
        self.is_ready_calls.append((node_id, metadata_priority))
        return node_id in self._ready_nodes

    def maybe_connect(self, node_id: object, *, wakeup: bool) -> bool:
        self.maybe_connect_calls.append((node_id, wakeup))
        self._connecting_node = node_id
        return True

    def _init_connect(self, node_id: object) -> bool:
        self.init_connect_calls.append(node_id)
        self._connecting_node = node_id
        return True

    def _poll(self, timeout_seconds: float) -> None:
        self.private_poll_calls.append(timeout_seconds)
        self.owner.timeout_events.append(self.config["request_timeout_ms"])
        node_id = self._connecting_node
        assert node_id is not None
        trace = self.connection_trace
        if trace is not None:
            if node_id == self.owner.advertised_node:
                trace.record(self.owner.bootstrap_node, "disconnected")
                trace.record_failure(self.owner.bootstrap_node, None)
            trace.record(node_id, "tcp_connecting")
            trace.record(node_id, "sasl_authenticating")
        expected_failure = {
            self.owner.bootstrap_node: "bootstrap_auth",
            self.owner.advertised_node: "advertised_broker_auth",
            self.owner.coordinator_node: "coordinator_auth",
        }[node_id]
        if self.owner.fail_at == expected_failure:
            from kafka.errors import KafkaTimeoutError

            raise KafkaTimeoutError(self.owner.exception_secret)
        if self.owner.fail_at == f"{expected_failure}_close_sasl":
            from kafka.errors import SaslAuthenticationFailedError

            error = SaslAuthenticationFailedError(
                self.owner.exception_secret
            )
            self._conns[node_id].close(error=error)
            return
        if trace is not None:
            trace.record(node_id, "connected")
        self._ready_nodes.add(node_id)

    def _fire_pending_completed_requests(self) -> list[object]:
        self.fire_completed_calls += 1
        return []

    def connection_failed(self, node_id: object) -> bool:
        return node_id in self._failed_nodes

    def _maybe_refresh_metadata(self) -> None:
        raise AssertionError(
            "authentication phases must not refresh Kafka metadata"
        )

    def poll(
        self,
        timeout_ms: int | None = None,
        future: object | None = None,
    ) -> None:
        assert future is not None, (
            "authentication stages must use connection-only private poll"
        )
        if timeout_ms is not None:
            self.owner.timeout_events.append(timeout_ms)
        self.poll_calls.append(
            {
                "timeout_ms": timeout_ms,
                "has_future": future is not None,
                "consumer_timeout_ms": self.owner.config[
                    "request_timeout_ms"
                ],
                "client_timeout_ms": self.config["request_timeout_ms"],
                "connection_timeouts_ms": tuple(
                    connection.config["request_timeout_ms"]
                    for connection in self._conns.values()
                ),
            }
        )


class _FakeKafkaCoordinator:
    def __init__(self, owner: "_FakeKafkaStartupConsumer") -> None:
        self.owner = owner
        self.ensure_ready_timeouts: list[int] = []

    def ensure_coordinator_ready(self, *, timeout_ms: int) -> bool:
        self.owner.timeout_events.append(timeout_ms)
        self.ensure_ready_timeouts.append(timeout_ms)
        if self.owner.fail_at == "group_coordinator":
            from kafka.errors import KafkaTimeoutError

            raise KafkaTimeoutError(self.owner.exception_secret)
        return True

    def coordinator(self) -> object:
        return self.owner.coordinator_node

    def ensure_active_group(self) -> None:
        raise AssertionError("startup probe must not issue JoinGroup")


class _FakeKafkaStartupConsumer:
    bootstrap_node = "private-bootstrap-node"
    advertised_node = "private-advertised-node"
    coordinator_node = "private-coordinator-node"
    exception_secret = "private-exception-endpoint-account-password"

    def __init__(
        self,
        *,
        fail_at: str | None = None,
        committed_offset: int | None = None,
        reused_nodes: set[object] | None = None,
    ) -> None:
        self.fail_at = fail_at
        self.committed_offset = committed_offset
        self.reused_nodes = set() if reused_nodes is None else reused_nodes
        self.timeout_events: list[int] = []
        self.config = {"request_timeout_ms": 15_000}
        self._client = _FakeKafkaClient(self)
        self._coordinator = _FakeKafkaCoordinator(self)
        self.committed_timeouts: list[int] = []
        self.offsets_for_times_timeouts: list[int] = []
        self.beginning_offsets_timeouts: list[int] = []
        self.end_offsets_timeouts: list[int] = []
        self.closed = False
        self.close_autocommit: bool | None = None

    def committed(self, _partition: object, *, timeout_ms: int) -> int | None:
        self.timeout_events.append(timeout_ms)
        self.committed_timeouts.append(timeout_ms)
        if self.fail_at == "offset_fetch":
            from kafka.errors import KafkaTimeoutError

            raise KafkaTimeoutError(self.exception_secret)
        return self.committed_offset

    def offsets_for_times(self, requested: dict[object, int]):
        self.timeout_events.append(self.config["request_timeout_ms"])
        self.offsets_for_times_timeouts.append(
            self.config["request_timeout_ms"]
        )
        return {
            partition: SimpleNamespace(offset=0)
            for partition in requested
        }

    def beginning_offsets(self, partitions: list[object]):
        self.timeout_events.append(self.config["request_timeout_ms"])
        self.beginning_offsets_timeouts.append(
            self.config["request_timeout_ms"]
        )
        return {partition: 0 for partition in partitions}

    def end_offsets(self, partitions: list[object]):
        self.timeout_events.append(self.config["request_timeout_ms"])
        self.end_offsets_timeouts.append(self.config["request_timeout_ms"])
        return {partition: 10 for partition in partitions}

    def subscribe(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("startup probe must not subscribe or JoinGroup")

    def assign(self, _partitions: list[object]) -> None:
        raise AssertionError("startup probe must not assign")

    def seek(self, _partition: object, _offset: int) -> None:
        raise AssertionError("startup probe must not seek")

    def poll(self, **_kwargs: object) -> object:
        raise AssertionError("startup probe must not call consumer.poll")

    def commit(self, **_kwargs: object) -> None:
        raise AssertionError("startup probe must not commit")

    def close(self, *, autocommit: bool, timeout_ms: int) -> None:
        self.close_autocommit = autocommit
        assert timeout_ms == 1_000
        self.closed = True

    def __iter__(self):
        raise AssertionError("startup probe must not consume")


def _install_fake_startup_consumer(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeKafkaStartupConsumer,
) -> None:
    def open_consumer(
        _self: object,
        *,
        connection_trace: object | None = None,
    ) -> _FakeKafkaStartupConsumer:
        fake._client.connection_trace = connection_trace
        return fake

    monkeypatch.setattr(
        DtsKafkaShadowConsumer,
        "_open_consumer",
        open_consumer,
    )


def _startup_probe_settings() -> DtsConsumerSettings:
    return DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("private-bootstrap.example:18003",),
        topic="private-topic-v2",
        group_id="private-provider-group-id",
        account="private-consumer-account",
        password="private-consumer-password",
        start_timestamp_seconds=1786550400,
    )


def test_settings_use_epoch_seconds_and_build_official_sasl_username() -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "ovs",
        "TIT_DTS_EXECUTION_REGION": "sg",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "topic-v2",
        "TIT_DTS_GROUP_ID": "opaque-provider-group-id-01",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-13T00:00:00+08:00",
    }

    settings = DtsConsumerSettings.from_env(values)

    assert settings.sasl_username == "consumer-opaque-provider-group-id-01"
    assert settings.start_timestamp_seconds == 1786550400
    summary = settings.safe_summary()
    assert summary["start_timestamp_seconds"] == 1786550400
    assert "start_timestamp_ms" not in summary
    assert "password" not in summary
    assert "account" not in summary

    values["TIT_DTS_PASSWORD"] = " secret-with-spaces "
    assert DtsConsumerSettings.from_env(values).password == " secret-with-spaces "

    values["TIT_DTS_START_AT"] = "2026-08-10 14:16:00"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_START_AT_REQUIRES_TIMEZONE",
    ):
        DtsConsumerSettings.from_env(values)


@pytest.mark.parametrize("group_id", ["tit-ovs-group", "tit-dom-group"])
def test_settings_reject_consumer_group_names_in_place_of_generated_ids(
    group_id: str,
) -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "ovs",
        "TIT_DTS_EXECUTION_REGION": "sg",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "topic-v2",
        "TIT_DTS_GROUP_ID": group_id,
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-13T00:00:00+08:00",
    }

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_GROUP_ID_PLACEHOLDER_FORBIDDEN$",
    ):
        DtsConsumerSettings.from_env(values)


def test_domestic_settings_require_china_execution_and_hmac_key() -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "dom",
        "TIT_DTS_EXECUTION_REGION": "cn",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "dom-topic-v2",
        "TIT_DTS_GROUP_ID": "dtsdom1234567890",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-12T16:30:00+08:00",
        "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD": "a" * 64,
    }

    settings = DtsConsumerSettings.from_env(values)

    assert settings.execution_region == "cn"
    assert settings.safe_summary()["student_subject_mode"] == "dom_hmac_v1"
    assert "domestic_student_hmac_key" not in repr(settings)
    fingerprint = settings.domestic_student_hmac_fingerprint()
    assert fingerprint is not None and len(fingerprint) == 64
    assert "a" * 64 not in fingerprint
    assert fingerprint == settings.domestic_student_hmac_fingerprint()
    settings.require_target_transport(
        host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        port=5432,
        expected_host="tide-system.rwlb.singapore.rds.aliyuncs.com",
        expected_port=5432,
    )
    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_DOM_CROSS_BORDER_TARGET_NOT_APPROVED$",
    ):
        settings.require_target_transport(
            host="other.internal",
            port=5432,
            expected_host="tide-system.rwlb.singapore.rds.aliyuncs.com",
            expected_port=5432,
        )

    for name, value, error in (
        (
            "TIT_DTS_EXECUTION_REGION",
            "sg",
            "TIT_DTS_EXECUTION_REGION_SOURCE_MISMATCH",
        ),
        (
            "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD",
            "not-a-64-character-lowercase-hex-secret",
            "TIT_DTS_DOM_STUDENT_HMAC_KEY_FORMAT_INVALID",
        ),
        (
            "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD",
            "非ASCII密钥",
            "TIT_DTS_DOM_STUDENT_HMAC_KEY_FORMAT_INVALID",
        ),
    ):
        invalid = {**values, name: value}
        with pytest.raises(DtsConfigurationError, match=f"^{error}$"):
            DtsConsumerSettings.from_env(invalid)

    missing_secret = dict(values)
    del missing_secret["TIT_DTS_DOM_STUDENT_HMAC_PASSWORD"]
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_DOM_STUDENT_HMAC_KEY_REQUIRED$",
    ):
        DtsConsumerSettings.from_env(missing_secret)


def test_domestic_settings_accept_matching_legacy_hmac_transition() -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "dom",
        "TIT_DTS_EXECUTION_REGION": "cn",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "dom-topic-v2",
        "TIT_DTS_GROUP_ID": "dtsdom1234567890",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-12T16:30:00+08:00",
        "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD": "a" * 64,
        "TIT_DTS_DOM_STUDENT_HMAC_KEY": "a" * 64,
    }

    settings = DtsConsumerSettings.from_env(values)

    assert settings.domestic_student_hmac_key == "a" * 64
    values["TIT_DTS_DOM_STUDENT_HMAC_KEY"] = "b" * 64
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_DOM_STUDENT_HMAC_SECRET_CONFLICT$",
    ) as conflict:
        DtsConsumerSettings.from_env(values)
    assert "a" * 64 not in str(conflict.value)
    assert "b" * 64 not in str(conflict.value)

    values["TIT_DTS_DOM_STUDENT_HMAC_KEY"] = "非ASCII旧密钥"
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_DOM_STUDENT_HMAC_KEY_FORMAT_INVALID$",
    ):
        DtsConsumerSettings.from_env(values)

    del values["TIT_DTS_DOM_STUDENT_HMAC_PASSWORD"]
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_DOM_STUDENT_HMAC_KEY_REQUIRED$",
    ):
        DtsConsumerSettings.from_env(values)


@pytest.mark.parametrize(
    ("secret_name", "error"),
    [
        (
            "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD",
            "TIT_DTS_DOM_STUDENT_HMAC_KEY_FORBIDDEN_FOR_OVS",
        ),
        (
            "TIT_DTS_DOM_STUDENT_HMAC_KEY",
            "TIT_DTS_DOM_STUDENT_HMAC_KEY_FORBIDDEN_FOR_OVS",
        ),
    ],
)
def test_overseas_settings_reject_domestic_hmac_secret(
    secret_name: str,
    error: str,
) -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "ovs",
        "TIT_DTS_EXECUTION_REGION": "sg",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "ovs-topic-v2",
        "TIT_DTS_GROUP_ID": "dtsovs1234567890",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-10T14:16:00+08:00",
    }
    assert DtsConsumerSettings.from_env(values).domestic_student_hmac_fingerprint() is None
    values[secret_name] = "a" * 64
    with pytest.raises(
        DtsConfigurationError,
        match=f"^{error}$",
    ):
        DtsConsumerSettings.from_env(values)


def test_overseas_settings_reject_both_domestic_hmac_names() -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "ovs",
        "TIT_DTS_EXECUTION_REGION": "sg",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "ovs-topic-v2",
        "TIT_DTS_GROUP_ID": "dtsovs1234567890",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-10T14:16:00+08:00",
        "TIT_DTS_DOM_STUDENT_HMAC_PASSWORD": "a" * 64,
        "TIT_DTS_DOM_STUDENT_HMAC_KEY": "a" * 64,
    }

    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_DOM_STUDENT_HMAC_KEY_FORBIDDEN_FOR_OVS$",
    ):
        DtsConsumerSettings.from_env(values)

    values["TIT_DTS_DOM_STUDENT_HMAC_PASSWORD"] = ""
    values["TIT_DTS_DOM_STUDENT_HMAC_KEY"] = ""
    assert (
        DtsConsumerSettings.from_env(values).domestic_student_hmac_key
        is None
    )


def test_domestic_student_ids_are_hmac_protected_before_routing() -> None:
    raw_student_id = "dom-student-987654"
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_appoint",
            fields=["id", "t_id", "s_id", "status", "use_point"],
            after=["course-1", "teacher-1", raw_student_id, "end", "buy"],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=10,
    )
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786523400,
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    protected = protect_domestic_student_ids(event, settings)

    assert protected.after is not None
    assert "s_id" not in protected.after
    token = protected.after["student_token"]
    assert isinstance(token, str) and token.startswith("dom:v1:")
    assert len(token) == len("dom:v1:") + 64
    assert token == "dom:v1:" + hmac.new(
        bytes.fromhex("a" * 64),
        DOMESTIC_STUDENT_HMAC_DOMAIN + raw_student_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert raw_student_id not in repr(protected)
    dirty = route_dirty_keys(protected)
    assert dirty.teacher_student_pairs == {("teacher-1", token)}
    candidate = project_appoint_candidate(protected)
    assert candidate is not None
    assert candidate.target_values["学员id"] == token


def test_domestic_student_protection_rejects_conflicting_aliases() -> None:
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_complaint",
            fields=["id", "stu_id", "user_id", "appoint_id"],
            after=["record-1", "student-1", "student-2", "course-1"],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=11,
    )
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    with pytest.raises(
        DtsRecordError,
        match="^DTS_DOM_STUDENT_ID_ALIASES_CONFLICT$",
    ):
        protect_domestic_student_ids(event, settings)


def test_domestic_source_cannot_supply_its_own_student_token() -> None:
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_appoint",
            fields=["id", "t_id", "student_token"],
            after=["course-1", "teacher-1", "dom:v1:" + "b" * 64],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=12,
    )
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    with pytest.raises(
        DtsRecordError,
        match="^DTS_DOM_SOURCE_STUDENT_TOKEN_FORBIDDEN$",
    ):
        protect_domestic_student_ids(event, settings)


def test_domestic_student_protection_recurses_into_json_info() -> None:
    raw_student_id = "student-in-json"
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_qa_ac_classroom_record",
            fields=["id", "info"],
            after=[
                "record-1",
                json.dumps(
                    {
                        "cpu": [
                            {
                                "appoint_id": "course-1",
                                "student_id": raw_student_id,
                            }
                        ]
                    }
                ),
            ],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=12,
    )
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    protected = protect_domestic_student_ids(event, settings)

    assert protected.after is not None
    protected_info = json.loads(str(protected.after["info"]))
    protected_row = protected_info["cpu"][0]
    assert "student_id" not in protected_row
    assert protected_row["student_token"].startswith("dom:v1:")
    assert raw_student_id not in str(protected.after)
    assert_domestic_event_protected(protected)


def test_domestic_student_protection_rejects_invalid_json_info() -> None:
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_qa_ac_classroom_record",
            fields=["id", "info"],
            after=["record-1", '{"student_id":123'],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=13,
    )
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    with pytest.raises(DtsRecordError, match="^DTS_DOM_INFO_INVALID_JSON$"):
        protect_domestic_student_ids(event, settings)


def test_domestic_free_text_reasons_are_reduced_before_cross_border_write() -> None:
    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )
    event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_teacher_absent_reason",
            fields=["id", "appoint_id", "t_id", "reason_desc"],
            after=[
                "reason-1",
                "course-1",
                "teacher-1",
                "student 987654, Unfilled Lesson Memo, private note",
            ],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=14,
    )

    protected = protect_domestic_student_ids(event, settings)

    assert protected.after is not None
    assert protected.after["reason_desc"] == "Unfilled Lesson Memo"
    assert "987654" not in repr(protected)

    appoint_event = build_change_event(
        record(
            object_name="tide_source_dom.public.dom_appoint",
            fields=["id", "t_id", "s_id", "cancel_reason"],
            after=["course-1", "teacher-1", "student-1", "call 123456"],
        ),
        source_region="dom",
        topic="dom-topic-v2",
        partition=0,
        offset=15,
    )
    protected_appoint = protect_domestic_student_ids(appoint_event, settings)
    assert protected_appoint.after is not None
    assert protected_appoint.after["cancel_reason"] == (
        "Domestic reason redacted"
    )
    assert "123456" not in repr(protected_appoint)


def test_broker_tcp_probe_connects_without_sending_application_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dts_source_consumer as consumer_module

    class Connection:
        closed = False
        timeout = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def connect(self, address: tuple[str, int]) -> None:
            calls.append((address, self.timeout))

        def close(self) -> None:
            self.closed = True

        def send(self, _payload: bytes) -> None:
            raise AssertionError("TCP probe must not send bytes")

        def recv(self, _size: int) -> bytes:
            raise AssertionError("TCP probe must not receive bytes")

    connection = Connection()
    calls: list[tuple[tuple[str, int], float | None]] = []
    monkeypatch.setattr(
        consumer_module.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (host, port))
        ],
    )
    monkeypatch.setattr(
        consumer_module.socket,
        "socket",
        lambda *_args: connection,
    )

    result = probe_broker_tcp(("100.103.7.163:18003",))

    assert result == {"status": "ok", "broker_count": 1}
    assert calls[0][0] == ("100.103.7.163", 18003)
    assert calls[0][1] is not None and 0 < calls[0][1] <= 5.0
    assert connection.closed is True


def test_broker_tcp_probe_supports_ipv6_and_falls_back_to_second_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dts_source_consumer as consumer_module

    class Connection:
        def __init__(self) -> None:
            self.closed = False
            self.timeout = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def connect(self, address: tuple[str, int]) -> None:
            assert self.timeout is not None and self.timeout > 0
            calls.append(address)
            if len(calls) == 1:
                raise TimeoutError("private endpoint omitted")

        def close(self) -> None:
            self.closed = True

    calls: list[tuple[str, int]] = []
    connections: list[Connection] = []
    monkeypatch.setattr(
        consumer_module.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (
                socket.AF_INET6 if ":" in host else socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                (host, port),
            )
        ],
    )

    def socket_factory(*_args: object) -> Connection:
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(consumer_module.socket, "socket", socket_factory)

    result = probe_broker_tcp(
        ("broker.internal:18003", "[2001:db8::1]:18004")
    )

    assert result == {"status": "ok", "broker_count": 2}
    assert calls == [
        ("broker.internal", 18003),
        ("2001:db8::1", 18004),
    ]
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (socket.gaierror("private details"), "DTS_BROKER_TCP_DNS_FAILED"),
        (TimeoutError("private details"), "DTS_BROKER_TCP_CONNECTION_TIMEOUT"),
        (
            ConnectionRefusedError("private details"),
            "DTS_BROKER_TCP_CONNECTION_REFUSED",
        ),
        (
            OSError(errno.EHOSTUNREACH, "private details"),
            "DTS_BROKER_TCP_UNREACHABLE",
        ),
        (OSError(errno.EIO, "private details"), "DTS_BROKER_TCP_CONNECTION_FAILED"),
    ],
)
def test_broker_tcp_probe_emits_stable_errors_without_endpoint_details(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    expected_code: str,
) -> None:
    from app import dts_source_consumer as consumer_module

    if isinstance(error, socket.gaierror):
        monkeypatch.setattr(
            consumer_module.socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        )
    else:
        monkeypatch.setattr(
            consumer_module.socket,
            "getaddrinfo",
            lambda host, port, **_kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", (host, port))
            ],
        )

        class FailingSocket:
            def settimeout(self, timeout: float) -> None:
                assert timeout > 0

            def connect(self, _address: tuple[str, int]) -> None:
                raise error

            def close(self) -> None:
                pass

        monkeypatch.setattr(
            consumer_module.socket,
            "socket",
            lambda *_args: FailingSocket(),
        )

    with pytest.raises(DtsConfigurationError) as raised:
        probe_broker_tcp(("broker.internal:18003",))

    assert str(raised.value) == expected_code
    assert "broker.internal" not in str(raised.value)
    assert "private details" not in str(raised.value)


@pytest.mark.parametrize(
    "broker",
    (
        "broker.internal",
        "https://broker.internal:18003",
        "user@broker.internal:18003",
        "broker.internal/path:18003",
        "broker.internal?query:18003",
        "2001:db8::1:18003",
        "[2001:db8::1]18003",
        "broker.internal:0",
        "broker.internal:65536",
    ),
)
def test_broker_tcp_probe_rejects_invalid_endpoint_syntax(broker: str) -> None:
    with pytest.raises(
        DtsConfigurationError,
        match="^TIT_DTS_BROKER_URL_INVALID$",
    ):
        probe_broker_tcp((broker,))


def test_broker_tcp_probe_reports_mixed_endpoint_failures_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dts_source_consumer as consumer_module

    def resolve(host: str, port: int, **_kwargs: object) -> object:
        if host == "first.internal":
            raise socket.gaierror("first endpoint")
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (host, port))]

    class TimedOutSocket:
        def settimeout(self, timeout: float) -> None:
            assert timeout > 0

        def connect(self, _address: tuple[str, int]) -> None:
            raise TimeoutError("second endpoint")

        def close(self) -> None:
            pass

    monkeypatch.setattr(consumer_module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        consumer_module.socket,
        "socket",
        lambda *_args: TimedOutSocket(),
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_BROKER_TCP_ALL_ENDPOINTS_FAILED$",
    ):
        probe_broker_tcp(("first.internal:18003", "second.internal:18003"))


@pytest.mark.parametrize("timeout", (0, -1, float("inf"), float("nan")))
def test_broker_tcp_probe_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_BROKER_TCP_TIMEOUT_INVALID$",
    ):
        probe_broker_tcp(("broker.internal:18003",), timeout_seconds=timeout)


def test_official_avro_schema_round_trips_heartbeat() -> None:
    raw = {
        "version": 1,
        "id": 8200,
        "sourceTimestamp": 1786342560,
        "sourcePosition": "lsn:1",
        "safeSourcePosition": "lsn:1",
        "sourceTxid": "",
        "source": {"sourceType": "PostgreSQL", "version": "14"},
        "operation": "HEARTBEAT",
        "objectName": None,
        "processTimestamps": None,
        "tags": {},
        "fields": None,
        "beforeImages": None,
        "afterImages": None,
        "bornTimestamp": 1786342560000,
    }
    buffer = io.BytesIO()
    schemaless_writer(buffer, _parsed_avro_schema(), raw)

    assert decode_dts_avro(buffer.getvalue()) == raw


def test_official_avro_schema_round_trips_postgresql_dml_image() -> None:
    raw = {
        "version": 1,
        "id": 8201,
        "sourceTimestamp": 1786342560,
        "sourcePosition": "lsn:2",
        "safeSourcePosition": "lsn:2",
        "sourceTxid": "tx-2",
        "source": {"sourceType": "PostgreSQL", "version": "14"},
        "operation": "INSERT",
        "objectName": "public.ovs_appoint",
        "processTimestamps": None,
        "tags": {},
        "fields": [{"name": "id", "dataTypeNumber": 20}],
        "beforeImages": None,
        "afterImages": [
            (
                "com.alibaba.dts.formats.avro.Integer",
                {"precision": 20, "value": "7"},
            )
        ],
        "bornTimestamp": 1786342560000,
    }
    buffer = io.BytesIO()
    schemaless_writer(buffer, _parsed_avro_schema(), raw)

    decoded = decode_dts_avro(buffer.getvalue())
    event = event_from_record(decoded)

    assert event.table_name == "ovs_appoint"
    assert event.after == {"id": "7"}


def test_build_change_event_maps_full_images_and_identity() -> None:
    raw = record(
        fields=["id", "status", "name"],
        before=[{"precision": 20, "value": "7"}, None, {"charset": "utf8", "value": b"old"}],
        after=[{"precision": 20, "value": "7"}, {"charset": "utf8", "value": b"end"}, {"charset": "utf8", "value": b"new"}],
    )

    event = event_from_record(raw)

    assert event.database_name == "tide_source_ovs"
    assert event.schema_name == "public"
    assert event.table_name == "ovs_appoint"
    assert event.before == {"id": "7", "status": None, "name": "old"}
    assert event.after == {"id": "7", "status": "end", "name": "new"}
    assert event.idempotency_key == ("ovs", "topic-v2", 0, 10)


def test_build_change_event_rejects_partial_dml_image() -> None:
    raw = record(fields=["id", "status"], after=[{"precision": 20, "value": "7"}])

    with pytest.raises(DtsRecordError, match="DTS_IMAGE_FIELD_COUNT_MISMATCH"):
        event_from_record(raw)


@pytest.mark.parametrize(
    ("region", "week", "lesson_time", "expected"),
    [
        ("dom", 1, "21:30", True),
        ("dom", 1, "21:30:01", False),
        ("dom", 1, "23:00", False),
        ("dom", 6, "10:00", True),
        ("ovs", 1, "00:00", True),
        ("ovs", 1, "05:30", True),
        ("ovs", 1, "05:30:01", False),
        ("ovs", 1, "17:59:59", False),
        ("ovs", 1, "18:00", True),
        ("ovs", 1, "23:00", True),
        ("ovs", 1, "23:30", True),
        ("ovs", 1, "23:30:01", False),
        ("ovs", 1, "10:00", False),
        ("ovs", 7, "09:00", True),
        ("ovs", 7, "10:00", True),
        ("ovs", 7, "11:30", True),
        ("ovs", 7, "11:30:01", False),
        ("ovs", 8, "23:00", False),
    ],
)
def test_peak_policy_matches_two_obs_scripts(
    region: str,
    week: int,
    lesson_time: str,
    expected: bool,
) -> None:
    assert is_peak_lesson(region, week, lesson_time) is expected


def test_teacher_scope_matches_two_obs_scripts() -> None:
    assert teacher_matches_region("ovs", "kids,global_cn") is True
    assert teacher_matches_region("ovs", "kids") is False
    assert teacher_matches_region("dom", "kids,global_pool") is False
    assert teacher_matches_region("dom", "kids,phonics") is True
    assert teacher_matches_region("ovs", None) is None


def test_appoint_routes_old_and_new_keys_and_builds_shadow_candidate() -> None:
    fields = [
        "id",
        "t_id",
        "s_id",
        "date",
        "time",
        "start_time",
        "week",
        "status",
        "use_point",
    ]
    before = ["99", "10", "20", "2026-08-11", "17:00", "2026-08-11 17:00:00", "1", "end", "buy"]
    after = ["99", "11", "21", "2026-08-11", "23:00", "2026-08-11 23:00:00", "1", "end", "buy"]
    event = event_from_record(record(fields=fields, before=before, after=after))

    dirty = route_dirty_keys(event)
    candidate = project_appoint_candidate(event)

    assert dirty.course_ids == {"99"}
    assert dirty.teacher_ids == {"10", "11"}
    assert dirty.teacher_student_pairs == {("10", "20"), ("11", "21")}
    assert candidate is not None
    assert candidate.action == "UPSERT_CANDIDATE"
    assert candidate.target_values["是否高峰"] is True
    assert candidate.target_values["上课时间"] == time(23, 0)
    assert candidate.required_sources == ("dom_teacher",)


def test_appoint_outside_script_scope_becomes_delete_candidate() -> None:
    fields = ["id", "t_id", "s_id", "status", "use_point"]
    event = event_from_record(
        record(
            operation="UPDATE",
            fields=fields,
            before=["99", "10", "20", "end", "buy"],
            after=["99", "10", "20", "cancel", "buy"],
        )
    )

    candidate = project_appoint_candidate(event)

    assert candidate is not None
    assert candidate.action == "DELETE"
    assert candidate.reason == "APPOINT_OUTSIDE_SCRIPT_SCOPE"


def test_qa_json_routes_every_embedded_appoint_id_without_rethresholding() -> None:
    raw = record(
        object_name="public.ovs_qa_ac_classroom_record",
        fields=["id", "type", "info"],
        after=[
            "1",
            "CPU",
            '{"cpu":[{"appoint_id":536848637,"proportion":0.82,"cpu":80}],'
            '"network_delay":[{"appoint_id":"538267933","proportion":0.67}]}',
        ],
    )

    dirty = route_dirty_keys(event_from_record(raw))

    assert dirty.course_ids == {"536848637", "538267933"}
    assert dirty.issues == ()


def test_qa_invalid_json_is_an_issue_not_a_false_value() -> None:
    raw = record(
        object_name="public.ovs_qa_ac_classroom_record",
        fields=["id", "type", "info"],
        after=["1", "CPU", "not-json"],
    )

    dirty = route_dirty_keys(event_from_record(raw))

    assert dirty.issues == ("QA_INFO_INVALID_JSON",)
    assert dirty.course_ids == frozenset()


def test_complaint_uses_latest_row_from_each_table_and_script_filters() -> None:
    user_rows = [
        {
            "id": 1,
            "user_id": 7,
            "appoint_id": 99,
            "teacher_id": 10,
            "complaint_type": 12,
            "complaint_type_child": 100,
        },
        {
            "id": 2,
            "user_id": 7,
            "appoint_id": 99,
            "teacher_id": 10,
            "complaint_type": 13,
            "complaint_type_child": 101,
            "complaint_type_grandson": 81,
        },
    ]
    complaint_rows = [
        {
            "id": 10,
            "stu_id": 7,
            "appoint_id": 99,
            "tea_id": 10,
            "complaint_type": 13,
            "complaint_type_child": 999,
            "approve": "n",
            "validity": 0,
        },
        {
            "id": 11,
            "stu_id": 7,
            "appoint_id": 99,
            "tea_id": 10,
            "complaint_type": 13,
            "complaint_type_child": 998,
            "approve": "y",
            "validity": 1,
            "course_date": "2026-08-11",
        },
    ]

    result = reduce_latest_complaints(user_rows, complaint_rows)

    assert len(result) == 1
    assert result[0]["complaint_type_child"] == 101
    assert result[0]["approve"] == "y"
    assert result[0]["validity"] == 1

    user_rows[-1]["complaint_type_grandson"] = 82
    assert reduce_latest_complaints(user_rows, complaint_rows) == []


def test_penalty_flags_use_max_valid_times_and_strictly_more_than_30_seconds() -> None:
    rows = [
        {"in_time": "2026-08-11 18:00:30", "out_time": "2026-08-11 18:25:00", "appeal_status": 0},
        {"in_time": "2026-08-11 18:00:31", "out_time": "2026-08-11 18:29:29", "appeal_status": 0},
        {"in_time": "2026-08-11 18:10:00", "out_time": "2026-08-11 18:00:00", "appeal_status": 2},
        {"in_time": "2026-08-11 19:00:00", "out_time": "2026-08-11 17:00:00", "appeal_status": None},
    ]

    assert derive_penalty_flags(
        rows,
        lesson_start="2026-08-11 18:00:00",
        lesson_end="2026-08-11 18:30:00",
    ) == (True, True)

    rows[1]["in_time"] = "2026-08-11 18:00:30"
    rows[1]["out_time"] = "2026-08-11 18:29:30"
    assert derive_penalty_flags(
        rows,
        lesson_start="2026-08-11 18:00:00",
        lesson_end="2026-08-11 18:30:00",
    ) == (False, False)


def test_processor_is_idempotent_within_shadow_run() -> None:
    event = event_from_record(
        record(
            operation="HEARTBEAT",
            object_name=None,
        )
    )
    sink = InMemoryShadowSink()
    processor = DtsEventProcessor(sink)

    assert processor.process(event).status == "IGNORED"
    assert processor.process(event).status == "DUPLICATE"
    assert len(sink.processed) == 1


def test_kafka_shadow_consumer_seeks_new_group_and_commits_exact_next_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kafka

    heartbeat = record(operation="HEARTBEAT", object_name=None)
    message = SimpleNamespace(
        topic="topic-v2",
        partition=0,
        offset=41,
        value=b"encoded",
    )

    class FakeConsumer:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.assigned = []
            self.seek_calls = []
            self.offsets_for_times_calls = []
            self.commit_calls = []
            self.commit_timeout_ms = None
            self.closed = False

        def assign(self, partitions: list[object]) -> None:
            self.assigned = partitions

        def committed(self, _partition: object, *, timeout_ms: int) -> None:
            assert timeout_ms == 15_000
            return None

        def offsets_for_times(self, requested: dict[object, int]):
            self.offsets_for_times_calls.append(requested)
            return {partition: SimpleNamespace(offset=40) for partition in requested}

        def seek(self, partition: object, offset: int) -> None:
            self.seek_calls.append((partition, offset))

        def commit(
            self,
            *,
            offsets: dict[object, object],
            timeout_ms: int,
        ) -> None:
            self.commit_calls.append(offsets)
            self.commit_timeout_ms = timeout_ms

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000
            self.closed = True

        def __iter__(self):
            return iter([message])

    holder: dict[str, FakeConsumer] = {}

    def factory(**kwargs: object) -> FakeConsumer:
        holder["consumer"] = FakeConsumer(**kwargs)
        return holder["consumer"]

    monkeypatch.setattr(kafka, "KafkaConsumer", factory)
    monkeypatch.setattr(
        "app.dts_source_consumer.decode_dts_avro",
        lambda _payload: heartbeat,
    )
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )
    consumer = DtsKafkaShadowConsumer(
        settings,
        DtsEventProcessor(InMemoryShadowSink()),
    )

    result = consumer.run(max_messages=1, commit_offsets=True)

    fake = holder["consumer"]
    assert result == {
        "seen": 1,
        "processed": 0,
        "ignored": 1,
        "duplicates": 0,
        "committed": 1,
    }
    assert fake.seek_calls[0][1] == 40
    assert next(iter(fake.offsets_for_times_calls[0].values())) == 1786550400
    committed = next(iter(fake.commit_calls[0].values()))
    assert committed.offset == 42
    assert committed.metadata == str(heartbeat["sourceTimestamp"])
    assert committed.leader_epoch == -1
    assert fake.kwargs["enable_auto_commit"] is False
    assert fake.kwargs["api_version"] == (2, 7)
    assert fake.kwargs["request_timeout_ms"] == 15_000
    assert fake.kwargs["sasl_plain_username"] == "consumer-dtsovs1234567890"
    assert fake.commit_timeout_ms == 15_000
    assert fake.closed is True


def test_kafka_commit_timeout_fails_after_sink_and_restart_uses_database_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kafka
    from kafka.errors import KafkaTimeoutError

    heartbeat = record(operation="HEARTBEAT", object_name=None)
    message = SimpleNamespace(
        topic="topic-v2",
        partition=0,
        offset=41,
        value=b"encoded",
    )

    class DurableProcessor:
        authoritative_checkpoint = True

        def __init__(self) -> None:
            self.checkpoint = 41
            self.processed_offsets: list[int] = []

        def resume_offset(self, **_kwargs: object) -> int:
            return self.checkpoint

        def process(self, event: object) -> object:
            offset = int(getattr(event, "offset"))
            self.processed_offsets.append(offset)
            self.checkpoint = offset + 1
            return SimpleNamespace(status="PROCESSED")

    class FakeConsumer:
        def __init__(self, *, fail_commit: bool, has_message: bool) -> None:
            self.config = {"request_timeout_ms": 15_000}
            self.fail_commit = fail_commit
            self.messages = [message] if has_message else []
            self.seek_calls: list[tuple[object, int]] = []
            self.commit_timeout_ms: int | None = None
            self.closed = False

        def committed(self, _partition: object, *, timeout_ms: int) -> int:
            assert timeout_ms == 15_000
            return 41

        def beginning_offsets(self, partitions: list[object]):
            return {partition: 0 for partition in partitions}

        def end_offsets(self, partitions: list[object]):
            return {partition: 100 for partition in partitions}

        def assign(self, _partitions: list[object]) -> None:
            pass

        def seek(self, partition: object, offset: int) -> None:
            self.seek_calls.append((partition, offset))

        def commit(
            self,
            *,
            offsets: dict[object, object],
            timeout_ms: int,
        ) -> None:
            assert offsets
            self.commit_timeout_ms = timeout_ms
            if self.fail_commit:
                raise KafkaTimeoutError("commit deadline exceeded")

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000
            self.closed = True

        def __iter__(self):
            return iter(self.messages)

    fake_consumers: list[FakeConsumer] = []

    def factory(**_kwargs: object) -> FakeConsumer:
        first_attempt = not fake_consumers
        consumer = FakeConsumer(
            fail_commit=first_attempt,
            has_message=first_attempt,
        )
        fake_consumers.append(consumer)
        return consumer

    monkeypatch.setattr(kafka, "KafkaConsumer", factory)
    monkeypatch.setattr(
        "app.dts_source_consumer.decode_dts_avro",
        lambda _payload: heartbeat,
    )
    processor = DurableProcessor()
    consumer = DtsKafkaShadowConsumer(
        DtsConsumerSettings(
            source_region="ovs",
            broker_urls=("broker.internal:18003",),
            topic="topic-v2",
            group_id="dtsovs1234567890",
            account="consumer",
            password="runtime-only",
            start_timestamp_seconds=1786550400,
        ),
        processor,
    )

    with pytest.raises(KafkaTimeoutError, match="commit deadline exceeded"):
        consumer.run(max_messages=1, commit_offsets=True)

    assert processor.processed_offsets == [41]
    assert processor.checkpoint == 42
    assert fake_consumers[0].commit_timeout_ms == 15_000
    assert fake_consumers[0].closed is True

    assert consumer.run(max_messages=1, commit_offsets=True) == {
        "seen": 0,
        "processed": 0,
        "ignored": 0,
        "duplicates": 0,
        "committed": 0,
    }
    assert processor.processed_offsets == [41]
    assert fake_consumers[1].seek_calls[0][1] == 42
    assert fake_consumers[1].closed is True


def test_domestic_kafka_run_protects_student_id_before_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kafka

    raw_student_id = "student-must-not-cross-border"
    decoded = record(
        operation="INSERT",
        object_name="tide_source_dom.public.dom_appoint",
        fields=["id", "t_id", "s_id", "status", "use_point"],
        after=["course-1", "teacher-1", raw_student_id, "end", "buy"],
    )
    message = SimpleNamespace(
        value=b"redacted",
        topic="dom-topic-v2",
        partition=0,
        offset=42,
    )

    class FakeConsumer:
        config = {"request_timeout_ms": 15_000}

        def committed(self, _partition: object, *, timeout_ms: int) -> None:
            assert timeout_ms == 15_000
            return None

        def offsets_for_times(self, requested: dict[object, int]):
            return {
                partition: SimpleNamespace(offset=42)
                for partition in requested
            }

        def assign(self, _partitions: list[object]) -> None:
            pass

        def seek(self, _partition: object, _offset: int) -> None:
            pass

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000

        def __iter__(self):
            return iter([message])

    monkeypatch.setattr(kafka, "KafkaConsumer", lambda **_kwargs: FakeConsumer())
    monkeypatch.setattr(
        "app.dts_source_consumer.decode_dts_avro",
        lambda _payload: decoded,
    )
    captured: list[object] = []

    class Processor:
        authoritative_checkpoint = False

        def resume_offset(self, **_kwargs: object) -> None:
            return None

        def process(self, event: object) -> object:
            captured.append(event)
            return SimpleNamespace(status="PROCESSED")

    settings = DtsConsumerSettings(
        source_region="dom",
        broker_urls=("broker.internal:18003",),
        topic="dom-topic-v2",
        group_id="dtsdom1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786523400,
        execution_region="cn",
        domestic_student_hmac_key="a" * 64,
    )

    result = DtsKafkaShadowConsumer(settings, Processor()).run(
        max_messages=1,
        commit_offsets=False,
    )

    assert result["processed"] == 1
    protected = captured[0]
    assert getattr(protected, "after")["student_token"].startswith("dom:v1:")
    assert raw_student_id not in repr(protected)


def test_kafka_startup_probe_resolves_offset_zero_without_consumer_state_change(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    fake = _FakeKafkaStartupConsumer()
    _install_fake_startup_consumer(monkeypatch, fake)
    settings = _startup_probe_settings()
    consumer = DtsKafkaShadowConsumer(
        settings,
        DtsEventProcessor(InMemoryShadowSink()),
    )

    emitted: list[dict[str, bool | int | str]] = []
    result = consumer.startup_probe(phase_callback=emitted.append)

    assert result == {
        "status": "ok",
        "tcp": "ok",
        "partition": 0,
        "initial_offset": 0,
    }
    assert fake.close_autocommit is False
    assert fake.closed is True
    client_probe = next(
        payload
        for payload in emitted
        if payload.get("probe") == "kafka_client_config"
    )
    assert client_probe["protocol_version_mode"] == "pinned"
    assert client_probe[
        "group_membership_mode"
    ] == "manual_partition_assignment"
    assert client_probe["join_group_enabled"] is False
    assert [
        payload["phase"]
        for payload in emitted
        if payload.get("status") == "begin"
    ] == [
        "consumer_open",
        "bootstrap_auth",
        "topic_metadata",
        "partition_check",
        "advertised_broker_auth",
        "group_coordinator",
        "coordinator_auth",
        "offset_fetch",
        "offsets_for_times",
    ]
    completed = {
        payload["phase"]: payload
        for payload in emitted
        if payload.get("status") == "ok" and "phase" in payload
    }
    for phase in (
        "bootstrap_auth",
        "advertised_broker_auth",
        "coordinator_auth",
    ):
        assert completed[phase][
            "request_type"
        ] == "AuthenticatedConnectionReady"
        assert completed[phase][
            "authenticated_connection_ready"
        ] is True
        assert completed[phase]["connection_reused"] is False
        assert completed[phase]["sasl_authenticated_observed"] is True
    assert completed["topic_metadata"] == {
        "phase": "topic_metadata",
        "request_type": "Metadata",
        "status": "ok",
        "elapsed_ms": completed["topic_metadata"]["elapsed_ms"],
        "metadata_request_completed": True,
        "metadata_scope": "configured_topic",
        "kcat_list_topic_semantics": True,
        "advertised_broker_count": 1,
    }
    assert completed["partition_check"][
        "configured_partition_present"
    ] is True
    assert completed["partition_check"]["metadata_partition_count"] == 1
    assert completed["advertised_broker_auth"][
        "broker_role"
    ] == "topic_partition_leader"
    assert completed["advertised_broker_auth"][
        "connection_state_path"
    ] == "tcp_connecting>sasl_authenticating>connected"
    assert "disconnected" not in str(
        completed["advertised_broker_auth"]["connection_state_path"]
    )
    assert completed["group_coordinator"][
        "coordinator_discovered"
    ] is True
    assert completed["offset_fetch"]["committed_offset_present"] is False
    assert fake._client.set_topics_calls == [[settings.topic]]
    assert fake._client.cluster.partitions_for_topic_calls == [settings.topic]
    assert len(fake._client.cluster.leader_for_partition_calls) == 1
    leader_partition = fake._client.cluster.leader_for_partition_calls[0]
    assert getattr(leader_partition, "topic") == settings.topic
    assert getattr(leader_partition, "partition") == 0
    assert fake._client.cluster.broker_metadata_calls == [
        fake.advertised_node
    ]
    assert len(fake._client.poll_calls) == 1
    assert fake._client.poll_calls[0]["has_future"] is True
    assert all(
        metadata_priority is False
        for _node_id, metadata_priority in fake._client.is_ready_calls
    )
    assert len(fake._client.private_poll_calls) == 3
    assert fake._client.maybe_connect_calls == [
        (fake.bootstrap_node, False),
        (fake.advertised_node, False),
        (fake.coordinator_node, False),
    ]
    assert fake._client.init_connect_calls == [
        fake.bootstrap_node,
        fake.advertised_node,
        fake.coordinator_node,
    ]
    assert fake._client.fire_completed_calls == 3
    assert all(
        payload["elapsed_ms"] >= 0
        for payload in emitted
        if payload.get("status") == "ok" and "phase" in payload
    )
    serialized = json.dumps(emitted)
    for secret in (
        settings.broker_urls[0],
        settings.topic,
        settings.group_id,
        settings.account,
        settings.password,
        fake.bootstrap_node,
        fake.advertised_node,
        fake.coordinator_node,
        fake.exception_secret,
    ):
        assert secret not in serialized


def test_kafka_connection_trace_ignores_non_target_bootstrap_close() -> None:
    trace = _KafkaConnectionTrace()
    trace.begin("private-topic-leader-node")

    trace.record("private-bootstrap-node", "disconnected")
    trace.record_failure("private-bootstrap-node", None)
    trace.record("private-topic-leader-node", "tcp_connecting")
    trace.record("private-topic-leader-node", "sasl_authenticating")
    trace.record("private-topic-leader-node", "connected")

    summary = trace.safe_summary()
    assert summary["connection_state_path"] == (
        "tcp_connecting>sasl_authenticating>connected"
    )
    assert "disconnected" not in str(summary["connection_state_path"])
    assert trace.recorded_failure() is None


def test_kafka_connection_trace_preserves_specific_sasl_failure() -> None:
    from kafka.errors import (
        KafkaConnectionError,
        SaslAuthenticationFailedError,
    )

    trace = _KafkaConnectionTrace()
    trace.begin("private-bootstrap-node")
    trace.record_failure(
        "private-bootstrap-node",
        SaslAuthenticationFailedError("private password"),
    )
    trace.record_failure(
        "private-bootstrap-node",
        KafkaConnectionError("generic wrapper"),
    )

    failure = trace.recorded_failure()
    assert isinstance(failure, SaslAuthenticationFailedError)
    assert failure.args == ()


def test_kafka_startup_probe_reused_connections_do_not_claim_new_handshake(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    fake = _FakeKafkaStartupConsumer(
        reused_nodes={
            _FakeKafkaStartupConsumer.bootstrap_node,
            _FakeKafkaStartupConsumer.advertised_node,
            _FakeKafkaStartupConsumer.coordinator_node,
        }
    )
    _install_fake_startup_consumer(monkeypatch, fake)
    emitted: list[dict[str, bool | int | str]] = []

    DtsKafkaShadowConsumer(
        _startup_probe_settings(),
        DtsEventProcessor(InMemoryShadowSink()),
    ).startup_probe(phase_callback=emitted.append)

    completed = {
        payload["phase"]: payload
        for payload in emitted
        if payload.get("status") == "ok" and "phase" in payload
    }
    for phase in (
        "bootstrap_auth",
        "advertised_broker_auth",
        "coordinator_auth",
    ):
        payload = completed[phase]
        assert payload["request_type"] == "AuthenticatedConnectionReady"
        assert payload["authenticated_connection_ready"] is True
        assert payload["connection_reused"] is True
        assert payload["connection_state_path"] == "not_observed"
        assert payload["connection_state_observed"] is False
        assert payload["sasl_started_observed"] is False
        assert payload["sasl_authenticated_observed"] is False
        assert payload["protocol_version_applied_observed"] is False
        assert payload["api_versions_request_sent_observed"] is False
    assert fake._client.private_poll_calls == []
    assert fake._client.maybe_connect_calls == []
    assert fake._client.init_connect_calls == []
    assert len(fake._client.poll_calls) == 1
    assert fake._client.poll_calls[0]["has_future"] is True


def test_kafka_connection_close_sasl_error_is_rethrown_without_body(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    from kafka.errors import SaslAuthenticationFailedError

    fake = _FakeKafkaStartupConsumer(
        fail_at="bootstrap_auth_close_sasl",
    )
    _install_fake_startup_consumer(monkeypatch, fake)
    emitted: list[dict[str, bool | int | str]] = []

    with pytest.raises(SaslAuthenticationFailedError) as raised:
        DtsKafkaShadowConsumer(
            _startup_probe_settings(),
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe(phase_callback=emitted.append)

    assert raised.value.args == ()
    assert fake.closed is True
    failures = [
        payload
        for payload in without_phase_elapsed(emitted)
        if payload.get("status") == "fail"
    ]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["phase"] == "bootstrap_auth"
    assert failure["request_type"] == "AuthenticatedConnectionReady"
    assert failure["error_code"] == "DTS_BROKER_SASL_AUTHENTICATION_FAILED"
    assert failure["error_type"] == "SaslAuthenticationFailedError"
    assert failure["retriable"] is False
    assert failure["sasl_started_observed"] is True
    assert failure["sasl_authenticated_observed"] is False
    serialized = json.dumps(emitted)
    assert fake.exception_secret not in serialized
    assert _startup_probe_settings().password not in serialized


def test_kafka_startup_probe_stops_before_credentials_when_tcp_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kafka
    from app import dts_source_consumer as consumer_module

    def fail_tcp(_brokers: object) -> object:
        raise DtsConfigurationError("DTS_BROKER_TCP_CONNECTION_TIMEOUT")

    monkeypatch.setattr(consumer_module, "probe_broker_tcp", fail_tcp)
    monkeypatch.setattr(
        kafka,
        "KafkaConsumer",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Kafka client must not receive credentials")
        ),
    )
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )
    emitted: list[dict[str, int | str]] = []

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_BROKER_TCP_CONNECTION_TIMEOUT$",
    ):
        DtsKafkaShadowConsumer(
            settings,
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe(phase_callback=emitted.append)

    assert emitted == []


def test_kafka_startup_probe_uses_database_boundary_for_new_durable_target(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
    bypass_kafka_protocol_probe: None,
) -> None:
    import kafka

    class FakeConsumer:
        def committed(self, _partition: object, *, timeout_ms: int) -> int:
            assert timeout_ms == 15_000
            return 99

        def offsets_for_times(self, requested: dict[object, int]):
            return {
                partition: SimpleNamespace(offset=40)
                for partition in requested
            }

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000

    monkeypatch.setattr(kafka, "KafkaConsumer", lambda **_kwargs: FakeConsumer())
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )
    processor = SimpleNamespace(
        authoritative_checkpoint=True,
        resume_offset=lambda **_kwargs: None,
    )

    assert DtsKafkaShadowConsumer(settings, processor).startup_probe()[
        "initial_offset"
    ] == 40


def test_kafka_startup_probe_keeps_shadow_group_resume_behavior(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
    bypass_kafka_protocol_probe: None,
) -> None:
    import kafka

    class FakeConsumer:
        def committed(self, _partition: object, *, timeout_ms: int) -> int:
            assert timeout_ms == 15_000
            return 12

        def offsets_for_times(self, _requested: dict[object, int]) -> object:
            raise AssertionError("a shadow group with a commit must resume it")

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000

    monkeypatch.setattr(kafka, "KafkaConsumer", lambda **_kwargs: FakeConsumer())
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )

    assert DtsKafkaShadowConsumer(
        settings,
        DtsEventProcessor(InMemoryShadowSink()),
    ).startup_probe()["initial_offset"] == 12


@pytest.mark.parametrize(
    ("resolved", "expected_error"),
    [
        (None, "DTS_START_AT_OUTSIDE_AVAILABLE_RANGE"),
        (SimpleNamespace(offset="invalid"), "DTS_KAFKA_INITIAL_OFFSET_INVALID"),
    ],
)
def test_kafka_startup_probe_rejects_unresolvable_initial_offset(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
    bypass_kafka_protocol_probe: None,
    resolved: object,
    expected_error: str,
) -> None:
    import kafka

    class FakeConsumer:
        closed = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def offsets_for_times(self, requested: dict[object, int]):
            return {partition: resolved for partition in requested}

        def committed(self, _partition: object, *, timeout_ms: int) -> None:
            assert timeout_ms == 15_000
            return None

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000
            self.closed = True

    fake = FakeConsumer()
    monkeypatch.setattr(kafka, "KafkaConsumer", lambda **_kwargs: fake)
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )

    with pytest.raises(DtsConfigurationError, match=f"^{expected_error}$"):
        DtsKafkaShadowConsumer(
            settings,
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe()

    assert fake.closed is True


@pytest.mark.parametrize(
    "failed_phase",
    [
        "bootstrap_auth",
        "topic_metadata",
        "partition_check",
        "advertised_broker_auth",
        "group_coordinator",
        "coordinator_auth",
        "offset_fetch",
    ],
)
def test_kafka_startup_probe_stops_at_exact_failed_protocol_phase(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
    failed_phase: str,
) -> None:
    fake = _FakeKafkaStartupConsumer(fail_at=failed_phase)
    _install_fake_startup_consumer(monkeypatch, fake)
    settings = _startup_probe_settings()
    emitted: list[dict[str, bool | int | str]] = []

    with pytest.raises(Exception) as raised:
        DtsKafkaShadowConsumer(
            settings,
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe(phase_callback=emitted.append)

    if failed_phase == "partition_check":
        from kafka.errors import UnknownTopicOrPartitionError

        assert isinstance(raised.value, UnknownTopicOrPartitionError)
        expected_error_code = "DTS_BROKER_TOPIC_PARTITION_UNAVAILABLE"
    else:
        assert isinstance(raised.value, DtsConfigurationError)
        assert str(raised.value) == "DTS_BROKER_KAFKA_REQUEST_TIMEOUT"
        expected_error_code = "DTS_BROKER_KAFKA_REQUEST_TIMEOUT"
    assert fake.closed is True
    assert fake.close_autocommit is False
    protocol_phases = [
        "bootstrap_auth",
        "topic_metadata",
        "partition_check",
        "advertised_broker_auth",
        "group_coordinator",
        "coordinator_auth",
        "offset_fetch",
    ]
    failed_index = protocol_phases.index(failed_phase)
    assert [
        payload["phase"]
        for payload in emitted
        if payload.get("status") == "begin"
        and payload.get("phase") in protocol_phases
    ] == protocol_phases[: failed_index + 1]
    failures = [
        payload
        for payload in emitted
        if payload.get("status") == "fail"
    ]
    assert len(failures) == 1
    assert failures[0]["phase"] == failed_phase
    assert failures[0]["error_code"] == expected_error_code
    assert not any(
        payload.get("phase") == "offsets_for_times"
        for payload in emitted
    )
    serialized = json.dumps(emitted)
    for secret in (
        settings.broker_urls[0],
        settings.topic,
        settings.group_id,
        settings.account,
        settings.password,
        fake.bootstrap_node,
        fake.advertised_node,
        fake.coordinator_node,
        fake.exception_secret,
    ):
        assert secret not in serialized


def test_kafka_startup_phase_does_not_log_non_timeout_exception_text() -> None:
    emitted: list[dict[str, int | str]] = []
    sensitive_exception_text = (
        "broker.internal account-name provider-group-id plaintext-password"
    )

    def fail_consumer_open() -> None:
        raise RuntimeError(sensitive_exception_text)

    with pytest.raises(RuntimeError, match="plaintext-password"):
        _run_kafka_startup_phase(
            emitted.append,
            "consumer_open",
            fail_consumer_open,
        )

    assert without_phase_elapsed(emitted) == [
        {"phase": "consumer_open", "status": "begin"},
        {
            "phase": "consumer_open",
            "status": "fail",
            "error_type": "UnexpectedError",
            "error_code": "DTS_BROKER_CONSUMER_OPEN_FAILED",
            "retriable": False,
        },
    ]
    serialized = json.dumps(emitted)
    assert "broker.internal" not in serialized
    assert "account-name" not in serialized
    assert "provider-group-id" not in serialized
    assert "plaintext-password" not in serialized


def test_kafka_startup_phase_reports_allowlisted_nested_kafka_cause() -> None:
    from kafka.errors import SaslAuthenticationFailedError

    emitted: list[dict[str, int | str]] = []

    def fail_offset_fetch() -> None:
        try:
            raise SaslAuthenticationFailedError(
                "sasl-user plaintext-password"
            )
        except SaslAuthenticationFailedError as cause:
            raise RuntimeError("outer broker.internal") from cause

    with pytest.raises(RuntimeError, match="outer broker.internal"):
        _run_kafka_startup_phase(
            emitted.append,
            "offset_fetch",
            fail_offset_fetch,
        )

    assert without_phase_elapsed(emitted) == [
        {"phase": "offset_fetch", "status": "begin"},
        {
            "phase": "offset_fetch",
            "status": "fail",
            "error_code": "DTS_BROKER_SASL_AUTHENTICATION_FAILED",
            "error_type": "SaslAuthenticationFailedError",
            "retriable": False,
        },
    ]
    serialized = json.dumps(emitted)
    assert "sasl-user" not in serialized
    assert "plaintext-password" not in serialized
    assert "broker.internal" not in serialized


@pytest.mark.parametrize(
    ("error_name", "expected_code", "expected_retriable"),
    [
        (
            "SaslAuthenticationFailedError",
            "DTS_BROKER_SASL_AUTHENTICATION_FAILED",
            False,
        ),
        (
            "TopicAuthorizationFailedError",
            "DTS_BROKER_TOPIC_AUTHORIZATION_FAILED",
            False,
        ),
        (
            "GroupAuthorizationFailedError",
            "DTS_BROKER_GROUP_AUTHORIZATION_FAILED",
            False,
        ),
        (
            "ClusterAuthorizationFailedError",
            "DTS_BROKER_CLUSTER_AUTHORIZATION_FAILED",
            False,
        ),
        (
            "UnsupportedSaslMechanismError",
            "DTS_BROKER_SASL_PROTOCOL_MISMATCH",
            False,
        ),
        ("InvalidTopicError", "DTS_BROKER_TOPIC_INVALID", False),
        (
            "UnknownTopicOrPartitionError",
            "DTS_BROKER_TOPIC_PARTITION_UNAVAILABLE",
            True,
        ),
        (
            "UnsupportedVersionError",
            "DTS_BROKER_PROTOCOL_UNSUPPORTED",
            False,
        ),
        (
            "RequestTimedOutError",
            "DTS_BROKER_REQUEST_TIMED_OUT",
            True,
        ),
        ("NoBrokersAvailable", "DTS_BROKER_UNAVAILABLE", True),
        (
            "KafkaConnectionError",
            "DTS_BROKER_CONNECTION_FAILED",
            True,
        ),
        (
            "CoordinatorNotAvailableError",
            "DTS_BROKER_GROUP_COORDINATOR_UNAVAILABLE",
            True,
        ),
        (
            "RebalanceInProgressError",
            "DTS_BROKER_GROUP_STATE_FAILED",
            False,
        ),
        (
            "CorrelationIdError",
            "DTS_BROKER_PROTOCOL_FAILED",
            True,
        ),
        (
            "OffsetOutOfRangeError",
            "DTS_BROKER_OFFSET_UNAVAILABLE",
            False,
        ),
        (
            "KafkaConfigurationError",
            "DTS_BROKER_CLIENT_CONFIGURATION_INVALID",
            False,
        ),
        (
            "KafkaTimeoutError",
            "DTS_BROKER_KAFKA_REQUEST_TIMEOUT",
            True,
        ),
    ],
)
def test_kafka_diagnostic_allowlist_matches_client_semantics(
    error_name: str,
    expected_code: str,
    expected_retriable: bool,
) -> None:
    from kafka import errors as kafka_errors

    error_type = getattr(kafka_errors, error_name)
    diagnostic = safe_kafka_error_diagnostic(
        error_type("private details"),
        fallback_error_code="DTS_BROKER_FALLBACK",
    )

    assert diagnostic == {
        "error_code": expected_code,
        "error_type": error_name,
        "retriable": expected_retriable,
    }


def test_kafka_diagnostic_prefers_specific_broker_timeout_cause() -> None:
    from kafka.errors import KafkaTimeoutError, RequestTimedOutError

    try:
        try:
            raise RequestTimedOutError("private broker response")
        except RequestTimedOutError as cause:
            raise KafkaTimeoutError("private client timeout") from cause
    except KafkaTimeoutError as error:
        diagnostic = safe_kafka_error_diagnostic(
            error,
            fallback_error_code="DTS_BROKER_FALLBACK",
        )

    assert diagnostic == {
        "error_code": "DTS_BROKER_REQUEST_TIMED_OUT",
        "error_type": "RequestTimedOutError",
        "retriable": True,
    }


def test_kafka_diagnostic_respects_suppressed_exception_context() -> None:
    from kafka.errors import SaslAuthenticationFailedError

    try:
        try:
            raise SaslAuthenticationFailedError("plaintext-password")
        except SaslAuthenticationFailedError:
            raise RuntimeError("outer error") from None
    except RuntimeError as error:
        diagnostic = safe_kafka_error_diagnostic(
            error,
            fallback_error_code="DTS_BROKER_FALLBACK",
        )

    assert diagnostic is None


def test_unknown_kafka_error_is_not_declared_retriable() -> None:
    from kafka.errors import KafkaError

    diagnostic = safe_kafka_error_diagnostic(
        KafkaError("private unknown failure"),
        fallback_error_code="DTS_BROKER_FALLBACK",
    )

    assert diagnostic == {
        "error_code": "DTS_BROKER_FALLBACK",
        "error_type": "KafkaError",
        "retriable": False,
    }


def test_kafka_library_logs_never_reach_the_root_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kafka

    captured: list[logging.LogRecord] = []
    captured_kwargs: dict[str, object] = {}
    constructed = object()

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    root_logger = logging.getLogger()
    kafka_logger = logging.getLogger("kafka")
    kafka_connection_logger = logging.getLogger("kafka.conn")
    root_handler = CaptureHandler()
    monkeypatch.setattr(root_logger, "handlers", [root_handler])
    monkeypatch.setattr(root_logger, "level", logging.DEBUG)
    monkeypatch.setattr(kafka_logger, "handlers", [])
    monkeypatch.setattr(kafka_logger, "propagate", True)
    monkeypatch.setattr(kafka_logger, "level", logging.NOTSET)
    monkeypatch.setattr(kafka_connection_logger, "handlers", [root_handler])
    monkeypatch.setattr(kafka_connection_logger, "propagate", True)
    monkeypatch.setattr(kafka_connection_logger, "level", logging.DEBUG)

    def factory(**kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        kafka_connection_logger.error(
            "SaslAuthenticateRequest auth_bytes=plaintext-password"
        )
        return constructed

    monkeypatch.setattr(kafka, "KafkaConsumer", factory)
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )
    consumer = DtsKafkaShadowConsumer(
        settings,
        DtsEventProcessor(InMemoryShadowSink()),
    )

    opened = consumer._open_consumer()

    assert opened is constructed
    assert captured == []
    assert captured_kwargs["allow_auto_create_topics"] is False
    assert captured_kwargs["api_version"] == (2, 7)
    assert captured_kwargs["enable_auto_commit"] is False
    assert isinstance(captured_kwargs["kafka_client"], type)


def test_kafka_startup_probe_classifies_consumer_construction_timeout(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    import kafka
    from kafka.errors import KafkaTimeoutError

    monkeypatch.setattr(
        kafka,
        "KafkaConsumer",
        lambda **_kwargs: (_ for _ in ()).throw(
            KafkaTimeoutError("private broker details omitted")
        ),
    )
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_BROKER_KAFKA_REQUEST_TIMEOUT$",
    ):
        DtsKafkaShadowConsumer(
            settings,
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe()


def test_kafka_startup_probe_fails_closed_on_kafka_client_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    import kafka
    from app import dts_source_consumer as consumer_module

    monkeypatch.setattr(
        consumer_module,
        "_kafka_client_version",
        lambda: "2.2.21",
    )
    monkeypatch.setattr(
        kafka,
        "KafkaConsumer",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported client version must not be opened")
        ),
    )
    emitted: list[dict[str, bool | int | str]] = []

    with pytest.raises(
        DtsConfigurationError,
        match="^DTS_KAFKA_CLIENT_VERSION_UNSUPPORTED$",
    ):
        DtsKafkaShadowConsumer(
            _startup_probe_settings(),
            DtsEventProcessor(InMemoryShadowSink()),
        ).startup_probe(phase_callback=emitted.append)

    failures = [
        payload
        for payload in without_phase_elapsed(emitted)
        if payload.get("status") == "fail"
    ]
    assert failures == [
        {
            "phase": "consumer_open",
            "status": "fail",
            "error_code": "DTS_KAFKA_CLIENT_VERSION_UNSUPPORTED",
            "error_type": "DtsConfigurationError",
            "retriable": False,
        }
    ]


def test_kafka_startup_probe_validates_database_checkpoint_with_bounded_calls(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
) -> None:
    from app import dts_source_consumer as consumer_module

    fake = _FakeKafkaStartupConsumer(committed_offset=0)
    _install_fake_startup_consumer(monkeypatch, fake)
    current_time = [100.0]

    def advancing_monotonic() -> float:
        value = current_time[0]
        current_time[0] += 0.25
        return value

    monkeypatch.setattr(
        consumer_module,
        "monotonic",
        advancing_monotonic,
    )
    settings = _startup_probe_settings()
    processor = SimpleNamespace(
        authoritative_checkpoint=True,
        resume_offset=lambda **_kwargs: 0,
    )

    emitted: list[dict[str, bool | int | str]] = []
    result = DtsKafkaShadowConsumer(settings, processor).startup_probe(
        phase_callback=emitted.append
    )

    assert result["initial_offset"] == 0
    assert fake.closed is True
    assert fake.offsets_for_times_timeouts == []
    timeout_samples = fake.timeout_events
    assert all(0 < timeout_ms < 15_000 for timeout_ms in timeout_samples)
    assert len(set(timeout_samples)) >= 5
    assert timeout_samples == sorted(timeout_samples, reverse=True)
    metadata_poll = fake._client.poll_calls[0]
    synchronized_timeout = metadata_poll["consumer_timeout_ms"]
    assert synchronized_timeout == metadata_poll["client_timeout_ms"]
    assert metadata_poll["connection_timeouts_ms"] == (
        synchronized_timeout,
        synchronized_timeout,
        synchronized_timeout,
    )
    assert fake.config["request_timeout_ms"] == fake._client.config[
        "request_timeout_ms"
    ]
    assert {
        connection.config["request_timeout_ms"]
        for connection in fake._client._conns.values()
    } == {fake.config["request_timeout_ms"]}
    assert without_phase_elapsed([
        payload
        for payload in emitted
        if payload.get("phase") in {"beginning_offsets", "end_offsets"}
    ]) == [
        {
            "phase": "beginning_offsets",
            "request_type": "ListOffsetsEarliest",
            "status": "begin",
        },
        {
            "phase": "beginning_offsets",
            "request_type": "ListOffsetsEarliest",
            "status": "ok",
        },
        {
            "phase": "end_offsets",
            "request_type": "ListOffsetsLatest",
            "status": "begin",
        },
        {
            "phase": "end_offsets",
            "request_type": "ListOffsetsLatest",
            "status": "ok",
        },
    ]


@pytest.mark.parametrize(
    ("checkpoint", "committed", "expected_error"),
    [
        (11, 10, "DTS_KAFKA_DATABASE_OFFSET_OUTSIDE_AVAILABLE_RANGE"),
        (5, 6, "DTS_KAFKA_OFFSET_AHEAD_OF_DATABASE"),
    ],
)
def test_kafka_startup_probe_rejects_checkpoint_outside_safe_range(
    monkeypatch: pytest.MonkeyPatch,
    successful_broker_tcp_probe: None,
    bypass_kafka_protocol_probe: None,
    checkpoint: int,
    committed: int,
    expected_error: str,
) -> None:
    import kafka

    class FakeConsumer:
        closed = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def committed(self, _partition: object, *, timeout_ms: int) -> int:
            assert timeout_ms == 15_000
            return committed

        def beginning_offsets(self, partitions: list[object]):
            return {partition: 0 for partition in partitions}

        def end_offsets(self, partitions: list[object]):
            return {partition: 10 for partition in partitions}

        def close(self, *, autocommit: bool, timeout_ms: int) -> None:
            assert autocommit is False
            assert timeout_ms == 1_000
            self.closed = True

    fake = FakeConsumer()
    monkeypatch.setattr(kafka, "KafkaConsumer", lambda **_kwargs: fake)
    settings = DtsConsumerSettings(
        source_region="ovs",
        broker_urls=("broker.internal:18003",),
        topic="topic-v2",
        group_id="dtsovs1234567890",
        account="consumer",
        password="runtime-only",
        start_timestamp_seconds=1786550400,
    )
    processor = SimpleNamespace(
        authoritative_checkpoint=True,
        resume_offset=lambda **_kwargs: checkpoint,
    )

    with pytest.raises(DtsConfigurationError, match=f"^{expected_error}$"):
        DtsKafkaShadowConsumer(settings, processor).startup_probe()

    assert fake.closed is True
