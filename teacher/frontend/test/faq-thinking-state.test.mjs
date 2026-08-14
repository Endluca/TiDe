import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dialogSource = await readFile(
  new URL("../src/components/FaqHelpDialog.jsx", import.meta.url),
  "utf8",
);

test("shows a localized thinking bubble inside the FAQ conversation", () => {
  assert.match(dialogSource, /messages\.length === 0 && !loading/);
  assert.match(
    dialogSource,
    /\{loading && \([\s\S]*?className="faq-message is-assistant"[\s\S]*?role="status"/,
  );
  assert.match(dialogSource, /copy\(language, "Thinking…", "思考中…"\)/);
  assert.match(
    dialogSource,
    /\[messages, open, historyLoading, loading, mode\]/,
  );
});
