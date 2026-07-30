import { personalizedTaskTemplates } from "./data/tasks/personalized-task-templates.js";
import { localizedTaskDue } from "./task-due.js";

const personalizedOwnedLocalizationKeys = [
  "stage",
  "cautions",
  "material",
  "scoreReason",
  "recoveryPath",
];

const personalizedTaskEnglish = Object.fromEntries(
  personalizedTaskTemplates.map((task) => [task.id, task]),
);

function localizedOwnedContent(localized) {
  return Object.fromEntries(
    personalizedOwnedLocalizationKeys.flatMap((key) => (
      localized[key] === undefined ? [] : [[key, localized[key]]]
    )),
  );
}

export function localizePersonalizedTask(
  task,
  language,
  localizedChinese = {},
  localizedSignal = {},
) {
  const localizationId = task.localizationId || task.id;
  const localized = language === "zh"
    ? localizedChinese
    : personalizedTaskEnglish[localizationId] || {};
  const duration = language === "zh" && /^\d+(?:\.\d+)? min$/.test(task.duration)
    ? task.duration.replace(/ min$/, " 分钟")
    : task.duration;
  const shiwenContent = {
    reason: task.reason,
    result: task.result,
    standard: task.standard,
    value: task.value,
    steps: task.steps,
  };

  return {
    ...task,
    ...localizedOwnedContent(localized),
    ...(language === "zh" ? localizedSignal : {}),
    ...shiwenContent,
    sourceStage: task.sourceStage || task.stage,
    duration,
    due: language === "zh"
      ? localizedTaskDue(task, localized.due) || task.due
      : task.due,
    priority: task.priority,
    stage: language === "zh" ? localized.stage || task.stage : task.stage,
  };
}
