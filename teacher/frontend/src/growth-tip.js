import { formatTaskDueAt } from "./task-due.js";

const localized = (language, english, chinese) =>
  language === "zh" ? chinese : english;

const stageCopy = {
  foundation: {
    range: ["DAY 1–7", "第 1–7 天"],
    title: [
      (name) => `${name}, let’s get the essentials ready`,
      (name) => `${name}，先把开课基础稳稳搭好`,
    ],
    body: [
      "This stage is about your profile, setup and core platform habits. One clear step at a time will help you feel ready for your first lessons.",
      "这一阶段重点是资料、设备和基础规则。一次完成一件，就离顺利开课更近一步。",
    ],
  },
  integration: {
    range: ["DAY 8–14", "第 8–14 天"],
    title: [
      (name) => `${name}, you’re building your teaching rhythm`,
      (name) => `${name}，你正在形成自己的授课节奏`,
    ],
    body: [
      "Keep practising the platform, classroom and reliability habits that make each lesson feel calmer and more consistent.",
      "继续把排课、课堂习惯和教学方法练熟，稳定的小习惯会让每节课更从容。",
    ],
  },
  advance: {
    range: ["DAY 15–30", "第 15–30 天"],
    title: [
      (name) => `${name}, make what you’ve learned even steadier`,
      (name) => `${name}，把已经学会的内容用得更稳`,
    ],
    body: [
      "You’re in the skill-building stage now. Keep moving through focused training and turn each next step into a reliable classroom habit.",
      "现在进入能力巩固阶段，继续完成专项课程，把每一个下一步变成稳定的课堂能力。",
    ],
  },
  unknown: {
    range: ["YOUR GROWTH", "成长进行中"],
    title: [
      (name) => `${name}, keep moving at your own pace`,
      (name) => `${name}，按自己的节奏继续前进`,
    ],
    body: [
      "Your training path is ready. I’ll keep the next useful step here whenever there is something for you to do.",
      "你的成长路径已经准备好。有适合继续完成的内容时，我会把下一步放在这里。",
    ],
  },
};

const statusPriority = {
  retry_required: 0,
  failed_final: 0,
  started: 1,
  submitting: 1,
  submitted: 2,
  verifying: 2,
  sync_pending: 2,
  available: 3,
  preview: 4,
};

const terminalStatuses = new Set(["completed", "waived", "cancelled", "expired"]);
const dueSoonStatuses = new Set(["available", "preview", "started", "retry_required"]);
const unsafeTeacherCopyPattern =
  /(?:evidence|internal|complaint|blacklist(?:ed)?|high[-_\s]?risk|risk[-_\s]?(?:level|label)|red[-_\s]?flag|error[-_\s]?code|投诉|拉黑|高危|风险标签|内部证据|错误码|扣分|淘汰|红线)/iu;
const stageKeys = ["foundation", "integration", "advance"];
const stageIndexByValue = {
  FOUNDATION: 0,
  DAY_1_7: 0,
  "Day 1-7": 0,
  INTEGRATION: 1,
  DAY_8_14: 1,
  "Day 8-14": 1,
  ADVANCE: 2,
  DAY_15_30: 2,
  "Day 15-30": 2,
};

export function stageIndexForCampDay(campDay) {
  if (campDay === null || campDay === undefined || campDay === "") return 0;
  const day = Number(campDay);
  if (!Number.isFinite(day)) return 0;
  if (day >= 15) return 2;
  if (day >= 8) return 1;
  return 0;
}

export function stageKeyForCampDay(campDay) {
  if (campDay === null || campDay === undefined || campDay === "") return "unknown";
  const day = Number(campDay);
  if (!Number.isFinite(day)) return "unknown";
  if (day >= 15) return "advance";
  if (day >= 8) return "integration";
  return "foundation";
}

function taskStageIndex(task) {
  const values = [
    task.backendContext?.display?.stageKey,
    task.sourceStage,
    task.stage,
  ];
  for (const value of values) {
    if (value in stageIndexByValue) return stageIndexByValue[value];
  }
  return null;
}

export function stageIndexForAvailableTasks(tasks = [], campDay) {
  return tasks.reduce((highest, task) => {
    if (task.taskCategory === "personalized" || task.locked) return highest;
    const index = taskStageIndex(task);
    return index === null ? highest : Math.max(highest, index);
  }, stageIndexForCampDay(campDay));
}

export function stageIndexFromPathSearch(search, highestAvailableStageIndex) {
  const requestedStage = Number(new URLSearchParams(search).get("stage"));
  if (!Number.isInteger(requestedStage) || requestedStage < 1 || requestedStage > 3) {
    return highestAvailableStageIndex;
  }
  return Math.min(requestedStage - 1, highestAvailableStageIndex);
}

function stageKeyForAvailableTasks(tasks, campDay) {
  return stageKeys[stageIndexForAvailableTasks(tasks, campDay)]
    || stageKeyForCampDay(campDay);
}

function taskPriority(task) {
  const value = task.backendContext?.assignment?.priority || task.priority;
  return { P0: 0, P1: 1, P2: 2, P3: 3 }[value] ?? 9;
}

function taskDueAt(task) {
  const dueAt = task.backendContext?.dueAt;
  const timestamp = dueAt ? new Date(dueAt).getTime() : Number.POSITIVE_INFINITY;
  return Number.isFinite(timestamp) ? timestamp : Number.POSITIVE_INFINITY;
}

function taskSortValue(task) {
  return [
    task.method === "content_pending" ? 5 : (statusPriority[task.status] ?? 6),
    taskPriority(task),
    taskDueAt(task),
    task.taskCategory === "personalized" ? 1 : 0,
    task.displayRank ?? 99,
  ];
}

function compareTasks(left, right) {
  const leftValue = taskSortValue(left);
  const rightValue = taskSortValue(right);
  for (let index = 0; index < leftValue.length; index += 1) {
    if (leftValue[index] !== rightValue[index]) return leftValue[index] - rightValue[index];
  }
  return String(left.id).localeCompare(String(right.id));
}

function teacherSafePrompt(task, language) {
  const prompt = [task.result, task.reason]
    .find((value) => typeof value === "string" && value.trim().length > 0)
    ?.replace(/\s+/gu, " ")
    .trim();
  if (
    !prompt ||
    prompt.length > 280 ||
    unsafeTeacherCopyPattern.test(prompt)
  ) {
    return localized(
      language,
      "Open the task when you’re ready and follow the steps provided.",
      "准备好后打开任务，按照页面步骤继续完成。",
    );
  }
  return prompt;
}

function dueSoon(task, now) {
  if (!dueSoonStatuses.has(task.status) || !task.dueAt) return null;
  const dueAt = new Date(task.dueAt);
  const remaining = dueAt.getTime() - now.getTime();
  if (!Number.isFinite(remaining) || remaining <= 0 || remaining > 24 * 60 * 60 * 1000) {
    return null;
  }
  return {
    label: formatTaskDueAt(task.dueAt),
    dateTime: dueAt.toISOString(),
  };
}

export function selectGrowthTipTask(tasks) {
  return [...tasks]
    .filter((task) => !task.locked && !terminalStatuses.has(task.status))
    .sort(compareTasks)[0] || null;
}

function statusCopy(task, prompt, language) {
  if (task.status === "retry_required") {
    return {
      label: localized(language, "ADJUST AND TRY AGAIN", "调整后再试一次"),
      prompt: localized(
        language,
        `Your progress is saved. ${prompt} Review the feedback, update only what needs attention and try again.`,
        `你的进度已经保存。${prompt}查看页面反馈，只调整需要处理的部分后再试一次。`,
      ),
      buttonLabel: localized(language, "Try again", "调整后重试"),
      mood: "thumb",
      tone: "recovery",
    };
  }
  if (task.status === "failed_final") {
    return {
      label: localized(language, "CHECK THE NEXT STEP", "查看后续安排"),
      prompt: localized(
        language,
        `Your progress is saved. ${prompt} Open the task to review the result and the next available step.`,
        `你的进度已经保存。${prompt}打开任务查看结果和接下来的安排。`,
      ),
      buttonLabel: localized(language, "View result", "查看结果"),
      mood: "wave",
      tone: "waiting",
    };
  }
  if (task.status === "sync_pending") {
    return {
      label: localized(language, "PROGRESS SYNCING", "进度同步中"),
      prompt: localized(
        language,
        "Your latest progress is being synced. You do not need to submit it again.",
        "最新进度正在同步，无需重复提交。",
      ),
      buttonLabel: localized(language, "View progress", "查看进度"),
      mood: "wave",
      tone: "waiting",
    };
  }
  if (["submitted", "verifying"].includes(task.status)) {
    return {
      label: localized(language, "SUBMISSION RECEIVED", "提交已收到"),
      prompt: localized(
        language,
        "Your work has been submitted, so there’s no need to repeat it. I’ll keep the latest review status here.",
        "内容已经提交，无需重复操作。审核结果更新后，我会继续陪你走下一步。",
      ),
      buttonLabel: localized(language, "View progress", "查看进度"),
      mood: "wave",
      tone: "waiting",
    };
  }
  if (task.status === "submitting") {
    return {
      label: localized(language, "SAVING YOUR WORK", "正在保存"),
      prompt: localized(
        language,
        "Your work is being saved. You do not need to submit it again.",
        "你的内容正在保存，无需重复提交。",
      ),
      buttonLabel: null,
      mood: "wave",
      tone: "waiting",
    };
  }
  if (task.status === "started") {
    return {
      label: localized(language, "CONTINUE YOUR NEXT STEP", "继续当前这一步"),
      prompt: localized(
        language,
        `${prompt} Continue from where you left off.`,
        `${prompt}从上次的位置继续就好。`,
      ),
      buttonLabel: localized(language, "Continue task", "继续任务"),
      mood: "thumb",
      tone: "continue",
    };
  }
  if (task.method === "content_pending") {
    return {
      label: localized(language, "CONTENT IN PREPARATION", "内容准备中"),
      prompt: localized(
        language,
        "This task is not ready yet. You do not need to take any action; it will update here when the content is available.",
        "这项任务暂未开放，现在无需操作；内容准备好后会在这里更新。",
      ),
      buttonLabel: null,
      mood: "wave",
      tone: "waiting",
    };
  }
  return {
    label: localized(language, "A GOOD NEXT STEP", "这一步可以先做"),
    prompt,
    buttonLabel: localized(language, "Start task", "开始任务"),
    mood: "thumb",
    tone: "ready",
  };
}

export function buildGrowthTip({
  teacherName,
  campDay,
  tasks = [],
  language = "en",
  sourceUnavailable = false,
  now = new Date(),
}) {
  const stageKey = stageKeyForAvailableTasks(tasks, campDay);
  const stage = stageCopy[stageKey];
  const languageIndex = language === "zh" ? 1 : 0;
  const safeName = teacherName || localized(language, "Teacher", "老师");

  if (sourceUnavailable) {
    return {
      stageKey,
      stageLabel: localized(language, "DATA UPDATING", "数据更新中"),
      title: localized(language, "Your latest progress is being updated", "最新成长进度正在更新"),
      body: localized(
        language,
        "Your completed work is safe. You can continue with any available task while we refresh your latest results.",
        "已完成的内容不会丢失，你可以先继续当前可用任务。",
      ),
      action: {
        label: localized(language, "SAFE NEXT STEP", "现在可以做"),
        title: localized(language, "Review your current tasks", "查看当前任务"),
        prompt: localized(
          language,
          "There is no need to repeat any completed work. Check back shortly for the latest progress.",
          "无需重复已经完成的内容，稍后回来即可查看最新进度。",
        ),
        task: null,
        buttonLabel: null,
        tone: "waiting",
      },
      mood: "wave",
    };
  }

  const task = selectGrowthTipTask(tasks);
  if (!task) {
    const hasCompletedTasks = tasks.some((item) => item.status === "completed");
    return {
      stageKey,
      stageLabel: stage.range[languageIndex],
      title: hasCompletedTasks
        ? localized(language, `${safeName}, you’re all caught up for now`, `${safeName}，当前任务都已完成`)
        : stage.title[languageIndex](safeName),
      body: hasCompletedTasks
        ? localized(
          language,
          "Nice work. There is nothing you need to handle right now. Keep your steady classroom routine going.",
          "做得很好！当前没有需要处理的内容，继续保持稳定的课堂节奏就好。",
        )
        : stage.body[languageIndex],
      action: {
        label: localized(language, "KEEP THE MOMENTUM", "继续保持"),
        title: localized(language, "No urgent action right now", "当前没有需要立即处理的任务"),
        prompt: localized(
          language,
          "When a useful next step is ready, I’ll place it here for you.",
          "有适合继续完成的内容时，我会把下一步放在这里。",
        ),
        task: null,
        buttonLabel: null,
        tone: "complete",
      },
      mood: hasCompletedTasks ? "cheer" : "wave",
    };
  }

  const basePrompt = teacherSafePrompt(task, language);
  const taskStatus = statusCopy(task, basePrompt, language);
  const taskDueSoon = dueSoon(task, now);

  return {
    stageKey,
    stageLabel: stage.range[languageIndex],
    title: stage.title[languageIndex](safeName),
    body: stage.body[languageIndex],
    action: {
      ...taskStatus,
      title: task.name,
      task,
      due: taskDueSoon?.label || null,
      dueAt: taskDueSoon?.dateTime || null,
    },
    mood: taskStatus.mood,
  };
}
