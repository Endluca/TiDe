export function normalizeStageIndex(index, stageCount) {
  if (!Number.isInteger(stageCount) || stageCount <= 0) return 0;
  return ((index % stageCount) + stageCount) % stageCount;
}

export function nextClockwiseStageIndex(index, stageCount) {
  return normalizeStageIndex(index + 1, stageCount);
}

export function clockwiseStageSequence(fromIndex, toIndex, stageCount) {
  if (!Number.isInteger(stageCount) || stageCount <= 0) return [];
  const from = normalizeStageIndex(fromIndex, stageCount);
  const to = normalizeStageIndex(toIndex, stageCount);
  const steps = normalizeStageIndex(to - from, stageCount);
  return Array.from(
    { length: steps },
    (_, offset) => normalizeStageIndex(from + offset + 1, stageCount),
  );
}

export function clockwiseOrbitSlot(stageIndex, selectedIndex, stageCount) {
  return normalizeStageIndex(selectedIndex - stageIndex, stageCount);
}
