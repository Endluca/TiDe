export const DOCUMENT_END_TOLERANCE_PX = 16;

const clamp = (value, minimum, maximum) => (
  Math.min(maximum, Math.max(minimum, value))
);

export function measureDocumentReadProgress({
  scrollTop,
  clientHeight,
  scrollHeight,
  tolerance = DOCUMENT_END_TOLERANCE_PX,
}) {
  const top = Number(scrollTop);
  const viewport = Number(clientHeight);
  const total = Number(scrollHeight);
  const parsedTolerance = Number(tolerance);
  const safeTolerance = Number.isFinite(parsedTolerance)
    ? Math.max(0, parsedTolerance)
    : DOCUMENT_END_TOLERANCE_PX;

  if (
    !Number.isFinite(top)
    || !Number.isFinite(viewport)
    || !Number.isFinite(total)
    || viewport <= 0
    || total <= 0
  ) {
    return { readPercent: 0, reachedEnd: false };
  }

  const visibleBottom = clamp(top, 0, Math.max(0, total - viewport)) + viewport;
  const reachedEnd = visibleBottom >= total - safeTolerance;
  return {
    readPercent: reachedEnd
      ? 100
      : clamp(Math.floor((visibleBottom / total) * 100), 0, 99),
    reachedEnd,
  };
}

export function mergeDocumentReadProgress(previous, current) {
  const previousPercent = Number.isInteger(previous?.readPercent)
    ? clamp(previous.readPercent, 0, 100)
    : 0;
  const currentPercent = Number.isInteger(current?.readPercent)
    ? clamp(current.readPercent, 0, 100)
    : 0;
  const reachedEnd = previous?.reachedEnd === true || current?.reachedEnd === true;
  return {
    readPercent: reachedEnd ? 100 : Math.max(previousPercent, currentPercent),
    reachedEnd,
  };
}

export function shouldPersistDocumentProgress(previousPercent, nextPercent) {
  const previous = Number.isInteger(previousPercent)
    ? clamp(previousPercent, 0, 100)
    : 0;
  const next = Number.isInteger(nextPercent)
    ? clamp(nextPercent, 0, 100)
    : 0;
  if (next <= previous) return false;
  return next === 100 || next - previous >= 5;
}

export function hasDocumentProgressAdvanced(previous, current) {
  const before = mergeDocumentReadProgress(null, previous);
  const after = mergeDocumentReadProgress(null, current);
  return (
    after.readPercent > before.readPercent
    || (after.reachedEnd && !before.reachedEnd)
  );
}

export function canPersistDocumentProgress({
  readOnly = false,
  assignmentCompleted = false,
  documentCompleted = false,
  contentCompatible = true,
} = {}) {
  return contentCompatible && !readOnly && !assignmentCompleted && !documentCompleted;
}

export function documentReadProgressFromStep(step, expected = {}) {
  const details = step?.details;
  const percent = step?.percent;
  const expectedStatus = percent === 100
    ? "COMPLETED"
    : percent > 0
      ? "IN_PROGRESS"
      : "NOT_STARTED";
  const valid = (
    step
    && typeof step === "object"
    && typeof step.stepKey === "string"
    && step.stepKey === expected.stepKey
    && Number.isInteger(percent)
    && percent >= 0
    && percent <= 100
    && step.status === expectedStatus
    && details
    && typeof details === "object"
    && !Array.isArray(details)
    && Number.isInteger(details.readPercent)
    && details.readPercent === percent
    && typeof details.reachedEnd === "boolean"
    && details.reachedEnd === (percent === 100)
    && details.contentVersion === expected.contentVersion
    && details.contentHash === expected.contentHash
  );
  if (!valid) throw new Error("INVALID_DOCUMENT_PROGRESS_RESPONSE");
  return {
    readPercent: percent,
    reachedEnd: details.reachedEnd,
  };
}
