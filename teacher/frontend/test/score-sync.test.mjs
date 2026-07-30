import assert from "node:assert/strict";
import test from "node:test";
import {
  pollScorecard,
  SCORE_SYNC_POLL_DELAYS_MS,
  scorecardFingerprint,
} from "../src/score-sync.js";

const baseline = {
  publicTotalScore: 20,
  availableScore: { score: 10 },
  calculatedAt: "2026-07-28T01:00:00.000Z",
  dimensions: [
    {
      code: "NEW_TEACHER_TASK",
      score: 4,
      components: [{ code: "G03", score: 2, unitCount: 1 }],
    },
  ],
};

test("scorecardFingerprint ignores dimension and component order", () => {
  const reordered = {
    ...baseline,
    dimensions: [
      { code: "USER_FEEDBACK", score: 5, components: [] },
      ...baseline.dimensions,
    ],
  };
  const sameDataDifferentOrder = {
    ...reordered,
    dimensions: [...reordered.dimensions].reverse(),
  };
  assert.equal(
    scorecardFingerprint(reordered),
    scorecardFingerprint(sameDataDifferentOrder),
  );
});

test("pollScorecard returns the first changed scorecard", async () => {
  let requestCount = 0;
  const updated = {
    ...baseline,
    publicTotalScore: 22,
    availableScore: { score: 8 },
    calculatedAt: "2026-07-28T01:00:02.000Z",
  };
  const result = await pollScorecard({
    baseline,
    intervalMs: 1,
    timeoutMs: 100,
    load: async () => (++requestCount < 2 ? baseline : updated),
  });

  assert.equal(result.status, "updated");
  assert.equal(result.scorecard, updated);
  assert.equal(requestCount, 2);
});

test("pollScorecard stops when aborted", async () => {
  const controller = new AbortController();
  controller.abort();
  const result = await pollScorecard({
    baseline,
    signal: controller.signal,
    load: async () => baseline,
  });

  assert.deepEqual(result, { status: "cancelled" });
});

test("uses a bounded exponential score-sync schedule", () => {
  assert.deepEqual(SCORE_SYNC_POLL_DELAYS_MS, [2_000, 4_000, 8_000, 15_000, 15_000]);
  assert.equal(SCORE_SYNC_POLL_DELAYS_MS.length + 1, 6);
});

test("pauses score reads while the page is hidden", async () => {
  const visibility = new EventTarget();
  visibility.visibilityState = "hidden";
  let calls = 0;
  const polling = pollScorecard({
    baseline,
    load: async () => {
      calls += 1;
      return { ...baseline, publicTotalScore: 99 };
    },
    timeoutMs: 20,
    visibility,
  });

  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(calls, 0);
  visibility.visibilityState = "visible";
  visibility.dispatchEvent(new Event("visibilitychange"));

  assert.equal((await polling).status, "updated");
  assert.equal(calls, 1);
});
