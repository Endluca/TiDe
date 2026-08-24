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
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.apache.avro.SchemaNormalization;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;
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
    private static final int MAX_BATCH_MESSAGES = 2048;
    private static final int MAX_BATCH_PAYLOAD_BYTES = 8 * 1024 * 1024;
    private static final long BATCH_LINGER_MS = 50L;
    private static final int AVRO_SINGLE_OBJECT_HEADER_BYTES = 10;
    private static final long DEFAULT_IDLE_TIMEOUT_MS = 10000L;
    private static final long SDK_START_TIMEOUT_MS = 305000L;
    private static final int PROTOCOL_VERSION = 2;
    private static final int MAX_NORMALIZATION_DEPTH = 32;

    private final BufferedReader input = new BufferedReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8));
    private final PrintWriter output = new PrintWriter(
            new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true);
    private final ArrayBlockingQueue<RecordEnvelope> records =
            new ArrayBlockingQueue<RecordEnvelope>(MAX_BATCH_MESSAGES);

    private volatile Throwable sdkFailure;
    private volatile Throwable listenerFailure;
    private volatile boolean closed;
    private DefaultDTSConsumer consumer;
    private Thread consumerThread;
    private RecordEnvelope firstRecord;
    private RecordEnvelope deferredRecord;
    private List<RecordEnvelope> inFlightBatch;
    private Set<String> supportedTableNames;
    private Set<String> dataOperations;
    private Set<String> controlOperations;
    private Map<String, Set<String>> sourceFieldWhitelist;
    private boolean lightweightPrefilterEnabled;
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
        List<String> requestedSupportedTableNames = requiredStringArray(
                command, "supported_table_names");
        List<String> requestedDataOperations = requiredStringArray(
                command, "data_operations");
        List<String> requestedControlOperations = requiredStringArray(
                command, "control_operations");
        lightweightPrefilterEnabled = requiredBoolean(
                command, "lightweight_prefilter_enabled");
        if (requiredNonNegativeInt(command, "protocol_version")
                != PROTOCOL_VERSION) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_PROTOCOL_VERSION_MISMATCH");
        }
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
        supportedTableNames = new HashSet<String>();
        for (String tableName : requestedSupportedTableNames) {
            supportedTableNames.add(tableName.toLowerCase(Locale.ROOT));
        }
        sourceFieldWhitelist = requiredStringSetMap(
                command,
                "source_field_whitelist",
                supportedTableNames);
        dataOperations = normalizedUppercaseSet(requestedDataOperations);
        controlOperations = normalizedUppercaseSet(requestedControlOperations);

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
                    }
                } catch (Throwable error) {
                    sdkFailure = error;
                }
            }
        }, "tit-dts-official-consumer");
        consumerThread.setDaemon(true);
        consumerThread.start();

        firstRecord = awaitRecord(SDK_START_TIMEOUT_MS);
        DefaultUserRecord record = firstRecord.record;
        // Materialize the first official SDK record before READY so a
        // normalization incompatibility is a startup failure, not a false
        // healthy transition followed by a deterministic POLL failure.
        firstRecord.structuredRecordJson();
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
        ready.put("protocol_version", PROTOCOL_VERSION);
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
        try {
            Record avroRecord = record.getAvroRecord();
            if (avroRecord == null) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID");
            }
            String tableName = recordTableName(avroRecord);
            boolean lightweight = lightweightPrefilterEnabled
                    && isLightweightRecord(
                            avroRecord,
                            supportedTableNames,
                            dataOperations,
                            controlOperations);
            Set<String> allowedFields = null;
            if (!lightweight && lightweightPrefilterEnabled
                    && tableName != null) {
                allowedFields = sourceFieldWhitelist.get(
                        tableName.toLowerCase(Locale.ROOT));
            }
            RecordEnvelope envelope = new RecordEnvelope(
                    record,
                    lightweight,
                    allowedFields);
            while (!closed && !records.offer(envelope, 200L, TimeUnit.MILLISECONDS)) {
                // Bound retained official records while allowing one database
                // transaction to durably acknowledge a complete protocol batch.
            }
            if (closed) {
                return;
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            listenerFailure = error;
        } catch (RuntimeException error) {
            listenerFailure = error;
            throw error;
        } catch (Error error) {
            listenerFailure = error;
            throw error;
        }
    }

    private void handlePoll(JSONObject command) throws IOException {
        requireStarted();
        if (inFlightBatch != null) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_ALREADY_IN_FLIGHT");
        }
        int maxMessages = requiredPositiveInt(command, "max_messages");
        if (maxMessages > MAX_BATCH_MESSAGES) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_MAX_MESSAGES_INVALID");
        }

        List<RecordEnvelope> batch = new ArrayList<RecordEnvelope>(maxMessages);
        int batchBytes = 0;
        while (batch.size() < maxMessages && !closed) {
            long waitMs = batch.isEmpty() ? idleTimeoutMs : BATCH_LINGER_MS;
            RecordEnvelope envelope = nextRecord(waitMs);
            if (envelope == null) {
                break;
            }
            int payloadBytes = envelope.protocolPayloadBytes();
            if (payloadBytes > MAX_BATCH_PAYLOAD_BYTES) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_TOO_LARGE");
            }
            if (!batch.isEmpty()
                    && batchBytes > MAX_BATCH_PAYLOAD_BYTES - payloadBytes) {
                deferredRecord = envelope;
                break;
            }
            batch.add(envelope);
            batchBytes += payloadBytes;
        }

        if (closed) {
            return;
        }
        inFlightBatch = batch;
        for (RecordEnvelope envelope : batch) {
            emitEvent(envelope);
        }
        JSONObject complete = message("BATCH_COMPLETE");
        complete.put("seen", batch.size());
        complete.put("batch_bytes", batchBytes);
        long normalizationElapsedNanos = 0L;
        for (RecordEnvelope envelope : batch) {
            normalizationElapsedNanos += envelope.normalizationElapsedNanos();
        }
        complete.put(
                "transport_normalize_elapsed_ms",
                TimeUnit.NANOSECONDS.toMillis(normalizationElapsedNanos));
        emit(complete);
        if (batch.isEmpty()) {
            inFlightBatch = null;
            return;
        }

        JSONObject acknowledgement = readCommand();
        if (acknowledgement == null) {
            throw new ProtocolException("DTS_OFFICIAL_JAVA_ACK_REQUIRED");
        }
        String acknowledgementType = requiredString(acknowledgement, TYPE);
        if ("CLOSE".equals(acknowledgementType)) {
            handleClose();
            return;
        }
        if (!"DURABLE_ACK_BATCH".equals(acknowledgementType)) {
            throw new ProtocolException("DTS_OFFICIAL_JAVA_ACK_REQUIRED");
        }
        acceptDurableAcknowledgementBatch(batch, acknowledgement);
        inFlightBatch = null;
    }

    private RecordEnvelope nextRecord(long timeoutMs) {
        if (firstRecord != null) {
            RecordEnvelope result = firstRecord;
            firstRecord = null;
            return result;
        }
        if (deferredRecord != null) {
            RecordEnvelope result = deferredRecord;
            deferredRecord = null;
            return result;
        }
        return awaitRecord(timeoutMs, true);
    }

    private RecordEnvelope awaitRecord(long timeoutMs) {
        return awaitRecord(timeoutMs, false);
    }

    private RecordEnvelope awaitRecord(long timeoutMs, boolean allowEmpty) {
        long deadline = System.nanoTime()
                + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        while (!closed) {
            if (consumeCloseCommandIfAvailable()) {
                return null;
            }
            throwRecordedFailureIfPresent();
            long remainingNanos = deadline - System.nanoTime();
            if (remainingNanos <= 0L) {
                if (allowEmpty && started) {
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

        JSONObject event = message("EVENT");
        event.put("topic", topicPartition.topic());
        event.put("partition", topicPartition.partition());
        event.put("offset", offset);
        event.put("source_timestamp", requiredSourceTimestamp(userRecord));
        if (envelope.lightweight) {
            event.put("lightweight", true);
            emit(event);
        } else {
            String eventWithoutRecord = event.toJSONString();
            String recordJson = envelope.structuredRecordJson();
            String line = eventWithoutRecord.substring(
                    0, eventWithoutRecord.length() - 1)
                    + ",\"record_bytes\":"
                    + envelope.protocolPayloadBytes()
                    + ",\"record\":"
                    + recordJson
                    + "}";
            emitLine(line);
        }
    }

    static boolean isLightweightRecord(
            Record sourceRecord,
            Set<String> supportedTables,
            Set<String> dataOperationNames,
            Set<String> controlOperationNames) {
        String operation = String.valueOf(sourceRecord.getOperation())
                .trim().toUpperCase(Locale.ROOT);
        if (controlOperationNames.contains(operation)) {
            return true;
        }
        if (!dataOperationNames.contains(operation)) {
            return false;
        }
        String tableName = recordTableName(sourceRecord);
        return tableName != null
                && !supportedTables.contains(tableName.toLowerCase(Locale.ROOT));
    }

    private static String recordTableName(Record sourceRecord) {
        Object rawTags = sourceRecord.getTags();
        if (rawTags instanceof Map<?, ?>) {
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) rawTags).entrySet()) {
                String key = String.valueOf(entry.getKey()).trim();
                if ("tablename".equalsIgnoreCase(key)
                        || "table".equalsIgnoreCase(key)) {
                    String tagged = stripIdentifier(
                            String.valueOf(entry.getValue()));
                    if (!tagged.isEmpty()) {
                        return tagged;
                    }
                }
            }
        }
        Object rawObjectName = sourceRecord.getObjectName();
        if (rawObjectName == null) {
            return null;
        }
        String objectName = String.valueOf(rawObjectName).trim();
        if (objectName.isEmpty()) {
            return null;
        }
        String[] parts = objectName.replace('/', '.').split("\\.");
        for (int index = parts.length - 1; index >= 0; index -= 1) {
            String part = stripIdentifier(parts[index]);
            if (!part.isEmpty()) {
                return part;
            }
        }
        return null;
    }

    static String stripIdentifier(String value) {
        String result = value == null ? "" : value.trim();
        int start = 0;
        int end = result.length();
        while (start < end && isIdentifierWrapper(result.charAt(start))) {
            start += 1;
        }
        while (end > start && isIdentifierWrapper(result.charAt(end - 1))) {
            end -= 1;
        }
        return result.substring(start, end);
    }

    private static boolean isIdentifierWrapper(char value) {
        return value == '`'
                || value == '"'
                || value == '['
                || value == ']';
    }

    private static Set<String> normalizedUppercaseSet(List<String> values) {
        Set<String> result = new HashSet<String>();
        for (String value : values) {
            result.add(value.toUpperCase(Locale.ROOT));
        }
        return result;
    }

    /**
     * Convert the already-decoded official SDK Record into the only structure
     * Python needs for validation, privacy protection and durable projection.
     * No Avro encoding is performed on the runtime path.
     */
    static JSONObject normalizeOfficialRecord(
            Record sourceRecord, Set<String> allowedFields) {
        JSONObject result = new JSONObject(true);
        result.put("id", normalizeAvroValue(sourceRecord.getId(), 0));
        result.put(
                "sourceTimestamp",
                normalizeAvroValue(sourceRecord.getSourceTimestamp(), 0));
        result.put(
                "sourcePosition",
                normalizeAvroValue(sourceRecord.getSourcePosition(), 0));
        result.put(
                "safeSourcePosition",
                normalizeAvroValue(sourceRecord.getSafeSourcePosition(), 0));
        result.put(
                "sourceTxid",
                normalizeAvroValue(sourceRecord.getSourceTxid(), 0));
        result.put(
                "operation",
                normalizeAvroValue(sourceRecord.getOperation(), 0));
        result.put(
                "objectName",
                normalizeAvroValue(sourceRecord.getObjectName(), 0));
        result.put("tags", normalizeAvroValue(sourceRecord.getTags(), 0));

        Object rawFields = sourceRecord.getFields();
        Object rawBeforeImages = sourceRecord.getBeforeImages();
        Object rawAfterImages = sourceRecord.getAfterImages();
        JSONArray fieldNames = normalizedFieldNames(rawFields);
        JSONObject fieldTypeNumbers = normalizedFieldTypeNumbers(rawFields);
        if (allowedFields != null
                && fieldNames != null
                && imagesMatchFields(rawBeforeImages, fieldNames.size())
                && imagesMatchFields(rawAfterImages, fieldNames.size())) {
            JSONArray selectedFields = new JSONArray();
            List<Integer> selectedIndexes = new ArrayList<Integer>();
            for (int index = 0; index < fieldNames.size(); index += 1) {
                String fieldName = fieldNames.getString(index);
                if (allowedFields.contains(fieldName)) {
                    selectedFields.add(fieldName);
                    selectedIndexes.add(Integer.valueOf(index));
                }
            }
            result.put("fields", selectedFields);
            result.put(
                    "fieldTypeNumbers",
                    selectFieldTypeNumbers(fieldTypeNumbers, selectedFields));
            result.put(
                    "beforeImages",
                    normalizeSelectedImages(rawBeforeImages, selectedIndexes));
            result.put(
                    "afterImages",
                    normalizeSelectedImages(rawAfterImages, selectedIndexes));
        } else {
            result.put(
                    "fields",
                    fieldNames == null
                            ? normalizeAvroValue(rawFields, 0)
                            : fieldNames);
            result.put("fieldTypeNumbers", fieldTypeNumbers);
            result.put(
                    "beforeImages",
                    normalizeAvroValue(rawBeforeImages, 0));
            result.put(
                    "afterImages",
                    normalizeAvroValue(rawAfterImages, 0));
        }
        return result;
    }

    private static JSONObject selectFieldTypeNumbers(
            JSONObject allTypeNumbers, JSONArray selectedFields) {
        JSONObject selected = new JSONObject();
        if (allTypeNumbers == null) {
            return selected;
        }
        for (Object rawName : selectedFields) {
            String fieldName = rawName == null ? null : rawName.toString();
            if (fieldName != null && allTypeNumbers.containsKey(fieldName)) {
                selected.put(fieldName, allTypeNumbers.get(fieldName));
            }
        }
        return selected;
    }

    private static JSONObject normalizedFieldTypeNumbers(Object rawFields) {
        if (!(rawFields instanceof List<?>)) {
            return null;
        }
        JSONObject result = new JSONObject();
        for (Object rawField : (List<?>) rawFields) {
            String name = fieldName(rawField);
            Object rawTypeNumber;
            if (rawField instanceof GenericRecord) {
                rawTypeNumber = ((GenericRecord) rawField).get(
                        "dataTypeNumber");
            } else if (rawField instanceof Map<?, ?>) {
                rawTypeNumber = ((Map<?, ?>) rawField).get(
                        "dataTypeNumber");
            } else {
                rawTypeNumber = null;
            }
            if (name == null || name.isEmpty() || rawTypeNumber == null) {
                continue;
            }
            Object normalized = normalizeAvroValue(rawTypeNumber, 0);
            if (normalized instanceof Number) {
                result.put(name, ((Number) normalized).intValue());
            } else {
                try {
                    result.put(name, Integer.valueOf(normalized.toString()));
                } catch (NumberFormatException exception) {
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_FIELD_TYPE_INVALID");
                }
            }
        }
        return result;
    }

    private static JSONArray normalizedFieldNames(Object rawFields) {
        if (!(rawFields instanceof List<?>)) {
            return null;
        }
        JSONArray names = new JSONArray();
        for (Object rawField : (List<?>) rawFields) {
            String fieldName = fieldName(rawField);
            if (fieldName == null || fieldName.isEmpty()) {
                return null;
            }
            names.add(fieldName);
        }
        return names;
    }

    private static String fieldName(Object rawField) {
        final Object value;
        if (rawField instanceof GenericRecord) {
            value = ((GenericRecord) rawField).get("name");
        } else if (rawField instanceof Map<?, ?>) {
            value = ((Map<?, ?>) rawField).get("name");
        } else if (rawField instanceof CharSequence) {
            value = rawField;
        } else {
            return null;
        }
        return value == null ? null : value.toString().trim();
    }

    private static boolean imagesMatchFields(Object images, int fieldCount) {
        return images == null
                || (images instanceof List<?>
                        && ((List<?>) images).size() == fieldCount);
    }

    private static Object normalizeSelectedImages(
            Object rawImages, List<Integer> selectedIndexes) {
        if (rawImages == null) {
            return null;
        }
        List<?> images = (List<?>) rawImages;
        JSONArray selected = new JSONArray();
        for (Integer selectedIndex : selectedIndexes) {
            selected.add(normalizeAvroValue(
                    images.get(selectedIndex.intValue()), 0));
        }
        return selected;
    }

    private static Object normalizeAvroValue(Object value, int depth) {
        if (depth > MAX_NORMALIZATION_DEPTH) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED");
        }
        if (value == null || value instanceof Boolean
                || value instanceof java.lang.Integer
                || value instanceof Long
                || value instanceof Short
                || value instanceof Byte) {
            return value;
        }
        if (value instanceof Float) {
            float floatValue = ((Float) value).floatValue();
            if (Float.isNaN(floatValue) || Float.isInfinite(floatValue)) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED");
            }
            return value;
        }
        if (value instanceof Double) {
            double doubleValue = ((Double) value).doubleValue();
            if (Double.isNaN(doubleValue) || Double.isInfinite(doubleValue)) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED");
            }
            return value;
        }
        if (value instanceof Number || value instanceof CharSequence
                || value instanceof Enum<?>
                || value instanceof GenericData.EnumSymbol) {
            return value instanceof CharSequence
                    || value instanceof Enum<?>
                    || value instanceof GenericData.EnumSymbol
                            ? value.toString()
                            : value;
        }
        if (value instanceof ByteBuffer) {
            ByteBuffer bytes = ((ByteBuffer) value).duplicate();
            byte[] copy = new byte[bytes.remaining()];
            bytes.get(copy);
            return new String(copy, StandardCharsets.UTF_8);
        }
        if (value instanceof byte[]) {
            return new String((byte[]) value, StandardCharsets.UTF_8);
        }
        if (value instanceof GenericRecord) {
            GenericRecord record = (GenericRecord) value;
            JSONObject normalized = new JSONObject(true);
            for (org.apache.avro.Schema.Field field
                    : record.getSchema().getFields()) {
                normalized.put(
                        field.name(),
                        normalizeAvroValue(
                                record.get(field.pos()), depth + 1));
            }
            return normalized;
        }
        if (value instanceof Map<?, ?>) {
            JSONObject normalized = new JSONObject(true);
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                normalized.put(
                        String.valueOf(entry.getKey()),
                        normalizeAvroValue(entry.getValue(), depth + 1));
            }
            return normalized;
        }
        if (value instanceof Iterable<?>) {
            JSONArray normalized = new JSONArray();
            for (Object item : (Iterable<?>) value) {
                normalized.add(normalizeAvroValue(item, depth + 1));
            }
            return normalized;
        }
        throw new ProtocolException(
                "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED");
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

    private AcknowledgementDecision validateDurableAcknowledgement(
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
        return new AcknowledgementDecision(action, sourceTimestamp);
    }

    private void acceptDurableAcknowledgementBatch(
            List<RecordEnvelope> batch, JSONObject acknowledgement) {
        JSONArray acknowledgements = acknowledgement.getJSONArray("acks");
        if (acknowledgements == null || acknowledgements.size() != batch.size()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_ACK_BATCH_COUNT_MISMATCH");
        }

        List<AcknowledgementDecision> decisions =
                new ArrayList<AcknowledgementDecision>(batch.size());
        for (int index = 0; index < batch.size(); index += 1) {
            JSONObject item = acknowledgements.getJSONObject(index);
            if (item == null) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
            }
            decisions.add(validateDurableAcknowledgement(
                    batch.get(index), item));
        }

        int advanced = 0;
        int replayed = 0;
        int lastAdvanceIndex = -1;
        // Validate the complete durable ACK before accepting any SDK
        // checkpoint. A malformed later item can therefore never partially
        // advance the official consumer. The official SDK exposes one pending
        // checkpoint slot, so only the highest contiguous ADVANCE is submitted;
        // it covers every earlier ADVANCE without racing repeated async commits.
        // REPLAY never calls commit.
        for (int index = 0; index < batch.size(); index += 1) {
            AcknowledgementDecision decision = decisions.get(index);
            if (ACTION_ADVANCE.equals(decision.action)) {
                advanced += 1;
                lastAdvanceIndex = index;
            } else {
                replayed += 1;
            }
        }
        if (lastAdvanceIndex >= 0) {
            AcknowledgementDecision lastAdvance = decisions.get(lastAdvanceIndex);
            batch.get(lastAdvanceIndex).record.commit(
                    Long.toString(lastAdvance.sourceTimestamp));
        }
        throwRecordedFailureIfPresent();

        JSONObject accepted = message("SDK_CHECKPOINTS_ACCEPTED");
        accepted.put("seen", batch.size());
        accepted.put("advanced", advanced);
        accepted.put("replayed", replayed);
        emit(accepted);
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
        closed = true;
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
        deferredRecord = null;
        inFlightBatch = null;
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

    private static Map<String, Set<String>> requiredStringSetMap(
            JSONObject object,
            String field,
            Set<String> expectedKeys) {
        JSONObject values = object.getJSONObject(field);
        if (values == null || values.isEmpty()) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        Map<String, Set<String>> result =
                new HashMap<String, Set<String>>();
        for (String rawKey : values.keySet()) {
            String key = rawKey.toLowerCase(Locale.ROOT);
            JSONArray rawItems = values.getJSONArray(rawKey);
            if (!expectedKeys.contains(key)
                    || rawItems == null
                    || rawItems.isEmpty()
                    || result.containsKey(key)) {
                throw new ProtocolException(
                        "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
            }
            Set<String> items = new HashSet<String>();
            for (Object rawItem : rawItems) {
                if (!(rawItem instanceof String)
                        || ((String) rawItem).isEmpty()) {
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
                }
                items.add((String) rawItem);
            }
            result.put(key, items);
        }
        if (!result.keySet().equals(expectedKeys)) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
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

    private static boolean requiredBoolean(JSONObject object, String field) {
        Object value = object.get(field);
        if (!(value instanceof Boolean)) {
            throw new ProtocolException(
                    "DTS_OFFICIAL_JAVA_COMMAND_INVALID");
        }
        return ((Boolean) value).booleanValue();
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
        emitLine(message.toJSONString());
    }

    private void emitLine(String line) {
        output.println(line);
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
        private final boolean lightweight;
        private final Set<String> allowedFields;
        private String structuredRecordJson;
        private int structuredRecordBytes;
        private long normalizationElapsedNanos;

        RecordEnvelope(
                DefaultUserRecord record,
                boolean lightweight,
                Set<String> allowedFields) {
            this.record = record;
            this.lightweight = lightweight;
            this.allowedFields = allowedFields;
        }

        int protocolPayloadBytes() {
            if (lightweight) {
                return 0;
            }
            structuredRecordJson();
            return structuredRecordBytes;
        }

        long normalizationElapsedNanos() {
            return normalizationElapsedNanos;
        }

        String structuredRecordJson() {
            if (structuredRecordJson == null) {
                DefaultUserRecord userRecord = record;
                Record avroRecord = userRecord.getAvroRecord();
                if (avroRecord == null) {
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_EVENT_PAYLOAD_INVALID");
                }
                long startedNanos = System.nanoTime();
                try {
                    structuredRecordJson = normalizeOfficialRecord(
                            avroRecord, allowedFields).toJSONString();
                    structuredRecordBytes = structuredRecordJson.getBytes(
                            StandardCharsets.UTF_8).length;
                } catch (RuntimeException error) {
                    safeLog(
                            "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED",
                            error);
                    throw new ProtocolException(
                            "DTS_OFFICIAL_JAVA_EVENT_NORMALIZATION_FAILED");
                } finally {
                    normalizationElapsedNanos = Math.max(
                            0L, System.nanoTime() - startedNanos);
                }
            }
            return structuredRecordJson;
        }
    }

    private static final class AcknowledgementDecision {
        private final String action;
        private final long sourceTimestamp;

        AcknowledgementDecision(String action, long sourceTimestamp) {
            this.action = action;
            this.sourceTimestamp = sourceTimestamp;
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
