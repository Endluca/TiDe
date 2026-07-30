import assert from "node:assert/strict";
import test from "node:test";
import { describeDataError } from "../src/data-error.js";

test("replaces raw throttler errors with a Chinese retry message", () => {
  const details = describeDataError({
    status: 429,
    message: "ThrottlerException: Too Many Requests",
    retryAfterSeconds: 12,
  }, "zh");

  assert.equal(details.title, "当前访问人数较多");
  assert.equal(details.retryAfterSeconds, 12);
  assert.doesNotMatch(details.message, /Throttler|Too Many Requests/i);
});

test("does not expose raw service errors for other failures", () => {
  const details = describeDataError({
    status: 500,
    message: "sensitive internal failure",
  }, "zh");

  assert.equal(details.title, "任务数据暂时无法加载");
  assert.doesNotMatch(details.message, /sensitive internal failure/);
});
