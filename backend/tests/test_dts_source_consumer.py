from __future__ import annotations

import io
from datetime import time
from types import SimpleNamespace

import pytest
from fastavro import schemaless_writer

from app.dts_source_consumer import (
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaShadowConsumer,
    DtsRecordError,
    InMemoryShadowSink,
    _parsed_avro_schema,
    build_change_event,
    decode_dts_avro,
    derive_penalty_flags,
    is_peak_lesson,
    project_appoint_candidate,
    reduce_latest_complaints,
    route_dirty_keys,
    teacher_matches_region,
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


def test_settings_use_epoch_seconds_and_build_official_sasl_username() -> None:
    values = {
        "TIT_DTS_SOURCE_REGION": "ovs",
        "TIT_DTS_BROKER_URL": "broker.internal:18003",
        "TIT_DTS_TOPIC": "topic-v2",
        "TIT_DTS_GROUP_ID": "tit-ovs-group",
        "TIT_DTS_ACCOUNT": "consumer",
        "TIT_DTS_PASSWORD": "runtime-only",
        "TIT_DTS_START_AT": "2026-08-13T00:00:00+08:00",
    }

    settings = DtsConsumerSettings.from_env(values)

    assert settings.sasl_username == "consumer-tit-ovs-group"
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
            self.closed = False

        def assign(self, partitions: list[object]) -> None:
            self.assigned = partitions

        def committed(self, _partition: object) -> None:
            return None

        def offsets_for_times(self, requested: dict[object, int]):
            self.offsets_for_times_calls.append(requested)
            return {partition: SimpleNamespace(offset=40) for partition in requested}

        def seek(self, partition: object, offset: int) -> None:
            self.seek_calls.append((partition, offset))

        def commit(self, *, offsets: dict[object, object]) -> None:
            self.commit_calls.append(offsets)

        def close(self) -> None:
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
        group_id="tit-ovs-group",
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
    assert committed.leader_epoch == -1
    assert fake.kwargs["enable_auto_commit"] is False
    assert fake.kwargs["sasl_plain_username"] == "consumer-tit-ovs-group"
    assert fake.closed is True
