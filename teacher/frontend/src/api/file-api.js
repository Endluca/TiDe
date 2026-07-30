import { apiRequest, newCommandKey } from "./api-client";

export async function sha256File(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function uploadTaskFile({ taskInstanceId, stepKey, file }) {
  const sha256 = await sha256File(file);
  const intentKey = newCommandKey("upload-intent");
  const intent = await apiRequest("/api/v1/files/upload-intents", {
    method: "POST",
    headers: { "Idempotency-Key": intentKey },
    body: {
      taskInstanceId,
      stepKey,
      filename: file.name || "evidence.jpg",
      mimeType: file.type,
      sizeBytes: file.size,
      sha256,
    },
  });
  if (intent.uploadMethod === "OSS_POST_FORM") {
    const formData = new FormData();
    Object.entries(intent.requiredFields || {}).forEach(([key, value]) => {
      formData.append(key, value);
    });
    formData.append("file", file, file.name || "evidence.jpg");
    const response = await fetch(intent.uploadUrl, {
      method: "POST",
      body: formData,
      signal: AbortSignal.timeout(120_000),
    });
    if (!response.ok) throw new Error(`OSS_UPLOAD_${response.status}`);
  } else {
    await apiRequest(intent.uploadUrl, {
      method: "PUT",
      headers: intent.requiredHeaders,
      body: file,
      timeoutMs: 120_000,
    });
  }
  const completeKey = newCommandKey("upload-complete");
  const readyFile = await apiRequest(`/api/v1/files/${intent.fileId}/complete`, {
    method: "POST",
    headers: { "Idempotency-Key": completeKey },
    body: { sha256 },
  });
  return readyFile;
}

export const downloadTaskFile = (fileId, signal) =>
  apiRequest(`/api/v1/files/${fileId}/content`, { responseType: "blob", signal });
