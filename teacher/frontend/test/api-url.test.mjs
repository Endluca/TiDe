import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeApiBaseUrl,
  toApiUrl,
} from "../src/api/api-url.js";

test("uses same-origin API paths when no cross-origin base is configured", () => {
  assert.equal(normalizeApiBaseUrl(undefined), "");
  assert.equal(toApiUrl("/api/v1/profile"), "/api/v1/profile");
  assert.equal(toApiUrl("api/v1/profile"), "/api/v1/profile");
});

test("keeps the explicit cross-origin base for legacy Sites builds", () => {
  assert.equal(
    toApiUrl("/api/v1/profile", " https://api.example.test/// "),
    "https://api.example.test/api/v1/profile",
  );
});

test("passes absolute signed URLs through unchanged", () => {
  const signedUrl = "https://objects.example.test/file?signature=abc";
  assert.equal(toApiUrl(signedUrl, "https://api.example.test"), signedUrl);
});
