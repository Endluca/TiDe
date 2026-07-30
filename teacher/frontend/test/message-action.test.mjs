import assert from "node:assert/strict";
import test from "node:test";
import {
  resolveMessageActionRoute,
  resolveTaskMessageRoute,
} from "../src/message-action.js";

test("opens the exact assignment target even before the refreshed task list contains it", () => {
  const route = resolveTaskMessageRoute(
    {
      actionType: "TASK_DETAIL",
      actionTarget: "/task/assignment-001",
      actionAvailable: true,
    },
    null,
  );

  assert.equal(route, "/task/assignment-001");
});

test("rejects invalid or unavailable task targets", () => {
  assert.equal(
    resolveTaskMessageRoute(
      {
        actionType: "TASK_DETAIL",
        actionTarget: "https://example.com/task/assignment-001",
        actionAvailable: true,
      },
      null,
    ),
    null,
  );
  assert.equal(
    resolveTaskMessageRoute(
      {
        actionType: "TASK_DETAIL",
        actionTarget: "/task/assignment-001",
        actionAvailable: true,
      },
      { status: "expired" },
    ),
    null,
  );
});

test("opens only valid growth-stage targets", () => {
  assert.equal(
    resolveMessageActionRoute({
      actionType: "TASKS",
      actionTarget: "/path?stage=2",
      actionAvailable: true,
    }),
    "/path?stage=2",
  );
  assert.equal(
    resolveMessageActionRoute({
      actionType: "TASKS",
      actionTarget: "/path?stage=9",
      actionAvailable: true,
    }),
    null,
  );
  assert.equal(
    resolveMessageActionRoute({
      actionType: "TASKS",
      actionTarget: "https://example.com/path?stage=2",
      actionAvailable: true,
    }),
    null,
  );
});
