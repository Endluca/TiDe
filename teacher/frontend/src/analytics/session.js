const SESSION_KEY = "tide-product-analytics-session-v1";
const SESSION_IDLE_MS = 30 * 60 * 1000;

const newSession = () => ({
  id: `analytics-${crypto.randomUUID()}`,
  lastActivityAt: Date.now(),
});

function readSession() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (
      parsed?.id
      && typeof parsed.id === "string"
      && Date.now() - Number(parsed.lastActivityAt) <= SESSION_IDLE_MS
    ) {
      return parsed;
    }
  } catch {
    // A new in-memory session is enough when browser storage is unavailable.
  }
  return newSession();
}

let currentSession = typeof window === "undefined" ? newSession() : readSession();

export function getAnalyticsSessionId() {
  if (Date.now() - currentSession.lastActivityAt > SESSION_IDLE_MS) {
    currentSession = newSession();
  } else {
    currentSession.lastActivityAt = Date.now();
  }
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(currentSession));
  } catch {
    // Analytics must never block user actions.
  }
  return currentSession.id;
}

export function resetAnalyticsSessionForTest() {
  currentSession = newSession();
}
