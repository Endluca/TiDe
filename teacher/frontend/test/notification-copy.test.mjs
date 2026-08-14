import assert from "node:assert/strict";
import test from "node:test";
import { localizeNotification } from "../src/notification-copy.js";

test("localizes our automatic task notifications", () => {
  const message = {
    source: "SYSTEM",
    typeCode: "TASK_REVIEW_PENDING",
    title: "Task review in progress",
    body: "Your task submission is saved and still being reviewed.",
  };

  assert.deepEqual(localizeNotification(message, "zh"), {
    title: "任务正在审核",
    body: "你的任务提交已保存，正在审核中。打开任务即可查看最新状态。",
  });
  assert.deepEqual(localizeNotification(message, "en"), {
    title: "Task review in progress",
    body: "Your task submission is saved and still under review. Open the task to check the latest status.",
  });
});

test("localizes the growth-stage notification without inventing a stage name", () => {
  const message = {
    source: "SYSTEM",
    typeCode: "GROWTH_STAGE_AVAILABLE",
    title: "Your next growth stage is ready",
    body: "A new set of required tasks is now available in your growth path. Complete them in the order that works best for you.",
  };

  assert.deepEqual(localizeNotification(message, "zh"), {
    title: "新的成长阶段已开放",
    body: "一组新的必修任务已经开放，你可以按照适合自己的顺序完成本阶段任务。",
  });
  assert.deepEqual(localizeNotification(message, "en"), {
    title: message.title,
    body: "A new set of required tasks is now available in your growth path. Work through them in the order that works best for you.",
  });
});

test("keeps Shiwen notification fields unchanged", () => {
  const message = {
    source: "EXTERNAL",
    typeCode: null,
    title: "运营确认任务",
    body: "请查看运营端确认结果。",
  };

  assert.deepEqual(localizeNotification(message, "en"), {
    title: message.title,
    body: message.body,
  });
});

test("keeps the Shiwen title while localizing our personalized reminder body", () => {
  const message = {
    source: "SYSTEM",
    typeCode: "PERSONALIZED_TASK_ASSIGNED",
    title: "出席问题",
    body: "A new improvement task is ready for you.",
  };

  assert.deepEqual(localizeNotification(message, "zh"), {
    title: "出席问题",
    body: "一项新的改善任务已准备好，你可以在合适的时候打开并开始下一步。",
  });
});
