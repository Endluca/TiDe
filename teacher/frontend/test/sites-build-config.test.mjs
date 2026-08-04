import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_PUBLIC_ASSET_BASE_URL,
  resolveSitesBuildConfig,
} from "../scripts/sites-build-config.mjs";

test("requires an explicit HTTPS API origin for cross-origin Sites", () => {
  assert.throws(
    () => resolveSitesBuildConfig({}),
    /VITE_API_BASE_URL is required/,
  );
  for (const value of [
    "http://api.example.test",
    "https://api.example.test/api",
    "https://user:password@api.example.test",
  ]) {
    assert.throws(
      () => resolveSitesBuildConfig({ VITE_API_BASE_URL: value }),
      /must be an absolute HTTPS origin/,
    );
  }
});

test("normalizes the Sites API origin and keeps the reviewed asset default", () => {
  assert.deepEqual(
    resolveSitesBuildConfig({
      VITE_API_BASE_URL: " https://api.example.test/ ",
    }),
    {
      apiBaseUrl: "https://api.example.test",
      publicAssetBaseUrl: DEFAULT_PUBLIC_ASSET_BASE_URL,
    },
  );
});

test("rejects unsafe public asset URLs", () => {
  for (const value of [
    "http://media.example.test",
    "https://user:password@media.example.test",
    "https://media.example.test/assets?token=secret",
  ]) {
    assert.throws(
      () =>
        resolveSitesBuildConfig({
          VITE_API_BASE_URL: "https://api.example.test",
          VITE_PUBLIC_ASSET_BASE_URL: value,
        }),
      /VITE_PUBLIC_ASSET_BASE_URL must be an absolute HTTPS URL/,
    );
  }
});
