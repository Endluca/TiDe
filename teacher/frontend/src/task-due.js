const englishDueDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZoneName: "short",
});

export function formatTaskDueAt(dueAt) {
  if (!dueAt) return null;
  const date = new Date(dueAt);
  if (!Number.isFinite(date.getTime())) return null;
  return `Due ${englishDueDateFormatter.format(date)}`;
}

export function localizedTaskDue(task, localizedDue) {
  if (task.taskCategory === "personalized") return task.due;
  if (task.status === "completed") return "已完成";
  if (task.due === "Available now") return "现已开放";
  return localizedDue || task.due;
}
