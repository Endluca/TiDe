export function taskNeedsStart(status) {
  return status === "ASSIGNED" || status === "VIEWED";
}
