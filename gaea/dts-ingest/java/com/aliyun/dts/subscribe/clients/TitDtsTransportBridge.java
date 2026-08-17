package com.aliyun.dts.subscribe.clients;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import com.aliyun.dts.subscribe.clients.ConsumerContext.ConsumerSubscribeMode;
import com.aliyun.dts.subscribe.clients.common.RecordListener;
import com.aliyun.dts.subscribe.clients.formats.avro.EmptyObject;
import com.aliyun.dts.subscribe.clients.formats.avro.Record;
import com.aliyun.dts.subscribe.clients.record.DefaultUserRecord;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.apache.avro.SchemaNormalization;
import org.apache.avro.generic.GenericData;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.AuthenticationException;
import org.apache.kafka.common.errors.AuthorizationException;
import org.apache.kafka.common.errors.RetriableException;

/**
 * Durable-ACK bridge around the official Aliyun DTS SDK 1.4.0 consumer path.
 *
 * <p>The SDK owns precheck, KafkaRecordFetcher, Avro generation and record
 * processing. Standard output is reserved for the Python parent protocol;
 * SDK/Kafka diagnostics are routed to standard error by log4j.properties.
 */
public final class TitDtsTransportBridge {
    private static final String TYPE = "type";
    private static final String ACTION_ADVANCE = "ADVANCE";
    private static final String ACTION_REPLAY = "REPLAY";
    private static final int MAX_COMMAND_BYTES = 1024 * 1024;
    private static final int MAX_BATCH_MESSAGES = 1000;
    private static final int AVRO_SINGLE_OBJECT_HEADER_BYTES = 10;
    private static final long DEFAULT_IDLE_TIMEOUT_MS = 10000L;
    private static final long SDK_START_TIMEOUT_MS = 305000L;

    private final BufferedReader input = new BufferedReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8));
    private final PrintWriter output = new PrintWriter(
            new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true);
    private final ArrayBlockingQueue<RecordEnvelope> records =
            new ArrayBlockingQueue<RecordEnvelope>(1);

    private volatile Throwable sdkFailure;
    private volatile Throwable listenerFailure;
    private volatile boolean closed;
    private DefaultDTSConsumer consumer;
    private Thread consumerThread;
    private RecordEnvelope firstRecord;
    private RecordEnvelope inFlight;
    private long idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS;
    private boolean started;

    private TitDtsTransportBridge() {
    }

    public static void main(String[] args) {
        if (args.length != 0) {
            emitStandaloneError("DTS_OFFICIAL_JAVA_ARGUMENTS_NOT_ALLOWED", false);
            return;
        }
        new TitDtsTransportBridge().run();
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
        Long resumeSourceTimestamp = optionalNonNegativeLong(
                command, "resume_source_timestamp");
        Long startTimestampSeconds = optionalNonNegativeLong(
                command, "start_timestamp_seconds");
        Long requestedIdleTimeoutMs = optionalPositiveLong(
                command, "idle_timeout_ms");
        if (partition != 0) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_PARTITION_UNSUPPORTED");
        }
        if ((resumeOffset == null) != (resumeSourceTimestamp == null)) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RESUME_CHECKPOINT_INCOMPLETE");
        }
        if (resumeOffset == null && startTimestampSeconds == null) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_INITIAL_CHECKPOINT_REQUIRED");
        }
        if (requestedIdleTimeoutMs != null) {
            idleTimeoutMs = requestedIdleTimeoutMs.longValue();
        }

        final boolean resumeCheckpointPresent = resumeOffset != null;
        final long checkpointTimestampSeconds = resumeCheckpointPresent
                ? resumeSourceTimestamp.longValue()
                : startTimestampSeconds.longValue();
        final String initialCheckpoint = resumeCheckpointPresent
                ? checkpointTimestampSeconds + "@" + resumeOffset.longValue()
                : Long.toString(checkpointTimestampSeconds);

        ConsumerContext context = new ConsumerContext(
                join(brokers),
                topic,
                groupId,
                account,
                password,
                initialCheckpoint,
                ConsumerSubscribeMode.ASSIGN);
        context.setForceUseCheckpoint(true);
        context.setUseLocalCheckpointStore(false);
        context.setCheckpointCommitInterval(0L);

        final DefaultDTSConsumer created = new DefaultDTSConsumer(context);
        Map<String, RecordListener> listeners =
                new HashMap<String, RecordListener>();
        listeners.put("titDurableAckListener", new RecordListener() {
            @Override
            public void consume(DefaultUserRecord record) {
                consumeOfficialRecord(record);
            }
        });
        created.addRecordListeners(listeners);
        consumer = created;
        password = null;

        consumerThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    created.start();
                    if (!closed) {
                        sdkFailure = new SdkTerminatedException();
                        cancelPendingRecords();
                    }
                } catch (Throwable error) {
                    sdkFailure = error;
                    cancelPendingRecords();
                }
            }
        }, "tit-dts-official-consumer");
        consumerThread.setDaemon(true);
        consumerThread.start();

        firstRecord = awaitRecord(SDK_START_TIMEOUT_MS);
        DefaultUserRecord record = firstRecord.record;
        // Materialize the first official SDK record before READY so an
        // encoder/schema incompatibility is a startup failure, not a false
        // healthy transition followed by a deterministic POLL failure.
        firstRecord.encodedPayload();
        TopicPartition topicPartition = requiredTopicPartition(record);
        if (!topic.equals(topicPartition.topic())
                || partition != topicPartition.partition()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RECORD_IDENTITY_MISMATCH");
        }
        long firstOffset = requiredRecordOffset(record);
        long firstSourceTimestamp = requiredSourceTimestamp(record);

        JSONObject ready = message("READY");
        ready.put("transport", "official_dts_sdk");
        ready.put("subscribe_mode", "ASSIGN");
        ready.put("partition", topicPartition.partition());
        ready.put("first_record_offset", firstOffset);
        ready.put("first_record_source_timestamp", firstSourceTimestamp);
        ready.put(
                "avro_writer_schema_fingerprint_sha256",
                avroWriterSchemaFingerprintSha256());
        ready.put("resume_checkpoint_present", resumeCheckpointPresent);
        ready.put("checkpoint_timestamp_seconds", checkpointTimestampSeconds);
        emit(ready);
        started = true;
        safeLog("DTS_OFFICIAL_JAVA_READY", null);
    }

    private static String avroWriterSchemaFingerprintSha256() {
        final byte[] fingerprint;
        try {
            fingerprint = SchemaNormalization.parsingFingerprint(
                    "SHA-256", Record.getClassSchema());
        } catch (NoSuchAlgorithmException error) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_AVRO_SCHEMA_UNAVAILABLE");
        }
        final char[] hexadecimal = "0123456789abcdef".toCharArray();
        char[] encoded = new char[fingerprint.length * 2];
        for (int index = 0; index < fingerprint.length; index++) {
            int value = fingerprint[index] & 0xff;
            encoded[index * 2] = hexadecimal[value >>> 4];
            encoded[index * 2 + 1] = hexadecimal[value & 0x0f];
        }
        return new String(encoded);
    }

    private void consumeOfficialRecord(DefaultUserRecord record) {
        if (record == null) {
            listenerFailure = new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RECORD_INVALID");
            return;
        }
        RecordEnvelope envelope = new RecordEnvelope(record);
        try {
            while (!closed && !records.offer(envelope, 200L, TimeUnit.MILLISECONDS)) {
                // Keep the official listener single-in-flight and stoppable.
            }
            if (closed) {
                envelope.cancel();
                return;
            }
            envelope.awaitDecision();
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            envelope.fail(error);
            listenerFailure = error;
        } catch (RuntimeException error) {
            envelope.fail(error);
            listenerFailure = error;
            throw error;
        } catch (Error error) {
            envelope.fail(error);
            listenerFailure = error;
            throw error;
        }
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
            RecordEnvelope envelope;
            if (firstRecord != null) {
                envelope = firstRecord;
                firstRecord = null;
            } else {
                envelope = awaitRecord(idleTimeoutMs);
            }
            if (envelope == null) {
                break;
            }
            inFlight = envelope;
            emitEvent(envelope);

            JSONObject acknowledgement = readCommand();
            if (acknowledgement == null) {
                throw new ProtocolException("DTS_OFFICIAL_JAVA_ACK_REQUIRED");
            }
            String acknowledgementType = requiredString(acknowledgement, TYPE);
            if ("CLOSE".equals(acknowledgementType)) {
                handleClose();
                return;
            }
            if (!"DURABLE_ACK".equals(acknowledgementType)) {
                throw new ProtocolException("DTS_OFFICIAL_JAVA_ACK_REQUIRED");
            }
            acceptDurableAcknowledgement(envelope, acknowledgement);
            inFlight = null;
            seen += 1;
        }

        if (!closed) {
            JSONObject complete = message("BATCH_COMPLETE");
            complete.put("seen", seen);
            emit(complete);
        }
    }

    private RecordEnvelope awaitRecord(long timeoutMs) {
        long deadline = System.nanoTime()
                + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        while (!closed) {
            if (consumeCloseCommandIfAvailable()) {
                return null;
            }
            throwRecordedFailureIfPresent();
            long remainingNanos = deadline - System.nanoTime();
            if (remainingNanos <= 0L) {
                if (timeoutMs == idleTimeoutMs && started) {
                    return null;
                }
                throw new TransportTimeoutException();
            }
            try {
                RecordEnvelope envelope = records.poll(
                        Math.min(remainingNanos,
                                TimeUnit.MILLISECONDS.toNanos(200L)),
                        TimeUnit.NANOSECONDS);
                if (envelope != null) {
                    return envelope;
                }
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new TransportTimeoutException();
            }
        }
        return null;
    }

    private boolean consumeCloseCommandIfAvailable() {
        try {
            if (!input.ready()) {
                return false;
            }
            JSONObject command = readCommand();
            if (command == null) {
                closed = true;
                closeConsumer();
                return true;
            }
            if (!"CLOSE".equals(requiredString(command, TYPE))) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_COMMAND_UNEXPECTED");
            }
            handleClose();
            return true;
        } catch (IOException error) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
    }

    private void emitEvent(RecordEnvelope envelope) {
        DefaultUserRecord userRecord = envelope.record;
        TopicPartition topicPartition = requiredTopicPartition(userRecord);
        long offset = requiredRecordOffset(userRecord);
        byte[] payload = envelope.encodedPayload();

        JSONObject event = message("EVENT");
        event.put("topic", topicPartition.topic());
        event.put("partition", topicPartition.partition());
        event.put("offset", offset);
        event.put("source_timestamp", requiredSourceTimestamp(userRecord));
        event.put("payload_base64", Base64.getEncoder().encodeToString(payload));
        emit(event);
    }

    /**
     * Encode with the model bundled in the pinned official SDK.
     *
     * <p>The SDK 1.4 generated {@link EmptyObject} enum lives under the
     * {@code com.aliyun} package while its Avro schema retains the historic
     * {@code com.alibaba} namespace. The generated encoder therefore cannot
     * resolve only that enum when it is stored in an image union. Replacing
     * that value with the equivalent Avro enum symbol preserves the official
     * schema and lets {@link Record#toByteBuffer()} own all wire encoding.
     */
    static byte[] encodeOfficialRecord(Record sourceRecord) {
        final Record avroRecord;
        final ByteBuffer encoded;
        final byte[] expectedFingerprint;
        try {
            avroRecord = normalizeOfficialEmptyObjects(sourceRecord);
            encoded = avroRecord.toByteBuffer().duplicate();
            expectedFingerprint = SchemaNormalization.parsingFingerprint(
                    "CRC-64-AVRO", Record.getClassSchema());
        } catch (IOException error) {
            safeLog("DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED", error);
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
        } catch (NoSuchAlgorithmException error) {
            safeLog("DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED", error);
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
        } catch (RuntimeException error) {
            safeLog("DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED", error);
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
        }

        if (encoded.remaining() <= AVRO_SINGLE_OBJECT_HEADER_BYTES
                || (encoded.get() & 0xff) != 0xc3
                || (encoded.get() & 0xff) != 0x01
                || expectedFingerprint.length != Long.BYTES) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
        }

        for (byte expectedByte : expectedFingerprint) {
            if (encoded.get() != expectedByte) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
            }
        }
        if (!encoded.hasRemaining()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ENCODING_FAILED");
        }

        byte[] payload = new byte[encoded.remaining()];
        encoded.get(payload);
        return payload;
    }

    private static Record normalizeOfficialEmptyObjects(Record sourceRecord) {
        Object beforeImages = normalizeOfficialEmptyObjectList(
                sourceRecord.getBeforeImages());
        Object afterImages = normalizeOfficialEmptyObjectList(
                sourceRecord.getAfterImages());
        if (beforeImages == sourceRecord.getBeforeImages()
                && afterImages == sourceRecord.getAfterImages()) {
            return sourceRecord;
        }
        return new Record(
                sourceRecord.getVersion(),
                sourceRecord.getId(),
                sourceRecord.getSourceTimestamp(),
                sourceRecord.getSourcePosition(),
                sourceRecord.getSafeSourcePosition(),
                sourceRecord.getSourceTxid(),
                sourceRecord.getSource(),
                sourceRecord.getOperation(),
                sourceRecord.getObjectName(),
                sourceRecord.getProcessTimestamps(),
                sourceRecord.getTags(),
                sourceRecord.getFields(),
                beforeImages,
                afterImages);
    }

    private static Object normalizeOfficialEmptyObjectList(Object value) {
        if (!(value instanceof List<?>)) {
            return value;
        }
        List<?> items = (List<?>) value;
        List<Object> normalized = null;
        for (int index = 0; index < items.size(); index += 1) {
            Object item = items.get(index);
            if (!(item instanceof EmptyObject)) {
                if (normalized != null) {
                    normalized.add(item);
                }
                continue;
            }
            if (normalized == null) {
                normalized = new ArrayList<Object>(items.size());
                normalized.addAll(items.subList(0, index));
            }
            EmptyObject emptyObject = (EmptyObject) item;
            normalized.add(new GenericData.EnumSymbol(
                    EmptyObject.getClassSchema(), emptyObject.name()));
        }
        return normalized == null ? value : normalized;
    }

    private void acceptDurableAcknowledgement(
            RecordEnvelope envelope, JSONObject acknowledgement) {
        DefaultUserRecord record = envelope.record;
        long recordOffset = requiredRecordOffset(record);
        long acknowledgedOffset = requiredNonNegativeLong(
                acknowledgement, "offset");
        long nextOffset = requiredNonNegativeLong(
                acknowledgement, "next_offset");
        long sourceTimestamp = requiredNonNegativeLong(
                acknowledgement, "source_timestamp");
        String action = requiredString(acknowledgement, "checkpoint_action");
        if (acknowledgedOffset != recordOffset
                || sourceTimestamp != requiredSourceTimestamp(record)) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_ACK_OFFSET_MISMATCH");
        }
        if (ACTION_ADVANCE.equals(action)) {
            if (recordOffset == Long.MAX_VALUE
                    || nextOffset != recordOffset + 1L) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_ACK_OFFSET_MISMATCH");
            }
        } else if (ACTION_REPLAY.equals(action)) {
            if (nextOffset <= recordOffset) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_ACK_OFFSET_MISMATCH");
            }
        } else {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_CHECKPOINT_ACTION_INVALID");
        }

        envelope.decide(action, sourceTimestamp);
        envelope.awaitApplied();
        throwFailure(envelope.failure);
        throwRecordedFailureIfPresent();

        JSONObject accepted = message("SDK_CHECKPOINT_ACCEPTED");
        accepted.put("offset", acknowledgedOffset);
        accepted.put("next_offset", nextOffset);
        accepted.put("checkpoint_action", action);
        emit(accepted);
    }

    private void handleClose() {
        if (closed) {
            return;
        }
        closed = true;
        cancelPendingRecords();
        closeConsumer();
        emit(message("CLOSED"));
        safeLog("DTS_OFFICIAL_JAVA_CLOSED", null);
    }

    private void closeConsumer() {
        cancelPendingRecords();
        DefaultDTSConsumer current = consumer;
        consumer = null;
        if (current != null) {
            try {
                current.close();
            } catch (Throwable error) {
                safeLog("DTS_OFFICIAL_JAVA_CLOSE_FAILED", error);
            }
        }
        Thread currentThread = consumerThread;
        consumerThread = null;
        if (currentThread != null && currentThread != Thread.currentThread()) {
            currentThread.interrupt();
            try {
                currentThread.join(3000L);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
            if (currentThread.isAlive()) {
                safeLog("DTS_OFFICIAL_JAVA_CLOSE_INCOMPLETE", null);
            }
        }
        records.clear();
        firstRecord = null;
        inFlight = null;
    }

    private void cancelPendingRecords() {
        RecordEnvelope current = inFlight;
        if (current != null) {
            current.cancel();
        }
        current = firstRecord;
        if (current != null) {
            current.cancel();
        }
        for (RecordEnvelope envelope : records) {
            envelope.cancel();
        }
    }

    private void requireStarted() {
        if (!started || consumer == null || closed) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_TRANSPORT_NOT_READY");
        }
        throwRecordedFailureIfPresent();
    }

    private void throwRecordedFailureIfPresent() {
        Throwable failure = listenerFailure;
        if (failure == null) {
            failure = sdkFailure;
        }
        throwFailure(failure);
    }

    private static void throwFailure(Throwable failure) {
        if (failure == null) {
            return;
        }
        Throwable current = failure;
        for (int depth = 0; depth < 8 && current != null; depth += 1) {
            if (current instanceof AuthenticationException) {
                throw (AuthenticationException) current;
            }
            if (current instanceof AuthorizationException) {
                throw (AuthorizationException) current;
            }
            if (current instanceof RetriableException) {
                throw (RetriableException) current;
            }
            current = current.getCause();
        }
        if (failure instanceof ProtocolException) {
            throw (ProtocolException) failure;
        }
        throw new SdkFailureException(failure);
    }

    private static TopicPartition requiredTopicPartition(
            DefaultUserRecord record) {
        TopicPartition value = record.getTopicPartition();
        if (value == null || value.topic() == null || value.topic().isEmpty()
                || value.partition() < 0) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RECORD_INVALID");
        }
        return value;
    }

    private static long requiredRecordOffset(DefaultUserRecord record) {
        long value = record.getOffset();
        if (value < 0L) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RECORD_INVALID");
        }
        return value;
    }

    private static long requiredSourceTimestamp(DefaultUserRecord record) {
        long value = record.getSourceTimestamp();
        if (value < 0L) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_RECORD_INVALID");
        }
        return value;
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
        if (result < 0L) {
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
            // The parent process classifies EOF if stdout is already gone.
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

    private static final class RecordEnvelope {
        private final DefaultUserRecord record;
        private final CountDownLatch decisionReady = new CountDownLatch(1);
        private final CountDownLatch decisionApplied = new CountDownLatch(1);
        private volatile String action;
        private volatile long sourceTimestamp;
        private volatile Throwable failure;
        private byte[] payload;

        RecordEnvelope(DefaultUserRecord record) {
            this.record = record;
        }

        byte[] encodedPayload() {
            if (payload == null) {
                DefaultUserRecord userRecord = record;
                Record avroRecord = userRecord.getAvroRecord();
                if (avroRecord == null) {
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID");
                }
                payload = encodeOfficialRecord(avroRecord);
            }
            return payload;
        }

        void decide(String requestedAction, long requestedSourceTimestamp) {
            action = requestedAction;
            sourceTimestamp = requestedSourceTimestamp;
            decisionReady.countDown();
        }

        void awaitDecision() throws InterruptedException {
            decisionReady.await();
            try {
                if (ACTION_ADVANCE.equals(action)) {
                    record.commit(Long.toString(sourceTimestamp));
                }
            } catch (RuntimeException error) {
                failure = error;
                throw error;
            } catch (Error error) {
                failure = error;
                throw error;
            } finally {
                decisionApplied.countDown();
            }
        }

        void awaitApplied() {
            try {
                if (!decisionApplied.await(
                        SDK_START_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                    throw new TransportTimeoutException();
                }
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new TransportTimeoutException();
            }
        }

        void cancel() {
            action = "CLOSE";
            decisionReady.countDown();
            decisionApplied.countDown();
        }

        void fail(Throwable error) {
            failure = error;
            decisionReady.countDown();
            decisionApplied.countDown();
        }
    }

    private static final class ProtocolException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        private final String errorCode;

        ProtocolException(String errorCode) {
            super(errorCode);
            this.errorCode = errorCode;
        }
    }

    private static final class TransportTimeoutException
            extends RetriableException {
        private static final long serialVersionUID = 1L;

        TransportTimeoutException() {
            super("DTS_OFFICIAL_JAVA_TRANSPORT_TIMEOUT");
        }
    }

    private static final class SdkFailureException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        SdkFailureException(Throwable cause) {
            super("DTS_OFFICIAL_JAVA_SDK_FAILED", cause);
        }
    }

    private static final class SdkTerminatedException
            extends RetriableException {
        private static final long serialVersionUID = 1L;

        SdkTerminatedException() {
            super("DTS_OFFICIAL_JAVA_SDK_TERMINATED");
        }
    }
}
