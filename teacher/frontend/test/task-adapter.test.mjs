import assert from "node:assert/strict";
import test from "node:test";
import {
  adaptTaskContext,
  composePresentationTasks,
  composeGrowthMapTasks,
  sortTaskContexts,
  taskCodeToRouteId,
} from "../src/task-adapter.js";
import { localizePersonalizedTask } from "../src/task-localization.js";
import { taskNeedsStart } from "../src/task-status.js";
import { formatTaskDueAt } from "../src/task-due.js";
import { fixedTaskCatalog } from "../src/live-catalog.js";

const context = (overrides = {}) => ({
  taskInstanceId: "00000000-0000-4000-8000-000000000001",
  taskCode: "G02",
  kind: "FIXED_GROWTH",
  status: "IN_PROGRESS",
  stateVersion: 2,
  dataOrigin: "MOCK",
  dueAt: null,
  content: {
    title: "[Mock] Policy training",
    why: "why",
    whatToDo: "watch and answer",
    completionStandard: "pass",
    outcome: "ready",
    language: "en",
  },
  display: { stageKey: "FOUNDATION", sequence: 2, points: 2, estimatedMinutes: 8 },
  capabilities: ["DOCUMENT"],
  steps: [
    {
      stepKey: "g02-policy-document",
      type: "DOCUMENT",
      title: "Read Overseas NT Policies",
      config: {
        sourceTitle: "Overseas NT Policies",
        contentVersion: "2026-07-24-overseas-nt-policies-v1",
        contentHash: "6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c",
      },
    },
  ],
  progress: {
    percent: 50,
    steps: [
      {
        stepKey: "g02-policy-document",
        status: "IN_PROGRESS",
        percent: 50,
        details: { readPercent: 50, reachedEnd: false },
      },
    ],
  },
  execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
  assignment: null,
  ...overrides,
});

test("maps every fixed task code to a stable frontend route", () => {
  assert.deepEqual(Object.keys(taskCodeToRouteId).filter((code) => /^G\d{2}$/.test(code)), [
    "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09",
  ]);
  assert.equal(taskCodeToRouteId.G02, "platform-policies");
  assert.equal(taskCodeToRouteId.G03, "student-types");
  assert.equal(taskCodeToRouteId.G04, "lesson-preparation");
  assert.equal(
    fixedTaskCatalog.find((task) => task.taskCode === "G04")?.name,
    "Lesson Preparation",
  );
  assert.equal(taskCodeToRouteId.G05, "ttp-orientation");
  assert.equal(taskCodeToRouteId.G06, "me-culture");
  assert.equal(taskCodeToRouteId.G07, "reliability-training");
});

test("adapts live task state and capabilities instead of localStorage state", () => {
  const task = adaptTaskContext(context());
  assert.equal(task.id, "platform-policies");
  assert.equal(task.status, "started");
  assert.equal(task.method, "document_reading");
  assert.equal(task.progress, 50);
  assert.equal(task.stateVersion, 2);
  assert.equal(task.documentReadPercent, 50);
  assert.equal(task.documentReachedEnd, false);
});

test("routes G02 to the current native document and projects completion", () => {
  const task = adaptTaskContext(context({
    capabilities: ["DOCUMENT"],
    steps: [
      {
        stepKey: "g02-policy-document",
        type: "DOCUMENT",
        title: "Read Overseas NT Policies",
        config: {
          sourceTitle: "Overseas NT Policies",
          sections: [{ key: "attendance", title: "Attendance", items: ["Monitor Attendance Report."] }],
        },
      },
    ],
    progress: {
      percent: 50,
      steps: [{
        stepKey: "g02-policy-document",
        status: "COMPLETED",
        percent: 100,
        details: { readPercent: 100, reachedEnd: true },
      }],
    },
  }));

  assert.equal(task.method, "document_reading");
  assert.equal(task.documentCompleted, true);
  assert.equal(task.documentReadPercent, 100);
  assert.equal(task.documentReachedEnd, true);
});

test("maps the blacklist custom text step to the factual response flow", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-FB-BLACKLIST",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: ["CUSTOM"],
    steps: [
      {
        stepKey: "teacher-factual-response",
        type: "CUSTOM",
        title: "Write and submit a factual response",
        config: {
          role: "FACTUAL_RESPONSE",
          kind: "TEXT_SUBMISSION",
          minCharacters: 20,
          maxCharacters: 2000,
        },
      },
    ],
  }));

  assert.equal(task.id, "00000000-0000-4000-8000-000000000001");
  assert.equal(task.method, "factual_response");
});

test("routes a ready personalized teaching-environment variant to the camera flow", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-FB-NEGATIVE",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: ["UPLOAD"],
    steps: [
      {
        stepKey: "p-fb-negative-environment-photo",
        type: "UPLOAD",
        title: "Retake teaching-view photo",
        config: {
          role: "ENVIRONMENT_PHOTO",
          reviewProfile: "TEACHING_ENVIRONMENT_V1",
          captureOnly: true,
        },
      },
    ],
  }));

  assert.equal(task.method, "personalized_environment_photo");
});

test("does not route an unrelated personalized upload to the environment camera flow", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-FB-NEGATIVE",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: ["UPLOAD"],
    steps: [
      {
        stepKey: "generic-proof",
        type: "UPLOAD",
        title: "Upload proof",
        config: { role: "COMPLETION_PROOF", captureOnly: false },
      },
    ],
  }));

  assert.equal(task.method, "upload_review");
});

test("requires ready personalized context and the exact environment review profile", () => {
  const environmentStep = {
    stepKey: "p-fb-negative-environment-photo",
    type: "UPLOAD",
    title: "Retake teaching-view photo",
    config: {
      role: "ENVIRONMENT_PHOTO",
      reviewProfile: "TEACHING_ENVIRONMENT_V1",
      captureOnly: true,
    },
  };
  const variants = [
    {
      kind: "FIXED_GROWTH",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      expected: "upload_review",
    },
    {
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "PENDING", contentVersion: "1", pendingReason: "WAITING" },
      expected: "content_pending",
    },
    {
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      step: { ...environmentStep, config: { ...environmentStep.config, role: "COMPLETION_PROOF" } },
      expected: "upload_review",
    },
    {
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      step: { ...environmentStep, config: { ...environmentStep.config, reviewProfile: "OTHER_PROFILE" } },
      expected: "upload_review",
    },
    {
      taskCode: "P-FB-COMPLAINT",
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      expected: "upload_review",
    },
    {
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      step: { ...environmentStep, stepKey: "another-environment-photo" },
      expected: "upload_review",
    },
    {
      kind: "PERSONALIZED_IMPROVEMENT",
      execution: { contentStatus: "READY", contentVersion: "1", pendingReason: null },
      step: { ...environmentStep, config: { ...environmentStep.config, captureOnly: false } },
      expected: "upload_review",
    },
  ];

  for (const variant of variants) {
    const task = adaptTaskContext(context({
      taskCode: variant.taskCode || "P-FB-NEGATIVE",
      kind: variant.kind,
      capabilities: ["UPLOAD"],
      execution: variant.execution,
      steps: [variant.step || environmentStep],
    }));
    assert.equal(task.method, variant.expected);
  }
});

test("routes G06 to the Kuozhi external course instead of the local player", () => {
  const task = adaptTaskContext(context({
    taskCode: "G06",
    capabilities: ["VIDEO"],
    steps: [
      {
        stepKey: "g07-me-culture-video",
        type: "VIDEO",
        title: "ME Culture Training",
        config: {
          assetUrl: "/videos/g07-me-culture/v1/01.mp4",
          chapterId: "g07-me-culture-video",
          durationSeconds: 255,
        },
      },
      {
        stepKey: "g07-parsnip-thailand-1",
        type: "VIDEO",
        title: "Global PARSNIP · Thailand Part 1",
        config: {
          assetUrl: "/videos/g07-me-culture/v1/02.mp4",
          chapterId: "g07-parsnip-thailand-1",
          durationSeconds: 404,
        },
      },
    ],
  }));
  assert.equal(task.method, "external_course");
});

test("routes ready G03 to the Kuozhi external course", () => {
  const task = adaptTaskContext(context({
    taskCode: "G03",
    execution: {
      contentStatus: "READY",
      contentVersion: "2026-08-12-student-types-kuozhi-v1",
      pendingReason: null,
    },
  }));

  assert.equal(task.method, "external_course");
});

test("routes ready P-REL-ATTENDANCE to the mapped Kuozhi course", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-REL-ATTENDANCE",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: [],
    steps: [],
    execution: {
      contentStatus: "READY",
      contentVersion: "2026-08-22-reliability-course-595-v1",
      pendingReason: null,
    },
  }));

  assert.equal(task.method, "external_course");
});

test("routes ready P-REL-MEMO to the embedded document reader", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-REL-MEMO",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: ["DOCUMENT"],
    steps: [
      {
        stepKey: "p-rel-memo-document",
        type: "DOCUMENT",
        title: "Read Lesson Memo Rules",
        config: {
          documentCode: "lesson-memo-rules",
          contentVersion: "2026-07-24-lesson-memo-rules-v1",
          contentHash: "43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51",
        },
      },
    ],
    execution: {
      contentStatus: "READY",
      contentVersion: "2026-07-24-lesson-memo-rules-v1",
      pendingReason: null,
    },
  }));

  assert.equal(task.method, "document_reading");
  assert.equal(task.documentStepKey, "p-rel-memo-document");
});

test("routes G05 to Kuozhi even while legacy checklist steps remain in the backend context", () => {
  const task = adaptTaskContext(context({
    taskCode: "G05",
    capabilities: ["VIDEO", "CHECKLIST"],
    steps: [
      {
        stepKey: "g06-ttp-orientation-video",
        type: "VIDEO",
        title: "Teacher Tie-up Program",
        config: {
          assetUrl: "/videos/g06-ttp-orientation/v1/01.mp4",
          durationSeconds: 314,
          mock: true,
        },
      },
      {
        stepKey: "g06-learning-checklist",
        type: "CHECKLIST",
        title: "Complete the TTP learning checklist",
        config: {
          items: [{ key: "item-1", label: "Understand the TTP commitment" }],
        },
      },
    ],
  }));

  assert.equal(task.method, "external_course");
});

test("keeps G05 on Kuozhi when an older reference-video config is still present", () => {
  const task = adaptTaskContext(context({
    taskCode: "G05",
    capabilities: ["CHECKLIST"],
    steps: [
      {
        stepKey: "g06-learning-checklist",
        type: "CHECKLIST",
        title: "Complete the TTP learning checklist",
        config: {
          items: [{ key: "item-1", label: "Understand TTP" }],
          referenceVideo: {
            assetUrl: "/videos/g06-ttp-orientation/v1/01.mp4",
            durationSeconds: 314,
          },
        },
      },
    ],
  }));

  assert.equal(task.method, "external_course");
});

test("routes G09 to Kuozhi when its official course mapping is ready", () => {
  const task = adaptTaskContext(context({
    taskCode: "G09",
    capabilities: ["VIDEO"],
    steps: [
      { stepKey: "legacy-video", type: "VIDEO", title: "Legacy video", config: {} },
    ],
  }));

  assert.equal(task.method, "external_course");
});

test("keeps G09 pending while Kuozhi has not published a course id", () => {
  const task = adaptTaskContext(context({
    taskCode: "G09",
    capabilities: [],
    steps: [],
    execution: {
      contentStatus: "PENDING",
      contentVersion: "2026-08-05",
      pendingReason: "KUOZHI_G09_COURSE_MAPPING_PENDING",
    },
  }));

  assert.equal(task.method, "content_pending");
});

test("does not fall back to a local player for an unmapped training task", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-FUTURE-TRAINING",
    kind: "PERSONALIZED_IMPROVEMENT",
    capabilities: ["VIDEO"],
    steps: [
      { stepKey: "video", type: "VIDEO", title: "Video", config: {} },
    ],
  }));

  assert.equal(task.method, "content_pending");
});

test("maps the backend ASSIGNED state to an available task", () => {
  const task = adaptTaskContext(context({ status: "ASSIGNED", stateVersion: 1 }));
  assert.equal(task.status, "available");
  assert.equal(task.backendStatus, "ASSIGNED");
});

test("maps both supported backend stage-key formats", () => {
  const integration = adaptTaskContext(context({
    taskCode: "G05",
    display: { stageKey: "DAY_8_14", sequence: 5, points: 3, estimatedMinutes: 15 },
  }));
  const advance = adaptTaskContext(context({
    taskCode: "G08",
    display: { stageKey: "ADVANCE", sequence: 8, points: 5, estimatedMinutes: 20 },
  }));

  assert.equal(integration.stage, "Day 8-14");
  assert.equal(advance.stage, "Day 15-30");
});

test("starts newly assigned or viewed tasks before saving progress", () => {
  assert.equal(taskNeedsStart("ASSIGNED"), true);
  assert.equal(taskNeedsStart("VIEWED"), true);
  assert.equal(taskNeedsStart("IN_PROGRESS"), false);
  assert.equal(taskNeedsStart("AVAILABLE"), false);
});

test("maps G01 with TESOL as its only external status", () => {
  const task = adaptTaskContext(
    context({
      taskCode: "G01",
      capabilities: ["CHECKLIST", "UPLOAD"],
      steps: [
        {
          stepKey: "g01-essay-confirmation",
          type: "CHECKLIST",
          title: "Essay confirmation",
          config: { role: "TESOL_ESSAY", items: [{ key: "essay-completed", label: "Essay completed" }] },
        },
        {
          stepKey: "g01-completion-proof",
          type: "UPLOAD",
          title: "Completion proof",
          config: { role: "COMPLETION_PROOF" },
        },
      ],
    }),
    {
      // Legacy callers may still send this field; the adapter must ignore it.
      selfIntroStatus: "APPROVED",
      tesolStatus: "IN_REVIEW",
      freshness: { sourceUpdatedAt: "2026-07-22T08:00:00.000Z" },
    },
  );
  assert.equal(task.method, "profile_credentials");
  assert.deepEqual(task.externalStatusItems.map((item) => item.status), ["reviewing"]);
  assert.deepEqual(task.externalStatusItems.map((item) => item.type), ["credential"]);
  assert.equal(task.backendContext.steps.length, 2);
  assert.equal(task.backendContext.capabilities.includes("UPLOAD"), true);
});

test("does not fall back to Mock G01 review results when the trusted source is unavailable", () => {
  const task = adaptTaskContext(context({ taskCode: "G01" }));

  assert.deepEqual(task.externalStatusItems.map((item) => item.status), ["unavailable"]);
  assert.equal(task.externalStatusItems.some((item) => item.actionGuideId), false);
});

test("sorts required tasks before personalized assignments", () => {
  const required = { taskCategory: "required", displayRank: 10 };
  const personalized = { taskCategory: "personalized", displayRank: 1 };
  assert.deepEqual(sortTaskContexts([personalized, required]), [required, personalized]);
});

test("displays assignments from the backend regardless of data origin without filling local tasks", () => {
  const realTask = adaptTaskContext(context({ dataOrigin: "REAL" }));
  const mockTask = adaptTaskContext(context({
    taskInstanceId: "mock-assignment",
    dataOrigin: "MOCK",
  }));

  assert.deepEqual(composePresentationTasks([mockTask, realTask]), [mockTask, realTask]);
  assert.deepEqual(composePresentationTasks([]), []);
});

test("maps teacher-safe facts and related courses from the backend task context", () => {
  const task = adaptTaskContext(context({
    taskCode: "NT-Q03",
    kind: "PERSONALIZED_IMPROVEMENT",
    assignment: {
      assignmentId: "personalized-assignment",
      teacherSafeReason: "A recent class needs attention.",
      teacherSafeFacts: [
        {
          label: "What we noticed",
          labelZh: "我们注意到",
          value: "The connection became unstable.",
          valueZh: "网络连接出现波动。",
        },
      ],
      relatedCourses: [
        {
          lessonId: "lesson-001",
          label: "Related class",
          labelZh: "关联课程",
          summary: "Jul 21, 18:00 · connection issue",
          summaryZh: "7 月 21 日 18:00 · 网络问题",
          occurredAt: "2026-07-21T10:00:00.000Z",
        },
      ],
      reminderNotificationId: "notification-001",
      priority: "P2",
      dueAt: null,
    },
  }));

  assert.equal(task.id, "00000000-0000-4000-8000-000000000001");
  assert.equal(task.name, "[Mock] Policy training");
  assert.deepEqual(task.relatedLessonIds, ["lesson-001"]);
  assert.equal(task.signalFacts.length, 2);
  assert.equal(task.signalFacts[0].labelZh, "我们注意到");
  assert.equal(task.signalFacts[1].valueZh, "7 月 21 日 18:00 · 网络问题");
  assert.equal(task.reminderNotificationId, "notification-001");
});

test("keeps all four Shiwen content fields when localizing a personalized task", () => {
  const task = adaptTaskContext(context({
    taskCode: "P-REL-ATTENDANCE",
    kind: "PERSONALIZED_IMPROVEMENT",
    content: {
      title: "出席问题",
      why: "共命中 1 节课程，该课程出现迟到。",
      whatToDo: "Watch two short chapters and pass the quick check.",
      completionStandard: "Watch both chapters and score 80% or higher.",
      outcome: "Build a simple pre-class routine.",
      language: "zh-CN",
    },
  }));
  const localizedZh = localizePersonalizedTask(task, "zh", {
    stage: "个性化任务",
    value: "建立简单的课前准备习惯，也知道课程可能受影响时应该怎么做。",
    result: "看完两段短视频并通过小测。",
    standard: "看完两段内容，小测达到 80 分。",
  });
  const localizedEn = localizePersonalizedTask(task, "en");

  assert.equal(task.localizationId, "attendance-reliability-refresher");
  assert.equal(localizedZh.name, "出席问题");
  assert.equal(localizedZh.reason, "共命中 1 节课程，该课程出现迟到。");
  assert.equal(localizedZh.value, "Build a simple pre-class routine.");
  assert.equal(localizedZh.result, "Watch two short chapters and pass the quick check.");
  assert.equal(localizedZh.standard, "Watch both chapters and score 80% or higher.");
  assert.equal(localizedZh.duration, "8 分钟");
  assert.equal(localizedEn.name, "出席问题");
  assert.equal(localizedEn.reason, "共命中 1 节课程，该课程出现迟到。");
  assert.equal(localizedEn.value, "Build a simple pre-class routine.");
  assert.equal(localizedEn.result, "Watch two short chapters and pass the quick check.");
  assert.equal(localizedEn.standard, "Watch both chapters and score 80% or higher.");
  assert.equal(localizedEn.duration, "8 min");
});

test("uses the backend personalized dueAt instead of local fixed due copy", () => {
  const dueAt = "2026-07-30T12:15:00.000Z";
  const task = adaptTaskContext(context({
    taskCode: "NT-Q03",
    kind: "PERSONALIZED_IMPROVEMENT",
    dueAt,
    assignment: {
      assignmentId: "personalized-assignment",
      teacherSafeReason: "A recent class needs attention.",
      teacherSafeFacts: [],
      relatedCourses: [],
      reminderNotificationId: null,
      priority: "P2",
      dueAt: "2026-08-01T12:15:00.000Z",
    },
  }));

  assert.equal(task.dueAt, dueAt);
  assert.equal(task.due, formatTaskDueAt(dueAt));
});

test("renders G04 as photo and courseware preparation without a device status item", () => {
  const task = adaptTaskContext(context({
    taskCode: "G04",
    capabilities: ["DEVICE_CHECK", "UPLOAD", "CHECKLIST"],
    steps: [
      { stepKey: "device-check", type: "DEVICE_CHECK", title: "Check", config: {} },
      { stepKey: "environment-photo", type: "UPLOAD", title: "Photo", config: {} },
      { stepKey: "courseware", type: "CHECKLIST", title: "Courseware", config: {} },
    ],
    progress: {
      percent: 0,
      steps: [{ stepKey: "device-check", status: "NOT_STARTED", percent: 0 }],
    },
  }));

  assert.equal(task.method, "readiness_photo");
  assert.deepEqual(task.externalStatusItems, []);
});

test("keeps current tasks and removes retired G00 history from teacher-facing tasks", () => {
  const g04 = adaptTaskContext(context({ taskCode: "G04", dataOrigin: "REAL" }));
  const g00 = adaptTaskContext(context({ taskCode: "G00", dataOrigin: "REAL" }));
  const g03 = adaptTaskContext(context({ taskCode: "G03", dataOrigin: "REAL" }));

  assert.deepEqual(
    composePresentationTasks([g04, g00, g03]).map((task) => task.taskCode),
    ["G04", "G03"],
  );
});

test("uses backend task content without replacing it with local presentation copy", () => {
  const task = adaptTaskContext(context({
    content: {
      title: "Backend title",
      why: "Backend reason",
      whatToDo: "Backend action",
      completionStandard: "Backend standard",
      outcome: "Backend outcome",
      language: "en",
    },
  }));

  assert.equal(task.name, "Backend title");
  assert.equal(task.reason, "Backend reason");
  assert.equal(task.result, "Backend action");
  assert.equal(task.standard, "Backend standard");
  assert.equal(task.value, "Backend outcome");
  assert.equal(task.contentLanguage, "en");
});

test("keeps backend G05 course copy without a local publication fallback", () => {
  const task = adaptTaskContext(context({
    taskCode: "G05",
    content: {
      title: "Database G05 title",
      why: "Database G05 reason",
      whatToDo: "Database G05 action",
      completionStandard: "Database G05 standard",
      outcome: "Database G05 outcome",
      language: "en",
    },
  }));

  assert.equal(task.name, "Database G05 title");
  assert.equal(task.reason, "Database G05 reason");
  assert.equal(task.result, "Database G05 action");
  assert.equal(task.standard, "Database G05 standard");
  assert.equal(task.value, "Database G05 outcome");
});

test("keeps backend G08 course copy without a local publication fallback", () => {
  const task = adaptTaskContext(context({
    taskCode: "G08",
    content: {
      title: "Database G08 title",
      why: "Database G08 reason",
      whatToDo: "Database G08 action",
      completionStandard: "Database G08 standard",
      outcome: "Database G08 outcome",
      language: "en",
    },
  }));

  assert.equal(task.name, "Database G08 title");
  assert.equal(task.shortName, "Database G08 title");
  assert.equal(task.reason, "Database G08 reason");
  assert.equal(task.result, "Database G08 action");
  assert.equal(task.standard, "Database G08 standard");
  assert.equal(task.value, "Database G08 outcome");
});

test("formats the Global Communicator course duration for teachers", () => {
  const task = adaptTaskContext(context({
    taskCode: "G08",
    display: {
      stageKey: "ADVANCE",
      sequence: 8,
      points: 5,
      estimatedMinutes: 155,
    },
  }));

  assert.equal(task.duration, "2 hr 35 min");
});

test("keeps the nine merged-task map checkpoints while locking future stages by camp day", () => {
  const stages = {
    G01: "FOUNDATION",
    G02: "FOUNDATION",
    G03: "FOUNDATION",
    G04: "DAY_1_7",
    G05: "DAY_8_14",
    G06: "INTEGRATION",
    G07: "DAY_8_14",
    G08: "ADVANCE",
    G09: "DAY_15_30",
  };
  const liveTasks = Object.entries(stages).map(([taskCode, stageKey], index) =>
    adaptTaskContext(context({
      taskCode,
      status: "ASSIGNED",
      display: { stageKey, sequence: index + 1, points: 1, estimatedMinutes: 10 },
    })),
  );

  const dayFour = composeGrowthMapTasks(liveTasks, 4);
  const dayEight = composeGrowthMapTasks(liveTasks, 8);
  const dayFifteen = composeGrowthMapTasks(liveTasks, 15);

  assert.equal(dayFour.length, 9);
  assert.deepEqual(
    ["Day 1-7", "Day 8-14", "Day 15-30"].map(
      (stage) => dayFour.filter((task) => task.stage === stage).length,
    ),
    [4, 3, 2],
  );
  assert.equal(dayFour.find((task) => task.id === "student-types").locked, false);
  assert.equal(dayFour.find((task) => task.id === "ttp-orientation").locked, true);
  assert.ok(dayFour.filter((task) => task.stage === "Day 8-14").every((task) => task.locked));
  assert.ok(dayEight.filter((task) => task.stage === "Day 8-14").every((task) => !task.locked));
  assert.ok(dayEight.filter((task) => task.stage === "Day 15-30").every((task) => task.locked));
  assert.ok(dayFifteen.every((task) => !task.locked));
});

test("unlocks the next growth stage early after the previous stage is complete", () => {
  const stages = {
    G01: "FOUNDATION",
    G02: "DAY_1_7",
    G03: "FOUNDATION",
    G04: "DAY_1_7",
    G05: "DAY_8_14",
    G06: "INTEGRATION",
    G07: "DAY_8_14",
    G08: "ADVANCE",
    G09: "DAY_15_30",
  };
  const tasks = Object.entries(stages).map(([taskCode, stageKey], index) =>
    adaptTaskContext(context({
      taskCode,
      status: index < 4 ? "COMPLETED" : "ASSIGNED",
      display: { stageKey, sequence: index + 1, points: 1, estimatedMinutes: 10 },
    })),
  );

  const afterFoundation = composeGrowthMapTasks(tasks, 4);
  assert.ok(afterFoundation
    .filter((task) => task.stage === "Day 8-14")
    .every((task) => !task.locked));
  assert.ok(afterFoundation
    .filter((task) => task.stage === "Day 15-30")
    .every((task) => task.locked));

  const afterIntegration = composeGrowthMapTasks(
    tasks.map((task) => (
      task.stage === "Day 8-14" ? { ...task, status: "completed" } : task
    )),
    4,
  );
  assert.ok(afterIntegration
    .filter((task) => task.stage === "Day 15-30")
    .every((task) => !task.locked));
});

test("keeps missing assignments visible without inventing a task state", () => {
  const foundationTasks = ["G01", "G02", "G03", "G04"].map((taskCode, index) =>
    adaptTaskContext(context({
      taskCode,
      status: "ASSIGNED",
      display: { stageKey: "FOUNDATION", sequence: index + 1, points: 1, estimatedMinutes: 10 },
    })),
  );

  const mapTasks = composeGrowthMapTasks(foundationTasks, 4);
  const missingIntegrationTask = mapTasks.find((task) => task.id === "me-culture");

  assert.equal(mapTasks.length, 9);
  assert.equal(missingIntegrationTask.assignmentMissing, true);
  assert.equal(missingIntegrationTask.locked, true);
  assert.equal(missingIntegrationTask.assignmentUnavailable, false);

  const currentStageMissing = composeGrowthMapTasks(
    foundationTasks.filter((task) => task.id !== "lesson-preparation"),
    4,
  )
    .find((task) => task.id === "lesson-preparation");
  assert.equal(currentStageMissing.assignmentUnavailable, true);
  assert.equal(currentStageMissing.status, "sync_pending");
});

test("keeps the full map structure when no assignments have arrived yet", () => {
  const mapTasks = composeGrowthMapTasks([], 4);

  assert.equal(mapTasks.length, 9);
  assert.equal(mapTasks[0].assignmentUnavailable, true);
  assert.equal(mapTasks[0].status, "sync_pending");
  assert.equal(mapTasks[4].assignmentUnavailable, false);
  assert.equal(mapTasks[4].locked, true);
});
