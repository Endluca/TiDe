import { apiRequest, newCommandKey } from "./api-client";

export const getTeacherProfile = (signal) => apiRequest("/api/v1/me/profile", { signal });
export const getG01Review = (signal) => apiRequest("/api/v1/me/g01-review", { signal });
export const getTideSummary = (signal) =>
  apiRequest("/api/v1/me/tide-summary", { signal, cache: "no-store" });
export function getCourses(signal, { page = 1, pageSize = 20, search = "" } = {}) {
  const query = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
  });
  if (search.trim()) query.set("search", search.trim());
  return apiRequest(
    `/api/v1/me/courses?${query.toString()}`,
    { signal },
  );
}

export async function getAllCourses(signal) {
  const pageSize = 100;
  const first = await getCourses(signal, { page: 1, pageSize });
  const pageCount = Math.ceil((first.totalCount || 0) / pageSize);
  if (pageCount <= 1) return first;
  const remaining = [];
  for (let page = 2; page <= pageCount; page += 1) {
    remaining.push(await getCourses(signal, { page, pageSize }));
  }
  return {
    ...first,
    items: [
      ...(first.items || []),
      ...remaining.flatMap((page) => page.items || []),
    ],
  };
}
export function getNotifications(signal, { cursor, filter = "ALL", limit = 30 } = {}) {
  const query = new URLSearchParams({ filter, limit: String(limit) });
  if (cursor) query.set("cursor", cursor);
  return apiRequest(`/api/v1/me/notifications?${query.toString()}`, { signal });
}

export function markNotificationRead(sourceNotificationId) {
  return apiRequest(`/api/v1/me/notifications/${encodeURIComponent(sourceNotificationId)}/read`, {
    method: "POST",
    headers: { "Idempotency-Key": newCommandKey("notification-read") },
  });
}

export function markNotificationClicked(sourceNotificationId) {
  return apiRequest(`/api/v1/me/notifications/${encodeURIComponent(sourceNotificationId)}/click`, {
    method: "POST",
    headers: { "Idempotency-Key": newCommandKey("notification-click") },
  });
}
