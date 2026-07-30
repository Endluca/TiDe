import assert from "node:assert/strict";
import test from "node:test";
import {
  localizePersonalizedTask,
  personalizedTaskCodes,
} from "../src/personalized-task-localization.js";

const liveTask = (taskCode, overrides = {}) => ({
  id: "00000000-0000-4000-8000-000000000001",
  backendId: "00000000-0000-4000-8000-000000000001",
  taskCategory: "personalized",
  taskCode,
  name: "English fallback title",
  shortName: "English short title",
  reason: "Shiwen reason",
  value: "Shiwen outcome",
  result: "Shiwen action",
  standard: "Shiwen standard",
  steps: ["Shiwen step"],
  ...overrides,
});

test("localizes presentation metadata without replacing Shiwen task content", () => {
  assert.deepEqual(personalizedTaskCodes, [
    "NT-Q03",
    "P-REL-ATTENDANCE",
    "P-REL-MEMO",
    "P-FB-NEGATIVE",
    "P-FB-COMPLAINT",
    "P-FB-BLACKLIST",
  ]);

  for (const taskCode of personalizedTaskCodes) {
    const source = liveTask(taskCode);
    const localized = localizePersonalizedTask(source);
    assert.match(localized.name, /[\u3400-\u9fff]/);
    assert.equal(localized.reason, source.reason);
    assert.equal(localized.value, source.value);
    assert.equal(localized.result, source.result);
    assert.equal(localized.standard, source.standard);
    assert.deepEqual(localized.steps, source.steps);
  }
});

test("keeps every teacher-specific Shiwen field unchanged", () => {
  const localized = localizePersonalizedTask(liveTask("P-REL-ATTENDANCE", {
    name: "出席问题",
    shortName: "出席问题",
    reason: "共命中 10 节课程，请参加培训并完成 quiz。",
    value: "世文收益",
    result: "世文操作",
    standard: "世文标准",
    steps: ["世文步骤"],
  }));

  assert.equal(localized.name, "出席问题");
  assert.equal(localized.shortName, "出席问题");
  assert.equal(localized.reason, "共命中 10 节课程，请参加培训并完成 quiz。");
  assert.equal(localized.value, "世文收益");
  assert.equal(localized.result, "世文操作");
  assert.equal(localized.standard, "世文标准");
  assert.deepEqual(localized.steps, ["世文步骤"]);
});
