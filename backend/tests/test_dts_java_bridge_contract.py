from pathlib import Path


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
        "new SpecificDatumWriter<Record>(Record.class)",
        "SchemaNormalization.parsingFingerprint(",
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
    )
    for marker in forbidden:
        assert marker not in source


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
