import { apiRequest, newCommandKey } from './api-client';

export const getKuozhiLaunch = (taskInstanceId, signal) =>
  apiRequest(`/api/v1/tasks/${taskInstanceId}/kuozhi-launch`, {
    signal,
    cache: 'no-store',
  });

export const getKuozhiProgress = (taskInstanceId, signal) =>
  apiRequest(`/api/v1/tasks/${taskInstanceId}/kuozhi-progress`, {
    signal,
    cache: 'no-store',
  });

export const refreshKuozhiProgress = (taskInstanceId, stateVersion, signal) => {
  const commandId = newCommandKey('kuozhi-progress-refresh');
  return apiRequest(
    `/api/v1/tasks/${taskInstanceId}/kuozhi-progress/refresh`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': commandId },
      body: { commandId, expectedStateVersion: stateVersion },
      signal,
    },
  );
};
