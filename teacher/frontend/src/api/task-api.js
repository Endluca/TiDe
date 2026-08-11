import { apiRequest, newCommandKey } from "./api-client";

export const listTasks = (signal) => apiRequest("/api/v1/tasks", { signal });
export const getTask = (taskInstanceId, signal) => apiRequest(`/api/v1/tasks/${taskInstanceId}`, { signal });
export const getTaskDocumentContent = (taskInstanceId, signal) =>
  apiRequest(`/api/v1/tasks/${taskInstanceId}/document-content`, { signal });
export const getTaskDocumentAsset = (taskInstanceId, assetKey, sha256, signal) =>
  apiRequest(
    `/api/v1/tasks/${encodeURIComponent(taskInstanceId)}/document-content/assets/${encodeURIComponent(assetKey)}?v=${encodeURIComponent(sha256)}`,
    { signal, responseType: "blob" },
  );
export const getTaskValidation = (taskInstanceId, signal) => apiRequest(`/api/v1/tasks/${taskInstanceId}/validation`, { signal });

function taskMutation(taskInstanceId, action, stateVersion, body = {}, requestOptions = {}) {
  const commandId = newCommandKey(action);
  return apiRequest(`/api/v1/tasks/${taskInstanceId}/${action}`, {
    method: action === "progress" ? "PUT" : "POST",
    headers: { "Idempotency-Key": commandId },
    body: { commandId, expectedStateVersion: stateVersion, ...body },
    keepalive: requestOptions.keepalive === true,
  });
}

export const startTask = (taskInstanceId, stateVersion, requestOptions = {}) =>
  taskMutation(taskInstanceId, "start", stateVersion, {}, requestOptions);
export const viewTask = (taskInstanceId, stateVersion) => taskMutation(taskInstanceId, "view", stateVersion);
export const saveTaskProgress = (
  taskInstanceId,
  stateVersion,
  stepKey,
  percent,
  progress,
  requestOptions = {},
) => taskMutation(
  taskInstanceId,
  "progress",
  stateVersion,
  { stepKey, percent, progress },
  requestOptions,
);
export const saveVideoHeartbeat = (taskInstanceId, stepKey, positionSeconds, playbackRate = 1) =>
  apiRequest(`/api/v1/tasks/${taskInstanceId}/video-heartbeat`, {
    method: "PUT",
    body: { stepKey, positionSeconds, playbackRate },
  });
export const submitTask = (taskInstanceId, stateVersion, outputs, requestOptions = {}) =>
  taskMutation(
    taskInstanceId,
    "submissions",
    stateVersion,
    { attemptId: crypto.randomUUID(), outputs },
    requestOptions,
  );
export const retryTask = (taskInstanceId, stateVersion, reasonCode, requestOptions = {}) =>
  taskMutation(
    taskInstanceId,
    "retry",
    stateVersion,
    reasonCode ? { reasonCode } : {},
    requestOptions,
  );
