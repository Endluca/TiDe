import base64
import re
from pathlib import Path

from app.dts_source_consumer import decode_dts_sdk_1_4_avro


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = (
    ROOT
    / "gaea"
    / "dts-ingest"
    / "java"
    / "com"
    / "aliyun"
    / "dts"
    / "subscribe"
    / "clients"
    / "TitDtsTransportBridge.java"
)
LOG4J = ROOT / "gaea" / "dts-ingest" / "log4j-bridge.properties"
AVRO_SELF_TEST = BRIDGE.with_name("TitDtsTransportBridgeAvroSelfTest.java")


def test_official_java_bridge_uses_the_vendor_sdk_main_path() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    required = (
        "new ConsumerContext(",
        "ConsumerSubscribeMode.ASSIGN",
        "context.setForceUseCheckpoint(true)",
        "context.setUseLocalCheckpointStore(false)",
        "context.setCheckpointCommitInterval(0L)",
        "new DefaultDTSConsumer(context)",
        "created.addRecordListeners(listeners)",
        "created.start()",
        "consume(DefaultUserRecord record)",
        "userRecord.getAvroRecord()",
        "avroRecord.toByteBuffer()",
        "SchemaNormalization.parsingFingerprint(",
        '"CRC-64-AVRO", Record.getClassSchema()',
        '"SHA-256", Record.getClassSchema()',
        '"avro_writer_schema_fingerprint_sha256"',
    )
    for marker in required:
        assert marker in source

    forbidden = (
        "KafkaConsumer<",
        "ConsumerRecord<",
        "consumer.partitionsFor",
        "consumer.poll",
        "consumer.committed",
        "consumer.offsetsForTimes",
        "consumer.commitSync",
        "OffsetAndMetadata",
        "Util.mergeSourceKafkaProperties",
        "SpecificDatumWriter",
        "ByteArrayOutputStream",
        "EncoderFactory",
    )
    for marker in forbidden:
        assert marker not in source


def test_official_java_bridge_validates_and_strips_single_object_header() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    encoding = source[
        source.index("static byte[] encodeOfficialRecord(") : source.index(
            "private void acceptDurableAcknowledgement("
        )
    ]
    lower = encoding.lower()

    # Record.toByteBuffer() is the encoder generated with the pinned SDK's
    # SpecificData model. Its result is Avro single-object encoding, so the
    # bridge must validate C3 01 + the schema fingerprint before exposing only
    # the schemaless datum to Python.
    assert "tobytebuffer()" in lower
    assert "0xc3" in lower
    assert "0x01" in lower
    assert "avro_single_object_header_bytes" in lower
    assert '"crc-64-avro"' in lower
    assert "remaining()" in lower
    assert "get(payload)" in lower
    assert ".array()" not in lower
    assert "dts_official_java_event_encoding_failed" in lower
    assert "catch (runtimeexception" in lower


def test_official_java_bridge_only_normalizes_empty_image_enums() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    normalization = source[
        source.index(
            "private static Record normalizeOfficialEmptyObjects("
        ) : source.index("private void acceptDurableAcknowledgement(")
    ]

    assert "sourceRecord.getBeforeImages()" in normalization
    assert "sourceRecord.getAfterImages()" in normalization
    assert "item instanceof EmptyObject" in normalization
    assert "new GenericData.EnumSymbol(" in normalization
    assert "EmptyObject.getClassSchema(), emptyObject.name()" in normalization
    assert "return sourceRecord;" in normalization
    assert "return new Record(" in normalization
    assert ".setBeforeImages(" not in normalization
    assert ".setAfterImages(" not in normalization


def test_first_official_record_is_encoded_before_ready_and_reused() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    startup = source[
        source.index("firstRecord = awaitRecord(") : source.index(
            "started = true;"
        )
    ]
    emit_event = source[
        source.index("private void emitEvent(") : source.index(
            "static byte[] encodeOfficialRecord("
        )
    ]

    assert startup.index("firstRecord.encodedPayload();") < startup.index(
        "emit(ready);"
    )
    assert "byte[] payload = envelope.encodedPayload();" in emit_event


def test_build_time_avro_fixture_covers_every_image_union_branch() -> None:
    source = AVRO_SELF_TEST.read_text(encoding="utf-8")
    fixture_block = source[
        source.index("UNION_FIXTURE_BASE64") : source.index(
            "private TitDtsTransportBridgeAvroSelfTest()"
        )
    ]
    encoded_parts = re.findall(r'"([A-Za-z0-9+/=]+)"', fixture_block)
    assert encoded_parts

    record = decode_dts_sdk_1_4_avro(
        base64.b64decode("".join(encoded_parts), validate=True)
    )
    images = record["afterImages"]

    assert [field["name"] for field in record["fields"]] == [
        f"f{index}" for index in range(13)
    ]
    assert isinstance(images, list)
    assert len(images) == 14
    assert images[0] == {"precision": 20, "value": "8"}
    assert images[1] == {"charset": "utf8", "value": b"hello"}
    assert images[2]["scale"] == 2
    assert images[3]["value"] == 1.5
    assert images[4]["timestamp"] == 1786342560
    assert images[5]["year"] == 2026
    assert images[6]["timezone"] == "UTC"
    assert images[7]["type"] == "POINT"
    assert images[8]["type"] == "WKT"
    assert images[9]["type"] == "BLOB"
    assert images[10]["type"] == "JSON"
    assert images[11:] == ["NONE", "NULL", None]
    assert record["bornTimestamp"] == 0

    assert "new AvroDeserializer().deserialize(expected)" in source
    assert "TitDtsTransportBridge.encodeOfficialRecord(" in source
    assert "Arrays.equals(expected, actual)" in source


def test_official_java_bridge_encoding_diagnostics_do_not_render_data() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "error.getMessage()" not in source
    assert "error.toString()" not in source
    assert "avroRecord.toString()" not in source
    assert "userRecord.toString()" not in source
    assert "System.err.println(code + \" error_type=\" + errorType)" in source


def test_official_java_bridge_preserves_database_before_sdk_checkpoint() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert '"payload_base64"' in source
    assert '"DURABLE_ACK"' in source
    assert 'message("SDK_CHECKPOINT_ACCEPTED")' in source
    assert "record.commit(Long.toString(sourceTimestamp))" in source
    assert source.index('"DURABLE_ACK"') < source.index(
        "record.commit(Long.toString(sourceTimestamp))"
    )
    advance_branch = source[source.index("void awaitDecision()") :]
    assert (
        "if (ACTION_ADVANCE.equals(action)) {\n"
        "                    record.commit(Long.toString(sourceTimestamp));"
    ) in advance_branch
    assert "ACTION_REPLAY" in source
    assert 'message("COMMITTED")' not in source


def test_official_java_bridge_resumes_from_database_timestamp_and_offset() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert 'optionalNonNegativeLong(command, "resume_offset")' in source
    assert (
        'optionalNonNegativeLong(\n'
        '                command, "resume_source_timestamp")'
    ) in source
    assert 'checkpointTimestampSeconds + "@" + resumeOffset.longValue()' in source
    assert "resumeCheckpointPresent" in source
    assert 'ready.put("first_record_offset", firstOffset)' in source


def test_official_java_bridge_keeps_logs_off_protocol_stdout() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    log4j = LOG4J.read_text(encoding="utf-8")

    assert "System.err.println" in source
    assert "System.out.println(error.toJSONString())" in source
    assert "System.out.print(" not in source
    assert "System.out.printf(" not in source
    assert "log4j.appender.STDERR.Target=System.err" in log4j
    assert "System.out" not in log4j
    assert (
        "log4j.logger.com.aliyun.dts.subscribe.clients."
        "recordgenerator=OFF"
    ) in log4j
    assert (
        "log4j.logger.com.aliyun.dts.subscribe.clients."
        "recordprocessor=OFF"
    ) in log4j
    assert "org.apache.kafka.clients.NetworkClient=TRACE" in log4j
    assert "org.apache.kafka.common.network.Selector=TRACE" in log4j
    assert (
        "org.apache.kafka.common.security.authenticator."
        "SaslClientAuthenticator=TRACE"
    ) in log4j


def test_official_java_bridge_never_reports_idle_after_sdk_termination() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert (
        "created.start();\n"
        "                    if (!closed) {\n"
        "                        sdkFailure = new SdkTerminatedException();"
    ) in source
    assert "throwRecordedFailureIfPresent();" in source
    assert "consumeCloseCommandIfAvailable()" in source
    assert '"CLOSE".equals(requiredString(command, TYPE))' in source
    assert "currentThread.join(3000L)" in source
    assert (
        "private static final class SdkTerminatedException\n"
        "            extends RetriableException"
    ) in source
