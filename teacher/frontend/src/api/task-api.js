import { apiRequest, newCommandKey } from "./api-client";

export const listTasks = (signal) => apiRequest("/api/v1/tasks", { signal });
export const getTask = (taskInstanceId, signal) => apiRequest(`/api/v1/tasks/${taskInstanceId}`, { signal });
export const getTaskValidation = (taskInstanceId, signal) => apiRequest(`/api/v1/tasks/${taskInstanceId}/validation`, { signal });

function taskMutation(taskInstanceId, action, stateVersion, body = {}) {
  const commandId = newCommandKey(action);
  return apiRequest(`/api/v1/tasks/${taskInstanceId}/${action}`, {
    method: action === "progress" ? "PUT" : "POST",
    headers: { "Idempotency-Key": commandId },
    body: { commandId, expectedStateVersion: stateVersion, ...body },
  });
}

export const startTask = (taskInstanceId, stateVersion) => taskMutation(taskInstanceId, "start", stateVersion);
export const viewTask = (taskInstanceId, stateVersion) => taskMutation(taskInstanceId, "view", stateVersion);
export const saveTaskProgress = (taskInstanceId, stateVersion, stepKey, percent, progress) =>
  taskMutation(taskInstanceId, "progress", stateVersion, { stepKey, percent, progress });
export const saveVideoHeartbeat = (taskInstanceId, stepKey, positionSeconds, playbackRate = 1) =>
  apiRequest(`/api/v1/tasks/${taskInstanceId}/video-heartbeat`, {
    method: "PUT",
    body: { stepKey, positionSeconds, playbackRate },
  });
export const submitTask = (taskInstanceId, stateVersion, outputs) =>
  taskMutation(taskInstanceId, "submissions", stateVersion, { attemptId: crypto.randomUUID(), outputs });
export const retryTask = (taskInstanceId, stateVersion, reasonCode) =>
  taskMutation(taskInstanceId, "retry", stateVersion, reasonCode ? { reasonCode } : {});
