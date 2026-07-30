export function getFirstLessonReminder(task, now = Date.now()) {
  const startsAt = task?.firstLessonStartAt ? new Date(task.firstLessonStartAt).getTime() : Number.NaN;
  if (!Number.isFinite(startsAt)) return null;

  const minutesRemaining = Math.ceil((startsAt - now) / 60000);
  const warningThreshold = task.warningReminderThresholdMinutes ?? 1440;
  const strongThreshold = task.strongReminderThresholdMinutes ?? 360;
  const criticalThreshold = task.criticalReminderThresholdMinutes ?? 60;
  const level = minutesRemaining <= 0
    ? "overdue"
    : minutesRemaining <= criticalThreshold
      ? "critical"
      : minutesRemaining <= strongThreshold
        ? "strong"
        : minutesRemaining <= warningThreshold
          ? "warning"
          : "normal";

  return { startsAt, minutesRemaining, level };
}

export function formatFirstLessonCountdown(minutesRemaining, language = "en") {
  if (minutesRemaining <= 0) return language === "zh" ? "首课时间已到" : "First lesson time reached";
  const hours = Math.floor(minutesRemaining / 60);
  const minutes = minutesRemaining % 60;
  if (hours <= 0) return language === "zh" ? `还剩 ${minutes} 分钟` : `${minutes} min remaining`;
  if (minutes === 0) return language === "zh" ? `还剩 ${hours} 小时` : `${hours} hr remaining`;
  return language === "zh" ? `还剩 ${hours} 小时 ${minutes} 分钟` : `${hours} hr ${minutes} min remaining`;
}

export function formatFirstLessonTime(startsAt, language = "en") {
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(startsAt);
}
