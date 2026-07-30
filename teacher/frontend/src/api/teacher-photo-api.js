import { apiRequest, newCommandKey } from './api-client';

export function submitTeacherPhoto(taskInstanceId, photo) {
  const form = new FormData();
  form.append('photo', photo, photo.name || 'teacher-photo.jpg');
  return apiRequest(`/api/v1/tasks/${encodeURIComponent(taskInstanceId)}/teacher-photo`, {
    method: 'POST',
    headers: { 'Idempotency-Key': newCommandKey('teacher-photo') },
    body: form,
  });
}

export function getTeacherPhoto(taskInstanceId, signal) {
  return apiRequest(`/api/v1/tasks/${encodeURIComponent(taskInstanceId)}/teacher-photo`, { signal });
}

export function downloadTeacherPhoto(taskInstanceId, signal) {
  return apiRequest(`/api/v1/tasks/${encodeURIComponent(taskInstanceId)}/teacher-photo/content`, {
    responseType: 'blob',
    signal,
  });
}
