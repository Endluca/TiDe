import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const teacherFacingCopyFiles = [
  new URL("../src/App.jsx", import.meta.url),
  new URL("../src/score-presentation.js", import.meta.url),
];

test("teacher-facing score copy does not expose internal system names", async () => {
  const sources = await Promise.all(
    teacherFacingCopyFiles.map((file) => readFile(file, "utf8")),
  );

  for (const source of sources) {
    assert.doesNotMatch(source, /世文|Shiwen/i);
  }
});
