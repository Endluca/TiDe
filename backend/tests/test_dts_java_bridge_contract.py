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


def test_official_java_bridge_preserves_database_before_kafka_commit() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "Util.mergeSourceKafkaProperties" in source
    assert "ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, \"false\"" in source
    assert "ConsumerConfig.MAX_POLL_RECORDS_CONFIG, \"1\"" in source
    assert "ConsumerConfig.CLIENT_ID_CONFIG" not in source
    assert '"payload_base64"' in source
    assert '"DURABLE_ACK"' in source
    assert "nextOffset != record.offset() + 1L" in source
    assert (
        "new OffsetAndMetadata(\n"
        "                nextOffset, Long.toString(sourceTimestamp))"
    ) in source
    assert "consumer.commitSync" in source
    assert source.index('"DURABLE_ACK"') < source.index("consumer.commitSync")
    assert source.index("consumer.commitSync") < source.index('message("COMMITTED")')
    assert "else if (committed != null)" not in source
    assert "DTS_KAFKA_OFFSET_AHEAD_OF_DATABASE" in source
    assert source.index("if (resumeOffset != null)") < source.index(
        "else if (startTimestampSeconds != null)"
    )


def test_official_java_bridge_fails_when_start_time_has_no_offset() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "DTS_KAFKA_START_AT_OUTSIDE_AVAILABLE_RANGE" in source
    assert "match == null ? endOffset" not in source
    assert (
        'if (match == null) {\n'
        '            throw new ProtocolException(\n'
        '                    "DTS_KAFKA_START_AT_OUTSIDE_AVAILABLE_RANGE");'
    ) in source


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
