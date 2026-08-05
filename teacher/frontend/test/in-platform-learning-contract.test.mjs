import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repoRoot = new URL("../../", import.meta.url);

test("question content never sends teachers to an external exam or form", async () => {
  const source = JSON.parse(
    await readFile(
      new URL("backend/reference/task-quiz-banks.json", repoRoot),
      "utf8",
    ),
  );
  const externalInstructions = Object.entries(source.taskQuizBanks)
    .flatMap(([bankKey, questions]) => questions.map((question) => ({
      bankKey,
      questionId: question.id,
      text: `${question.question || ""} ${question.questionZh || ""}`,
    })))
    .filter(({ text }) => /https?:\/\/|forms\.gle/i.test(text));

  assert.deepEqual(externalInstructions, []);
});

test("the active catalog no longer publishes local learning steps for Kuozhi tasks", async () => {
  const catalog = await readFile(
    new URL("backend/scripts/sync-current-task-catalog.ts", repoRoot),
    "utf8",
  );
  const videoManifest = JSON.parse(
    await readFile(
      new URL("backend/config/public-videos-v2.json", repoRoot),
      "utf8",
    ),
  );

  assert.equal(catalog.includes("pendingQuizStep("), false);
  assert.equal(catalog.includes("g06-ttp-orientation-video"), false);
  assert.equal(catalog.includes("g10-set-fundamentals-video"), false);
  assert.equal(catalog.includes("mock-set-fundamentals-2026-07-v1"), false);
  assert.deepEqual(videoManifest.videos, []);
});

test("a video plus checklist task requires the video before completion", async () => {
  const taskFlow = await readFile(
    new URL("frontend/src/components/TaskFlow.jsx", repoRoot),
    "utf8",
  );

  assert.match(taskFlow, /必看课程视频/);
  assert.match(
    taskFlow,
    /checked\.length !== items\.length \|\| \(videoRequired && !videoComplete\)/,
  );
});
