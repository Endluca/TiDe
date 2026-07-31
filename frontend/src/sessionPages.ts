export function retainVisitedPage(current: Set<string>, pageKey: string): Set<string> {
  if (current.has(pageKey)) return current
  const next = new Set(current)
  next.add(pageKey)
  return next
}
