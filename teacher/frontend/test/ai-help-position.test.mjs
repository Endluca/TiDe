import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  clampAiHelpPosition,
  defaultAiHelpPosition,
  getAiHelpBounds,
} from "../src/ai-help-position.js";

test("loads the floating AI helper styles before the lazy support dialog", () => {
  const mainSource = readFileSync(
    new URL("../src/main.jsx", import.meta.url),
    "utf8",
  );
  const helperStyles = readFileSync(
    new URL("../src/ai-help-fab.css", import.meta.url),
    "utf8",
  );

  assert.match(mainSource, /import "\.\/ai-help-fab\.css";/);
  assert.match(helperStyles, /\.ai-help-fab > button > img/);
  assert.match(helperStyles, /transform: translate\(8%, 2px\);/);
});

test("keeps the desktop AI helper below the top navigation", () => {
  const bounds = getAiHelpBounds({
    viewportWidth: 1440,
    viewportHeight: 900,
    headerBottom: 74,
    lowerBoundary: 900,
    buttonSize: 84,
    edgeGap: 28,
  });

  assert.deepEqual(bounds, {
    minX: 28,
    maxX: 1328,
    minY: 102,
    maxY: 788,
  });
  assert.deepEqual(defaultAiHelpPosition(bounds, 48), { x: 1328, y: 740 });
});

test("keeps the mobile AI helper above the bottom navigation", () => {
  const bounds = getAiHelpBounds({
    viewportWidth: 390,
    viewportHeight: 844,
    headerBottom: 66,
    lowerBoundary: 768,
    buttonSize: 62,
    edgeGap: 15,
  });

  assert.deepEqual(bounds, {
    minX: 15,
    maxX: 313,
    minY: 81,
    maxY: 691,
  });
  assert.deepEqual(defaultAiHelpPosition(bounds, 18), { x: 313, y: 673 });
});

test("keeps a lifted default inside short viewports", () => {
  const bounds = { minX: 15, maxX: 313, minY: 120, maxY: 140 };

  assert.deepEqual(defaultAiHelpPosition(bounds, 48), { x: 313, y: 120 });
});

test("clamps saved and dragged positions back into the visible area", () => {
  const bounds = {
    minX: 15,
    maxX: 313,
    minY: 81,
    maxY: 691,
  };

  assert.deepEqual(
    clampAiHelpPosition({ x: -200, y: 900 }, bounds),
    { x: 15, y: 691 },
  );
  assert.deepEqual(
    clampAiHelpPosition({ x: 120, y: 240 }, bounds),
    { x: 120, y: 240 },
  );
});
