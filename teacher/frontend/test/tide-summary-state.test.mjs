import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  isTideSummaryEmpty,
  TIDE_SUMMARY_EMPTY_REASON,
} from "../src/tide-summary-state.js";

test("recognizes only the explicit no-growth-data response", () => {
  assert.equal(isTideSummaryEmpty({
    available: false,
    reason: TIDE_SUMMARY_EMPTY_REASON,
  }), true);
  assert.equal(isTideSummaryEmpty({ available: true }), false);
  assert.equal(isTideSummaryEmpty({
    available: false,
    reason: "SOURCE_UNAVAILABLE",
  }), false);
  assert.equal(isTideSummaryEmpty(null), false);
});

test("wires no data to a stable empty state instead of loading or retry copy", async () => {
  const appSource = await readFile(
    new URL("../src/App.jsx", import.meta.url),
    "utf8",
  );

  assert.match(appSource, /if \(isTideSummaryEmpty\(tideSummary\)\)/);
  assert.match(appSource, /<GrowthDataEmptyCard language=\{language\} \/>/);
  assert.match(appSource, /No growth data to show/);
  assert.match(appSource, /<Link className="growth-empty-action" to="\/path">/);
  assert.match(appSource, /tideSummary\?\.available === true/);
  assert.doesNotMatch(appSource, /Your growth data is being prepared/);
});

test("keeps a development-only route for visual empty-state acceptance", async () => {
  const mainSource = await readFile(
    new URL("../src/main.jsx", import.meta.url),
    "utf8",
  );

  assert.match(mainSource, /"\/preview\/empty-growth"/);
  assert.match(mainSource, /emptyGrowthPreview=\{previewRoute === "\/preview\/empty-growth"\}/);
});
