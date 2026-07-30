const taskTargetPattern = /^\/task\/[A-Za-z0-9_-]+$/;
const tasksTargetPattern = /^\/path(?:\?stage=[1-3])?$/;
const unavailableTaskStatuses = new Set(["cancelled", "expired"]);

export function resolveTaskMessageRoute(message, relatedTask) {
  if (
    message?.actionType !== "TASK_DETAIL" ||
    !message.actionAvailable ||
    !taskTargetPattern.test(message.actionTarget || "") ||
    (relatedTask && unavailableTaskStatuses.has(relatedTask.status))
  ) {
    return null;
  }
  return message.actionTarget;
}

export function resolveMessageActionRoute(message, relatedTask) {
  if (!message?.actionAvailable) return null;
  if (message.actionType === "TASK_DETAIL") {
    return resolveTaskMessageRoute(message, relatedTask);
  }
  if (message.actionType === "MY_TIDE" && message.actionTarget === "/") {
    return "/";
  }
  if (
    message.actionType === "TASKS" &&
    tasksTargetPattern.test(message.actionTarget || "")
  ) {
    return message.actionTarget;
  }
  return null;
}
