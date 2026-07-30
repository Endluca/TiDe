import assert from "node:assert/strict";
import test from "node:test";

class MemoryStorage {
  #values = new Map();
  getItem(key) { return this.#values.get(key) ?? null; }
  setItem(key, value) { this.#values.set(key, String(value)); }
  removeItem(key) { this.#values.delete(key); }
}

globalThis.window = { sessionStorage: new MemoryStorage() };
const session = await import("../src/api/session-store.js");

test("keeps refresh token restorable while access token stays in memory", () => {
  session.saveTokenPair({
    accessToken: "access-token",
    accessTokenExpiresIn: 60,
    refreshToken: "refresh-token",
    refreshTokenExpiresAt: new Date(Date.now() + 60_000).toISOString(),
  });
  assert.equal(session.getAccessToken(), "access-token");
  assert.equal(session.getRefreshToken(), "refresh-token");
  assert.equal(session.hasRestorableSession(), true);
  session.clearSession();
  assert.equal(session.getAccessToken(), null);
  assert.equal(session.getRefreshToken(), null);
});
