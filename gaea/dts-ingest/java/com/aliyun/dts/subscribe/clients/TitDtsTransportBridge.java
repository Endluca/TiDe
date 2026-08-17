package com.aliyun.dts.subscribe.clients;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import com.aliyun.dts.subscribe.clients.common.Util;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Queue;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.PartitionInfo;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.AuthenticationException;
import org.apache.kafka.common.errors.AuthorizationException;
import org.apache.kafka.common.errors.RetriableException;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;

/**
 * Stdio bridge around the exact Kafka client shipped by the official Aliyun
 * DTS diagnostic package.
 *
 * <p>Standard output is reserved for one-line JSON protocol messages. Kafka,
 * DTS SDK and bridge diagnostics must use standard error through the separate
 * Log4j configuration.
 */
public final class TitDtsTransportBridge {
    private static final String TYPE = "type";
    private static final int MAX_COMMAND_BYTES = 1024 * 1024;
    private static final int MAX_BATCH_MESSAGES = 1000;
    private static final long DEFAULT_IDLE_TIMEOUT_MS = 10000L;

    private final BufferedReader input = new BufferedReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8));
    private final PrintWriter output = new PrintWriter(
            new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true);
    private final Queue<ConsumerRecord<byte[], byte[]>> bufferedRecords =
            new ArrayDeque<ConsumerRecord<byte[], byte[]>>();

    private KafkaConsumer<byte[], byte[]> consumer;
    private TopicPartition topicPartition;
    private ConsumerRecord<byte[], byte[]> inFlight;
    private long idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS;
    private boolean started;
    private boolean closed;

    private TitDtsTransportBridge() {
    }

    public static void main(String[] args) {
        if (args.length != 0) {
            emitStandaloneError("DTS_OFFICIAL_JAVA_ARGUMENTS_NOT_ALLOWED", false);
            return;
        }
        TitDtsTransportBridge bridge = new TitDtsTransportBridge();
        bridge.run();
    }

    private void run() {
        try {
            while (!closed) {
                JSONObject command = readCommand();
                if (command == null) {
                    break;
                }
                String type = requiredString(command, TYPE);
                if ("START".equals(type)) {
                    handleStart(command);
                } else if ("POLL".equals(type)) {
                    handlePoll(command);
                } else if ("CLOSE".equals(type)) {
                    handleClose();
                } else {
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_COMMAND_UNEXPECTED");
                }
            }
        } catch (ProtocolException error) {
            emitError(error.errorCode, false);
            safeLog(error.errorCode, error);
        } catch (AuthenticationException error) {
            emitError("DTS_OFFICIAL_JAVA_AUTHENTICATION_FAILED", false);
            safeLog("DTS_OFFICIAL_JAVA_AUTHENTICATION_FAILED", error);
        } catch (AuthorizationException error) {
            emitError("DTS_OFFICIAL_JAVA_AUTHORIZATION_FAILED", false);
            safeLog("DTS_OFFICIAL_JAVA_AUTHORIZATION_FAILED", error);
        } catch (RetriableException error) {
            emitError("DTS_OFFICIAL_JAVA_TRANSPORT_FAILED", true);
            safeLog("DTS_OFFICIAL_JAVA_TRANSPORT_FAILED", error);
        } catch (Throwable error) {
            emitError("DTS_OFFICIAL_JAVA_TRANSPORT_FAILED", false);
            safeLog("DTS_OFFICIAL_JAVA_TRANSPORT_FAILED", error);
        } finally {
            closeConsumer();
        }
    }

    private void handleStart(JSONObject command) {
        if (started || consumer != null) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_START_ALREADY_RECEIVED");
        }

        List<String> brokers = requiredStringArray(command, "broker_urls");
        String topic = requiredString(command, "topic");
        String groupId = requiredString(command, "group_id");
        String account = requiredString(command, "account");
        String password = requiredString(command, "password");
        int partition = requiredNonNegativeInt(command, "partition");
        Long resumeOffset = optionalNonNegativeLong(command, "resume_offset");
        Long startTimestampSeconds = optionalNonNegativeLong(
                command, "start_timestamp_seconds");
        Long requestedIdleTimeoutMs = optionalPositiveLong(
                command, "idle_timeout_ms");
        if (requestedIdleTimeoutMs != null) {
            idleTimeoutMs = requestedIdleTimeoutMs.longValue();
        }

        Properties sourceProperties = new Properties();
        sourceProperties.setProperty("broker", join(brokers));
        sourceProperties.setProperty("group", groupId);
        sourceProperties.setProperty("user", account);
        sourceProperties.setProperty("password", password);

        Properties kafkaProperties = new Properties();
        // This is the vendor-owned configuration path used by the official
        // diagnostic client. It builds the DTS username/group JAAS contract.
        Util.mergeSourceKafkaProperties(sourceProperties, kafkaProperties);
        kafkaProperties.setProperty(
                ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        kafkaProperties.setProperty(
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,
                ByteArrayDeserializer.class.getName());
        kafkaProperties.setProperty(
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG,
                ByteArrayDeserializer.class.getName());
        kafkaProperties.setProperty(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, "1");

        try {
            consumer = new KafkaConsumer<byte[], byte[]>(kafkaProperties);
        } finally {
            sourceProperties.clear();
            kafkaProperties.clear();
            password = null;
        }

        topicPartition = new TopicPartition(topic, partition);
        List<PartitionInfo> partitions = consumer.partitionsFor(topic);
        boolean partitionPresent = false;
        for (PartitionInfo info : partitions) {
            if (info.partition() == partition) {
                partitionPresent = true;
                break;
            }
        }
        if (!partitionPresent) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_PARTITION_NOT_FOUND");
        }

        consumer.assign(Collections.singletonList(topicPartition));
        OffsetAndMetadata committed = consumer.committed(topicPartition);
        long beginOffset = onlyOffset(
                consumer.beginningOffsets(
                        Collections.singletonList(topicPartition)),
                topicPartition,
                "DTS_OFFICIAL_JAVA_BEGIN_OFFSET_UNAVAILABLE");
        long endOffset = onlyOffset(
                consumer.endOffsets(Collections.singletonList(topicPartition)),
                topicPartition,
                "DTS_OFFICIAL_JAVA_END_OFFSET_UNAVAILABLE");

        long initialOffset;
        if (resumeOffset != null) {
            initialOffset = resumeOffset.longValue();
            if (committed != null && committed.offset() > initialOffset) {
                throw new ProtocolException(
                        "DTS_KAFKA_OFFSET_AHEAD_OF_DATABASE");
            }
        } else if (startTimestampSeconds != null) {
            initialOffset = resolveTimestampOffset(
                    startTimestampSeconds.longValue());
        } else {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_INITIAL_OFFSET_REQUIRED");
        }
        if (initialOffset < beginOffset || initialOffset > endOffset) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_INITIAL_OFFSET_OUT_OF_RANGE");
        }
        consumer.seek(topicPartition, initialOffset);

        JSONObject ready = message("READY");
        ready.put("initial_offset", initialOffset);
        ready.put("committed_present", committed != null);
        ready.put("begin_offset", beginOffset);
        ready.put("end_offset", endOffset);
        emit(ready);
        started = true;
        safeLog("DTS_OFFICIAL_JAVA_READY", null);
    }

    private long resolveTimestampOffset(long timestampSeconds) {
        if (timestampSeconds > Long.MAX_VALUE / 1000L) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_START_TIMESTAMP_INVALID");
        }
        Map<TopicPartition, Long> query =
                new HashMap<TopicPartition, Long>();
        query.put(topicPartition, timestampSeconds * 1000L);
        return resolveOffsetForTimes(query);
    }

    private long resolveOffsetForTimes(Map<TopicPartition, Long> query) {
        org.apache.kafka.clients.consumer.OffsetAndTimestamp match =
                consumer.offsetsForTimes(query).get(topicPartition);
        if (match == null) {
            throw new ProtocolException(
                    "DTS_KAFKA_START_AT_OUTSIDE_AVAILABLE_RANGE");
        }
        return match.offset();
    }

    private void handlePoll(JSONObject command) throws IOException {
        requireStarted();
        if (inFlight != null) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ALREADY_IN_FLIGHT");
        }
        int maxMessages = requiredPositiveInt(command, "max_messages");
        if (maxMessages > MAX_BATCH_MESSAGES) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_MAX_MESSAGES_INVALID");
        }

        int seen = 0;
        while (seen < maxMessages && !closed) {
            ConsumerRecord<byte[], byte[]> record = nextRecord();
            if (record == null) {
                break;
            }
            inFlight = record;
            emitEvent(record);

            JSONObject acknowledgement = readCommand();
            if (acknowledgement == null) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_ACK_REQUIRED");
            }
            String acknowledgementType = requiredString(acknowledgement, TYPE);
            if ("CLOSE".equals(acknowledgementType)) {
                handleClose();
                return;
            }
            if (!"DURABLE_ACK".equals(acknowledgementType)) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_ACK_REQUIRED");
            }
            commitAcknowledgedRecord(acknowledgement);
            seen += 1;
        }

        if (!closed) {
            JSONObject complete = message("BATCH_COMPLETE");
            complete.put("seen", seen);
            emit(complete);
        }
    }

    private ConsumerRecord<byte[], byte[]> nextRecord() {
        ConsumerRecord<byte[], byte[]> buffered = bufferedRecords.poll();
        if (buffered != null) {
            return buffered;
        }
        ConsumerRecords<byte[], byte[]> records = consumer.poll(idleTimeoutMs);
        for (ConsumerRecord<byte[], byte[]> record : records) {
            bufferedRecords.add(record);
        }
        return bufferedRecords.poll();
    }

    private void emitEvent(ConsumerRecord<byte[], byte[]> record) {
        byte[] value = record.value();
        if (value == null || value.length == 0) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID");
        }
        JSONObject event = message("EVENT");
        event.put("topic", record.topic());
        event.put("partition", record.partition());
        event.put("offset", record.offset());
        event.put(
                "payload_base64",
                Base64.getEncoder().encodeToString(value));
        emit(event);
    }

    private void commitAcknowledgedRecord(JSONObject acknowledgement) {
        ConsumerRecord<byte[], byte[]> record = inFlight;
        if (record == null) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_ACK_WITHOUT_EVENT");
        }
        long acknowledgedOffset = requiredNonNegativeLong(
                acknowledgement, "offset");
        long nextOffset = requiredNonNegativeLong(
                acknowledgement, "next_offset");
        long sourceTimestamp = requiredNonNegativeLong(
                acknowledgement, "source_timestamp");
        if (acknowledgedOffset != record.offset()
                || record.offset() == Long.MAX_VALUE
                || nextOffset != record.offset() + 1L) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_ACK_OFFSET_MISMATCH");
        }

        OffsetAndMetadata offsetAndMetadata = new OffsetAndMetadata(
                nextOffset, Long.toString(sourceTimestamp));
        consumer.commitSync(Collections.singletonMap(
                topicPartition, offsetAndMetadata));

        JSONObject committed = message("COMMITTED");
        committed.put("offset", acknowledgedOffset);
        committed.put("next_offset", nextOffset);
        emit(committed);
        inFlight = null;
    }

    private void handleClose() {
        if (closed) {
            return;
        }
        closed = true;
        closeConsumer();
        emit(message("CLOSED"));
        safeLog("DTS_OFFICIAL_JAVA_CLOSED", null);
    }

    private void closeConsumer() {
        KafkaConsumer<byte[], byte[]> current = consumer;
        consumer = null;
        if (current != null) {
            try {
                current.close();
            } catch (Throwable error) {
                safeLog("DTS_OFFICIAL_JAVA_CLOSE_FAILED", error);
            }
        }
        bufferedRecords.clear();
        inFlight = null;
    }

    private void requireStarted() {
        if (!started || consumer == null || topicPartition == null || closed) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_NOT_READY");
        }
    }

    private JSONObject readCommand() throws IOException {
        String line = input.readLine();
        if (line == null) {
            return null;
        }
        if (line.isEmpty() || line.length() > MAX_COMMAND_BYTES) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        try {
            JSONObject command = JSON.parseObject(line);
            if (command == null) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
            }
            return command;
        } catch (ProtocolException error) {
            throw error;
        } catch (RuntimeException error) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
    }

    private static List<String> requiredStringArray(
            JSONObject object, String field) {
        JSONArray values = object.getJSONArray(field);
        if (values == null || values.isEmpty()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        List<String> result = new ArrayList<String>(values.size());
        for (Object value : values) {
            if (!(value instanceof String) || ((String) value).isEmpty()) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
            }
            result.add((String) value);
        }
        return result;
    }

    private static String requiredString(JSONObject object, String field) {
        Object value = object.get(field);
        if (!(value instanceof String) || ((String) value).isEmpty()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return (String) value;
    }

    private static int requiredNonNegativeInt(
            JSONObject object, String field) {
        long value = requiredNonNegativeLong(object, field);
        if (value > Integer.MAX_VALUE) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return (int) value;
    }

    private static int requiredPositiveInt(JSONObject object, String field) {
        int value = requiredNonNegativeInt(object, field);
        if (value < 1) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return value;
    }

    private static long requiredNonNegativeLong(
            JSONObject object, String field) {
        Object value = object.get(field);
        if (!(value instanceof Number)) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        long result = ((Number) value).longValue();
        if (result < 0) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return result;
    }

    private static Long optionalNonNegativeLong(
            JSONObject object, String field) {
        if (!object.containsKey(field) || object.get(field) == null) {
            return null;
        }
        return Long.valueOf(requiredNonNegativeLong(object, field));
    }

    private static Long optionalPositiveLong(
            JSONObject object, String field) {
        Long value = optionalNonNegativeLong(object, field);
        if (value != null && value.longValue() < 1L) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return value;
    }

    private static long onlyOffset(
            Map<TopicPartition, Long> offsets,
            TopicPartition partition,
            String errorCode) {
        Long value = offsets.get(partition);
        if (value == null || value.longValue() < 0L) {
            throw new ProtocolException(errorCode);
        }
        return value.longValue();
    }

    private static String join(List<String> values) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            if (result.length() > 0) {
                result.append(',');
            }
            result.append(value);
        }
        return result.toString();
    }

    private static JSONObject message(String type) {
        JSONObject result = new JSONObject(true);
        result.put(TYPE, type);
        return result;
    }

    private void emit(JSONObject message) {
        output.println(message.toJSONString());
        if (output.checkError()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_PROTOCOL_WRITE_FAILED");
        }
    }

    private void emitError(String errorCode, boolean retriable) {
        JSONObject error = message("ERROR");
        error.put("error_code", errorCode);
        error.put("retriable", retriable);
        try {
            emit(error);
        } catch (Throwable ignored) {
            // The parent process will classify EOF if stdout is already gone.
        }
    }

    private static void emitStandaloneError(
            String errorCode, boolean retriable) {
        JSONObject error = message("ERROR");
        error.put("error_code", errorCode);
        error.put("retriable", retriable);
        System.out.println(error.toJSONString());
        System.out.flush();
    }

    private static void safeLog(String code, Throwable error) {
        String errorType = error == null
                ? "none"
                : error.getClass().getName();
        System.err.println(code + " error_type=" + errorType);
    }

    private static final class ProtocolException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        private final String errorCode;

        ProtocolException(String errorCode) {
            super(errorCode);
            this.errorCode = errorCode;
        }
    }

}
