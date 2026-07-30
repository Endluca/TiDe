export const LESSONS_PER_PAGE = 12;

export function clampLessonPage(page, pageCount) {
  const safePageCount = Math.max(1, Number(pageCount) || 1);
  const numericPage = Number(page);
  if (!Number.isFinite(numericPage)) return 1;
  return Math.min(Math.max(Math.trunc(numericPage), 1), safePageCount);
}

export function buildLessonPageItems(page, pageCount) {
  const safePageCount = Math.max(1, Number(pageCount) || 1);
  const current = clampLessonPage(page, safePageCount);
  if (safePageCount <= 7) {
    return Array.from({ length: safePageCount }, (_, index) => index + 1);
  }

  const items = [1];
  const windowStart = Math.max(2, current - 1);
  const windowEnd = Math.min(safePageCount - 1, current + 1);

  if (windowStart > 2) items.push("start-ellipsis");
  for (let value = windowStart; value <= windowEnd; value += 1) {
    items.push(value);
  }
  if (windowEnd < safePageCount - 1) items.push("end-ellipsis");
  items.push(safePageCount);
  return items;
}
