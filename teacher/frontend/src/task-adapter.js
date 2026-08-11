import { fixedTaskCatalog } from "./live-catalog.js";
import { personalizedTaskTemplates } from "./data/tasks/personalized-task-templates.js";
import { publicAsset } from "./public-assets.js";
import { formatTaskDueAt } from "./task-due.js";

const personalizedTaskCodeToRouteId = Object.fromEntries(
  personalizedTaskTemplates.flatMap((task) => (
    task.templateId ? [[task.templateId, task.id]] : []
  )),
);

export const taskCodeToRouteId = {
  ...Object.fromEntries(fixedTaskCatalog.map((task) => [task.taskCode, task.id])),
  ...personalizedTaskCodeToRouteId,
};

const routeIdToCatalog = new Map(
  [...fixedTaskCatalog, ...personalizedTaskTemplates].map((task) => [task.id, task]),
);

const statusMap = {
  ASSIGNED: "available",
  VIEWED: "available",
  IN_PROGRESS: "started",
  SUBMITTED: "submitted",
  UNDER_REVIEW: "verifying",
  COMPLETED: "completed",
  FAILED: "retry_required",
  EXPIRED: "expired",
  WAIVED: "waived",
  CANCELLED: "cancelled",
};

const stageMap = {
  FOUNDATION: "Day 1-7",
  DAY_1_7: "Day 1-7",
  INTEGRATION: "Day 8-14",
  DAY_8_14: "Day 8-14",
  ADVANCE: "Day 15-30",
  DAY_15_30: "Day 15-30",
};

const fixedTaskRouteIds = Object.entries(taskCodeToRouteId)
  .filter(([taskCode]) => /^G\d{2}$/.test(taskCode))
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([, routeId]) => routeId);

const fixedTaskCatalogById = new Map(
  fixedTaskCatalog.map((task) => [task.id, task]),
);

const growthStageIndex = {
  "Day 1-7": 0,
  "Day 8-14": 1,
  "Day 15-30": 2,
};

const externalCourseTaskCodes = new Set(["G02", "G05", "G06", "G07", "G08", "G09"]);

function currentGrowthStageIndex(campDay) {
  const day = Number(campDay);
  if (!Number.isFinite(day) || day < 8) return 0;
  return day >= 15 ? 2 : 1;
}

function inferMethod(context) {
  if (context.execution?.contentStatus !== "READY") return "content_pending";
  if (context.taskCode === "G01") return "profile_credentials";
  if (context.taskCode === "G04") return "readiness_photo";
  if (externalCourseTaskCodes.has(context.taskCode)) return "external_course";
  if (context.steps.some((step) => (
    step.type === "CUSTOM" && step.config?.kind === "TEXT_SUBMISSION"
  ))) return "factual_response";
  const capabilities = new Set(context.capabilities);
  if (capabilities.has("DEVICE_CHECK") && capabilities.has("UPLOAD")) return "readiness_photo";
  if (capabilities.has("VIDEO")) return "content_pending";
  if (capabilities.has("CHECKLIST")) return "learning_checklist";
  if (capabilities.has("DEVICE_CHECK")) return "device_check";
  if (capabilities.has("UPLOAD")) return "upload_review";
  return "content_pending";
}

function stepRole(step) {
  return typeof step.config?.role === "string" ? step.config.role : null;
}

function localizedItems(step) {
  return (step?.config?.items || []).map((item) => (
    typeof item === "string"
      ? { key: item, label: item }
      : item
  ));
}

function tesolReviewItem(status, sourceUpdatedAt) {
  return {
    id: "credential-status",
    type: "credential",
    label: "TESOL / teaching credential",
    labelZh: "TESOL / 教学资质",
    source: "Trusted external credential review",
    sourceZh: "教学资质审核状态",
    updatedAt: sourceUpdatedAt ? new Date(sourceUpdatedAt).toLocaleString("en-US") : "Updating",
    updatedAtZh: sourceUpdatedAt ? new Date(sourceUpdatedAt).toLocaleString("zh-CN") : "更新中",
    status: {
      APPROVED: "approved",
      IN_REVIEW: "reviewing",
      NEEDS_CHANGES: "update_required",
      WAITING: "waiting",
      UNAVAILABLE: "unavailable",
    }[status] || "unavailable",
  };
}

export function adaptTaskContext(context, g01Review) {
  const isPersonalized = context.kind === "PERSONALIZED_IMPROVEMENT";
  const catalogRouteId = taskCodeToRouteId[context.taskCode];
  const id = isPersonalized
    ? context.taskInstanceId
    : catalogRouteId || context.taskInstanceId;
  const catalogTask = routeIdToCatalog.get(catalogRouteId) || {};
  const status = statusMap[context.status] || "available";
  const minutes = context.display.estimatedMinutes;
  const dueAt = context.dueAt || context.assignment?.dueAt || null;
  const documentStep = context.steps.find((step) => step.type === "DOCUMENT");
  const checklistStep = context.steps.find((step) => step.type === "CHECKLIST");
  const videoSteps = context.steps.filter((step) => step.type === "VIDEO");
  const referenceVideo = checklistStep?.config?.referenceVideo;
  const progressByStep = Object.fromEntries((context.progress.steps || []).map((step) => [step.stepKey, step]));
  const externalStatusItems =
    context.taskCode === "G01"
      ? g01Review
        ? [
            tesolReviewItem(g01Review.tesolStatus, g01Review.freshness?.sourceUpdatedAt),
          ]
        : [
            tesolReviewItem("UNAVAILABLE", null),
          ]
      : [];
  const teacherSafeFacts = (context.assignment?.teacherSafeFacts || []).map((fact) => ({
    label: fact.label,
    labelZh: fact.labelZh || fact.label,
    value: fact.value,
    valueZh: fact.valueZh || fact.value,
  }));
  const relatedCourseFacts = (context.assignment?.relatedCourses || []).map((course) => ({
    label: course.label,
    labelZh: course.labelZh || course.label,
    value: course.summary,
    valueZh: course.summaryZh || course.summary,
    lessonId: course.lessonId,
    occurredAt: course.occurredAt,
  }));
  return {
    ...catalogTask,
    id,
    localizationId: isPersonalized ? catalogRouteId || null : id,
    backendId: context.taskInstanceId,
    taskCode: context.taskCode,
    stateVersion: context.stateVersion,
    backendStatus: context.status,
    backendContext: context,
    localizationId: catalogRouteId || id,
    name: context.content.title,
    shortName: context.content.title,
    contentLanguage: context.content.language,
    method: inferMethod(context),
    status,
    locked: false,
    taskCategory: isPersonalized ? "personalized" : "required",
    stage: isPersonalized
      ? "Personalized"
      : stageMap[context.display.stageKey] || catalogTask.stage || "Day 1-7",
    duration: minutes ? `${minutes} min` : "View anytime",
    due: formatTaskDueAt(dueAt) || "Available now",
    dueAt,
    priority: context.assignment?.priority || (context.display.points ? `${context.display.points} pts` : "No task points"),
    reason: context.content.why,
    value: context.content.outcome,
    result: context.content.whatToDo,
    standard: context.content.completionStandard,
    steps: checklistStep
      ? localizedItems(checklistStep).map((item) => item.label)
      : context.steps.length
        ? context.steps.map((step) => step.title)
        : [context.content.whatToDo],
    backendSteps: context.steps,
    backendProgressByStep: progressByStep,
    documentContent: documentStep?.config || null,
    documentStepKey: documentStep?.stepKey || null,
    documentCompleted: documentStep
      ? progressByStep[documentStep.stepKey]?.status === "COMPLETED"
      : false,
    videoChapters: videoSteps.length > 1
      ? videoSteps.map((step, index) => ({
          id: step.config.chapterId || step.stepKey,
          stepKey: step.stepKey,
          title: step.title,
          titleZh: step.config.titleZh || step.title,
          videoSrc: publicAsset(step.config.assetUrl) || null,
          mock: Boolean(step.config?.mock),
          order: index + 1,
        }))
      : null,
    videoSrc: videoSteps.length === 1
      ? publicAsset(videoSteps[0].config.assetUrl) || null
      : publicAsset(referenceVideo?.assetUrl) || null,
    videoDuration: videoSteps.length === 0
      ? Number(referenceVideo?.durationSeconds) || 0
      : undefined,
    referenceVideo: videoSteps.length === 0 && Boolean(referenceVideo?.assetUrl),
    videoStepKey: videoSteps.length === 1 ? videoSteps[0].stepKey : null,
    videoMock: videoSteps.length === 1 ? Boolean(videoSteps[0].config?.mock) : false,
    videoRequired: videoSteps.length > 0 && Boolean(checklistStep),
    progress: context.progress.percent,
    displayRank: context.display.sequence ?? 99,
    isPrimary: context.display.sequence === 1 || context.display.sequence === 2,
    dataOrigin: context.dataOrigin,
    signalFacts: [...teacherSafeFacts, ...relatedCourseFacts],
    relatedLessonIds: relatedCourseFacts.map((fact) => fact.lessonId),
    reminderNotificationId: context.assignment?.reminderNotificationId || null,
    externalStatusItems,
    externalStatusCopy: catalogTask.externalStatusCopy,
    contentPendingReason: context.execution?.pendingReason || null,
  };
}

export function sortTaskContexts(tasks) {
  return [...tasks].sort((left, right) => {
    if (left.taskCategory !== right.taskCategory) return left.taskCategory === "required" ? -1 : 1;
    return (left.displayRank ?? 99) - (right.displayRank ?? 99);
  });
}

export function composePresentationTasks(tasks) {
  return sortTaskContexts(
    tasks.filter((task) => task.taskCode !== "G00"),
  );
}

export function composeGrowthMapTasks(tasks, campDay) {
  const liveTasksById = new Map(
    tasks
      .filter((task) => task.taskCategory !== "personalized")
      .map((task) => [task.id, task]),
  );
  const completedStage = (stage) => {
    const expectedTaskIds = fixedTaskRouteIds.filter(
      (routeId) => fixedTaskCatalogById.get(routeId)?.stage === stage,
    );
    return expectedTaskIds.length > 0 && expectedTaskIds.every(
      (routeId) => liveTasksById.get(routeId)?.status === "completed",
    );
  };
  const openStageIndex = Math.max(
    currentGrowthStageIndex(campDay),
    completedStage("Day 1-7") ? 1 : 0,
    completedStage("Day 8-14") ? 2 : 0,
  );

  return fixedTaskRouteIds.map((routeId) => {
    const fallback = fixedTaskCatalogById.get(routeId);
    const liveTask = liveTasksById.get(routeId);
    const stage = liveTask?.stage || fallback?.stage || "Day 1-7";
    const stageIndex = growthStageIndex[stage] ?? 0;
    const assignmentMissing = !liveTask;
    const assignmentUnavailable = assignmentMissing && stageIndex <= openStageIndex;

    return {
      ...fallback,
      ...liveTask,
      id: routeId,
      stage,
      taskCategory: "required",
      locked: assignmentMissing || stageIndex > openStageIndex,
      assignmentMissing,
      assignmentUnavailable,
      status: assignmentUnavailable ? "sync_pending" : liveTask?.status || "available",
    };
  });
}
