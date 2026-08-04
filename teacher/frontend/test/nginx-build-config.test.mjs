import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { resolveNginxBuildConfig } from "../scripts/nginx-build-config.mjs";

test("requires only the public asset HTTPS URL", () => {
  assert.throws(
    () =>
      resolveNginxBuildConfig({
        VITE_PUBLIC_ASSET_BASE_URL: "http://media.example.test",
      }),
    /VITE_PUBLIC_ASSET_BASE_URL must use HTTPS/,
  );
});

test("forces same-origin API paths and ignores a stale API build variable", () => {
  assert.deepEqual(
    resolveNginxBuildConfig({
      VITE_API_BASE_URL: "https://stale.example.test/api",
      VITE_PUBLIC_ASSET_BASE_URL: "https://media.example.test/",
    }),
    {
      apiBaseUrl: "",
      publicAssetBaseUrl: "https://media.example.test",
    },
  );
});

test("accepts the full support-ticket body without buffering it twice", () => {
  const nginx = readFileSync(
    new URL("../nginx.conf", import.meta.url),
    "utf8",
  );

  assert.match(nginx, /client_max_body_size 26m;/);
  assert.match(nginx, /proxy_request_buffering off;/);
  assert.match(
    nginx,
    /sub_filter '__SITE_ORIGIN__' '\$tide_forwarded_proto:\/\/\$host';/,
  );
  assert.match(nginx, /default \$scheme;\s+http http;\s+https https;/);
});
