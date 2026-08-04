import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("loads unread navigation badge styles before the lazy message center", () => {
  const mainSource = readFileSync(
    new URL("../src/main.jsx", import.meta.url),
    "utf8",
  );
  const globalNavigationStyles = readFileSync(
    new URL("../src/brand-v2.css", import.meta.url),
    "utf8",
  );
  const messageCenterStyles = readFileSync(
    new URL("../src/message-center.css", import.meta.url),
    "utf8",
  );

  assert.match(mainSource, /import "\.\/brand-v2\.css";/);
  assert.match(globalNavigationStyles, /\.nav-unread-badge\s*\{/);
  assert.match(globalNavigationStyles, /\.ref-mobile-nav \.nav-unread-badge\s*\{/);
  assert.doesNotMatch(messageCenterStyles, /\.nav-unread-badge\s*\{/);
});
