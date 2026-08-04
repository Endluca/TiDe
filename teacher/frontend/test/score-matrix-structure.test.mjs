import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("keeps score-matrix descriptions accessible without visible table captions", async () => {
  const source = await readFile(
    new URL("../src/App.jsx", import.meta.url),
    "utf8",
  );

  assert.equal(source.includes("<caption"), false);
  assert.match(
    source,
    /<table\s+aria-label=\{copy\(/,
  );
});
