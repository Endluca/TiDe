import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  replaceSiteOrigin,
  resolveNginxBuildConfig,
} from "../scripts/nginx-build-config.mjs";

test("requires both HTTPS build-time URLs", () => {
  assert.throws(
    () =>
      resolveNginxBuildConfig({
        VITE_PUBLIC_ASSET_BASE_URL: "https://media.example.test",
      }),
    /VITE_API_BASE_URL is required/,
  );
  assert.throws(
    () =>
      resolveNginxBuildConfig({
        VITE_API_BASE_URL: "http://teacher.example.test",
        VITE_PUBLIC_ASSET_BASE_URL: "https://media.example.test",
      }),
    /VITE_API_BASE_URL must use HTTPS/,
  );
  assert.throws(
    () =>
      resolveNginxBuildConfig({
        VITE_API_BASE_URL: "https://teacher.example.test",
        VITE_PUBLIC_ASSET_BASE_URL: "http://media.example.test",
      }),
    /VITE_PUBLIC_ASSET_BASE_URL must use HTTPS/,
  );
});

test("requires the API base URL to be an origin", () => {
  assert.throws(
    () =>
      resolveNginxBuildConfig({
        VITE_API_BASE_URL: "https://teacher.example.test/api",
        VITE_PUBLIC_ASSET_BASE_URL: "https://media.example.test",
      }),
    /must be an origin/,
  );
});

test("derives the site origin and normalizes trailing slashes", () => {
  assert.deepEqual(
    resolveNginxBuildConfig({
      VITE_API_BASE_URL: "https://teacher.example.test/",
      VITE_PUBLIC_ASSET_BASE_URL: "https://media.example.test/",
    }),
    {
      apiBaseUrl: "https://teacher.example.test",
      publicAssetBaseUrl: "https://media.example.test",
      siteOrigin: "https://teacher.example.test",
    },
  );
});

test("replaces every site origin marker and fails when it is absent", () => {
  assert.equal(
    replaceSiteOrigin(
      '<meta content="__SITE_ORIGIN__/og.png"><p>__SITE_ORIGIN__</p>',
      "https://teacher.example.test",
    ),
    '<meta content="https://teacher.example.test/og.png"><p>https://teacher.example.test</p>',
  );
  assert.throws(
    () => replaceSiteOrigin("<html></html>", "https://teacher.example.test"),
    /missing the __SITE_ORIGIN__ marker/,
  );
});

test("accepts the full support-ticket body without buffering it twice", () => {
  const nginx = readFileSync(
    new URL("../nginx.conf", import.meta.url),
    "utf8",
  );

  assert.match(nginx, /client_max_body_size 26m;/);
  assert.match(nginx, /proxy_request_buffering off;/);
});
