import assert from "node:assert/strict";
import test from "node:test";
import { loadTaskContexts } from "../src/task-context-loader.js";

test("limits concurrent task-detail requests and keeps result order", async () => {
  const items = Array.from({ length: 8 }, (_, index) => ({
    taskInstanceId: `task-${index}`,
  }));
  let active = 0;
  let maximumActive = 0;

  const results = await loadTaskContexts(items, async (taskInstanceId) => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active -= 1;
    return taskInstanceId;
  }, null, 3);

  assert.equal(maximumActive, 3);
  assert.deepEqual(
    results.map((result) => result.value),
    items.map((item) => item.taskInstanceId),
  );
});

test("keeps other task details when one request is rate limited", async () => {
  const items = [
    { taskInstanceId: "task-1" },
    { taskInstanceId: "task-2" },
    { taskInstanceId: "task-3" },
  ];
  const results = await loadTaskContexts(items, async (taskInstanceId) => {
    if (taskInstanceId === "task-2") throw Object.assign(new Error("Too Many Requests"), { status: 429 });
    return taskInstanceId;
  });

  assert.deepEqual(results.map((result) => result.status), [
    "fulfilled",
    "rejected",
    "fulfilled",
  ]);
});
