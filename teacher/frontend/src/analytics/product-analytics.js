import { sendAppEvents } from "../api/app-event-api";
import { setApiFailureObserver } from "../api/api-client";
import { getAccessToken, getRefreshToken } from "../api/session-store";
import {
  EVENT_SCHEMA_VERSION,
  PRODUCT_EVENT_NAME_SET,
} from "./event-dictionary";
import { getAnalyticsSessionId } from "./session";
import { sanitizeAnalyticsProperties } from "./sanitize";
import {
  analyticsRetryDelay,
  isPermanentAnalyticsFailure,
} from "./retry-policy";

const QUEUE_KEY = "tide-product-analytics-queue-v1";
const ONCE_KEY = "tide-product-analytics-once-v1";
const MAX_QUEUE_SIZE = 100;
const MAX_RETRIES = 3;
const FLUSH_THRESHOLD = 20;
const FLUSH_BATCH_SIZE = 50;
const FLUSH_DELAY_MS = 5_000;
let languageContext = "en";
let queue = [];
let flushing = false;
let retryTimer = null;
let runtimeStarted = false;
let onceSessionId = null;
let onceKeys = new Set();

function currentOnceKeys() {
  const sessionId = getAnalyticsSessionId();
  if (onceSessionId === sessionId) return { sessionId, keys: onceKeys };
  onceSessionId = sessionId;
  onceKeys = new Set();
  try {
    const stored = JSON.parse(sessionStorage.getItem(ONCE_KEY) || "null");
    if (stored?.sessionId === sessionId && Array.isArray(stored.keys)) {
      onceKeys = new Set(stored.keys.slice(-2_000));
    }
  } catch {
    // In-memory deduplication is enough when browser storage is unavailable.
  }
  return { sessionId, keys: onceKeys };
}

function rememberOnce(key) {
  const { sessionId, keys } = currentOnceKeys();
  if (keys.size >= 2_000) keys.clear();
  keys.add(key);
  try {
    sessionStorage.setItem(
      ONCE_KEY,
      JSON.stringify({ sessionId, keys: [...keys] }),
    );
  } catch {
    // Analytics must never block user actions.
  }
}

function taskAssignmentIdFor(options) {
  return options.taskAssignmentId
    || options.task?.backendId
    || options.task?.backendContext?.taskInstanceId
    || null;
}

function detectDevice() {
  const ua = navigator.userAgent || "";
  const mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(ua);
  const os = /Windows/i.test(ua)
    ? "Windows"
    : /Android/i.test(ua)
      ? "Android"
      : /iPhone|iPad|iPod/i.test(ua)
        ? "iOS"
        : /Mac OS/i.test(ua)
          ? "macOS"
          : /Linux/i.test(ua)
            ? "Linux"
            : "Other";
  const browser = /Edg\//i.test(ua)
    ? "Edge"
    : /Chrome\//i.test(ua)
      ? "Chrome"
      : /Firefox\//i.test(ua)
        ? "Firefox"
        : /Safari\//i.test(ua)
          ? "Safari"
          : "Other";
  return { deviceType: mobile ? "MOBILE" : "PC", os, browser };
}

function pagePath() {
  const hashPath = window.location.hash.replace(/^#/, "").split("?")[0];
  return hashPath || window.location.pathname || "/";
}

function clientVersion() {
  const environment = import.meta.env || {};
  return environment.VITE_APP_VERSION || environment.VITE_COMMIT_SHA || "development";
}

function taskProperties(task) {
  const context = task?.backendContext;
  return sanitizeAnalyticsProperties({
    taskCode: task?.taskCode || context?.taskCode,
    taskType: task?.taskCategory || context?.kind,
    templateVersion: context?.templateVersion,
    executionContractVersion: context?.executionContractVersion,
    taskStatus: context?.status || task?.backendStatus || task?.status,
    attemptNo: task?.attemptNo,
  });
}

function loadQueue() {
  try {
    const stored = JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
    if (Array.isArray(stored)) queue = stored.slice(-MAX_QUEUE_SIZE);
  } catch {
    queue = [];
  }
}

function persistQueue() {
  try {
    if (queue.length === 0) localStorage.removeItem(QUEUE_KEY);
    else localStorage.setItem(QUEUE_KEY, JSON.stringify(queue.slice(-MAX_QUEUE_SIZE)));
  } catch {
    // Analytics storage is optional.
  }
}

function scheduleFlush(delay = FLUSH_DELAY_MS) {
  if (retryTimer && delay > 0) return;
  if (retryTimer) window.clearTimeout(retryTimer);
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    void flushAnalyticsQueue();
  }, delay);
}

export function setAnalyticsLanguage(language) {
  languageContext = language === "zh" ? "zh" : "en";
}

export function trackProductEvent(eventName, options = {}) {
  if (!PRODUCT_EVENT_NAME_SET.has(eventName)) return null;
  const authenticated = Boolean(getAccessToken() || getRefreshToken());
  const taskAssignmentId = taskAssignmentIdFor(options);
  if (taskAssignmentId && !authenticated) return null;
  const payload = {
    eventName,
    eventId: `event-${crypto.randomUUID()}`,
    eventSchemaVersion: EVENT_SCHEMA_VERSION,
    sessionId: getAnalyticsSessionId(),
    ...(taskAssignmentId ? { taskAssignmentId } : {}),
    properties: sanitizeAnalyticsProperties({
      page: pagePath(),
      ...detectDevice(),
      language: languageContext,
      clientVersion: clientVersion(),
      online: navigator.onLine,
      ...taskProperties(options.task),
      ...(options.properties || {}),
    }),
    occurredAt: new Date().toISOString(),
  };
  queue.push({
    payload,
    anonymous: !authenticated,
    attempts: 0,
    nextAttemptAt: 0,
  });
  if (queue.length > MAX_QUEUE_SIZE) queue = queue.slice(-MAX_QUEUE_SIZE);
  persistQueue();
  scheduleFlush(queue.length >= FLUSH_THRESHOLD ? 0 : FLUSH_DELAY_MS);
  return payload.eventId;
}

export function trackProductEventOnce(eventName, options = {}, uniqueKey = "default") {
  const taskAssignmentId = taskAssignmentIdFor(options) || "anonymous";
  const key = [eventName, taskAssignmentId, uniqueKey].join(":");
  const { keys } = currentOnceKeys();
  if (keys.has(key)) return null;
  const eventId = trackProductEvent(eventName, options);
  if (eventId) rememberOnce(key);
  return eventId;
}

export async function flushAnalyticsQueue({ keepalive = false } = {}) {
  if (flushing || queue.length === 0 || !navigator.onLine) return;
  flushing = true;
  try {
    while (queue.length > 0) {
      const now = Date.now();
      const firstReady = queue.find((item) => item.nextAttemptAt <= now);
      if (!firstReady) {
        scheduleFlush(Math.max(
          0,
          Math.min(...queue.map((item) => item.nextAttemptAt)) - now,
        ));
        break;
      }
      const batch = queue
        .filter((item) => (
          item.anonymous === firstReady.anonymous
          && item.nextAttemptAt <= now
        ))
        .slice(0, FLUSH_BATCH_SIZE);
      try {
        await sendAppEvents(
          batch.map((item) => item.payload),
          firstReady.anonymous,
          { keepalive },
        );
        const sent = new Set(batch);
        queue = queue.filter((item) => !sent.has(item));
        persistQueue();
      } catch (error) {
        const status = Number(error?.status) || 0;
        const permanent = isPermanentAnalyticsFailure(status);
        batch.forEach((item) => {
          if (permanent || item.attempts >= MAX_RETRIES) {
            queue = queue.filter((queued) => queued !== item);
            return;
          }
          item.attempts += 1;
          item.nextAttemptAt = Date.now() + analyticsRetryDelay(
            item.attempts,
            error?.retryAfterSeconds,
          );
        });
        persistQueue();
        if (!permanent) {
          scheduleFlush(Math.max(
            0,
            Math.min(...batch.map((item) => item.nextAttemptAt)) - Date.now(),
          ));
        }
        break;
      }
    }
  } finally {
    flushing = false;
  }
}

export function beginPageAnalytics({ task = null, loadResult = "SUCCESS" } = {}) {
  const startedAt = performance.now();
  let visibleStartedAt = document.visibilityState === "visible" ? performance.now() : null;
  let visibleMs = 0;
  trackProductEvent("PAGE_VIEWED", { task });
  trackProductEvent(
    loadResult === "SUCCESS" ? "PAGE_LOAD_SUCCEEDED" : "PAGE_LOAD_FAILED",
    {
      task,
      properties: {
        result: loadResult,
        ...(loadResult === "SUCCESS" ? {} : { errorCode: "PAGE_DATA_UNAVAILABLE" }),
      },
    },
  );
  const visibilityChanged = () => {
    if (document.visibilityState === "visible") {
      visibleStartedAt = performance.now();
    } else if (visibleStartedAt !== null) {
      visibleMs += performance.now() - visibleStartedAt;
      visibleStartedAt = null;
    }
  };
  document.addEventListener("visibilitychange", visibilityChanged);
  return () => {
    if (visibleStartedAt !== null) visibleMs += performance.now() - visibleStartedAt;
    document.removeEventListener("visibilitychange", visibilityChanged);
    trackProductEvent("PAGE_EXITED", {
      task,
      properties: {
        durationMs: Math.max(0, Math.round(performance.now() - startedAt)),
        effectiveDurationMs: Math.max(0, Math.round(visibleMs)),
      },
    });
  };
}

export function observeEffectiveImpressions(selector, eventName) {
  if (!("IntersectionObserver" in window)) return () => {};
  const timers = new Map();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const element = entry.target;
      const dataset = element.dataset;
      const key = [
        getAnalyticsSessionId(),
        eventName,
        dataset.analyticsTaskAssignmentId || dataset.analyticsMessageId || "",
        dataset.analyticsEntrySource || "",
        dataset.analyticsDisplayPosition || "",
      ].join(":");
      const { keys: seen } = currentOnceKeys();
      if (entry.intersectionRatio >= 0.5 && !seen.has(key) && !timers.has(element)) {
        const timer = window.setTimeout(() => {
          timers.delete(element);
          if (!element.isConnected || seen.has(key)) return;
          const eventId = trackProductEvent(eventName, {
            taskAssignmentId: dataset.analyticsTaskAssignmentId || null,
            properties: {
              entrySource: dataset.analyticsEntrySource || "UNKNOWN",
              displayPosition: dataset.analyticsDisplayPosition || "UNKNOWN",
              taskCode: dataset.analyticsTaskCode || undefined,
              taskType: dataset.analyticsTaskType || undefined,
              notificationType: dataset.analyticsNotificationType || undefined,
              visibilityRatio: 0.5,
            },
          });
          if (eventId) {
            rememberOnce(key);
          }
        }, 1_000);
        timers.set(element, timer);
      } else if (entry.intersectionRatio < 0.5 && timers.has(element)) {
        window.clearTimeout(timers.get(element));
        timers.delete(element);
      }
    });
  }, { threshold: [0, 0.5, 1] });
  document.querySelectorAll(selector).forEach((element) => observer.observe(element));
  return () => {
    timers.forEach((timer) => window.clearTimeout(timer));
    timers.clear();
    observer.disconnect();
  };
}

function normalizedEndpoint(path) {
  return String(path || "")
    .split("?")[0]
    .replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, ":id")
    .replace(/\/(TASK|ASSIGNMENT)-[^/]+/gi, "/:id")
    .replace(/\/[A-Za-z0-9:_-]{24,}(?=\/|$)/g, "/:id")
    .slice(0, 256);
}

export function startAnalyticsRuntime() {
  if (runtimeStarted) return;
  runtimeStarted = true;
  loadQueue();
  setApiFailureObserver((failure) => {
    if (String(failure.path).includes("/app-events")) return;
    const timeout = failure.errorCode === "NETWORK_TIMEOUT";
    trackProductEvent(timeout ? "NETWORK_TIMEOUT" : "API_FAILED", {
      properties: {
        endpointGroup: normalizedEndpoint(failure.path),
        httpMethod: failure.method,
        statusCode: failure.status || 0,
        errorCode: failure.errorCode || "REQUEST_FAILED",
        durationMs: failure.durationMs,
        result: "FAILURE",
      },
    });
  });
  document.addEventListener("click", (event) => {
    const taskCard = event.target.closest?.("[data-analytics-task-card]");
    if (taskCard?.dataset.analyticsTaskAssignmentId) {
      const taskAssignmentId = taskCard.dataset.analyticsTaskAssignmentId;
      const entrySource = taskCard.dataset.analyticsEntrySource || "UNKNOWN";
      const displayPosition =
        taskCard.dataset.analyticsDisplayPosition || "UNKNOWN";
      try {
        sessionStorage.setItem("tide-last-task-entry-v1", JSON.stringify({
          taskAssignmentId,
          entrySource,
          displayPosition,
          recordedAt: Date.now(),
        }));
      } catch {
        // Entry attribution is optional.
      }
      trackProductEvent("TASK_CARD_CLICKED", {
        taskAssignmentId,
        properties: {
          entrySource,
          displayPosition,
          taskCode: taskCard.dataset.analyticsTaskCode || undefined,
          taskType: taskCard.dataset.analyticsTaskType || undefined,
        },
      });
    }
    const anchor = event.target.closest?.("a[href]");
    if (anchor) {
      trackProductEvent("NAVIGATION_CLICKED", {
        properties: {
          sourcePage: pagePath(),
          targetPage: String(anchor.getAttribute("href") || "").replace(/^#/, "").split("?")[0].slice(0, 256),
          entrySource: anchor.dataset.analyticsEntrySource || "NAVIGATION",
          displayPosition: anchor.dataset.analyticsDisplayPosition || "LINK",
        },
      });
    }
  });
  window.addEventListener("online", () => scheduleFlush());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      void flushAnalyticsQueue({ keepalive: true });
    }
  });
  window.addEventListener("pagehide", () => {
    void flushAnalyticsQueue({ keepalive: true });
  });
  window.addEventListener("error", (event) => {
    trackProductEvent("FRONTEND_UNCAUGHT_ERROR", {
      properties: {
        errorCode: event.error?.name || "WINDOW_ERROR",
        result: "FAILURE",
      },
    });
  });
  window.addEventListener("unhandledrejection", (event) => {
    trackProductEvent("FRONTEND_UNCAUGHT_ERROR", {
      properties: {
        errorCode: event.reason?.name || "UNHANDLED_REJECTION",
        result: "FAILURE",
      },
    });
  });
  scheduleFlush(FLUSH_DELAY_MS);
}

export function taskEntryAttribution(taskAssignmentId) {
  try {
    const value = JSON.parse(sessionStorage.getItem("tide-last-task-entry-v1") || "null");
    if (
      value?.taskAssignmentId === taskAssignmentId
      && Date.now() - Number(value.recordedAt) < 30 * 60 * 1000
    ) {
      return {
        entrySource: value.entrySource || "UNKNOWN",
        displayPosition: value.displayPosition || "UNKNOWN",
      };
    }
  } catch {
    // Use an explicit unknown attribution when storage is unavailable.
  }
  return { entrySource: "DIRECT", displayPosition: "UNKNOWN" };
}
