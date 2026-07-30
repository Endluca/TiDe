import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  PRODUCT_EVENT_NAMES,
  PRODUCT_EVENT_PROPERTY_NAMES,
} from "../src/analytics/event-dictionary.js";
import { sanitizeAnalyticsProperties } from "../src/analytics/sanitize.js";
import {
  analyticsRetryDelay,
  isPermanentAnalyticsFailure,
} from "../src/analytics/retry-policy.js";

const backendDictionaryUrl = new URL(
  "../../backend/src/app-events/app-event.dictionary.ts",
  import.meta.url,
);

test("keeps frontend event names and properties inside the backend dictionary", async () => {
  const backendDictionary = await readFile(backendDictionaryUrl, "utf8");

  for (const eventName of PRODUCT_EVENT_NAMES) {
    assert.match(backendDictionary, new RegExp(`['"]${eventName}['"]`));
  }
  for (const propertyName of PRODUCT_EVENT_PROPERTY_NAMES) {
    assert.match(backendDictionary, new RegExp(`['"]${propertyName}['"]`));
  }
});

test("drops sensitive, nested and unknown analytics properties", () => {
  const sanitized = sanitizeAnalyticsProperties({
    page: "/my-tide",
    taskCode: "G06",
    durationMs: 1200,
    email: "teacher@example.com",
    answers: ["A"],
    imageUrl: "https://example.com/private.jpg",
    unknownProperty: "not-in-dictionary",
    nested: { value: true },
  });

  assert.deepEqual(sanitized, {
    page: "/my-tide",
    taskCode: "G06",
    durationMs: 1200,
  });
});

test("keeps analytics strings bounded to the server contract", () => {
  const sanitized = sanitizeAnalyticsProperties({
    page: "x".repeat(700),
  });

  assert.equal(sanitized.page.length, 512);
});

test("uses the 50 percent for one second effective-impression rule", async () => {
  const source = await readFile(
    new URL("../src/analytics/product-analytics.js", import.meta.url),
    "utf8",
  );

  assert.match(source, /intersectionRatio >= 0\.5/);
  assert.match(source, /}, 1_000\)/);
  assert.match(source, /threshold: \[0, 0\.5, 1\]/);
  assert.match(source, /tide-product-analytics-once-v1/);
  assert.match(source, /sessionStorage\.setItem\(\s*ONCE_KEY/);
});

test("uses a 30-minute per-tab analytics session", async () => {
  const source = await readFile(
    new URL("../src/analytics/session.js", import.meta.url),
    "utf8",
  );

  assert.match(source, /30 \* 60 \* 1000/);
  assert.match(source, /sessionStorage/);
});

test("debounces task exit across transient task-detail remounts", async () => {
  const source = await readFile(
    new URL("../src/App.jsx", import.meta.url),
    "utf8",
  );

  assert.match(
    source,
    /analyticsExitTimerRef\.current\?\.taskId === raw\.backendId/,
  );
  assert.match(
    source,
    /window\.clearTimeout\(analyticsExitTimerRef\.current\.timer\)/,
  );
  assert.match(source, /trackProductEvent\("TASK_EXITED"[\s\S]{0,800}\}, 500\)/);
});

test("retries transient analytics failures and respects Retry-After", () => {
  assert.equal(isPermanentAnalyticsFailure(400), true);
  assert.equal(isPermanentAnalyticsFailure(401), true);
  assert.equal(isPermanentAnalyticsFailure(408), false);
  assert.equal(isPermanentAnalyticsFailure(429), false);
  assert.equal(isPermanentAnalyticsFailure(503), false);
  assert.equal(analyticsRetryDelay(1), 1_000);
  assert.equal(analyticsRetryDelay(2), 5_000);
  assert.equal(analyticsRetryDelay(3), 30_000);
  assert.equal(analyticsRetryDelay(1, 12), 12_000);
});

test("batches analytics on size, time, and page exit", async () => {
  const source = await readFile(
    new URL("../src/analytics/product-analytics.js", import.meta.url),
    "utf8",
  );

  assert.match(source, /const FLUSH_THRESHOLD = 20/);
  assert.match(source, /const FLUSH_BATCH_SIZE = 50/);
  assert.match(source, /const FLUSH_DELAY_MS = 5_000/);
  assert.match(source, /sendAppEvents\(/);
  assert.match(source, /flushAnalyticsQueue\(\{ keepalive: true \}\)/);
});

test("does not retain the legacy G02 policy video-heartbeat completion loop", async () => {
  const source = await readFile(
    new URL("../src/components/IntegratedTaskFlow.jsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /completeLegacyPolicyPrerequisite/);
  assert.doesNotMatch(source, /while \(position < duration\)/);
});
