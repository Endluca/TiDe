import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repoRoot = new URL("../../", import.meta.url);

test("uses Support consistently across teacher help surfaces", async () => {
  const files = await Promise.all([
    "frontend/src/components/FaqHelpDialog.jsx",
    "frontend/src/components/GuideLibraryDialog.jsx",
    "frontend/src/components/OnboardingGuide.jsx",
  ].map((path) => readFile(new URL(path, repoRoot), "utf8")));
  const copy = files.join("\n");

  assert.match(copy, /Submit a ticket and the Support team will follow up\./);
  assert.match(copy, /Contact Support/);
  assert.match(copy, /a Support ticket/);
  assert.equal(copy.includes("operations team will follow up"), false);
  assert.equal(copy.includes("Contact operations"), false);
  assert.equal(copy.includes("operations ticket"), false);
  assert.equal(copy.includes("operations needs to step in"), false);
  assert.equal(copy.includes("operations support"), false);
});
