package com.aliyun.dts.subscribe.clients;

import com.aliyun.dts.subscribe.clients.formats.avro.Record;
import com.aliyun.dts.subscribe.clients.recordgenerator.AvroDeserializer;
import java.util.Arrays;
import java.util.Base64;

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
    }
}
