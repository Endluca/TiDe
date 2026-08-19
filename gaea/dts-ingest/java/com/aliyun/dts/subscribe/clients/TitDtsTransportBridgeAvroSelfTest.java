package com.aliyun.dts.subscribe.clients;

import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import com.aliyun.dts.subscribe.clients.formats.avro.Record;
import com.aliyun.dts.subscribe.clients.recordgenerator.AvroDeserializer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashSet;
import java.util.Set;

/** Build-time proof that the pinned official decoder and encoder round-trip. */
public final class TitDtsTransportBridgeAvroSelfTest {
    private static final String UNION_FIXTURE_BASE64 =
            "AtKMAcCyy6cNEGxzbjpmdWxsEGxzbjpmdWxsDnR4LWZ1bGwGBDE0AAIgcHVibGljLnN5bnRoZXRpYwIEAgQAAhhkYXRhYmFzZU5hbWUEZGIABBoEZjAABGYxAgRmMgQEZjMGBGY0CARmNQoEZjYMBGY3DgRmOBAEZjkSBmYxMBQGZjExFgZmMTIYAAIEW10EHAIoAjgECHV0ZjgKaGVsbG8GCDEuMjMKBAgAAAAAAAD4PwoECsCyy6cN9gEMAtQfAhACIgIYAkQCcAKqDA4C1B8CEAIiAhgCRAJwAqoMBlVUQxAKUE9JTlQGZ2VvEgZXS1QUUE9JTlQoMSAyKRAIQkxPQgZvYmoSCEpTT04Ee30YAhgAAAA=";

    private TitDtsTransportBridgeAvroSelfTest() {
    }

    public static void main(String[] args) {
        byte[] expected = Base64.getDecoder().decode(UNION_FIXTURE_BASE64);
        Record officialRecord = new AvroDeserializer().deserialize(expected);
        if (officialRecord == null) {
            throw new AssertionError("official Avro decoder returned null");
        }
        byte[] actual = TitDtsTransportBridge.encodeOfficialRecord(
                officialRecord);
        if (!Arrays.equals(expected, actual)) {
            throw new AssertionError(
                    "official Avro decoder and encoder did not round-trip");
        }
        JSONObject normalized = TitDtsTransportBridge.normalizeOfficialRecord(
                officialRecord, null);
        if (normalized.getLongValue("id") != 9001L
                || !"public.synthetic".equals(
                        normalized.getString("objectName"))) {
            throw new AssertionError("structured record identity changed");
        }
        JSONArray normalizedFields = normalized.getJSONArray("fields");
        JSONArray normalizedImages = normalized.getJSONArray("afterImages");
        if (normalizedFields == null
                || normalizedFields.size() != 13
                || !"f0".equals(normalizedFields.getString(0))
                || normalizedImages == null
                || normalizedImages.size() != 14
                || !"8".equals(
                        normalizedImages.getJSONObject(0).getString("value"))
                || !"hello".equals(
                        normalizedImages.getJSONObject(1).getString("value"))
                || !"NONE".equals(normalizedImages.getString(11))
                || !"NULL".equals(normalizedImages.getString(12))
                || normalizedImages.get(13) != null) {
            throw new AssertionError("image union normalization changed");
        }

        ArrayList<Object> alignedImages = new ArrayList<Object>(
                ((java.util.List<?>) officialRecord.getAfterImages())
                        .subList(0, 13));
        Record alignedRecord = new Record(
                officialRecord.getVersion(),
                officialRecord.getId(),
                officialRecord.getSourceTimestamp(),
                officialRecord.getSourcePosition(),
                officialRecord.getSafeSourcePosition(),
                officialRecord.getSourceTxid(),
                officialRecord.getSource(),
                officialRecord.getOperation(),
                officialRecord.getObjectName(),
                officialRecord.getProcessTimestamps(),
                officialRecord.getTags(),
                officialRecord.getFields(),
                null,
                alignedImages);
        Set<String> allowedFields = new HashSet<String>();
        allowedFields.add("f0");
        allowedFields.add("f4");
        JSONObject filtered = TitDtsTransportBridge.normalizeOfficialRecord(
                alignedRecord, allowedFields);
        JSONArray filteredFields = filtered.getJSONArray("fields");
        if (filteredFields.size() != 2
                || !"f0".equals(filteredFields.getString(0))
                || !"f4".equals(filteredFields.getString(1))
                || filtered.getJSONArray("afterImages").size() != 2) {
            throw new AssertionError("source field whitelist changed");
        }
        Set<String> supportedTables = new HashSet<String>();
        supportedTables.add("synthetic");
        Set<String> dataOperations = new HashSet<String>();
        dataOperations.add("INSERT");
        dataOperations.add("UPDATE");
        dataOperations.add("DELETE");
        Set<String> controlOperations = new HashSet<String>();
        controlOperations.add("HEARTBEAT");
        if (TitDtsTransportBridge.isLightweightRecord(
                officialRecord,
                supportedTables,
                dataOperations,
                controlOperations)) {
            throw new AssertionError("supported table was prefiltered");
        }
        supportedTables.clear();
        supportedTables.add("dom_teacher");
        if (!TitDtsTransportBridge.isLightweightRecord(
                officialRecord,
                supportedTables,
                dataOperations,
                controlOperations)) {
            throw new AssertionError("unsupported table was not prefiltered");
        }
        if (!"synthetic".equals(
                TitDtsTransportBridge.stripIdentifier("[synthetic"))) {
            throw new AssertionError(
                    "identifier normalization diverged from Python");
        }
    }
}
