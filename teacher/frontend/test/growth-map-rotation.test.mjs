import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  clockwiseOrbitSlot,
  clockwiseStageSequence,
  nextClockwiseStageIndex,
} from "../src/growth-map-rotation.js";

test("stage selection always advances clockwise one stage at a time", () => {
  assert.deepEqual(clockwiseStageSequence(0, 2, 3), [1, 2]);
  assert.deepEqual(clockwiseStageSequence(2, 1, 3), [0, 1]);
  assert.equal(nextClockwiseStageIndex(2, 3), 0);
});

test("the selected stage advances every island into the next clockwise orbit slot", () => {
  assert.deepEqual(
    [0, 1, 2].map((stageIndex) => clockwiseOrbitSlot(stageIndex, 0, 3)),
    [0, 2, 1],
  );
  assert.deepEqual(
    [0, 1, 2].map((stageIndex) => clockwiseOrbitSlot(stageIndex, 1, 3)),
    [1, 0, 2],
  );
});

test("the desktop route is a persistent SVG loop and is never stage-hidden", async () => {
  const styles = await readFile(
    new URL("../src/growth-path.css", import.meta.url),
    "utf8",
  );
  const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");

  assert.match(app, /className="map-route-network"/);
  assert.match(styles, /\.map-route-dashes/);
  assert.doesNotMatch(styles, /selected-stage-[^\n]+\.map-route/);
});
