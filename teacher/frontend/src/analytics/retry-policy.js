const RETRY_DELAYS = [1_000, 5_000, 30_000];

export function isPermanentAnalyticsFailure(status) {
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

export function analyticsRetryDelay(attempt, retryAfterSeconds = null) {
  const backoff =
    RETRY_DELAYS[Math.min(Math.max(0, attempt - 1), RETRY_DELAYS.length - 1)];
  const serverDelay = Number.isFinite(Number(retryAfterSeconds))
    ? Math.max(0, Number(retryAfterSeconds) * 1_000)
    : 0;
  return Math.max(backoff, serverDelay);
}
