import assert from "node:assert/strict";
import test from "node:test";
import {
  formatPolicyDocumentVersion,
  safePolicyDocumentUrl,
} from "../src/features/task-content/document-reading-safety.js";

test("policy links allow only absolute HTTPS destinations", () => {
  assert.equal(safePolicyDocumentUrl("https://example.com/policy"), "https://example.com/policy");
  assert.equal(safePolicyDocumentUrl("http://example.com/policy"), null);
  assert.equal(safePolicyDocumentUrl("javascript:alert(1)"), null);
  assert.equal(safePolicyDocumentUrl("/internal/path"), null);
  assert.equal(safePolicyDocumentUrl("not a url"), null);
});

test("policy version uses a readable source date and falls back to content version", () => {
  assert.equal(
    formatPolicyDocumentVersion("2026-07-24T01:47:08Z", "policy-v1", "en"),
    "Jul 24, 2026",
  );
  assert.equal(
    formatPolicyDocumentVersion("invalid", "policy-v1", "en"),
    "policy-v1",
  );
});
