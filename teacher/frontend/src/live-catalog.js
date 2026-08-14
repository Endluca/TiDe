const localized = (en, zh) => ({ en, zh });

export const fixedTaskCatalog = [
  { taskCode: "G01", id: "profile-credentials", name: "Profile & Credentials Completion", stage: "Day 1-7" },
  { taskCode: "G02", id: "platform-policies", name: "Platform Policies", stage: "Day 1-7" },
  { taskCode: "G03", id: "student-types", name: "How to handle different types of students", stage: "Day 1-7" },
  { taskCode: "G04", id: "lesson-preparation", name: "Lesson Preparation", stage: "Day 1-7" },
  { taskCode: "G05", id: "ttp-orientation", name: "TTP Orientation", stage: "Day 8-14" },
  { taskCode: "G06", id: "me-culture", name: "ME Culture & PARSNIP", stage: "Day 8-14" },
  { taskCode: "G07", id: "reliability-training", name: "Reliability Training", stage: "Day 8-14" },
  { taskCode: "G08", id: "cocos-training", name: "Cocos Course Training", stage: "Day 15-30" },
  { taskCode: "G09", id: "set-fundamentals", name: "SET Teaching Fundamentals", stage: "Day 15-30" },
];

export const stageDescriptions = [
  {
    id: "first-lessons",
    range: "Day 1-7",
    releaseDay: 1,
    number: "01",
    title: "Ready for your first lessons",
    description: "Set up the essentials and build a calm, consistent pre-class routine.",
  },
  {
    id: "build-rhythm",
    range: "Day 8-14",
    releaseDay: 8,
    number: "02",
    title: "Build your teaching rhythm",
    description: "Strengthen platform habits and practise key lesson moments.",
  },
  {
    id: "grow-skills",
    range: "Day 15-30",
    releaseDay: 15,
    number: "03",
    title: "Grow course-ready skills",
    description: "Complete focused training and build course readiness.",
  },
];

export const dimensionCatalog = [
  {
    id: "feedback",
    sourceKey: "userFeedback",
    title: localized("User Feedback", "用户反馈"),
    englishTitle: "User Feedback",
    unbounded: true,
  },
  {
    id: "reliability",
    sourceKey: "reliability",
    title: localized("Reliability", "上课稳定度"),
    englishTitle: "Reliability",
    unbounded: true,
  },
  {
    id: "quality",
    sourceKey: "classQuality",
    title: localized("Hardware Quality", "硬件质量"),
    englishTitle: "Hardware Quality",
    unbounded: true,
  },
  {
    id: "availability",
    sourceKey: "capacity",
    title: localized("Teaching Availability", "有效供给"),
    englishTitle: "Teaching Availability",
    cap: 10,
  },
  {
    id: "required-tasks",
    sourceKey: "newTeacherTask",
    title: localized("Required Tasks", "必修任务"),
    englishTitle: "Required Tasks",
    cap: 30,
  },
];

export const scoreMilestones = {
  total: 200,
  graduation: 100,
  gold: 200,
};

export const localizeCatalogValue = (value, language) => {
  if (value && typeof value === "object" && "en" in value && "zh" in value) {
    return language === "zh" ? value.zh : value.en;
  }
  return value;
};
