import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("fades to a static Toki after a one-shot motion ends", () => {
  const componentSource = readFileSync(
    new URL("../src/components/UI.jsx", import.meta.url),
    "utf8",
  );
  const styleSource = readFileSync(
    new URL("../src/styles.css", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(componentSource, /poster=\{poster\}/);
  assert.doesNotMatch(componentSource, /onError=/);
  assert.match(componentSource, /preload="none"/);
  assert.match(componentSource, /onEnded=/);
  assert.match(componentSource, /video\.play\(\)\?\.catch\(\(\) => \{\}\)/);
  assert.match(componentSource, /showStaticFallback/);
  assert.match(componentSource, /toki-static-enter/);
  assert.match(styleSource, /@keyframes toki-static-fade-in/);
});
