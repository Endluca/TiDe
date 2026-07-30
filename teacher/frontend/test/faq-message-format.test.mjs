import assert from "node:assert/strict";
import test from "node:test";
import { toFaqDisplayMarkdown } from "../src/faq-message-format.js";

test("preserves Markdown structure while removing internal source markers", () => {
  const answer = [
    "### Before class",
    "",
    "1. Open **classroom settings**. [Source 1]",
    "2. Test your microphone. [Source 1]",
  ].join("\n");

  assert.equal(
    toFaqDisplayMarkdown(answer),
    [
      "### Before class",
      "",
      "1. Open **classroom settings**.",
      "2. Test your microphone.",
    ].join("\n"),
  );
});

test("never exposes multiple internal source markers", () => {
  assert.equal(
    toFaqDisplayMarkdown(
      "Use the approved workflow. [Source 1] [Source 2]",
    ),
    "Use the approved workflow.",
  );
});
