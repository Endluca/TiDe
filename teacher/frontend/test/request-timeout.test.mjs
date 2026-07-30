import assert from "node:assert/strict";
import test from "node:test";
import { createRequestSignal } from "../src/api/request-timeout.js";

test("forwards an upstream abort", () => {
  const upstream = new AbortController();
  const request = createRequestSignal(upstream.signal, 1_000);

  upstream.abort();

  assert.equal(request.signal.aborted, true);
  assert.equal(request.didTimeout(), false);
  request.cleanup();
});

test("marks deadline aborts as timeouts", async () => {
  const request = createRequestSignal(undefined, 1);

  await new Promise((resolve) => setTimeout(resolve, 5));

  assert.equal(request.signal.aborted, true);
  assert.equal(request.didTimeout(), true);
  request.cleanup();
});
