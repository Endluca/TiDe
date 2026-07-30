export const courseAbilityTagCatalog = [
  {
    taskCode: "G08",
    taskId: "cocos-training",
    label: {
      en: "Cocos course ready",
      zh: "Cocos 课程准备",
    },
  },
  {
    taskCode: "G09",
    taskId: "set-fundamentals",
    label: {
      en: "SET course ready",
      zh: "SET 课程准备",
    },
  },
];

export function courseAbilityTagForTask(task, language = "en") {
  const definition = courseAbilityTagCatalog.find((item) =>
    item.taskCode === task?.taskCode || item.taskId === task?.id
  );

  if (!definition) return null;

  return {
    ...definition,
    label: definition.label[language] || definition.label.en,
  };
}

export function buildCourseAbilityTags(tasks = [], language = "en") {
  return courseAbilityTagCatalog.map((definition) => {
    const task = tasks.find((item) =>
      item.taskCode === definition.taskCode || item.id === definition.taskId
    );
    const state = !task
      ? "syncing"
      : task.backendStatus === "COMPLETED" || task.status === "completed"
        ? "earned"
        : "pending";

    return {
      ...definition,
      label: definition.label[language] || definition.label.en,
      state,
    };
  });
}
