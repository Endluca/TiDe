import assert from "node:assert/strict";
import test from "node:test";
import {
  formatTaskDueAt,
  localizedTaskDue,
} from "../src/task-due.js";

test("formats backend dueAt with an explicit English date format", () => {
  const due = formatTaskDueAt("2026-07-24T08:30:00.000Z");

  assert.match(due, /^Due [A-Z][a-z]{2} \d{1,2}, 2026,/);
  assert.match(due, /\d{1,2}:\d{2} [AP]M/);
  assert.doesNotMatch(due, /年|月|日/);
});

test("keeps the backend English due copy in Chinese mode for personalized tasks", () => {
  const due = formatTaskDueAt("2026-07-24T08:30:00.000Z");

  assert.equal(
    localizedTaskDue(
      {
        taskCategory: "personalized",
        status: "completed",
        due,
      },
      "建议在下一节相关课程前完成",
    ),
    due,
  );
});

test("keeps existing localized due behavior for required tasks", () => {
  assert.equal(
    localizedTaskDue(
      {
        taskCategory: "required",
        status: "available",
        due: "Available now",
      },
      "首课前完成",
    ),
    "现已开放",
  );
});
