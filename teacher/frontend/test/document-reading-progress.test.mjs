import assert from "node:assert/strict";
import test from "node:test";
import {
  canPersistDocumentProgress,
  documentReadProgressFromStep,
  hasDocumentProgressAdvanced,
  measureDocumentReadProgress,
  mergeDocumentReadProgress,
  shouldPersistDocumentProgress,
} from "../src/features/task-content/document-reading-progress.js";

test("document reading stays below 100 until the reader reaches the end", () => {
  assert.deepEqual(
    measureDocumentReadProgress({
      scrollTop: 830,
      clientHeight: 150,
      scrollHeight: 1000,
    }),
    { readPercent: 98, reachedEnd: false },
  );
  assert.deepEqual(
    measureDocumentReadProgress({
      scrollTop: 834,
      clientHeight: 150,
      scrollHeight: 1000,
    }),
    { readPercent: 100, reachedEnd: true },
  );
  assert.deepEqual(
    measureDocumentReadProgress({
      scrollTop: 0,
      clientHeight: 1000,
      scrollHeight: 800,
    }),
    { readPercent: 100, reachedEnd: true },
  );
});

test("document reading progress is monotonic and reaching the end is terminal", () => {
  assert.deepEqual(
    mergeDocumentReadProgress(
      { readPercent: 72, reachedEnd: false },
      { readPercent: 40, reachedEnd: false },
    ),
    { readPercent: 72, reachedEnd: false },
  );
  assert.deepEqual(
    mergeDocumentReadProgress(
      { readPercent: 72, reachedEnd: false },
      { readPercent: 100, reachedEnd: true },
    ),
    { readPercent: 100, reachedEnd: true },
  );
  assert.deepEqual(
    mergeDocumentReadProgress(
      { readPercent: 100, reachedEnd: true },
      { readPercent: 10, reachedEnd: false },
    ),
    { readPercent: 100, reachedEnd: true },
  );
  assert.deepEqual(
    mergeDocumentReadProgress(
      { readPercent: 20, reachedEnd: false },
      { readPercent: 5, reachedEnd: false },
    ),
    { readPercent: 20, reachedEnd: false },
    "an older server receipt must not roll local scrolling back",
  );
  assert.equal(
    hasDocumentProgressAdvanced(
      { readPercent: 5, reachedEnd: false },
      { readPercent: 20, reachedEnd: false },
    ),
    true,
  );
});

test("document reading persists every five points and always persists 100", () => {
  assert.equal(shouldPersistDocumentProgress(20, 24), false);
  assert.equal(shouldPersistDocumentProgress(20, 25), true);
  assert.equal(shouldPersistDocumentProgress(99, 100), true);
  assert.equal(shouldPersistDocumentProgress(80, 60), false);
});

test("read-only previews and terminal assignments never persist progress", () => {
  assert.equal(canPersistDocumentProgress(), true);
  assert.equal(canPersistDocumentProgress({ readOnly: true }), false);
  assert.equal(canPersistDocumentProgress({ assignmentCompleted: true }), false);
  assert.equal(canPersistDocumentProgress({ documentCompleted: true }), false);
  assert.equal(canPersistDocumentProgress({ contentCompatible: false }), false);
});

test("document save acknowledgements must be complete and internally consistent", () => {
  const expected = {
    stepKey: "g02-document",
    contentVersion: "policy-v1",
    contentHash: "a".repeat(64),
  };
  const valid = {
    stepKey: expected.stepKey,
    status: "IN_PROGRESS",
    percent: 20,
    details: {
      contentVersion: expected.contentVersion,
      contentHash: expected.contentHash,
      readPercent: 20,
      reachedEnd: false,
    },
  };
  assert.deepEqual(
    documentReadProgressFromStep(valid, expected),
    { readPercent: 20, reachedEnd: false },
  );
  assert.deepEqual(
    documentReadProgressFromStep({
      ...valid,
      status: "COMPLETED",
      percent: 100,
      details: { ...valid.details, readPercent: 100, reachedEnd: true },
    }, expected),
    { readPercent: 100, reachedEnd: true },
  );
  assert.throws(
    () => documentReadProgressFromStep(undefined, expected),
    /INVALID_DOCUMENT_PROGRESS_RESPONSE/,
  );
  assert.throws(
    () => documentReadProgressFromStep({ ...valid, stepKey: "wrong-step" }, expected),
    /INVALID_DOCUMENT_PROGRESS_RESPONSE/,
  );
  assert.throws(
    () => documentReadProgressFromStep({
      ...valid,
      details: { ...valid.details, readPercent: 5 },
    }, expected),
    /INVALID_DOCUMENT_PROGRESS_RESPONSE/,
  );
  assert.throws(
    () => documentReadProgressFromStep({
      ...valid,
      details: { ...valid.details, contentHash: "b".repeat(64) },
    }, expected),
    /INVALID_DOCUMENT_PROGRESS_RESPONSE/,
  );
});
