import assert from "node:assert/strict";
import test from "node:test";
import { createNotificationRequestQueue } from "../src/notification-refresh.js";

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
};

test("deduplicates concurrent notification requests with the same input", async () => {
  const request = deferred();
  let calls = 0;
  const queue = createNotificationRequestQueue(() => {
    calls += 1;
    return request.promise;
  });

  const first = queue.run({ filter: "ALL" });
  const duplicate = queue.run({ filter: "ALL" });
  await Promise.resolve();

  assert.equal(first, duplicate);
  assert.equal(calls, 1);
  assert.equal(queue.isRunning(), true);

  request.resolve({ items: [] });
  assert.deepEqual(await first, { items: [] });
  assert.equal(queue.isRunning(), false);
});

test("serializes different notification requests and deduplicates queued work", async () => {
  const requests = [deferred(), deferred()];
  const calls = [];
  let active = 0;
  let maximumActive = 0;
  const queue = createNotificationRequestQueue(async (input) => {
    calls.push(input);
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    const current = requests[calls.length - 1];
    const result = await current.promise;
    active -= 1;
    return result;
  });

  const all = queue.run({ filter: "ALL" });
  const unread = queue.run({ filter: "UNREAD" });
  const duplicateUnread = queue.run({ filter: "UNREAD" });
  await Promise.resolve();

  assert.equal(unread, duplicateUnread);
  assert.equal(calls.length, 1);

  requests[0].resolve("all");
  assert.equal(await all, "all");
  await Promise.resolve();
  assert.equal(calls.length, 2);

  requests[1].resolve("unread");
  assert.equal(await unread, "unread");
  assert.equal(maximumActive, 1);
  assert.equal(queue.isRunning(), false);
});

test("continues queued notification refreshes after a request fails", async () => {
  let calls = 0;
  const queue = createNotificationRequestQueue(async ({ filter }) => {
    calls += 1;
    if (filter === "ALL") throw new Error("unavailable");
    return filter;
  });

  const failed = queue.run({ filter: "ALL" });
  const next = queue.run({ filter: "UNREAD" });

  await assert.rejects(failed, /unavailable/);
  assert.equal(await next, "UNREAD");
  assert.equal(calls, 2);
  assert.equal(queue.isRunning(), false);
});
