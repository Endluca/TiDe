export const SCORE_SYNC_POLL_INTERVAL_MS = 2_000;
export const SCORE_SYNC_TIMEOUT_MS = 60_000;
export const SCORE_SYNC_POLL_DELAYS_MS = [2_000, 4_000, 8_000, 15_000, 15_000];

function stableScorecardDimensions(dimensions = []) {
  return dimensions
    .map((dimension) => ({
      code: dimension.code,
      score: dimension.score,
      calculatedAt: dimension.calculatedAt,
      components: (dimension.components || [])
        .map((component) => ({
          code: component.code,
          score: component.score,
          unitCount: component.unitCount,
        }))
        .sort((left, right) => String(left.code).localeCompare(String(right.code))),
    }))
    .sort((left, right) => String(left.code).localeCompare(String(right.code)));
}

export function scorecardFingerprint(scorecard) {
  if (!scorecard) return null;
  return JSON.stringify({
    publicTotalScore: scorecard.publicTotalScore,
    availableScore: scorecard.availableScore?.score,
    graduationQualified: scorecard.graduationQualified,
    goldQualified: scorecard.goldQualified,
    scoreRuleVersion: scorecard.scoreRuleVersion,
    calculatedAt: scorecard.calculatedAt,
    dimensions: stableScorecardDimensions(scorecard.dimensions),
  });
}

function waitForNextPoll(milliseconds, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timeoutId = setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timeoutId);
        resolve();
      },
      { once: true },
    );
  });
}

function waitUntilVisible(signal, visibility) {
  if (!visibility || visibility.visibilityState !== "hidden") {
    return Promise.resolve(0);
  }
  return new Promise((resolve) => {
    const hiddenAt = Date.now();
    const finish = () => {
      visibility.removeEventListener?.("visibilitychange", changed);
      signal?.removeEventListener?.("abort", finish);
      resolve(Date.now() - hiddenAt);
    };
    const changed = () => {
      if (visibility.visibilityState !== "hidden") finish();
    };
    visibility.addEventListener?.("visibilitychange", changed);
    signal?.addEventListener?.("abort", finish, { once: true });
  });
}

export async function pollScorecard({
  load,
  baseline,
  signal,
  intervalMs,
  timeoutMs = SCORE_SYNC_TIMEOUT_MS,
  visibility = globalThis.document,
}) {
  const baselineFingerprint = scorecardFingerprint(baseline);
  let deadline = Date.now() + timeoutMs;
  const pollDelays = intervalMs === undefined
    ? SCORE_SYNC_POLL_DELAYS_MS
    : [Math.max(0, intervalMs)];
  let delayIndex = 0;
  let lastError = null;

  while (!signal?.aborted && Date.now() <= deadline) {
    deadline += await waitUntilVisible(signal, visibility);
    if (signal?.aborted) break;
    try {
      const scorecard = await load(signal);
      if (scorecardFingerprint(scorecard) !== baselineFingerprint) {
        return { status: "updated", scorecard };
      }
      lastError = null;
    } catch (error) {
      if (signal?.aborted) return { status: "cancelled" };
      lastError = error;
    }

    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) break;
    const nextDelay = pollDelays[Math.min(delayIndex, pollDelays.length - 1)];
    delayIndex += 1;
    await waitForNextPoll(Math.min(nextDelay, remainingMs), signal);
  }

  if (signal?.aborted) return { status: "cancelled" };
  return { status: "timeout", error: lastError };
}
