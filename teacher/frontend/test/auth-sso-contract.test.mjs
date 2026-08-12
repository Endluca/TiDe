import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const authApi = readFileSync(
  new URL("../src/api/auth-api.js", import.meta.url),
  "utf8",
);
const authScreen = readFileSync(
  new URL("../src/components/AuthScreen.jsx", import.meta.url),
  "utf8",
);

test("exchanges only the opaque CRM callback code for a TIDE session", () => {
  assert.match(authApi, /\/api\/v1\/auth\/crm-sso\/exchange/);
  assert.match(authApi, /body: \{ code \}/);
  assert.match(authScreen, /path === "\/sso\/callback"/);
  assert.match(authScreen, /replaceState\(\{\}, "", "\/sso\/callback"\)/);
  assert.doesNotMatch(authScreen, /saveTokenPair\([^)]*token/i);
});

test("keeps hybrid routes capability-driven for the later SSO-only switch", () => {
  assert.match(authApi, /\/api\/v1\/auth\/capabilities/);
  assert.match(authScreen, /result\.authMode === "CRM_SSO_ONLY"/);
  assert.match(authScreen, /capabilities\?\.crmEntryUrl/);
});
