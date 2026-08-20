export const TIDE_SUMMARY_EMPTY_REASON = "NO_GROWTH_DATA";

export function isTideSummaryEmpty(summary) {
  return summary?.available === false
    && summary.reason === TIDE_SUMMARY_EMPTY_REASON;
}
