import {
  clearSession,
  getAccessToken,
  getRefreshToken,
  saveTokenPair,
} from "./session-store";
import { getAnalyticsSessionId } from "../analytics/session";
import { createRequestSignal } from "./request-timeout";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:3000").replace(/\/$/, "");
export const DEFAULT_QUERY_TIMEOUT_MS = 15_000;
export const DEFAULT_COMMAND_TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  constructor({
    status,
    code,
    message,
    requestId = null,
    retryable = false,
    details = null,
    retryAfterSeconds = null,
  }) {
    super(message || "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.code = code || `HTTP_${status}`;
    this.requestId = requestId;
    this.retryable = retryable;
    this.details = details;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

let refreshPromise = null;
let apiFailureObserver = null;

export function setApiFailureObserver(observer) {
  apiFailureObserver = typeof observer === "function" ? observer : null;
}

function reportApiFailure(input) {
  try {
    apiFailureObserver?.(input);
  } catch {
    // Observability must never change request behavior.
  }
}

function toUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

async function parseResponse(response, responseType) {
  if (response.status === 204) return null;
  if (responseType === "blob") return response.blob();
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function errorFrom(response, payload) {
  const body = payload && typeof payload === "object" ? payload : {};
  const retryAfter = response.headers.get("retry-after");
  const retryAfterSeconds = retryAfter && /^\d+$/.test(retryAfter)
    ? Number(retryAfter)
    : null;
  return new ApiError({
    status: response.status,
    code: body.code,
    message: body.message || response.statusText,
    requestId: body.requestId || response.headers.get("x-request-id"),
    retryable: body.retryable === true,
    details: body.details || null,
    retryAfterSeconds,
  });
}

async function refreshSession() {
  if (refreshPromise) return refreshPromise;
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new ApiError({ status: 401, code: "AUTH_REQUIRED", message: "Please sign in again." });

  refreshPromise = apiRequest("/api/v1/auth/refresh", {
    method: "POST",
    body: { refreshToken },
    auth: false,
    retryAuth: false,
    timeoutMs: DEFAULT_QUERY_TIMEOUT_MS,
  })
    .then((payload) => {
      saveTokenPair(payload);
      return payload;
    })
    .catch((error) => {
      clearSession();
      throw error;
    })
    .finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
}

export async function apiRequest(path, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    auth = true,
    responseType = "json",
    retryAuth = true,
    signal,
    cache,
    keepalive = false,
    timeoutMs = method === "GET"
      ? DEFAULT_QUERY_TIMEOUT_MS
      : DEFAULT_COMMAND_TIMEOUT_MS,
  } = options;
  let token = auth ? getAccessToken() : null;
  if (auth && !token && getRefreshToken()) {
    await refreshSession();
    token = getAccessToken();
  }

  const requestHeaders = new Headers(headers);
  if (!requestHeaders.has("x-tide-session-id")) {
    requestHeaders.set("x-tide-session-id", getAnalyticsSessionId());
  }
  if (auth && token) requestHeaders.set("authorization", `Bearer ${token}`);
  const isFormData = body instanceof FormData;
  const isRawBody =
    (typeof Blob !== "undefined" && body instanceof Blob) ||
    (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer);
  if (
    typeof Blob !== "undefined" &&
    body instanceof Blob &&
    body.type &&
    !requestHeaders.has("content-type")
  ) {
    requestHeaders.set("content-type", body.type);
  }
  if (
    body !== undefined &&
    !isFormData &&
    !isRawBody &&
    !requestHeaders.has("content-type")
  ) {
    requestHeaders.set("content-type", "application/json");
  }

  const requestStartedAt = performance.now();
  const requestSignal = createRequestSignal(signal, timeoutMs);
  let response;
  let payload;
  try {
    response = await fetch(toUrl(path), {
      method,
      headers: requestHeaders,
      body:
        body === undefined || isFormData || isRawBody
          ? body
          : JSON.stringify(body),
      signal: requestSignal.signal,
      cache,
      keepalive,
    });
    payload = await parseResponse(response, responseType);
  } catch (error) {
    if (requestSignal.didTimeout()) {
      reportApiFailure({
        path,
        method,
        status: 0,
        errorCode: "NETWORK_TIMEOUT",
        durationMs: Math.round(performance.now() - requestStartedAt),
      });
      throw new ApiError({
        status: 0,
        code: "NETWORK_TIMEOUT",
        message: "Request timed out",
        retryable: true,
      });
    }
    if (error?.name !== "AbortError") {
      reportApiFailure({
        path,
        method,
        status: 0,
        errorCode:
          typeof navigator === "undefined" || navigator.onLine
            ? "NETWORK_ERROR"
            : "NETWORK_OFFLINE",
        durationMs: Math.round(performance.now() - requestStartedAt),
      });
    }
    throw error;
  } finally {
    requestSignal.cleanup();
  }

  if (response.status === 401 && auth && retryAuth && getRefreshToken()) {
    await refreshSession();
    return apiRequest(path, { ...options, retryAuth: false });
  }
  if (!response.ok) {
    const error = errorFrom(response, payload);
    reportApiFailure({
      path,
      method,
      status: response.status,
      errorCode: error.code,
      durationMs: Math.round(performance.now() - requestStartedAt),
    });
    throw error;
  }
  return payload;
}

export function newCommandKey(prefix = "command") {
  return `${prefix}-${crypto.randomUUID()}`;
}

export async function restoreSession() {
  if (!getRefreshToken()) return false;
  try {
    await refreshSession();
    return true;
  } catch {
    return false;
  }
}
