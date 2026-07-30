const notificationCopy = {
  GROWTH_STAGE_AVAILABLE: {
    title: {
      en: "Your next growth stage is ready",
      zh: "新的成长阶段已开放",
    },
    body: {
      en: "A new set of required tasks is now available in your growth path. Complete them in the order that works best for you.",
      zh: "一组新的必修任务已经开放，你可以按照适合自己的顺序完成本阶段任务。",
    },
  },
  PERSONALIZED_TASK_ASSIGNED: {
    body: {
      en: "A new improvement task is ready for you. Open it when you are ready to take the next step.",
      zh: "一项新的改善任务已准备好，你可以在合适的时候打开并开始下一步。",
    },
  },
  PERSONALIZED_TASK_DUE_24H: {
    body: {
      en: "A quick reminder: this improvement task is due within 24 hours. You can continue from where you left off.",
      zh: "温馨提醒：这项改善任务将在 24 小时内到期，你可以从上次停下的位置继续。",
    },
  },
  TASK_REVIEW_COMPLETED: {
    title: {
      en: "Task completed",
      zh: "任务已完成",
    },
    body: {
      en: "Your task submission has been reviewed and completed.",
      zh: "你的任务提交已审核并完成。",
    },
  },
  TASK_RETRY_REQUIRED: {
    title: {
      en: "Your task needs another attempt",
      zh: "任务需要再次尝试",
    },
    body: {
      en: "Open the task to review the feedback and try again.",
      zh: "请打开任务查看反馈后再次尝试。",
    },
  },
  TASK_REVIEW_PENDING: {
    title: {
      en: "Task review in progress",
      zh: "任务正在审核",
    },
    body: {
      en: "Your task submission is saved and still being reviewed. Open the task to check the latest status.",
      zh: "你的任务提交已保存，正在审核中。打开任务即可查看最新状态。",
    },
  },
};

export function localizeNotification(message, language) {
  if (message?.source !== "SYSTEM") {
    return {
      title: message?.title || "",
      body: message?.body || "",
    };
  }
  const localized = notificationCopy[message.typeCode];
  const locale = language === "zh" ? "zh" : "en";
  return {
    title: localized?.title?.[locale] || message.title,
    body: localized?.body?.[locale] || message.body,
  };
}
