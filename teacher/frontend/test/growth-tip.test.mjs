import assert from "node:assert/strict";
import test from "node:test";
import {
  buildGrowthTip,
  selectGrowthTipTask,
  stageIndexForAvailableTasks,
  stageIndexForCampDay,
  stageIndexFromPathSearch,
  stageKeyForCampDay,
} from "../src/growth-tip.js";

const task = (overrides = {}) => ({
  id: "profile-credentials",
  name: "资料与资质完善",
  status: "available",
  taskCategory: "required",
  displayRank: 1,
  locked: false,
  method: "external_status",
  stage: "Day 1-7",
  ...overrides,
});

test("uses camp day as the automatic growth-stage baseline", () => {
  assert.equal(stageIndexForCampDay(1), 0);
  assert.equal(stageIndexForCampDay(8), 1);
  assert.equal(stageIndexForCampDay(15), 2);
  assert.equal(stageKeyForCampDay(30), "advance");
});

test("uses the highest actually available stage after an early unlock", () => {
  const nextStageTask = task({
    id: "ttp-orientation",
    stage: "Day 8-14",
  });

  assert.equal(stageIndexForAvailableTasks([nextStageTask], 4), 1);
  assert.equal(buildGrowthTip({
    teacherName: "Jouna",
    campDay: 4,
    tasks: [nextStageTask],
    language: "en",
  }).stageKey, "integration");
});

test("opens a requested notification stage without allowing locked-stage URLs", () => {
  assert.equal(stageIndexFromPathSearch("?stage=2", 2), 1);
  assert.equal(stageIndexFromPathSearch("?stage=3", 1), 1);
  assert.equal(stageIndexFromPathSearch("?stage=invalid", 2), 2);
});

test("keeps a useful Toki card when no task is available", () => {
  const tip = buildGrowthTip({
    teacherName: "Jouna",
    campDay: 15,
    tasks: [],
    language: "zh",
  });

  assert.equal(tip.stageKey, "advance");
  assert.equal(tip.action.task, null);
  assert.match(tip.action.title, /没有需要立即处理/);
  assert.equal(tip.mood, "wave");
});

test("does not claim completed work happened today", () => {
  const tip = buildGrowthTip({
    teacherName: "Jouna",
    campDay: 15,
    tasks: [task({ status: "completed" })],
    language: "en",
  });

  assert.match(tip.title, /all caught up for now/);
  assert.doesNotMatch(`${tip.title} ${tip.body}`, /today/i);
});

test("combines the camp stage with the selected task-specific prompt", () => {
  const selected = task({ result: "后端返回的下一步任务指引。" });
  const tip = buildGrowthTip({
    teacherName: "Jouna",
    campDay: 15,
    tasks: [selected],
    language: "zh",
  });

  assert.equal(tip.stageKey, "advance");
  assert.equal(tip.action.task, selected);
  assert.match(tip.title, /已经学会的内容用得更稳/);
  assert.equal(tip.action.prompt, "后端返回的下一步任务指引。");
});

test("prioritizes recovery and in-progress work over newly assigned work", () => {
  const available = task({ id: "G-available", displayRank: 1 });
  const started = task({ id: "G-started", status: "started", displayRank: 10 });
  const recovery = task({ id: "G-retry", status: "retry_required", displayRank: 20 });

  assert.equal(selectGrowthTipTask([available, started, recovery]), recovery);
});

test("uses the real personalized assignment and a teacher-safe task prompt", () => {
  const personalized = task({
    id: "feedback-interaction-engagement",
    name: "让学员更多开口",
    result: "完成后端返回的互动练习。",
    status: "started",
    taskCategory: "personalized",
  });
  const tip = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 10,
    tasks: [personalized],
    language: "zh",
  });

  assert.equal(tip.action.task, personalized);
  assert.match(tip.action.prompt, /后端返回的互动练习/);
  assert.equal(tip.action.buttonLabel, "继续任务");
});

test("shows a non-judgmental waiting message after submission", () => {
  const submitted = task({ status: "verifying" });
  const tip = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [submitted],
    language: "zh",
  });

  assert.equal(tip.action.tone, "waiting");
  assert.match(tip.action.prompt, /无需重复操作/);
  assert.equal(tip.action.buttonLabel, "查看进度");
});

test("separates retry, final result, syncing and content-pending actions", () => {
  const retry = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({ status: "retry_required", result: "调整指定内容。" })],
    language: "zh",
  });
  const finalResult = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({ status: "failed_final", result: "查看本次结果。" })],
    language: "zh",
  });
  const syncing = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({ status: "sync_pending" })],
    language: "zh",
  });
  const pendingContent = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({ method: "content_pending" })],
    language: "zh",
  });

  assert.equal(retry.action.buttonLabel, "调整后重试");
  assert.equal(finalResult.action.buttonLabel, "查看结果");
  assert.match(syncing.action.prompt, /无需重复提交/);
  assert.equal(pendingContent.action.buttonLabel, null);
});

test("shows a stable English due date only within the next 24 hours", () => {
  const now = new Date("2026-07-24T08:00:00.000Z");
  const dueSoon = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({
      dueAt: "2026-07-25T07:00:00.000Z",
      status: "started",
    })],
    language: "zh",
    now,
  });
  const dueLater = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({
      dueAt: "2026-07-26T08:00:00.000Z",
      status: "started",
    })],
    language: "zh",
    now,
  });

  assert.match(dueSoon.action.due, /^Due [A-Z][a-z]{2} \d{1,2}, 2026,/);
  assert.doesNotMatch(dueSoon.action.due, /年|月|日/);
  assert.equal(dueLater.action.due, null);
});

test("replaces unsafe or overly long backend guidance with teacher-safe copy", () => {
  const tip = buildGrowthTip({
    teacherName: "Teacher",
    campDay: 4,
    tasks: [task({ result: "Internal high-risk label: complaint confirmed." })],
    language: "en",
  });

  assert.equal(
    tip.action.prompt,
    "Open the task when you’re ready and follow the steps provided.",
  );
});

test("uses the backend-provided guidance for every fixed and personalized task route", () => {
  const taskIds = [
    "profile-credentials",
    "device-network",
    "platform-policies",
    "lesson-preparation",
    "ttp-orientation",
    "me-culture",
    "reliability-training",
    "student-types",
    "cocos-training",
    "set-fundamentals",
    "classroom-quality-reminder",
    "attendance-reliability-refresher",
    "lesson-memo-rules-learning",
    "feedback-self-study",
    "feedback-interaction-engagement",
    "feedback-correction-explanation",
    "feedback-speaking-pace",
    "feedback-scaffolding-language",
    "feedback-teaching-aids",
    "feedback-student-response",
    "feedback-pronunciation",
    "feedback-professionalism",
    "feedback-blacklist-review",
  ];
  const prompts = taskIds.map((id, index) => buildGrowthTip({
    teacherName: "Teacher",
    campDay: 10,
    tasks: [task({ id, name: id, result: `Backend guidance ${index + 1}.` })],
    language: "zh",
  }).action.prompt);

  assert.equal(new Set(prompts).size, taskIds.length);
  assert.deepEqual(
    prompts,
    taskIds.map((_, index) => `Backend guidance ${index + 1}.`),
  );
});

test("keeps a safe Toki fallback when trusted growth data is unavailable", () => {
  const tip = buildGrowthTip({
    teacherName: "Teacher",
    campDay: null,
    tasks: [],
    language: "zh",
    sourceUnavailable: true,
  });

  assert.equal(tip.action.task, null);
  assert.match(tip.title, /进度正在更新/);
  assert.doesNotMatch(tip.action.prompt, /前端|缺失数据/);
  assert.match(tip.action.prompt, /无需重复/);
});
